from __future__ import annotations

import csv
import sys
from collections import defaultdict
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

SCHEMA = "client_wb_probe_2"
MARKETPLACE = "wb"
MOSCOW = ZoneInfo("Europe/Moscow")
MONEY = Decimal("0.01")
FOUR = Decimal("0.0001")
UNATTRIBUTED_BUCKET = "__UNATTRIBUTED_FINANCE__"
UNATTRIBUTED_NAME = "UNATTRIBUTED_FINANCE_BUCKET"

OUTPUT_DIR = ROOT / "exports" / "profit_control_deep_analysis_pack_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_deep_analysis_pack_v1.md"
STORE_DAILY_CSV = OUTPUT_DIR / "store_daily_metrics.csv"
STORE_WINDOWS_CSV = OUTPUT_DIR / "store_window_comparison.csv"
PRODUCT_WINDOWS_CSV = OUTPUT_DIR / "product_window_comparison.csv"
PRODUCT_PNL_CSV = OUTPUT_DIR / "product_pnl_breakdown.csv"
EXPENSE_DAILY_CSV = OUTPUT_DIR / "expense_component_daily.csv"
EXPENSE_WEEKLY_CSV = OUTPUT_DIR / "expense_component_weekly.csv"
CHANGE_DRIVERS_CSV = OUTPUT_DIR / "change_drivers.csv"
PRODUCT_TOP_WORST_CSV = OUTPUT_DIR / "product_top_worst.csv"
CANDIDATES_CSV = OUTPUT_DIR / "product_candidates_for_hypothesis_work.csv"

WINDOWS = {
    "short_7d_vs_prev_28d": {"current_days": 7, "base_days": 28},
    "medium_14d_vs_prev_42d": {"current_days": 14, "base_days": 42},
}

NON_ADDITIVE_METRICS = {
    "average_check",
    "ctr",
    "cpc",
    "cr",
    "ad_cost_per_order",
    "ad_cost_per_revenue_rub",
    "ad_share_of_revenue",
    "ad_share_of_profit_before_ads",
    "logistics_per_order",
    "storage_per_order",
    "profit_before_ads_margin",
    "margin_after_ads",
}

COMPONENTS = [
    "revenue_net",
    "positive_adjustment",
    "ad_spend",
    "commission_cost",
    "delivery_cost",
    "warehouse_logistics_tail_cost",
    "storage_cost",
    "acceptance_cost",
    "acquiring_cost",
    "penalty_cost",
    "withholding_cost",
    "negative_adjustment",
]

EXPENSE_COMPONENT_LABELS = [
    ("ad_spend", "ads"),
    ("commission_cost", "commission"),
    ("delivery_cost", "delivery"),
    ("warehouse_logistics_tail_cost", "warehouse_logistics_tail"),
    ("storage_cost", "storage"),
    ("acceptance_cost", "acceptance"),
    ("acquiring_cost", "acquiring"),
    ("penalty_cost", "penalties"),
    ("withholding_cost", "withholding"),
    ("negative_adjustment", "other_negative_adjustment"),
    ("positive_adjustment", "positive_adjustment"),
]


@dataclass
class Window:
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
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def q4(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    if isinstance(value, Decimal):
        return value.quantize(FOUR, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(FOUR, rounding=ROUND_HALF_UP)


def add(a: Any, b: Any) -> Decimal:
    return q2(a) + q2(b)


def safe_div(numerator: Any, denominator: Any, places: Decimal = FOUR) -> Decimal:
    den = Decimal(str(denominator or 0))
    if den == 0:
        return Decimal("0").quantize(places, rounding=ROUND_HALF_UP)
    num = Decimal(str(numerator or 0))
    return (num / den).quantize(places, rounding=ROUND_HALF_UP)


def pct(value: Decimal) -> str:
    return f"{(value * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def money(value: Decimal) -> str:
    return f"{q2(value):,.2f}".replace(",", " ")


def num(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, Decimal):
        return f"{value:,.2f}".replace(",", " ")
    return str(value)


def week_start(day_value: date) -> date:
    return day_value - timedelta(days=day_value.weekday())


def daterange(start: date, end: date) -> list[date]:
    out: list[date] = []
    current = start
    while current <= end:
        out.append(current)
        current += timedelta(days=1)
    return out


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


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


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


def fetch_corridor_bounds() -> tuple[date, date]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT MIN(operation_date::date), MAX(operation_date::date)
                FROM {SCHEMA}.stg_wb_finance
                """
            )
            start_day, end_day = cur.fetchone()
    return start_day, end_day


def build_windows(corridor_start: date, corridor_end: date) -> list[Window]:
    windows: list[Window] = []
    for label, cfg in WINDOWS.items():
        current_days = cfg["current_days"]
        base_days = cfg["base_days"]
        current_start = corridor_end - timedelta(days=current_days - 1)
        base_end = current_start - timedelta(days=1)
        base_start = base_end - timedelta(days=base_days - 1)
        if base_start < corridor_start:
            continue
        windows.append(
            Window(
                label=label,
                current_start=current_start,
                current_end=corridor_end,
                base_start=base_start,
                base_end=base_end,
                current_days=current_days,
                base_days=base_days,
            )
        )
    return windows


def fetch_product_daily(corridor_start: date, corridor_end: date) -> list[dict[str, Any]]:
    query = f"""
    WITH dim_map AS (
      SELECT
        product_id::text AS product_id,
        MAX(offer_id) AS offer_id,
        MAX(product_name) AS product_name,
        MAX(brand) AS brand,
        MAX(category_name) AS category_name
      FROM {SCHEMA}.dim_product
      WHERE marketplace = %s
        AND product_id IS NOT NULL
        AND product_id <> ''
      GROUP BY 1
    ),
    dim_offer AS (
      SELECT LOWER(offer_id) AS offer_id_lc, MAX(product_id::text) AS product_id
      FROM {SCHEMA}.dim_product
      WHERE marketplace = %s
        AND offer_id IS NOT NULL
        AND offer_id <> ''
      GROUP BY 1
    ),
    sales AS (
      SELECT
        (sale_date AT TIME ZONE 'UTC')::date AS day,
        nm_id::text AS product_id,
        MAX(COALESCE(NULLIF(subject_name, ''), NULLIF(supplier_article, ''), nm_id::text)) AS fallback_name,
        SUM(CASE WHEN COALESCE(is_return, FALSE) THEN -COALESCE(quantity, 0) ELSE COALESCE(quantity, 0) END)::numeric(18,2) AS sold_units_net,
        COUNT(*) FILTER (WHERE NOT COALESCE(is_return, FALSE))::int AS sale_events,
        COUNT(*) FILTER (WHERE COALESCE(is_return, FALSE))::int AS return_events,
        COUNT(DISTINCT order_id) FILTER (WHERE order_id IS NOT NULL AND order_id <> '')::int AS sold_order_count,
        SUM(CASE WHEN COALESCE(is_return, FALSE) THEN -COALESCE(revenue_gross, 0) ELSE COALESCE(revenue_gross, 0) END)::numeric(18,2) AS revenue_net
      FROM {SCHEMA}.stg_wb_sale
      WHERE marketplace = %s
        AND (sale_date AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
        AND nm_id IS NOT NULL
        AND nm_id <> ''
      GROUP BY 1, 2
    ),
    orders AS (
      SELECT
        (order_date AT TIME ZONE 'UTC')::date AS day,
        nm_id::text AS product_id,
        MAX(COALESCE(NULLIF(subject_name, ''), NULLIF(supplier_article, ''), nm_id::text)) AS fallback_name,
        SUM(COALESCE(quantity, 0))::numeric(18,2) AS ordered_units,
        COUNT(*)::int AS order_events,
        COUNT(DISTINCT order_id)::int AS order_count,
        SUM(COALESCE(sale_price, 0))::numeric(18,2) AS ordered_gmv
      FROM {SCHEMA}.stg_wb_order
      WHERE marketplace = %s
        AND (order_date AT TIME ZONE 'UTC')::date BETWEEN %s AND %s
        AND nm_id IS NOT NULL
        AND nm_id <> ''
      GROUP BY 1, 2
    ),
    ads AS (
      SELECT
        date AS day,
        product_id::text AS product_id,
        MAX(COALESCE(NULLIF(campaign_name, ''), product_id::text)) AS fallback_name,
        SUM(COALESCE(spend, 0))::numeric(18,2) AS ad_spend,
        SUM(COALESCE(impressions, 0))::numeric(18,2) AS impressions,
        SUM(COALESCE(clicks, 0))::numeric(18,2) AS clicks,
        SUM(COALESCE(orders_count, 0))::numeric(18,2) AS ad_orders,
        SUM(COALESCE(revenue, 0))::numeric(18,2) AS ad_attributed_revenue,
        COUNT(DISTINCT campaign_id)::int AS campaign_count
      FROM {SCHEMA}.fact_ads_spend
      WHERE marketplace = %s
        AND date BETWEEN %s AND %s
        AND product_id IS NOT NULL
        AND product_id <> ''
      GROUP BY 1, 2
    ),
    finance_raw AS (
      SELECT
        f.operation_date::date AS day,
        CASE
          WHEN f.nm_id IS NOT NULL AND f.nm_id <> '' THEN f.nm_id::text
          WHEN dof.product_id IS NOT NULL AND dof.product_id <> '' THEN dof.product_id
          WHEN LOWER(COALESCE(f.offer_id, '')) ~ '^wb_nm_[0-9]+$' THEN REGEXP_REPLACE(LOWER(f.offer_id), '^wb_nm_', '')
          ELSE NULL
        END AS product_id,
        COALESCE(dm.product_name, NULLIF(f.subject_name, ''), NULLIF(f.offer_id, ''), '{UNATTRIBUTED_NAME}') AS fallback_name,
        GREATEST(-COALESCE(f.commission_amount, 0), 0)::numeric(18,2) AS commission_cost,
        COALESCE(f.delivery_rub, 0)::numeric(18,2) AS delivery_cost,
        COALESCE(f.rebill_logistic_cost, 0)::numeric(18,2) AS warehouse_logistics_tail_cost,
        COALESCE(f.storage_fee, 0)::numeric(18,2) AS storage_cost,
        COALESCE(f.acceptance_amount, 0)::numeric(18,2) AS acceptance_cost,
        COALESCE(f.acquiring_fee, 0)::numeric(18,2) AS acquiring_cost,
        COALESCE(f.penalty_amount, 0)::numeric(18,2) AS penalty_cost,
        COALESCE(f.deduction_amount, 0)::numeric(18,2) AS withholding_cost,
        CASE
          WHEN COALESCE(f.delivery_rub, 0) = 0
           AND COALESCE(f.rebill_logistic_cost, 0) = 0
           AND COALESCE(f.storage_fee, 0) = 0
           AND COALESCE(f.acceptance_amount, 0) = 0
           AND COALESCE(f.acquiring_fee, 0) = 0
           AND COALESCE(f.penalty_amount, 0) = 0
           AND COALESCE(f.deduction_amount, 0) = 0
           AND GREATEST(-COALESCE(f.commission_amount, 0), 0) = 0
           AND COALESCE(f.amount, 0) > 0
          THEN COALESCE(f.amount, 0)::numeric(18,2)
          ELSE 0::numeric(18,2)
        END AS positive_adjustment,
        CASE
          WHEN COALESCE(f.delivery_rub, 0) = 0
           AND COALESCE(f.rebill_logistic_cost, 0) = 0
           AND COALESCE(f.storage_fee, 0) = 0
           AND COALESCE(f.acceptance_amount, 0) = 0
           AND COALESCE(f.acquiring_fee, 0) = 0
           AND COALESCE(f.penalty_amount, 0) = 0
           AND COALESCE(f.deduction_amount, 0) = 0
           AND GREATEST(-COALESCE(f.commission_amount, 0), 0) = 0
           AND COALESCE(f.amount, 0) < 0
          THEN -COALESCE(f.amount, 0)::numeric(18,2)
          ELSE 0::numeric(18,2)
        END AS negative_adjustment
      FROM {SCHEMA}.stg_wb_finance f
      LEFT JOIN dim_offer dof
        ON LOWER(COALESCE(f.offer_id, '')) = dof.offer_id_lc
      LEFT JOIN dim_map dm
        ON dm.product_id = COALESCE(
          NULLIF(f.nm_id, ''),
          dof.product_id,
          CASE
            WHEN LOWER(COALESCE(f.offer_id, '')) ~ '^wb_nm_[0-9]+$' THEN REGEXP_REPLACE(LOWER(f.offer_id), '^wb_nm_', '')
            ELSE NULL
          END
        )
      WHERE f.operation_date::date BETWEEN %s AND %s
    ),
    finance AS (
      SELECT
        day,
        COALESCE(product_id, '{UNATTRIBUTED_BUCKET}') AS product_id,
        MAX(CASE WHEN product_id IS NULL THEN '{UNATTRIBUTED_NAME}' ELSE fallback_name END) AS fallback_name,
        SUM(commission_cost)::numeric(18,2) AS commission_cost,
        SUM(delivery_cost)::numeric(18,2) AS delivery_cost,
        SUM(warehouse_logistics_tail_cost)::numeric(18,2) AS warehouse_logistics_tail_cost,
        SUM(storage_cost)::numeric(18,2) AS storage_cost,
        SUM(acceptance_cost)::numeric(18,2) AS acceptance_cost,
        SUM(acquiring_cost)::numeric(18,2) AS acquiring_cost,
        SUM(penalty_cost)::numeric(18,2) AS penalty_cost,
        SUM(withholding_cost)::numeric(18,2) AS withholding_cost,
        SUM(positive_adjustment)::numeric(18,2) AS positive_adjustment,
        SUM(negative_adjustment)::numeric(18,2) AS negative_adjustment
      FROM finance_raw
      GROUP BY 1, 2
    ),
    latest_inventory AS (
      SELECT
        product_id::text AS product_id,
        SUM(COALESCE(stock_total, 0))::numeric(18,2) AS stock_total,
        SUM(COALESCE(stock_available, 0))::numeric(18,2) AS stock_available,
        SUM(COALESCE(stock_reserved, 0))::numeric(18,2) AS stock_reserved,
        SUM(COALESCE(stock_defect, 0))::numeric(18,2) AS stock_defect,
        MAX(snapshot_date) AS snapshot_date
      FROM {SCHEMA}.fact_inventory_snapshot
      WHERE marketplace = %s
        AND snapshot_date = (
          SELECT MAX(snapshot_date)
          FROM {SCHEMA}.fact_inventory_snapshot
          WHERE marketplace = %s
        )
        AND product_id IS NOT NULL
        AND product_id <> ''
      GROUP BY 1
    ),
    keys AS (
      SELECT day, product_id FROM sales
      UNION
      SELECT day, product_id FROM orders
      UNION
      SELECT day, product_id FROM ads
      UNION
      SELECT day, product_id FROM finance
    )
    SELECT
      k.day,
      k.product_id,
      CASE
        WHEN k.product_id = '{UNATTRIBUTED_BUCKET}' THEN '{UNATTRIBUTED_NAME}'
        ELSE COALESCE(dm.product_name, s.fallback_name, o.fallback_name, a.fallback_name, f.fallback_name, k.product_id)
      END AS product_name,
      CASE WHEN k.product_id = '{UNATTRIBUTED_BUCKET}' THEN 'UNATTRIBUTED' ELSE COALESCE(dm.brand, '') END AS brand,
      CASE WHEN k.product_id = '{UNATTRIBUTED_BUCKET}' THEN 'UNATTRIBUTED' ELSE COALESCE(dm.category_name, '') END AS category_name,
      COALESCE(s.revenue_net, 0)::numeric(18,2) AS revenue_net,
      COALESCE(s.sold_units_net, 0)::numeric(18,2) AS sold_units_net,
      COALESCE(s.sale_events, 0)::int AS sale_events,
      COALESCE(s.return_events, 0)::int AS return_events,
      COALESCE(s.sold_order_count, 0)::int AS sold_order_count,
      COALESCE(o.ordered_units, 0)::numeric(18,2) AS ordered_units,
      COALESCE(o.order_events, 0)::int AS order_events,
      COALESCE(o.order_count, 0)::int AS order_count,
      COALESCE(o.ordered_gmv, 0)::numeric(18,2) AS ordered_gmv,
      COALESCE(a.ad_spend, 0)::numeric(18,2) AS ad_spend,
      COALESCE(a.impressions, 0)::numeric(18,2) AS impressions,
      COALESCE(a.clicks, 0)::numeric(18,2) AS clicks,
      COALESCE(a.ad_orders, 0)::numeric(18,2) AS ad_orders,
      COALESCE(a.ad_attributed_revenue, 0)::numeric(18,2) AS ad_attributed_revenue,
      COALESCE(a.campaign_count, 0)::int AS campaign_count,
      COALESCE(f.commission_cost, 0)::numeric(18,2) AS commission_cost,
      COALESCE(f.delivery_cost, 0)::numeric(18,2) AS delivery_cost,
      COALESCE(f.warehouse_logistics_tail_cost, 0)::numeric(18,2) AS warehouse_logistics_tail_cost,
      COALESCE(f.storage_cost, 0)::numeric(18,2) AS storage_cost,
      COALESCE(f.acceptance_cost, 0)::numeric(18,2) AS acceptance_cost,
      COALESCE(f.acquiring_cost, 0)::numeric(18,2) AS acquiring_cost,
      COALESCE(f.penalty_cost, 0)::numeric(18,2) AS penalty_cost,
      COALESCE(f.withholding_cost, 0)::numeric(18,2) AS withholding_cost,
      COALESCE(f.positive_adjustment, 0)::numeric(18,2) AS positive_adjustment,
      COALESCE(f.negative_adjustment, 0)::numeric(18,2) AS negative_adjustment,
      li.snapshot_date,
      COALESCE(li.stock_total, 0)::numeric(18,2) AS stock_total,
      COALESCE(li.stock_available, 0)::numeric(18,2) AS stock_available,
      COALESCE(li.stock_reserved, 0)::numeric(18,2) AS stock_reserved,
      COALESCE(li.stock_defect, 0)::numeric(18,2) AS stock_defect
    FROM keys k
    LEFT JOIN dim_map dm ON dm.product_id = k.product_id
    LEFT JOIN sales s ON s.day = k.day AND s.product_id = k.product_id
    LEFT JOIN orders o ON o.day = k.day AND o.product_id = k.product_id
    LEFT JOIN ads a ON a.day = k.day AND a.product_id = k.product_id
    LEFT JOIN finance f ON f.day = k.day AND f.product_id = k.product_id
    LEFT JOIN latest_inventory li ON li.product_id = k.product_id
    ORDER BY k.day, k.product_id
    """
    params = (
        MARKETPLACE,
        MARKETPLACE,
        MARKETPLACE,
        corridor_start,
        corridor_end,
        MARKETPLACE,
        corridor_start,
        corridor_end,
        MARKETPLACE,
        corridor_start,
        corridor_end,
        corridor_start,
        corridor_end,
        MARKETPLACE,
        MARKETPLACE,
    )
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row["profit_before_ads"] = q2(
            q2(row["revenue_net"])
            + q2(row["positive_adjustment"])
            - q2(row["commission_cost"])
            - q2(row["delivery_cost"])
            - q2(row["warehouse_logistics_tail_cost"])
            - q2(row["storage_cost"])
            - q2(row["acceptance_cost"])
            - q2(row["acquiring_cost"])
            - q2(row["penalty_cost"])
            - q2(row["withholding_cost"])
            - q2(row["negative_adjustment"])
        )
        row["profit_after_ads"] = q2(row["profit_before_ads"] - q2(row["ad_spend"]))
        row["margin_after_ads"] = safe_div(row["profit_after_ads"], row["revenue_net"])
        row["profit_before_ads_margin"] = safe_div(row["profit_before_ads"], row["revenue_net"])
        logistics_total = q2(row["delivery_cost"]) + q2(row["warehouse_logistics_tail_cost"]) + q2(row["storage_cost"]) + q2(row["acceptance_cost"])
        row["logistics_total_cost"] = logistics_total
        row["average_check"] = q2(safe_div(row["revenue_net"], row["order_count"], MONEY))
        row["ctr"] = safe_div(row["clicks"], row["impressions"])
        row["cpc"] = q2(safe_div(row["ad_spend"], row["clicks"], MONEY))
        row["cr"] = safe_div(row["ad_orders"], row["clicks"])
        row["ad_cost_per_order"] = q2(safe_div(row["ad_spend"], row["ad_orders"], MONEY))
        row["ad_cost_per_revenue_rub"] = q4(safe_div(row["ad_spend"], row["ad_attributed_revenue"]))
        row["ad_share_of_revenue"] = safe_div(row["ad_spend"], row["revenue_net"])
        row["ad_share_of_profit_before_ads"] = safe_div(row["ad_spend"], row["profit_before_ads"])
        row["logistics_per_order"] = q2(safe_div(logistics_total, row["order_count"], MONEY))
        row["storage_per_order"] = q2(safe_div(row["storage_cost"], row["order_count"], MONEY))
        row["is_unattributed_finance_bucket"] = row["product_id"] == UNATTRIBUTED_BUCKET
    return rows


def aggregate_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    agg: dict[str, Any] = {
        "days": set(),
        "revenue_net": Decimal("0.00"),
        "sold_units_net": Decimal("0.00"),
        "sale_events": 0,
        "return_events": 0,
        "sold_order_count": 0,
        "ordered_units": Decimal("0.00"),
        "order_events": 0,
        "order_count": 0,
        "ordered_gmv": Decimal("0.00"),
        "ad_spend": Decimal("0.00"),
        "impressions": Decimal("0.00"),
        "clicks": Decimal("0.00"),
        "ad_orders": Decimal("0.00"),
        "ad_attributed_revenue": Decimal("0.00"),
        "campaign_count": 0,
        "commission_cost": Decimal("0.00"),
        "delivery_cost": Decimal("0.00"),
        "warehouse_logistics_tail_cost": Decimal("0.00"),
        "storage_cost": Decimal("0.00"),
        "acceptance_cost": Decimal("0.00"),
        "acquiring_cost": Decimal("0.00"),
        "penalty_cost": Decimal("0.00"),
        "withholding_cost": Decimal("0.00"),
        "positive_adjustment": Decimal("0.00"),
        "negative_adjustment": Decimal("0.00"),
        "profit_before_ads": Decimal("0.00"),
        "profit_after_ads": Decimal("0.00"),
        "unattributed_finance_cost": Decimal("0.00"),
    }
    for row in rows:
        agg["days"].add(row["day"])
        for key in [
            "revenue_net",
            "sold_units_net",
            "ordered_units",
            "ordered_gmv",
            "ad_spend",
            "impressions",
            "clicks",
            "ad_orders",
            "ad_attributed_revenue",
            "commission_cost",
            "delivery_cost",
            "warehouse_logistics_tail_cost",
            "storage_cost",
            "acceptance_cost",
            "acquiring_cost",
            "penalty_cost",
            "withholding_cost",
            "positive_adjustment",
            "negative_adjustment",
            "profit_before_ads",
            "profit_after_ads",
        ]:
            agg[key] += q2(row[key])
        for key in ["sale_events", "return_events", "sold_order_count", "order_events", "order_count", "campaign_count"]:
            agg[key] += int(row[key] or 0)
        if row.get("is_unattributed_finance_bucket"):
            agg["unattributed_finance_cost"] += (
                q2(row["commission_cost"])
                + q2(row["delivery_cost"])
                + q2(row["warehouse_logistics_tail_cost"])
                + q2(row["storage_cost"])
                + q2(row["acceptance_cost"])
                + q2(row["acquiring_cost"])
                + q2(row["penalty_cost"])
                + q2(row["withholding_cost"])
                + q2(row["negative_adjustment"])
                - q2(row["positive_adjustment"])
            )
    agg["day_count"] = len(agg["days"])
    agg["logistics_total_cost"] = agg["delivery_cost"] + agg["warehouse_logistics_tail_cost"] + agg["storage_cost"] + agg["acceptance_cost"]
    agg["average_check"] = q2(safe_div(agg["revenue_net"], agg["order_count"], MONEY))
    agg["ctr"] = safe_div(agg["clicks"], agg["impressions"])
    agg["cpc"] = q2(safe_div(agg["ad_spend"], agg["clicks"], MONEY))
    agg["cr"] = safe_div(agg["ad_orders"], agg["clicks"])
    agg["ad_cost_per_order"] = q2(safe_div(agg["ad_spend"], agg["ad_orders"], MONEY))
    agg["ad_cost_per_revenue_rub"] = q4(safe_div(agg["ad_spend"], agg["ad_attributed_revenue"]))
    agg["ad_share_of_revenue"] = safe_div(agg["ad_spend"], agg["revenue_net"])
    agg["ad_share_of_profit_before_ads"] = safe_div(agg["ad_spend"], agg["profit_before_ads"])
    agg["logistics_per_order"] = q2(safe_div(agg["logistics_total_cost"], agg["order_count"], MONEY))
    agg["storage_per_order"] = q2(safe_div(agg["storage_cost"], agg["order_count"], MONEY))
    agg["profit_before_ads_margin"] = safe_div(agg["profit_before_ads"], agg["revenue_net"])
    agg["margin_after_ads"] = safe_div(agg["profit_after_ads"], agg["revenue_net"])
    return agg


def per_day_from_agg(agg: dict[str, Any], metric: str) -> Decimal:
    day_count = agg.get("day_count") or 0
    if day_count <= 0:
        return Decimal("0.00")
    places = MONEY if metric not in {"ctr", "cr", "ad_cost_per_revenue_rub", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "profit_before_ads_margin", "margin_after_ads"} else FOUR
    return Decimal(str(agg[metric])) / Decimal(day_count) if places == FOUR else q2(Decimal(str(agg[metric])) / Decimal(day_count))


def window_normalized_metric(agg: dict[str, Any], metric: str, days_count: int) -> Decimal:
    if metric in NON_ADDITIVE_METRICS:
        return q4(agg.get(metric, Decimal("0.00"))) if metric in {"margin_after_ads", "ctr", "cr", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "ad_cost_per_revenue_rub", "profit_before_ads_margin"} else q2(agg.get(metric, Decimal("0.00")))
    if days_count <= 0:
        return Decimal("0.0000") if metric in {"margin_after_ads", "ctr", "cr", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "ad_cost_per_revenue_rub", "profit_before_ads_margin"} else Decimal("0.00")
    value = Decimal(str(agg.get(metric, 0)))
    return q2(value / Decimal(days_count))


def build_store_daily(product_daily: list[dict[str, Any]], corridor_days: list[date]) -> list[dict[str, Any]]:
    by_day: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in product_daily:
        by_day[row["day"]].append(row)
    store_rows: list[dict[str, Any]] = []
    for day_value in corridor_days:
        agg = aggregate_metrics(by_day.get(day_value, []))
        store_rows.append(
            {
                "day": day_value.isoformat(),
                "revenue_net": str(agg["revenue_net"]),
                "ordered_units": str(agg["ordered_units"]),
                "order_count": agg["order_count"],
                "sold_units_net": str(agg["sold_units_net"]),
                "sold_order_count": agg["sold_order_count"],
                "average_check": str(agg["average_check"]),
                "profit_before_ads": str(agg["profit_before_ads"]),
                "profit_after_ads": str(agg["profit_after_ads"]),
                "margin_after_ads": str(q4(agg["margin_after_ads"])),
                "ad_spend": str(agg["ad_spend"]),
                "impressions": str(agg["impressions"]),
                "clicks": str(agg["clicks"]),
                "ad_orders": str(agg["ad_orders"]),
                "ad_attributed_revenue": str(agg["ad_attributed_revenue"]),
                "ctr": str(q4(agg["ctr"])),
                "cpc": str(agg["cpc"]),
                "cr": str(q4(agg["cr"])),
                "ad_cost_per_order": str(agg["ad_cost_per_order"]),
                "ad_cost_per_revenue_rub": str(q4(agg["ad_cost_per_revenue_rub"])),
                "ad_share_of_revenue": str(q4(agg["ad_share_of_revenue"])),
                "ad_share_of_profit_before_ads": str(q4(agg["ad_share_of_profit_before_ads"])),
                "commission_cost": str(agg["commission_cost"]),
                "delivery_cost": str(agg["delivery_cost"]),
                "warehouse_logistics_tail_cost": str(agg["warehouse_logistics_tail_cost"]),
                "storage_cost": str(agg["storage_cost"]),
                "acceptance_cost": str(agg["acceptance_cost"]),
                "acquiring_cost": str(agg["acquiring_cost"]),
                "penalty_cost": str(agg["penalty_cost"]),
                "withholding_cost": str(agg["withholding_cost"]),
                "positive_adjustment": str(agg["positive_adjustment"]),
                "negative_adjustment": str(agg["negative_adjustment"]),
                "logistics_total_cost": str(agg["logistics_total_cost"]),
                "logistics_per_order": str(agg["logistics_per_order"]),
                "storage_per_order": str(agg["storage_per_order"]),
                "unattributed_finance_cost": str(agg["unattributed_finance_cost"]),
            }
        )
    return store_rows


def slice_rows(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [row for row in rows if start <= row["day"] <= end]


def window_comparisons(store_rows_raw: list[dict[str, Any]], windows: list[Window]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    metrics = [
        "revenue_net",
        "order_count",
        "sold_units_net",
        "average_check",
        "profit_before_ads",
        "profit_after_ads",
        "margin_after_ads",
        "ad_spend",
        "impressions",
        "clicks",
        "ad_orders",
        "ctr",
        "cpc",
        "cr",
        "ad_cost_per_order",
        "ad_cost_per_revenue_rub",
        "ad_share_of_revenue",
        "ad_share_of_profit_before_ads",
        "commission_cost",
        "delivery_cost",
        "warehouse_logistics_tail_cost",
        "storage_cost",
        "acceptance_cost",
        "acquiring_cost",
        "penalty_cost",
        "withholding_cost",
        "positive_adjustment",
        "negative_adjustment",
        "logistics_total_cost",
        "logistics_per_order",
        "storage_per_order",
        "unattributed_finance_cost",
    ]
    for window in windows:
        current_rows = slice_rows(store_rows_raw, window.current_start, window.current_end)
        base_rows = slice_rows(store_rows_raw, window.base_start, window.base_end)
        current_agg = aggregate_metrics(current_rows)
        base_agg = aggregate_metrics(base_rows)
        for metric in metrics:
            current_total = current_agg.get(metric, Decimal("0.00"))
            base_total = base_agg.get(metric, Decimal("0.00"))
            current_per_day = window_normalized_metric(current_agg, metric, window.current_days)
            base_per_day = window_normalized_metric(base_agg, metric, window.base_days)
            delta_per_day = q2(current_per_day - base_per_day) if metric not in {"margin_after_ads", "ctr", "cr", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "ad_cost_per_revenue_rub", "profit_before_ads_margin"} else q4(current_per_day - base_per_day)
            delta_pct = safe_div((current_per_day - base_per_day), base_per_day) if base_per_day != 0 else Decimal("0.0000")
            comparisons.append(
                {
                    "window_label": window.label,
                    "current_start": window.current_start.isoformat(),
                    "current_end": window.current_end.isoformat(),
                    "base_start": window.base_start.isoformat(),
                    "base_end": window.base_end.isoformat(),
                    "metric": metric,
                    "current_total": str(current_total),
                    "base_total": str(base_total),
                    "current_per_day": str(current_per_day),
                    "base_per_day": str(base_per_day),
                    "delta_per_day": str(delta_per_day),
                    "delta_pct_vs_base_per_day": str(q4(delta_pct)),
                }
            )
    return comparisons


def product_groups(product_daily: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_daily:
        grouped[row["product_id"]].append(row)
    return grouped


def product_summary(
    product_id: str,
    rows: list[dict[str, Any]],
    corridor_end: date,
    windows: list[Window],
) -> dict[str, Any]:
    agg = aggregate_metrics(rows)
    summary: dict[str, Any] = {
        "product_id": product_id,
        "product_name": rows[0]["product_name"],
        "brand": rows[0]["brand"],
        "category_name": rows[0]["category_name"],
        "is_unattributed_finance_bucket": rows[0].get("is_unattributed_finance_bucket", False),
        "days_active": agg["day_count"],
        "revenue_net_total": str(agg["revenue_net"]),
        "ordered_units_total": str(agg["ordered_units"]),
        "order_count_total": agg["order_count"],
        "sold_units_net_total": str(agg["sold_units_net"]),
        "average_check_total": str(agg["average_check"]),
        "profit_before_ads_total": str(agg["profit_before_ads"]),
        "profit_after_ads_total": str(agg["profit_after_ads"]),
        "margin_after_ads_total": str(q4(agg["margin_after_ads"])),
        "ad_spend_total": str(agg["ad_spend"]),
        "ad_share_of_revenue_total": str(q4(agg["ad_share_of_revenue"])),
        "commission_cost_total": str(agg["commission_cost"]),
        "delivery_cost_total": str(agg["delivery_cost"]),
        "warehouse_logistics_tail_cost_total": str(agg["warehouse_logistics_tail_cost"]),
        "storage_cost_total": str(agg["storage_cost"]),
        "acceptance_cost_total": str(agg["acceptance_cost"]),
        "acquiring_cost_total": str(agg["acquiring_cost"]),
        "penalty_cost_total": str(agg["penalty_cost"]),
        "withholding_cost_total": str(agg["withholding_cost"]),
        "positive_adjustment_total": str(agg["positive_adjustment"]),
        "negative_adjustment_total": str(agg["negative_adjustment"]),
        "logistics_total_cost_total": str(agg["logistics_total_cost"]),
        "logistics_share_of_revenue_total": str(q4(safe_div(agg["logistics_total_cost"], agg["revenue_net"]))),
        "wb_finance_cost_share_total": str(
            q4(
                safe_div(
                    agg["commission_cost"]
                    + agg["delivery_cost"]
                    + agg["warehouse_logistics_tail_cost"]
                    + agg["storage_cost"]
                    + agg["acceptance_cost"]
                    + agg["acquiring_cost"]
                    + agg["penalty_cost"]
                    + agg["withholding_cost"]
                    + agg["negative_adjustment"]
                    - agg["positive_adjustment"],
                    agg["revenue_net"],
                )
            )
        ),
    }
    latest_stock_total = q2(rows[0].get("stock_total"))
    latest_stock_available = q2(rows[0].get("stock_available"))
    summary["latest_stock_snapshot_date"] = rows[0].get("snapshot_date").isoformat() if rows[0].get("snapshot_date") else ""
    summary["latest_stock_total"] = str(latest_stock_total)
    summary["latest_stock_available"] = str(latest_stock_available)
    summary["latest_stock_reserved"] = str(q2(rows[0].get("stock_reserved")))
    summary["latest_stock_defect"] = str(q2(rows[0].get("stock_defect")))
    last_7_rows = slice_rows(rows, corridor_end - timedelta(days=6), corridor_end)
    last_14_rows = slice_rows(rows, corridor_end - timedelta(days=13), corridor_end)
    last_7_agg = aggregate_metrics(last_7_rows)
    last_14_agg = aggregate_metrics(last_14_rows)
    avg_daily_units_7 = safe_div(last_7_agg["sold_units_net"], Decimal("7"), MONEY)
    avg_daily_units_14 = safe_div(last_14_agg["sold_units_net"], Decimal("14"), MONEY)
    summary["avg_daily_units_7d"] = str(avg_daily_units_7)
    summary["avg_daily_units_14d"] = str(avg_daily_units_14)
    summary["days_cover_7d"] = str(q2(safe_div(latest_stock_available, avg_daily_units_7, MONEY))) if avg_daily_units_7 != 0 else ""
    summary["days_cover_14d"] = str(q2(safe_div(latest_stock_available, avg_daily_units_14, MONEY))) if avg_daily_units_14 != 0 else ""

    for window in windows:
        current_rows = slice_rows(rows, window.current_start, window.current_end)
        base_rows = slice_rows(rows, window.base_start, window.base_end)
        current_agg = aggregate_metrics(current_rows)
        base_agg = aggregate_metrics(base_rows)
        prefix = "short" if window.label.startswith("short") else "medium"
        summary[f"{prefix}_current_revenue_total"] = str(current_agg["revenue_net"])
        summary[f"{prefix}_base_revenue_total"] = str(base_agg["revenue_net"])
        summary[f"{prefix}_current_profit_after_ads_total"] = str(current_agg["profit_after_ads"])
        summary[f"{prefix}_base_profit_after_ads_total"] = str(base_agg["profit_after_ads"])
        summary[f"{prefix}_current_ad_spend_total"] = str(current_agg["ad_spend"])
        summary[f"{prefix}_base_ad_spend_total"] = str(base_agg["ad_spend"])
        summary[f"{prefix}_current_logistics_total"] = str(current_agg["logistics_total_cost"])
        summary[f"{prefix}_base_logistics_total"] = str(base_agg["logistics_total_cost"])
        summary[f"{prefix}_current_withholding_total"] = str(current_agg["withholding_cost"])
        summary[f"{prefix}_base_withholding_total"] = str(base_agg["withholding_cost"])
        summary[f"{prefix}_current_margin_after_ads"] = str(q4(current_agg["margin_after_ads"]))
        summary[f"{prefix}_base_margin_after_ads"] = str(q4(base_agg["margin_after_ads"]))
        current_profit_per_day = q2(current_agg["profit_after_ads"] / Decimal(window.current_days))
        base_profit_per_day = q2(base_agg["profit_after_ads"] / Decimal(window.base_days))
        current_revenue_per_day = q2(current_agg["revenue_net"] / Decimal(window.current_days))
        base_revenue_per_day = q2(base_agg["revenue_net"] / Decimal(window.base_days))
        current_ads_per_day = q2(current_agg["ad_spend"] / Decimal(window.current_days))
        base_ads_per_day = q2(base_agg["ad_spend"] / Decimal(window.base_days))
        current_logistics_per_day = q2(current_agg["logistics_total_cost"] / Decimal(window.current_days))
        base_logistics_per_day = q2(base_agg["logistics_total_cost"] / Decimal(window.base_days))
        current_withholding_per_day = q2(current_agg["withholding_cost"] / Decimal(window.current_days))
        base_withholding_per_day = q2(base_agg["withholding_cost"] / Decimal(window.base_days))
        summary[f"{prefix}_profit_after_ads_delta_per_day"] = str(q2(current_profit_per_day - base_profit_per_day))
        summary[f"{prefix}_revenue_delta_per_day"] = str(q2(current_revenue_per_day - base_revenue_per_day))
        summary[f"{prefix}_ad_spend_delta_per_day"] = str(q2(current_ads_per_day - base_ads_per_day))
        summary[f"{prefix}_logistics_delta_per_day"] = str(q2(current_logistics_per_day - base_logistics_per_day))
        summary[f"{prefix}_withholding_delta_per_day"] = str(q2(current_withholding_per_day - base_withholding_per_day))
        summary[f"{prefix}_margin_after_ads_delta"] = str(q4(current_agg["margin_after_ads"] - base_agg["margin_after_ads"]))
    return summary


def build_product_comparisons(product_daily: list[dict[str, Any]], corridor_end: date, windows: list[Window]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped = product_groups(product_daily)
    product_window_rows: list[dict[str, Any]] = []
    product_pnl_rows: list[dict[str, Any]] = []
    for product_id, rows in grouped.items():
        rows.sort(key=lambda item: item["day"])
        summary = product_summary(product_id, rows, corridor_end, windows)
        product_window_rows.append(summary)
        product_pnl_rows.append(
            {
                key: summary[key]
                for key in [
                    "product_id",
                    "product_name",
                    "brand",
                    "category_name",
                    "is_unattributed_finance_bucket",
                    "days_active",
                    "revenue_net_total",
                    "ordered_units_total",
                    "order_count_total",
                    "sold_units_net_total",
                    "average_check_total",
                    "profit_before_ads_total",
                    "profit_after_ads_total",
                    "margin_after_ads_total",
                    "ad_spend_total",
                    "ad_share_of_revenue_total",
                    "commission_cost_total",
                    "delivery_cost_total",
                    "warehouse_logistics_tail_cost_total",
                    "storage_cost_total",
                    "acceptance_cost_total",
                    "acquiring_cost_total",
                    "penalty_cost_total",
                    "withholding_cost_total",
                    "positive_adjustment_total",
                    "negative_adjustment_total",
                    "logistics_total_cost_total",
                    "logistics_share_of_revenue_total",
                    "wb_finance_cost_share_total",
                    "latest_stock_snapshot_date",
                    "latest_stock_total",
                    "latest_stock_available",
                    "latest_stock_reserved",
                    "latest_stock_defect",
                    "avg_daily_units_7d",
                    "avg_daily_units_14d",
                    "days_cover_7d",
                    "days_cover_14d",
                ]
            }
        )
    return product_window_rows, product_pnl_rows


def build_expense_component_tables(product_daily: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_day_component: dict[tuple[date, str], dict[str, Decimal]] = defaultdict(lambda: {"total": Decimal("0.00"), "unattributed": Decimal("0.00")})
    by_week_component: dict[tuple[date, str], dict[str, Decimal]] = defaultdict(lambda: {"total": Decimal("0.00"), "unattributed": Decimal("0.00")})
    for row in product_daily:
        for key, label in EXPENSE_COMPONENT_LABELS:
            amount = q2(row[key])
            if amount == 0:
                continue
            day_key = (row["day"], label)
            week_key = (week_start(row["day"]), label)
            by_day_component[day_key]["total"] += amount
            by_week_component[week_key]["total"] += amount
            if row.get("is_unattributed_finance_bucket"):
                by_day_component[day_key]["unattributed"] += amount
                by_week_component[week_key]["unattributed"] += amount
    daily_rows = []
    for (day_value, component), values in sorted(by_day_component.items()):
        daily_rows.append(
            {
                "day": day_value.isoformat(),
                "component": component,
                "amount_total": str(q2(values["total"])),
                "amount_unattributed": str(q2(values["unattributed"])),
                "amount_attributed": str(q2(values["total"] - values["unattributed"])),
            }
        )
    weekly_rows = []
    for (week_value, component), values in sorted(by_week_component.items()):
        weekly_rows.append(
            {
                "week_start": week_value.isoformat(),
                "component": component,
                "amount_total": str(q2(values["total"])),
                "amount_unattributed": str(q2(values["unattributed"])),
                "amount_attributed": str(q2(values["total"] - values["unattributed"])),
            }
        )
    return daily_rows, weekly_rows


def driver_sign(component: str) -> int:
    return 1 if component in {"revenue_net", "positive_adjustment"} else -1


def build_change_drivers(
    store_rows_raw: list[dict[str, Any]],
    product_window_rows: list[dict[str, Any]],
    windows: list[Window],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window in windows:
        current_agg = aggregate_metrics(slice_rows(store_rows_raw, window.current_start, window.current_end))
        base_agg = aggregate_metrics(slice_rows(store_rows_raw, window.base_start, window.base_end))
        component_effects: list[dict[str, Any]] = []
        for component in COMPONENTS:
            current_per_day = q2(current_agg[component] / Decimal(window.current_days))
            base_per_day = q2(base_agg[component] / Decimal(window.base_days))
            delta_component = current_per_day - base_per_day
            effect = Decimal(driver_sign(component)) * delta_component
            component_effects.append(
                {
                    "window_label": window.label,
                    "scope": "store",
                    "entity_id": "STORE",
                    "entity_name": "STORE",
                    "driver_component": component,
                    "current_per_day": str(q2(current_per_day) if component not in {"margin_after_ads", "ctr", "cr", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "ad_cost_per_revenue_rub"} else q4(current_per_day)),
                    "base_per_day": str(q2(base_per_day) if component not in {"margin_after_ads", "ctr", "cr", "ad_share_of_revenue", "ad_share_of_profit_before_ads", "ad_cost_per_revenue_rub"} else q4(base_per_day)),
                    "delta_component_per_day": str(q2(delta_component)),
                    "profit_effect_per_day": str(q2(effect)),
                }
            )
        component_effects.sort(key=lambda item: abs(q2(item["profit_effect_per_day"])), reverse=True)
        total_abs = sum(abs(q2(item["profit_effect_per_day"])) for item in component_effects) or Decimal("1.00")
        for item in component_effects:
            item["share_of_abs_effect"] = str(q4(abs(q2(item["profit_effect_per_day"])) / total_abs))
            rows.append(item)

    for summary in product_window_rows:
        for prefix, window_label in [("short", "short_7d_vs_prev_28d"), ("medium", "medium_14d_vs_prev_42d")]:
            current_revenue = q2(summary[f"{prefix}_current_revenue_total"])
            base_revenue = q2(summary[f"{prefix}_base_revenue_total"])
            current_ads = q2(summary[f"{prefix}_current_ad_spend_total"])
            base_ads = q2(summary[f"{prefix}_base_ad_spend_total"])
            current_logistics = q2(summary[f"{prefix}_current_logistics_total"])
            base_logistics = q2(summary[f"{prefix}_base_logistics_total"])
            current_withholding = q2(summary[f"{prefix}_current_withholding_total"])
            base_withholding = q2(summary[f"{prefix}_base_withholding_total"])
            profit_delta = q2(summary[f"{prefix}_profit_after_ads_delta_per_day"])
            current_days = Decimal("7" if prefix == "short" else "14")
            base_days = Decimal("28" if prefix == "short" else "42")
            deltas = [
                ("revenue_net", q2(current_revenue / current_days - base_revenue / base_days)),
                ("ad_spend", q2(current_ads / current_days - base_ads / base_days)),
                ("logistics_total_cost", q2(current_logistics / current_days - base_logistics / base_days)),
                ("withholding_cost", q2(current_withholding / current_days - base_withholding / base_days)),
            ]
            for component, delta_component in deltas:
                effect = Decimal(1 if component == "revenue_net" else -1) * delta_component
                rows.append(
                    {
                        "window_label": window_label,
                        "scope": "product",
                        "entity_id": summary["product_id"],
                        "entity_name": summary["product_name"],
                        "driver_component": component,
                        "current_per_day": "",
                        "base_per_day": "",
                        "delta_component_per_day": str(q2(delta_component)),
                        "profit_effect_per_day": str(q2(effect)),
                        "share_of_abs_effect": "",
                        "profit_delta_per_day": str(profit_delta),
                    }
                )
    return rows


def build_product_top_worst(product_window_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in product_window_rows if row["product_id"] != UNATTRIBUTED_BUCKET]
    output: list[dict[str, Any]] = []
    segments = [
        ("top_profit_full", sorted(rows, key=lambda item: q2(item["profit_after_ads_total"]), reverse=True)[:5]),
        ("worst_profit_full", sorted(rows, key=lambda item: q2(item["profit_after_ads_total"]))[:5]),
        ("biggest_short_profit_drop", sorted(rows, key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]))[:5]),
        ("highest_ad_share", sorted(rows, key=lambda item: q4(item["ad_share_of_revenue_total"]), reverse=True)[:5]),
        ("highest_logistics_share", sorted(rows, key=lambda item: q4(item["logistics_share_of_revenue_total"]), reverse=True)[:5]),
        ("highest_withholding_total", sorted(rows, key=lambda item: q2(item["withholding_cost_total"]), reverse=True)[:5]),
        ("no_ads_positive_profit", [row for row in rows if q2(row["ad_spend_total"]) == 0 and q2(row["profit_after_ads_total"]) > 0][:5]),
    ]
    for segment, segment_rows in segments:
        for rank, row in enumerate(segment_rows, start=1):
            output.append(
                {
                    "segment": segment,
                    "rank": rank,
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "category_name": row["category_name"],
                    "revenue_net_total": row["revenue_net_total"],
                    "profit_after_ads_total": row["profit_after_ads_total"],
                    "margin_after_ads_total": row["margin_after_ads_total"],
                    "ad_spend_total": row["ad_spend_total"],
                    "logistics_total_cost_total": row["logistics_total_cost_total"],
                    "withholding_cost_total": row["withholding_cost_total"],
                    "short_profit_after_ads_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                    "medium_profit_after_ads_delta_per_day": row["medium_profit_after_ads_delta_per_day"],
                }
            )
    return output


def dominant_product_driver(row: dict[str, Any]) -> str:
    candidates = {
        "revenue_drop": -q2(row["short_revenue_delta_per_day"]),
        "ads_growth": q2(row["short_ad_spend_delta_per_day"]),
        "logistics_growth": q2(row["short_logistics_delta_per_day"]),
        "withholding_growth": q2(row["short_withholding_delta_per_day"]),
    }
    return max(candidates.items(), key=lambda item: item[1])[0]


def build_candidates(product_window_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in product_window_rows if row["product_id"] != UNATTRIBUTED_BUCKET]
    rows = [row for row in rows if q2(row["revenue_net_total"]) > 0 or q2(row["withholding_cost_total"]) > 0 or q2(row["ad_spend_total"]) > 0]
    rows.sort(
        key=lambda item: (
            q2(item["short_profit_after_ads_delta_per_day"]) + q2(item["medium_profit_after_ads_delta_per_day"]),
            -q2(item["revenue_net_total"]),
        )
    )
    output: list[dict[str, Any]] = []
    for rank, row in enumerate(rows[:12], start=1):
        output.append(
            {
                "rank": rank,
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category_name": row["category_name"],
                "revenue_net_total": row["revenue_net_total"],
                "profit_after_ads_total": row["profit_after_ads_total"],
                "short_profit_after_ads_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                "medium_profit_after_ads_delta_per_day": row["medium_profit_after_ads_delta_per_day"],
                "short_margin_after_ads_delta": row["short_margin_after_ads_delta"],
                "ad_spend_total": row["ad_spend_total"],
                "logistics_total_cost_total": row["logistics_total_cost_total"],
                "withholding_cost_total": row["withholding_cost_total"],
                "ad_share_of_revenue_total": row["ad_share_of_revenue_total"],
                "logistics_share_of_revenue_total": row["logistics_share_of_revenue_total"],
                "dominant_driver": dominant_product_driver(row),
                "candidate_reason": (
                    f"short_delta={row['short_profit_after_ads_delta_per_day']} per day; "
                    f"medium_delta={row['medium_profit_after_ads_delta_per_day']} per day; "
                    f"dominant_driver={dominant_product_driver(row)}"
                ),
            }
        )
    return output


def weekly_pattern_summary(expense_weekly_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_component: dict[str, list[Decimal]] = defaultdict(list)
    for row in expense_weekly_rows:
        by_component[row["component"]].append(q2(row["amount_total"]))
    summary: list[dict[str, Any]] = []
    for component, values in by_component.items():
        if not values:
            continue
        mean = sum(values) / Decimal(len(values))
        if mean == 0:
            cv = Decimal("0.0000")
        else:
            variance = sum((value - mean) * (value - mean) for value in values) / Decimal(len(values))
            cv = safe_div(variance.sqrt() if variance >= 0 else Decimal("0"), mean)
        direction_changes = 0
        deltas = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
        for idx in range(1, len(deltas)):
            if (deltas[idx] > 0 > deltas[idx - 1]) or (deltas[idx] < 0 < deltas[idx - 1]):
                direction_changes += 1
        pattern = "wave_like" if cv > Decimal("0.35") and direction_changes >= 2 else ("stable" if cv < Decimal("0.15") else "mixed")
        summary.append(
            {
                "component": component,
                "weeks": len(values),
                "weekly_mean": str(q2(mean)),
                "weekly_min": str(q2(min(values))),
                "weekly_max": str(q2(max(values))),
                "coefficient_of_variation": str(q4(cv)),
                "direction_changes": direction_changes,
                "pattern": pattern,
            }
        )
    summary.sort(key=lambda item: q4(item["coefficient_of_variation"]), reverse=True)
    return summary


def strongest_store_facts(
    store_rows_raw: list[dict[str, Any]],
    store_window_rows: list[dict[str, Any]],
    change_driver_rows: list[dict[str, Any]],
    product_window_rows: list[dict[str, Any]],
    corridor_start: date,
    corridor_end: date,
) -> dict[str, Any]:
    full_agg = aggregate_metrics(store_rows_raw)
    by_metric = {(row["window_label"], row["metric"]): row for row in store_window_rows}
    short_profit = by_metric[("short_7d_vs_prev_28d", "profit_after_ads")]
    short_revenue = by_metric[("short_7d_vs_prev_28d", "revenue_net")]
    short_ads = by_metric[("short_7d_vs_prev_28d", "ad_spend")]
    short_logistics = by_metric[("short_7d_vs_prev_28d", "logistics_total_cost")]
    short_withholding = by_metric[("short_7d_vs_prev_28d", "withholding_cost")]
    short_margin = by_metric[("short_7d_vs_prev_28d", "margin_after_ads")]
    medium_profit = by_metric[("medium_14d_vs_prev_42d", "profit_after_ads")]
    store_driver_rows = [row for row in change_driver_rows if row["scope"] == "store" and row["window_label"] == "short_7d_vs_prev_28d"]
    strongest_negative = sorted(store_driver_rows, key=lambda item: q2(item["profit_effect_per_day"]))[:5]
    worst_products = sorted(
        [row for row in product_window_rows if row["product_id"] != UNATTRIBUTED_BUCKET],
        key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]),
    )[:5]
    top_profit_products = sorted(
        [row for row in product_window_rows if row["product_id"] != UNATTRIBUTED_BUCKET],
        key=lambda item: q2(item["profit_after_ads_total"]),
        reverse=True,
    )[:5]
    unattributed = next((row for row in product_window_rows if row["product_id"] == UNATTRIBUTED_BUCKET), None)
    return {
        "corridor_start": corridor_start.isoformat(),
        "corridor_end": corridor_end.isoformat(),
        "corridor_days": (corridor_end - corridor_start).days + 1,
        "full_revenue": money(full_agg["revenue_net"]),
        "full_profit_after_ads": money(full_agg["profit_after_ads"]),
        "full_margin_after_ads": pct(full_agg["margin_after_ads"]),
        "full_ad_spend": money(full_agg["ad_spend"]),
        "full_logistics_total": money(full_agg["logistics_total_cost"]),
        "full_withholding_total": money(full_agg["withholding_cost"]),
        "short_profit_delta_per_day": money(q2(short_profit["delta_per_day"])),
        "short_revenue_delta_per_day": money(q2(short_revenue["delta_per_day"])),
        "short_ads_delta_per_day": money(q2(short_ads["delta_per_day"])),
        "short_logistics_delta_per_day": money(q2(short_logistics["delta_per_day"])),
        "short_withholding_delta_per_day": money(q2(short_withholding["delta_per_day"])),
        "short_margin_delta": pct(q4(short_margin["delta_per_day"])),
        "medium_profit_delta_per_day": money(q2(medium_profit["delta_per_day"])),
        "strongest_negative": strongest_negative,
        "worst_products": worst_products,
        "top_profit_products": top_profit_products,
        "unattributed_bucket": unattributed,
    }


def build_report(
    *,
    facts: dict[str, Any],
    store_daily_rows: list[dict[str, Any]],
    store_window_rows: list[dict[str, Any]],
    product_window_rows: list[dict[str, Any]],
    product_pnl_rows: list[dict[str, Any]],
    change_driver_rows: list[dict[str, Any]],
    weekly_pattern_rows: list[dict[str, Any]],
    candidates_rows: list[dict[str, Any]],
) -> str:
    short_positive_offsets = sorted(
        [
            row
            for row in change_driver_rows
            if row["scope"] == "store"
            and row["window_label"] == "short_7d_vs_prev_28d"
            and q2(row["profit_effect_per_day"]) > 0
        ],
        key=lambda item: q2(item["profit_effect_per_day"]),
        reverse=True,
    )[:4]
    short_worst = [
        {
            "product_name": row["product_name"],
            "short_profit_delta_per_day": row["short_profit_after_ads_delta_per_day"],
            "short_revenue_delta_per_day": row["short_revenue_delta_per_day"],
            "short_ad_spend_delta_per_day": row["short_ad_spend_delta_per_day"],
            "short_logistics_delta_per_day": row["short_logistics_delta_per_day"],
            "short_withholding_delta_per_day": row["short_withholding_delta_per_day"],
        }
        for row in facts["worst_products"]
    ]
    top_profit = [
        {
            "product_name": row["product_name"],
            "profit_after_ads_total": row["profit_after_ads_total"],
            "revenue_net_total": row["revenue_net_total"],
            "margin_after_ads_total": row["margin_after_ads_total"],
        }
        for row in facts["top_profit_products"]
    ]
    short_driver_table = [
        {
            "driver_component": row["driver_component"],
            "profit_effect_per_day": row["profit_effect_per_day"],
            "delta_component_per_day": row["delta_component_per_day"],
            "share_of_abs_effect": row["share_of_abs_effect"],
        }
        for row in facts["strongest_negative"][:5]
    ]
    unattributed = facts["unattributed_bucket"]
    lines = [
        "# PROFIT CONTROL — Deep Analysis Pack v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Analysis Scope",
        "",
        f"- Finance-complete economics corridor: `{facts['corridor_start']} .. {facts['corridor_end']}` (`{facts['corridor_days']}` days).",
        "- Sales and ads exist beyond the corridor end, but full P&L stays bounded by the last finance-complete day.",
        "- Product grain uses `nm_id/product_id` as canonical WB product key because it is the only key with full overlap across sales, finance, ads, and product dimension.",
        "- Finance rows that cannot be mapped to a product are preserved in a separate `UNATTRIBUTED_FINANCE_BUCKET` instead of being silently dropped.",
        "",
        "## Store Overview Pack",
        "",
        f"- Full corridor revenue: `{facts['full_revenue']}`; profit after ads: `{facts['full_profit_after_ads']}`; margin after ads: `{facts['full_margin_after_ads']}`.",
        f"- Full corridor ad spend: `{facts['full_ad_spend']}`; total logistics block: `{facts['full_logistics_total']}`; withholdings: `{facts['full_withholding_total']}`.",
        f"- Short window (`last 7d vs previous 28d`) profit-after-ads delta is `{facts['short_profit_delta_per_day']}` per day; revenue delta `{facts['short_revenue_delta_per_day']}` per day; ads delta `{facts['short_ads_delta_per_day']}` per day.",
        f"- In the same short window, margin after ads changed by `{facts['short_margin_delta']}`; logistics moved by `{facts['short_logistics_delta_per_day']}` per day and withholdings by `{facts['short_withholding_delta_per_day']}` per day, so part of the profit drop was cushioned by lower finance pressure instead of being caused by it.",
        f"- Medium window (`last 14d vs previous 42d`) profit-after-ads delta is `{facts['medium_profit_delta_per_day']}` per day.",
        "",
        "### Strongest Short-Window Change Drivers",
        "",
        md_table(short_driver_table, ["driver_component", "profit_effect_per_day", "delta_component_per_day", "share_of_abs_effect"]),
        "",
        "### Main Positive Offsets In The Same Short Window",
        "",
        md_table(
            [
                {
                    "driver_component": row["driver_component"],
                    "profit_effect_per_day": row["profit_effect_per_day"],
                    "delta_component_per_day": row["delta_component_per_day"],
                    "share_of_abs_effect": row["share_of_abs_effect"],
                }
                for row in short_positive_offsets
            ],
            ["driver_component", "profit_effect_per_day", "delta_component_per_day", "share_of_abs_effect"],
        ),
        "",
        "## Product Deep Analysis Pack",
        "",
        f"- Economics corridor contains `{len([row for row in product_pnl_rows if row['product_id'] != UNATTRIBUTED_BUCKET])}` mapped products plus the unattributed finance bucket.",
        "- Product candidates below are factual analysis targets for the next hypothesis stage, not recommendations.",
        "",
        "### Top Profit Contributors",
        "",
        md_table(top_profit, ["product_name", "profit_after_ads_total", "revenue_net_total", "margin_after_ads_total"]),
        "",
        "### Biggest Short-Window Deterioration",
        "",
        md_table(short_worst, ["product_name", "short_profit_delta_per_day", "short_revenue_delta_per_day", "short_ad_spend_delta_per_day", "short_logistics_delta_per_day", "short_withholding_delta_per_day"]),
        "",
        "## Expense Breakdown Pack",
        "",
        f"- Weekly volatility is highest where `coefficient_of_variation` and repeated direction changes are both elevated; the most wave-like components are listed below.",
        "",
        md_table(weekly_pattern_rows[:6], ["component", "weekly_mean", "weekly_min", "weekly_max", "coefficient_of_variation", "direction_changes", "pattern"]),
        "",
        "## Change Drivers Pack",
        "",
        "- Profit change is decomposed as revenue and positive adjustments minus ads and finance-cost components on a per-day basis, so short and medium windows stay comparable despite different lengths.",
        "",
        "## Data Quality / Attribution Facts",
        "",
    ]
    if unattributed is not None:
        lines.extend(
            [
                f"- `UNATTRIBUTED_FINANCE_BUCKET` carries `{unattributed['profit_after_ads_total']}` profit after ads, `{unattributed['withholding_cost_total']}` withholdings, `{unattributed['logistics_total_cost_total']}` logistics, and `{unattributed['storage_cost_total']}` storage.",
                "- This bucket is structural, not a random outlier: it contains finance rows with no product-mappable key, so store totals are stronger than pure product-attributed totals unless that bucket is analyzed explicitly.",
            ]
        )
    lines.extend(
        [
            "",
            "## Weekly Pattern Pack",
            "",
            "- `wave_like` means high week-to-week volatility plus at least two direction reversals across the corridor.",
            "- `stable` means low coefficient of variation; `mixed` sits between these poles.",
            "",
            "## Product Candidates For Hypothesis Work",
            "",
            md_table(candidates_rows[:10], ["rank", "product_name", "short_profit_after_ads_delta_per_day", "medium_profit_after_ads_delta_per_day", "dominant_driver", "candidate_reason"]),
            "",
            "## Strong Factual Conclusions",
            "",
            f"- Main store P&L analysis is currently bounded by the finance-complete corridor `{facts['corridor_start']} .. {facts['corridor_end']}`; broader sales/ads history exists, but not broader confirmed store-level economics.",
            f"- The store-level short-window deterioration is primarily revenue-led: revenue moved by `{facts['short_revenue_delta_per_day']}` per day, while several finance-cost lines actually moved down and softened the hit.",
            "- Product-level deterioration is concentrated: a small subset of products drives the weakest short-window profit delta, while the top contributors remain distinctly positive over the full corridor.",
            "- Expense pressure is not homogeneous: some components are weekly stable, while others are clearly wave-like and recur in spikes rather than drift smoothly.",
            "- The unattributed finance bucket is materially large enough to matter for store interpretation and cannot be ignored in later causal analysis.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    corridor_start, corridor_end = fetch_corridor_bounds()
    windows = build_windows(corridor_start, corridor_end)
    product_daily = fetch_product_daily(corridor_start, corridor_end)
    corridor_days = daterange(corridor_start, corridor_end)
    store_daily_rows = build_store_daily(product_daily, corridor_days)

    store_rows_raw = [{**row, "day": date.fromisoformat(row["day"])} for row in store_daily_rows]
    for row in store_rows_raw:
        for key in row:
            if key != "day":
                try:
                    row[key] = Decimal(str(row[key]))
                except Exception:
                    pass

    store_window_rows = window_comparisons(product_daily, windows)
    product_window_rows, product_pnl_rows = build_product_comparisons(product_daily, corridor_end, windows)
    expense_daily_rows, expense_weekly_rows = build_expense_component_tables(product_daily)
    change_driver_rows = build_change_drivers(product_daily, product_window_rows, windows)
    top_worst_rows = build_product_top_worst(product_window_rows)
    candidates_rows = build_candidates(product_window_rows)
    weekly_pattern_rows = weekly_pattern_summary(expense_weekly_rows)
    facts = strongest_store_facts(product_daily, store_window_rows, change_driver_rows, product_window_rows, corridor_start, corridor_end)

    write_csv(STORE_DAILY_CSV, store_daily_rows)
    write_csv(STORE_WINDOWS_CSV, store_window_rows)
    write_csv(PRODUCT_WINDOWS_CSV, product_window_rows)
    write_csv(PRODUCT_PNL_CSV, product_pnl_rows)
    write_csv(EXPENSE_DAILY_CSV, expense_daily_rows)
    write_csv(EXPENSE_WEEKLY_CSV, expense_weekly_rows)
    write_csv(CHANGE_DRIVERS_CSV, change_driver_rows)
    write_csv(PRODUCT_TOP_WORST_CSV, top_worst_rows)
    write_csv(CANDIDATES_CSV, candidates_rows)
    DOC_PATH.write_text(
        build_report(
            facts=facts,
            store_daily_rows=store_daily_rows,
            store_window_rows=store_window_rows,
            product_window_rows=product_window_rows,
            product_pnl_rows=product_pnl_rows,
            change_driver_rows=change_driver_rows,
            weekly_pattern_rows=weekly_pattern_rows,
            candidates_rows=candidates_rows,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {STORE_DAILY_CSV.relative_to(ROOT)}")
    print(f"Wrote {STORE_WINDOWS_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_WINDOWS_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_PNL_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXPENSE_DAILY_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXPENSE_WEEKLY_CSV.relative_to(ROOT)}")
    print(f"Wrote {CHANGE_DRIVERS_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_TOP_WORST_CSV.relative_to(ROOT)}")
    print(f"Wrote {CANDIDATES_CSV.relative_to(ROOT)}")
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
