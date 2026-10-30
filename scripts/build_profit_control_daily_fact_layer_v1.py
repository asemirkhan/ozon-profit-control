#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
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

TABLES = {
    "store": "pc_v2_daily_store_fact",
    "product": "pc_v2_daily_product_fact",
    "warehouse": "pc_v2_daily_warehouse_fact",
    "region": "pc_v2_daily_region_fact",
}

REPORT_PATH = ROOT / "profit_control_daily_fact_layer_v1_report.md"
TABLES_CSV_PATH = ROOT / "profit_control_daily_fact_layer_v1_tables.csv"
STRUCTURE_CSV_PATH = ROOT / "profit_control_daily_fact_layer_v1_structures.csv"
CHECKS_CSV_PATH = ROOT / "profit_control_daily_fact_layer_v1_checks.csv"

HEURISTIC_METHODS = (
    "paid_storage_share_day",
    "revenue_share_window",
    "barcode_match",
    "bridge_match",
    "category_default_cogs",
    "global_default_cogs",
    "single_product_day",
    "equal_day_split",
)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def money_text(value: Any) -> str:
    if value is None:
        return "0.00"
    return f"{value:.2f}" if hasattr(value, "quantize") else str(value)


def execute(conn: Any, sql: str) -> None:
    with conn.cursor() as cur:
        cur.execute(sql)


def fetch_all(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def fetch_one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else {}


def build_daily_layer(conn: Any) -> None:
    heuristic_sql = ", ".join(f"'{item}'" for item in HEURISTIC_METHODS)

    statements = [
        f"DROP TABLE IF EXISTS {SCHEMA}.{TABLES['store']};",
        f"DROP TABLE IF EXISTS {SCHEMA}.{TABLES['product']};",
        f"DROP TABLE IF EXISTS {SCHEMA}.{TABLES['warehouse']};",
        f"DROP TABLE IF EXISTS {SCHEMA}.{TABLES['region']};",
        "DROP TABLE IF EXISTS tmp_pc_v2_confirmed_boundary;",
        "DROP TABLE IF EXISTS tmp_pc_v2_bridge_best;",
        "DROP TABLE IF EXISTS tmp_pc_v2_bridge_info;",
        "DROP TABLE IF EXISTS tmp_pc_v2_sales_mapped;",
        "DROP TABLE IF EXISTS tmp_pc_v2_orders_mapped;",
        "DROP TABLE IF EXISTS tmp_pc_v2_product_sales_daily;",
        "DROP TABLE IF EXISTS tmp_pc_v2_product_orders_daily;",
        "DROP TABLE IF EXISTS tmp_pc_v2_fact_split_daily;",
        "DROP TABLE IF EXISTS tmp_pc_v2_store_rows_daily;",
        "DROP TABLE IF EXISTS tmp_pc_v2_warehouse_slice_daily;",
        "DROP TABLE IF EXISTS tmp_pc_v2_region_slice_daily;",
    ]
    for sql in statements:
        execute(conn, sql)

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_confirmed_boundary AS
        SELECT MAX(day)::date AS confirmed_through
        FROM {SCHEMA}.finance_day_state
        WHERE final_analytic_usable_flag = TRUE;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_bridge_best AS
        SELECT
          b.marketplace,
          b.canonical_product_key,
          b.offer_id,
          b.sku,
          b.barcode,
          b.nm_id,
          b.product_name,
          b.updated_at,
          b.bridge_id,
          b.match_confidence,
          ROW_NUMBER() OVER (
            PARTITION BY b.marketplace, b.canonical_product_key
            ORDER BY
              CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
              b.updated_at DESC,
              b.bridge_id DESC
          ) AS rn
        FROM {SCHEMA}.dim_product_bridge b
        WHERE b.marketplace = 'wb';
        """,
    )

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_bridge_info AS
        SELECT
          marketplace,
          canonical_product_key,
          COALESCE(NULLIF(offer_id, ''), '~') AS offer_id,
          COALESCE(NULLIF(sku, ''), NULLIF(barcode, ''), '~') AS sku,
          COALESCE(NULLIF(product_name, ''), '~') AS product_name,
          nm_id,
          barcode
        FROM tmp_pc_v2_bridge_best
        WHERE rn = 1;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_sales_mapped AS
        SELECT
          (s.sale_date)::date AS date,
          'wb'::text AS marketplace,
          COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
          COALESCE(bi.offer_id, COALESCE(NULLIF(s.offer_id, '~'), '~')) AS offer_id,
          COALESCE(bi.sku, COALESCE(NULLIF(s.barcode, ''), '~')) AS sku,
          COALESCE(bi.product_name, '~') AS product_name,
          COALESCE(NULLIF(s.warehouse_name, ''), '~') AS warehouse_name,
          COALESCE(NULLIF(s.region_name, ''), '~') AS region_name,
          s.order_id,
          s.sale_id,
          CASE
            WHEN COALESCE(s.is_return, FALSE) THEN -ABS(COALESCE(s.quantity, 0))
            ELSE COALESCE(s.quantity, 0)
          END::numeric(18,2) AS units_sold,
          COALESCE(s.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
          ABS(COALESCE(s.revenue_gross, 0))::numeric(18,2) AS revenue_share_basis,
          ABS(
            CASE
              WHEN COALESCE(s.is_return, FALSE) THEN -ABS(COALESCE(s.quantity, 0))
              ELSE COALESCE(s.quantity, 0)
            END
          )::numeric(18,2) AS units_share_basis
        FROM {SCHEMA}.stg_wb_sale s
        LEFT JOIN LATERAL (
          SELECT b.canonical_product_key
          FROM tmp_pc_v2_bridge_best b
          WHERE
            (NULLIF(s.offer_id, '~') IS NOT NULL AND b.offer_id = s.offer_id)
            OR (NULLIF(s.nm_id, '') IS NOT NULL AND b.nm_id = s.nm_id)
            OR (NULLIF(s.barcode, '') IS NOT NULL AND b.barcode = s.barcode)
          ORDER BY
            CASE
              WHEN NULLIF(s.offer_id, '~') IS NOT NULL AND b.offer_id = s.offer_id THEN 1
              WHEN NULLIF(s.nm_id, '') IS NOT NULL AND b.nm_id = s.nm_id THEN 2
              WHEN NULLIF(s.barcode, '') IS NOT NULL AND b.barcode = s.barcode THEN 3
              ELSE 4
            END,
            CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            b.updated_at DESC,
            b.bridge_id DESC
          LIMIT 1
        ) bm ON TRUE
        LEFT JOIN tmp_pc_v2_bridge_info bi
          ON bi.marketplace = 'wb'
         AND bi.canonical_product_key = COALESCE(bm.canonical_product_key, '~')
        WHERE COALESCE(s.marketplace, 'wb') = 'wb';
        """,
    )

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_orders_mapped AS
        SELECT
          (o.order_date)::date AS date,
          'wb'::text AS marketplace,
          COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
          COALESCE(NULLIF(o.offer_id, '~'), bi.offer_id, '~') AS offer_id,
          COALESCE(bi.sku, COALESCE(NULLIF(o.barcode, ''), '~')) AS sku,
          COALESCE(bi.product_name, '~') AS product_name,
          COALESCE(NULLIF(o.warehouse_name, ''), '~') AS warehouse_name,
          COALESCE(NULLIF(o.region_name, ''), '~') AS region_name,
          o.order_id,
          COALESCE(o.quantity, 0)::numeric(18,2) AS ordered_units
        FROM {SCHEMA}.stg_wb_order o
        LEFT JOIN LATERAL (
          SELECT b.canonical_product_key
          FROM tmp_pc_v2_bridge_best b
          WHERE
            (NULLIF(o.offer_id, '~') IS NOT NULL AND b.offer_id = o.offer_id)
            OR (NULLIF(o.nm_id, '') IS NOT NULL AND b.nm_id = o.nm_id)
            OR (NULLIF(o.barcode, '') IS NOT NULL AND b.barcode = o.barcode)
          ORDER BY
            CASE
              WHEN NULLIF(o.offer_id, '~') IS NOT NULL AND b.offer_id = o.offer_id THEN 1
              WHEN NULLIF(o.nm_id, '') IS NOT NULL AND b.nm_id = o.nm_id THEN 2
              WHEN NULLIF(o.barcode, '') IS NOT NULL AND b.barcode = o.barcode THEN 3
              ELSE 4
            END,
            CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            b.updated_at DESC,
            b.bridge_id DESC
          LIMIT 1
        ) bm ON TRUE
        LEFT JOIN tmp_pc_v2_bridge_info bi
          ON bi.marketplace = 'wb'
         AND bi.canonical_product_key = COALESCE(bm.canonical_product_key, '~')
        WHERE COALESCE(o.marketplace, 'wb') = 'wb';
        """,
    )

    execute(
        conn,
        """
        CREATE TEMP TABLE tmp_pc_v2_product_sales_daily AS
        SELECT
          date,
          marketplace,
          canonical_product_key,
          offer_id,
          sku,
          product_name,
          COUNT(DISTINCT sale_id)::numeric(18,2) AS sales_count,
          SUM(COALESCE(units_sold, 0))::numeric(18,2) AS units_sold,
          SUM(COALESCE(revenue_gross, 0))::numeric(18,2) AS revenue_gross
        FROM tmp_pc_v2_sales_mapped
        WHERE canonical_product_key <> '~'
        GROUP BY 1,2,3,4,5,6;
        """,
    )

    execute(
        conn,
        """
        CREATE TEMP TABLE tmp_pc_v2_product_orders_daily AS
        SELECT
          date,
          marketplace,
          canonical_product_key,
          offer_id,
          sku,
          product_name,
          COUNT(DISTINCT order_id)::numeric(18,2) AS orders_count
        FROM tmp_pc_v2_orders_mapped
        WHERE canonical_product_key <> '~'
        GROUP BY 1,2,3,4,5,6;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TEMP TABLE tmp_pc_v2_fact_split_daily AS
        WITH fact_enriched AS (
          SELECT
            fpm.event_date AS date,
            fpm.marketplace,
            fpm.canonical_product_key,
            fpm.money_bucket,
            fpm.amount,
            fpm.attribution_method,
            fpm.attribution_confidence,
            wf.finance_id,
            COALESCE(wf.acquiring_fee, 0)::numeric(18,2) AS acquiring_fee
          FROM {SCHEMA}.fact_product_money_attribution fpm
          LEFT JOIN {SCHEMA}.stg_wb_finance wf
            ON fpm.source_table = 'stg_wb_finance'
           AND wf.finance_id = split_part(fpm.source_record_id, ':', 1)
          WHERE fpm.marketplace = 'wb'
        )
        SELECT
          date,
          marketplace,
          canonical_product_key,
          SUM(CASE WHEN money_bucket = 'revenue' THEN amount ELSE 0 END)::numeric(18,2) AS revenue_gross,
          SUM(CASE WHEN money_bucket = 'returns' THEN amount ELSE 0 END)::numeric(18,2) AS returns_amount,
          SUM(CASE WHEN money_bucket = 'ads' THEN amount ELSE 0 END)::numeric(18,2) AS ads_amount,
          SUM(CASE WHEN money_bucket = 'commission' THEN amount ELSE 0 END)::numeric(18,2) AS commission_amount,
          SUM(CASE WHEN money_bucket = 'logistics' THEN amount ELSE 0 END)::numeric(18,2) AS logistics_amount,
          SUM(CASE WHEN money_bucket = 'storage' THEN amount ELSE 0 END)::numeric(18,2) AS storage_amount,
          SUM(CASE WHEN money_bucket = 'penalties' THEN amount ELSE 0 END)::numeric(18,2) AS penalties_amount,
          SUM(CASE WHEN money_bucket = 'other' THEN amount ELSE 0 END)::numeric(18,2) AS other_expenses_amount,
          SUM(CASE WHEN money_bucket = 'cogs' THEN amount ELSE 0 END)::numeric(18,2) AS cogs_amount,
          SUM(
            CASE
              WHEN money_bucket = 'services' AND COALESCE(acquiring_fee, 0) > 0 THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS acquiring_amount,
          SUM(
            CASE
              WHEN money_bucket = 'services'
               AND canonical_product_key <> '~'
               AND COALESCE(acquiring_fee, 0) = 0
              THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS product_services_amount,
          SUM(
            CASE
              WHEN money_bucket = 'services'
               AND canonical_product_key = '~'
              THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS store_services_amount,
          SUM(
            CASE
              WHEN canonical_product_key <> '~'
               AND money_bucket <> 'revenue'
               AND attribution_method NOT IN ({heuristic_sql})
              THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS product_level_expense_amount,
          SUM(
            CASE
              WHEN canonical_product_key <> '~'
               AND money_bucket <> 'revenue'
               AND attribution_method IN ({heuristic_sql})
              THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS mixed_expense_amount,
          SUM(
            CASE
              WHEN canonical_product_key = '~'
               AND money_bucket <> 'revenue'
              THEN amount
              ELSE 0
            END
          )::numeric(18,2) AS store_level_expense_amount
        FROM fact_enriched
        GROUP BY 1,2,3;
        """,
    )

    execute(
        conn,
        """
        CREATE TEMP TABLE tmp_pc_v2_store_rows_daily AS
        SELECT
          date,
          marketplace,
          SUM(COALESCE(store_services_amount, 0))::numeric(18,2) AS store_services_amount,
          SUM(COALESCE(store_level_expense_amount, 0))::numeric(18,2) AS store_level_expense_amount
        FROM tmp_pc_v2_fact_split_daily
        WHERE canonical_product_key = '~'
        GROUP BY 1,2;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TABLE {SCHEMA}.{TABLES['product']} AS
        WITH keys AS (
          SELECT date, marketplace, canonical_product_key, offer_id, sku, product_name
          FROM tmp_pc_v2_product_sales_daily
          UNION
          SELECT date, marketplace, canonical_product_key, offer_id, sku, product_name
          FROM tmp_pc_v2_product_orders_daily
          UNION
          SELECT
            fs.date,
            fs.marketplace,
            fs.canonical_product_key,
            COALESCE(bi.offer_id, '~') AS offer_id,
            COALESCE(bi.sku, '~') AS sku,
            COALESCE(bi.product_name, '~') AS product_name
          FROM tmp_pc_v2_fact_split_daily fs
          LEFT JOIN tmp_pc_v2_bridge_info bi
            ON bi.marketplace = fs.marketplace
           AND bi.canonical_product_key = fs.canonical_product_key
          WHERE fs.canonical_product_key <> '~'
        ),
        boundary AS (
          SELECT confirmed_through FROM tmp_pc_v2_confirmed_boundary
        )
        SELECT
          k.date,
          k.marketplace,
          k.canonical_product_key,
          k.offer_id,
          k.sku,
          k.product_name,
          COALESCE(o.orders_count, 0)::numeric(18,2) AS orders_count,
          COALESCE(s.sales_count, 0)::numeric(18,2) AS sales_count,
          COALESCE(s.units_sold, 0)::numeric(18,2) AS units_sold,
          COALESCE(fs.revenue_gross, COALESCE(s.revenue_gross, 0), 0)::numeric(18,2) AS revenue_gross,
          COALESCE(fs.ads_amount, 0)::numeric(18,2) AS ads_amount,
          COALESCE(fs.commission_amount, 0)::numeric(18,2) AS commission_amount,
          COALESCE(fs.logistics_amount, 0)::numeric(18,2) AS logistics_amount,
          COALESCE(fs.storage_amount, 0)::numeric(18,2) AS storage_amount,
          COALESCE(fs.acquiring_amount, 0)::numeric(18,2) AS acquiring_amount,
          COALESCE(fs.returns_amount, 0)::numeric(18,2) AS returns_amount,
          COALESCE(fs.other_expenses_amount, 0)::numeric(18,2) AS other_expenses_amount,
          COALESCE(fs.product_services_amount, 0)::numeric(18,2) AS product_services_amount,
          0::numeric(18,2) AS store_services_amount,
          COALESCE(fs.penalties_amount, 0)::numeric(18,2) AS penalties_amount,
          COALESCE(fs.cogs_amount, 0)::numeric(18,2) AS cogs_amount,
          (
            COALESCE(fs.revenue_gross, COALESCE(s.revenue_gross, 0), 0)
            - COALESCE(fs.returns_amount, 0)
            - COALESCE(fs.commission_amount, 0)
            - COALESCE(fs.logistics_amount, 0)
            - COALESCE(fs.storage_amount, 0)
            - COALESCE(fs.acquiring_amount, 0)
            - COALESCE(fs.other_expenses_amount, 0)
            - COALESCE(fs.product_services_amount, 0)
            - COALESCE(fs.penalties_amount, 0)
          )::numeric(18,2) AS profit_before_ads,
          (
            COALESCE(fs.revenue_gross, COALESCE(s.revenue_gross, 0), 0)
            - COALESCE(fs.returns_amount, 0)
            - COALESCE(fs.commission_amount, 0)
            - COALESCE(fs.logistics_amount, 0)
            - COALESCE(fs.storage_amount, 0)
            - COALESCE(fs.acquiring_amount, 0)
            - COALESCE(fs.other_expenses_amount, 0)
            - COALESCE(fs.product_services_amount, 0)
            - COALESCE(fs.penalties_amount, 0)
            - COALESCE(fs.ads_amount, 0)
          )::numeric(18,2) AS profit_after_ads,
          COALESCE(fs.product_level_expense_amount, 0)::numeric(18,2) AS product_level_expense_amount,
          0::numeric(18,2) AS store_level_expense_amount,
          COALESCE(fs.mixed_expense_amount, 0)::numeric(18,2) AS mixed_expense_amount,
          (COALESCE(fs.product_level_expense_amount, 0) > 0) AS has_product_level_expenses,
          FALSE AS has_store_level_expenses,
          (COALESCE(fs.mixed_expense_amount, 0) > 0) AS has_mixed_expenses,
          (SELECT confirmed_through FROM boundary) AS confirmed_through,
          CASE
            WHEN (SELECT confirmed_through FROM boundary) IS NULL THEN FALSE
            WHEN k.date <= (SELECT confirmed_through FROM boundary) THEN TRUE
            ELSE FALSE
          END AS finance_confirmed_flag
        FROM keys k
        LEFT JOIN tmp_pc_v2_product_sales_daily s
          ON s.date = k.date
         AND s.marketplace = k.marketplace
         AND s.canonical_product_key = k.canonical_product_key
        LEFT JOIN tmp_pc_v2_product_orders_daily o
          ON o.date = k.date
         AND o.marketplace = k.marketplace
         AND o.canonical_product_key = k.canonical_product_key
        LEFT JOIN tmp_pc_v2_fact_split_daily fs
          ON fs.date = k.date
         AND fs.marketplace = k.marketplace
         AND fs.canonical_product_key = k.canonical_product_key;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TABLE {SCHEMA}.{TABLES['store']} AS
        WITH product_agg AS (
          SELECT
            date,
            marketplace,
            SUM(orders_count)::numeric(18,2) AS orders_count,
            SUM(sales_count)::numeric(18,2) AS sales_count,
            SUM(units_sold)::numeric(18,2) AS units_sold,
            SUM(revenue_gross)::numeric(18,2) AS revenue_gross,
            SUM(ads_amount)::numeric(18,2) AS ads_amount,
            SUM(commission_amount)::numeric(18,2) AS commission_amount,
            SUM(logistics_amount)::numeric(18,2) AS logistics_amount,
            SUM(storage_amount)::numeric(18,2) AS storage_amount,
            SUM(acquiring_amount)::numeric(18,2) AS acquiring_amount,
            SUM(returns_amount)::numeric(18,2) AS returns_amount,
            SUM(other_expenses_amount)::numeric(18,2) AS other_expenses_amount,
            SUM(product_services_amount)::numeric(18,2) AS product_services_amount,
            SUM(penalties_amount)::numeric(18,2) AS penalties_amount,
            SUM(cogs_amount)::numeric(18,2) AS cogs_amount,
            SUM(product_level_expense_amount)::numeric(18,2) AS product_level_expense_amount,
            SUM(mixed_expense_amount)::numeric(18,2) AS mixed_expense_amount
          FROM {SCHEMA}.{TABLES['product']}
          GROUP BY 1,2
        ),
        direct_orders AS (
          SELECT
            date,
            marketplace,
            COUNT(DISTINCT order_id)::numeric(18,2) AS orders_count
          FROM tmp_pc_v2_orders_mapped
          GROUP BY 1,2
        ),
        direct_sales AS (
          SELECT
            date,
            marketplace,
            COUNT(DISTINCT sale_id)::numeric(18,2) AS sales_count,
            SUM(COALESCE(units_sold, 0))::numeric(18,2) AS units_sold
          FROM tmp_pc_v2_sales_mapped
          GROUP BY 1,2
        ),
        keys AS (
          SELECT date, marketplace FROM product_agg
          UNION
          SELECT date, marketplace FROM tmp_pc_v2_store_rows_daily
          UNION
          SELECT date, marketplace FROM direct_orders
          UNION
          SELECT date, marketplace FROM direct_sales
        ),
        boundary AS (
          SELECT confirmed_through FROM tmp_pc_v2_confirmed_boundary
        )
        SELECT
          k.date,
          k.marketplace,
          COALESCE(dord.orders_count, pa.orders_count, 0)::numeric(18,2) AS orders_count,
          COALESCE(ds.sales_count, pa.sales_count, 0)::numeric(18,2) AS sales_count,
          COALESCE(ds.units_sold, pa.units_sold, 0)::numeric(18,2) AS units_sold,
          COALESCE(pa.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
          COALESCE(pa.ads_amount, 0)::numeric(18,2) AS ads_amount,
          COALESCE(pa.commission_amount, 0)::numeric(18,2) AS commission_amount,
          COALESCE(pa.logistics_amount, 0)::numeric(18,2) AS logistics_amount,
          COALESCE(pa.storage_amount, 0)::numeric(18,2) AS storage_amount,
          COALESCE(pa.acquiring_amount, 0)::numeric(18,2) AS acquiring_amount,
          COALESCE(pa.returns_amount, 0)::numeric(18,2) AS returns_amount,
          COALESCE(pa.other_expenses_amount, 0)::numeric(18,2) AS other_expenses_amount,
          COALESCE(pa.product_services_amount, 0)::numeric(18,2) AS product_services_amount,
          COALESCE(sr.store_services_amount, 0)::numeric(18,2) AS store_services_amount,
          COALESCE(pa.penalties_amount, 0)::numeric(18,2) AS penalties_amount,
          COALESCE(pa.cogs_amount, 0)::numeric(18,2) AS cogs_amount,
          (
            COALESCE(pa.revenue_gross, 0)
            - COALESCE(pa.returns_amount, 0)
            - COALESCE(pa.commission_amount, 0)
            - COALESCE(pa.logistics_amount, 0)
            - COALESCE(pa.storage_amount, 0)
            - COALESCE(pa.acquiring_amount, 0)
            - COALESCE(pa.other_expenses_amount, 0)
            - COALESCE(pa.product_services_amount, 0)
            - COALESCE(sr.store_services_amount, 0)
            - COALESCE(pa.penalties_amount, 0)
          )::numeric(18,2) AS profit_before_ads,
          (
            COALESCE(pa.revenue_gross, 0)
            - COALESCE(pa.returns_amount, 0)
            - COALESCE(pa.commission_amount, 0)
            - COALESCE(pa.logistics_amount, 0)
            - COALESCE(pa.storage_amount, 0)
            - COALESCE(pa.acquiring_amount, 0)
            - COALESCE(pa.other_expenses_amount, 0)
            - COALESCE(pa.product_services_amount, 0)
            - COALESCE(sr.store_services_amount, 0)
            - COALESCE(pa.penalties_amount, 0)
            - COALESCE(pa.ads_amount, 0)
          )::numeric(18,2) AS profit_after_ads,
          COALESCE(pa.product_level_expense_amount, 0)::numeric(18,2) AS product_level_expense_amount,
          COALESCE(sr.store_level_expense_amount, 0)::numeric(18,2) AS store_level_expense_amount,
          COALESCE(pa.mixed_expense_amount, 0)::numeric(18,2) AS mixed_expense_amount,
          (COALESCE(pa.product_level_expense_amount, 0) > 0) AS has_product_level_expenses,
          (COALESCE(sr.store_level_expense_amount, 0) > 0) AS has_store_level_expenses,
          (COALESCE(pa.mixed_expense_amount, 0) > 0) AS has_mixed_expenses,
          (SELECT confirmed_through FROM boundary) AS confirmed_through,
          CASE
            WHEN (SELECT confirmed_through FROM boundary) IS NULL THEN FALSE
            WHEN k.date <= (SELECT confirmed_through FROM boundary) THEN TRUE
            ELSE FALSE
          END AS finance_confirmed_flag
        FROM keys k
        LEFT JOIN product_agg pa
          ON pa.date = k.date
         AND pa.marketplace = k.marketplace
        LEFT JOIN tmp_pc_v2_store_rows_daily sr
          ON sr.date = k.date
         AND sr.marketplace = k.marketplace
        LEFT JOIN direct_orders dord
          ON dord.date = k.date
         AND dord.marketplace = k.marketplace
        LEFT JOIN direct_sales ds
          ON ds.date = k.date
         AND ds.marketplace = k.marketplace;
        """,
    )

    execute(
        conn,
        """
        CREATE TEMP TABLE tmp_pc_v2_warehouse_slice_daily AS
        WITH sales_agg AS (
          SELECT
            date,
            marketplace,
            canonical_product_key,
            COALESCE(warehouse_name, '~') AS warehouse_name,
            COUNT(DISTINCT sale_id)::numeric(18,2) AS sales_count,
            SUM(COALESCE(units_sold, 0))::numeric(18,2) AS units_sold,
            SUM(COALESCE(revenue_gross, 0))::numeric(18,2) AS revenue_gross,
            SUM(COALESCE(revenue_share_basis, 0))::numeric(18,2) AS revenue_share_basis,
            SUM(COALESCE(units_share_basis, 0))::numeric(18,2) AS units_share_basis
          FROM tmp_pc_v2_sales_mapped
          WHERE canonical_product_key <> '~'
          GROUP BY 1,2,3,4
        ),
        orders_agg AS (
          SELECT
            date,
            marketplace,
            canonical_product_key,
            COALESCE(warehouse_name, '~') AS warehouse_name,
            COUNT(DISTINCT order_id)::numeric(18,2) AS orders_count
          FROM tmp_pc_v2_orders_mapped
          WHERE canonical_product_key <> '~'
          GROUP BY 1,2,3,4
        ),
        keys AS (
          SELECT date, marketplace, canonical_product_key, warehouse_name FROM sales_agg
          UNION
          SELECT date, marketplace, canonical_product_key, warehouse_name FROM orders_agg
        )
        SELECT
          k.date,
          k.marketplace,
          k.canonical_product_key,
          k.warehouse_name,
          COALESCE(o.orders_count, 0)::numeric(18,2) AS orders_count,
          COALESCE(s.sales_count, 0)::numeric(18,2) AS sales_count,
          COALESCE(s.units_sold, 0)::numeric(18,2) AS units_sold,
          COALESCE(s.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
          CASE
            WHEN SUM(COALESCE(s.revenue_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(s.revenue_share_basis, 0)
                / SUM(COALESCE(s.revenue_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            WHEN SUM(COALESCE(s.units_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(s.units_share_basis, 0)
                / SUM(COALESCE(s.units_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            WHEN SUM(COALESCE(o.orders_count, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(o.orders_count, 0)
                / SUM(COALESCE(o.orders_count, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            ELSE 0::numeric
          END::numeric(18,8) AS alloc_share
        FROM keys k
        LEFT JOIN sales_agg s
          ON s.date = k.date
         AND s.marketplace = k.marketplace
         AND s.canonical_product_key = k.canonical_product_key
         AND s.warehouse_name = k.warehouse_name
        LEFT JOIN orders_agg o
          ON o.date = k.date
         AND o.marketplace = k.marketplace
         AND o.canonical_product_key = k.canonical_product_key
         AND o.warehouse_name = k.warehouse_name;
        """,
    )

    execute(
        conn,
        """
        CREATE TEMP TABLE tmp_pc_v2_region_slice_daily AS
        WITH sales_agg AS (
          SELECT
            date,
            marketplace,
            canonical_product_key,
            COALESCE(region_name, '~') AS region_name,
            COUNT(DISTINCT sale_id)::numeric(18,2) AS sales_count,
            SUM(COALESCE(units_sold, 0))::numeric(18,2) AS units_sold,
            SUM(COALESCE(revenue_gross, 0))::numeric(18,2) AS revenue_gross,
            SUM(COALESCE(revenue_share_basis, 0))::numeric(18,2) AS revenue_share_basis,
            SUM(COALESCE(units_share_basis, 0))::numeric(18,2) AS units_share_basis
          FROM tmp_pc_v2_sales_mapped
          WHERE canonical_product_key <> '~'
          GROUP BY 1,2,3,4
        ),
        orders_agg AS (
          SELECT
            date,
            marketplace,
            canonical_product_key,
            COALESCE(region_name, '~') AS region_name,
            COUNT(DISTINCT order_id)::numeric(18,2) AS orders_count
          FROM tmp_pc_v2_orders_mapped
          WHERE canonical_product_key <> '~'
          GROUP BY 1,2,3,4
        ),
        keys AS (
          SELECT date, marketplace, canonical_product_key, region_name FROM sales_agg
          UNION
          SELECT date, marketplace, canonical_product_key, region_name FROM orders_agg
        )
        SELECT
          k.date,
          k.marketplace,
          k.canonical_product_key,
          k.region_name,
          COALESCE(o.orders_count, 0)::numeric(18,2) AS orders_count,
          COALESCE(s.sales_count, 0)::numeric(18,2) AS sales_count,
          COALESCE(s.units_sold, 0)::numeric(18,2) AS units_sold,
          COALESCE(s.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
          CASE
            WHEN SUM(COALESCE(s.revenue_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(s.revenue_share_basis, 0)
                / SUM(COALESCE(s.revenue_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            WHEN SUM(COALESCE(s.units_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(s.units_share_basis, 0)
                / SUM(COALESCE(s.units_share_basis, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            WHEN SUM(COALESCE(o.orders_count, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key) > 0
              THEN COALESCE(o.orders_count, 0)
                / SUM(COALESCE(o.orders_count, 0)) OVER (PARTITION BY k.date, k.marketplace, k.canonical_product_key)
            ELSE 0::numeric
          END::numeric(18,8) AS alloc_share
        FROM keys k
        LEFT JOIN sales_agg s
          ON s.date = k.date
         AND s.marketplace = k.marketplace
         AND s.canonical_product_key = k.canonical_product_key
         AND s.region_name = k.region_name
        LEFT JOIN orders_agg o
          ON o.date = k.date
         AND o.marketplace = k.marketplace
         AND o.canonical_product_key = k.canonical_product_key
         AND o.region_name = k.region_name;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TABLE {SCHEMA}.{TABLES['warehouse']} AS
        WITH product_base AS (
          SELECT * FROM {SCHEMA}.{TABLES['product']}
        )
        SELECT
          s.date,
          s.marketplace,
          s.warehouse_name,
          SUM(COALESCE(s.orders_count, 0))::numeric(18,2) AS orders_count,
          SUM(COALESCE(s.sales_count, 0))::numeric(18,2) AS sales_count,
          SUM(COALESCE(s.units_sold, 0))::numeric(18,2) AS units_sold,
          SUM(COALESCE(s.revenue_gross, 0))::numeric(18,2) AS revenue_gross,
          SUM(COALESCE(p.ads_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS ads_amount,
          SUM(COALESCE(p.commission_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS commission_amount,
          SUM(COALESCE(p.logistics_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS logistics_amount,
          SUM(COALESCE(p.storage_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS storage_amount,
          SUM(COALESCE(p.acquiring_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS acquiring_amount,
          SUM(COALESCE(p.returns_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS returns_amount,
          SUM(COALESCE(p.other_expenses_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS other_expenses_amount,
          SUM(COALESCE(p.product_services_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS product_services_amount,
          0::numeric(18,2) AS store_services_amount,
          SUM(COALESCE(p.penalties_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS penalties_amount,
          SUM(COALESCE(p.cogs_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS cogs_amount,
          SUM(COALESCE(p.profit_before_ads, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS profit_before_ads,
          SUM(COALESCE(p.profit_after_ads, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS profit_after_ads,
          SUM(COALESCE(p.product_level_expense_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS product_level_expense_amount,
          0::numeric(18,2) AS store_level_expense_amount,
          SUM(COALESCE(p.mixed_expense_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS mixed_expense_amount,
          BOOL_OR(COALESCE(p.has_product_level_expenses, FALSE)) AS has_product_level_expenses,
          FALSE AS has_store_level_expenses,
          BOOL_OR(COALESCE(p.has_mixed_expenses, FALSE)) AS has_mixed_expenses,
          MAX(p.confirmed_through) AS confirmed_through,
          BOOL_AND(COALESCE(p.finance_confirmed_flag, FALSE)) AS finance_confirmed_flag,
          'product_day_revenue_share_with_units_and_orders_fallback'::text AS allocation_method
        FROM tmp_pc_v2_warehouse_slice_daily s
        LEFT JOIN product_base p
          ON p.date = s.date
         AND p.marketplace = s.marketplace
         AND p.canonical_product_key = s.canonical_product_key
        GROUP BY 1,2,3;
        """,
    )

    execute(
        conn,
        f"""
        CREATE TABLE {SCHEMA}.{TABLES['region']} AS
        WITH product_base AS (
          SELECT * FROM {SCHEMA}.{TABLES['product']}
        )
        SELECT
          s.date,
          s.marketplace,
          s.region_name,
          SUM(COALESCE(s.orders_count, 0))::numeric(18,2) AS orders_count,
          SUM(COALESCE(s.sales_count, 0))::numeric(18,2) AS sales_count,
          SUM(COALESCE(s.units_sold, 0))::numeric(18,2) AS units_sold,
          SUM(COALESCE(s.revenue_gross, 0))::numeric(18,2) AS revenue_gross,
          SUM(COALESCE(p.ads_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS ads_amount,
          SUM(COALESCE(p.commission_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS commission_amount,
          SUM(COALESCE(p.logistics_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS logistics_amount,
          SUM(COALESCE(p.storage_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS storage_amount,
          SUM(COALESCE(p.acquiring_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS acquiring_amount,
          SUM(COALESCE(p.returns_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS returns_amount,
          SUM(COALESCE(p.other_expenses_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS other_expenses_amount,
          SUM(COALESCE(p.product_services_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS product_services_amount,
          0::numeric(18,2) AS store_services_amount,
          SUM(COALESCE(p.penalties_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS penalties_amount,
          SUM(COALESCE(p.cogs_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS cogs_amount,
          SUM(COALESCE(p.profit_before_ads, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS profit_before_ads,
          SUM(COALESCE(p.profit_after_ads, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS profit_after_ads,
          SUM(COALESCE(p.product_level_expense_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS product_level_expense_amount,
          0::numeric(18,2) AS store_level_expense_amount,
          SUM(COALESCE(p.mixed_expense_amount, 0) * COALESCE(s.alloc_share, 0))::numeric(18,2) AS mixed_expense_amount,
          BOOL_OR(COALESCE(p.has_product_level_expenses, FALSE)) AS has_product_level_expenses,
          FALSE AS has_store_level_expenses,
          BOOL_OR(COALESCE(p.has_mixed_expenses, FALSE)) AS has_mixed_expenses,
          MAX(p.confirmed_through) AS confirmed_through,
          BOOL_AND(COALESCE(p.finance_confirmed_flag, FALSE)) AS finance_confirmed_flag,
          'product_day_revenue_share_with_units_and_orders_fallback'::text AS allocation_method
        FROM tmp_pc_v2_region_slice_daily s
        LEFT JOIN product_base p
          ON p.date = s.date
         AND p.marketplace = s.marketplace
         AND p.canonical_product_key = s.canonical_product_key
        GROUP BY 1,2,3;
        """,
    )

    index_statements = [
        f"CREATE INDEX pc_v2_daily_store_fact_date_idx ON {SCHEMA}.{TABLES['store']} (marketplace, date);",
        f"CREATE INDEX pc_v2_daily_product_fact_date_idx ON {SCHEMA}.{TABLES['product']} (marketplace, date);",
        f"CREATE INDEX pc_v2_daily_product_fact_offer_idx ON {SCHEMA}.{TABLES['product']} (marketplace, offer_id);",
        f"CREATE INDEX pc_v2_daily_warehouse_fact_date_idx ON {SCHEMA}.{TABLES['warehouse']} (marketplace, date);",
        f"CREATE INDEX pc_v2_daily_region_fact_date_idx ON {SCHEMA}.{TABLES['region']} (marketplace, date);",
    ]
    for sql in index_statements:
        execute(conn, sql)


def collect_structure(conn: Any) -> list[dict[str, Any]]:
    table_sql = ", ".join(f"'{name}'" for name in TABLES.values())
    sql = f"""
    SELECT
      table_name,
      column_name,
      data_type,
      ordinal_position
    FROM information_schema.columns
    WHERE table_schema = %s
      AND table_name IN ({table_sql})
    ORDER BY table_name, ordinal_position
    """
    return fetch_all(conn, sql, (SCHEMA,))


def collect_checks(conn: Any) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    table_tuple = tuple(TABLES.values())

    summary_sql = f"""
    SELECT
      table_name,
      COUNT(*)::bigint AS row_count,
      MIN(date)::text AS min_date,
      MAX(date)::text AS max_date,
      MAX(confirmed_through)::text AS confirmed_through
    FROM (
      SELECT '{TABLES['store']}'::text AS table_name, date, confirmed_through FROM {SCHEMA}.{TABLES['store']}
      UNION ALL
      SELECT '{TABLES['product']}'::text AS table_name, date, confirmed_through FROM {SCHEMA}.{TABLES['product']}
      UNION ALL
      SELECT '{TABLES['warehouse']}'::text AS table_name, date, confirmed_through FROM {SCHEMA}.{TABLES['warehouse']}
      UNION ALL
      SELECT '{TABLES['region']}'::text AS table_name, date, confirmed_through FROM {SCHEMA}.{TABLES['region']}
    ) x
    GROUP BY table_name
    ORDER BY table_name
    """
    for row in fetch_all(conn, summary_sql):
        checks.append({"check_group": "table_summary", **row})

    compare_sql = f"""
    WITH boundary AS (
      SELECT MAX(day)::date AS confirmed_through
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    ),
    old_store AS (
      SELECT
        SUM(COALESCE(revenue_gross, 0))::numeric(18,2) AS revenue_old,
        SUM(COALESCE(ads_spend, 0))::numeric(18,2) AS ads_old,
        SUM(COALESCE(commission_amount, 0))::numeric(18,2) AS commission_old,
        SUM(COALESCE(logistics_amount, 0))::numeric(18,2) AS logistics_old,
        SUM(COALESCE(storage_amount, 0))::numeric(18,2) AS storage_old,
        SUM(COALESCE(services_amount, 0))::numeric(18,2) AS services_old,
        SUM(COALESCE(profit_after_ads, 0))::numeric(18,2) AS profit_after_ads_old
      FROM {SCHEMA}.mart_pnl_daily_by_product
      WHERE marketplace = 'wb'
        AND date <= (SELECT confirmed_through FROM boundary)
    ),
    new_store AS (
      SELECT
        SUM(COALESCE(revenue_gross, 0))::numeric(18,2) AS revenue_new,
        SUM(COALESCE(ads_amount, 0))::numeric(18,2) AS ads_new,
        SUM(COALESCE(commission_amount, 0))::numeric(18,2) AS commission_new,
        SUM(COALESCE(logistics_amount, 0))::numeric(18,2) AS logistics_new,
        SUM(COALESCE(storage_amount, 0))::numeric(18,2) AS storage_new,
        SUM(COALESCE(acquiring_amount, 0) + COALESCE(product_services_amount, 0) + COALESCE(store_services_amount, 0))::numeric(18,2) AS services_like_new,
        SUM(COALESCE(profit_after_ads, 0))::numeric(18,2) AS profit_after_ads_new
      FROM {SCHEMA}.{TABLES['store']}
      WHERE marketplace = 'wb'
        AND finance_confirmed_flag = TRUE
    )
    SELECT * FROM old_store CROSS JOIN new_store
    """
    compare = fetch_one(conn, compare_sql)
    for key, value in compare.items():
        checks.append(
            {
                "check_group": "confirmed_window_compare",
                "table_name": "wb_store",
                "metric_name": key,
                "metric_value": value,
            }
        )

    split_sql = f"""
    SELECT
      'product'::text AS layer,
      SUM(COALESCE(product_level_expense_amount, 0))::numeric(18,2) AS product_level_amount,
      SUM(COALESCE(store_level_expense_amount, 0))::numeric(18,2) AS store_level_amount,
      SUM(COALESCE(mixed_expense_amount, 0))::numeric(18,2) AS mixed_amount
    FROM {SCHEMA}.{TABLES['product']}
    WHERE marketplace = 'wb'
    UNION ALL
    SELECT
      'store'::text AS layer,
      SUM(COALESCE(product_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(store_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(mixed_expense_amount, 0))::numeric(18,2)
    FROM {SCHEMA}.{TABLES['store']}
    WHERE marketplace = 'wb'
    UNION ALL
    SELECT
      'warehouse'::text AS layer,
      SUM(COALESCE(product_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(store_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(mixed_expense_amount, 0))::numeric(18,2)
    FROM {SCHEMA}.{TABLES['warehouse']}
    WHERE marketplace = 'wb'
    UNION ALL
    SELECT
      'region'::text AS layer,
      SUM(COALESCE(product_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(store_level_expense_amount, 0))::numeric(18,2),
      SUM(COALESCE(mixed_expense_amount, 0))::numeric(18,2)
    FROM {SCHEMA}.{TABLES['region']}
    WHERE marketplace = 'wb'
    """
    for row in fetch_all(conn, split_sql):
        checks.append({"check_group": "expense_split", **row})

    return checks


def build_report(conn: Any, structures: list[dict[str, Any]], checks: list[dict[str, Any]]) -> str:
    table_summary = [row for row in checks if row["check_group"] == "table_summary"]
    compare_rows = [row for row in checks if row["check_group"] == "confirmed_window_compare"]
    split_rows = [row for row in checks if row["check_group"] == "expense_split"]

    summary_by_table = {row["table_name"]: row for row in table_summary}
    compare_map = {row["metric_name"]: row["metric_value"] for row in compare_rows}

    lines = [
        "# Profit Control Daily Fact Layer V1",
        "",
        f"- Schema: `{SCHEMA}`",
        "- New tables are built separately from the old contour and do not replace existing marts.",
        f"- New table names: `{TABLES['store']}`, `{TABLES['product']}`, `{TABLES['warehouse']}`, `{TABLES['region']}`",
        "",
        "## 1. What Was Created",
        "",
        f"- `{TABLES['store']}`: day x store x marketplace with direct store totals and confirmed-through flag.",
        f"- `{TABLES['product']}`: day x product x marketplace with separated `acquiring`, `product_services`, and zero store-only services on SKU rows.",
        f"- `{TABLES['warehouse']}`: day x warehouse x marketplace with direct orders/sales/revenue and allocated product-day economics.",
        f"- `{TABLES['region']}`: day x region x marketplace with direct orders/sales/revenue and allocated product-day economics.",
        "",
        "## 2. Source Map",
        "",
        f"- `{TABLES['store']}` sources: `{SCHEMA}.stg_wb_order`, `{SCHEMA}.stg_wb_sale`, `{SCHEMA}.fact_product_money_attribution`, `{SCHEMA}.stg_wb_finance`, `{SCHEMA}.finance_day_state`",
        f"- `{TABLES['product']}` sources: `{SCHEMA}.stg_wb_order`, `{SCHEMA}.stg_wb_sale`, `{SCHEMA}.dim_product_bridge`, `{SCHEMA}.fact_product_money_attribution`, `{SCHEMA}.stg_wb_finance`, `{SCHEMA}.finance_day_state`",
        f"- `{TABLES['warehouse']}` sources: `{SCHEMA}.stg_wb_order`, `{SCHEMA}.stg_wb_sale`, `{TABLES['product']}`",
        f"- `{TABLES['region']}` sources: `{SCHEMA}.stg_wb_order`, `{SCHEMA}.stg_wb_sale`, `{TABLES['product']}`",
        "",
        "## 3. Expense Level Rules",
        "",
        "- Product-level direct: `ads`, `commission`, `logistics`, `acquiring`, direct `product_services`, direct `other`, direct `returns`, direct `penalties`.",
        "- Store-level only: service rows with `canonical_product_key='~'`, currently this is the store-only services layer.",
        "- Mixed / heuristic: rows with attribution methods like `paid_storage_share_day`, `revenue_share_window`, `barcode_match`, `bridge_match`, and fallback COGS methods.",
        "- Warehouse and region tables do not spread store-only services down; those columns stay zero there by design.",
        "",
        "## 4. Separation From Old Contour",
        "",
        "- Old marts are untouched.",
        "- Cabinet pack is untouched.",
        "- Existing runtime ordering is untouched.",
        "- New layer is built by a standalone script and standalone table names only.",
        "",
        "## 5. Short Checks",
        "",
        f"- `confirmedThrough` preserved: `{summary_by_table[TABLES['store']]['confirmed_through']}`",
        f"- Store table max date: `{summary_by_table[TABLES['store']]['max_date']}`",
        f"- Product table max date: `{summary_by_table[TABLES['product']]['max_date']}`",
        f"- Warehouse table max date: `{summary_by_table[TABLES['warehouse']]['max_date']}`",
        f"- Region table max date: `{summary_by_table[TABLES['region']]['max_date']}`",
        f"- Confirmed-window revenue old vs new: `{money_text(compare_map['revenue_old'])}` vs `{money_text(compare_map['revenue_new'])}`",
        f"- Confirmed-window ads old vs new: `{money_text(compare_map['ads_old'])}` vs `{money_text(compare_map['ads_new'])}`",
        f"- Confirmed-window commission old vs new: `{money_text(compare_map['commission_old'])}` vs `{money_text(compare_map['commission_new'])}`",
        f"- Confirmed-window logistics old vs new: `{money_text(compare_map['logistics_old'])}` vs `{money_text(compare_map['logistics_new'])}`",
        f"- Confirmed-window storage old vs new: `{money_text(compare_map['storage_old'])}` vs `{money_text(compare_map['storage_new'])}`",
        f"- Confirmed-window old `services_amount` vs new `acquiring + product_services + store_services`: `{money_text(compare_map['services_old'])}` vs `{money_text(compare_map['services_like_new'])}`",
        f"- Confirmed-window profit-after-ads old vs new: `{money_text(compare_map['profit_after_ads_old'])}` vs `{money_text(compare_map['profit_after_ads_new'])}`",
        "",
        "## 6. Expense Split Snapshot",
        "",
    ]

    for row in split_rows:
        lines.append(
            f"- `{row['layer']}`: product `{money_text(row['product_level_amount'])}`, store `{money_text(row['store_level_amount'])}`, mixed `{money_text(row['mixed_amount'])}`"
        )

    lines.extend(
        [
            "",
            "## 7. What Is Already Enough",
            "",
            "- There is now a separate daily fact layer for store, product, warehouse, and region.",
            "- `confirmedThrough` and `finance_confirmed_flag` are carried into every new table.",
            "- Product vs store separation is explicit instead of hidden inside old buckets.",
            "- `acquiring` is separated from the generic services layer in the new layer.",
            "",
            "## 8. What Is Still Missing",
            "",
            "- Warehouse and region costs are still partially allocated from product-day economics, not direct finance truth at that grain.",
            "- Mixed attribution flags are present, but future versions may need more subtype columns if interpretation grows.",
            "- Store-only services are not pushed into product, warehouse, or region rows by design.",
            "",
            "## 9. Possible Later Additions",
            "",
            "- Separate subtype columns for `review_service`, `jam_subscription`, and `other_service` if the next stage needs them at day grain.",
            "- A dedicated daily subtype layer for `other` components if future interpretation will read reasons directly.",
        ]
    )

    return "\n".join(lines) + "\n"


def main() -> int:
    with get_conn() as conn:
        with conn.transaction():
            build_daily_layer(conn)

    print(f"built daily fact layer in schema {SCHEMA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
