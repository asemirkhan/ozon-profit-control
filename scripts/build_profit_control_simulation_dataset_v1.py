#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo"
OUTPUT_DIR = ROOT / "artifacts" / "profit_control_simulation_dataset_v1"
REPORT_PATH = ROOT / "profit_control_simulation_dataset_v1_report.md"
HANDOFF_PATH = ROOT / "profit_control_simulation_dataset_v1_handoff.md"

STORE_CSV = OUTPUT_DIR / "store_two_periods.csv"
ARTICLES_CSV = OUTPUT_DIR / "result_articles.csv"
TOP_PRODUCTS_CSV = OUTPUT_DIR / "top_products_result_delta.csv"
TOP_GROUPS_CSV = OUTPUT_DIR / "top_groups_result_delta.csv"
DETAIL_CSV = OUTPUT_DIR / "selected_products_detail.csv"
STORE_ONLY_CSV = OUTPUT_DIR / "store_only_expenses.csv"
WEEKLY_CSV = OUTPUT_DIR / "store_weekly_tempo.csv"
MTD_CSV = OUTPUT_DIR / "current_month_tempo.csv"
QUALITY_CSV = OUTPUT_DIR / "quality_notes.csv"

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class Window:
    confirmed_through: date
    current_start: date
    current_end: date
    previous_start: date
    previous_end: date


def q2(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(MONEY)
    return Decimal(str(value)).quantize(MONEY)


def safe_div(num: Decimal, den: Decimal) -> Decimal:
    if den == 0:
        return Decimal("0.00")
    return (num / den).quantize(MONEY)


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


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = fetch_all(sql, params)
    return rows[0] if rows else {}


def build_window() -> Window:
    row = fetch_one(
        f"""
        SELECT MAX(day)::date AS confirmed_through
        FROM {SCHEMA}.finance_day_state
        WHERE final_analytic_usable_flag = TRUE
        """
    )
    confirmed = row["confirmed_through"]
    if confirmed is None:
        raise RuntimeError("No finance data available for analysis")
    current_end = confirmed
    current_start = current_end - timedelta(days=27)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=27)
    return Window(
        confirmed_through=confirmed,
        current_start=current_start,
        current_end=current_end,
        previous_start=previous_start,
        previous_end=previous_end,
    )


def load_group_map() -> dict[str, str]:
    rows = fetch_all(
        f"""
        WITH latest_bridge AS (
          SELECT *,
                 ROW_NUMBER() OVER (
                   PARTITION BY canonical_product_key
                   ORDER BY updated_at DESC, bridge_id DESC
                 ) AS rn
          FROM {SCHEMA}.dim_product_bridge
          WHERE marketplace = 'wb'
        )
        SELECT canonical_product_key, COALESCE(NULLIF(category_name, ''), '~') AS group_name
        FROM latest_bridge
        WHERE rn = 1
        """
    )
    return {row["canonical_product_key"]: row["group_name"] for row in rows}


def load_store_rows(window: Window) -> list[dict[str, Any]]:
    row = fetch_one(
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
          MIN(date) AS period_start,
          MAX(date) AS period_end,
          MAX(confirmed_through) AS confirmed_through,
          SUM(revenue_gross)::numeric(18,2) AS revenue_total,
          SUM(cogs_amount)::numeric(18,2) AS cogs_total,
          SUM(ads_amount)::numeric(18,2) AS ads_total,
          SUM(commission_amount)::numeric(18,2) AS commission_total,
          SUM(logistics_amount)::numeric(18,2) AS logistics_total,
          SUM(storage_amount)::numeric(18,2) AS storage_total,
          SUM(acquiring_amount)::numeric(18,2) AS acquiring_total,
          SUM(returns_amount)::numeric(18,2) AS returns_total,
          SUM(other_expenses_amount + product_services_amount + penalties_amount)::numeric(18,2) AS other_product_expenses_total,
          SUM(store_level_expense_amount)::numeric(18,2) AS store_only_expenses_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total
        FROM base
        GROUP BY period_label
        ORDER BY period_label
        """,
        (
            window.current_start,
            window.current_end,
            window.previous_start,
            window.previous_end,
            window.previous_start,
            window.current_end,
        ),
    )
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
          MIN(date) AS period_start,
          MAX(date) AS period_end,
          MAX(confirmed_through) AS confirmed_through,
          SUM(revenue_gross)::numeric(18,2) AS revenue_total,
          SUM(cogs_amount)::numeric(18,2) AS cogs_total,
          SUM(ads_amount)::numeric(18,2) AS ads_total,
          SUM(commission_amount)::numeric(18,2) AS commission_total,
          SUM(logistics_amount)::numeric(18,2) AS logistics_total,
          SUM(storage_amount)::numeric(18,2) AS storage_total,
          SUM(acquiring_amount)::numeric(18,2) AS acquiring_total,
          SUM(returns_amount)::numeric(18,2) AS returns_total,
          SUM(other_expenses_amount + product_services_amount + penalties_amount)::numeric(18,2) AS other_product_expenses_total,
          SUM(store_level_expense_amount)::numeric(18,2) AS store_only_expenses_total,
          SUM(profit_after_ads)::numeric(18,2) AS result_total
        FROM base
        GROUP BY period_label
        ORDER BY CASE period_label WHEN 'previous' THEN 1 ELSE 2 END
        """,
        (
            window.current_start,
            window.current_end,
            window.previous_start,
            window.previous_end,
            window.previous_start,
            window.current_end,
        ),
    )
    return rows


def build_result_articles(store_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed = {row["period_label"]: row for row in store_rows}
    prev = keyed["previous"]
    curr = keyed["current"]
    result_delta = q2(curr["result_total"]) - q2(prev["result_total"])
    article_specs = [
        ("revenue", "revenue_total", 1),
        ("ads", "ads_total", -1),
        ("commission", "commission_total", -1),
        ("logistics", "logistics_total", -1),
        ("storage", "storage_total", -1),
        ("acquiring", "acquiring_total", -1),
        ("returns", "returns_total", -1),
        ("other_product_expenses", "other_product_expenses_total", -1),
        ("store_only_expenses", "store_only_expenses_total", -1),
        ("total_result", "result_total", 1),
    ]
    rows: list[dict[str, Any]] = []
    for article_name, field_name, sign in article_specs:
        prev_value = q2(prev[field_name])
        curr_value = q2(curr[field_name])
        delta = curr_value - prev_value
        impact = delta if sign > 0 else -delta
        share = Decimal("0.0000") if result_delta == 0 else (impact / result_delta).quantize(Decimal("0.0001"))
        rows.append(
            {
                "article_name": article_name,
                "prev": f"{prev_value:.2f}",
                "curr": f"{curr_value:.2f}",
                "delta": f"{delta:.2f}",
                "result_impact_delta": f"{impact:.2f}",
                "share_of_result_delta": f"{share:.4f}",
            }
        )
    return rows


def load_product_rows(window: Window, group_map: dict[str, str]) -> list[dict[str, Any]]:
    metric_window_params = (
        window.previous_start,
        window.previous_end,
        window.current_start,
        window.current_end,
    ) * 9
    rows = fetch_all(
        f"""
        WITH product_period AS (
          SELECT
            canonical_product_key,
            MIN(offer_id) AS offer_id,
            MIN(sku) AS sku,
            MIN(product_name) AS product_name,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN revenue_gross ELSE 0 END)::numeric(18,2) AS revenue_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN revenue_gross ELSE 0 END)::numeric(18,2) AS revenue_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN ads_amount ELSE 0 END)::numeric(18,2) AS ads_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN ads_amount ELSE 0 END)::numeric(18,2) AS ads_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN commission_amount ELSE 0 END)::numeric(18,2) AS commission_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN commission_amount ELSE 0 END)::numeric(18,2) AS commission_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN logistics_amount ELSE 0 END)::numeric(18,2) AS logistics_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN logistics_amount ELSE 0 END)::numeric(18,2) AS logistics_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN storage_amount ELSE 0 END)::numeric(18,2) AS storage_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN storage_amount ELSE 0 END)::numeric(18,2) AS storage_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN acquiring_amount ELSE 0 END)::numeric(18,2) AS acquiring_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN acquiring_amount ELSE 0 END)::numeric(18,2) AS acquiring_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN returns_amount ELSE 0 END)::numeric(18,2) AS returns_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN returns_amount ELSE 0 END)::numeric(18,2) AS returns_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN (other_expenses_amount + product_services_amount + penalties_amount) ELSE 0 END)::numeric(18,2) AS other_product_expenses_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN (other_expenses_amount + product_services_amount + penalties_amount) ELSE 0 END)::numeric(18,2) AS other_product_expenses_curr,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN profit_after_ads ELSE 0 END)::numeric(18,2) AS result_prev,
            SUM(CASE WHEN date BETWEEN %s AND %s THEN profit_after_ads ELSE 0 END)::numeric(18,2) AS result_curr
          FROM {SCHEMA}.pc_v2_daily_product_fact
          WHERE marketplace = 'wb'
            AND date BETWEEN %s AND %s
          GROUP BY canonical_product_key
        )
        SELECT *
        FROM product_period
        WHERE COALESCE(revenue_prev, 0) <> 0
           OR COALESCE(revenue_curr, 0) <> 0
           OR COALESCE(result_prev, 0) <> 0
           OR COALESCE(result_curr, 0) <> 0
        """,
        (*metric_window_params, window.previous_start, window.current_end),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        row["group_name"] = group_map.get(row["canonical_product_key"], "~")
        result_prev = q2(row["result_prev"])
        result_curr = q2(row["result_curr"])
        revenue_prev = q2(row["revenue_prev"])
        revenue_curr = q2(row["revenue_curr"])
        expense_prev = (
            q2(row["ads_prev"])
            + q2(row["commission_prev"])
            + q2(row["logistics_prev"])
            + q2(row["storage_prev"])
            + q2(row["acquiring_prev"])
            + q2(row["returns_prev"])
            + q2(row["other_product_expenses_prev"])
        )
        expense_curr = (
            q2(row["ads_curr"])
            + q2(row["commission_curr"])
            + q2(row["logistics_curr"])
            + q2(row["storage_curr"])
            + q2(row["acquiring_curr"])
            + q2(row["returns_curr"])
            + q2(row["other_product_expenses_curr"])
        )
        row["result_delta"] = result_curr - result_prev
        row["revenue_delta"] = revenue_curr - revenue_prev
        row["expense_delta_total"] = expense_curr - expense_prev
        out.append(row)
    return out


def build_top_product_rows(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    down = sorted(products, key=lambda item: (item["result_delta"], item["revenue_delta"]))[:10]
    up = sorted(products, key=lambda item: (item["result_delta"], item["revenue_delta"]), reverse=True)[:10]
    rows: list[dict[str, Any]] = []
    for direction, items in (("minus", down), ("plus", up)):
        for item in items:
            rows.append(
                {
                    "direction": direction,
                    "canonical_product_key": item["canonical_product_key"],
                    "offer_id": item["offer_id"],
                    "sku": item["sku"],
                    "product_name": item["product_name"],
                    "group_name": item["group_name"],
                    "revenue_prev": f"{q2(item['revenue_prev']):.2f}",
                    "revenue_curr": f"{q2(item['revenue_curr']):.2f}",
                    "ads_prev": f"{q2(item['ads_prev']):.2f}",
                    "ads_curr": f"{q2(item['ads_curr']):.2f}",
                    "commission_prev": f"{q2(item['commission_prev']):.2f}",
                    "commission_curr": f"{q2(item['commission_curr']):.2f}",
                    "logistics_prev": f"{q2(item['logistics_prev']):.2f}",
                    "logistics_curr": f"{q2(item['logistics_curr']):.2f}",
                    "storage_prev": f"{q2(item['storage_prev']):.2f}",
                    "storage_curr": f"{q2(item['storage_curr']):.2f}",
                    "acquiring_prev": f"{q2(item['acquiring_prev']):.2f}",
                    "acquiring_curr": f"{q2(item['acquiring_curr']):.2f}",
                    "returns_prev": f"{q2(item['returns_prev']):.2f}",
                    "returns_curr": f"{q2(item['returns_curr']):.2f}",
                    "other_product_expenses_prev": f"{q2(item['other_product_expenses_prev']):.2f}",
                    "other_product_expenses_curr": f"{q2(item['other_product_expenses_curr']):.2f}",
                    "result_prev": f"{q2(item['result_prev']):.2f}",
                    "result_curr": f"{q2(item['result_curr']):.2f}",
                    "result_delta": f"{q2(item['result_delta']):.2f}",
                    "revenue_delta": f"{q2(item['revenue_delta']):.2f}",
                    "expense_delta_total": f"{q2(item['expense_delta_total']):.2f}",
                }
            )
    return rows


def build_group_rows(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    agg: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0.00")))
    for item in products:
        group = item["group_name"] or "~"
        for field in (
            "revenue_prev",
            "revenue_curr",
            "ads_prev",
            "ads_curr",
            "commission_prev",
            "commission_curr",
            "logistics_prev",
            "logistics_curr",
            "storage_prev",
            "storage_curr",
            "acquiring_prev",
            "acquiring_curr",
            "returns_prev",
            "returns_curr",
            "other_product_expenses_prev",
            "other_product_expenses_curr",
            "result_prev",
            "result_curr",
            "result_delta",
            "revenue_delta",
            "expense_delta_total",
        ):
            agg[group][field] += q2(item[field])
    grouped = []
    for group_name, values in agg.items():
        row = {"group_name": group_name}
        for key, value in values.items():
            row[key] = value
        grouped.append(row)
    down = sorted(grouped, key=lambda item: item["result_delta"])[:10]
    up = sorted(grouped, key=lambda item: item["result_delta"], reverse=True)[:10]
    rows: list[dict[str, Any]] = []
    for direction, items in (("minus", down), ("plus", up)):
        for item in items:
            payload = {"direction": direction, "group_name": item["group_name"]}
            for field in item:
                if field == "group_name":
                    continue
                payload[field] = f"{q2(item[field]):.2f}"
            rows.append(payload)
    return rows


def selected_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    down = sorted(products, key=lambda item: item["result_delta"])[:5]
    up = sorted(products, key=lambda item: item["result_delta"], reverse=True)[:3]
    return down + up


def load_ads_period_metrics(window: Window) -> dict[tuple[str, str], dict[str, Decimal]]:
    rows = fetch_all(
        f"""
        SELECT
          sku,
          CASE
            WHEN date BETWEEN %s AND %s THEN 'previous'
            WHEN date BETWEEN %s AND %s THEN 'current'
          END AS period_label,
          SUM(orders_count)::numeric(18,2) AS ad_attributed_orders,
          SUM(revenue)::numeric(18,2) AS ad_attributed_revenue,
          SUM(impressions)::numeric(18,2) AS views,
          SUM(clicks)::numeric(18,2) AS clicks
        FROM {SCHEMA}.mart_ads_daily
        WHERE marketplace = 'wb'
          AND date BETWEEN %s AND %s
        GROUP BY 1,2
        """,
        (
            window.previous_start,
            window.previous_end,
            window.current_start,
            window.current_end,
            window.previous_start,
            window.current_end,
        ),
    )
    result: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row in rows:
        result[(row["sku"], row["period_label"])] = {
            "ad_attributed_orders": q2(row["ad_attributed_orders"]),
            "ad_attributed_revenue": q2(row["ad_attributed_revenue"]),
            "views": q2(row["views"]),
            "clicks": q2(row["clicks"]),
        }
    return result


def load_product_period_operational(window: Window) -> dict[tuple[str, str], dict[str, Decimal]]:
    rows = fetch_all(
        f"""
        SELECT
          sku,
          offer_id,
          canonical_product_key,
          CASE
            WHEN date BETWEEN %s AND %s THEN 'previous'
            WHEN date BETWEEN %s AND %s THEN 'current'
          END AS period_label,
          SUM(orders_count)::numeric(18,2) AS orders_count,
          SUM(sales_count)::numeric(18,2) AS sales_count,
          SUM(units_sold)::numeric(18,2) AS units_sold,
          SUM(revenue_gross)::numeric(18,2) AS revenue_gross,
          SUM(ads_amount)::numeric(18,2) AS ads_amount,
          SUM(profit_after_ads)::numeric(18,2) AS profit_after_ads
        FROM {SCHEMA}.pc_v2_daily_product_fact
        WHERE marketplace = 'wb'
          AND date BETWEEN %s AND %s
        GROUP BY 1,2,3,4
        """,
        (
            window.previous_start,
            window.previous_end,
            window.current_start,
            window.current_end,
            window.previous_start,
            window.current_end,
        ),
    )
    result: dict[tuple[str, str], dict[str, Decimal]] = {}
    for row in rows:
        result[(row["sku"], row["period_label"])] = {
            "orders_count": q2(row["orders_count"]),
            "sales_count": q2(row["sales_count"]),
            "units_sold": q2(row["units_sold"]),
            "revenue_gross": q2(row["revenue_gross"]),
            "ads_amount": q2(row["ads_amount"]),
            "profit_after_ads": q2(row["profit_after_ads"]),
        }
    return result


def load_stock_metrics(selected: list[dict[str, Any]], window: Window) -> dict[tuple[str, str], dict[str, Any]]:
    if not selected:
        return {}
    sku_to_offer = {item["sku"]: item["offer_id"] for item in selected}
    conditions = []
    params: list[Any] = []
    for sku, offer_id in sku_to_offer.items():
        conditions.append("(offer_id = %s OR barcode = %s)")
        params.extend([offer_id, sku])
    rows = fetch_all(
        f"""
        SELECT
          snapshot_date,
          offer_id,
          barcode,
          warehouse_name,
          COALESCE(stock_available, stock_total, 0) AS stock_available,
          COALESCE(stock_total, 0) AS stock_total
        FROM {SCHEMA}.stg_wb_stock
        WHERE marketplace = 'wb'
          AND snapshot_date BETWEEN %s AND %s
          AND ({' OR '.join(conditions)})
        """,
        tuple([window.previous_start, window.current_end, *params]),
    )
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for sku, offer_id in sku_to_offer.items():
        sku_rows = [
            row for row in rows
            if row["offer_id"] == offer_id or row["barcode"] == sku
        ]
        for label, start, end in (
            ("previous", window.previous_start, window.previous_end),
            ("current", window.current_start, window.current_end),
        ):
            period_rows = [row for row in sku_rows if start <= row["snapshot_date"] <= end]
            limited_days = {
                row["snapshot_date"]
                for row in period_rows
                if Decimal(str(row["stock_available"] or 0)) <= 0
                or Decimal(str(row["stock_total"] or 0)) <= 0
            }
            wh_counter: dict[str, int] = defaultdict(int)
            wh_limited: dict[str, int] = defaultdict(int)
            for row in period_rows:
                warehouse = row["warehouse_name"] or "~"
                wh_counter[warehouse] += 1
                if Decimal(str(row["stock_available"] or 0)) <= 0 or Decimal(str(row["stock_total"] or 0)) <= 0:
                    wh_limited[warehouse] += 1
            top_warehouses = {name for name, _ in sorted(wh_counter.items(), key=lambda item: item[1], reverse=True)[:3]}
            main_issue = any(name in top_warehouses and wh_limited.get(name, 0) > 0 for name in top_warehouses)
            result[(sku, label)] = {
                "stock_days_limited": len(limited_days),
                "main_warehouses_issue_flag": main_issue,
            }
    return result


def build_detail_rows(selected: list[dict[str, Any]], window: Window) -> list[dict[str, Any]]:
    ads = load_ads_period_metrics(window)
    ops = load_product_period_operational(window)
    stock = load_stock_metrics(selected, window)
    rows: list[dict[str, Any]] = []
    for item in selected:
        sku = item["sku"]
        prev_ops = ops.get((sku, "previous"), {})
        curr_ops = ops.get((sku, "current"), {})
        prev_ads = ads.get((sku, "previous"), {})
        curr_ads = ads.get((sku, "current"), {})
        prev_stock = stock.get((sku, "previous"), {})
        curr_stock = stock.get((sku, "current"), {})
        orders_prev = q2(prev_ops.get("orders_count"))
        orders_curr = q2(curr_ops.get("orders_count"))
        sales_prev = q2(prev_ops.get("units_sold"))
        sales_curr = q2(curr_ops.get("units_sold"))
        conv_prev = safe_div(q2(prev_ops.get("sales_count")), orders_prev)
        conv_curr = safe_div(q2(curr_ops.get("sales_count")), orders_curr)
        price_prev = safe_div(q2(prev_ops.get("revenue_gross")), sales_prev)
        price_curr = safe_div(q2(curr_ops.get("revenue_gross")), sales_curr)
        rows.append(
            {
                "direction": "down" if q2(item["result_delta"]) < 0 else "up",
                "canonical_product_key": item["canonical_product_key"],
                "offer_id": item["offer_id"],
                "sku": sku,
                "product_name": item["product_name"],
                "group_name": item["group_name"],
                "orders_prev": f"{orders_prev:.2f}",
                "orders_curr": f"{orders_curr:.2f}",
                "sales_units_prev": f"{sales_prev:.2f}",
                "sales_units_curr": f"{sales_curr:.2f}",
                "order_to_sale_conversion_prev": f"{conv_prev:.2f}",
                "order_to_sale_conversion_curr": f"{conv_curr:.2f}",
                "avg_realized_price_prev": f"{price_prev:.2f}",
                "avg_realized_price_curr": f"{price_curr:.2f}",
                "ads_spend_prev": f"{q2(prev_ops.get('ads_amount')):.2f}",
                "ads_spend_curr": f"{q2(curr_ops.get('ads_amount')):.2f}",
                "ad_attributed_orders_prev": f"{q2(prev_ads.get('ad_attributed_orders')):.2f}",
                "ad_attributed_orders_curr": f"{q2(curr_ads.get('ad_attributed_orders')):.2f}",
                "ad_attributed_revenue_prev": f"{q2(prev_ads.get('ad_attributed_revenue')):.2f}",
                "ad_attributed_revenue_curr": f"{q2(curr_ads.get('ad_attributed_revenue')):.2f}",
                "views_prev": f"{q2(prev_ads.get('views')):.2f}",
                "clicks_prev": f"{q2(prev_ads.get('clicks')):.2f}",
                "views_curr": f"{q2(curr_ads.get('views')):.2f}",
                "clicks_curr": f"{q2(curr_ads.get('clicks')):.2f}",
                "avg_position_prev": "",
                "avg_position_curr": "",
                "competitor_context_short": "not_available_in_current_confirmed_layer",
                "stock_days_limited_prev": int(prev_stock.get("stock_days_limited", 0)),
                "stock_days_limited_curr": int(curr_stock.get("stock_days_limited", 0)),
                "main_warehouses_issue_flag": bool(curr_stock.get("main_warehouses_issue_flag", False)),
                "result_prev": f"{q2(item['result_prev']):.2f}",
                "result_curr": f"{q2(item['result_curr']):.2f}",
                "result_delta": f"{q2(item['result_delta']):.2f}",
            }
        )
    return rows


def build_store_only_rows(window: Window) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"""
        WITH base AS (
          SELECT
            a.event_date,
            a.amount,
            CASE
              WHEN lower(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%%джем%%' THEN 'Джем'
              ELSE COALESCE(NULLIF(f.raw_payload->>'bonus_type_name', ''), NULLIF(f.operation_type, ''), NULLIF(f.doc_type_name, ''), NULLIF(f.supplier_oper_name, ''), 'store_only_services')
            END AS article_name
          FROM {SCHEMA}.fact_product_money_attribution a
          LEFT JOIN {SCHEMA}.stg_wb_finance f
            ON a.source_table = 'stg_wb_finance'
           AND split_part(a.source_record_id, ':', 1) = f.finance_id
          WHERE a.marketplace = 'wb'
            AND a.canonical_product_key = '~'
            AND a.money_bucket = 'services'
            AND a.event_date BETWEEN %s AND %s
        )
        SELECT
          article_name,
          SUM(CASE WHEN event_date BETWEEN %s AND %s THEN amount ELSE 0 END)::numeric(18,2) AS prev,
          SUM(CASE WHEN event_date BETWEEN %s AND %s THEN amount ELSE 0 END)::numeric(18,2) AS curr
        FROM base
        GROUP BY article_name
        ORDER BY curr DESC, prev DESC
        """,
        (
            window.previous_start,
            window.current_end,
            window.previous_start,
            window.previous_end,
            window.current_start,
            window.current_end,
        ),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        prev = q2(row["prev"])
        curr = q2(row["curr"])
        out.append(
            {
                "article_name": row["article_name"],
                "prev": f"{prev:.2f}",
                "curr": f"{curr:.2f}",
                "delta": f"{(curr - prev):.2f}",
            }
        )
    return out


def build_weekly_rows(window: Window) -> list[dict[str, Any]]:
    rows = fetch_all(
        f"""
        WITH weekly AS (
          SELECT
            date_trunc('week', date)::date AS week_start,
            (date_trunc('week', date)::date + INTERVAL '6 days')::date AS week_end,
            SUM(revenue_gross)::numeric(18,2) AS revenue,
            SUM(profit_after_ads)::numeric(18,2) AS result,
            SUM(ads_amount)::numeric(18,2) AS ads,
            SUM(orders_count)::numeric(18,2) AS orders,
            SUM(units_sold)::numeric(18,2) AS sales_units
          FROM {SCHEMA}.pc_v2_daily_store_fact
          WHERE marketplace = 'wb'
            AND date <= %s
          GROUP BY 1,2
        )
        SELECT *
        FROM weekly
        WHERE week_end <= %s
        ORDER BY week_start DESC
        LIMIT 8
        """,
        (window.confirmed_through, window.confirmed_through),
    )
    rows.reverse()
    return [
        {
            "week_start": row["week_start"].isoformat(),
            "week_end": row["week_end"].isoformat(),
            "revenue": f"{q2(row['revenue']):.2f}",
            "result": f"{q2(row['result']):.2f}",
            "ads": f"{q2(row['ads']):.2f}",
            "orders": f"{q2(row['orders']):.2f}",
            "sales_units": f"{q2(row['sales_units']):.2f}",
        }
        for row in rows
    ]


def build_mtd_rows(window: Window) -> list[dict[str, Any]]:
    current_month_start = window.confirmed_through.replace(day=1)
    current_len = (window.confirmed_through - current_month_start).days + 1
    previous_month_end = current_month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_same_end = previous_month_start + timedelta(days=current_len - 1)
    rows = fetch_all(
        f"""
        WITH base AS (
          SELECT
            date,
            CASE
              WHEN date BETWEEN %s AND %s THEN 'current_month_mtd'
              WHEN date BETWEEN %s AND %s THEN 'previous_month_same_days'
            END AS period_label,
            revenue_gross,
            profit_after_ads,
            ads_amount,
            orders_count,
            units_sold
          FROM {SCHEMA}.pc_v2_daily_store_fact
          WHERE marketplace = 'wb'
            AND (
              date BETWEEN %s AND %s
              OR date BETWEEN %s AND %s
            )
        )
        SELECT
          period_label,
          date,
          SUM(revenue_gross)::numeric(18,2) AS revenue,
          SUM(profit_after_ads)::numeric(18,2) AS result,
          SUM(ads_amount)::numeric(18,2) AS ads,
          SUM(orders_count)::numeric(18,2) AS orders,
          SUM(units_sold)::numeric(18,2) AS sales_units
        FROM base
        GROUP BY period_label, date
        ORDER BY period_label, date
        """,
        (
            current_month_start,
            window.confirmed_through,
            previous_month_start,
            previous_same_end,
            current_month_start,
            window.confirmed_through,
            previous_month_start,
            previous_same_end,
        ),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "period_label": row["period_label"],
                "date": row["date"].isoformat(),
                "revenue": f"{q2(row['revenue']):.2f}",
                "result": f"{q2(row['result']):.2f}",
                "ads": f"{q2(row['ads']):.2f}",
                "orders": f"{q2(row['orders']):.2f}",
                "sales_units": f"{q2(row['sales_units']):.2f}",
            }
        )
    return out


def quality_rows(window: Window) -> list[dict[str, Any]]:
    return [
        {
            "area": "confirmed_boundary",
            "quality": "high",
            "note": f"All simulation tables are anchored at confirmedThrough={window.confirmed_through.isoformat()}. Days after that exist in source layers but are not treated as financially confirmed.",
        },
        {
            "area": "store_result_metric",
            "quality": "high",
            "note": "Store result_total is profit_after_ads from pc_v2 daily fact layer. This is the current best store-level final metric in the new daily layer.",
        },
        {
            "area": "ads_link_to_product",
            "quality": "medium",
            "note": "Ads spend and ads-attributed orders/revenue are linked to SKU through the WB ads layer and mapped marts. Reliable for contour analysis, not perfect sale-by-sale truth.",
        },
        {
            "area": "availability",
            "quality": "medium",
            "note": "Availability is estimated from stg_wb_stock snapshots by zero/empty stock days. This is a practical signal, not a full continuous stockout timeline.",
        },
        {
            "area": "competitor_context",
            "quality": "low",
            "note": "No robust competitor/position layer is included in this pack. avg_position and competitor_context_short are left empty or marked unavailable.",
        },
        {
            "area": "product_identity",
            "quality": "medium",
            "note": "For several WB legacy rows, sku/barcode alone is not globally unique. Use canonical_product_key plus offer_id when a receiving side needs a strict product identifier.",
        },
        {
            "area": "store_only_expenses",
            "quality": "medium",
            "note": "Store-only expenses are extracted from store-level services rows. Human article names are reconstructed from finance raw payload, including bonus_type_name for Jam-like rows.",
        },
    ]


def build_report(
    window: Window,
    store_rows: list[dict[str, Any]],
    article_rows: list[dict[str, Any]],
    top_product_rows: list[dict[str, Any]],
    top_group_rows: list[dict[str, Any]],
    detail_rows: list[dict[str, Any]],
    store_only_rows: list[dict[str, Any]],
    weekly_rows: list[dict[str, Any]],
    quality_notes: list[dict[str, Any]],
) -> str:
    selected_down = [row for row in detail_rows if row["direction"] == "down"][:5]
    selected_up = [row for row in detail_rows if row["direction"] == "up"][:3]
    return "\n".join(
        [
            "# Profit Control Simulation Dataset v1",
            "",
            "## Main Setup",
            "",
            f"- Schema: `{SCHEMA}`",
            f"- Window type: `28 confirmed days vs previous 28 confirmed days`",
            f"- Confirmed through: `{window.confirmed_through.isoformat()}`",
            f"- Current period: `{window.current_start.isoformat()} .. {window.current_end.isoformat()}`",
            f"- Previous period: `{window.previous_start.isoformat()} .. {window.previous_end.isoformat()}`",
            f"- Chosen store result metric: `profit_after_ads`",
            "",
            "## What Is Included",
            "",
            "- Store-level two-period snapshot with revenue, expense components, and final result.",
            "- Result article table showing how the store delta decomposes across revenue and expense blocks.",
            "- Top-10 products and top-10 groups by negative and positive result contribution.",
            "- Detailed metrics for 5 most dropped products and 3 strongest growers.",
            "- Store-only expense slice reconstructed from finance store-level rows.",
            "- Last 8 completed weeks and current-month tempo slices for an operational block.",
            "",
            "## What Is Missing Or Approximate",
            "",
            "- Fresh finance after `2026-06-07` is excluded because it is not yet strictly confirmed in the warehouse.",
            "- `avg_position` and robust competitor context are not available in the current confirmed layer.",
            "- Availability is based on stock snapshots, so it is useful but still approximate.",
            "- Ads-attributed order/revenue is good for practical directional analysis, but it is not a perfect one-to-one sale proof.",
            "",
            "## Store Snapshot",
            "",
            md_table(
                store_rows,
                [
                    "period_label",
                    "period_start",
                    "period_end",
                    "confirmed_through",
                    "revenue_total",
                    "ads_total",
                    "commission_total",
                    "logistics_total",
                    "storage_total",
                    "acquiring_total",
                    "other_product_expenses_total",
                    "store_only_expenses_total",
                    "result_total",
                ],
            ),
            "",
            "## Result Articles",
            "",
            md_table(
                article_rows,
                ["article_name", "prev", "curr", "delta", "result_impact_delta", "share_of_result_delta"],
            ),
            "",
            "## Product Contributors",
            "",
            "Top negative contributors:",
            md_table(
                [row for row in top_product_rows if row["direction"] == "minus"][:10],
                ["canonical_product_key", "offer_id", "sku", "product_name", "group_name", "result_delta", "revenue_delta", "expense_delta_total"],
            ),
            "",
            "Top positive contributors:",
            md_table(
                [row for row in top_product_rows if row["direction"] == "plus"][:10],
                ["canonical_product_key", "offer_id", "sku", "product_name", "group_name", "result_delta", "revenue_delta", "expense_delta_total"],
            ),
            "",
            "## Group Contributors",
            "",
            md_table(top_group_rows[:10], ["direction", "group_name", "result_delta", "revenue_delta", "expense_delta_total"]),
            "",
            "## Selected Products",
            "",
            "Worst 5:",
            md_table(
                selected_down,
                ["canonical_product_key", "offer_id", "sku", "product_name", "result_delta", "orders_prev", "orders_curr", "sales_units_prev", "sales_units_curr", "ads_spend_prev", "ads_spend_curr", "stock_days_limited_curr"],
            ),
            "",
            "Best 3:",
            md_table(
                selected_up,
                ["canonical_product_key", "offer_id", "sku", "product_name", "result_delta", "orders_prev", "orders_curr", "sales_units_prev", "sales_units_curr", "ads_spend_prev", "ads_spend_curr", "stock_days_limited_curr"],
            ),
            "",
            "## Store-Only Expenses",
            "",
            md_table(store_only_rows, ["article_name", "prev", "curr", "delta"]),
            "",
            "## Weekly Tempo",
            "",
            md_table(weekly_rows, ["week_start", "week_end", "revenue", "result", "ads", "orders", "sales_units"]),
            "",
            "## Quality Notes",
            "",
            md_table(quality_notes, ["area", "quality", "note"]),
            "",
            "## Output Files",
            "",
            f"- `[store_two_periods.csv]({STORE_CSV})`",
            f"- `[result_articles.csv]({ARTICLES_CSV})`",
            f"- `[top_products_result_delta.csv]({TOP_PRODUCTS_CSV})`",
            f"- `[top_groups_result_delta.csv]({TOP_GROUPS_CSV})`",
            f"- `[selected_products_detail.csv]({DETAIL_CSV})`",
            f"- `[store_only_expenses.csv]({STORE_ONLY_CSV})`",
            f"- `[store_weekly_tempo.csv]({WEEKLY_CSV})`",
            f"- `[current_month_tempo.csv]({MTD_CSV})`",
            f"- `[quality_notes.csv]({QUALITY_CSV})`",
        ]
    ) + "\n"


def build_handoff(report_path: Path) -> str:
    return "\n".join(
        [
            "# Chat Handoff",
            "",
            "Use this dataset package as the only source for a manual simulation of the new cabinet.",
            "",
            "What to build from it:",
            "- main store screen",
            "- one problem product card",
            "- one growing product card",
            "- short human conclusions and actions without inventing missing data",
            "",
            "Rules:",
            "- do not invent competitor explanations where the dataset marks them unavailable",
            "- do not use days after confirmedThrough",
            "- treat profit_after_ads as the final store result metric",
            "- treat ads-attributed order/revenue as directional evidence, not exact sale truth",
            "",
            f"Main report: `[profit_control_simulation_dataset_v1_report.md]({report_path})`",
            "",
            "Primary CSVs:",
            f"- `[store_two_periods.csv]({STORE_CSV})`",
            f"- `[result_articles.csv]({ARTICLES_CSV})`",
            f"- `[top_products_result_delta.csv]({TOP_PRODUCTS_CSV})`",
            f"- `[selected_products_detail.csv]({DETAIL_CSV})`",
            f"- `[store_weekly_tempo.csv]({WEEKLY_CSV})`",
            f"- `[current_month_tempo.csv]({MTD_CSV})`",
        ]
    ) + "\n"


def main() -> None:
    window = build_window()
    group_map = load_group_map()
    store_rows = load_store_rows(window)
    article_rows = build_result_articles(store_rows)
    products = load_product_rows(window, group_map)
    top_product_rows = build_top_product_rows(products)
    top_group_rows = build_group_rows(products)
    detail_rows = build_detail_rows(selected_products(products), window)
    store_only_rows = build_store_only_rows(window)
    weekly_rows = build_weekly_rows(window)
    mtd_rows = build_mtd_rows(window)
    quality_notes = quality_rows(window)

    write_csv(STORE_CSV, store_rows)
    write_csv(ARTICLES_CSV, article_rows)
    write_csv(TOP_PRODUCTS_CSV, top_product_rows)
    write_csv(TOP_GROUPS_CSV, top_group_rows)
    write_csv(DETAIL_CSV, detail_rows)
    write_csv(STORE_ONLY_CSV, store_only_rows)
    write_csv(WEEKLY_CSV, weekly_rows)
    write_csv(MTD_CSV, mtd_rows)
    write_csv(QUALITY_CSV, quality_notes)

    REPORT_PATH.write_text(
        build_report(
            window,
            store_rows,
            article_rows,
            top_product_rows,
            top_group_rows,
            detail_rows,
            store_only_rows,
            weekly_rows,
            quality_notes,
        ),
        encoding="utf-8",
    )
    HANDOFF_PATH.write_text(build_handoff(REPORT_PATH), encoding="utf-8")

    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {HANDOFF_PATH}")
    print(f"Wrote {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
