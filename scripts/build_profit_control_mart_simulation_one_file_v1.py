#!/usr/bin/env python3
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_profit_control_recommendation_simulation_pack_v1 import (  # noqa: E402
    SCHEMA,
    build_horizon,
    build_normalized_products,
    build_store_1d_signal,
    current_card_snapshot_by_nm,
    fetch_all,
    fetch_ads_period_metrics,
    fetch_product_daily_series,
    fetch_product_period_metrics,
    fetch_store_horizon,
    latest_product_identity,
    load_stock_metrics,
    md_table,
    norm_text,
    q2,
    safe_div_dec,
    signal_stats,
)
from build_profit_control_simulation_dataset_v1 import build_result_articles, load_store_rows  # noqa: E402

REPORT_PATH = ROOT / "profit_control_mart_simulation_one_file_v1.md"


def blank() -> str:
    return ""


def compute_product_1d_signals(window: Any, skus: list[str]) -> dict[str, dict[str, str]]:
    rows = fetch_product_daily_series(window.confirmed_through - timedelta(days=28), window.confirmed_through)
    by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_sku[str(row["sku"])].append(row)
    out: dict[str, dict[str, str]] = {}
    for sku in skus:
        series = by_sku.get(sku, [])
        if not series:
            out[sku] = {
                "signal_1d_result_significant_yes_no": "no",
                "signal_1d_revenue_significant_yes_no": "no",
                "signal_1d_sales_units_significant_yes_no": "no",
            }
            continue
        hist = series[:-1]
        curr = series[-1]
        sigs = {}
        for field, out_name in (
            ("result_total", "signal_1d_result_significant_yes_no"),
            ("revenue_total", "signal_1d_revenue_significant_yes_no"),
            ("sales_units_total", "signal_1d_sales_units_significant_yes_no"),
        ):
            values = [float(q2(row[field])) for row in hist]
            current_value = float(q2(curr[field]))
            _, _, significant = signal_stats(values, current_value)
            sigs[out_name] = "yes" if significant else "no"
        out[sku] = sigs
    return out


def build_all_product_rows(window: Any, normalized_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    h28 = build_horizon(window, 28)
    h7 = build_horizon(window, 7)
    ops28 = fetch_product_period_metrics(h28)
    ops7 = fetch_product_period_metrics(h7)
    ads28 = fetch_ads_period_metrics(h28)
    stock = load_stock_metrics(
        [{"sku": row["sku"], "offer_id": row["offer_id"]} for row in normalized_products],
        h28,
    )
    one_day_signals = compute_product_1d_signals(window, [str(row["sku"]) for row in normalized_products])

    rows: list[dict[str, Any]] = []
    for item in normalized_products:
        sku = str(item["sku"])
        prev_ops = ops28.get((sku, "previous"), {})
        curr_ops = ops28.get((sku, "current"), {})
        prev7 = ops7.get((sku, "previous"), {})
        curr7 = ops7.get((sku, "current"), {})
        prev_ads = ads28.get((sku, "previous"), {})
        curr_ads = ads28.get((sku, "current"), {})
        prev_stock = stock.get((sku, "previous"), {})
        curr_stock = stock.get((sku, "current"), {})
        one_day = one_day_signals.get(sku, {})

        revenue_prev = q2(item["revenue_prev"])
        revenue_curr = q2(item["revenue_curr"])
        expenses_prev = (
            q2(item["ads_prev"])
            + q2(item["commission_prev"])
            + q2(item["logistics_prev"])
            + q2(item["storage_prev"])
            + q2(item["acquiring_prev"])
            + q2(item["returns_prev"])
            + q2(item["other_product_expenses_prev"])
        )
        expenses_curr = (
            q2(item["ads_curr"])
            + q2(item["commission_curr"])
            + q2(item["logistics_curr"])
            + q2(item["storage_curr"])
            + q2(item["acquiring_curr"])
            + q2(item["returns_curr"])
            + q2(item["other_product_expenses_curr"])
        )
        orders_prev = q2(prev_ops.get("orders_count", 0))
        orders_curr = q2(curr_ops.get("orders_count", 0))
        sales_prev = q2(prev_ops.get("sales_units", 0))
        sales_curr = q2(curr_ops.get("sales_units", 0))
        sales_count_prev = q2(prev_ops.get("sales_count", 0))
        sales_count_curr = q2(curr_ops.get("sales_count", 0))
        conv_prev = safe_div_dec(sales_count_prev, orders_prev) if orders_prev else Decimal("0.00")
        conv_curr = safe_div_dec(sales_count_curr, orders_curr) if orders_curr else Decimal("0.00")
        price_prev = safe_div_dec(revenue_prev, sales_prev) if sales_prev else Decimal("0.00")
        price_curr = safe_div_dec(revenue_curr, sales_curr) if sales_curr else Decimal("0.00")
        total_per_unit_prev = safe_div_dec(expenses_prev, sales_prev) if sales_prev else Decimal("0.00")
        total_per_unit_curr = safe_div_dec(expenses_curr, sales_curr) if sales_curr else Decimal("0.00")
        logistics_per_unit_prev = safe_div_dec(q2(item["logistics_prev"]), sales_prev) if sales_prev else Decimal("0.00")
        logistics_per_unit_curr = safe_div_dec(q2(item["logistics_curr"]), sales_curr) if sales_curr else Decimal("0.00")
        storage_per_unit_prev = safe_div_dec(q2(item["storage_prev"]), sales_prev) if sales_prev else Decimal("0.00")
        storage_per_unit_curr = safe_div_dec(q2(item["storage_curr"]), sales_curr) if sales_curr else Decimal("0.00")
        ads_per_unit_prev = safe_div_dec(q2(item["ads_prev"]), sales_prev) if sales_prev else Decimal("0.00")
        ads_per_unit_curr = safe_div_dec(q2(item["ads_curr"]), sales_curr) if sales_curr else Decimal("0.00")
        result_per_unit_prev = safe_div_dec(q2(item["result_prev"]), sales_prev) if sales_prev else Decimal("0.00")
        result_per_unit_curr = safe_div_dec(q2(item["result_curr"]), sales_curr) if sales_curr else Decimal("0.00")

        rows.append(
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
                "result_prev": f"{q2(item['result_prev']):.2f}",
                "result_curr": f"{q2(item['result_curr']):.2f}",
                "result_delta": f"{q2(item['result_delta']):.2f}",
                "orders_prev": f"{orders_prev:.2f}",
                "orders_curr": f"{orders_curr:.2f}",
                "orders_delta": f"{(orders_curr - orders_prev):.2f}",
                "sales_units_prev": f"{sales_prev:.2f}",
                "sales_units_curr": f"{sales_curr:.2f}",
                "sales_units_delta": f"{(sales_curr - sales_prev):.2f}",
                "order_to_sale_conversion_prev": f"{conv_prev:.2f}",
                "order_to_sale_conversion_curr": f"{conv_curr:.2f}",
                "conversion_delta": f"{(conv_curr - conv_prev):.2f}",
                "avg_realized_price_prev": f"{price_prev:.2f}",
                "avg_realized_price_curr": f"{price_curr:.2f}",
                "avg_realized_price_delta": f"{(price_curr - price_prev):.2f}",
                "ads_prev": f"{q2(item['ads_prev']):.2f}",
                "ads_curr": f"{q2(item['ads_curr']):.2f}",
                "ads_delta": f"{(q2(item['ads_curr']) - q2(item['ads_prev'])):.2f}",
                "commission_prev": f"{q2(item['commission_prev']):.2f}",
                "commission_curr": f"{q2(item['commission_curr']):.2f}",
                "commission_delta": f"{(q2(item['commission_curr']) - q2(item['commission_prev'])):.2f}",
                "logistics_prev": f"{q2(item['logistics_prev']):.2f}",
                "logistics_curr": f"{q2(item['logistics_curr']):.2f}",
                "logistics_delta": f"{(q2(item['logistics_curr']) - q2(item['logistics_prev'])):.2f}",
                "storage_prev": f"{q2(item['storage_prev']):.2f}",
                "storage_curr": f"{q2(item['storage_curr']):.2f}",
                "storage_delta": f"{(q2(item['storage_curr']) - q2(item['storage_prev'])):.2f}",
                "acquiring_prev": f"{q2(item['acquiring_prev']):.2f}",
                "acquiring_curr": f"{q2(item['acquiring_curr']):.2f}",
                "acquiring_delta": f"{(q2(item['acquiring_curr']) - q2(item['acquiring_prev'])):.2f}",
                "other_product_expenses_prev": f"{q2(item['other_product_expenses_prev']):.2f}",
                "other_product_expenses_curr": f"{q2(item['other_product_expenses_curr']):.2f}",
                "other_product_expenses_delta": f"{(q2(item['other_product_expenses_curr']) - q2(item['other_product_expenses_prev'])):.2f}",
                "total_expense_per_sold_unit_prev": f"{total_per_unit_prev:.2f}",
                "total_expense_per_sold_unit_curr": f"{total_per_unit_curr:.2f}",
                "total_expense_per_sold_unit_delta": f"{(total_per_unit_curr - total_per_unit_prev):.2f}",
                "logistics_per_sold_unit_prev": f"{logistics_per_unit_prev:.2f}",
                "logistics_per_sold_unit_curr": f"{logistics_per_unit_curr:.2f}",
                "logistics_per_sold_unit_delta": f"{(logistics_per_unit_curr - logistics_per_unit_prev):.2f}",
                "storage_per_sold_unit_prev": f"{storage_per_unit_prev:.2f}",
                "storage_per_sold_unit_curr": f"{storage_per_unit_curr:.2f}",
                "storage_per_sold_unit_delta": f"{(storage_per_unit_curr - storage_per_unit_prev):.2f}",
                "ads_per_sold_unit_prev": f"{ads_per_unit_prev:.2f}",
                "ads_per_sold_unit_curr": f"{ads_per_unit_curr:.2f}",
                "ads_per_sold_unit_delta": f"{(ads_per_unit_curr - ads_per_unit_prev):.2f}",
                "result_per_sold_unit_prev": f"{result_per_unit_prev:.2f}",
                "result_per_sold_unit_curr": f"{result_per_unit_curr:.2f}",
                "result_per_sold_unit_delta": f"{(result_per_unit_curr - result_per_unit_prev):.2f}",
                "stock_days_limited_prev": int(prev_stock.get("stock_days_limited", 0)),
                "stock_days_limited_curr": int(curr_stock.get("stock_days_limited", 0)),
                "warehouse_issue_short": "limited_main_warehouses" if curr_stock.get("main_warehouses_issue_flag") else "no_main_wh_issue",
                "ads_spend_prev": f"{q2(item['ads_prev']):.2f}",
                "ads_spend_curr": f"{q2(item['ads_curr']):.2f}",
                "views_prev": f"{q2(prev_ads.get('views', 0)):.2f}",
                "views_curr": f"{q2(curr_ads.get('views', 0)):.2f}",
                "clicks_prev": f"{q2(prev_ads.get('clicks', 0)):.2f}",
                "clicks_curr": f"{q2(curr_ads.get('clicks', 0)):.2f}",
                "attributed_orders_prev": f"{q2(prev_ads.get('ad_attributed_orders', 0)):.2f}",
                "attributed_orders_curr": f"{q2(curr_ads.get('ad_attributed_orders', 0)):.2f}",
                "attributed_revenue_prev": f"{q2(prev_ads.get('ad_attributed_revenue', 0)):.2f}",
                "attributed_revenue_curr": f"{q2(curr_ads.get('ad_attributed_revenue', 0)):.2f}",
                "signal_7d_result_delta": f"{(q2(curr7.get('result_total', 0)) - q2(prev7.get('result_total', 0))):.2f}",
                "signal_7d_revenue_delta": f"{(q2(curr7.get('revenue_gross', 0)) - q2(prev7.get('revenue_gross', 0))):.2f}",
                "signal_7d_sales_units_delta": f"{(q2(curr7.get('sales_units', 0)) - q2(prev7.get('sales_units', 0))):.2f}",
                "signal_1d_result_significant_yes_no": one_day.get("signal_1d_result_significant_yes_no", "no"),
                "signal_1d_revenue_significant_yes_no": one_day.get("signal_1d_revenue_significant_yes_no", "no"),
                "signal_1d_sales_units_significant_yes_no": one_day.get("signal_1d_sales_units_significant_yes_no", "no"),
            }
        )
    return rows


def build_store_upper_table(window: Any) -> list[dict[str, Any]]:
    h28 = fetch_store_horizon(build_horizon(window, 28))
    h7 = fetch_store_horizon(build_horizon(window, 7))
    sig1d = build_store_1d_signal(window)
    rows = []
    for label, data in (("28d", h28), ("7d", h7)):
        rows.append(
            {
                "horizon": label,
                "signal_metric": blank(),
                "revenue_prev": f"{data['revenue_prev']:.2f}",
                "revenue_curr": f"{data['revenue_curr']:.2f}",
                "revenue_delta": f"{(data['revenue_curr'] - data['revenue_prev']):.2f}",
                "expenses_prev": f"{data['expenses_prev']:.2f}",
                "expenses_curr": f"{data['expenses_curr']:.2f}",
                "expenses_delta": f"{(data['expenses_curr'] - data['expenses_prev']):.2f}",
                "result_prev": f"{data['result_prev']:.2f}",
                "result_curr": f"{data['result_curr']:.2f}",
                "result_delta": f"{(data['result_curr'] - data['result_prev']):.2f}",
                "ads_prev": f"{data['ads_prev']:.2f}",
                "ads_curr": f"{data['ads_curr']:.2f}",
                "ads_delta": f"{(data['ads_curr'] - data['ads_prev']):.2f}",
                "commission_prev": f"{data['commission_prev']:.2f}",
                "commission_curr": f"{data['commission_curr']:.2f}",
                "commission_delta": f"{(data['commission_curr'] - data['commission_prev']):.2f}",
                "logistics_prev": f"{data['logistics_prev']:.2f}",
                "logistics_curr": f"{data['logistics_curr']:.2f}",
                "logistics_delta": f"{(data['logistics_curr'] - data['logistics_prev']):.2f}",
                "storage_prev": f"{data['storage_prev']:.2f}",
                "storage_curr": f"{data['storage_curr']:.2f}",
                "storage_delta": f"{(data['storage_curr'] - data['storage_prev']):.2f}",
                "acquiring_prev": f"{data['acquiring_prev']:.2f}",
                "acquiring_curr": f"{data['acquiring_curr']:.2f}",
                "acquiring_delta": f"{(data['acquiring_curr'] - data['acquiring_prev']):.2f}",
                "returns_prev": f"{data['returns_prev']:.2f}",
                "returns_curr": f"{data['returns_curr']:.2f}",
                "returns_delta": f"{(data['returns_curr'] - data['returns_prev']):.2f}",
                "other_product_expenses_prev": f"{data['other_product_expenses_prev']:.2f}",
                "other_product_expenses_curr": f"{data['other_product_expenses_curr']:.2f}",
                "other_product_expenses_delta": f"{(data['other_product_expenses_curr'] - data['other_product_expenses_prev']):.2f}",
                "store_only_expenses_prev": f"{data['store_only_expenses_prev']:.2f}",
                "store_only_expenses_curr": f"{data['store_only_expenses_curr']:.2f}",
                "store_only_expenses_delta": f"{(data['store_only_expenses_curr'] - data['store_only_expenses_prev']):.2f}",
                "baseline_value": blank(),
                "current_value": blank(),
                "deviation": blank(),
                "deviation_pct": blank(),
                "significant_yes_no": blank(),
            }
        )
    for row in sig1d:
        if row["metric_name"] not in {"revenue_total", "expenses_total", "result_total"}:
            continue
        rows.append(
            {
                "horizon": "1d",
                "signal_metric": row["metric_name"],
                "revenue_prev": blank(),
                "revenue_curr": blank(),
                "revenue_delta": blank(),
                "expenses_prev": blank(),
                "expenses_curr": blank(),
                "expenses_delta": blank(),
                "result_prev": blank(),
                "result_curr": blank(),
                "result_delta": blank(),
                "ads_prev": blank(),
                "ads_curr": blank(),
                "ads_delta": blank(),
                "commission_prev": blank(),
                "commission_curr": blank(),
                "commission_delta": blank(),
                "logistics_prev": blank(),
                "logistics_curr": blank(),
                "logistics_delta": blank(),
                "storage_prev": blank(),
                "storage_curr": blank(),
                "storage_delta": blank(),
                "acquiring_prev": blank(),
                "acquiring_curr": blank(),
                "acquiring_delta": blank(),
                "returns_prev": blank(),
                "returns_curr": blank(),
                "returns_delta": blank(),
                "other_product_expenses_prev": blank(),
                "other_product_expenses_curr": blank(),
                "other_product_expenses_delta": blank(),
                "store_only_expenses_prev": blank(),
                "store_only_expenses_curr": blank(),
                "store_only_expenses_delta": blank(),
                "baseline_value": row["baseline_day_value"],
                "current_value": row["current_day_value"],
                "deviation": row["deviation"],
                "deviation_pct": row["deviation_pct"],
                "significant_yes_no": row["is_significant_after_outlier_cleaning"],
            }
        )
    return rows


def build_top_contributors(all_products: list[dict[str, Any]], top_n: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    base_rows = [
        {
            "normalized_product_key": row["normalized_product_key"],
            "offer_id": row["offer_id"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "group_name": row["group_name"],
            "result_prev": row["result_prev"],
            "result_curr": row["result_curr"],
            "result_delta": row["result_delta"],
            "revenue_prev": row["revenue_prev"],
            "revenue_curr": row["revenue_curr"],
            "revenue_delta": row["revenue_delta"],
            "expenses_prev": row["expenses_prev"],
            "expenses_curr": row["expenses_curr"],
            "expenses_delta": row["expenses_delta"],
        }
        for row in all_products
    ]
    down = sorted(base_rows, key=lambda row: Decimal(row["result_delta"]))[:top_n]
    up = sorted(base_rows, key=lambda row: Decimal(row["result_delta"]), reverse=True)[:top_n]
    return down, up


def build_candidate_rows(all_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in all_products
        if not (norm_text(row["offer_id"]) in {"", "~"} and norm_text(row["product_name"]) in {"", "~"})
    ]
    down = sorted(eligible, key=lambda row: Decimal(row["result_delta"]))[:10]
    up = sorted(eligible, key=lambda row: Decimal(row["result_delta"]), reverse=True)[:10]
    combined = down + up
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in combined:
        key = str(row["normalized_product_key"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def build_expense_depth_sections(candidate_rows: list[dict[str, Any]], normalized_products: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from build_profit_control_recommendation_simulation_pack_v1 import expense_depth_for_selected

    # Reuse the subtype extraction from the previous pack, then reshape it into four strict tables.
    window = type("W", (), {"confirmed_through": None})  # placeholder for typing
    # actual window will be injected by caller through outer scope
    raise RuntimeError("build_expense_depth_sections requires runtime window injection")


def build_state_table(normalized_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                "rating_start": "unavailable",
                "rating_end": f"{q2(card['rating']):.2f}" if card and card.get("rating") is not None else "unavailable",
                "review_count_start": "unavailable",
                "review_count_end": f"{q2(card['feedbacks']):.2f}" if card and card.get("feedbacks") is not None else "unavailable",
                "avg_position_start": "unavailable",
                "avg_position_end": "unavailable",
            }
        )
    return rows


def build_depth_tables(window: Any, candidate_rows: list[dict[str, Any]], grouped: dict[str, list[dict[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    h28 = build_horizon(window, 28)
    ads28 = fetch_ads_period_metrics(h28)
    stock = load_stock_metrics([{"sku": row["sku"], "offer_id": row["offer_id"]} for row in candidate_rows], h28)

    ads_rows: list[dict[str, Any]] = []
    logistics_rows: list[dict[str, Any]] = []
    storage_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []

    for product in candidate_rows:
        sku = str(product["sku"])
        nkey = product["normalized_product_key"]
        prev_ads = ads28.get((sku, "previous"), {})
        curr_ads = ads28.get((sku, "current"), {})
        revenue_prev = Decimal(product["revenue_prev"])
        revenue_curr = Decimal(product["revenue_curr"])
        sales_prev = Decimal(product["sales_units_prev"])
        sales_curr = Decimal(product["sales_units_curr"])
        logistics_prev = Decimal(product["logistics_prev"])
        logistics_curr = Decimal(product["logistics_curr"])
        storage_prev = Decimal(product["storage_prev"])
        storage_curr = Decimal(product["storage_curr"])
        curr_stock = stock.get((sku, "current"), {})

        spend_prev = Decimal(product["ads_spend_prev"])
        spend_curr = Decimal(product["ads_spend_curr"])
        attr_orders_prev = Decimal(product["attributed_orders_prev"])
        attr_orders_curr = Decimal(product["attributed_orders_curr"])
        attr_rev_prev = Decimal(product["attributed_revenue_prev"])
        attr_rev_curr = Decimal(product["attributed_revenue_curr"])

        ads_rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "spend_prev": product["ads_spend_prev"],
                "spend_curr": product["ads_spend_curr"],
                "spend_delta": f"{(spend_curr - spend_prev):.2f}",
                "views_prev": f"{q2(prev_ads.get('views', 0)):.2f}",
                "views_curr": f"{q2(curr_ads.get('views', 0)):.2f}",
                "clicks_prev": f"{q2(prev_ads.get('clicks', 0)):.2f}",
                "clicks_curr": f"{q2(curr_ads.get('clicks', 0)):.2f}",
                "ctr_prev": f"{(safe_div_dec(q2(prev_ads.get('clicks', 0)), q2(prev_ads.get('views', 0))) if q2(prev_ads.get('views', 0)) else Decimal('0.00')):.2f}",
                "ctr_curr": f"{(safe_div_dec(q2(curr_ads.get('clicks', 0)), q2(curr_ads.get('views', 0))) if q2(curr_ads.get('views', 0)) else Decimal('0.00')):.2f}",
                "attributed_orders_prev": product["attributed_orders_prev"],
                "attributed_orders_curr": product["attributed_orders_curr"],
                "attributed_revenue_prev": product["attributed_revenue_prev"],
                "attributed_revenue_curr": product["attributed_revenue_curr"],
                "cost_per_attributed_order_prev": f"{(safe_div_dec(spend_prev, attr_orders_prev) if attr_orders_prev else Decimal('0.00')):.2f}",
                "cost_per_attributed_order_curr": f"{(safe_div_dec(spend_curr, attr_orders_curr) if attr_orders_curr else Decimal('0.00')):.2f}",
                "ad_share_of_revenue_prev": f"{(safe_div_dec(attr_rev_prev, revenue_prev) if revenue_prev else Decimal('0.00')):.2f}",
                "ad_share_of_revenue_curr": f"{(safe_div_dec(attr_rev_curr, revenue_curr) if revenue_curr else Decimal('0.00')):.2f}",
            }
        )

        subtype_rows = []
        cpk_list = [row["canonical_product_key"] for row in grouped[nkey]]
        subtype_rows = fetch_all(
            f"""
            WITH finance_link AS (
              SELECT
                a.event_date,
                a.money_bucket,
                a.amount,
                COALESCE(f.operation_type, '') AS operation_type,
                COALESCE(f.supplier_oper_name, '') AS supplier_oper_name,
                COALESCE(f.raw_payload->>'bonus_type_name', '') AS bonus_type_name
              FROM {SCHEMA}.fact_product_money_attribution a
              LEFT JOIN {SCHEMA}.stg_wb_finance f
                ON a.source_table = 'stg_wb_finance'
               AND split_part(a.source_record_id, ':', 1) = f.finance_id
              WHERE a.marketplace='wb'
                AND a.event_date BETWEEN %s AND %s
                AND a.canonical_product_key = ANY(%s)
                AND a.source_table='stg_wb_finance'
                AND a.money_bucket IN ('other','services','penalties')
            )
            SELECT
              CASE
                WHEN money_bucket='other' AND operation_type='Возмещение издержек по перевозке/по складским операциям с товаром' THEN 'transport_warehouse_reimbursement'
                WHEN money_bucket='other' AND supplier_oper_name='Возмещение за выдачу и возврат товаров на ПВЗ' THEN 'pvz_issue_return_reimbursement'
                WHEN money_bucket='other' AND operation_type='Продажа' AND supplier_oper_name='Продажа' THEN 'ppvz_reward_sale_flow'
                WHEN money_bucket='services' THEN COALESCE(NULLIF(bonus_type_name,''), NULLIF(supplier_oper_name,''), 'services_other')
                WHEN money_bucket='penalties' THEN COALESCE(NULLIF(bonus_type_name,''), NULLIF(supplier_oper_name,''), 'penalties_other')
                ELSE money_bucket || '_other'
              END AS subtype_name,
              CASE
                WHEN event_date BETWEEN %s AND %s THEN 'current'
                WHEN event_date BETWEEN %s AND %s THEN 'previous'
              END AS period_label,
              ABS(amount)::numeric(18,2) AS amount
            FROM finance_link
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
        subtype_agg: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"previous": Decimal("0.00"), "current": Decimal("0.00")})
        for row in subtype_rows:
            subtype_agg[row["subtype_name"]][row["period_label"]] += q2(row["amount"])

        logistics_rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "logistics_core_prev": product["logistics_prev"],
                "logistics_core_curr": product["logistics_curr"],
                "logistics_core_delta": product["logistics_delta"],
                "transportation_rebill_prev": f"{subtype_agg['transport_warehouse_reimbursement']['previous']:.2f}",
                "transportation_rebill_curr": f"{subtype_agg['transport_warehouse_reimbursement']['current']:.2f}",
                "transportation_rebill_delta": f"{(subtype_agg['transport_warehouse_reimbursement']['current'] - subtype_agg['transport_warehouse_reimbursement']['previous']):.2f}",
                "pvz_component_prev": f"{subtype_agg['pvz_issue_return_reimbursement']['previous']:.2f}",
                "pvz_component_curr": f"{subtype_agg['pvz_issue_return_reimbursement']['current']:.2f}",
                "pvz_component_delta": f"{(subtype_agg['pvz_issue_return_reimbursement']['current'] - subtype_agg['pvz_issue_return_reimbursement']['previous']):.2f}",
                "logistics_per_unit_prev": f"{(safe_div_dec(logistics_prev, sales_prev) if sales_prev else Decimal('0.00')):.2f}",
                "logistics_per_unit_curr": f"{(safe_div_dec(logistics_curr, sales_curr) if sales_curr else Decimal('0.00')):.2f}",
                "logistics_per_unit_delta": f"{((safe_div_dec(logistics_curr, sales_curr) if sales_curr else Decimal('0.00')) - (safe_div_dec(logistics_prev, sales_prev) if sales_prev else Decimal('0.00'))):.2f}",
            }
        )
        storage_rows.append(
            {
                "normalized_product_key": nkey,
                "product_name": product["product_name"],
                "storage_total_prev": product["storage_prev"],
                "storage_total_curr": product["storage_curr"],
                "storage_total_delta": product["storage_delta"],
                "storage_per_unit_prev": f"{(safe_div_dec(storage_prev, sales_prev) if sales_prev else Decimal('0.00')):.2f}",
                "storage_per_unit_curr": f"{(safe_div_dec(storage_curr, sales_curr) if sales_curr else Decimal('0.00')):.2f}",
                "storage_per_unit_delta": f"{((safe_div_dec(storage_curr, sales_curr) if sales_curr else Decimal('0.00')) - (safe_div_dec(storage_prev, sales_prev) if sales_prev else Decimal('0.00'))):.2f}",
                "warehouse_split": "limited_main_warehouses" if curr_stock.get("main_warehouses_issue_flag") else "no_main_wh_issue",
            }
        )
        for subtype_name, values in sorted(subtype_agg.items()):
            if subtype_name in {"transport_warehouse_reimbursement", "pvz_issue_return_reimbursement"}:
                continue
            prev = values["previous"]
            curr = values["current"]
            if prev == 0 and curr == 0:
                continue
            other_rows.append(
                {
                    "normalized_product_key": nkey,
                    "product_name": product["product_name"],
                    "subtype_name": subtype_name,
                    "prev": f"{prev:.2f}",
                    "curr": f"{curr:.2f}",
                    "delta": f"{(curr - prev):.2f}",
                }
            )
    return ads_rows, logistics_rows, storage_rows, other_rows


def build_report(window: Any) -> str:
    normalized_products, grouped = build_normalized_products(window)
    all_products = build_all_product_rows(window, normalized_products)
    store_upper = build_store_upper_table(window)
    store_rows = load_store_rows(window)
    result_articles = build_result_articles(store_rows)
    top_down, top_up = build_top_contributors(all_products, 15)
    candidate_rows = build_candidate_rows(all_products)
    ads_depth, logistics_depth, storage_depth, other_depth = build_depth_tables(window, candidate_rows, grouped)
    state_rows = build_state_table(normalized_products)

    return "\n".join(
        [
            "# Profit Control Marts Simulation One File v1",
            "",
            "## 1. Контекст данных",
            f"- schema: `{SCHEMA}`",
            f"- confirmedThrough: `{window.confirmed_through.isoformat()}`",
            f"- current_28d_window: `{(window.confirmed_through - timedelta(days=27)).isoformat()} .. {window.confirmed_through.isoformat()}`",
            f"- previous_28d_window: `{(window.confirmed_through - timedelta(days=55)).isoformat()} .. {(window.confirmed_through - timedelta(days=28)).isoformat()}`",
            f"- current_7d_window: `{(window.confirmed_through - timedelta(days=6)).isoformat()} .. {window.confirmed_through.isoformat()}`",
            f"- previous_7d_window: `{(window.confirmed_through - timedelta(days=13)).isoformat()} .. {(window.confirmed_through - timedelta(days=7)).isoformat()}`",
            f"- current_1d_date: `{window.confirmed_through.isoformat()}`",
            "- baseline_method_for_1d_signal: `mean of previous 28 confirmed daily values after IQR outlier cleaning`",
            "- store_result_metric_name: `profit_after_ads`",
            "- normalized_product_key rule: `sku-first lower key, else lower offer_id, else lower canonical_product_key`",
            "",
            "## 2. Магазин: верхний уровень",
            md_table(
                store_upper,
                [
                    "horizon",
                    "signal_metric",
                    "revenue_prev",
                    "revenue_curr",
                    "revenue_delta",
                    "expenses_prev",
                    "expenses_curr",
                    "expenses_delta",
                    "result_prev",
                    "result_curr",
                    "result_delta",
                    "ads_prev",
                    "ads_curr",
                    "ads_delta",
                    "commission_prev",
                    "commission_curr",
                    "commission_delta",
                    "logistics_prev",
                    "logistics_curr",
                    "logistics_delta",
                    "storage_prev",
                    "storage_curr",
                    "storage_delta",
                    "acquiring_prev",
                    "acquiring_curr",
                    "acquiring_delta",
                    "returns_prev",
                    "returns_curr",
                    "returns_delta",
                    "other_product_expenses_prev",
                    "other_product_expenses_curr",
                    "other_product_expenses_delta",
                    "store_only_expenses_prev",
                    "store_only_expenses_curr",
                    "store_only_expenses_delta",
                    "baseline_value",
                    "current_value",
                    "deviation",
                    "deviation_pct",
                    "significant_yes_no",
                ],
            ),
            "",
            "## 3. Из чего сложилось изменение результата магазина",
            md_table(
                result_articles,
                ["article_name", "prev", "curr", "delta", "result_impact_delta"],
            ),
            "",
            "## 4. Кто дал изменение результата магазина",
            "### Топ-15 по снижению result_delta",
            md_table(
                top_down,
                ["normalized_product_key", "offer_id", "sku", "product_name", "group_name", "result_prev", "result_curr", "result_delta", "revenue_prev", "revenue_curr", "revenue_delta", "expenses_prev", "expenses_curr", "expenses_delta"],
            ),
            "",
            "### Топ-15 по приросту result_delta",
            md_table(
                top_up,
                ["normalized_product_key", "offer_id", "sku", "product_name", "group_name", "result_prev", "result_curr", "result_delta", "revenue_prev", "revenue_curr", "revenue_delta", "expenses_prev", "expenses_curr", "expenses_delta"],
            ),
            "",
            "## 5. Полная таблица кандидатов для карточки товара",
            md_table(
                candidate_rows,
                [
                    "normalized_product_key",
                    "offer_id",
                    "sku",
                    "product_name",
                    "group_name",
                    "revenue_prev",
                    "revenue_curr",
                    "revenue_delta",
                    "expenses_prev",
                    "expenses_curr",
                    "expenses_delta",
                    "result_prev",
                    "result_curr",
                    "result_delta",
                    "orders_prev",
                    "orders_curr",
                    "orders_delta",
                    "sales_units_prev",
                    "sales_units_curr",
                    "sales_units_delta",
                    "order_to_sale_conversion_prev",
                    "order_to_sale_conversion_curr",
                    "conversion_delta",
                    "avg_realized_price_prev",
                    "avg_realized_price_curr",
                    "avg_realized_price_delta",
                    "ads_prev",
                    "ads_curr",
                    "ads_delta",
                    "commission_prev",
                    "commission_curr",
                    "commission_delta",
                    "logistics_prev",
                    "logistics_curr",
                    "logistics_delta",
                    "storage_prev",
                    "storage_curr",
                    "storage_delta",
                    "acquiring_prev",
                    "acquiring_curr",
                    "acquiring_delta",
                    "other_product_expenses_prev",
                    "other_product_expenses_curr",
                    "other_product_expenses_delta",
                    "total_expense_per_sold_unit_prev",
                    "total_expense_per_sold_unit_curr",
                    "total_expense_per_sold_unit_delta",
                    "logistics_per_sold_unit_prev",
                    "logistics_per_sold_unit_curr",
                    "logistics_per_sold_unit_delta",
                    "storage_per_sold_unit_prev",
                    "storage_per_sold_unit_curr",
                    "storage_per_sold_unit_delta",
                    "ads_per_sold_unit_prev",
                    "ads_per_sold_unit_curr",
                    "ads_per_sold_unit_delta",
                    "result_per_sold_unit_prev",
                    "result_per_sold_unit_curr",
                    "result_per_sold_unit_delta",
                    "stock_days_limited_prev",
                    "stock_days_limited_curr",
                    "warehouse_issue_short",
                    "ads_spend_prev",
                    "ads_spend_curr",
                    "views_prev",
                    "views_curr",
                    "clicks_prev",
                    "clicks_curr",
                    "attributed_orders_prev",
                    "attributed_orders_curr",
                    "attributed_revenue_prev",
                    "attributed_revenue_curr",
                    "signal_7d_result_delta",
                    "signal_7d_revenue_delta",
                    "signal_7d_sales_units_delta",
                    "signal_1d_result_significant_yes_no",
                    "signal_1d_revenue_significant_yes_no",
                    "signal_1d_sales_units_significant_yes_no",
                ],
            ),
            "",
            "## 6. Глубина по расходам для тех же товаров",
            "### Реклама",
            md_table(
                ads_depth,
                ["normalized_product_key", "product_name", "spend_prev", "spend_curr", "spend_delta", "views_prev", "views_curr", "clicks_prev", "clicks_curr", "ctr_prev", "ctr_curr", "attributed_orders_prev", "attributed_orders_curr", "attributed_revenue_prev", "attributed_revenue_curr", "cost_per_attributed_order_prev", "cost_per_attributed_order_curr", "ad_share_of_revenue_prev", "ad_share_of_revenue_curr"],
            ),
            "",
            "### Логистика",
            md_table(
                logistics_depth,
                ["normalized_product_key", "product_name", "logistics_core_prev", "logistics_core_curr", "logistics_core_delta", "transportation_rebill_prev", "transportation_rebill_curr", "transportation_rebill_delta", "pvz_component_prev", "pvz_component_curr", "pvz_component_delta", "logistics_per_unit_prev", "logistics_per_unit_curr", "logistics_per_unit_delta"],
            ),
            "",
            "### Хранение",
            md_table(
                storage_depth,
                ["normalized_product_key", "product_name", "storage_total_prev", "storage_total_curr", "storage_total_delta", "storage_per_unit_prev", "storage_per_unit_curr", "storage_per_unit_delta", "warehouse_split"],
            ),
            "",
            "### Прочие товарные расходы",
            md_table(
                other_depth,
                ["normalized_product_key", "product_name", "subtype_name", "prev", "curr", "delta"],
            ),
            "",
            "## 7. Накопительные показатели",
            md_table(
                state_rows,
                ["normalized_product_key", "rating_start", "rating_end", "review_count_start", "review_count_end", "avg_position_start", "avg_position_end"],
            ),
            "",
            "## 8. Ограничения",
            "- надежно: confirmed windows, store result metric `profit_after_ads`, normalized product identity, core revenue/expense/product facts from marts and linked layers",
            "- приблизительно: stock limitation is based on stock snapshots; ads-attributed orders/revenue is directional, not sale-by-sale accounting truth",
            "- нет: historical `avg_position`, historical `rating_start/review_count_start`, full competitor layer inside this one-file confirmed pack",
        ]
    ) + "\n"


def main() -> None:
    from build_profit_control_simulation_dataset_v1 import build_window

    window = build_window()
    REPORT_PATH.write_text(build_report(window), encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
