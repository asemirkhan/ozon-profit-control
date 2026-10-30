#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import subprocess
from urllib import parse as urlparse
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
COMPETITOR_TOP_N = int(os.getenv("WB_COMPETITOR_TOP_N", "5"))

# Public search capture needs to tolerate local SSL setups; canonical runtime already uses this in dev.
WB_SEARCH_ENDPOINT = "https://search.wb.ru/exactmatch/ru/common/v5/search"



@dataclass(frozen=True)
class CompetitorRow:
    own_nm_id: str
    competitor_nm_id: str
    search_query: str
    competitor_name: str | None
    competitor_price: float | None
    competitor_rating: float | None
    competitor_reviews: float | None
    position: int | None


@dataclass(frozen=True)
class ProductCardRow:
    nm_id: str
    product_title: str | None
    brand: str | None
    rating: float | None
    feedbacks: float | None
    photo_count: int | None
    has_video: bool
    characteristics_count: int | None
    search_query: str | None = None
    search_position: int | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


def normalize_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric > 1000:
        return round(numeric / 100.0, 2)
    return round(numeric, 2)


def fetch_target_products() -> list[dict[str, Any]]:
    query = f"""
    WITH latest_store AS (
      SELECT period_start, period_end
      FROM {SCHEMA}.mart_store_summary_30d
      WHERE marketplace = %s
      ORDER BY period_end DESC, run_date DESC
      LIMIT 1
    ),
    latest_product_meta AS (
      SELECT *
      FROM (
        SELECT
          p.offer_id,
          p.nm_id,
          p.product_name,
          p.vendor_code,
          ROW_NUMBER() OVER (
            PARTITION BY p.offer_id
            ORDER BY p.raw_updated_at DESC NULLS LAST, p.loaded_at DESC NULLS LAST
          ) AS rn
        FROM {SCHEMA}.stg_wb_product p
        WHERE COALESCE(p.marketplace, 'wb') = %s
          AND p.offer_id IS NOT NULL
          AND p.offer_id <> ''
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
      COALESCE(NULLIF(pm.product_name, ''), NULLIF(lb.product_name, ''), p.offer_id) AS product_name,
      COALESCE(pm.nm_id, lb.nm_id) AS nm_id,
      COALESCE(pm.vendor_code, p.offer_id) AS vendor_code
    FROM {SCHEMA}.mart_pnl_30d_by_product p
    JOIN latest_store s
      ON p.period_start = s.period_start
     AND p.period_end = s.period_end
    LEFT JOIN latest_product_meta pm
      ON pm.offer_id = p.offer_id
    LEFT JOIN latest_bridge lb
      ON lb.offer_id = p.offer_id
    WHERE p.marketplace = %s
      AND COALESCE(pm.nm_id, lb.nm_id) IS NOT NULL
      AND COALESCE(pm.nm_id, lb.nm_id) <> ''
    ORDER BY p.offer_id
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, MARKETPLACE, MARKETPLACE, MARKETPLACE))
            return [dict(row) for row in cur.fetchall()]


def extract_products(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("products", "goods", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        for key in ("products", "goods", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def build_search_params(query: str) -> dict[str, Any]:
    return {
        "appType": 1,
        "curr": "rub",
        "dest": "-1257786",
        "query": query,
        "resultset": "catalog",
        "sort": "popular",
        "spp": 0,
        "TestGroup": "no_test",
        "TestID": "no_test",
    }


def fetch_search_payload(query: str) -> dict[str, Any] | list[Any] | None:
    params = build_search_params(query)
    full_url = f"{WB_SEARCH_ENDPOINT}?{urlparse.urlencode(params)}"
    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    cmd = [
        "curl",
        "-s",
        "--compressed",
        "-H",
        f"User-Agent: {user_agent}",
        "-H",
        "Accept: application/json, text/plain, */*",
        full_url,
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        import json

        return json.loads(raw)
    except Exception:
        return None


def parse_product_card(
    card: dict[str, Any],
    nm_id: str,
    *,
    search_query: str | None = None,
    search_position: int | None = None,
) -> ProductCardRow:
    photos = card.get("pics") if isinstance(card.get("pics"), list) else []
    media_files = card.get("mediaFiles") if isinstance(card.get("mediaFiles"), list) else []
    characteristics = card.get("characteristics")
    return ProductCardRow(
        nm_id=nm_id,
        product_title=as_text(card.get("name") or card.get("title")) or None,
        brand=as_text(card.get("brand")) or None,
        rating=as_float(card.get("rating") or card.get("reviewRating")),
        feedbacks=as_float(card.get("feedbacks") or card.get("reviews") or card.get("comments")),
        photo_count=len(photos) if photos else (len(media_files) if media_files else None),
        has_video=bool(card.get("hasVideo") or card.get("video")),
        characteristics_count=len(characteristics) if isinstance(characteristics, list) else None,
        search_query=search_query,
        search_position=search_position,
    )


def _simplify_query(name: str) -> str:
    parts = [part for part in name.split() if part]
    if len(parts) <= 3:
        return name
    return " ".join(parts[:3])


def fetch_competitors_for_product(
    product_name: str,
    own_nm_id: str,
    vendor_code: str | None,
) -> tuple[list[CompetitorRow], ProductCardRow | None]:
    payload = fetch_search_payload(product_name)
    if payload is None:
        return [], None
    products = extract_products(payload)
    results: list[CompetitorRow] = []
    seen: set[str] = set()
    own_card: ProductCardRow | None = None
    for search_position, item in enumerate(products, start=1):
        nm_id = as_text(item.get("id") or item.get("nmId") or item.get("nm_id"))
        if not nm_id or nm_id == own_nm_id:
            if nm_id == own_nm_id and own_card is None:
                own_card = parse_product_card(
                    item,
                    own_nm_id,
                    search_query=product_name,
                    search_position=search_position,
                )
            continue
        if nm_id in seen:
            continue
        seen.add(nm_id)
        name = as_text(item.get("name") or item.get("title"))
        price = normalize_price(item.get("salePriceU") or item.get("priceU") or item.get("salePrice") or item.get("price"))
        rating = as_float(item.get("rating") or item.get("reviewRating"))
        reviews = as_float(item.get("feedbacks") or item.get("reviews") or item.get("comments"))
        results.append(
            CompetitorRow(
                own_nm_id=own_nm_id,
                competitor_nm_id=nm_id,
                search_query=product_name,
                competitor_name=name or None,
                competitor_price=price,
                competitor_rating=rating,
                competitor_reviews=reviews,
                position=search_position,
            )
        )
        if len(results) >= COMPETITOR_TOP_N:
            break
    if own_card is None:
        alt_query = _simplify_query(product_name)
        if alt_query and alt_query != product_name:
            alt_payload = fetch_search_payload(alt_query)
            alt_products = extract_products(alt_payload) if alt_payload else []
            for search_position, item in enumerate(alt_products, start=1):
                nm_id = as_text(item.get("id") or item.get("nmId") or item.get("nm_id"))
                if nm_id == own_nm_id:
                    own_card = parse_product_card(
                        item,
                        own_nm_id,
                        search_query=alt_query,
                        search_position=search_position,
                    )
                    break
    if own_card is None and vendor_code:
        alt_payload = fetch_search_payload(vendor_code)
        alt_products = extract_products(alt_payload) if alt_payload else []
        for search_position, item in enumerate(alt_products, start=1):
            nm_id = as_text(item.get("id") or item.get("nmId") or item.get("nm_id"))
            if nm_id == own_nm_id:
                own_card = parse_product_card(
                    item,
                    own_nm_id,
                    search_query=vendor_code,
                    search_position=search_position,
                )
                break

    return results, own_card


def chunked(iterable: Iterable[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    items = list(iterable)
    return [items[i : i + size] for i in range(0, len(items), size)]


def upsert_rows(rows: list[CompetitorRow]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        with conn.cursor() as cur:
            own_ids = sorted({row.own_nm_id for row in rows})
            cur.execute(
                f"""
                DELETE FROM {SCHEMA}.stg_wb_competitor_snapshot
                WHERE marketplace = %s
                  AND own_nm_id = ANY(%s)
                """,
                (MARKETPLACE, own_ids),
            )
            args = [
                (
                    row.own_nm_id,
                    row.competitor_nm_id,
                    MARKETPLACE,
                    row.search_query,
                    row.competitor_name,
                    row.competitor_price,
                    row.competitor_rating,
                    row.competitor_reviews,
                    row.position,
                    now_iso(),
                )
                for row in rows
            ]
            cur.executemany(
                f"""
                INSERT INTO {SCHEMA}.stg_wb_competitor_snapshot (
                  own_nm_id,
                  competitor_nm_id,
                  marketplace,
                  search_query,
                  competitor_name,
                  competitor_price,
                  competitor_rating,
                  competitor_reviews,
                  position,
                  captured_at
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (own_nm_id, competitor_nm_id, search_query) DO UPDATE
                SET competitor_name = EXCLUDED.competitor_name,
                    competitor_price = EXCLUDED.competitor_price,
                    competitor_rating = EXCLUDED.competitor_rating,
                    competitor_reviews = EXCLUDED.competitor_reviews,
                    position = EXCLUDED.position,
                    captured_at = EXCLUDED.captured_at
                """,
                args,
            )
            cur.executemany(
                f"""
                INSERT INTO {SCHEMA}.fact_wb_competitor_observation (
                  observation_date, own_nm_id, competitor_nm_id, marketplace,
                  search_query, competitor_name, competitor_price,
                  competitor_rating, competitor_reviews, search_position, captured_at
                ) VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observation_date, own_nm_id, competitor_nm_id, search_query)
                DO UPDATE SET competitor_name=EXCLUDED.competitor_name,
                  competitor_price=EXCLUDED.competitor_price,
                  competitor_rating=EXCLUDED.competitor_rating,
                  competitor_reviews=EXCLUDED.competitor_reviews,
                  search_position=EXCLUDED.search_position,
                  captured_at=EXCLUDED.captured_at
                """,
                args,
            )


def upsert_card_rows(rows: list[ProductCardRow]) -> None:
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
                    row.search_query,
                    row.search_position,
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
                  search_query,
                  search_position,
                  captured_at
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (nm_id) DO UPDATE
                SET product_title = EXCLUDED.product_title,
                    brand = EXCLUDED.brand,
                    rating = EXCLUDED.rating,
                    feedbacks = EXCLUDED.feedbacks,
                    photo_count = EXCLUDED.photo_count,
                    has_video = EXCLUDED.has_video,
                    characteristics_count = EXCLUDED.characteristics_count,
                    search_query = EXCLUDED.search_query,
                    search_position = EXCLUDED.search_position,
                    captured_at = EXCLUDED.captured_at
                """,
                args,
            )
            cur.executemany(
                f"""
                INSERT INTO {SCHEMA}.fact_wb_product_card_observation (
                  observation_date, nm_id, marketplace, product_title, brand,
                  rating, feedbacks, photo_count, has_video,
                  characteristics_count, search_query, search_position, captured_at
                ) VALUES (CURRENT_DATE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (observation_date, nm_id) DO UPDATE SET
                  product_title=EXCLUDED.product_title, brand=EXCLUDED.brand,
                  rating=EXCLUDED.rating, feedbacks=EXCLUDED.feedbacks,
                  photo_count=EXCLUDED.photo_count, has_video=EXCLUDED.has_video,
                  characteristics_count=EXCLUDED.characteristics_count,
                  search_query=EXCLUDED.search_query,
                  search_position=EXCLUDED.search_position,
                  captured_at=EXCLUDED.captured_at
                """,
                args,
            )


def main() -> None:
    target_products = fetch_target_products()
    if not target_products:
        print("No products found for competitor snapshot.")
        return

    all_rows: list[CompetitorRow] = []
    card_rows: list[ProductCardRow] = []
    for batch in chunked(target_products, 5):
        for row in batch:
            name = as_text(row.get("product_name"))
            vendor_code = as_text(row.get("vendor_code"))
            nm_id = as_text(row.get("nm_id"))
            if not name or not nm_id:
                continue
            competitors, own_card = fetch_competitors_for_product(name, nm_id, vendor_code or None)
            all_rows.extend(competitors)
            if own_card:
                card_rows.append(own_card)
            time.sleep(0.6)
        time.sleep(1.2)

    upsert_rows(all_rows)
    upsert_card_rows(card_rows)
    print(
        f"competitor_snapshot_rows={len(all_rows)} products={len(target_products)} top_n={COMPETITOR_TOP_N}"
    )
    print(f"product_card_snapshot_rows={len(card_rows)} products={len(target_products)}")


if __name__ == "__main__":
    main()
