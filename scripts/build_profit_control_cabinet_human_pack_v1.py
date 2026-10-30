#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.recommendations import build_recommendation_payload, rule_label
from src.services.wb_expense_granularity import (
    build_wb_expense_granularity_matrix,
    build_wb_product_expense_breakdown,
)
from src.utils.db import get_conn

MOSCOW = ZoneInfo("Europe/Moscow")
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EXPORTS = ROOT / "exports"
DOCS = ROOT / "artifacts"
CABINET_DATA = ROOT / "cabinet" / "data"
PACK_DIR = EXPORTS / "profit_control_cabinet_human_pack_v1"
PRODUCT_IMAGE_MANIFEST = ROOT / "cabinet" / "data" / "wb_product_image_manifest.json"

load_dotenv(ROOT / ".env")

SCHEMA = (
    os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA")
    or os.getenv("SCHEMA")
    or os.getenv("OZON_SCHEMA")
    or "client_wb_probe_2"
)
MARKETPLACE = os.getenv("PROFIT_CONTROL_MARKETPLACE", "wb")
CURRENT_WINDOW_DAYS = int(os.getenv("PROFIT_CONTROL_CURRENT_WINDOW_DAYS", "7"))
BASE_WINDOW_DAYS = int(os.getenv("PROFIT_CONTROL_BASE_WINDOW_DAYS", "28"))
SERIES_TOTAL_DAYS = int(
    os.getenv(
        "PROFIT_CONTROL_SERIES_TOTAL_DAYS",
        "90",
    )
)

FINANCE_LAYER_LABELS = [
    {
        "key": "strict",
        "label": "Полностью подтвержденные данные",
        "description": "День уже закрыт и его можно использовать как основную правду по прибыли.",
    },
    {
        "key": "preliminary",
        "label": "Предварительные данные",
        "description": "Часть данных уже пришла, но день еще нельзя считать окончательно закрытым.",
    },
    {
        "key": "waking_up",
        "label": "Данные начали поступать, но день еще не закрыт",
        "description": "Источник уже открылся, но использовать такой день как финальную прибыль еще рано.",
    },
]

ACTION_TYPE_TITLES = {
    "ads": "товары с проблемной рекламой",
    "inventory": "товары с риском дефицита",
    "logistics": "товары с тяжелой логистикой",
    "margin": "товары со слишком тонкой маржой",
    "other": "товары с приоритетными отклонениями",
}

PRIMARY_PROBLEM_ZONE = {
    "negative_profit_after_ads": "Убыток после рекламы",
    "ads_eating_profit": "Реклама съедает прибыль",
    "ads_without_sales": "Реклама без продаж",
    "stockout_risk": "Риск дефицита",
    "high_logistics_share": "Слишком дорогая логистика",
    "low_margin_after_ads": "Слишком низкая маржа",
}

SECONDARY_PROBLEM_ZONE = {
    "negative_profit_after_ads": "Слабая экономика товара",
    "ads_eating_profit": "Нужно сдержать рекламную нагрузку",
    "ads_without_sales": "Нужно проверить карточку и наличие",
    "stockout_risk": "Нужно быстро пополнить остаток",
    "high_logistics_share": "Нужно проверить поставку и упаковку",
    "low_margin_after_ads": "Нужно увеличить запас маржи",
}


def ensure_schema(schema: str) -> str:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema: {schema}")
    return schema


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    number = as_float(value)
    if number is None:
        return None
    return int(round(number))


def as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def fmt_money(value: float | None) -> str:
    if value is None:
        return "0"
    return f"{value:,.0f}".replace(",", " ")


def fmt_count(value: float | None) -> str:
    if value is None:
        return "0"
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def fmt_pct(value: float | None) -> str:
    if value is None:
        return "0%"
    return f"{value * 100:.1f}%".replace(".", ",")


def serialize_expense_breakdown_rows(
    rows: list[dict[str, Any]],
    *,
    hide_zero: bool,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        if as_text(row.get("bucket_subtype")).lower() == "cogs":
            continue
        if as_text(row.get("should_be_visible_in_cabinet_yes_no")).lower() == "no":
            continue
        amount = as_float(row.get("amount")) or 0.0
        if hide_zero and abs(amount) < 1e-9:
            continue
        items.append(
            {
                "bucketGroup": as_text(row.get("bucket_group")),
                "bucketSubtype": as_text(row.get("bucket_subtype")),
                "label": as_text(row.get("label")),
                "amount": round(amount, 2),
                "productLevelAmount": round(as_float(row.get("product_level_amount")) or 0.0, 2),
                "storeLevelAmount": round(as_float(row.get("store_level_amount")) or 0.0, 2),
                "mixedAmount": round(as_float(row.get("mixed_amount")) or 0.0, 2),
                "sourceLayer": as_text(row.get("source_layer")),
                "attributionMethod": as_text(row.get("attribution_method")),
                "confidence": as_text(row.get("confidence")),
                "reason": as_text(row.get("reason")),
            }
        )
    return items


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) < 1e-9:
        return None
    return numerator / denominator


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def date_from_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def money_range(effect_value: float | None) -> dict[str, str]:
    if effect_value is None:
        return {"Осторожно": "0", "Рабочая оценка": "0", "Оптимистично": "0"}
    return {
        "Осторожно": fmt_money(max(effect_value * 0.6, 0)),
        "Рабочая оценка": fmt_money(max(effect_value, 0)),
        "Оптимистично": fmt_money(max(effect_value * 1.4, 0)),
    }


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    tmp_path.replace(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def extract_product_image_url(raw_payload: Any) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    photos = raw_payload.get("photos")
    if isinstance(photos, list) and photos:
        first = photos[0]
        if isinstance(first, dict):
            for key in ("big", "hq", "tm", "square"):
                value = first.get(key)
                if value:
                    return str(value)
        if isinstance(first, str):
            return first
    for key in ("photo", "image", "img", "pics", "images", "media", "mediaFiles"):
        value = raw_payload.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first:
                return first
            if isinstance(first, dict):
                for subkey in ("big", "hq", "tm", "square", "url"):
                    subval = first.get(subkey)
                    if subval:
                        return str(subval)
    return None


def extract_product_title(raw_payload: Any) -> str | None:
    if not isinstance(raw_payload, dict):
        return None
    for key in ("title", "name", "imt_name"):
        value = raw_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_product_rating(raw_payload: Any) -> float | None:
    if not isinstance(raw_payload, dict):
        return None
    for key in ("rating", "reviewRating"):
        value = raw_payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def extract_product_feedbacks(raw_payload: Any) -> float | None:
    if not isinstance(raw_payload, dict):
        return None
    for key in ("feedbacks", "reviews", "comments"):
        value = raw_payload.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def query_rows(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    ensure_schema(SCHEMA)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]


def query_one(query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = query_rows(query, params)
    return rows[0] if rows else None


def table_exists(table_name: str) -> bool:
    row = query_one(
        """
        SELECT EXISTS (
          SELECT 1
          FROM information_schema.tables
          WHERE table_schema = %s AND table_name = %s
        ) AS present
        """,
        (SCHEMA, table_name),
    )
    return bool(row and row["present"])


def latest_store_summary() -> dict[str, Any]:
    query = f"""
    SELECT *
    FROM {SCHEMA}.mart_store_summary_30d
    WHERE marketplace = %s
    ORDER BY period_end DESC, run_date DESC
    LIMIT 1
    """
    row = query_one(query, (MARKETPLACE,))
    if not row:
        raise RuntimeError("mart_store_summary_30d is empty; build marts before cabinet pack.")
    return row


def fetch_product_rows(period_start: date, period_end: date) -> list[dict[str, Any]]:
    query = f"""
    WITH latest_product_meta AS (
      SELECT *
      FROM (
        SELECT
          p.nm_id,
          p.offer_id,
          LOWER(p.offer_id) AS offer_id_lower,
          p.product_name,
          p.brand,
          p.category_name,
          p.barcode,
          p.raw_payload,
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
          LOWER(b.offer_id) AS offer_id_lower,
          b.nm_id,
          b.sku,
          b.product_name,
          b.brand,
          b.category_name,
          ROW_NUMBER() OVER (
            PARTITION BY b.offer_id
            ORDER BY b.updated_at DESC, b.bridge_id DESC
          ) AS rn
        FROM {SCHEMA}.dim_product_bridge b
        WHERE b.marketplace = %s
      ) ranked
      WHERE rn = 1
    ),
    latest_price AS (
      SELECT
        p.offer_id,
        CASE
          WHEN p.units_sold IS NULL OR p.units_sold = 0 THEN NULL
          ELSE p.revenue_gross / NULLIF(p.units_sold, 0)
        END AS sale_price
      FROM {SCHEMA}.mart_pnl_30d_by_product p
      WHERE p.marketplace = %s
        AND p.period_start = %s
        AND p.period_end = %s
    ),
    inventory AS (
      SELECT
        i.offer_id,
        i.stock_available,
        i.days_of_stock_30d,
        i.out_of_stock_risk
      FROM {SCHEMA}.mart_inventory_snapshot i
      WHERE i.marketplace = %s
    ),
    actions AS (
      SELECT
        a.offer_id,
        a.primary_rule_code,
        a.action_code,
        a.action_type,
        a.action_priority,
        a.action_priority_score,
        a.action_title,
        a.action_summary,
        a.why_now,
        a.first_step,
        a.expected_effect_note,
        a.money_impact_30d,
        a.revenue_at_risk_30d,
        a.estimated_profit_uplift_30d,
        a.estimated_revenue_protection_30d,
        a.estimated_gain_if_fixed,
        a.supporting_rule_codes,
        a.supporting_problem_count,
        a.attribution_quality,
        a.confidence
      FROM {SCHEMA}.mart_action_queue_14d a
      WHERE a.marketplace = %s
    )
    SELECT
      p.offer_id,
      COALESCE(NULLIF(pm.product_name, ''), NULLIF(lb.product_name, ''), NULLIF(p.product_name, '~'), p.offer_id) AS product_name,
      p.units_sold,
      p.revenue_gross,
      p.commission_amount,
      p.logistics_amount,
      p.services_amount,
      p.storage_amount,
      p.payment_processing_amount,
      p.penalties_amount,
      p.other_amount,
      p.marketplace_other_fees_amount,
      p.returns_amount,
      p.cogs_amount,
      p.ads_spend,
      p.profit_before_ads,
      p.profit_after_ads,
      p.margin_after_ads,
      p.profit_per_unit_after_ads,
      COALESCE(pm.nm_id, lb.nm_id) AS nm_id,
      COALESCE(NULLIF(pm.brand, ''), NULLIF(lb.brand, '')) AS brand,
      COALESCE(NULLIF(pm.category_name, ''), NULLIF(lb.category_name, '')) AS category_name,
      pm.raw_payload AS product_raw_payload,
      COALESCE(lb.sku, '~') AS sku,
      lp.sale_price AS current_price,
      inv.stock_available,
      inv.days_of_stock_30d,
      inv.out_of_stock_risk,
      act.primary_rule_code,
      act.action_code,
      act.action_type,
      act.action_priority,
      act.action_priority_score,
      act.action_title,
      act.action_summary,
      act.why_now,
      act.first_step,
      act.expected_effect_note,
      act.money_impact_30d,
      act.revenue_at_risk_30d,
      act.estimated_profit_uplift_30d,
      act.estimated_revenue_protection_30d,
      act.estimated_gain_if_fixed,
      act.supporting_rule_codes,
      act.supporting_problem_count,
      act.attribution_quality,
      act.confidence
    FROM {SCHEMA}.mart_pnl_30d_by_product p
    LEFT JOIN latest_product_meta pm
      ON pm.offer_id_lower = LOWER(p.offer_id)
    LEFT JOIN latest_bridge lb
      ON lb.offer_id_lower = LOWER(p.offer_id)
    LEFT JOIN latest_price lp
      ON lp.offer_id = p.offer_id
    LEFT JOIN inventory inv
      ON inv.offer_id = p.offer_id
    LEFT JOIN actions act
      ON act.offer_id = p.offer_id
    WHERE p.marketplace = %s
      AND p.period_start = %s
      AND p.period_end = %s
    ORDER BY COALESCE(act.action_priority, 9999), p.profit_after_ads ASC, p.offer_id
    """
    return query_rows(
        query,
        (
            MARKETPLACE,
            MARKETPLACE,
            MARKETPLACE,
            period_start,
            period_end,
            MARKETPLACE,
            MARKETPLACE,
            MARKETPLACE,
            period_start,
            period_end,
        ),
    )


def fetch_daily_rows(window_start: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      d.date,
      d.marketplace,
      d.offer_id,
      d.product_name,
      d.units_sold,
      d.revenue_gross,
      d.returns_amount,
      d.payout,
      d.commission_amount,
      d.logistics_amount,
      d.services_amount,
      d.storage_amount,
      d.payment_processing_amount,
      d.penalties_amount,
      d.other_amount,
      d.cogs_amount,
      d.ads_spend,
      d.profit_before_ads,
      d.profit_after_ads
    FROM {SCHEMA}.mart_pnl_daily_by_product d
    WHERE d.marketplace = %s
      AND d.date >= %s
    ORDER BY d.date, d.offer_id
    """
    return query_rows(query, (MARKETPLACE, window_start))


def clamp_pnl_rows(rows: list[dict[str, Any]], finance_cutoff: date | None) -> list[dict[str, Any]]:
    if finance_cutoff is None:
        return rows
    clamped: list[dict[str, Any]] = []
    for row in rows:
        row_day = date_from_value(row["date"])
        if row_day <= finance_cutoff:
            clamped.append(row)
            continue
        next_row = dict(row)
        for key in [
            "returns_amount",
            "payout",
            "commission_amount",
            "logistics_amount",
            "services_amount",
            "storage_amount",
            "payment_processing_amount",
            "penalties_amount",
            "other_amount",
            "profit_before_ads",
            "profit_after_ads",
        ]:
            next_row[key] = 0
        clamped.append(next_row)
    return clamped


def aggregate_product_period_metrics(
    daily_rows: list[dict[str, Any]],
    period_start: date,
    period_end: date,
) -> dict[str, dict[str, float | None]]:
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "units_sold": 0.0,
            "revenue_gross": 0.0,
            "returns_amount": 0.0,
            "payout": 0.0,
            "commission_amount": 0.0,
            "logistics_amount": 0.0,
            "services_amount": 0.0,
            "storage_amount": 0.0,
            "payment_processing_amount": 0.0,
            "penalties_amount": 0.0,
            "other_amount": 0.0,
            "cogs_amount": 0.0,
            "ads_spend": 0.0,
            "profit_before_ads": 0.0,
            "profit_after_ads": 0.0,
        }
    )
    for row in daily_rows:
        row_day = date_from_value(row["date"])
        if row_day < period_start or row_day > period_end:
            continue
        bucket = grouped[row["offer_id"]]
        for key in bucket:
            bucket[key] += as_float(row.get(key)) or 0.0

    result: dict[str, dict[str, float | None]] = {}
    for offer_id, values in grouped.items():
        revenue = values["revenue_gross"]
        units = values["units_sold"]
        other_fees = (
            values["services_amount"]
            + values["storage_amount"]
            + values["payment_processing_amount"]
            + values["penalties_amount"]
            + values["other_amount"]
        )
        result[offer_id] = {
            **values,
            "marketplace_other_fees_amount": other_fees,
            "margin_after_ads": (values["profit_after_ads"] / revenue) if revenue > 0 else None,
            "profit_per_unit_after_ads": (values["profit_after_ads"] / units) if units else None,
        }
    return result


def merge_product_period_metrics(
    product_rows: list[dict[str, Any]],
    period_metrics: dict[str, dict[str, float | None]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for row in product_rows:
        next_row = dict(row)
        metrics = period_metrics.get(row["offer_id"])
        if metrics:
            next_row.update(metrics)
        merged.append(next_row)
    return merged


def merge_store_summary_from_daily(
    store_summary: dict[str, Any],
    daily_rows: list[dict[str, Any]],
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    merged = dict(store_summary)
    totals = {
        "units_sold_total": 0.0,
        "revenue_total": 0.0,
        "ads_spend_total": 0.0,
        "logistics_total": 0.0,
        "profit_before_ads_total": 0.0,
        "profit_after_ads_total": 0.0,
    }
    for row in daily_rows:
        row_day = date_from_value(row["date"])
        if row_day < period_start or row_day > period_end:
            continue
        totals["units_sold_total"] += as_float(row.get("units_sold")) or 0.0
        totals["revenue_total"] += as_float(row.get("revenue_gross")) or 0.0
        totals["ads_spend_total"] += as_float(row.get("ads_spend")) or 0.0
        totals["logistics_total"] += as_float(row.get("logistics_amount")) or 0.0
        totals["profit_before_ads_total"] += as_float(row.get("profit_before_ads")) or 0.0
        totals["profit_after_ads_total"] += as_float(row.get("profit_after_ads")) or 0.0
    merged.update(totals)
    revenue_total = totals["revenue_total"]
    merged["margin_after_ads_total"] = (
        totals["profit_after_ads_total"] / revenue_total if revenue_total > 0 else None
    )
    return merged


def fetch_region_rows(window_start: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      DATE(s.sale_date) AS day,
      COALESCE(NULLIF(s.offer_id, ''), '~') AS offer_id,
      COALESCE(NULLIF(s.region_name, ''), 'Не указан регион') AS bucket_name,
      SUM(COALESCE(s.quantity, 0))::numeric(18,2) AS units_sold,
      SUM(COALESCE(s.revenue_gross, 0))::numeric(18,2) AS revenue_gross
    FROM {SCHEMA}.stg_wb_sale s
    WHERE COALESCE(s.marketplace, 'wb') = %s
      AND DATE(s.sale_date) >= %s
    GROUP BY 1,2,3
    ORDER BY 1,2,3
    """
    return query_rows(query, (MARKETPLACE, window_start))


def fetch_warehouse_rows(window_start: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      DATE(s.sale_date) AS day,
      COALESCE(NULLIF(s.offer_id, ''), '~') AS offer_id,
      COALESCE(NULLIF(s.warehouse_name, ''), 'Не указан склад') AS bucket_name,
      SUM(COALESCE(s.quantity, 0))::numeric(18,2) AS units_sold,
      SUM(COALESCE(s.revenue_gross, 0))::numeric(18,2) AS revenue_gross
    FROM {SCHEMA}.stg_wb_sale s
    WHERE COALESCE(s.marketplace, 'wb') = %s
      AND DATE(s.sale_date) >= %s
    GROUP BY 1,2,3
    ORDER BY 1,2,3
    """
    return query_rows(query, (MARKETPLACE, window_start))


def fetch_competitor_snapshot() -> list[dict[str, Any]]:
    if not table_exists("stg_wb_competitor_snapshot"):
        return []
    query = f"""
    SELECT
      own_nm_id,
      competitor_nm_id,
      search_query,
      competitor_name,
      competitor_price,
      competitor_rating,
      competitor_reviews,
      position
    FROM {SCHEMA}.stg_wb_competitor_snapshot
    WHERE marketplace = %s
    ORDER BY own_nm_id, position NULLS LAST
    """
    return query_rows(query, (MARKETPLACE,))


def fetch_product_card_snapshot() -> dict[str, dict[str, Any]]:
    if not table_exists("stg_wb_product_card_snapshot"):
        return {}
    query = f"""
    SELECT
      nm_id,
      product_title,
      brand,
      rating,
      feedbacks,
      photo_count,
      has_video,
      characteristics_count
    FROM {SCHEMA}.stg_wb_product_card_snapshot
    WHERE marketplace = %s
    """
    rows = query_rows(query, (MARKETPLACE,))
    return {as_text(row.get("nm_id")): row for row in rows}


def fetch_finance_boundary() -> dict[str, Any]:
    if not table_exists("finance_day_state"):
        return {
            "confirmedThrough": None,
            "note": "В текущей схеме еще нет подтвержденной финансовой границы.",
            "freshTailDays": [],
        }

    query = f"""
    WITH strict_day AS (
      SELECT MAX(day) AS confirmed_through
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    ),
    tail_days AS (
      SELECT day
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = FALSE
      ORDER BY day DESC
      LIMIT 3
    )
    SELECT
      (SELECT confirmed_through FROM strict_day) AS confirmed_through,
      ARRAY(SELECT day::text FROM tail_days) AS fresh_tail_days
    """
    row = query_one(query)
    strict_through = row["confirmed_through"] if row else None
    fresh_tail_days = list(row["fresh_tail_days"] or []) if row else []
    if strict_through:
        note = (
            "Продажи обновляются отдельно и могут быть более свежими. "
            "Полностью подтвержденные данные по финансам доступны "
            f"по {strict_through.isoformat()}. Более свежие дни остаются в предварительном слое, пока источник не закроется окончательно."
        )
    else:
        note = "Продажи обновляются отдельно. Финансовые дни еще не перешли в полностью подтвержденный слой."
    return {
        "confirmedThrough": strict_through.isoformat() if strict_through else None,
        "note": note,
        "freshTailDays": fresh_tail_days,
    }


def finance_clamp_cutoff(boundary: dict[str, Any]) -> date | None:
    confirmed = boundary.get("confirmedThrough")
    if not confirmed:
        return None
    try:
        return date.fromisoformat(str(confirmed))
    except ValueError:
        return None


def build_series_index(daily_rows: list[dict[str, Any]]) -> tuple[date | None, dict[str, list[dict[str, Any]]], dict[date, dict[str, float]]]:
    latest_day: date | None = None
    by_offer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    store_by_day: dict[date, dict[str, float]] = defaultdict(
        lambda: {
            "revenue": 0.0,
            "profit": 0.0,
            "orders": 0.0,
            "ads": 0.0,
            "logistics": 0.0,
            "commission": 0.0,
            "acquiring": 0.0,
            "storage": 0.0,
            "withholding": 0.0,
            "expenses": 0.0,
        }
    )
    for row in daily_rows:
        row_day = date_from_value(row["date"])
        latest_day = row_day if latest_day is None or row_day > latest_day else latest_day
        revenue = as_float(row.get("revenue_gross")) or 0.0
        profit = as_float(row.get("profit_after_ads")) or 0.0
        orders = as_float(row.get("units_sold")) or 0.0
        ads = as_float(row.get("ads_spend")) or 0.0
        logistics = as_float(row.get("logistics_amount")) or 0.0
        commission = as_float(row.get("commission_amount")) or 0.0
        acquiring = as_float(row.get("payment_processing_amount")) or 0.0
        storage = as_float(row.get("storage_amount")) or 0.0
        withholding = (
            (as_float(row.get("penalties_amount")) or 0.0)
            + (as_float(row.get("other_amount")) or 0.0)
            + (as_float(row.get("services_amount")) or 0.0)
        )
        by_offer[row["offer_id"]].append(
            {
                "day": row_day,
                "revenue": revenue,
                "profit": profit,
                "orders": orders,
                "ads": ads,
                "logistics": logistics,
                "commission": commission,
                "acquiring": acquiring,
                "storage": storage,
                "withholding": withholding,
                "expenses": ads + logistics + commission + acquiring + storage + withholding,
            }
        )
        bucket = store_by_day[row_day]
        bucket["revenue"] += revenue
        bucket["profit"] += profit
        bucket["orders"] += orders
        bucket["ads"] += ads
        bucket["logistics"] += logistics
        bucket["commission"] += commission
        bucket["acquiring"] += acquiring
        bucket["storage"] += storage
        bucket["withholding"] += withholding
        bucket["expenses"] += ads + logistics + commission + acquiring + storage + withholding
    for rows in by_offer.values():
        rows.sort(key=lambda item: item["day"])
    return latest_day, by_offer, store_by_day


def build_window(rows: list[dict[str, Any]], latest_day: date | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if latest_day is None:
        return [], []
    current_start = latest_day - timedelta(days=CURRENT_WINDOW_DAYS - 1)
    base_start = current_start - timedelta(days=BASE_WINDOW_DAYS)
    current_rows = [row for row in rows if current_start <= row["day"] <= latest_day]
    base_rows = [row for row in rows if base_start <= row["day"] < current_start]
    return current_rows, base_rows


def build_window_metrics(rows: list[dict[str, Any]], latest_day: date | None) -> dict[str, float | None]:
    current_rows, base_rows = build_window(rows, latest_day)
    current_orders = mean([row["orders"] for row in current_rows])
    base_orders = mean([row["orders"] for row in base_rows])
    current_revenue = mean([row["revenue"] for row in current_rows])
    base_revenue = mean([row["revenue"] for row in base_rows])
    current_profit = mean([row["profit"] for row in current_rows])
    base_profit = mean([row["profit"] for row in base_rows])
    current_ads = mean([row["ads"] for row in current_rows])
    base_ads = mean([row["ads"] for row in base_rows])
    return {
        "currentUnitsPerDay": current_orders,
        "baseUnitsPerDay": base_orders,
        "currentOrdersPerDay": current_orders,
        "baseOrdersPerDay": base_orders,
        "currentRevenuePerDay": current_revenue,
        "baseRevenuePerDay": base_revenue,
        "currentProfitPerDay": current_profit,
        "baseProfitPerDay": base_profit,
        "currentAdSpendPerDay": current_ads,
        "baseAdSpendPerDay": base_ads,
    }


def series_rows(
    rows_by_day: dict[date, dict[str, float]],
    latest_day: date | None,
    *,
    finance_cutoff: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if latest_day is None:
        return [], []
    start_day = latest_day - timedelta(days=SERIES_TOTAL_DAYS - 1)
    income_rows: list[dict[str, Any]] = []
    expense_rows: list[dict[str, Any]] = []
    for offset in range(SERIES_TOTAL_DAYS):
        day_value = start_day + timedelta(days=offset)
        bucket = rows_by_day.get(day_value, {})
        finance_allowed = finance_cutoff is None or day_value <= finance_cutoff
        profit = bucket.get("profit", 0.0)
        expenses = bucket.get("expenses", 0.0)
        ads = bucket.get("ads", 0.0)
        logistics = bucket.get("logistics", 0.0)
        commission = bucket.get("commission", 0.0)
        acquiring = bucket.get("acquiring", 0.0)
        storage = bucket.get("storage", 0.0)
        withholding = bucket.get("withholding", 0.0)
        income_rows.append(
            {
                "day": day_value.isoformat(),
                "revenue": round(bucket.get("revenue", 0.0), 2),
                # Financial result is unknown after the confirmed boundary.
                # A null produces a chart gap; zero would assert a false loss.
                "profitAfterAds": round(profit, 2) if finance_allowed else None,
                "orders": round(bucket.get("orders", 0.0), 2),
                "expenses": round(expenses, 2),
                "isPreliminary": not finance_allowed,
            }
        )
        expense_rows.append(
            {
                "day": day_value.isoformat(),
                # A missing value in an unfinished period is unknown, not zero.
                # Recharts leaves null points empty instead of drawing a false fall.
                "total": None if not finance_allowed and expenses == 0 else round(expenses, 2),
                "ads": None if not finance_allowed and ads == 0 else round(ads, 2),
                "logistics": None if not finance_allowed and logistics == 0 else round(logistics, 2),
                "commission": None if not finance_allowed and commission == 0 else round(commission, 2),
                "acquiring": None if not finance_allowed and acquiring == 0 else round(acquiring, 2),
                "storage": None if not finance_allowed and storage == 0 else round(storage, 2),
                "withholding": None if not finance_allowed and withholding == 0 else round(withholding, 2),
                "isPreliminary": not finance_allowed,
            }
        )
    return income_rows, expense_rows


def build_geo_rows(
    rows: list[dict[str, Any]],
    latest_day: date | None,
    *,
    offer_id: str | None = None,
) -> list[dict[str, Any]]:
    if latest_day is None:
        return []
    current_start = latest_day - timedelta(days=CURRENT_WINDOW_DAYS - 1)
    base_start = current_start - timedelta(days=BASE_WINDOW_DAYS)
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "current_revenue": 0.0,
            "base_revenue": 0.0,
            "current_orders": 0.0,
            "base_orders": 0.0,
        }
    )
    for row in rows:
        if offer_id is not None and row["offer_id"] != offer_id:
            continue
        row_day = date_from_value(row["day"])
        if row_day < base_start or row_day > latest_day:
            continue
        bucket = grouped[row["bucket_name"]]
        revenue = as_float(row.get("revenue_gross")) or 0.0
        orders = as_float(row.get("units_sold")) or 0.0
        if current_start <= row_day <= latest_day:
            bucket["current_revenue"] += revenue
            bucket["current_orders"] += orders
        elif base_start <= row_day < current_start:
            bucket["base_revenue"] += revenue
            bucket["base_orders"] += orders

    result: list[dict[str, Any]] = []
    total_delta = sum(abs(bucket["current_revenue"] / CURRENT_WINDOW_DAYS - bucket["base_revenue"] / BASE_WINDOW_DAYS) for bucket in grouped.values())
    for bucket_name, bucket in grouped.items():
        current_revenue_per_day = bucket["current_revenue"] / CURRENT_WINDOW_DAYS
        base_revenue_per_day = bucket["base_revenue"] / BASE_WINDOW_DAYS if BASE_WINDOW_DAYS else 0.0
        current_orders_per_day = bucket["current_orders"] / CURRENT_WINDOW_DAYS
        base_orders_per_day = bucket["base_orders"] / BASE_WINDOW_DAYS if BASE_WINDOW_DAYS else 0.0
        revenue_delta = current_revenue_per_day - base_revenue_per_day
        orders_delta = current_orders_per_day - base_orders_per_day
        contribution = abs(revenue_delta) / total_delta if total_delta > 0 else 0.0
        revenue_delta_pct = safe_div(revenue_delta, base_revenue_per_day) if base_revenue_per_day else None
        result.append(
            {
                "name": bucket_name,
                "revenueCurrentPerDay": round(current_revenue_per_day, 2),
                "revenueBasePerDay": round(base_revenue_per_day, 2),
                "revenueDeltaPerDay": round(revenue_delta, 2),
                "revenueDeltaPct": round(revenue_delta_pct, 4) if revenue_delta_pct is not None else None,
                "ordersCurrentPerDay": round(current_orders_per_day, 2),
                "ordersBasePerDay": round(base_orders_per_day, 2),
                "ordersDeltaPerDay": round(orders_delta, 2),
                "contribution": round(contribution, 4),
                "reliability": "high",
            }
        )
    result.sort(key=lambda item: abs(item["revenueDeltaPerDay"]), reverse=True)
    return result[:10]


def choose_primary_problem(rule_code: str | None, orders_delta: float | None, stock_days: float | None) -> str:
    if rule_code:
        return PRIMARY_PROBLEM_ZONE.get(rule_code, rule_label(rule_code))
    if stock_days is not None and stock_days > 45:
        return "Слишком длинный запас"
    if orders_delta is not None and orders_delta < 0:
        return "Снижение спроса"
    return "Нужна дополнительная диагностика"


def choose_secondary_problem(rule_code: str | None, stock_risk: str | None) -> str:
    if rule_code:
        return SECONDARY_PROBLEM_ZONE.get(rule_code, "Подтвердить причину на следующем цикле")
    if stock_risk and stock_risk not in {"ok", "no_sales"}:
        return "Нужно проверить остатки и доступность"
    return "Нет выраженной второй проблемы"


def why_main_problem(rule_code: str | None) -> str:
    mapping = {
        "negative_profit_after_ads": "Товар уже уходит в минус после рекламы, поэтому любая дополнительная активность усиливает потери.",
        "ads_eating_profit": "Реклама забирает слишком большую часть результата и мешает товару оставаться устойчиво прибыльным.",
        "ads_without_sales": "Бюджет расходуется без подтвержденной отдачи, значит сначала нужно остановить прямые потери.",
        "stockout_risk": "Риск дефицита ограничивает будущую выручку, поэтому приоритетом становится пополнение, а не разгон спроса.",
        "high_logistics_share": "Логистика уже съедает заметную часть выручки, и рост продаж без исправления экономики только усилит давление на прибыль.",
        "low_margin_after_ads": "Маржа после рекламы слишком тонкая и не оставляет товару запаса прочности.",
    }
    return mapping.get(rule_code or "", "Главная проблема выбрана по текущему денежному эффекту и силе сигнала.")


def build_store_pack(
    store_summary: dict[str, Any],
    product_rows: list[dict[str, Any]],
    store_income_rows: list[dict[str, Any]],
    store_expense_rows: list[dict[str, Any]],
    store_expense_breakdown: list[dict[str, Any]],
    region_rows: list[dict[str, Any]],
    warehouse_rows: list[dict[str, Any]],
    latest_day: date | None,
) -> dict[str, Any]:
    store_series_index = {
        date_from_value(row["day"]): {
            "revenue": as_float(row["revenue"]) or 0.0,
            "profit": as_float(row["profitAfterAds"]) or 0.0,
            "orders": as_float(row["orders"]) or 0.0,
            "ads": as_float(next(item["ads"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
            "logistics": as_float(next(item["logistics"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
            "commission": as_float(next(item["commission"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
            "acquiring": as_float(next(item["acquiring"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
            "storage": as_float(next(item["storage"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
            "withholding": as_float(next(item["withholding"] for item in store_expense_rows if item["day"] == row["day"])) or 0.0,
        }
        for row in store_income_rows
    }
    store_window = build_window_metrics(
        [
            {
                "day": day_value,
                "revenue": values["revenue"],
                "profit": values["profit"],
                "orders": values["orders"],
                "ads": values["ads"],
            }
            for day_value, values in store_series_index.items()
        ],
        latest_day,
    )
    current_orders_drag = None
    current_revenue_drag = None
    current_profit_drag = None
    if store_window["currentOrdersPerDay"] is not None and store_window["baseOrdersPerDay"] is not None:
        current_orders_drag = store_window["currentOrdersPerDay"] - store_window["baseOrdersPerDay"]
    if store_window["currentRevenuePerDay"] is not None and store_window["baseRevenuePerDay"] is not None:
        current_revenue_drag = store_window["currentRevenuePerDay"] - store_window["baseRevenuePerDay"]
    if store_window["currentProfitPerDay"] is not None and store_window["baseProfitPerDay"] is not None:
        current_profit_drag = store_window["currentProfitPerDay"] - store_window["baseProfitPerDay"]

    operating_expenses_total = sum(
        (as_float(row.get("commission_amount")) or 0.0)
        + (as_float(row.get("logistics_amount")) or 0.0)
        + (as_float(row.get("services_amount")) or 0.0)
        + (as_float(row.get("storage_amount")) or 0.0)
        + (as_float(row.get("payment_processing_amount")) or 0.0)
        + (as_float(row.get("penalties_amount")) or 0.0)
        + (as_float(row.get("other_amount")) or 0.0)
        for row in product_rows
    )
    action_mix = Counter(row.get("action_type") or "other" for row in product_rows)
    scenario_mix = ", ".join(
        f"{ACTION_TYPE_TITLES.get(key, key)}: {count}"
        for key, count in action_mix.most_common()
    ) or "Приоритетные группы пока не определены."

    window_summary = [
        {
            "metric": "revenue",
            "currentPerDay": store_window["currentRevenuePerDay"],
            "basePerDay": store_window["baseRevenuePerDay"],
            "deltaPerDay": current_revenue_drag,
            "deltaPct": safe_div(current_revenue_drag, store_window["baseRevenuePerDay"]) if current_revenue_drag is not None else None,
        },
        {
            "metric": "profit_after_ads",
            "currentPerDay": store_window["currentProfitPerDay"],
            "basePerDay": store_window["baseProfitPerDay"],
            "deltaPerDay": current_profit_drag,
            "deltaPct": safe_div(current_profit_drag, store_window["baseProfitPerDay"]) if current_profit_drag is not None else None,
        },
        {
            "metric": "orders",
            "currentPerDay": store_window["currentOrdersPerDay"],
            "basePerDay": store_window["baseOrdersPerDay"],
            "deltaPerDay": current_orders_drag,
            "deltaPct": safe_div(current_orders_drag, store_window["baseOrdersPerDay"]) if current_orders_drag is not None else None,
        },
        {
            "metric": "ads",
            "currentPerDay": mean([as_float(row["ads"]) or 0.0 for row in store_expense_rows[-CURRENT_WINDOW_DAYS:]]),
            "basePerDay": mean([as_float(row["ads"]) or 0.0 for row in store_expense_rows[:-CURRENT_WINDOW_DAYS]]),
            "deltaPerDay": None,
            "deltaPct": None,
        },
        {
            "metric": "commission",
            "currentPerDay": mean([as_float(row["commission"]) or 0.0 for row in store_expense_rows[-CURRENT_WINDOW_DAYS:]]),
            "basePerDay": mean([as_float(row["commission"]) or 0.0 for row in store_expense_rows[:-CURRENT_WINDOW_DAYS]]),
            "deltaPerDay": None,
            "deltaPct": None,
        },
        {
            "metric": "storage",
            "currentPerDay": mean([as_float(row["storage"]) or 0.0 for row in store_expense_rows[-CURRENT_WINDOW_DAYS:]]),
            "basePerDay": mean([as_float(row["storage"]) or 0.0 for row in store_expense_rows[:-CURRENT_WINDOW_DAYS]]),
            "deltaPerDay": None,
            "deltaPct": None,
        },
    ]

    top_actions = [
        row["action_title"]
        for row in sorted(product_rows, key=lambda item: (item.get("action_priority") is None, item.get("action_priority") or 9999))[:3]
        if row.get("action_title")
    ]
    dangerous_now = (
        "Сейчас прибыль сильнее всего давят: " + "; ".join(top_actions)
        if top_actions
        else "Сейчас магазин требует повторной проверки главных товарных проблем."
    )

    return {
        "whatIsHappening": store_summary.get("store_health_message") or "Магазин нуждается в коротком цикле повторной проверки.",
        "mainLosses": (
            f"За последние 30 дней выручка составила {fmt_money(as_float(store_summary.get('revenue_total')))} ₽, "
            f"а прибыль после рекламы {fmt_money(as_float(store_summary.get('profit_after_ads_total')))} ₽."
        ),
        "whatIsDangerousNow": dangerous_now,
        "scenarioGroupsNow": scenario_mix,
        "currentOrdersDragPerDay": current_orders_drag,
        "currentRevenueDragPerDay": current_revenue_drag,
        "currentProfitDragPerDay": current_profit_drag,
        "residualProfitDragAfterCalendarPerDay": current_profit_drag,
        "paidOrdersDeltaPerDaySum": 0.0,
        "nonPaidOrdersDeltaPerDaySum": current_orders_drag,
        "logisticsShareOfRevenuePool": safe_div(as_float(store_summary.get("logistics_total")), as_float(store_summary.get("revenue_total"))),
        "storageShareOfRevenuePool": safe_div(sum(as_float(row.get("storage_amount")) or 0.0 for row in product_rows), as_float(store_summary.get("revenue_total"))),
        "commissionShareOfRevenuePool": safe_div(sum(as_float(row.get("commission_amount")) or 0.0 for row in product_rows), as_float(store_summary.get("revenue_total"))),
        "acquiringShareOfRevenuePool": safe_div(sum(as_float(row.get("payment_processing_amount")) or 0.0 for row in product_rows), as_float(store_summary.get("revenue_total"))),
        "scenarioMixNow": scenario_mix,
        "dailyCharts": store_income_rows,
        "incomeRows": store_income_rows,
        "expenseRows": store_expense_rows,
        "expenseBreakdown": serialize_expense_breakdown_rows(store_expense_breakdown, hide_zero=True),
        "regions": region_rows,
        "warehouses": warehouse_rows,
        "windowSummary": window_summary,
        "overview": {
            "revenueTotal": as_float(store_summary.get("revenue_total")),
            "operatingExpensesTotal": operating_expenses_total,
            "adSpendTotal": as_float(store_summary.get("ads_spend_total")),
            "profitAfterAdsTotal": as_float(store_summary.get("profit_after_ads_total")),
            "marginAfterAdsTotal": as_float(store_summary.get("margin_after_ads_total")),
            "ordersTotal": as_float(store_summary.get("units_sold_total")),
            "averageCheck": safe_div(as_float(store_summary.get("revenue_total")), as_float(store_summary.get("units_sold_total"))),
            "averageProfitPerOrder": safe_div(as_float(store_summary.get("profit_after_ads_total")), as_float(store_summary.get("units_sold_total"))),
        },
    }


def build_competitors(
    rows: list[dict[str, Any]],
    own_price: float | None,
    own_rating: float | None,
    own_reviews: float | None,
) -> list[dict[str, Any]]:
    competitors: list[dict[str, Any]] = []
    for row in rows:
        nm_id = as_text(row.get("competitor_nm_id"))
        if not nm_id:
            continue
        comp_price = as_float(row.get("competitor_price")) or 0.0
        comp_rating = as_float(row.get("competitor_rating")) or 0.0
        comp_reviews = as_float(row.get("competitor_reviews")) or 0.0
        why_dangerous_parts: list[str] = []
        if own_price is not None and comp_price is not None and comp_price < own_price:
            why_dangerous_parts.append("ниже цена")
        if own_rating is not None and comp_rating is not None and comp_rating > own_rating:
            why_dangerous_parts.append("выше рейтинг")
        if own_reviews is not None and comp_reviews is not None and comp_reviews > own_reviews:
            why_dangerous_parts.append("больше отзывов")
        if not why_dangerous_parts:
            why_dangerous_parts.append("давит в выдаче")
        search_query = as_text(row.get("search_query"))
        competitors.append(
            {
                "nmId": nm_id,
                "name": as_text(row.get("competitor_name")) or f"WB {nm_id}",
                "price": comp_price,
                "rating": comp_rating,
                "reviews": comp_reviews,
                "photoCount": 0,
                "hasVideo": False,
                "descriptionFill": "unknown",
                "characteristicsCount": 0,
                "aboveUs": True,
                "queries": [search_query] if search_query else [],
                "whyComparable": "Появляется в поиске WB по запросу товара.",
                "whyDangerous": ", ".join(why_dangerous_parts),
            }
        )
    return competitors


def build_product_pack(
    product_rows: list[dict[str, Any]],
    by_offer_daily: dict[str, list[dict[str, Any]]],
    product_expense_breakdown: dict[str, list[dict[str, Any]]],
    latest_day: date | None,
    region_source: list[dict[str, Any]],
    warehouse_source: list[dict[str, Any]],
    finance_boundary: dict[str, Any],
    finance_cutoff: date | None,
    product_image_manifest: dict[str, str] | None,
    competitor_map: dict[str, list[dict[str, Any]]],
    product_card_snapshot: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for row in product_rows:
        offer_id = row["offer_id"]
        product_name = row.get("product_name") or offer_id or "Товар"
        product_id = str(row.get("nm_id") or offer_id)
        if not product_id or product_id == "~":
            continue
        dedupe_key = product_id
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        image_url = None
        if product_image_manifest:
            image_url = product_image_manifest.get(product_id)
        if not image_url:
            image_url = extract_product_image_url(row.get("product_raw_payload"))
        card_snapshot = product_card_snapshot.get(product_id)
        if (
            not product_name
            or product_name in {"~", offer_id}
            or product_name.startswith("wb_nm_")
        ):
            fallback_title = extract_product_title(row.get("product_raw_payload"))
            if card_snapshot and as_text(card_snapshot.get("product_title")):
                fallback_title = as_text(card_snapshot.get("product_title"))
            if fallback_title:
                product_name = fallback_title

        daily_rows = by_offer_daily.get(offer_id, [])
        window = build_window_metrics(daily_rows, latest_day)
        product_rows_by_day = {
            row_item["day"]: {
                "revenue": row_item["revenue"],
                "profit": row_item["profit"],
                "orders": row_item["orders"],
                "ads": row_item["ads"],
                "logistics": row_item["logistics"],
                "commission": row_item["commission"],
                "acquiring": row_item["acquiring"],
                "storage": row_item["storage"],
                "withholding": row_item["withholding"],
                "expenses": row_item["expenses"],
            }
            for row_item in daily_rows
        }
        income_rows, expense_rows = series_rows(
            product_rows_by_day,
            latest_day,
            finance_cutoff=finance_cutoff,
        )
        regions = build_geo_rows(region_source, latest_day, offer_id=offer_id)
        warehouses = build_geo_rows(warehouse_source, latest_day, offer_id=offer_id)
        recommendation = build_recommendation_payload(row)
        effect_value = as_float(row.get("estimated_gain_if_fixed"))
        if effect_value is None:
            effect_value = as_float(row.get("estimated_profit_uplift_30d"))
        if effect_value is None:
            effect_value = as_float(row.get("money_impact_30d"))
        if effect_value is None:
            effect_value = as_float(row.get("revenue_at_risk_30d"))
        primary_rule_code = row.get("primary_rule_code")
        orders_delta = None
        revenue_delta = None
        profit_delta = None
        if window["currentOrdersPerDay"] is not None and window["baseOrdersPerDay"] is not None:
            orders_delta = window["currentOrdersPerDay"] - window["baseOrdersPerDay"]
        if window["currentRevenuePerDay"] is not None and window["baseRevenuePerDay"] is not None:
            revenue_delta = window["currentRevenuePerDay"] - window["baseRevenuePerDay"]
        if window["currentProfitPerDay"] is not None and window["baseProfitPerDay"] is not None:
            profit_delta = window["currentProfitPerDay"] - window["baseProfitPerDay"]
        if effect_value is None and profit_delta is not None:
            effect_value = profit_delta * 30
        if effect_value is None and revenue_delta is not None:
            effect_value = revenue_delta * 30
        if effect_value is None or effect_value == 0:
            total_profit = as_float(row.get("profit_after_ads"))
            if total_profit is not None:
                effect_value = abs(total_profit)
        if effect_value is None or effect_value == 0:
            total_revenue = as_float(row.get("revenue_gross"))
            if total_revenue is not None:
                effect_value = abs(total_revenue)
        if effect_value is None:
            effect_value = 0.0

        expense_breakdown = serialize_expense_breakdown_rows(
            product_expense_breakdown.get(offer_id, []),
            hide_zero=True,
        )

        where_parts = []
        if regions:
            where_parts.append(
                "По регионам сильнее всего выделяются "
                + ", ".join(f"{item['name']} ({fmt_money(item['revenueDeltaPerDay'])} ₽/день)" for item in regions[:3])
            )
        if warehouses:
            where_parts.append(
                "По складам сильнее всего выделяются "
                + ", ".join(f"{item['name']} ({fmt_money(item['revenueDeltaPerDay'])} ₽/день)" for item in warehouses[:3])
            )
        if not where_parts and orders_delta is not None:
            where_parts.append(f"Основное отклонение сейчас видно в общем изменении заказов: {fmt_count(orders_delta)} в день.")

        proven_facts = (
            f"Сейчас товар дает {fmt_count(window['currentOrdersPerDay'])} заказа в день против {fmt_count(window['baseOrdersPerDay'])} раньше, "
            f"выручку {fmt_money(window['currentRevenuePerDay'])} ₽/день против {fmt_money(window['baseRevenuePerDay'])} ₽/день "
            f"и прибыль после рекламы {fmt_money(window['currentProfitPerDay'])} ₽/день против {fmt_money(window['baseProfitPerDay'])} ₽/день."
        )
        still_hypothesis = (
            "Конкурентный слой собран по публичной выдаче WB и может требовать уточнений по коммерческим условиям."
        )
        competitors = build_competitors(
            competitor_map.get(product_id, []),
            as_float(row.get("current_price")),
            None,
            None,
        )
        if not competitors:
            still_hypothesis = (
                "Конкурентный pressure-слой пока не подключен к каноническому пакету, поэтому сравнение с конкурентами остается пустым до отдельного текущего источника."
            )
        unique_queries = {
            as_text(item.get("search_query"))
            for item in competitor_map.get(product_id, [])
            if as_text(item.get("search_query"))
        }

        products.append(
            {
                "productId": product_id,
                "productName": product_name,
                "imageUrl": image_url,
                "imageFallback": product_name[:1].upper(),
                "confidence": row.get("confidence") or "medium",
                "price": as_float(row.get("current_price")) or 0.0,
                "rating": (
                    as_float(card_snapshot.get("rating"))
                    if card_snapshot and card_snapshot.get("rating") is not None
                    else extract_product_rating(row.get("product_raw_payload"))
                ) or 0.0,
                "feedbacks": (
                    as_float(card_snapshot.get("feedbacks"))
                    if card_snapshot and card_snapshot.get("feedbacks") is not None
                    else extract_product_feedbacks(row.get("product_raw_payload"))
                ) or 0.0,
                "confirmedCompetitorCount": len(competitors),
                "higherSearchCompetitorCount": len(competitors),
                "strongerCardCompetitorCount": 0,
                "repeatedPressureQueries": len(unique_queries),
                "primaryProblemZone": choose_primary_problem(primary_rule_code, orders_delta, as_float(row.get("days_of_stock_30d"))),
                "secondaryProblemZone": choose_secondary_problem(primary_rule_code, row.get("out_of_stock_risk")),
                "whatHappened": (
                    f"Заказы изменились на {fmt_count(orders_delta)} в день, выручка на {fmt_money(revenue_delta)} ₽ в день, "
                    f"прибыль после рекламы на {fmt_money(profit_delta)} ₽ в день."
                ),
                "whereProblem": " ".join(where_parts) if where_parts else "Главная проблема пока видна в экономике товара, а не в отдельной географии.",
                "whyMainProblem": why_main_problem(primary_rule_code),
                "provenFacts": proven_facts,
                "stillHypothesis": still_hypothesis,
                "whatToDoNow": recommendation["what_to_do"] or "Перепроверить карточку и динамику спроса по товару.",
                "whyThisAction": recommendation["problem_summary"] or "Действие выбрано как базовый шаг для стабилизации спроса и маржи.",
                "moneyInPlay30d": money_range(effect_value),
                "moneyInPlayText": recommendation["expected_effect_note"] or "Оценка по 30-дневной дельте прибыли/выручки.",
                "mainRisk": recommendation["if_not_improved"] or "Снижение маржи и спроса без компенсирующих действий.",
                "residualProfitAfterCalendarPerDay": profit_delta,
                "ordersDeltaPerDay": orders_delta,
                "revenueDeltaPerDay": revenue_delta,
                "profitAfterAdsDeltaPerDay": profit_delta,
                "paidOrdersDeltaPerDay": 0.0,
                "nonPaidOrdersDeltaPerDay": orders_delta,
                "daysOfStockOld": as_float(row.get("days_of_stock_30d")),
                "daysOfStockNew": as_float(row.get("days_of_stock_30d")),
                "stockExtensionDays": 0.0,
                "currentStockUnits": as_float(row.get("stock_available")),
                "logisticsShare": safe_div(as_float(row.get("logistics_amount")), as_float(row.get("revenue_gross"))),
                "storageShare": safe_div(as_float(row.get("storage_amount")), as_float(row.get("revenue_gross"))),
                "commissionShare": safe_div(as_float(row.get("commission_amount")), as_float(row.get("revenue_gross"))),
                "acquiringShare": safe_div(as_float(row.get("payment_processing_amount")), as_float(row.get("revenue_gross"))),
                "withholdingShare": safe_div(
                    (as_float(row.get("penalties_amount")) or 0.0) + (as_float(row.get("other_amount")) or 0.0) + (as_float(row.get("services_amount")) or 0.0),
                    as_float(row.get("revenue_gross")),
                ),
                "publicTitle": product_name,
                "publicBrand": as_text(card_snapshot.get("brand")) if card_snapshot and card_snapshot.get("brand") else row.get("brand") or "—",
                "publicSubject": row.get("category_name") or "—",
                "publicSupplier": "—",
                "machineSummary": recommendation["problem_summary"] or "Сводка будет уточнена после следующего прогрева аналитики.",
                "finalSummary": {
                    "Что произошло": (
                        f"Выручка {fmt_money(window['currentRevenuePerDay'])} ₽/день против {fmt_money(window['baseRevenuePerDay'])} ₽/день, "
                        f"прибыль после рекламы {fmt_money(window['currentProfitPerDay'])} ₽/день против {fmt_money(window['baseProfitPerDay'])} ₽/день."
                    ),
                    "Где именно проблема": " ".join(where_parts) if where_parts else "Проблема пока видна в общей экономике товара.",
                    "Почему это считается главной проблемой": why_main_problem(primary_rule_code),
                    "Что делать сейчас": recommendation["what_to_do"],
                    "Главный риск": recommendation["if_not_improved"],
                },
                "competitors": competitors,
                "queryPressure": [],
                "geoHotspots": regions,
                "warehouseHotspots": warehouses,
                "financeLabel": (
                    "Полностью подтвержденные данные"
                    if finance_boundary.get("confirmedThrough")
                    else "Предварительные данные"
                ),
                "overview": {
                    "revenueTotal": as_float(row.get("revenue_gross")),
                    "orderCountTotal": as_float(row.get("units_sold")),
                    "averageCheckTotal": safe_div(as_float(row.get("revenue_gross")), as_float(row.get("units_sold"))),
                    "profitAfterAdsTotal": as_float(row.get("profit_after_ads")),
                    "marginAfterAdsTotal": as_float(row.get("margin_after_ads")),
                    "adSpendTotal": as_float(row.get("ads_spend")),
                    "logisticsTotal": as_float(row.get("logistics_amount")),
                    "wbExpensesTotal": (
                        (as_float(row.get("commission_amount")) or 0.0)
                        + (as_float(row.get("services_amount")) or 0.0)
                        + (as_float(row.get("storage_amount")) or 0.0)
                        + (as_float(row.get("payment_processing_amount")) or 0.0)
                        + (as_float(row.get("penalties_amount")) or 0.0)
                        + (as_float(row.get("other_amount")) or 0.0)
                    ),
                    "averageProfitPerOrder": safe_div(as_float(row.get("profit_after_ads")), as_float(row.get("units_sold"))),
                },
                "expenseBreakdown": expense_breakdown,
                "windowMetrics": window,
                "incomeRows": income_rows,
                "expenseRows": expense_rows,
            }
        )
    products.sort(key=lambda item: item.get("residualProfitAfterCalendarPerDay") or 0.0)
    return products


def build_report(pack: dict[str, Any]) -> str:
    return f"""# PROFIT CONTROL — Cabinet Humanization + Build v1

Generated at: {pack["generatedAt"]}

## Canonical Inputs

- Source of truth for cabinet pack: current canonical DB schema `{SCHEMA}`.
- Required layers: `mart_store_summary_30d`, `mart_pnl_30d_by_product`, `mart_pnl_daily_by_product`, `mart_inventory_snapshot`, `mart_action_queue_14d`, optional `finance_day_state`.
- Historical export packs are not required for the active cabinet contour anymore.

## What This Build Produces

- Human-readable store summary from canonical marts.
- Human-readable product cards and action texts from canonical action queue + recommendation payload.
- Store and product dynamics for the active cabinet pages directly inside the cabinet pack.
- Finance boundary note and fresh tail days from current `finance_day_state` when available.
- Finance-dependent metrics are clamped to `finance_day_state.confirmedThrough` so the tail does not look like exact values.
- Sales/revenue metrics remain live when the sales layer is fresh.

## Active Pages Served By This Pack

- `/cabinet/economics`
- `/cabinet/products`
- `/cabinet/products/[sku]`
- `/cabinet/products/[sku]/compare`
"""


def main() -> None:
    ensure_schema(SCHEMA)
    store_summary = latest_store_summary()
    period_start = date_from_value(store_summary["period_start"])
    period_end = date_from_value(store_summary["period_end"])
    window_start = period_end - timedelta(days=SERIES_TOTAL_DAYS - 1)

    product_image_manifest = None
    manifest_payload = load_json(PRODUCT_IMAGE_MANIFEST)
    if isinstance(manifest_payload, dict) and isinstance(manifest_payload.get("images"), dict):
        product_image_manifest = {
            str(key): str(value) for key, value in manifest_payload["images"].items() if value
        }

    finance_boundary = fetch_finance_boundary()
    finance_cutoff = finance_clamp_cutoff(finance_boundary)
    daily_rows = fetch_daily_rows(window_start)
    period_metrics = aggregate_product_period_metrics(daily_rows, period_start, period_end)
    with get_conn() as conn:
        wb_expense_matrix = build_wb_expense_granularity_matrix(
            conn,
            SCHEMA,
            period_start.isoformat(),
            period_end.isoformat(),
        )
        wb_product_expense_breakdown = build_wb_product_expense_breakdown(
            conn,
            SCHEMA,
            period_start.isoformat(),
            period_end.isoformat(),
        )
    product_rows = merge_product_period_metrics(
        fetch_product_rows(period_start, period_end),
        period_metrics,
    )
    store_summary = merge_store_summary_from_daily(
        store_summary,
        daily_rows,
        period_start,
        period_end,
    )
    latest_day, by_offer_daily, store_by_day = build_series_index(daily_rows)
    store_income_rows, store_expense_rows = series_rows(
        store_by_day,
        latest_day,
        finance_cutoff=finance_cutoff,
    )
    region_source = fetch_region_rows(window_start)
    warehouse_source = fetch_warehouse_rows(window_start)
    competitor_rows = fetch_competitor_snapshot()
    competitor_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in competitor_rows:
        competitor_map[as_text(row.get("own_nm_id"))].append(row)
    product_card_snapshot = fetch_product_card_snapshot()

    store_regions = build_geo_rows(region_source, latest_day)
    store_warehouses = build_geo_rows(warehouse_source, latest_day)
    products = build_product_pack(
        product_rows,
        by_offer_daily,
        wb_product_expense_breakdown,
        latest_day,
        region_source,
        warehouse_source,
        finance_boundary,
        finance_cutoff,
        product_image_manifest,
        competitor_map,
        product_card_snapshot,
    )
    store = build_store_pack(
        store_summary,
        product_rows,
        store_income_rows,
        store_expense_rows,
        wb_expense_matrix,
        store_regions,
        store_warehouses,
        latest_day,
    )

    pack = {
        "generatedAt": now_iso(),
        "financeLayers": FINANCE_LAYER_LABELS,
        "financeBoundary": finance_boundary,
        "store": store,
        "products": products,
        "cabinetStructure": {
            "economicsPage": [
                "Короткий вывод по магазину",
                "Карточки с ключевыми потерями",
                "Графики доходов и расходов",
                "География по текущему каноническому слою",
            ],
            "productsPage": [
                "Таблица товаров с текущими проблемами",
                "Экономика товара из канонического пакета",
                "Действие по каждому товару без historical exports",
            ],
            "productPage": [
                "Обзор экономики товара",
                "Динамика товара за последние 35 дней",
                "Сравнение и гипотезы только из текущего pack",
            ],
        },
    }

    PACK_DIR.mkdir(parents=True, exist_ok=True)
    CABINET_DATA.mkdir(parents=True, exist_ok=True)

    write_json(CABINET_DATA / "profit_control_cabinet_human_pack_v1.json", pack)
    write_csv(
        PACK_DIR / "cabinet_final_build_input.csv",
        [
            {
                "module": "economics_page",
                "view": "store_kpis_and_dynamics",
                "status": "ready",
                "source": "canonical_db_and_profit_control_cabinet_human_pack_v1.json",
                "notes": "Reads only canonical pack fields.",
            },
            {
                "module": "products_page",
                "view": "products_table_and_economics",
                "status": "ready",
                "source": "canonical_db_and_profit_control_cabinet_human_pack_v1.json",
                "notes": "Historical exports are no longer required.",
            },
            {
                "module": "product_card",
                "view": "detail_and_compare",
                "status": "ready",
                "source": "canonical_db_and_profit_control_cabinet_human_pack_v1.json",
                "notes": "Detail page reads overview/window/series directly from pack.",
            },
        ],
    )
    write_text(DOCS / "profit_control_cabinet_humanization_and_build_v1.md", build_report(pack))
    print(f"Built canonical cabinet pack from schema={SCHEMA} into {CABINET_DATA / 'profit_control_cabinet_human_pack_v1.json'}")


if __name__ == "__main__":
    main()
