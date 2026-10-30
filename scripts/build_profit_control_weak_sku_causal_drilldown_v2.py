from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.db import get_conn

import scripts.build_profit_control_calendar_seasonality_layer_v1 as cal
import scripts.build_profit_control_deep_analysis_pack_v1 as deep

MOSCOW = ZoneInfo("Europe/Moscow")
MONEY = Decimal("0.01")
FOUR = Decimal("0.0001")
SCHEMA = "client_wb_probe_2"
MARKETPLACE = "wb"
STORE_ENTITY_ID = "__STORE__"
STORE_ENTITY_NAME = "STORE_TOTAL"

MANDATORY_WEAK_NAMES = [
    'Набор для творчества для девочек "Феи Единороги Балерины"',
    "Деревянная парковка для машинок со скотч дорогой и азс",
    "Деревянный автомобильный гараж для машин с разметкой дороги",
]
ADDITIONAL_WEAK_LIMIT = 3
STABLE_COMPARATOR_NAME = "Большой деревянный гараж для машинок с лифтом и трамплином"

OUTPUT_DIR = ROOT / "exports" / "profit_control_weak_sku_causal_drilldown_v2"
DOC_PATH = ROOT / "artifacts" / "profit_control_weak_sku_causal_drilldown_v2.md"
INTERNAL_NOTE_PATH = OUTPUT_DIR / "internal_causal_note.md"

GEO_STORE_CSV = OUTPUT_DIR / "geo_store_summary.csv"
GEO_PRODUCT_CSV = OUTPUT_DIR / "geo_product_summary.csv"
GEO_DELTA_MAP_CSV = OUTPUT_DIR / "geo_delta_map.csv"
WAREHOUSE_PRODUCT_CSV = OUTPUT_DIR / "warehouse_product_summary.csv"
PAID_NONPAID_CSV = OUTPUT_DIR / "paid_vs_nonpaid_summary.csv"
CAMPAIGN_DETAIL_CSV = OUTPUT_DIR / "campaign_detail_summary.csv"
AVAILABILITY_CSV = OUTPUT_DIR / "availability_check.csv"
SKU_VARIANT_CSV = OUTPUT_DIR / "sku_variant_check.csv"
PRODUCT_CONTEXT_CSV = OUTPUT_DIR / "product_vs_store_context.csv"
EXTERNAL_SIGNALS_CSV = OUTPUT_DIR / "next_external_signals_needed.csv"
REGION_MAP_ABSOLUTE_CSV = OUTPUT_DIR / "region_map_absolute_metrics.csv"
REGION_MAP_DELTA_CSV = OUTPUT_DIR / "region_map_delta_metrics.csv"
REGION_MAP_CONTRIB_CSV = OUTPUT_DIR / "region_map_contribution_metrics.csv"
SUMMARY_CSV = OUTPUT_DIR / "sku_causal_summary.csv"

STRENGTHS_CSV = ROOT / "exports" / "profit_control_strengths_weaknesses_layer_v1" / "product_strengths_weaknesses.csv"
CALENDAR_PRODUCT_CSV = ROOT / "exports" / "profit_control_calendar_seasonality_layer_v1" / "product_calendar_adjusted_summary.csv"
PRODUCT_CONTEXT_SOURCE_CSV = ROOT / "exports" / "profit_control_dynamic_analysis_layer_v1" / "product_vs_store_context.csv"
PRODUCT_WINDOW_CSV = ROOT / "exports" / "profit_control_deep_analysis_pack_v1" / "product_window_comparison.csv"
STORE_WINDOW_CSV = ROOT / "exports" / "profit_control_deep_analysis_pack_v1" / "store_window_comparison.csv"

RELIABILITY_MIN_TOTAL_ORDERS = Decimal("5")
RELIABILITY_MIN_TOTAL_REVENUE = Decimal("3000.00")


@dataclass(frozen=True)
class WindowDef:
    label: str
    current_start: date
    current_end: date
    base_start: date
    base_end: date
    current_days: int
    base_days: int


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def q2(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def q4(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0.0000")
    if isinstance(value, Decimal):
        return value.quantize(FOUR, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(FOUR, rounding=ROUND_HALF_UP)


def safe_div(numerator: Any, denominator: Any, places: Decimal = FOUR) -> Decimal:
    den = Decimal(str(denominator or 0))
    if den == 0:
        return Decimal("0").quantize(places, rounding=ROUND_HALF_UP)
    num = Decimal(str(numerator or 0))
    return (num / den).quantize(places, rounding=ROUND_HALF_UP)


def money(value: Any) -> str:
    return f"{q2(value):,.2f}".replace(",", " ")


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def as_text(value: Any) -> str:
    return (str(value or "")).strip()


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(fmt(row.get(column)).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


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
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def day_as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def build_windows(corridor_start: date, corridor_end: date) -> list[WindowDef]:
    return [
        WindowDef(
            label="short_7d_vs_prev_28d",
            current_start=corridor_end - timedelta(days=6),
            current_end=corridor_end,
            base_start=corridor_end - timedelta(days=34),
            base_end=corridor_end - timedelta(days=7),
            current_days=7,
            base_days=28,
        ),
        WindowDef(
            label="medium_14d_vs_prev_42d",
            current_start=corridor_end - timedelta(days=13),
            current_end=corridor_end,
            base_start=corridor_start,
            base_end=corridor_end - timedelta(days=14),
            current_days=14,
            base_days=42,
        ),
    ]


def window_days(window: WindowDef, part: str) -> list[date]:
    start = window.current_start if part == "current" else window.base_start
    end = window.current_end if part == "current" else window.base_end
    return deep.daterange(start, end)


def in_window(day_value: date, window: WindowDef, part: str) -> bool:
    return day_value in set(window_days(window, part))


def calendar_match_pool(
    *,
    base_days: list[date],
    current_day: date,
    calendar_meta: dict[date, dict[str, Any]],
) -> tuple[list[date], str]:
    current_meta = calendar_meta[current_day]
    exact = [day for day in base_days if calendar_meta[day]["calendar_segment"] == current_meta["calendar_segment"]]
    if exact:
        return exact, "exact_segment"
    same_day_type = [day for day in base_days if calendar_meta[day]["calendar_day_type"] == current_meta["calendar_day_type"]]
    if same_day_type:
        return same_day_type, "calendar_day_type"
    same_phase = [day for day in base_days if calendar_meta[day]["event_phase"] == current_meta["event_phase"]]
    if same_phase:
        return same_phase, "event_phase"
    return list(base_days), "all_base_days"


def revenue_signed(row: dict[str, Any]) -> Decimal:
    return -q2(row["revenue_gross"]) if row.get("is_return") else q2(row["revenue_gross"])


def quantity_signed(row: dict[str, Any]) -> Decimal:
    return -q2(row["quantity"]) if row.get("is_return") else q2(row["quantity"])


def aggregate_sales_metrics(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    revenue = sum(revenue_signed(row) for row in rows)
    units = sum(quantity_signed(row) for row in rows)
    sold_order_ids = {as_text(row["order_id"]) for row in rows if as_text(row.get("order_id"))}
    return {
        "revenue_net": q2(revenue),
        "sold_units_net": q2(units),
        "sold_order_count": Decimal(len(sold_order_ids)),
    }


def aggregate_order_metrics(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    order_ids = {as_text(row["order_id"]) for row in rows if as_text(row.get("order_id"))}
    ordered_units = sum(q2(row["quantity"]) for row in rows)
    ordered_gmv = sum(q2(row["sale_price"]) for row in rows)
    price_with_disc_values = [q2(row["price_with_disc"]) for row in rows if row.get("price_with_disc") is not None]
    finished_price_values = [q2(row["finished_price"]) for row in rows if row.get("finished_price") is not None]
    discount_values = [q2(row["discount_percent"]) for row in rows if row.get("discount_percent") is not None]
    spp_values = [q2(row["spp"]) for row in rows if row.get("spp") is not None]
    return {
        "order_count": Decimal(len(order_ids)),
        "ordered_units": q2(ordered_units),
        "ordered_gmv": q2(ordered_gmv),
        "price_with_disc_avg": q2(sum(price_with_disc_values) / Decimal(len(price_with_disc_values))) if price_with_disc_values else Decimal("0.00"),
        "finished_price_avg": q2(sum(finished_price_values) / Decimal(len(finished_price_values))) if finished_price_values else Decimal("0.00"),
        "discount_percent_avg": q2(sum(discount_values) / Decimal(len(discount_values))) if discount_values else Decimal("0.00"),
        "spp_avg": q2(sum(spp_values) / Decimal(len(spp_values))) if spp_values else Decimal("0.00"),
    }


def aggregate_ads_metrics(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    impressions = sum(q2(row["impressions"]) for row in rows)
    clicks = sum(q2(row["clicks"]) for row in rows)
    ad_orders = sum(q2(row["orders_count"]) for row in rows)
    ad_revenue = sum(q2(row["revenue"]) for row in rows)
    spend = sum(q2(row["spend"]) for row in rows)
    return {
        "impressions": q2(impressions),
        "clicks": q2(clicks),
        "ad_orders": q2(ad_orders),
        "ad_revenue": q2(ad_revenue),
        "ad_spend": q2(spend),
        "ctr": q4(safe_div(clicks, impressions)),
        "cpc": q2(safe_div(spend, clicks, MONEY)),
        "cr": q4(safe_div(ad_orders, clicks)),
        "cost_per_order": q2(safe_div(spend, ad_orders, MONEY)),
    }


def combine_metrics(
    sales_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    ads_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Decimal]:
    ads_rows = ads_rows or []
    sales_agg = aggregate_sales_metrics(sales_rows)
    order_agg = aggregate_order_metrics(order_rows)
    ads_agg = aggregate_ads_metrics(ads_rows)
    out: dict[str, Decimal] = {
        **sales_agg,
        **order_agg,
        **ads_agg,
    }
    out["average_check"] = q2(safe_div(sales_agg["revenue_net"], order_agg["order_count"], MONEY))
    out["non_paid_order_proxy"] = q2(order_agg["order_count"] - ads_agg["ad_orders"])
    out["non_paid_revenue_proxy"] = q2(sales_agg["revenue_net"] - ads_agg["ad_revenue"])
    return out


def zero_metric_template() -> dict[str, Decimal]:
    return {
        "revenue_net": Decimal("0.00"),
        "sold_units_net": Decimal("0.00"),
        "sold_order_count": Decimal("0"),
        "order_count": Decimal("0"),
        "ordered_units": Decimal("0.00"),
        "ordered_gmv": Decimal("0.00"),
        "price_with_disc_avg": Decimal("0.00"),
        "finished_price_avg": Decimal("0.00"),
        "discount_percent_avg": Decimal("0.00"),
        "spp_avg": Decimal("0.00"),
        "impressions": Decimal("0.00"),
        "clicks": Decimal("0.00"),
        "ad_orders": Decimal("0.00"),
        "ad_revenue": Decimal("0.00"),
        "ad_spend": Decimal("0.00"),
        "ctr": Decimal("0.0000"),
        "cpc": Decimal("0.00"),
        "cr": Decimal("0.0000"),
        "cost_per_order": Decimal("0.00"),
        "average_check": Decimal("0.00"),
        "non_paid_order_proxy": Decimal("0.00"),
        "non_paid_revenue_proxy": Decimal("0.00"),
    }


def per_day(value: Decimal, days: int) -> Decimal:
    return q2(safe_div(value, Decimal(days), MONEY))


def build_daily_metric_map(
    *,
    sales_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    ads_rows: list[dict[str, Any]] | None = None,
    days: Iterable[date],
) -> dict[date, dict[str, Decimal]]:
    ads_rows = ads_rows or []
    days_list = list(days)
    out: dict[date, dict[str, Decimal]] = {}
    for day_value in days_list:
        sales_subset = [row for row in sales_rows if day_as_date(row["day"]) == day_value]
        order_subset = [row for row in order_rows if day_as_date(row["day"]) == day_value]
        ads_subset = [row for row in ads_rows if day_as_date(row["day"]) == day_value]
        metrics = zero_metric_template()
        metrics.update(combine_metrics(sales_subset, order_subset, ads_subset))
        out[day_value] = metrics
    return out


def aggregate_metric_maps(days: list[date], metrics_by_day: dict[date, dict[str, Decimal]]) -> dict[str, Decimal]:
    additive_keys = {
        "revenue_net",
        "sold_units_net",
        "sold_order_count",
        "order_count",
        "ordered_units",
        "ordered_gmv",
        "impressions",
        "clicks",
        "ad_orders",
        "ad_revenue",
        "ad_spend",
        "non_paid_order_proxy",
        "non_paid_revenue_proxy",
    }
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for day_value in days:
        day_metrics = metrics_by_day.get(day_value, zero_metric_template())
        for key in additive_keys:
            totals[key] = q2(totals[key] + q2(day_metrics.get(key)))
    totals["average_check"] = q2(safe_div(totals["revenue_net"], totals["order_count"], MONEY))
    totals["ctr"] = q4(safe_div(totals["clicks"], totals["impressions"]))
    totals["cpc"] = q2(safe_div(totals["ad_spend"], totals["clicks"], MONEY))
    totals["cr"] = q4(safe_div(totals["ad_orders"], totals["clicks"]))
    totals["cost_per_order"] = q2(safe_div(totals["ad_spend"], totals["ad_orders"], MONEY))
    totals["price_with_disc_avg"] = Decimal("0.00")
    totals["finished_price_avg"] = Decimal("0.00")
    totals["discount_percent_avg"] = Decimal("0.00")
    totals["spp_avg"] = Decimal("0.00")
    return totals


def expected_current_from_base(
    *,
    base_days: list[date],
    current_days_list: list[date],
    base_metrics_by_day: dict[date, dict[str, Decimal]],
    calendar_meta: dict[date, dict[str, Any]],
) -> tuple[dict[str, Decimal], Counter[str]]:
    additive_keys = {
        "revenue_net",
        "sold_units_net",
        "sold_order_count",
        "order_count",
        "ordered_units",
        "ordered_gmv",
        "impressions",
        "clicks",
        "ad_orders",
        "ad_revenue",
        "ad_spend",
        "non_paid_order_proxy",
        "non_paid_revenue_proxy",
    }
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    match_counter: Counter[str] = Counter()
    for current_day in current_days_list:
        pool, match_level = calendar_match_pool(base_days=base_days, current_day=current_day, calendar_meta=calendar_meta)
        match_counter[match_level] += 1
        pool_size = Decimal(len(pool) or 1)
        for key in additive_keys:
            metric_sum = sum(q2(base_metrics_by_day[day].get(key)) for day in pool)
            totals[key] = q2(totals[key] + q2(metric_sum / pool_size))
    totals["average_check"] = q2(safe_div(totals["revenue_net"], totals["order_count"], MONEY))
    totals["ctr"] = q4(safe_div(totals["clicks"], totals["impressions"]))
    totals["cpc"] = q2(safe_div(totals["ad_spend"], totals["clicks"], MONEY))
    totals["cr"] = q4(safe_div(totals["ad_orders"], totals["clicks"]))
    totals["cost_per_order"] = q2(safe_div(totals["ad_spend"], totals["ad_orders"], MONEY))
    totals["price_with_disc_avg"] = Decimal("0.00")
    totals["finished_price_avg"] = Decimal("0.00")
    totals["discount_percent_avg"] = Decimal("0.00")
    totals["spp_avg"] = Decimal("0.00")
    return totals, match_counter


def reliability_from_metrics(
    *,
    current_revenue_total: Decimal,
    base_revenue_total: Decimal,
    current_orders_total: Decimal,
    base_orders_total: Decimal,
    match_counter: Counter[str],
) -> tuple[str, str]:
    low_volume = (current_orders_total + base_orders_total) < RELIABILITY_MIN_TOTAL_ORDERS or (current_revenue_total + base_revenue_total) < RELIABILITY_MIN_TOTAL_REVENUE
    fallback = bool(match_counter.get("all_base_days")) or bool(match_counter.get("event_phase"))
    if low_volume and fallback:
        return "low_volume_and_calendar_fallback", "too_few orders/revenue and calendar match needed broad fallback"
    if low_volume:
        return "low_volume", "too few orders/revenue for stable geo interpretation"
    if fallback:
        return "calendar_fallback", "calendar-adjusted baseline needed event-phase/all-base fallback"
    return "ok", ""


def fetch_sales_rows(start_day: date, end_day: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      (sale_date AT TIME ZONE 'UTC')::date AS day,
      nm_id::text AS product_id,
      order_id,
      offer_id,
      barcode,
      tech_size,
      supplier_article,
      warehouse_name,
      region_name,
      COALESCE(is_return, FALSE) AS is_return,
      COALESCE(quantity, 0)::int AS quantity,
      COALESCE(revenue_gross, 0)::numeric(18,2) AS revenue_gross
    FROM {SCHEMA}.stg_wb_sale
    WHERE marketplace = %s
      AND (sale_date AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, start_day, end_day))
            return [dict(row) for row in cur.fetchall()]


def fetch_order_rows(start_day: date, end_day: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      (order_date AT TIME ZONE 'UTC')::date AS day,
      nm_id::text AS product_id,
      order_id,
      offer_id,
      barcode,
      tech_size,
      supplier_article,
      warehouse_name,
      warehouse_type,
      region_name,
      COALESCE(quantity, 0)::int AS quantity,
      COALESCE(sale_price, 0)::numeric(18,2) AS sale_price,
      NULLIF(raw_payload->>'priceWithDisc', '')::numeric(18,2) AS price_with_disc,
      NULLIF(raw_payload->>'finishedPrice', '')::numeric(18,2) AS finished_price,
      NULLIF(raw_payload->>'discountPercent', '')::numeric(18,2) AS discount_percent,
      NULLIF(raw_payload->>'spp', '')::numeric(18,2) AS spp
    FROM {SCHEMA}.stg_wb_order
    WHERE marketplace = %s
      AND (order_date AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, start_day, end_day))
            return [dict(row) for row in cur.fetchall()]


def fetch_ads_rows(start_day: date, end_day: date) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      date AS day,
      nm_id::text AS product_id,
      campaign_id,
      campaign_name,
      campaign_type,
      app_type,
      offer_id,
      COALESCE(impressions, 0)::numeric(18,2) AS impressions,
      COALESCE(clicks, 0)::numeric(18,2) AS clicks,
      COALESCE(orders_count, 0)::numeric(18,2) AS orders_count,
      COALESCE(revenue, 0)::numeric(18,2) AS revenue,
      COALESCE(spend, 0)::numeric(18,2) AS spend,
      COALESCE(ctr, 0)::numeric(18,4) AS row_ctr,
      COALESCE(cpc, 0)::numeric(18,2) AS row_cpc,
      COALESCE(cr, 0)::numeric(18,4) AS row_cr,
      COALESCE(atbs, 0)::numeric(18,2) AS atbs
    FROM {SCHEMA}.stg_wb_ads
    WHERE marketplace = %s
      AND date BETWEEN %s AND %s
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, start_day, end_day))
            return [dict(row) for row in cur.fetchall()]


def fetch_inventory_rows(product_ids: list[str]) -> list[dict[str, Any]]:
    query = f"""
    SELECT
      snapshot_date,
      product_id::text AS product_id,
      warehouse_name,
      warehouse_type,
      COALESCE(stock_total, 0)::numeric(18,2) AS stock_total,
      COALESCE(stock_available, 0)::numeric(18,2) AS stock_available,
      COALESCE(stock_reserved, 0)::numeric(18,2) AS stock_reserved,
      COALESCE(stock_defect, 0)::numeric(18,2) AS stock_defect
    FROM {SCHEMA}.fact_inventory_snapshot
    WHERE marketplace = %s
      AND product_id = ANY(%s)
    ORDER BY product_id, warehouse_name
    """
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, (MARKETPLACE, product_ids))
            return [dict(row) for row in cur.fetchall()]


def enrich_sales_with_order_context(sales_rows: list[dict[str, Any]], order_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_order: dict[str, dict[str, str]] = {}
    for row in order_rows:
        order_id = as_text(row.get("order_id"))
        if order_id and order_id not in by_order:
            by_order[order_id] = {
                "warehouse_type": as_text(row.get("warehouse_type")),
                "warehouse_name": as_text(row.get("warehouse_name")),
                "region_name": as_text(row.get("region_name")),
            }
    out: list[dict[str, Any]] = []
    for row in sales_rows:
        order_ctx = by_order.get(as_text(row.get("order_id")), {})
        new_row = dict(row)
        new_row["warehouse_type"] = as_text(order_ctx.get("warehouse_type"))
        if not as_text(new_row.get("warehouse_name")):
            new_row["warehouse_name"] = as_text(order_ctx.get("warehouse_name"))
        if not as_text(new_row.get("region_name")):
            new_row["region_name"] = as_text(order_ctx.get("region_name"))
        out.append(new_row)
    return out


def load_indices() -> tuple[
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, str]],
]:
    strength_rows = read_csv_rows(STRENGTHS_CSV)
    calendar_rows = read_csv_rows(CALENDAR_PRODUCT_CSV)
    context_rows = read_csv_rows(PRODUCT_CONTEXT_SOURCE_CSV)
    product_window_rows = read_csv_rows(PRODUCT_WINDOW_CSV)
    store_window_rows = read_csv_rows(STORE_WINDOW_CSV)

    strength_index = {row["product_id"]: row for row in strength_rows}
    calendar_index = {row["product_id"]: row for row in calendar_rows}
    context_index = {row["product_id"]: row for row in context_rows}
    product_window_index = {row["product_id"]: row for row in product_window_rows}
    store_window_index: dict[str, dict[str, str]] = {}
    for row in store_window_rows:
        store_window_index[f"{row['window_label']}::{row['metric']}"] = row
    return strength_rows, strength_index, calendar_index, context_index, product_window_index, store_window_index


def choose_targets(
    strength_rows: list[dict[str, Any]],
    strength_index: dict[str, dict[str, Any]],
    calendar_index: dict[str, dict[str, Any]],
    product_window_index: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    weak_rows = [
        row
        for row in strength_rows
        if row["dominant_pattern"] == "revenue_driven" and q2(row["short_residual_profit_after_calendar_per_day"]) < 0
    ]
    weak_rows.sort(key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]))

    selected_ids: list[str] = []
    for product_name in MANDATORY_WEAK_NAMES:
        candidates = [row for row in weak_rows if row["product_name"] == product_name]
        if not candidates:
            raise RuntimeError(f"Mandatory weak SKU not found: {product_name}")
        selected_ids.append(candidates[0]["product_id"])

    for row in weak_rows:
        if row["product_id"] in selected_ids:
            continue
        selected_ids.append(row["product_id"])
        if len(selected_ids) >= len(MANDATORY_WEAK_NAMES) + ADDITIONAL_WEAK_LIMIT:
            break

    stable_candidates = [
        row
        for row in strength_rows
        if row["product_name"] == STABLE_COMPARATOR_NAME
        or (
            row["relative_to_store"] == "resilient_vs_store"
            and q2(row["short_residual_profit_after_calendar_per_day"]) >= 0
            and q2(row["full_revenue_total"]) >= Decimal("100000")
        )
    ]
    stable_candidates.sort(
        key=lambda item: (
            item["product_name"] != STABLE_COMPARATOR_NAME,
            -q2(item["short_residual_profit_after_calendar_per_day"]),
            -q2(item["full_revenue_total"]),
        )
    )
    if not stable_candidates:
        raise RuntimeError("Stable comparator not found")
    stable_id = stable_candidates[0]["product_id"]

    targets: list[dict[str, Any]] = []
    for idx, product_id in enumerate([*selected_ids, stable_id], start=1):
        strength = dict(strength_index[product_id])
        strength.update(calendar_index.get(product_id, {}))
        strength.update(product_window_index.get(product_id, {}))
        strength["selection_order"] = idx
        strength["comparison_role"] = "stable_comparator" if product_id == stable_id else (
            "mandatory_weak" if product_id in selected_ids[: len(MANDATORY_WEAK_NAMES)] else "additional_weak"
        )
        targets.append(strength)
    return targets


def entity_rows(rows: list[dict[str, Any]], entity_id: str) -> list[dict[str, Any]]:
    if entity_id == STORE_ENTITY_ID:
        return rows
    return [row for row in rows if row["product_id"] == entity_id]


def build_entity_window_metric_index(
    *,
    entity_ids: list[str],
    sales_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    ads_rows: list[dict[str, Any]],
    windows: list[WindowDef],
    calendar_meta: dict[date, dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for entity_id in entity_ids:
        entity_sales = entity_rows(sales_rows, entity_id)
        entity_orders = entity_rows(order_rows, entity_id)
        entity_ads = entity_rows(ads_rows, entity_id)
        out[entity_id] = {}
        for window in windows:
            current_days_list = window_days(window, "current")
            base_days_list = window_days(window, "base")
            current_metrics = combine_metrics(
                [row for row in entity_sales if day_as_date(row["day"]) in set(current_days_list)],
                [row for row in entity_orders if day_as_date(row["day"]) in set(current_days_list)],
                [row for row in entity_ads if day_as_date(row["day"]) in set(current_days_list)],
            )
            base_metrics = combine_metrics(
                [row for row in entity_sales if day_as_date(row["day"]) in set(base_days_list)],
                [row for row in entity_orders if day_as_date(row["day"]) in set(base_days_list)],
                [row for row in entity_ads if day_as_date(row["day"]) in set(base_days_list)],
            )
            base_metrics_by_day = build_daily_metric_map(
                sales_rows=[row for row in entity_sales if day_as_date(row["day"]) in set(base_days_list)],
                order_rows=[row for row in entity_orders if day_as_date(row["day"]) in set(base_days_list)],
                ads_rows=[row for row in entity_ads if day_as_date(row["day"]) in set(base_days_list)],
                days=base_days_list,
            )
            adjusted_metrics, match_counter = expected_current_from_base(
                base_days=base_days_list,
                current_days_list=current_days_list,
                base_metrics_by_day=base_metrics_by_day,
                calendar_meta=calendar_meta,
            )
            out[entity_id][window.label] = {
                "current": current_metrics,
                "base": base_metrics,
                "calendar_adjusted_base": adjusted_metrics,
                "calendar_match_counter": match_counter,
            }
    return out


def summarize_dimension_rows(
    *,
    entity_scope: str,
    entity_id: str,
    entity_name: str,
    comparison_role: str,
    dim_name: str,
    dim_rows_sales: list[dict[str, Any]],
    dim_rows_orders: list[dict[str, Any]],
    windows: list[WindowDef],
    calendar_meta: dict[date, dict[str, Any]],
    entity_window_metrics: dict[str, dict[str, dict[str, Any]]],
    store_benchmark: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    values = sorted(
        {
            as_text(row.get(dim_name))
            for row in dim_rows_sales + dim_rows_orders
            if as_text(row.get(dim_name))
        }
    )
    for window in windows:
        current_days_list = window_days(window, "current")
        base_days_list = window_days(window, "base")
        entity_total = entity_window_metrics[entity_id][window.label]
        entity_revenue_delta_adjusted_per_day = q2(
            per_day(entity_total["current"]["revenue_net"], window.current_days)
            - per_day(entity_total["calendar_adjusted_base"]["revenue_net"], window.current_days)
        )
        entity_order_delta_adjusted_per_day = q2(
            per_day(entity_total["current"]["order_count"], window.current_days)
            - per_day(entity_total["calendar_adjusted_base"]["order_count"], window.current_days)
        )
        for value in values:
            value_sales = [row for row in dim_rows_sales if as_text(row.get(dim_name)) == value]
            value_orders = [row for row in dim_rows_orders if as_text(row.get(dim_name)) == value]
            current_sales = [row for row in value_sales if day_as_date(row["day"]) in set(current_days_list)]
            base_sales = [row for row in value_sales if day_as_date(row["day"]) in set(base_days_list)]
            current_orders = [row for row in value_orders if day_as_date(row["day"]) in set(current_days_list)]
            base_orders = [row for row in value_orders if day_as_date(row["day"]) in set(base_days_list)]

            current_metrics = combine_metrics(current_sales, current_orders)
            base_metrics = combine_metrics(base_sales, base_orders)
            base_metrics_by_day = build_daily_metric_map(
                sales_rows=base_sales,
                order_rows=base_orders,
                days=base_days_list,
            )
            adjusted_metrics, match_counter = expected_current_from_base(
                base_days=base_days_list,
                current_days_list=current_days_list,
                base_metrics_by_day=base_metrics_by_day,
                calendar_meta=calendar_meta,
            )
            reliability_flag, reliability_reason = reliability_from_metrics(
                current_revenue_total=current_metrics["revenue_net"],
                base_revenue_total=base_metrics["revenue_net"],
                current_orders_total=current_metrics["order_count"],
                base_orders_total=base_metrics["order_count"],
                match_counter=match_counter,
            )

            current_revenue_per_day = per_day(current_metrics["revenue_net"], window.current_days)
            base_revenue_per_day = per_day(base_metrics["revenue_net"], window.base_days)
            adjusted_revenue_per_day = per_day(adjusted_metrics["revenue_net"], window.current_days)
            current_orders_per_day = per_day(current_metrics["order_count"], window.current_days)
            base_orders_per_day = per_day(base_metrics["order_count"], window.base_days)
            adjusted_orders_per_day = per_day(adjusted_metrics["order_count"], window.current_days)
            current_units_per_day = per_day(current_metrics["sold_units_net"], window.current_days)
            base_units_per_day = per_day(base_metrics["sold_units_net"], window.base_days)
            adjusted_units_per_day = per_day(adjusted_metrics["sold_units_net"], window.current_days)

            revenue_delta_vs_base = q2(current_revenue_per_day - base_revenue_per_day)
            revenue_delta_vs_adjusted = q2(current_revenue_per_day - adjusted_revenue_per_day)
            orders_delta_vs_base = q2(current_orders_per_day - base_orders_per_day)
            orders_delta_vs_adjusted = q2(current_orders_per_day - adjusted_orders_per_day)

            row = {
                "entity_scope": entity_scope,
                "entity_id": entity_id,
                "entity_name": entity_name,
                "comparison_role": comparison_role,
                "window_label": window.label,
                "current_start": window.current_start.isoformat(),
                "current_end": window.current_end.isoformat(),
                "base_start": window.base_start.isoformat(),
                "base_end": window.base_end.isoformat(),
                "dimension": dim_name,
                "dimension_value": value,
                "current_revenue_total": str(q2(current_metrics["revenue_net"])),
                "base_revenue_total": str(q2(base_metrics["revenue_net"])),
                "calendar_adjusted_base_revenue_total": str(q2(adjusted_metrics["revenue_net"])),
                "current_revenue_per_day": str(current_revenue_per_day),
                "base_revenue_per_day": str(base_revenue_per_day),
                "calendar_adjusted_base_revenue_per_day": str(adjusted_revenue_per_day),
                "revenue_delta_vs_base_per_day": str(revenue_delta_vs_base),
                "revenue_delta_pct_vs_base_per_day": str(q4(safe_div(revenue_delta_vs_base, base_revenue_per_day))),
                "revenue_delta_vs_calendar_adjusted_per_day": str(revenue_delta_vs_adjusted),
                "revenue_delta_pct_vs_calendar_adjusted_per_day": str(q4(safe_div(revenue_delta_vs_adjusted, adjusted_revenue_per_day))),
                "current_orders_total": str(q2(current_metrics["order_count"])),
                "base_orders_total": str(q2(base_metrics["order_count"])),
                "calendar_adjusted_base_orders_total": str(q2(adjusted_metrics["order_count"])),
                "current_orders_per_day": str(current_orders_per_day),
                "base_orders_per_day": str(base_orders_per_day),
                "calendar_adjusted_base_orders_per_day": str(adjusted_orders_per_day),
                "orders_delta_vs_base_per_day": str(orders_delta_vs_base),
                "orders_delta_pct_vs_base_per_day": str(q4(safe_div(orders_delta_vs_base, base_orders_per_day))),
                "orders_delta_vs_calendar_adjusted_per_day": str(orders_delta_vs_adjusted),
                "orders_delta_pct_vs_calendar_adjusted_per_day": str(q4(safe_div(orders_delta_vs_adjusted, adjusted_orders_per_day))),
                "current_sold_units_total": str(q2(current_metrics["sold_units_net"])),
                "base_sold_units_total": str(q2(base_metrics["sold_units_net"])),
                "calendar_adjusted_base_sold_units_total": str(q2(adjusted_metrics["sold_units_net"])),
                "current_sold_units_per_day": str(current_units_per_day),
                "base_sold_units_per_day": str(base_units_per_day),
                "calendar_adjusted_base_sold_units_per_day": str(adjusted_units_per_day),
                "current_profit_after_ads_total": "",
                "base_profit_after_ads_total": "",
                "calendar_adjusted_base_profit_after_ads_total": "",
                "current_profit_after_ads_per_day": "",
                "base_profit_after_ads_per_day": "",
                "calendar_adjusted_base_profit_after_ads_per_day": "",
                "profit_after_ads_status": "unavailable_no_region_finance_or_geo_attribution",
                "revenue_contribution_to_entity_delta_share": str(q4(safe_div(revenue_delta_vs_adjusted, entity_revenue_delta_adjusted_per_day))),
                "orders_contribution_to_entity_delta_share": str(q4(safe_div(orders_delta_vs_adjusted, entity_order_delta_adjusted_per_day))),
                "current_revenue_share_of_entity": str(q4(safe_div(current_metrics["revenue_net"], entity_total["current"]["revenue_net"]))),
                "base_revenue_share_of_entity": str(q4(safe_div(base_metrics["revenue_net"], entity_total["base"]["revenue_net"]))),
                "calendar_match_exact_days": match_counter.get("exact_segment", 0),
                "calendar_match_day_type_days": match_counter.get("calendar_day_type", 0),
                "calendar_match_event_phase_days": match_counter.get("event_phase", 0),
                "calendar_match_fallback_days": match_counter.get("all_base_days", 0),
                "reliability_flag": reliability_flag,
                "reliability_reason": reliability_reason,
            }
            if store_benchmark and entity_scope == "product":
                store_metrics = store_benchmark[STORE_ENTITY_ID][window.label]
                store_value_sales = [row for row in entity_rows(store_benchmark["__sales_rows__"], STORE_ENTITY_ID) if as_text(row.get(dim_name)) == value]
                store_value_orders = [row for row in entity_rows(store_benchmark["__order_rows__"], STORE_ENTITY_ID) if as_text(row.get(dim_name)) == value]
                store_current_sales = [row for row in store_value_sales if day_as_date(row["day"]) in set(current_days_list)]
                store_base_sales = [row for row in store_value_sales if day_as_date(row["day"]) in set(base_days_list)]
                store_current_orders = [row for row in store_value_orders if day_as_date(row["day"]) in set(current_days_list)]
                store_base_orders = [row for row in store_value_orders if day_as_date(row["day"]) in set(base_days_list)]
                store_base_day_map = build_daily_metric_map(
                    sales_rows=store_base_sales,
                    order_rows=store_base_orders,
                    days=base_days_list,
                )
                store_adjusted, _ = expected_current_from_base(
                    base_days=base_days_list,
                    current_days_list=current_days_list,
                    base_metrics_by_day=store_base_day_map,
                    calendar_meta=calendar_meta,
                )
                store_current = combine_metrics(store_current_sales, store_current_orders)
                store_base = combine_metrics(store_base_sales, store_base_orders)
                row.update(
                    {
                        "store_current_revenue_per_day": str(per_day(store_current["revenue_net"], window.current_days)),
                        "store_base_revenue_per_day": str(per_day(store_base["revenue_net"], window.base_days)),
                        "store_calendar_adjusted_base_revenue_per_day": str(per_day(store_adjusted["revenue_net"], window.current_days)),
                        "store_revenue_delta_vs_calendar_adjusted_per_day": str(
                            q2(per_day(store_current["revenue_net"], window.current_days) - per_day(store_adjusted["revenue_net"], window.current_days))
                        ),
                        "store_current_orders_per_day": str(per_day(store_current["order_count"], window.current_days)),
                        "store_base_orders_per_day": str(per_day(store_base["order_count"], window.base_days)),
                        "store_calendar_adjusted_base_orders_per_day": str(per_day(store_adjusted["order_count"], window.current_days)),
                        "store_orders_delta_vs_calendar_adjusted_per_day": str(
                            q2(per_day(store_current["order_count"], window.current_days) - per_day(store_adjusted["order_count"], window.current_days))
                        ),
                    }
                )
            rows.append(row)
    return rows


def build_summary_rows(
    *,
    targets: list[dict[str, Any]],
    geo_product_rows: list[dict[str, Any]],
    warehouse_rows: list[dict[str, Any]],
    paid_nonpaid_rows: list[dict[str, Any]],
    availability_rows: list[dict[str, Any]],
    variant_rows: list[dict[str, Any]],
    product_context_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    geo_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wh_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    paid_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    avail_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    variant_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    context_idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in geo_product_rows:
        if row["window_label"] == "short_7d_vs_prev_28d":
            geo_idx[row["entity_id"]].append(row)
    for row in warehouse_rows:
        if row["window_label"] == "short_7d_vs_prev_28d":
            wh_idx[row["entity_id"]].append(row)
    for row in paid_nonpaid_rows:
        if row["window_label"] == "short_7d_vs_prev_28d":
            paid_idx[row["entity_id"]].append(row)
    for row in availability_rows:
        avail_idx[row["product_id"]].append(row)
    for row in variant_rows:
        variant_idx[row["product_id"]].append(row)
    for row in product_context_rows:
        if row["window_label"] == "short_7d_vs_prev_28d":
            context_idx[row["product_id"]].append(row)

    def breadth_signal(rows: list[dict[str, Any]], field: str) -> str:
        active = [row for row in rows if q2(row["base_revenue_per_day"]) > 0 or q2(row["base_orders_per_day"]) > 0]
        negative = [row for row in active if q2(row[field]) < 0]
        if len(active) >= 5 and len(negative) / len(active) >= 0.7:
            return "broad_drop"
        if negative:
            return "localized_drop"
        return "no_drop_signal"

    def top_dims(rows: list[dict[str, Any]], field: str, reverse: bool = False, limit: int = 3) -> str:
        ordered = sorted(rows, key=lambda item: q2(item[field]), reverse=reverse)
        picked = ordered[:limit]
        return "; ".join(f"{row['dimension_value']} ({row[field]})" for row in picked)

    summary_rows: list[dict[str, Any]] = []
    for target in targets:
        product_id = target["product_id"]
        geo_rows = geo_idx[product_id]
        wh_name_rows = [row for row in wh_idx[product_id] if row["dimension"] == "warehouse_name"]
        paid_row = paid_idx[product_id][0] if paid_idx[product_id] else {}
        context_row = context_idx[product_id][0] if context_idx[product_id] else {}
        variant_row = variant_idx[product_id][0] if variant_idx[product_id] else {}
        product_availability_rows = avail_idx[product_id]
        broad_geo = breadth_signal(geo_rows, "revenue_delta_vs_calendar_adjusted_per_day")
        broad_wh = breadth_signal(wh_name_rows, "revenue_delta_vs_calendar_adjusted_per_day")

        ad_signal = as_text(paid_row.get("ad_signal"))
        flow_signal = as_text(paid_row.get("flow_signal"))
        stock_risk_count = sum(1 for row in product_availability_rows if row["availability_risk_flag"] == "snapshot_zero_or_drop_overlap")
        duplicate_name_count = int(variant_row.get("same_name_product_id_count") or 0)
        availability_signal = (
            "snapshot_overlap_risk" if stock_risk_count > 0 else (
                "snapshot_zero_stock_some_warehouses" if any(q2(row["stock_available"]) == 0 for row in product_availability_rows) else "no_snapshot_shortage_signal"
            )
        )

        if target["comparison_role"] == "stable_comparator":
            strongest_signal = "stable_comparator_holding_up_vs_store"
        elif availability_signal == "snapshot_overlap_risk" and stock_risk_count >= 2:
            strongest_signal = "availability_constraint_risk"
        elif ad_signal in {"budget_down", "same_budget_weaker_output"}:
            strongest_signal = "paid_channel_issue_or_budget_shift"
        elif flow_signal in {"non_paid_weaker_than_paid", "paid_stable_non_paid_main_drop"}:
            strongest_signal = "organic_or_non_paid_demand_drop"
        elif broad_geo == "localized_drop" or broad_wh == "localized_drop":
            strongest_signal = "localized_geo_or_warehouse_drop"
        elif as_text(target.get("relative_to_store")) == "falling_with_store":
            strongest_signal = "store_background_plus_product_drop"
        else:
            strongest_signal = "broad_demand_weakness"

        summary_rows.append(
            {
                "product_id": product_id,
                "product_name": target["product_name"],
                "comparison_role": target["comparison_role"],
                "relative_to_store": target.get("relative_to_store", ""),
                "dominant_pattern": target.get("dominant_pattern", ""),
                "short_residual_profit_after_calendar_per_day": target.get("short_residual_profit_after_calendar_per_day", ""),
                "short_residual_revenue_after_calendar_per_day": target.get("short_residual_revenue_after_calendar_per_day", ""),
                "short_actual_profit_delta_per_day": target.get("short_actual_profit_delta_per_day", ""),
                "short_actual_revenue_delta_per_day": target.get("short_actual_revenue_delta_per_day", ""),
                "short_profit_delta_per_day": target.get("short_profit_delta_per_day", target.get("short_profit_after_ads_delta_per_day", "")),
                "short_revenue_delta_per_day": target.get("short_revenue_delta_per_day", ""),
                "geo_signal": broad_geo,
                "warehouse_signal": broad_wh,
                "top_negative_regions": top_dims(geo_rows, "revenue_delta_vs_calendar_adjusted_per_day"),
                "top_stable_regions": top_dims(geo_rows, "revenue_delta_vs_calendar_adjusted_per_day", reverse=True),
                "top_negative_warehouses": top_dims(wh_name_rows, "revenue_delta_vs_calendar_adjusted_per_day"),
                "top_stable_warehouses": top_dims(wh_name_rows, "revenue_delta_vs_calendar_adjusted_per_day", reverse=True),
                "ad_signal": ad_signal,
                "flow_signal": flow_signal,
                "paid_order_delta_per_day": paid_row.get("paid_orders_delta_per_day", ""),
                "non_paid_order_delta_per_day": paid_row.get("non_paid_orders_delta_per_day", ""),
                "availability_signal": availability_signal,
                "availability_risk_overlap_count": stock_risk_count,
                "variant_signal": variant_row.get("variant_signal", ""),
                "same_name_product_id_count": duplicate_name_count,
                "same_name_product_ids": variant_row.get("same_name_product_ids", ""),
                "same_name_residual_signals": variant_row.get("same_name_residual_signals", ""),
                "strongest_causal_signal": strongest_signal,
                "best_fit_explanation": target.get("best_fit_explanation", ""),
                "calendar_explained_share_profit_abs": target.get("short_calendar_explained_share_profit_abs", ""),
                "store_short_revenue_delta_pct": context_row.get("store_revenue_delta_pct", ""),
                "store_short_profit_delta_pct": context_row.get("store_profit_after_ads_delta_pct", ""),
                "store_short_orders_delta_pct": context_row.get("store_orders_delta_pct", ""),
            }
        )
    return summary_rows


def build_paid_nonpaid_rows(
    *,
    targets: list[dict[str, Any]],
    windows: list[WindowDef],
    entity_window_metrics: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    entity_defs = [(STORE_ENTITY_ID, STORE_ENTITY_NAME, "store", "store_total")]
    entity_defs.extend((target["product_id"], target["product_name"], "product", target["comparison_role"]) for target in targets)
    for entity_id, entity_name, scope, comparison_role in entity_defs:
        for window in windows:
            metrics = entity_window_metrics[entity_id][window.label]
            current = metrics["current"]
            base = metrics["base"]
            current_paid_orders = per_day(current["ad_orders"], window.current_days)
            base_paid_orders = per_day(base["ad_orders"], window.base_days)
            current_non_paid_orders = per_day(current["non_paid_order_proxy"], window.current_days)
            base_non_paid_orders = per_day(base["non_paid_order_proxy"], window.base_days)
            current_paid_revenue = per_day(current["ad_revenue"], window.current_days)
            base_paid_revenue = per_day(base["ad_revenue"], window.base_days)
            current_non_paid_revenue = per_day(current["non_paid_revenue_proxy"], window.current_days)
            base_non_paid_revenue = per_day(base["non_paid_revenue_proxy"], window.base_days)
            paid_delta = q2(current_paid_orders - base_paid_orders)
            non_paid_delta = q2(current_non_paid_orders - base_non_paid_orders)
            budget_delta = q2(per_day(current["ad_spend"], window.current_days) - per_day(base["ad_spend"], window.base_days))
            if budget_delta <= Decimal("-50.00"):
                ad_signal = "budget_down"
            elif abs(budget_delta) < Decimal("50.00") and paid_delta < Decimal("0"):
                ad_signal = "same_budget_weaker_output"
            elif abs(budget_delta) < Decimal("50.00") and paid_delta >= Decimal("-0.50"):
                ad_signal = "ads_not_main_drop_signal"
            else:
                ad_signal = "mixed_ads_signal"
            if non_paid_delta < paid_delta and non_paid_delta < 0:
                flow_signal = "non_paid_weaker_than_paid"
            elif paid_delta < 0 and non_paid_delta >= 0:
                flow_signal = "paid_weaker_than_non_paid"
            elif paid_delta < 0 and non_paid_delta < 0:
                flow_signal = "both_paid_and_non_paid_down"
            else:
                flow_signal = "paid_stable_non_paid_main_drop"

            total_order_delta = q2(per_day(current["order_count"], window.current_days) - per_day(base["order_count"], window.base_days))
            rows.append(
                {
                    "entity_scope": scope,
                    "entity_id": entity_id,
                    "entity_name": entity_name,
                    "comparison_role": comparison_role,
                    "window_label": window.label,
                    "current_start": window.current_start.isoformat(),
                    "current_end": window.current_end.isoformat(),
                    "base_start": window.base_start.isoformat(),
                    "base_end": window.base_end.isoformat(),
                    "current_orders_per_day": str(per_day(current["order_count"], window.current_days)),
                    "base_orders_per_day": str(per_day(base["order_count"], window.base_days)),
                    "current_paid_orders_per_day": str(current_paid_orders),
                    "base_paid_orders_per_day": str(base_paid_orders),
                    "paid_orders_delta_per_day": str(paid_delta),
                    "current_non_paid_orders_per_day": str(current_non_paid_orders),
                    "base_non_paid_orders_per_day": str(base_non_paid_orders),
                    "non_paid_orders_delta_per_day": str(non_paid_delta),
                    "current_paid_revenue_per_day": str(current_paid_revenue),
                    "base_paid_revenue_per_day": str(base_paid_revenue),
                    "paid_revenue_delta_per_day": str(q2(current_paid_revenue - base_paid_revenue)),
                    "current_non_paid_revenue_per_day": str(current_non_paid_revenue),
                    "base_non_paid_revenue_per_day": str(base_non_paid_revenue),
                    "non_paid_revenue_delta_per_day": str(q2(current_non_paid_revenue - base_non_paid_revenue)),
                    "current_ad_spend_per_day": str(per_day(current["ad_spend"], window.current_days)),
                    "base_ad_spend_per_day": str(per_day(base["ad_spend"], window.base_days)),
                    "ad_spend_delta_per_day": str(budget_delta),
                    "current_ctr": str(current["ctr"]),
                    "base_ctr": str(base["ctr"]),
                    "current_cpc": str(current["cpc"]),
                    "base_cpc": str(base["cpc"]),
                    "current_cr": str(current["cr"]),
                    "base_cr": str(base["cr"]),
                    "current_cost_per_order": str(current["cost_per_order"]),
                    "base_cost_per_order": str(base["cost_per_order"]),
                    "paid_contribution_to_total_order_delta_share": str(q4(safe_div(paid_delta, total_order_delta))),
                    "non_paid_contribution_to_total_order_delta_share": str(q4(safe_div(non_paid_delta, total_order_delta))),
                    "ad_signal": ad_signal,
                    "flow_signal": flow_signal,
                    "paid_nonpaid_method": "proxy_from_ads_attributed_orders_and_total_orders",
                }
            )
    return rows


def build_campaign_rows(
    *,
    targets: list[dict[str, Any]],
    ads_rows: list[dict[str, Any]],
    windows: list[WindowDef],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    entity_defs = [(STORE_ENTITY_ID, STORE_ENTITY_NAME, "store", "store_total")]
    entity_defs.extend((target["product_id"], target["product_name"], "product", target["comparison_role"]) for target in targets)
    for entity_id, entity_name, scope, comparison_role in entity_defs:
        entity_ads = entity_rows(ads_rows, entity_id)
        campaigns = sorted(
            {
                (
                    as_text(row.get("campaign_id")),
                    as_text(row.get("campaign_name")),
                    as_text(row.get("campaign_type")),
                    as_text(row.get("app_type")),
                )
                for row in entity_ads
            }
        )
        for window in windows:
            for campaign_id, campaign_name, campaign_type, app_type in campaigns:
                current_rows = [
                    row
                    for row in entity_ads
                    if as_text(row.get("campaign_id")) == campaign_id
                    and day_as_date(row["day"]) in set(window_days(window, "current"))
                ]
                base_rows = [
                    row
                    for row in entity_ads
                    if as_text(row.get("campaign_id")) == campaign_id
                    and day_as_date(row["day"]) in set(window_days(window, "base"))
                ]
                if not current_rows and not base_rows:
                    continue
                current = aggregate_ads_metrics(current_rows)
                base = aggregate_ads_metrics(base_rows)
                rows.append(
                    {
                        "entity_scope": scope,
                        "entity_id": entity_id,
                        "entity_name": entity_name,
                        "comparison_role": comparison_role,
                        "window_label": window.label,
                        "current_start": window.current_start.isoformat(),
                        "current_end": window.current_end.isoformat(),
                        "base_start": window.base_start.isoformat(),
                        "base_end": window.base_end.isoformat(),
                        "campaign_id": campaign_id,
                        "campaign_name": campaign_name,
                        "campaign_type": campaign_type,
                        "app_type": app_type,
                        "placement_status": "placement_unavailable_only_app_type_present",
                        "current_spend_per_day": str(per_day(current["ad_spend"], window.current_days)),
                        "base_spend_per_day": str(per_day(base["ad_spend"], window.base_days)),
                        "spend_delta_per_day": str(q2(per_day(current["ad_spend"], window.current_days) - per_day(base["ad_spend"], window.base_days))),
                        "current_impressions_per_day": str(per_day(current["impressions"], window.current_days)),
                        "base_impressions_per_day": str(per_day(base["impressions"], window.base_days)),
                        "current_clicks_per_day": str(per_day(current["clicks"], window.current_days)),
                        "base_clicks_per_day": str(per_day(base["clicks"], window.base_days)),
                        "current_ad_orders_per_day": str(per_day(current["ad_orders"], window.current_days)),
                        "base_ad_orders_per_day": str(per_day(base["ad_orders"], window.base_days)),
                        "current_ad_revenue_per_day": str(per_day(current["ad_revenue"], window.current_days)),
                        "base_ad_revenue_per_day": str(per_day(base["ad_revenue"], window.base_days)),
                        "current_ctr": str(current["ctr"]),
                        "base_ctr": str(base["ctr"]),
                        "current_cpc": str(current["cpc"]),
                        "base_cpc": str(base["cpc"]),
                        "current_cr": str(current["cr"]),
                        "base_cr": str(base["cr"]),
                        "current_cost_per_order": str(current["cost_per_order"]),
                        "base_cost_per_order": str(base["cost_per_order"]),
                    }
                )
    rows.sort(key=lambda item: (item["entity_scope"], item["entity_id"], item["window_label"], q2(item["base_spend_per_day"])), reverse=False)
    return rows


def build_variant_rows(
    *,
    targets: list[dict[str, Any]],
    sales_rows: list[dict[str, Any]],
    order_rows: list[dict[str, Any]],
    windows: list[WindowDef],
    strength_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    name_to_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in strength_rows:
        name_to_rows[row["product_name"]].append(row)

    rows: list[dict[str, Any]] = []
    for target in targets:
        product_id = target["product_id"]
        product_name = target["product_name"]
        same_name_rows = sorted(name_to_rows[product_name], key=lambda item: item["product_id"])
        sales_subset = [row for row in sales_rows if row["product_id"] == product_id]
        order_subset = [row for row in order_rows if row["product_id"] == product_id]
        variant_keys = sorted(
            {
                (
                    as_text(row.get("offer_id")),
                    as_text(row.get("barcode")),
                    as_text(row.get("tech_size")),
                    as_text(row.get("supplier_article")),
                )
                for row in sales_subset + order_subset
            }
        )
        if not variant_keys:
            variant_keys = [("", "", "", "")]
        multi_variant = len([key for key in variant_keys if any(key)]) > 1
        for offer_id, barcode, tech_size, supplier_article in variant_keys:
            key_sales = [
                row
                for row in sales_subset
                if as_text(row.get("offer_id")) == offer_id
                and as_text(row.get("barcode")) == barcode
                and as_text(row.get("tech_size")) == tech_size
                and as_text(row.get("supplier_article")) == supplier_article
            ]
            key_orders = [
                row
                for row in order_subset
                if as_text(row.get("offer_id")) == offer_id
                and as_text(row.get("barcode")) == barcode
                and as_text(row.get("tech_size")) == tech_size
                and as_text(row.get("supplier_article")) == supplier_article
            ]
            current_sales = [row for row in key_sales if day_as_date(row["day"]) in set(window_days(windows[0], "current"))]
            base_sales = [row for row in key_sales if day_as_date(row["day"]) in set(window_days(windows[0], "base"))]
            current_orders = [row for row in key_orders if day_as_date(row["day"]) in set(window_days(windows[0], "current"))]
            base_orders = [row for row in key_orders if day_as_date(row["day"]) in set(window_days(windows[0], "base"))]
            current_metrics = combine_metrics(current_sales, current_orders)
            base_metrics = combine_metrics(base_sales, base_orders)
            rows.append(
                {
                    "product_id": product_id,
                    "product_name": product_name,
                    "comparison_role": target["comparison_role"],
                    "offer_id": offer_id,
                    "barcode": barcode,
                    "tech_size": tech_size,
                    "supplier_article": supplier_article,
                    "current_revenue_per_day": str(per_day(current_metrics["revenue_net"], windows[0].current_days)),
                    "base_revenue_per_day": str(per_day(base_metrics["revenue_net"], windows[0].base_days)),
                    "current_orders_per_day": str(per_day(current_metrics["order_count"], windows[0].current_days)),
                    "base_orders_per_day": str(per_day(base_metrics["order_count"], windows[0].base_days)),
                    "variant_signal": "multi_variant_key" if multi_variant else "single_variant_key",
                    "same_name_product_id_count": len(same_name_rows),
                    "same_name_product_ids": ";".join(row["product_id"] for row in same_name_rows),
                    "same_name_residual_signals": ";".join(
                        f"{row['product_id']}:{row['short_residual_profit_after_calendar_per_day']}" for row in same_name_rows
                    ),
                }
            )
    return rows


def build_availability_rows(
    *,
    targets: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]],
    warehouse_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_index = {row["product_id"]: row for row in targets}
    warehouse_idx: dict[tuple[str, str], dict[str, Any]] = {}
    for row in warehouse_rows:
        if row["window_label"] == "short_7d_vs_prev_28d" and row["dimension"] == "warehouse_name":
            warehouse_idx[(row["entity_id"], row["dimension_value"])] = row
    rows: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in inventory_rows:
        grouped[row["product_id"]].append(row)
    for product_id, items in grouped.items():
        target = target_index[product_id]
        days_cover_14d = q2(target.get("days_cover_14d", "0"))
        total_available = q2(sum(q2(item["stock_available"]) for item in items))
        for item in items:
            wh_key = (product_id, as_text(item["warehouse_name"]))
            wh_signal = warehouse_idx.get(wh_key, {})
            base_rev = q2(wh_signal.get("base_revenue_per_day"))
            current_rev = q2(wh_signal.get("current_revenue_per_day"))
            rev_delta = q2(wh_signal.get("revenue_delta_vs_calendar_adjusted_per_day"))
            drop_overlap = base_rev > 0 and current_rev <= Decimal("0") and q2(item["stock_available"]) <= 0
            rows.append(
                {
                    "product_id": product_id,
                    "product_name": target["product_name"],
                    "comparison_role": target["comparison_role"],
                    "snapshot_date": item["snapshot_date"].isoformat(),
                    "warehouse_name": as_text(item["warehouse_name"]),
                    "warehouse_type": as_text(item.get("warehouse_type")),
                    "stock_total": str(q2(item["stock_total"])),
                    "stock_available": str(q2(item["stock_available"])),
                    "stock_reserved": str(q2(item["stock_reserved"])),
                    "stock_defect": str(q2(item["stock_defect"])),
                    "product_total_available": str(total_available),
                    "days_cover_14d": str(days_cover_14d),
                    "warehouse_current_revenue_per_day": str(current_rev),
                    "warehouse_base_revenue_per_day": str(base_rev),
                    "warehouse_revenue_delta_vs_calendar_adjusted_per_day": str(rev_delta),
                    "availability_risk_flag": (
                        "snapshot_zero_or_drop_overlap"
                        if drop_overlap
                        else ("snapshot_low_cover" if days_cover_14d and days_cover_14d < Decimal("10.00") else "no_snapshot_overlap_risk")
                    ),
                    "availability_note": (
                        "single snapshot only; overlap with dropped warehouse is suggestive, not proof"
                        if drop_overlap
                        else "single WB snapshot only; no direct time-series stock proof"
                    ),
                }
            )
    return rows


def build_product_context_rows(
    *,
    targets: list[dict[str, Any]],
    store_window_index: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in targets:
        for window_label in ["short_7d_vs_prev_28d", "medium_14d_vs_prev_42d"]:
            store_rev = store_window_index.get(f"{window_label}::revenue_net", {})
            store_profit = store_window_index.get(f"{window_label}::profit_after_ads", {})
            store_orders = store_window_index.get(f"{window_label}::order_count", {})
            prefix = "short" if window_label.startswith("short") else "medium"
            rows.append(
                {
                    "product_id": target["product_id"],
                    "product_name": target["product_name"],
                    "comparison_role": target["comparison_role"],
                    "window_label": window_label,
                    "product_current_revenue_total": target.get(f"{prefix}_current_revenue_total", ""),
                    "product_base_revenue_total": target.get(f"{prefix}_base_revenue_total", ""),
                    "product_current_revenue_per_day": str(per_day(q2(target.get(f"{prefix}_current_revenue_total", "0")), 7 if prefix == "short" else 14)),
                    "product_base_revenue_per_day": str(per_day(q2(target.get(f"{prefix}_base_revenue_total", "0")), 28 if prefix == "short" else 42)),
                    "product_profit_after_ads_delta_per_day": target.get(f"{prefix}_profit_delta_per_day", target.get(f"{prefix}_profit_after_ads_delta_per_day", "")),
                    "product_revenue_delta_per_day": target.get(f"{prefix}_revenue_delta_per_day", ""),
                    "product_ad_spend_delta_per_day": target.get(f"{prefix}_ad_spend_delta_per_day", ""),
                    "product_logistics_delta_per_day": target.get(f"{prefix}_logistics_delta_per_day", ""),
                    "product_withholding_delta_per_day": target.get(f"{prefix}_withholding_delta_per_day", ""),
                    "product_residual_profit_after_calendar_per_day": target.get("short_residual_profit_after_calendar_per_day", "") if prefix == "short" else "",
                    "product_residual_revenue_after_calendar_per_day": target.get("short_residual_revenue_after_calendar_per_day", "") if prefix == "short" else "",
                    "store_current_revenue_per_day": store_rev.get("current_per_day", ""),
                    "store_base_revenue_per_day": store_rev.get("base_per_day", ""),
                    "store_revenue_delta_per_day": store_rev.get("delta_per_day", ""),
                    "store_revenue_delta_pct": store_rev.get("delta_pct_vs_base_per_day", ""),
                    "store_current_profit_after_ads_per_day": store_profit.get("current_per_day", ""),
                    "store_base_profit_after_ads_per_day": store_profit.get("base_per_day", ""),
                    "store_profit_after_ads_delta_per_day": store_profit.get("delta_per_day", ""),
                    "store_profit_after_ads_delta_pct": store_profit.get("delta_pct_vs_base_per_day", ""),
                    "store_current_orders_per_day": store_orders.get("current_per_day", ""),
                    "store_base_orders_per_day": store_orders.get("base_per_day", ""),
                    "store_orders_delta_per_day": store_orders.get("delta_per_day", ""),
                    "store_orders_delta_pct": store_orders.get("delta_pct_vs_base_per_day", ""),
                    "relative_to_store": target.get("relative_to_store", ""),
                    "dominant_pattern": target.get("dominant_pattern", ""),
                    "best_fit_explanation": target.get("best_fit_explanation", ""),
                }
            )
    return rows


def build_geo_map_rows(geo_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    absolute_rows: list[dict[str, Any]] = []
    delta_rows: list[dict[str, Any]] = []
    contribution_rows: list[dict[str, Any]] = []
    for row in geo_rows:
        base_common = {
            "entity_scope": row["entity_scope"],
            "entity_id": row["entity_id"],
            "entity_name": row["entity_name"],
            "comparison_role": row["comparison_role"],
            "window_label": row["window_label"],
            "current_start": row["current_start"],
            "current_end": row["current_end"],
            "base_start": row["base_start"],
            "base_end": row["base_end"],
            "region_name": row["dimension_value"],
            "reliability_flag": row["reliability_flag"],
            "reliability_reason": row["reliability_reason"],
        }
        metric_map = {
            "revenue_net": {
                "current_total": row["current_revenue_total"],
                "base_total": row["base_revenue_total"],
                "adjusted_total": row["calendar_adjusted_base_revenue_total"],
                "current_per_day": row["current_revenue_per_day"],
                "base_per_day": row["base_revenue_per_day"],
                "adjusted_per_day": row["calendar_adjusted_base_revenue_per_day"],
                "delta_base_per_day": row["revenue_delta_vs_base_per_day"],
                "delta_base_pct": row["revenue_delta_pct_vs_base_per_day"],
                "delta_adjusted_per_day": row["revenue_delta_vs_calendar_adjusted_per_day"],
                "delta_adjusted_pct": row["revenue_delta_pct_vs_calendar_adjusted_per_day"],
                "contribution_share": row["revenue_contribution_to_entity_delta_share"],
                "available": "true",
            },
            "order_count": {
                "current_total": row["current_orders_total"],
                "base_total": row["base_orders_total"],
                "adjusted_total": row["calendar_adjusted_base_orders_total"],
                "current_per_day": row["current_orders_per_day"],
                "base_per_day": row["base_orders_per_day"],
                "adjusted_per_day": row["calendar_adjusted_base_orders_per_day"],
                "delta_base_per_day": row["orders_delta_vs_base_per_day"],
                "delta_base_pct": row["orders_delta_pct_vs_base_per_day"],
                "delta_adjusted_per_day": row["orders_delta_vs_calendar_adjusted_per_day"],
                "delta_adjusted_pct": row["orders_delta_pct_vs_calendar_adjusted_per_day"],
                "contribution_share": row["orders_contribution_to_entity_delta_share"],
                "available": "true",
            },
            "profit_after_ads": {
                "current_total": row["current_profit_after_ads_total"],
                "base_total": row["base_profit_after_ads_total"],
                "adjusted_total": row["calendar_adjusted_base_profit_after_ads_total"],
                "current_per_day": row["current_profit_after_ads_per_day"],
                "base_per_day": row["base_profit_after_ads_per_day"],
                "adjusted_per_day": row["calendar_adjusted_base_profit_after_ads_per_day"],
                "delta_base_per_day": "",
                "delta_base_pct": "",
                "delta_adjusted_per_day": "",
                "delta_adjusted_pct": "",
                "contribution_share": "",
                "available": "false",
            },
        }
        for metric, values in metric_map.items():
            for period_kind, total_key, pd_key in [
                ("current", "current_total", "current_per_day"),
                ("base", "base_total", "base_per_day"),
                ("calendar_adjusted_base", "adjusted_total", "adjusted_per_day"),
            ]:
                absolute_rows.append(
                    {
                        **base_common,
                        "metric": metric,
                        "period_kind": period_kind,
                        "value_total": values[total_key],
                        "value_per_day": values[pd_key],
                        "metric_available": values["available"],
                        "profit_after_ads_status": row["profit_after_ads_status"],
                    }
                )
            delta_rows.append(
                {
                    **base_common,
                    "metric": metric,
                    "delta_vs_base_per_day": values["delta_base_per_day"],
                    "delta_pct_vs_base_per_day": values["delta_base_pct"],
                    "delta_vs_calendar_adjusted_per_day": values["delta_adjusted_per_day"],
                    "delta_pct_vs_calendar_adjusted_per_day": values["delta_adjusted_pct"],
                    "metric_available": values["available"],
                    "profit_after_ads_status": row["profit_after_ads_status"],
                }
            )
            contribution_rows.append(
                {
                    **base_common,
                    "metric": metric,
                    "delta_vs_calendar_adjusted_per_day": values["delta_adjusted_per_day"],
                    "contribution_share_of_entity_delta": values["contribution_share"],
                    "metric_available": values["available"],
                    "profit_after_ads_status": row["profit_after_ads_status"],
                }
            )
    return absolute_rows, delta_rows, contribution_rows


def build_external_signal_rows(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shared_rows = [
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "price_history",
            "layer": "external_market",
            "current_status": "missing",
            "blocking_hypothesis": "cannot separate demand loss from price-position loss",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "search_position",
            "layer": "traffic",
            "current_status": "missing",
            "blocking_hypothesis": "cannot prove ranking/visibility loss behind non-paid drop",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "rating_reviews_timeline",
            "layer": "card_quality",
            "current_status": "missing",
            "blocking_hypothesis": "cannot confirm review/rating shock by date",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "card_content_change_log",
            "layer": "card_quality",
            "current_status": "missing",
            "blocking_hypothesis": "cannot confirm content/photo edits before drop",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "competitor_snapshot",
            "layer": "external_market",
            "current_status": "missing",
            "blocking_hypothesis": "cannot separate local SKU weakness from competitor pressure",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "stock_history_by_warehouse",
            "layer": "availability",
            "current_status": "missing",
            "blocking_hypothesis": "only one WB stock snapshot exists on 2026-03-11",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "region_level_profit_after_ads",
            "layer": "geo_profit",
            "current_status": "missing",
            "blocking_hypothesis": "finance and ads are not attributable to region with current typed keys",
        },
        {
            "scope": "global",
            "product_id": "",
            "product_name": "",
            "signal_name": "delivery_zone_or_eta",
            "layer": "delivery",
            "current_status": "missing",
            "blocking_hypothesis": "cannot test delivery-zone deterioration separately from warehouse effect",
        },
    ]
    per_product_rows: list[dict[str, Any]] = []
    for target in targets:
        per_product_rows.append(
            {
                "scope": "product",
                "product_id": target["product_id"],
                "product_name": target["product_name"],
                "signal_name": "same_name_sku_split_check",
                "layer": "catalog_structure",
                "current_status": "partial",
                "blocking_hypothesis": "duplicate names across product_id can mask whether traffic shifted to sibling SKU",
            }
        )
    return shared_rows + per_product_rows


def build_report(
    *,
    targets: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    geo_product_rows: list[dict[str, Any]],
    warehouse_rows: list[dict[str, Any]],
    paid_nonpaid_rows: list[dict[str, Any]],
    campaign_rows: list[dict[str, Any]],
    availability_rows: list[dict[str, Any]],
    variant_rows: list[dict[str, Any]],
    product_context_rows: list[dict[str, Any]],
) -> str:
    summary_idx = {row["product_id"]: row for row in summary_rows}
    context_idx: dict[tuple[str, str], dict[str, Any]] = {
        (row["product_id"], row["window_label"]): row for row in product_context_rows
    }
    paid_idx: dict[tuple[str, str], dict[str, Any]] = {
        (row["entity_id"], row["window_label"]): row for row in paid_nonpaid_rows if row["entity_scope"] == "product"
    }
    target_names = [f"{row['product_name']} [{row['product_id']}]" for row in targets]
    lines = [
        "# PROFIT CONTROL — Weak SKU Causal Drilldown v2",
        "",
        f"Date: {now_iso()}",
        "",
        "## Scope",
        "",
        f"- Target SKUs: {', '.join(f'`{name}`' for name in target_names)}.",
        "- Windows: `2026-03-16..2026-03-22` vs `2026-02-16..2026-03-15`, and `2026-03-09..2026-03-22` vs `2026-01-26..2026-03-08`.",
        "- Geo/warehouse deltas include calendar-adjusted baselines where possible by matching current day mix to base calendar segments; fallback level is tracked in reliability flags.",
        "- Region-level `profit_after_ads` is intentionally left unavailable: current finance/ads attribution is product-level, not region-level, so a geo profit number would be methodologically unsafe.",
        "- Stock history remains limited to one WB snapshot date: `2026-03-11`.",
        "",
        "## Target Summary",
        "",
        md_table(
            summary_rows,
            [
                "product_name",
                "comparison_role",
                "relative_to_store",
                "short_residual_profit_after_calendar_per_day",
                "short_residual_revenue_after_calendar_per_day",
                "geo_signal",
                "warehouse_signal",
                "ad_signal",
                "flow_signal",
                "availability_signal",
                "strongest_causal_signal",
            ],
        ),
        "",
        "## Per-SKU Causal Notes",
        "",
    ]
    for target in targets:
        product_id = target["product_id"]
        summary = summary_idx[product_id]
        short_context = context_idx[(product_id, "short_7d_vs_prev_28d")]
        medium_context = context_idx[(product_id, "medium_14d_vs_prev_42d")]
        paid_short = paid_idx[(product_id, "short_7d_vs_prev_28d")]
        geo_short = [
            row
            for row in geo_product_rows
            if row["entity_id"] == product_id and row["window_label"] == "short_7d_vs_prev_28d"
        ]
        wh_short = [
            row
            for row in warehouse_rows
            if row["entity_id"] == product_id
            and row["window_label"] == "short_7d_vs_prev_28d"
            and row["dimension"] == "warehouse_name"
        ]
        top_negative_regions = sorted(geo_short, key=lambda item: q2(item["revenue_delta_vs_calendar_adjusted_per_day"]))[:5]
        holding_regions = sorted(geo_short, key=lambda item: q2(item["revenue_delta_vs_calendar_adjusted_per_day"]), reverse=True)[:3]
        top_negative_warehouses = sorted(wh_short, key=lambda item: q2(item["revenue_delta_vs_calendar_adjusted_per_day"]))[:5]
        holding_warehouses = sorted(wh_short, key=lambda item: q2(item["revenue_delta_vs_calendar_adjusted_per_day"]), reverse=True)[:3]
        campaign_short = [
            row
            for row in campaign_rows
            if row["entity_id"] == product_id and row["window_label"] == "short_7d_vs_prev_28d"
        ]
        campaign_short.sort(key=lambda item: q2(item["base_spend_per_day"]), reverse=True)
        availability_short = [row for row in availability_rows if row["product_id"] == product_id][:8]
        variant_short = [row for row in variant_rows if row["product_id"] == product_id][:6]
        lines.extend(
            [
                f"### {target['product_name']} [{product_id}]",
                "",
                "**Level 1 — Fact**",
                "",
                f"- Short residual after calendar: profit `{money(summary['short_residual_profit_after_calendar_per_day'])}` per day, revenue `{money(summary['short_residual_revenue_after_calendar_per_day'])}` per day.",
                f"- Short raw product revenue delta: `{money(short_context['product_revenue_delta_per_day'])}` per day vs store `{short_context['store_revenue_delta_per_day']}` per day.",
                f"- Medium raw product revenue delta: `{money(medium_context['product_revenue_delta_per_day'])}` per day.",
                "",
                "**Level 2 — Components**",
                "",
                f"- Orders: paid delta `{paid_short['paid_orders_delta_per_day']}` per day, non-paid delta `{paid_short['non_paid_orders_delta_per_day']}` per day.",
                f"- Ads: spend delta `{paid_short['ad_spend_delta_per_day']}` per day, CTR `{paid_short['current_ctr']}` vs `{paid_short['base_ctr']}`, CR `{paid_short['current_cr']}` vs `{paid_short['base_cr']}`.",
                f"- Financial context: logistics delta `{short_context['product_logistics_delta_per_day']}` per day, withholding delta `{short_context['product_withholding_delta_per_day']}` per day.",
                "",
                "**Level 3 — Context**",
                "",
                f"- Store relation: `{summary['relative_to_store']}`; dominant pattern `{summary['dominant_pattern']}`.",
                f"- Geography: `{summary['geo_signal']}`; weakest regions `{summary['top_negative_regions']}`; holding regions `{summary['top_stable_regions']}`.",
                f"- Warehouses: `{summary['warehouse_signal']}`; weakest warehouses `{summary['top_negative_warehouses']}`; holding warehouses `{summary['top_stable_warehouses']}`.",
                f"- Availability: `{summary['availability_signal']}`; variant/internal key signal `{summary['variant_signal']}`.",
                "",
                "**Level 4 — Hypothesis**",
                "",
                f"- Most likely explanation now: `{summary['strongest_causal_signal']}`.",
                "",
                "Top negative regions:",
                "",
                md_table(
                    top_negative_regions,
                    [
                        "dimension_value",
                        "current_revenue_per_day",
                        "calendar_adjusted_base_revenue_per_day",
                        "revenue_delta_vs_calendar_adjusted_per_day",
                        "current_orders_per_day",
                        "calendar_adjusted_base_orders_per_day",
                        "reliability_flag",
                    ],
                ),
                "",
                "Holding regions:",
                "",
                md_table(
                    holding_regions,
                    [
                        "dimension_value",
                        "current_revenue_per_day",
                        "calendar_adjusted_base_revenue_per_day",
                        "revenue_delta_vs_calendar_adjusted_per_day",
                        "reliability_flag",
                    ],
                ),
                "",
                "Top negative warehouses:",
                "",
                md_table(
                    top_negative_warehouses,
                    [
                        "dimension_value",
                        "current_revenue_per_day",
                        "calendar_adjusted_base_revenue_per_day",
                        "revenue_delta_vs_calendar_adjusted_per_day",
                        "reliability_flag",
                    ],
                ),
                "",
                "Holding warehouses:",
                "",
                md_table(
                    holding_warehouses,
                    [
                        "dimension_value",
                        "current_revenue_per_day",
                        "calendar_adjusted_base_revenue_per_day",
                        "revenue_delta_vs_calendar_adjusted_per_day",
                        "reliability_flag",
                    ],
                ),
                "",
                "Paid vs non-paid:",
                "",
                md_table(
                    [paid_short],
                    [
                        "current_paid_orders_per_day",
                        "base_paid_orders_per_day",
                        "paid_orders_delta_per_day",
                        "current_non_paid_orders_per_day",
                        "base_non_paid_orders_per_day",
                        "non_paid_orders_delta_per_day",
                        "ad_signal",
                        "flow_signal",
                    ],
                ),
                "",
                "Campaign detail:",
                "",
                md_table(
                    campaign_short[:6],
                    [
                        "campaign_name",
                        "app_type",
                        "current_spend_per_day",
                        "base_spend_per_day",
                        "current_clicks_per_day",
                        "base_clicks_per_day",
                        "current_ad_orders_per_day",
                        "base_ad_orders_per_day",
                        "current_cr",
                        "base_cr",
                    ],
                ),
                "",
                "Availability snapshot:",
                "",
                md_table(
                    availability_short,
                    [
                        "snapshot_date",
                        "warehouse_name",
                        "stock_available",
                        "warehouse_base_revenue_per_day",
                        "warehouse_current_revenue_per_day",
                        "availability_risk_flag",
                    ],
                ),
                "",
                "Variant and internal key check:",
                "",
                md_table(
                    variant_short,
                    [
                        "offer_id",
                        "barcode",
                        "tech_size",
                        "supplier_article",
                        "variant_signal",
                        "same_name_product_id_count",
                        "same_name_product_ids",
                    ],
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## Internal Boundary",
            "",
            "- Facts here prove where weakness is broad vs localized, whether paid or non-paid flow weakened, and whether any warehouse overlap with zero stock exists on the single available snapshot.",
            "- Facts here do not prove external demand causes such as price, ranking, reviews, card content, or competitor moves.",
            "- Delivery-zone layer and region-level profit-after-ads remain unavailable in the current typed WB stack.",
            "",
        ]
    )
    return "\n".join(lines)


def build_internal_note(summary_rows: list[dict[str, Any]]) -> str:
    weak_rows = [row for row in summary_rows if row["comparison_role"] != "stable_comparator"]
    stable_rows = [row for row in summary_rows if row["comparison_role"] == "stable_comparator"]
    strongest = sorted(weak_rows, key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]))[:3]
    local_geo = [row for row in weak_rows if row["geo_signal"] == "localized_drop" or row["warehouse_signal"] == "localized_drop"]
    store_like = [row for row in weak_rows if row["relative_to_store"] == "falling_with_store"]
    lines = [
        "# Internal Causal Note",
        "",
        "## Strongest Causal Signal",
        "",
    ]
    for row in strongest:
        lines.append(
            f"- {row['product_name']} [{row['product_id']}]: {row['strongest_causal_signal']}; residual profit/day {row['short_residual_profit_after_calendar_per_day']}."
        )
    lines.extend(
        [
            "",
            "## Local Problem Layer",
            "",
        ]
    )
    if local_geo:
        for row in local_geo:
            lines.append(f"- {row['product_name']} [{row['product_id']}]: localized geo/warehouse weakness on top of overall store background.")
    else:
        lines.append("- No selected weak SKU shows a pure isolated local-only pattern; most weakness is broad or mixed.")
    lines.extend(
        [
            "",
            "## Store Background Layer",
            "",
        ]
    )
    if store_like:
        for row in store_like:
            lines.append(f"- {row['product_name']} [{row['product_id']}]: falling with store, so store-wide demand background remains part of explanation.")
    else:
        lines.append("- Selected weak SKUs mostly fall faster than store rather than simply copying store background.")
    lines.extend(
        [
            "",
            "## Stable Comparator",
            "",
        ]
    )
    for row in stable_rows:
        lines.append(f"- {row['product_name']} [{row['product_id']}]: {row['strongest_causal_signal']}.")
    lines.extend(
        [
            "",
            "## Missing External Layer",
            "",
            "- Price/ranking/review/card-content/competitor history.",
            "- Stock history by warehouse beyond the single 2026-03-11 snapshot.",
            "- Region-level finance for a correct geo profit-after-ads layer.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    strength_rows, strength_index, calendar_index, _context_index, product_window_index, store_window_index = load_indices()
    targets = choose_targets(strength_rows, strength_index, calendar_index, product_window_index)
    target_ids = [row["product_id"] for row in targets]

    corridor_start, corridor_end = deep.fetch_corridor_bounds()
    windows = build_windows(corridor_start, corridor_end)
    calendar_meta = cal.build_calendar_metadata(deep.daterange(corridor_start, corridor_end))

    sales_rows = fetch_sales_rows(corridor_start, corridor_end)
    order_rows = fetch_order_rows(corridor_start, corridor_end)
    ads_rows = fetch_ads_rows(corridor_start, corridor_end)
    sales_rows = enrich_sales_with_order_context(sales_rows, order_rows)
    inventory_rows = fetch_inventory_rows(target_ids)

    entity_ids = [STORE_ENTITY_ID, *target_ids]
    entity_window_metrics = build_entity_window_metric_index(
        entity_ids=entity_ids,
        sales_rows=sales_rows,
        order_rows=order_rows,
        ads_rows=ads_rows,
        windows=windows,
        calendar_meta=calendar_meta,
    )

    store_benchmark = {
        STORE_ENTITY_ID: entity_window_metrics[STORE_ENTITY_ID],
        "__sales_rows__": sales_rows,
        "__order_rows__": order_rows,
    }

    geo_store_rows = summarize_dimension_rows(
        entity_scope="store",
        entity_id=STORE_ENTITY_ID,
        entity_name=STORE_ENTITY_NAME,
        comparison_role="store_total",
        dim_name="region_name",
        dim_rows_sales=sales_rows,
        dim_rows_orders=order_rows,
        windows=windows,
        calendar_meta=calendar_meta,
        entity_window_metrics=entity_window_metrics,
    )

    geo_product_rows: list[dict[str, Any]] = []
    warehouse_rows: list[dict[str, Any]] = []
    for target in targets:
        entity_sales = [row for row in sales_rows if row["product_id"] == target["product_id"]]
        entity_orders = [row for row in order_rows if row["product_id"] == target["product_id"]]
        geo_product_rows.extend(
            summarize_dimension_rows(
                entity_scope="product",
                entity_id=target["product_id"],
                entity_name=target["product_name"],
                comparison_role=target["comparison_role"],
                dim_name="region_name",
                dim_rows_sales=entity_sales,
                dim_rows_orders=entity_orders,
                windows=windows,
                calendar_meta=calendar_meta,
                entity_window_metrics=entity_window_metrics,
                store_benchmark=store_benchmark,
            )
        )
        warehouse_rows.extend(
            summarize_dimension_rows(
                entity_scope="product",
                entity_id=target["product_id"],
                entity_name=target["product_name"],
                comparison_role=target["comparison_role"],
                dim_name="warehouse_name",
                dim_rows_sales=entity_sales,
                dim_rows_orders=entity_orders,
                windows=windows,
                calendar_meta=calendar_meta,
                entity_window_metrics=entity_window_metrics,
                store_benchmark=store_benchmark,
            )
        )
        warehouse_rows.extend(
            summarize_dimension_rows(
                entity_scope="product",
                entity_id=target["product_id"],
                entity_name=target["product_name"],
                comparison_role=target["comparison_role"],
                dim_name="warehouse_type",
                dim_rows_sales=entity_sales,
                dim_rows_orders=entity_orders,
                windows=windows,
                calendar_meta=calendar_meta,
                entity_window_metrics=entity_window_metrics,
                store_benchmark=store_benchmark,
            )
        )

    paid_nonpaid_rows = build_paid_nonpaid_rows(
        targets=targets,
        windows=windows,
        entity_window_metrics=entity_window_metrics,
    )
    campaign_rows = build_campaign_rows(targets=targets, ads_rows=ads_rows, windows=windows)
    variant_rows = build_variant_rows(
        targets=targets,
        sales_rows=sales_rows,
        order_rows=order_rows,
        windows=windows,
        strength_rows=strength_rows,
    )
    availability_rows = build_availability_rows(
        targets=targets,
        inventory_rows=inventory_rows,
        warehouse_rows=warehouse_rows,
    )
    product_context_rows = build_product_context_rows(
        targets=targets,
        store_window_index=store_window_index,
    )
    geo_delta_map_rows = geo_store_rows + geo_product_rows
    region_map_absolute_rows, region_map_delta_rows, region_map_contribution_rows = build_geo_map_rows(geo_delta_map_rows)
    external_signal_rows = build_external_signal_rows(targets)
    summary_rows = build_summary_rows(
        targets=targets,
        geo_product_rows=geo_product_rows,
        warehouse_rows=warehouse_rows,
        paid_nonpaid_rows=paid_nonpaid_rows,
        availability_rows=availability_rows,
        variant_rows=variant_rows,
        product_context_rows=product_context_rows,
    )

    write_csv(SUMMARY_CSV, summary_rows)
    write_csv(GEO_STORE_CSV, geo_store_rows)
    write_csv(GEO_PRODUCT_CSV, geo_product_rows)
    write_csv(GEO_DELTA_MAP_CSV, geo_delta_map_rows)
    write_csv(WAREHOUSE_PRODUCT_CSV, warehouse_rows)
    write_csv(PAID_NONPAID_CSV, paid_nonpaid_rows)
    write_csv(CAMPAIGN_DETAIL_CSV, campaign_rows)
    write_csv(AVAILABILITY_CSV, availability_rows)
    write_csv(SKU_VARIANT_CSV, variant_rows)
    write_csv(PRODUCT_CONTEXT_CSV, product_context_rows)
    write_csv(EXTERNAL_SIGNALS_CSV, external_signal_rows)
    write_csv(REGION_MAP_ABSOLUTE_CSV, region_map_absolute_rows)
    write_csv(REGION_MAP_DELTA_CSV, region_map_delta_rows)
    write_csv(REGION_MAP_CONTRIB_CSV, region_map_contribution_rows)

    DOC_PATH.write_text(
        build_report(
            targets=targets,
            summary_rows=summary_rows,
            geo_product_rows=geo_product_rows,
            warehouse_rows=warehouse_rows,
            paid_nonpaid_rows=paid_nonpaid_rows,
            campaign_rows=campaign_rows,
            availability_rows=availability_rows,
            variant_rows=variant_rows,
            product_context_rows=product_context_rows,
        ),
        encoding="utf-8",
    )
    INTERNAL_NOTE_PATH.write_text(build_internal_note(summary_rows), encoding="utf-8")

    print(f"Wrote {SUMMARY_CSV.relative_to(ROOT)}")
    print(f"Wrote {GEO_STORE_CSV.relative_to(ROOT)}")
    print(f"Wrote {GEO_PRODUCT_CSV.relative_to(ROOT)}")
    print(f"Wrote {GEO_DELTA_MAP_CSV.relative_to(ROOT)}")
    print(f"Wrote {WAREHOUSE_PRODUCT_CSV.relative_to(ROOT)}")
    print(f"Wrote {PAID_NONPAID_CSV.relative_to(ROOT)}")
    print(f"Wrote {CAMPAIGN_DETAIL_CSV.relative_to(ROOT)}")
    print(f"Wrote {AVAILABILITY_CSV.relative_to(ROOT)}")
    print(f"Wrote {SKU_VARIANT_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_CONTEXT_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXTERNAL_SIGNALS_CSV.relative_to(ROOT)}")
    print(f"Wrote {REGION_MAP_ABSOLUTE_CSV.relative_to(ROOT)}")
    print(f"Wrote {REGION_MAP_DELTA_CSV.relative_to(ROOT)}")
    print(f"Wrote {REGION_MAP_CONTRIB_CSV.relative_to(ROOT)}")
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}")
    print(f"Wrote {INTERNAL_NOTE_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
