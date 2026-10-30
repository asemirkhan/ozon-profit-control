#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_profit_control_identity_normalization_stage_v1 import (  # noqa: E402
    aggregate_normalized_products,
    build_mapping_rows,
    choose_representative_row,
    is_placeholder,
    md_table,
    merge_reason,
    norm_text,
)
from build_profit_control_simulation_dataset_v1 import (  # noqa: E402
    SCHEMA,
    build_store_only_rows,
    build_window,
    load_group_map,
    load_product_rows,
    load_stock_metrics,
    q2,
    write_csv,
)
from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

OUTPUT_DIR = ROOT / "artifacts" / "profit_control_recommendation_simulation_pack_v1"
REPORT_PATH = ROOT / "profit_control_recommendation_simulation_pack_v1.md"

STORE_HORIZONS_CSV = OUTPUT_DIR / "store_horizons.csv"
STORE_1D_SIGNAL_CSV = OUTPUT_DIR / "store_daily_signal_1d.csv"
ALL_PRODUCTS_CSV = OUTPUT_DIR / "all_products_28d_normalized.csv"
PRODUCT_FLAGS_CSV = OUTPUT_DIR / "product_problem_flags.csv"
UNIT_ECONOMICS_CSV = OUTPUT_DIR / "product_unit_economics.csv"
EXPENSE_DEPTH_CSV = OUTPUT_DIR / "priority_product_expense_depth.csv"
SHORTLIST_CSV = OUTPUT_DIR / "recommendation_shortlist.csv"
STATE_HISTORY_CSV = OUTPUT_DIR / "product_state_metrics_history.csv"
STORE_ONLY_CSV = OUTPUT_DIR / "store_only_expenses_28d.csv"
PRODUCT_SIGNALS_CSV = OUTPUT_DIR / "product_signals_28d_7d_1d.csv"

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class Horizon:
    name: str
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date


STORE_METRIC_FIELDS = [
    "revenue_total",
    "expenses_total",
    "result_total",
    "ads_total",
    "commission_total",
    "logistics_total",
    "storage_total",
    "acquiring_total",
    "returns_total",
    "other_product_expenses_total",
    "store_only_expenses_total",
]


def safe_div_dec(num: Decimal, den: Decimal) -> Decimal:
    if den == 0:
        return Decimal("0.00")
    return (num / den).quantize(MONEY)


def fmt(value: Any) -> str:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, (int, float)):
        return f"{Decimal(str(value)).quantize(MONEY):.2f}"
    return str(value)


def decimal_or_blank(num: Decimal, den: Decimal) -> str:
    if den == 0:
        return "unavailable"
    return f"{safe_div_dec(num, den):.2f}"


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def build_horizon(window: Any, days: int) -> Horizon:
    # The primary management horizon is the latest fully completed calendar
    # month versus the preceding calendar month.  Keep the historical "28d"
    # key in the exported contract until the cabinet migration is complete.
    if days == 28:
        first_of_confirmed_month = window.confirmed_through.replace(day=1)
        current_end = first_of_confirmed_month - timedelta(days=1)
        current_start = current_end.replace(day=1)
        previous_end = current_start - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        return Horizon(
            name="28d",
            current_start=current_start,
            current_end=current_end,
            previous_start=previous_start,
            previous_end=previous_end,
        )
    current_end = window.confirmed_through
    current_start = current_end - timedelta(days=days - 1)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return Horizon(
        name=f"{days}d",
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
    )


def fetch_store_horizon(h: Horizon) -> dict[str, Any]:
    rows = fetch_all(
        f"""
        WITH base AS (
          SELECT *,
                 CASE
                   WHEN date BETWEEN %s AND %s THEN 'current'
                   WHEN date BETWEEN %s AND %s THEN 'previous'
                 END AS period_label
          FROM {SCHEMA}.pc_v2_daily_store_fact
          WHERE marketplace = 'wb'
            AND date BETWEEN %s AND %s
        )
        SELECT
          period_label,
          SUM(revenue_gross)::numeric(18,2) AS revenue_total,
          SUM(ads_amount)::numeric(18,2) AS ads_total,
          SUM(commission_amount)::numeric(18,2) AS commission_total,
          SUM(logistics_amount)::numeric(18,2) AS logistics_total,
          SUM(storage_amount)::numeric(18,2) AS storage_total,
          SUM(acquiring_amount)::numeric(18,2) AS acquiring_total,
          SUM(returns_amount)::numeric(18,2) AS returns_total,
          SUM(other_expenses_amount + product_services_amount + penalties_amount)::numeric(18,2) AS other_product_expenses_total,
          SUM(store_level_expense_amount)::numeric(18,2) AS store_only_expenses_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total,
          MAX(confirmed_through)::date AS confirmed_through
        FROM base
        GROUP BY period_label
        """,
        (
            h.current_start,
            h.current_end,
            h.previous_start,
            h.previous_end,
            h.previous_start,
            h.current_end,
        ),
    )
    keyed = {row["period_label"]: row for row in rows}
    out: dict[str, Any] = {
        "horizon": h.name,
        "current_start": h.current_start.isoformat(),
        "current_end": h.current_end.isoformat(),
        "previous_start": h.previous_start.isoformat(),
        "previous_end": h.previous_end.isoformat(),
    }
    for label in ("previous", "current"):
        row = keyed[label]
        expenses = (
            q2(row["ads_total"])
            + q2(row["commission_total"])
            + q2(row["logistics_total"])
            + q2(row["storage_total"])
            + q2(row["acquiring_total"])
            + q2(row["returns_total"])
            + q2(row["other_product_expenses_total"])
            + q2(row["store_only_expenses_total"])
        )
        prefix = "prev" if label == "previous" else "curr"
        out[f"revenue_{prefix}"] = q2(row["revenue_total"])
        out[f"expenses_{prefix}"] = expenses
        out[f"result_{prefix}"] = q2(row["result_total"])
        out[f"ads_{prefix}"] = q2(row["ads_total"])
        out[f"commission_{prefix}"] = q2(row["commission_total"])
        out[f"logistics_{prefix}"] = q2(row["logistics_total"])
        out[f"storage_{prefix}"] = q2(row["storage_total"])
        out[f"acquiring_{prefix}"] = q2(row["acquiring_total"])
        out[f"returns_{prefix}"] = q2(row["returns_total"])
        out[f"other_product_expenses_{prefix}"] = q2(row["other_product_expenses_total"])
        out[f"store_only_expenses_{prefix}"] = q2(row["store_only_expenses_total"])
        out["confirmed_through"] = row["confirmed_through"].isoformat()
    return out


def fetch_store_daily_series(date_from: date, date_to: date) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT
          date,
          SUM(revenue_gross)::numeric(18,2) AS revenue_total,
          SUM(ads_amount)::numeric(18,2) AS ads_total,
          SUM(commission_amount)::numeric(18,2) AS commission_total,
          SUM(logistics_amount)::numeric(18,2) AS logistics_total,
          SUM(storage_amount)::numeric(18,2) AS storage_total,
          SUM(acquiring_amount)::numeric(18,2) AS acquiring_total,
          SUM(returns_amount)::numeric(18,2) AS returns_total,
          SUM(other_expenses_amount + product_services_amount + penalties_amount)::numeric(18,2) AS other_product_expenses_total,
          SUM(store_level_expense_amount)::numeric(18,2) AS store_only_expenses_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total
        FROM {SCHEMA}.pc_v2_daily_store_fact
        WHERE marketplace='wb'
          AND date BETWEEN %s AND %s
        GROUP BY date
        ORDER BY date
        """,
        (date_from, date_to),
    )


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    xs = sorted(values)
    idx = (len(xs) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return xs[lo]
    frac = idx - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def cleaned_baseline(values: list[float]) -> list[float]:
    if len(values) < 5:
        return values[:]
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr
    cleaned = [v for v in values if lower <= v <= upper]
    return cleaned if len(cleaned) >= max(3, len(values) // 2) else values[:]


def signal_stats(values: list[float], current_value: float) -> tuple[float, float, bool]:
    baseline_values = cleaned_baseline(values)
    baseline = statistics.median(baseline_values) if baseline_values else 0.0
    deviation = current_value - baseline
    deviation_pct = 0.0 if baseline == 0 else deviation / baseline
    if len(baseline_values) >= 2:
        median = statistics.median(baseline_values)
        mad = statistics.median([abs(v - median) for v in baseline_values]) if baseline_values else 0.0
        robust_sigma = mad * 1.4826
    else:
        robust_sigma = 0.0
    threshold = max(abs(baseline) * 0.20, robust_sigma * 2.5, 1.0)
    significant = abs(deviation) >= threshold
    return baseline, deviation_pct, significant


def build_store_1d_signal(window: Any) -> list[dict[str, Any]]:
    baseline_start = window.confirmed_through - timedelta(days=56)
    baseline_end = window.confirmed_through - timedelta(days=1)
    rows = fetch_store_daily_series(baseline_start, window.confirmed_through)
    history = [
        row for row in rows[:-1]
        if row["date"].weekday() == window.confirmed_through.weekday()
    ]
    current = rows[-1]
    result: list[dict[str, Any]] = []
    for field in STORE_METRIC_FIELDS:
        if field == "expenses_total":
            hist_values = [
                float(
                    q2(row["ads_total"])
                    + q2(row["commission_total"])
                    + q2(row["logistics_total"])
                    + q2(row["storage_total"])
                    + q2(row["acquiring_total"])
                    + q2(row["returns_total"])
                    + q2(row["other_product_expenses_total"])
                    + q2(row["store_only_expenses_total"])
                )
                for row in history
            ]
            current_value = float(
                q2(current["ads_total"])
                + q2(current["commission_total"])
                + q2(current["logistics_total"])
                + q2(current["storage_total"])
                + q2(current["acquiring_total"])
                + q2(current["returns_total"])
                + q2(current["other_product_expenses_total"])
                + q2(current["store_only_expenses_total"])
            )
        else:
            hist_values = [float(q2(row[field])) for row in history]
            current_value = float(q2(current[field]))
        baseline, deviation_pct, significant = signal_stats(hist_values, current_value)
        deviation = current_value - baseline
        result.append(
            {
                "metric_name": field,
                "baseline_day_value": f"{baseline:.2f}",
                "current_day_value": f"{current_value:.2f}",
                "deviation": f"{deviation:.2f}",
                "deviation_pct": f"{deviation_pct:.4f}",
                "is_significant_after_outlier_cleaning": "yes" if significant else "no",
                "baseline_window": f"{baseline_start.isoformat()} .. {baseline_end.isoformat()}",
                "current_day": window.confirmed_through.isoformat(),
            }
        )
    return result


def fetch_product_period_metrics(h: Horizon) -> dict[tuple[str, str], dict[str, Decimal]]:
    rows = fetch_all(
        f"""
        SELECT
          sku,
          CASE
            WHEN date BETWEEN %s AND %s THEN 'current'
            WHEN date BETWEEN %s AND %s THEN 'previous'
          END AS period_label,
          SUM(orders_count)::numeric(18,2) AS orders_count,
          SUM(sales_count)::numeric(18,2) AS sales_count,
          SUM(units_sold)::numeric(18,2) AS sales_units,
          SUM(revenue_gross)::numeric(18,2) AS revenue_gross,
          SUM(ads_amount)::numeric(18,2) AS ads_spend,
          SUM(logistics_amount)::numeric(18,2) AS logistics_total,
          SUM(storage_amount)::numeric(18,2) AS storage_total,
          SUM(acquiring_amount)::numeric(18,2) AS acquiring_total,
          SUM(commission_amount)::numeric(18,2) AS commission_total,
          SUM(returns_amount)::numeric(18,2) AS returns_total,
          SUM(other_expenses_amount + product_services_amount + penalties_amount)::numeric(18,2) AS other_product_expenses_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total
        FROM {SCHEMA}.pc_v2_daily_product_fact
        WHERE marketplace='wb'
          AND date BETWEEN %s AND %s
        GROUP BY sku, period_label
        """,
        (
            h.current_start,
            h.current_end,
            h.previous_start,
            h.previous_end,
            h.previous_start,
            h.current_end,
        ),
    )
    out: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row in rows:
        out[(str(row["sku"]), row["period_label"])] = {
            "orders_count": q2(row["orders_count"]),
            "sales_count": q2(row["sales_count"]),
            "sales_units": q2(row["sales_units"]),
            "revenue_gross": q2(row["revenue_gross"]),
            "ads_spend": q2(row["ads_spend"]),
            "logistics_total": q2(row["logistics_total"]),
            "storage_total": q2(row["storage_total"]),
            "acquiring_total": q2(row["acquiring_total"]),
            "commission_total": q2(row["commission_total"]),
            "returns_total": q2(row["returns_total"]),
            "other_product_expenses_total": q2(row["other_product_expenses_total"]),
            "result_total": q2(row["result_total"]),
        }
    return out


def fetch_order_cohort_metrics(h: Horizon) -> dict[tuple[str, str], dict[str, Decimal]]:
    """Measure order completion inside one order-date cohort.

    Sales by sale date must not be divided by orders from another date range:
    delayed fulfilment can otherwise produce a fictitious conversion above
    100%.  Here every sale is joined back to the date of its original order.
    """
    rows = fetch_all(
        f"""
        SELECT
          o.barcode AS sku,
          CASE
            WHEN o.order_date::date BETWEEN %s AND %s THEN 'current'
            WHEN o.order_date::date BETWEEN %s AND %s THEN 'previous'
          END AS period_label,
          COUNT(DISTINCT o.order_id)::numeric(18,2) AS cohort_orders,
          COUNT(DISTINCT o.order_id) FILTER (
            WHERE EXISTS (
              SELECT 1
              FROM {SCHEMA}.stg_wb_sale s
              WHERE s.order_id = o.order_id
                AND COALESCE(s.is_return, FALSE) = FALSE
            )
          )::numeric(18,2) AS fulfilled_orders,
          COUNT(DISTINCT o.order_id) FILTER (
            WHERE o.cancel_dt IS NOT NULL
              AND o.cancel_dt::date > DATE '2000-01-01'
          )::numeric(18,2) AS cancelled_orders
        FROM {SCHEMA}.stg_wb_order o
        WHERE o.marketplace = 'wb'
          AND o.order_date::date BETWEEN %s AND %s
          AND NULLIF(o.barcode, '') IS NOT NULL
          AND NULLIF(o.order_id, '') IS NOT NULL
        GROUP BY o.barcode, period_label
        """,
        (
            h.current_start,
            h.current_end,
            h.previous_start,
            h.previous_end,
            h.previous_start,
            h.current_end,
        ),
    )
    return {
        (str(row["sku"]), row["period_label"]): {
            "cohort_orders": q2(row["cohort_orders"]),
            "fulfilled_orders": q2(row["fulfilled_orders"]),
            "cancelled_orders": q2(row["cancelled_orders"]),
        }
        for row in rows
    }


def fetch_ads_period_metrics(h: Horizon) -> dict[tuple[str, str], dict[str, Decimal]]:
    rows = fetch_all(
        f"""
        SELECT
          sku,
          CASE
            WHEN date BETWEEN %s AND %s THEN 'current'
            WHEN date BETWEEN %s AND %s THEN 'previous'
          END AS period_label,
          SUM(orders_count)::numeric(18,2) AS attributed_orders,
          SUM(revenue)::numeric(18,2) AS attributed_revenue,
          SUM(impressions)::numeric(18,2) AS views,
          SUM(clicks)::numeric(18,2) AS clicks
        FROM {SCHEMA}.mart_ads_daily
        WHERE marketplace='wb'
          AND date BETWEEN %s AND %s
        GROUP BY sku, period_label
        """,
        (
            h.current_start,
            h.current_end,
            h.previous_start,
            h.previous_end,
            h.previous_start,
            h.current_end,
        ),
    )
    out: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row in rows:
        out[(str(row["sku"]), row["period_label"])] = {
            "ad_attributed_orders": q2(row["attributed_orders"]),
            "ad_attributed_revenue": q2(row["attributed_revenue"]),
            "views": q2(row["views"]),
            "clicks": q2(row["clicks"]),
        }
    return out


def fetch_product_daily_series(date_from: date, date_to: date) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT
          date,
          sku,
          SUM(revenue_gross)::numeric(18,2) AS revenue_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total,
          SUM(orders_count)::numeric(18,2) AS orders_total,
          SUM(units_sold)::numeric(18,2) AS sales_units_total
        FROM {SCHEMA}.pc_v2_daily_product_fact
        WHERE marketplace='wb'
          AND date BETWEEN %s AND %s
        GROUP BY date, sku
        ORDER BY sku, date
        """,
        (date_from, date_to),
    )


def latest_product_identity() -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        f"""
        WITH latest_bridge AS (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY lower(offer_id)
                   ORDER BY updated_at DESC, bridge_id DESC
                 ) AS rn
          FROM {SCHEMA}.dim_product_bridge
          WHERE marketplace='wb'
        )
        SELECT lower(offer_id) AS offer_id_norm,
               nm_id,
               product_name
        FROM latest_bridge
        WHERE rn = 1
        """
    )
    return {str(row["offer_id_norm"]): row for row in rows}


def current_card_snapshot_by_nm() -> dict[str, dict[str, Any]]:
    rows = fetch_all(
        f"""
        SELECT
          nm_id,
          rating,
          feedbacks,
          captured_at
        FROM {SCHEMA}.stg_wb_product_card_snapshot
        WHERE marketplace='wb'
        """
    )
    return {str(row["nm_id"]): row for row in rows}


def severity_down(prev: Decimal, curr: Decimal) -> str:
    if curr >= prev:
        return "none"
    if prev == 0:
        return "high" if curr < 0 else "none"
    pct = float((curr - prev) / prev)
    if pct <= -0.30:
        return "high"
    if pct <= -0.15:
        return "medium"
    return "low"


def severity_up(prev: Decimal, curr: Decimal) -> str:
    if curr <= prev:
        return "none"
    if prev == 0:
        return "high" if curr > 0 else "none"
    pct = float((curr - prev) / prev)
    if pct >= 0.30:
        return "high"
    if pct >= 0.15:
        return "medium"
    return "low"


def severity_count(value: int) -> str:
    if value <= 0:
        return "none"
    if value >= 7:
        return "high"
    if value >= 2:
        return "medium"
    return "low"


def severity_points(level: str) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(level, 0)


def build_normalized_products(window: Any) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    group_map = load_group_map()
    raw_products = load_product_rows(window, group_map)
    _, grouped = build_mapping_rows(raw_products)
    normalized = aggregate_normalized_products(grouped)
    return normalized, grouped


def enrich_all_products(window: Any, normalized_products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    h28 = build_horizon(window, 28)
    h7 = build_horizon(window, 7)
    ops28 = fetch_product_period_metrics(h28)
    cohorts28 = fetch_order_cohort_metrics(h28)
    ops7 = fetch_product_period_metrics(h7)
    ads28 = fetch_ads_period_metrics(h28)
    stock = load_stock_metrics(
        [{"sku": row["sku"], "offer_id": row["offer_id"]} for row in normalized_products],
        h28,
    )
    daily_rows = fetch_product_daily_series(window.confirmed_through - timedelta(days=56), window.confirmed_through)
    by_sku_daily: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in daily_rows:
        by_sku_daily[str(row["sku"])].append(row)

    all_products: list[dict[str, Any]] = []
    signals_rows: list[dict[str, Any]] = []
    signals_index: dict[str, dict[str, Any]] = {}
    for item in normalized_products:
        sku = str(item["sku"])
        prev_ops = ops28.get((sku, "previous"), {})
        curr_ops = ops28.get((sku, "current"), {})
        prev_cohort = cohorts28.get((sku, "previous"), {})
        curr_cohort = cohorts28.get((sku, "current"), {})
        prev_ads = ads28.get((sku, "previous"), {})
        curr_ads = ads28.get((sku, "current"), {})
        prev_stock = stock.get((sku, "previous"), {})
        curr_stock = stock.get((sku, "current"), {})

        expenses_prev = sum(q2(prev_ops.get(field, 0)) for field in (
            "ads_spend", "commission_total", "logistics_total", "storage_total",
            "acquiring_total", "returns_total", "other_product_expenses_total",
        ))
        expenses_curr = sum(q2(curr_ops.get(field, 0)) for field in (
            "ads_spend", "commission_total", "logistics_total", "storage_total",
            "acquiring_total", "returns_total", "other_product_expenses_total",
        ))
        sales_prev = q2(prev_ops.get("sales_units", 0))
        sales_curr = q2(curr_ops.get("sales_units", 0))
        revenue_prev = q2(prev_ops.get("revenue_gross", 0))
        revenue_curr = q2(curr_ops.get("revenue_gross", 0))
        result_prev = q2(prev_ops.get("result_total", 0))
        result_curr = q2(curr_ops.get("result_total", 0))
        avg_price_prev = safe_div_dec(revenue_prev, sales_prev) if sales_prev else Decimal("0.00")
        avg_price_curr = safe_div_dec(revenue_curr, sales_curr) if sales_curr else Decimal("0.00")

        prev7 = ops7.get((sku, "previous"), {})
        curr7 = ops7.get((sku, "current"), {})

        daily_series = by_sku_daily.get(sku, [])
        history_values = [
            float(q2(row["result_total"]))
            for row in daily_series[:-1]
            if row["date"].weekday() == window.confirmed_through.weekday()
        ]
        current_day_result = float(q2(daily_series[-1]["result_total"])) if daily_series else 0.0
        baseline, deviation_pct, significant = signal_stats(history_values, current_day_result) if daily_series else (0.0, 0.0, False)

        signal_row = {
            "normalized_product_key": item["normalized_product_key"],
            "offer_id": item["offer_id"],
            "sku": item["sku"],
            "product_name": item["product_name"],
            "result_delta_28d": f"{(result_curr - result_prev):.2f}",
            "revenue_delta_28d": f"{(revenue_curr - revenue_prev):.2f}",
            "result_prev_7d": f"{q2(prev7.get('result_total', 0)):.2f}",
            "result_curr_7d": f"{q2(curr7.get('result_total', 0)):.2f}",
            "result_delta_7d": f"{(q2(curr7.get('result_total', 0)) - q2(prev7.get('result_total', 0))):.2f}",
            "revenue_prev_7d": f"{q2(prev7.get('revenue_gross', 0)):.2f}",
            "revenue_curr_7d": f"{q2(curr7.get('revenue_gross', 0)):.2f}",
            "revenue_delta_7d": f"{(q2(curr7.get('revenue_gross', 0)) - q2(prev7.get('revenue_gross', 0))):.2f}",
            "result_baseline_day_value": f"{baseline:.2f}",
            "result_current_day_value": f"{current_day_result:.2f}",
            "result_day_deviation_pct": f"{deviation_pct:.4f}",
            "sharp_1d_signal": "yes" if significant else "no",
        }
        signals_rows.append(signal_row)
        signals_index[item["normalized_product_key"]] = signal_row

        all_products.append(
            {
                "normalized_product_key": item["normalized_product_key"],
                "offer_id": item["offer_id"],
                "sku": item["sku"],
                "product_name": item["product_name"],
                "group_name": item["group_name"],
                "revenue_prev": f"{revenue_prev:.2f}",
                "revenue_curr": f"{revenue_curr:.2f}",
                "revenue_delta": f"{(revenue_curr - revenue_prev):.2f}",
                "expenses_prev": f"{expenses_prev:.2f}",
                "expenses_curr": f"{expenses_curr:.2f}",
                "expenses_delta": f"{(expenses_curr - expenses_prev):.2f}",
                "result_prev": f"{result_prev:.2f}",
                "result_curr": f"{result_curr:.2f}",
                "result_delta": f"{(result_curr - result_prev):.2f}",
                "orders_prev": f"{q2(prev_ops.get('orders_count', 0)):.2f}",
                "orders_curr": f"{q2(curr_ops.get('orders_count', 0)):.2f}",
                "orders_delta": f"{(q2(curr_ops.get('orders_count', 0)) - q2(prev_ops.get('orders_count', 0))):.2f}",
                "fulfilled_orders_prev": f"{q2(prev_cohort.get('fulfilled_orders', 0)):.2f}",
                "fulfilled_orders_curr": f"{q2(curr_cohort.get('fulfilled_orders', 0)):.2f}",
                "fulfilled_rate_prev": (
                    f"{safe_div_dec(q2(prev_cohort.get('fulfilled_orders', 0)), q2(prev_cohort.get('cohort_orders', 0))):.4f}"
                    if q2(prev_cohort.get("cohort_orders", 0)) else "unavailable"
                ),
                "fulfilled_rate_curr": (
                    f"{safe_div_dec(q2(curr_cohort.get('fulfilled_orders', 0)), q2(curr_cohort.get('cohort_orders', 0))):.4f}"
                    if q2(curr_cohort.get("cohort_orders", 0)) else "unavailable"
                ),
                "cancelled_orders_prev": f"{q2(prev_cohort.get('cancelled_orders', 0)):.2f}",
                "cancelled_orders_curr": f"{q2(curr_cohort.get('cancelled_orders', 0)):.2f}",
                "sales_units_prev": f"{sales_prev:.2f}",
                "sales_units_curr": f"{sales_curr:.2f}",
                "sales_units_delta": f"{(sales_curr - sales_prev):.2f}",
                "avg_realized_price_prev": f"{avg_price_prev:.2f}",
                "avg_realized_price_curr": f"{avg_price_curr:.2f}",
                "avg_realized_price_delta": f"{(avg_price_curr - avg_price_prev):.2f}",
                "ads_spend_prev": f"{q2(prev_ops.get('ads_spend', 0)):.2f}",
                "ads_spend_curr": f"{q2(curr_ops.get('ads_spend', 0)):.2f}",
                "ads_spend_delta": f"{(q2(curr_ops.get('ads_spend', 0)) - q2(prev_ops.get('ads_spend', 0))):.2f}",
                "ad_attributed_orders_prev": f"{q2(prev_ads.get('ad_attributed_orders', 0)):.2f}",
                "ad_attributed_orders_curr": f"{q2(curr_ads.get('ad_attributed_orders', 0)):.2f}",
                "ad_attributed_orders_delta": f"{(q2(curr_ads.get('ad_attributed_orders', 0)) - q2(prev_ads.get('ad_attributed_orders', 0))):.2f}",
                "ad_attributed_revenue_prev": f"{q2(prev_ads.get('ad_attributed_revenue', 0)):.2f}",
                "ad_attributed_revenue_curr": f"{q2(curr_ads.get('ad_attributed_revenue', 0)):.2f}",
                "ad_attributed_revenue_delta": f"{(q2(curr_ads.get('ad_attributed_revenue', 0)) - q2(prev_ads.get('ad_attributed_revenue', 0))):.2f}",
                "logistics_prev": f"{q2(prev_ops.get('logistics_total', 0)):.2f}",
                "logistics_curr": f"{q2(curr_ops.get('logistics_total', 0)):.2f}",
                "logistics_delta": f"{(q2(curr_ops.get('logistics_total', 0)) - q2(prev_ops.get('logistics_total', 0))):.2f}",
                "storage_prev": f"{q2(prev_ops.get('storage_total', 0)):.2f}",
                "storage_curr": f"{q2(curr_ops.get('storage_total', 0)):.2f}",
                "storage_delta": f"{(q2(curr_ops.get('storage_total', 0)) - q2(prev_ops.get('storage_total', 0))):.2f}",
                "acquiring_prev": f"{q2(prev_ops.get('acquiring_total', 0)):.2f}",
                "acquiring_curr": f"{q2(curr_ops.get('acquiring_total', 0)):.2f}",
                "acquiring_delta": f"{(q2(curr_ops.get('acquiring_total', 0)) - q2(prev_ops.get('acquiring_total', 0))):.2f}",
                "commission_prev": f"{q2(prev_ops.get('commission_total', 0)):.2f}",
                "commission_curr": f"{q2(curr_ops.get('commission_total', 0)):.2f}",
                "commission_delta": f"{(q2(curr_ops.get('commission_total', 0)) - q2(prev_ops.get('commission_total', 0))):.2f}",
                "other_product_expenses_prev": f"{q2(prev_ops.get('other_product_expenses_total', 0)):.2f}",
                "other_product_expenses_curr": f"{q2(curr_ops.get('other_product_expenses_total', 0)):.2f}",
                "other_product_expenses_delta": f"{(q2(curr_ops.get('other_product_expenses_total', 0)) - q2(prev_ops.get('other_product_expenses_total', 0))):.2f}",
                "stock_days_limited_prev": int(prev_stock.get("stock_days_limited", 0)),
                "stock_days_limited_curr": int(curr_stock.get("stock_days_limited", 0)),
            }
        )
    return all_products, signals_rows, signals_index


def build_flags_and_unit_econ(all_products: list[dict[str, Any]], signals_index: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flags_rows: list[dict[str, Any]] = []
    econ_rows: list[dict[str, Any]] = []
    for row in all_products:
        nkey = row["normalized_product_key"]
        sales_prev = Decimal(row["sales_units_prev"])
        sales_curr = Decimal(row["sales_units_curr"])
        orders_prev = Decimal(row["orders_prev"])
        orders_curr = Decimal(row["orders_curr"])
        result_prev = Decimal(row["result_prev"])
        result_curr = Decimal(row["result_curr"])
        avg_price_prev = Decimal(row["avg_realized_price_prev"])
        avg_price_curr = Decimal(row["avg_realized_price_curr"])
        ads_prev = Decimal(row["ads_spend_prev"])
        ads_curr = Decimal(row["ads_spend_curr"])
        logistics_prev = Decimal(row["logistics_prev"])
        logistics_curr = Decimal(row["logistics_curr"])
        storage_prev = Decimal(row["storage_prev"])
        storage_curr = Decimal(row["storage_curr"])
        expenses_prev = Decimal(row["expenses_prev"])
        expenses_curr = Decimal(row["expenses_curr"])
        attr_rev_prev = Decimal(row["ad_attributed_revenue_prev"])
        attr_rev_curr = Decimal(row["ad_attributed_revenue_curr"])
        attr_orders_prev = Decimal(row["ad_attributed_orders_prev"])
        attr_orders_curr = Decimal(row["ad_attributed_orders_curr"])
        roas_prev = Decimal("0.00") if ads_prev == 0 else safe_div_dec(attr_rev_prev, ads_prev)
        roas_curr = Decimal("0.00") if ads_curr == 0 else safe_div_dec(attr_rev_curr, ads_curr)
        cpa_prev = Decimal("0.00") if attr_orders_prev == 0 else safe_div_dec(ads_prev, attr_orders_prev)
        cpa_curr = Decimal("0.00") if attr_orders_curr == 0 else safe_div_dec(ads_curr, attr_orders_curr)

        sales_units_down = severity_down(sales_prev, sales_curr)
        orders_down = severity_down(orders_prev, orders_curr)
        avg_price_down = severity_down(avg_price_prev, avg_price_curr)
        result_down = severity_down(result_prev, result_curr)
        ads_spend_up = severity_up(ads_prev, ads_curr)
        if ads_prev > 0 and ads_curr > 0 and roas_curr < roas_prev:
            ad_efficiency_down = severity_down(roas_prev, roas_curr)
        elif ads_curr > 0 and attr_orders_curr == 0:
            ad_efficiency_down = "high"
        elif cpa_prev > 0 and cpa_curr > cpa_prev:
            ad_efficiency_down = severity_up(cpa_prev, cpa_curr)
        else:
            ad_efficiency_down = "none"
        logistics_up = severity_up(logistics_prev, logistics_curr)
        storage_up = severity_up(storage_prev, storage_curr)
        epu_prev = Decimal("0.00") if sales_prev == 0 else safe_div_dec(expenses_prev, sales_prev)
        epu_curr = Decimal("0.00") if sales_curr == 0 else safe_div_dec(expenses_curr, sales_curr)
        cost_per_unit_up = severity_up(epu_prev, epu_curr)
        stock_limited = severity_count(int(row["stock_days_limited_curr"]))

        signal = signals_index[nkey]
        rapid_7d_drop = severity_down(Decimal(signal["result_prev_7d"]), Decimal(signal["result_curr_7d"]))
        sharp_1d_signal = "high" if signal["sharp_1d_signal"] == "yes" else "none"

        flags_rows.append(
            {
                "normalized_product_key": nkey,
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "sales_units_down": sales_units_down,
                "orders_down": orders_down,
                "avg_price_down": avg_price_down,
                "result_down": result_down,
                "ads_spend_up": ads_spend_up,
                "ad_efficiency_down": ad_efficiency_down,
                "logistics_up": logistics_up,
                "storage_up": storage_up,
                "cost_per_unit_up": cost_per_unit_up,
                "stock_limited": stock_limited,
                "rapid_7d_drop": rapid_7d_drop,
                "sharp_1d_signal": sharp_1d_signal,
            }
        )

        econ_rows.append(
            {
                "normalized_product_key": nkey,
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "expense_per_sold_unit_prev": decimal_or_blank(expenses_prev, sales_prev),
                "expense_per_sold_unit_curr": decimal_or_blank(expenses_curr, sales_curr),
                "expense_per_sold_unit_delta": "unavailable" if sales_prev == 0 or sales_curr == 0 else f"{(epu_curr - epu_prev):.2f}",
                "logistics_per_sold_unit_prev": decimal_or_blank(logistics_prev, sales_prev),
                "logistics_per_sold_unit_curr": decimal_or_blank(logistics_curr, sales_curr),
                "storage_per_sold_unit_prev": decimal_or_blank(storage_prev, sales_prev),
                "storage_per_sold_unit_curr": decimal_or_blank(storage_curr, sales_curr),
                "ads_per_sold_unit_prev": decimal_or_blank(ads_prev, sales_prev),
                "ads_per_sold_unit_curr": decimal_or_blank(ads_curr, sales_curr),
                "result_per_sold_unit_prev": decimal_or_blank(result_prev, sales_prev),
                "result_per_sold_unit_curr": decimal_or_blank(result_curr, sales_curr),
            }
        )
    return flags_rows, econ_rows


def shortlist_products(all_products: list[dict[str, Any]], flags_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags_index = {row["normalized_product_key"]: row for row in flags_rows}
    weight_map = {
        "result_down": 12,
        "sales_units_down": 10,
        "orders_down": 8,
        "avg_price_down": 7,
        "ad_efficiency_down": 9,
        "ads_spend_up": 5,
        "logistics_up": 6,
        "storage_up": 4,
        "cost_per_unit_up": 8,
        "stock_limited": 9,
        "rapid_7d_drop": 8,
        "sharp_1d_signal": 7,
    }
    rows: list[dict[str, Any]] = []
    for product in all_products:
        if is_placeholder(norm_text(product["offer_id"])) and is_placeholder(norm_text(product["product_name"])):
            continue
        flags = flags_index[product["normalized_product_key"]]
        scored: list[tuple[int, str, str]] = []
        actionability = 0
        for key, base_weight in weight_map.items():
            sev = flags[key]
            pts = base_weight * severity_points(sev)
            if pts > 0:
                actionability += pts
                scored.append((pts, key, sev))
        scored.sort(reverse=True)
        top_codes = [item[1] for item in scored[:3]]
        if not scored:
            continue
        result_delta = Decimal(product["result_delta"])
        attention_class = "negative_28d" if result_delta < 0 else "signal_watch"
        rows.append(
            {
                "normalized_product_key": product["normalized_product_key"],
                "product_name": product["product_name"],
                "offer_id": product["offer_id"],
                "sku": product["sku"],
                "group_name": product["group_name"],
                "result_delta": product["result_delta"],
                "attention_class": attention_class,
                "main_negative_driver_1": top_codes[0] if len(top_codes) > 0 else "",
                "main_negative_driver_2": top_codes[1] if len(top_codes) > 1 else "",
                "main_negative_driver_3": top_codes[2] if len(top_codes) > 2 else "",
                "problem_flags": ",".join(f"{k}:{flags[k]}" for _, k, _ in scored[:6]),
                "actionability_score": actionability,
                "is_priority_yes_no": "yes" if actionability >= 20 or result_delta < 0 else "no",
            }
        )
    rows.sort(
        key=lambda row: (
            0 if row["attention_class"] == "negative_28d" else 1,
            -row["actionability_score"],
            Decimal(row["result_delta"]),
        )
    )
    return rows[:20]


def build_store_only_28d(window: Any) -> list[dict[str, Any]]:
    base_rows = build_store_only_rows(window)
    result_delta = fetch_store_horizon(build_horizon(window, 28))
    total_delta = result_delta["result_curr"] - result_delta["result_prev"]
    out: list[dict[str, Any]] = []
    for row in base_rows:
        delta = Decimal(row["delta"])
        share = Decimal("0.0000") if total_delta == 0 else (delta / total_delta).quantize(Decimal("0.0001"))
        out.append(
            {
                "article_name": row["article_name"],
                "prev": row["prev"],
                "curr": row["curr"],
                "delta": row["delta"],
                "share_of_result_delta": f"{share:.4f}",
            }
        )
    return out


def build_state_history(normalized_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    identity = latest_product_identity()
    cards = current_card_snapshot_by_nm()
    rows: list[dict[str, Any]] = []
    for product in normalized_products:
        offer_norm = norm_text(product["offer_id"])
        meta = identity.get(offer_norm)
        nm_id = str(meta["nm_id"]) if meta and meta.get("nm_id") is not None else ""
        card = cards.get(nm_id) if nm_id else None
        rows.append(
            {
                "normalized_product_key": product["normalized_product_key"],
                "offer_id": product["offer_id"],
                "sku": product["sku"],
                "product_name": product["product_name"],
                "nm_id": nm_id or "unavailable",
                "rating_start": "unavailable",
                "rating_end": fmt(q2(card["rating"])) if card and card.get("rating") is not None else "unavailable",
                "review_count_start": "unavailable",
                "review_count_end": fmt(q2(card["feedbacks"])) if card and card.get("feedbacks") is not None else "unavailable",
                "avg_position_start": "unavailable",
                "avg_position_end": "unavailable",
                "state_history_note": (
                    f"only current card snapshot at {card['captured_at'].isoformat()}" if card else "historical state timeline unavailable"
                ),
            }
        )
    return rows


def expense_depth_for_selected(window: Any, grouped: dict[str, list[dict[str, Any]]], all_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    top_minus = sorted(all_products, key=lambda row: Decimal(row["result_delta"]))[:10]
    top_plus = sorted(all_products, key=lambda row: Decimal(row["result_delta"]), reverse=True)[:10]
    selected = top_minus + top_plus
    by_key = {row["normalized_product_key"]: row for row in selected}

    h28 = build_horizon(window, 28)
    ads28 = fetch_ads_period_metrics(h28)
    stock = load_stock_metrics([{"sku": row["sku"], "offer_id": row["offer_id"]} for row in selected], h28)
    rows: list[dict[str, Any]] = []

    # Generic current-vs-previous subtype extraction from finance-linked attribution rows.
    for nkey, product in by_key.items():
        members = grouped[nkey]
        cpk_list = [row["canonical_product_key"] for row in members]
        sku = str(product["sku"])
        prev_ads = ads28.get((sku, "previous"), {})
        curr_ads = ads28.get((sku, "current"), {})
        revenue_prev = Decimal(product["revenue_prev"])
        revenue_curr = Decimal(product["revenue_curr"])
        ads_spend_prev = Decimal(product["ads_spend_prev"])
        ads_spend_curr = Decimal(product["ads_spend_curr"])
        attr_orders_prev = q2(prev_ads.get("ad_attributed_orders", 0))
        attr_orders_curr = q2(curr_ads.get("ad_attributed_orders", 0))
        attr_rev_prev = q2(prev_ads.get("ad_attributed_revenue", 0))
        attr_rev_curr = q2(curr_ads.get("ad_attributed_revenue", 0))
        views_prev = q2(prev_ads.get("views", 0))
        views_curr = q2(curr_ads.get("views", 0))
        clicks_prev = q2(prev_ads.get("clicks", 0))
        clicks_curr = q2(curr_ads.get("clicks", 0))
        sales_prev = Decimal(product["sales_units_prev"])
        sales_curr = Decimal(product["sales_units_curr"])
        curr_stock = stock.get((sku, "current"), {})

        rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "expense_area": "ads",
                "subtype_name": "overall",
                "prev": f"{ads_spend_prev:.2f}",
                "curr": f"{ads_spend_curr:.2f}",
                "delta": f"{(ads_spend_curr - ads_spend_prev):.2f}",
                "metric_note": (
                    f"views_prev={views_prev:.2f}; views_curr={views_curr:.2f}; "
                    f"clicks_prev={clicks_prev:.2f}; clicks_curr={clicks_curr:.2f}; "
                    f"ctr_prev={(safe_div_dec(clicks_prev, views_prev) if views_prev else Decimal('0.00')):.2f}; "
                    f"ctr_curr={(safe_div_dec(clicks_curr, views_curr) if views_curr else Decimal('0.00')):.2f}; "
                    f"attributed_orders_prev={attr_orders_prev:.2f}; attributed_orders_curr={attr_orders_curr:.2f}; "
                    f"attributed_revenue_prev={attr_rev_prev:.2f}; attributed_revenue_curr={attr_rev_curr:.2f}; "
                    f"cost_per_attributed_order_prev={(safe_div_dec(ads_spend_prev, attr_orders_prev) if attr_orders_prev else Decimal('0.00')):.2f}; "
                    f"cost_per_attributed_order_curr={(safe_div_dec(ads_spend_curr, attr_orders_curr) if attr_orders_curr else Decimal('0.00')):.2f}; "
                    f"ad_share_of_revenue_prev={(safe_div_dec(attr_rev_prev, revenue_prev) if revenue_prev else Decimal('0.00')):.2f}; "
                    f"ad_share_of_revenue_curr={(safe_div_dec(attr_rev_curr, revenue_curr) if revenue_curr else Decimal('0.00')):.2f}"
                ),
            }
        )
        rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "expense_area": "logistics",
                "subtype_name": "logistics_core",
                "prev": product["logistics_prev"],
                "curr": product["logistics_curr"],
                "delta": product["logistics_delta"],
                "metric_note": (
                    f"logistics_per_unit_prev={decimal_or_blank(Decimal(product['logistics_prev']), sales_prev)}; "
                    f"logistics_per_unit_curr={decimal_or_blank(Decimal(product['logistics_curr']), sales_curr)}"
                ),
            }
        )
        rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "expense_area": "storage",
                "subtype_name": "storage_total",
                "prev": product["storage_prev"],
                "curr": product["storage_curr"],
                "delta": product["storage_delta"],
                "metric_note": (
                    f"storage_per_unit_prev={decimal_or_blank(Decimal(product['storage_prev']), sales_prev)}; "
                    f"storage_per_unit_curr={decimal_or_blank(Decimal(product['storage_curr']), sales_curr)}; "
                    f"limited_stock_days_curr={curr_stock.get('stock_days_limited', 0)}; "
                    f"warehouse_split={'limited_main_warehouses' if curr_stock.get('main_warehouses_issue_flag') else 'no_main_wh_issue'}"
                ),
            }
        )

        subtype_rows = fetch_all(
            f"""
            WITH finance_link AS (
              SELECT
                a.event_date,
                a.money_bucket,
                a.amount,
                a.canonical_product_key,
                COALESCE(f.operation_type, '') AS operation_type,
                COALESCE(f.supplier_oper_name, '') AS supplier_oper_name,
                COALESCE(f.doc_type_name, '') AS doc_type_name,
                COALESCE(f.raw_payload->>'bonus_type_name', '') AS bonus_type_name
              FROM {SCHEMA}.fact_product_money_attribution a
              LEFT JOIN {SCHEMA}.stg_wb_finance f
                ON a.source_table = 'stg_wb_finance'
               AND split_part(a.source_record_id, ':', 1) = f.finance_id
              WHERE a.marketplace='wb'
                AND a.event_date BETWEEN %s AND %s
                AND a.canonical_product_key = ANY(%s)
                AND a.source_table = 'stg_wb_finance'
                AND a.money_bucket IN ('other','services','penalties','logistics','storage')
            ),
            classified AS (
              SELECT
                CASE
                  WHEN money_bucket='other'
                    AND operation_type='Продажа'
                    AND supplier_oper_name='Продажа'
                    THEN 'ppvz_reward_sale_flow'
                  WHEN money_bucket='other'
                    AND operation_type='Возмещение издержек по перевозке/по складским операциям с товаром'
                    THEN 'transport_warehouse_reimbursement'
                  WHEN money_bucket='other'
                    AND supplier_oper_name='Возмещение за выдачу и возврат товаров на ПВЗ'
                    THEN 'pvz_issue_return_reimbursement'
                  WHEN money_bucket='services'
                    THEN COALESCE(NULLIF(bonus_type_name,''), NULLIF(supplier_oper_name,''), 'services_other')
                  WHEN money_bucket='penalties'
                    THEN COALESCE(NULLIF(bonus_type_name,''), NULLIF(supplier_oper_name,''), 'penalties_other')
                  WHEN money_bucket='logistics'
                    THEN COALESCE(NULLIF(supplier_oper_name,''), 'logistics_other')
                  WHEN money_bucket='storage'
                    THEN COALESCE(NULLIF(bonus_type_name,''), 'storage_other')
                  ELSE money_bucket || '_other'
                END AS subtype_name,
                CASE
                  WHEN event_date BETWEEN %s AND %s THEN 'current'
                  WHEN event_date BETWEEN %s AND %s THEN 'previous'
                END AS period_label,
                CASE
                  WHEN money_bucket='other' AND operation_type='Возмещение издержек по перевозке/по складским операциям с товаром' THEN 'logistics'
                  WHEN money_bucket='other' AND supplier_oper_name='Возмещение за выдачу и возврат товаров на ПВЗ' THEN 'logistics'
                  WHEN money_bucket='storage' THEN 'storage'
                  WHEN money_bucket='logistics' THEN 'logistics'
                  ELSE 'other_product_expenses'
                END AS expense_area,
                ABS(amount)::numeric(18,2) AS amount
              FROM finance_link
            )
            SELECT expense_area, subtype_name,
                   COALESCE(SUM(CASE WHEN period_label='previous' THEN amount ELSE 0 END),0)::numeric(18,2) AS prev,
                   COALESCE(SUM(CASE WHEN period_label='current' THEN amount ELSE 0 END),0)::numeric(18,2) AS curr
            FROM classified
            GROUP BY expense_area, subtype_name
            HAVING COALESCE(SUM(CASE WHEN period_label='previous' THEN amount ELSE 0 END),0) <> 0
                OR COALESCE(SUM(CASE WHEN period_label='current' THEN amount ELSE 0 END),0) <> 0
            ORDER BY expense_area, subtype_name
            """,
            (
                h28.previous_start,
                h28.current_end,
                cpk_list,
                h28.current_start,
                h28.current_end,
                h28.previous_start,
                h28.previous_end,
            ),
        )
        for sub in subtype_rows:
            prev = q2(sub["prev"])
            curr = q2(sub["curr"])
            rows.append(
                {
                    "normalized_product_key": nkey,
                    "product_name": product["product_name"],
                    "expense_area": sub["expense_area"],
                    "subtype_name": sub["subtype_name"],
                    "prev": f"{prev:.2f}",
                    "curr": f"{curr:.2f}",
                    "delta": f"{(curr - prev):.2f}",
                    "metric_note": "finance-linked subtype",
                }
            )
    return rows


def build_report(
    window: Any,
    store_horizons: list[dict[str, Any]],
    store_signal_1d: list[dict[str, Any]],
    all_products: list[dict[str, Any]],
    flags_rows: list[dict[str, Any]],
    econ_rows: list[dict[str, Any]],
    expense_depth_rows: list[dict[str, Any]],
    shortlist_rows: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    store_only_rows: list[dict[str, Any]],
) -> str:
    h28 = next(row for row in store_horizons if row["horizon"] == "28d")
    h7 = next(row for row in store_horizons if row["horizon"] == "7d")
    top_problem = shortlist_rows[:10]
    top_signals = [row for row in store_signal_1d if row["is_significant_after_outlier_cleaning"] == "yes"][:10]
    unavailable_history = sum(1 for row in history_rows if row["rating_end"] == "unavailable")
    return "\n".join(
        [
            "# Этап. Полный пакет данных для симуляции рекомендаций по магазину и товару",
            "",
            "## 1. Цель этапа",
            "Собрать полный data-пакет, которого хватит для ручной симуляции сильных рекомендаций по магазину, товару, статье расхода и сигналам 28/7/1 день, не меняя кабинет и не подменяя данные текстовыми рекомендациями.",
            "",
            "## 2. Что сделано",
            "- Собраны магазинные горизонты `28d` и `7d` по финальным confirmed данным.",
            "- Собран 1-дневный store signal с baseline после outlier cleaning.",
            "- Пересобрана полная таблица товаров на 28-дневом горизонте уже по `normalized_product_key`.",
            "- Добавлены продуктовые признаки проблем, 7-дневные и 1-дневные сигналы.",
            "- Добавлена таблица удельной экономики товара на единицу.",
            "- Собрана depth-таблица по рекламе, логистике, хранению и raw expense subtypes для топ-товаров.",
            "- Собран shortlist товаров для recommendation simulation с драйверами в виде кодов.",
            "- Собрана таблица state/history полей с честной маркировкой `unavailable`, где исторического слоя нет.",
            "",
            "## 3. Что не трогалось",
            "- Кабинет не менялся.",
            "- Старые marts не менялись.",
            "- Текущая confirmed математика не переписывалась.",
            "- Текстовые рекомендации не генерировались вместо данных.",
            "",
            "## 4. Главный результат",
            f"Пакет теперь покрывает магазинные горизонты `28/7/1`, полный normalized product layer, признаки проблем, unit economics и expense depth для приоритетных SKU. Для рекомендаций по экономике и операционным провалам данных уже хватает; главный оставшийся пробел — исторический timeline по rating/reviews и полноценный `avg_position`, который в текущем confirmed-пакете отсутствует.",
            "",
            "## 5. Магазин: 28 дней vs предыдущие 28 дней",
            md_table(
                [
                    {
                        "period": "previous",
                        "revenue_total": f"{h28['revenue_prev']:.2f}",
                        "expenses_total": f"{h28['expenses_prev']:.2f}",
                        "result_total": f"{h28['result_prev']:.2f}",
                        "ads_total": f"{h28['ads_prev']:.2f}",
                        "commission_total": f"{h28['commission_prev']:.2f}",
                        "logistics_total": f"{h28['logistics_prev']:.2f}",
                        "storage_total": f"{h28['storage_prev']:.2f}",
                        "acquiring_total": f"{h28['acquiring_prev']:.2f}",
                        "returns_total": f"{h28['returns_prev']:.2f}",
                        "other_product_expenses_total": f"{h28['other_product_expenses_prev']:.2f}",
                        "store_only_expenses_total": f"{h28['store_only_expenses_prev']:.2f}",
                        "confirmed_through": h28["confirmed_through"],
                    },
                    {
                        "period": "current",
                        "revenue_total": f"{h28['revenue_curr']:.2f}",
                        "expenses_total": f"{h28['expenses_curr']:.2f}",
                        "result_total": f"{h28['result_curr']:.2f}",
                        "ads_total": f"{h28['ads_curr']:.2f}",
                        "commission_total": f"{h28['commission_curr']:.2f}",
                        "logistics_total": f"{h28['logistics_curr']:.2f}",
                        "storage_total": f"{h28['storage_curr']:.2f}",
                        "acquiring_total": f"{h28['acquiring_curr']:.2f}",
                        "returns_total": f"{h28['returns_curr']:.2f}",
                        "other_product_expenses_total": f"{h28['other_product_expenses_curr']:.2f}",
                        "store_only_expenses_total": f"{h28['store_only_expenses_curr']:.2f}",
                        "confirmed_through": h28["confirmed_through"],
                    },
                ],
                ["period", "revenue_total", "expenses_total", "result_total", "ads_total", "commission_total", "logistics_total", "storage_total", "acquiring_total", "returns_total", "other_product_expenses_total", "store_only_expenses_total", "confirmed_through"],
            ),
            "",
            "## 6. Магазин: 7 дней vs предыдущие 7 дней",
            md_table(
                [
                    {
                        "period": "previous",
                        "revenue_total": f"{h7['revenue_prev']:.2f}",
                        "expenses_total": f"{h7['expenses_prev']:.2f}",
                        "result_total": f"{h7['result_prev']:.2f}",
                        "ads_total": f"{h7['ads_prev']:.2f}",
                        "commission_total": f"{h7['commission_prev']:.2f}",
                        "logistics_total": f"{h7['logistics_prev']:.2f}",
                        "storage_total": f"{h7['storage_prev']:.2f}",
                        "acquiring_total": f"{h7['acquiring_prev']:.2f}",
                        "returns_total": f"{h7['returns_prev']:.2f}",
                        "other_product_expenses_total": f"{h7['other_product_expenses_prev']:.2f}",
                        "store_only_expenses_total": f"{h7['store_only_expenses_prev']:.2f}",
                        "confirmed_through": h7["confirmed_through"],
                    },
                    {
                        "period": "current",
                        "revenue_total": f"{h7['revenue_curr']:.2f}",
                        "expenses_total": f"{h7['expenses_curr']:.2f}",
                        "result_total": f"{h7['result_curr']:.2f}",
                        "ads_total": f"{h7['ads_curr']:.2f}",
                        "commission_total": f"{h7['commission_curr']:.2f}",
                        "logistics_total": f"{h7['logistics_curr']:.2f}",
                        "storage_total": f"{h7['storage_curr']:.2f}",
                        "acquiring_total": f"{h7['acquiring_curr']:.2f}",
                        "returns_total": f"{h7['returns_curr']:.2f}",
                        "other_product_expenses_total": f"{h7['other_product_expenses_curr']:.2f}",
                        "store_only_expenses_total": f"{h7['store_only_expenses_curr']:.2f}",
                        "confirmed_through": h7["confirmed_through"],
                    },
                ],
                ["period", "revenue_total", "expenses_total", "result_total", "ads_total", "commission_total", "logistics_total", "storage_total", "acquiring_total", "returns_total", "other_product_expenses_total", "store_only_expenses_total", "confirmed_through"],
            ),
            "",
            "## 7. Store 1-Day Signal",
            md_table(
                top_signals,
                ["metric_name", "baseline_day_value", "current_day_value", "deviation", "deviation_pct", "is_significant_after_outlier_cleaning"],
            ),
            "",
            "## 8. Priority Product Shortlist",
            md_table(
                top_problem,
                ["normalized_product_key", "product_name", "result_delta", "main_negative_driver_1", "main_negative_driver_2", "main_negative_driver_3", "actionability_score", "is_priority_yes_no"],
            ),
            "",
            "## 9. Strong Signals Already Available",
            "- Revenue/result direction on `28d` and `7d` windows is already strong enough for hard store and SKU recommendations.",
            "- Cost pressure signals are strong where `logistics_per_unit`, `expense_per_sold_unit`, `ads_efficiency_down`, `stock_limited` light up together.",
            "- Store-only costs are isolated and have result-delta share, so they can drive separate store-level actions.",
            "",
            "## 10. Gaps And Honest Limits",
            f"- `rating/reviews` history start values are unavailable for most products; only current card snapshot exists for a subset (`{len(history_rows) - unavailable_history}` with current snapshot, `{unavailable_history}` unavailable).",
            "- `avg_position` historical layer is unavailable in the current confirmed pack, so search-visibility recommendations would still be weak.",
            "- Ads-attributed orders/revenue remains a directional signal, not sale-by-sale accounting truth.",
            "",
            "## 11. What Recommendations Can Already Be Hard",
            "- Store-level: revenue up/down vs expense pressure by article on `28d`, `7d`, `1d`.",
            "- Product-level: sales volume drop, price drop, ad efficiency deterioration, logistics/storage burden, stock limitation.",
            "- Expense-level: explicit ad/logistics/storage/other subtype pressure on priority SKU set.",
            "",
            "## 12. What Recommendations Would Still Be Weak",
            "- Search position recovery actions tied to `avg_position` movement.",
            "- Rating/review shock narratives over time, because start-state history is missing.",
            "- Competitor narratives beyond current confirmed economics.",
            "",
            "## 13. Artifacts",
            f"- `[store_horizons.csv]({STORE_HORIZONS_CSV})`",
            f"- `[store_daily_signal_1d.csv]({STORE_1D_SIGNAL_CSV})`",
            f"- `[all_products_28d_normalized.csv]({ALL_PRODUCTS_CSV})`",
            f"- `[product_problem_flags.csv]({PRODUCT_FLAGS_CSV})`",
            f"- `[product_unit_economics.csv]({UNIT_ECONOMICS_CSV})`",
            f"- `[priority_product_expense_depth.csv]({EXPENSE_DEPTH_CSV})`",
            f"- `[recommendation_shortlist.csv]({SHORTLIST_CSV})`",
            f"- `[product_state_metrics_history.csv]({STATE_HISTORY_CSV})`",
            f"- `[store_only_expenses_28d.csv]({STORE_ONLY_CSV})`",
            f"- `[product_signals_28d_7d_1d.csv]({PRODUCT_SIGNALS_CSV})`",
        ]
    ) + "\n"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    window = build_window()
    h28 = build_horizon(window, 28)
    h7 = build_horizon(window, 7)

    store_horizons_raw = [fetch_store_horizon(h28), fetch_store_horizon(h7)]
    store_horizons_rows: list[dict[str, Any]] = []
    for row in store_horizons_raw:
        store_horizons_rows.append(
            {
                "horizon": row["horizon"],
                "current_start": row["current_start"],
                "current_end": row["current_end"],
                "previous_start": row["previous_start"],
                "previous_end": row["previous_end"],
                "revenue_prev": f"{row['revenue_prev']:.2f}",
                "revenue_curr": f"{row['revenue_curr']:.2f}",
                "expenses_prev": f"{row['expenses_prev']:.2f}",
                "expenses_curr": f"{row['expenses_curr']:.2f}",
                "result_prev": f"{row['result_prev']:.2f}",
                "result_curr": f"{row['result_curr']:.2f}",
                "ads_prev": f"{row['ads_prev']:.2f}",
                "ads_curr": f"{row['ads_curr']:.2f}",
                "commission_prev": f"{row['commission_prev']:.2f}",
                "commission_curr": f"{row['commission_curr']:.2f}",
                "logistics_prev": f"{row['logistics_prev']:.2f}",
                "logistics_curr": f"{row['logistics_curr']:.2f}",
                "storage_prev": f"{row['storage_prev']:.2f}",
                "storage_curr": f"{row['storage_curr']:.2f}",
                "acquiring_prev": f"{row['acquiring_prev']:.2f}",
                "acquiring_curr": f"{row['acquiring_curr']:.2f}",
                "returns_prev": f"{row['returns_prev']:.2f}",
                "returns_curr": f"{row['returns_curr']:.2f}",
                "other_product_expenses_prev": f"{row['other_product_expenses_prev']:.2f}",
                "other_product_expenses_curr": f"{row['other_product_expenses_curr']:.2f}",
                "store_only_expenses_prev": f"{row['store_only_expenses_prev']:.2f}",
                "store_only_expenses_curr": f"{row['store_only_expenses_curr']:.2f}",
                "confirmed_through": row["confirmed_through"],
            }
        )

    store_signal_1d = build_store_1d_signal(window)
    normalized_products, grouped = build_normalized_products(window)
    all_products, product_signals, signals_index = enrich_all_products(window, normalized_products)
    flags_rows, econ_rows = build_flags_and_unit_econ(all_products, signals_index)
    shortlist_rows = shortlist_products(all_products, flags_rows)
    history_rows = build_state_history(normalized_products)
    store_only_rows = build_store_only_28d(window)
    expense_depth_rows = expense_depth_for_selected(window, grouped, all_products)

    write_csv(STORE_HORIZONS_CSV, store_horizons_rows)
    write_csv(STORE_1D_SIGNAL_CSV, store_signal_1d)
    write_csv(ALL_PRODUCTS_CSV, all_products)
    write_csv(PRODUCT_FLAGS_CSV, flags_rows)
    write_csv(UNIT_ECONOMICS_CSV, econ_rows)
    write_csv(EXPENSE_DEPTH_CSV, expense_depth_rows)
    write_csv(SHORTLIST_CSV, shortlist_rows)
    write_csv(STATE_HISTORY_CSV, history_rows)
    write_csv(STORE_ONLY_CSV, store_only_rows)
    write_csv(PRODUCT_SIGNALS_CSV, product_signals)

    print(f"Wrote {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
