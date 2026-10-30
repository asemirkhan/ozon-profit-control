#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.db import get_conn

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
MARKETPLACE = os.getenv("PROFIT_CONTROL_MARKETPLACE", "wb")

WB_SEARCH_ENDPOINT = "https://search.wb.ru/exactmatch/ru/common/v4/search"


@dataclass(frozen=True)
class CardRow:
    nm_id: str
    product_title: str | None
    brand: str | None
    rating: float | None
    feedbacks: float | None
    photo_count: int | None
    has_video: bool
    characteristics_count: int | None


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_target_products() -> list[dict[str, Any]]:
    query = f"""
    WITH latest_store AS (
      SELECT period_start, period_end
      FROM {SCHEMA}.mart_store_summary_30d
      WHERE marketplace = %s
      ORDER BY period_end DESC, run_date DESC
      LIMIT 1
    ),
    latest_meta AS (
      SELECT *
      FROM (
        SELECT
          p.offer_id,
          p.nm_id,
          p.product_name,
          ROW_NUMBER() OVER (
            PARTITION BY p.offer_id
            ORDER BY p.raw_updated_at DESC NULLS LAST, p.loaded_at DESC NULLS LAST
          ) AS rn
        FROM {SCHEMA}.stg_wb_product p
        WHERE COALESCE(p.marketplace, 'wb') = %s
      ) ranked
      WHERE rn = 1
    ),
    latest_bridge AS (
      SELECT *
      FROM (
        SELECT
          b.offer_id,
          b.nm_id,
          b.product_name,
          ROW_NUMBER() OVER (
            PARTITION BY b.offer_id
            ORDER BY b.updated_at DESC, b.bridge_id DESC
          ) AS rn
        FROM {SCHEMA}.dim_product_bridge b
        WHERE b.marketplace = %s
      ) ranked
      WHERE rn = 1
    )
    SELECT
      COALESCE(m.nm_id, b.nm_id) AS nm_id
    FROM {SCHEMA}.mart_pnl_30d_by_product p
    JOIN latest_store s
      ON p.period_start = s.period_start
     AND p.period_end = s.period_end
    LEFT JOIN latest_meta m
      ON m.offer_id = p.offer_id
    LEFT JOIN latest_bridge b
      ON b.offer_id = p.offer_id
    WHERE p.marketplace = %s
      AND COALESCE(m.nm_id, b.nm_id) IS NOT NULL
    ORDER BY p.offer_id
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, MARKETPLACE, MARKETPLACE, MARKETPLACE))
            return [dict(row) for row in cur.fetchall()]


def fetch_search_payload(query: str) -> dict[str, Any] | None:
    params = {
        "appType": 1,
        "curr": "rub",
        "dest": "-1257786",
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": 0,
    }
    query_str = "&".join(f"{key}={value}" for key, value in params.items())
    full_url = f"{WB_SEARCH_ENDPOINT}?{query_str}"
    cmd = [
        "curl",
        "-s",
        "--compressed",
        "-H",
        "User-Agent: Mozilla/5.0",
        "-H",
        "Accept: application/json",
        full_url,
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    if raw.startswith("<!DOCTYPE html>"):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def extract_cards(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            products = data.get("products")
            if isinstance(products, list):
                return [item for item in products if isinstance(item, dict)]
        products = payload.get("products")
        if isinstance(products, list):
            return [item for item in products if isinstance(item, dict)]
    return []


def parse_card(card: dict[str, Any], nm_id: str) -> CardRow | None:
    media_files = card.get("mediaFiles") if isinstance(card.get("mediaFiles"), list) else []
    photos = card.get("photos") if isinstance(card.get("photos"), list) else []
    photo_count = as_int(card.get("photosCount")) or (len(media_files) if media_files else len(photos))
    has_video = bool(card.get("video"))
    characteristics = card.get("characteristics")
    characteristics_count = len(characteristics) if isinstance(characteristics, list) else None
    return CardRow(
        nm_id=nm_id,
        product_title=as_text(card.get("name") or card.get("title")) or None,
        brand=as_text(card.get("brand")) or None,
        rating=as_float(card.get("rating") or card.get("reviewRating")),
        feedbacks=as_float(card.get("feedbacks") or card.get("reviews") or card.get("comments")),
        photo_count=photo_count,
        has_video=has_video,
        characteristics_count=characteristics_count,
    )


def upsert_rows(rows: list[CardRow]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            nm_ids = sorted({row.nm_id for row in rows})
            cur.execute(
                f"""
                DELETE FROM {SCHEMA}.stg_wb_product_card_snapshot
                WHERE marketplace = %s
                  AND nm_id = ANY(%s)
                """,
                (MARKETPLACE, nm_ids),
            )
            args = [
                (
                    row.nm_id,
                    MARKETPLACE,
                    row.product_title,
                    row.brand,
                    row.rating,
                    row.feedbacks,
                    row.photo_count,
                    row.has_video,
                    row.characteristics_count,
                    now_iso(),
                )
                for row in rows
            ]
            cur.executemany(
                f"""
                INSERT INTO {SCHEMA}.stg_wb_product_card_snapshot (
                  nm_id,
                  marketplace,
                  product_title,
                  brand,
                  rating,
                  feedbacks,
                  photo_count,
                  has_video,
                  characteristics_count,
                  captured_at
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (nm_id) DO UPDATE
                SET product_title = EXCLUDED.product_title,
                    brand = EXCLUDED.brand,
                    rating = EXCLUDED.rating,
                    feedbacks = EXCLUDED.feedbacks,
                    photo_count = EXCLUDED.photo_count,
                    has_video = EXCLUDED.has_video,
                    characteristics_count = EXCLUDED.characteristics_count,
                    captured_at = EXCLUDED.captured_at
                """,
                args,
            )


def main() -> None:
    targets = fetch_target_products()
    if not targets:
        print("No nm_ids found for card snapshot.")
        return
    rows: list[CardRow] = []
    for target in targets:
        nm_id = as_text(target.get("nm_id"))
        if not nm_id:
            continue
        payload = fetch_search_payload(nm_id)
        cards = extract_cards(payload) if payload else []
        for card in cards:
            card_nm_id = as_text(card.get("id") or card.get("nmId") or card.get("nm_id"))
            if card_nm_id == nm_id:
                parsed = parse_card(card, nm_id)
                if parsed:
                    rows.append(parsed)
                break
        time.sleep(1.2)
    upsert_rows(rows)
    print(f"product_card_snapshot_rows={len(rows)} products={len(targets)}")


if __name__ == "__main__":
    main()
