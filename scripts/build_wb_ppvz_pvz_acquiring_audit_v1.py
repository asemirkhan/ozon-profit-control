#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.wb_expense_granularity import (  # noqa: E402
    build_wb_expense_granularity_matrix,
    latest_wb_period,
)
from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA") or "client_demo_90d_20260408"

MATRIX_CSV = ROOT / "wb_ppvz_pvz_acquiring_matrix.csv"
PPVZ_REPORT = ROOT / "wb_ppvz_reward_check.md"
PVZ_REPORT = ROOT / "wb_pvz_check.md"
ACQUIRING_REPORT = ROOT / "wb_acquiring_and_payment_processing_check.md"


def money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def money_text(value: Any) -> str:
    return f"{money(value):.2f}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_text(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def query_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query_one(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    rows = query_rows(conn, sql, params)
    return rows[0] if rows else {}


def numeric_json_expr(field_name: str) -> str:
    return f"""
    CASE
      WHEN COALESCE(w.raw_payload->>'{field_name}', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
        THEN ABS((w.raw_payload->>'{field_name}')::numeric)::numeric(18,2)
      ELSE 0::numeric(18,2)
    END
    """


def fact_rows_cte(schema: str) -> str:
    return f"""
    WITH fact_rows AS (
      SELECT
        split_part(source_record_id, ':', 1) AS finance_id,
        money_bucket,
        SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_level_amount,
        SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_level_amount
      FROM {schema}.fact_product_money_attribution
      WHERE marketplace = 'wb'
        AND money_component_type = 'expense'
        AND source_table = 'stg_wb_finance'
      GROUP BY 1, 2
    ),
    fact_by_finance AS (
      SELECT
        finance_id,
        string_agg(DISTINCT money_bucket, ' | ' ORDER BY money_bucket) AS current_bucket,
        SUM(product_level_amount)::numeric(18,2) AS product_level_amount,
        SUM(store_level_amount)::numeric(18,2) AS store_level_amount
      FROM fact_rows
      GROUP BY 1
    ),
    finance_base AS (
      SELECT
        w.finance_id,
        w.operation_date::date AS day,
        COALESCE(w.operation_type, '') AS operation_type,
        COALESCE(w.supplier_oper_name, '') AS supplier_oper_name,
        COALESCE(w.doc_type_name, '') AS doc_type_name,
        COALESCE(w.raw_payload->>'bonus_type_name', '') AS bonus_type_name,
        COALESCE(w.raw_payload->>'payment_processing', '') AS payment_processing_label,
        ABS(COALESCE(w.amount, 0))::numeric(18,2) AS amount_abs,
        ABS(COALESCE(w.service_amount, 0))::numeric(18,2) AS service_amount_abs,
        ABS(COALESCE(w.commission_amount, 0))::numeric(18,2) AS commission_amount_abs,
        ABS(COALESCE(w.acquiring_fee, 0))::numeric(18,2) AS acquiring_fee_abs,
        {numeric_json_expr('ppvz_reward')} AS ppvz_reward_abs,
        CASE
          WHEN NULLIF(TRIM(COALESCE(w.nm_id::text, '')), '') IS NOT NULL
            AND TRIM(COALESCE(w.nm_id::text, '')) NOT IN ('0', '~')
            THEN 1
          WHEN NULLIF(TRIM(COALESCE(w.offer_id, '')), '') IS NOT NULL
            AND TRIM(COALESCE(w.offer_id, '')) <> '~'
            THEN 1
          WHEN NULLIF(TRIM(COALESCE(w.barcode, '')), '') IS NOT NULL
            AND TRIM(COALESCE(w.barcode, '')) <> '~'
            THEN 1
          ELSE 0
        END AS has_direct_sku_key
      FROM {schema}.stg_wb_finance w
      WHERE COALESCE(w.marketplace, 'wb') = 'wb'
        AND w.operation_date::date BETWEEN %s AND %s
    )
    """


def fetch_matrix_rows(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_rows_cte(schema)}
    SELECT
      source_field,
      raw_label,
      rows,
      amount,
      current_bucket,
      suspected_business_meaning,
      product_store_level,
      has_direct_sku_key_yes_no
    FROM (
      SELECT
        'commission_amount' AS source_field,
        'Продажа | Продажа | Продажа' AS raw_label,
        COUNT(*)::int AS rows,
        SUM(fb.commission_amount_abs)::numeric(18,2) AS amount,
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped') AS current_bucket,
        'Комиссия маркетплейса WB по продажам' AS suspected_business_meaning,
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END AS product_store_level,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END AS has_direct_sku_key_yes_no,
        10 AS sort_key
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.commission_amount_abs > 0
        AND fb.operation_type = 'Продажа'
        AND fb.supplier_oper_name = 'Продажа'
        AND fb.doc_type_name = 'Продажа'

      UNION ALL

      SELECT
        'commission_amount',
        'Возврат | Возврат | Возврат',
        COUNT(*)::int,
        SUM(fb.commission_amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'Сторно / возвратная часть комиссии WB',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        20
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.commission_amount_abs > 0
        AND fb.operation_type = 'Возврат'
        AND fb.supplier_oper_name = 'Возврат'
        AND fb.doc_type_name = 'Возврат'

      UNION ALL

      SELECT
        'amount = raw_payload.ppvz_reward',
        'Продажа | Продажа | Продажа',
        COUNT(*)::int,
        SUM(fb.amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'Вознаграждение Wildberries / ppvz_reward на продаже',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        30
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.ppvz_reward_abs > 0
        AND fb.amount_abs = fb.ppvz_reward_abs
        AND fb.operation_type = 'Продажа'
        AND fb.supplier_oper_name = 'Продажа'
        AND fb.doc_type_name = 'Продажа'

      UNION ALL

      SELECT
        'amount = raw_payload.ppvz_reward',
        'Продажа | Возмещение за выдачу и возврат товаров на ПВЗ | Продажа',
        COUNT(*)::int,
        SUM(fb.amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'Возмещение ПВЗ, пришедшее через ppvz_reward/amount',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        40
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.ppvz_reward_abs > 0
        AND fb.amount_abs = fb.ppvz_reward_abs
        AND fb.supplier_oper_name = 'Возмещение за выдачу и возврат товаров на ПВЗ'

      UNION ALL

      SELECT
        'amount = raw_payload.ppvz_reward',
        'Возврат | Возврат | Возврат',
        COUNT(*)::int,
        SUM(fb.amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'Корректировка / сторно вознаграждения Wildberries на возврате',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        50
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.ppvz_reward_abs > 0
        AND fb.amount_abs = fb.ppvz_reward_abs
        AND fb.operation_type = 'Возврат'
        AND fb.supplier_oper_name = 'Возврат'
        AND fb.doc_type_name = 'Возврат'

      UNION ALL

      SELECT
        'acquiring_fee',
        COALESCE(NULLIF(fb.payment_processing_label, ''), 'Без текстового ярлыка payment_processing'),
        COUNT(*)::int,
        SUM(fb.acquiring_fee_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        CASE COALESCE(NULLIF(fb.payment_processing_label, ''), 'Без текстового ярлыка payment_processing')
          WHEN 'Перевыставление эквайринга' THEN 'Перевыставленный эквайринг'
          WHEN 'Компенсация платёжных услуг' THEN 'Компенсация платёжных услуг'
          WHEN 'Комиссия за организацию платежа с НДС' THEN 'Комиссия за организацию платежа'
          ELSE 'Прочий платежный компонент / эквайринг'
        END,
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        60
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.acquiring_fee_abs > 0
      GROUP BY 2

      UNION ALL

      SELECT
        'amount',
        'Продажа | Возмещение за выдачу и возврат товаров на ПВЗ | Продажа',
        COUNT(*)::int,
        SUM(fb.amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'Возмещение ПВЗ',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        70
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.supplier_oper_name = 'Возмещение за выдачу и возврат товаров на ПВЗ'

      UNION ALL

      SELECT
        'amount',
        'Штраф | Платное хранение возвратов на ПВЗ более 3 дней',
        COUNT(*)::int,
        SUM(fb.amount_abs)::numeric(18,2),
        COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped'),
        'ПВЗ-связанный штраф / хранение возвратов на ПВЗ',
        CASE
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
          WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
          ELSE 'unmapped'
        END,
        CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END,
        80
      FROM finance_base fb
      LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
      WHERE fb.bonus_type_name = 'Платное хранение возвратов на ПВЗ более 3 дней'
    ) x
    WHERE rows > 0
    ORDER BY sort_key, amount DESC, raw_label
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_ppvz_summary(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, Any]:
    sql = f"""
    {fact_rows_cte(schema)}
    SELECT
      COUNT(*) FILTER (WHERE fb.ppvz_reward_abs > 0)::int AS rows_with_ppvz_reward,
      SUM(CASE WHEN fb.ppvz_reward_abs > 0 THEN fb.ppvz_reward_abs ELSE 0 END)::numeric(18,2) AS ppvz_reward_total_any,
      COUNT(*) FILTER (WHERE fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs)::int AS rows_amount_eq_ppvz,
      SUM(CASE WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs THEN fb.amount_abs ELSE 0 END)::numeric(18,2) AS amount_eq_ppvz_total,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
         AND fb.operation_type = 'Продажа' AND fb.supplier_oper_name = 'Продажа' AND fb.doc_type_name = 'Продажа'
          THEN fb.amount_abs
        ELSE 0
      END)::numeric(18,2) AS sale_ppvz_total,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
         AND fb.supplier_oper_name = 'Возмещение за выдачу и возврат товаров на ПВЗ'
          THEN fb.amount_abs
        ELSE 0
      END)::numeric(18,2) AS pvz_ppvz_total,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
         AND fb.operation_type = 'Возврат' AND fb.supplier_oper_name = 'Возврат' AND fb.doc_type_name = 'Возврат'
          THEN fb.amount_abs
        ELSE 0
      END)::numeric(18,2) AS return_ppvz_total,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
          THEN fb.commission_amount_abs
        ELSE 0
      END)::numeric(18,2) AS commission_on_same_rows,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
          THEN fb.acquiring_fee_abs
        ELSE 0
      END)::numeric(18,2) AS acquiring_on_same_rows,
      SUM(CASE
        WHEN fb.ppvz_reward_abs > 0 AND fb.amount_abs = fb.ppvz_reward_abs
          THEN fb.service_amount_abs
        ELSE 0
      END)::numeric(18,2) AS service_on_same_rows
    FROM finance_base fb
    """
    return query_one(conn, sql, (period_start, period_end))


def fetch_ppvz_fact_overlap(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    WITH target_ids AS (
      SELECT finance_id
      FROM {schema}.stg_wb_finance w
      WHERE COALESCE(w.marketplace, 'wb') = 'wb'
        AND w.operation_date::date BETWEEN %s AND %s
        AND COALESCE(w.raw_payload->>'ppvz_reward', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
        AND ABS(COALESCE(w.amount, 0))::numeric(18,2) = ABS((w.raw_payload->>'ppvz_reward')::numeric)::numeric(18,2)
        AND ABS(COALESCE(w.amount, 0)) > 0
    )
    SELECT
      money_bucket,
      COUNT(DISTINCT split_part(source_record_id, ':', 1))::int AS finance_rows,
      SUM(amount)::numeric(18,2) AS fact_amount,
      SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_level_amount,
      SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_level_amount
    FROM {schema}.fact_product_money_attribution
    WHERE marketplace = 'wb'
      AND money_component_type = 'expense'
      AND source_table = 'stg_wb_finance'
      AND split_part(source_record_id, ':', 1) IN (SELECT finance_id FROM target_ids)
    GROUP BY 1
    ORDER BY fact_amount DESC, money_bucket
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_pvz_summary(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_rows_cte(schema)}
    SELECT
      CASE
        WHEN fb.supplier_oper_name = 'Возмещение за выдачу и возврат товаров на ПВЗ'
          THEN 'pvz_reimbursement'
        WHEN fb.bonus_type_name = 'Платное хранение возвратов на ПВЗ более 3 дней'
          THEN 'pvz_return_storage_penalty'
        ELSE 'pvz_other'
      END AS pvz_slice,
      COUNT(*)::int AS rows,
      SUM(fb.amount_abs)::numeric(18,2) AS amount,
      COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped') AS current_bucket,
      CASE
        WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
        WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
        WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
        ELSE 'unmapped'
      END AS product_store_level,
      CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END AS has_direct_sku_key_yes_no
    FROM finance_base fb
    LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
    WHERE fb.supplier_oper_name ILIKE '%%ПВЗ%%'
       OR fb.operation_type ILIKE '%%ПВЗ%%'
       OR fb.doc_type_name ILIKE '%%ПВЗ%%'
       OR fb.bonus_type_name ILIKE '%%ПВЗ%%'
       OR fb.payment_processing_label ILIKE '%%ПВЗ%%'
    GROUP BY 1
    ORDER BY amount DESC, pvz_slice
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_acquiring_summary(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_rows_cte(schema)}
    SELECT
      COALESCE(NULLIF(fb.payment_processing_label, ''), 'Без текстового ярлыка payment_processing') AS payment_processing_label,
      COUNT(*)::int AS rows,
      SUM(fb.acquiring_fee_abs)::numeric(18,2) AS acquiring_fee_total,
      SUM(fb.amount_abs)::numeric(18,2) AS amount_total_on_same_rows,
      COALESCE(string_agg(DISTINCT fa.current_bucket, ' | ' ORDER BY fa.current_bucket), 'unmapped') AS current_bucket,
      CASE
        WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) = 0 THEN 'product'
        WHEN COALESCE(SUM(fa.product_level_amount), 0) = 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'store'
        WHEN COALESCE(SUM(fa.product_level_amount), 0) > 0 AND COALESCE(SUM(fa.store_level_amount), 0) > 0 THEN 'mixed'
        ELSE 'unmapped'
      END AS product_store_level,
      CASE WHEN MIN(fb.has_direct_sku_key) = 1 THEN 'yes' ELSE 'no' END AS has_direct_sku_key_yes_no
    FROM finance_base fb
    LEFT JOIN fact_by_finance fa ON fa.finance_id = fb.finance_id
    WHERE fb.acquiring_fee_abs > 0
    GROUP BY 1
    ORDER BY acquiring_fee_total DESC, payment_processing_label
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_payment_processing_fact_total(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, Any]:
    sql = f"""
    SELECT
      COALESCE(SUM(amount), 0)::numeric(18,2) AS amount,
      COUNT(*)::int AS rows
    FROM {schema}.fact_product_money_attribution
    WHERE marketplace = 'wb'
      AND money_component_type = 'expense'
      AND source_table = 'stg_wb_finance'
      AND money_bucket = 'payment_processing'
      AND event_date BETWEEN %s AND %s
    """
    return query_one(conn, sql, (period_start, period_end))


def fetch_finance_dataset(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      w.finance_id,
      COALESCE(w.operation_type, '') AS operation_type,
      COALESCE(w.supplier_oper_name, '') AS supplier_oper_name,
      COALESCE(w.doc_type_name, '') AS doc_type_name,
      COALESCE(w.raw_payload->>'bonus_type_name', '') AS bonus_type_name,
      COALESCE(w.raw_payload->>'payment_processing', '') AS payment_processing_label,
      ABS(COALESCE(w.amount, 0))::numeric(18,2) AS amount_abs,
      ABS(COALESCE(w.service_amount, 0))::numeric(18,2) AS service_amount_abs,
      ABS(COALESCE(w.commission_amount, 0))::numeric(18,2) AS commission_amount_abs,
      ABS(COALESCE(w.acquiring_fee, 0))::numeric(18,2) AS acquiring_fee_abs,
      CASE
        WHEN COALESCE(w.raw_payload->>'ppvz_reward', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
          THEN ABS((w.raw_payload->>'ppvz_reward')::numeric)::numeric(18,2)
        ELSE 0::numeric(18,2)
      END AS ppvz_reward_abs,
      CASE
        WHEN NULLIF(TRIM(COALESCE(w.nm_id::text, '')), '') IS NOT NULL
          AND TRIM(COALESCE(w.nm_id::text, '')) NOT IN ('0', '~')
          THEN 1
        WHEN NULLIF(TRIM(COALESCE(w.offer_id, '')), '') IS NOT NULL
          AND TRIM(COALESCE(w.offer_id, '')) <> '~'
          THEN 1
        WHEN NULLIF(TRIM(COALESCE(w.barcode, '')), '') IS NOT NULL
          AND TRIM(COALESCE(w.barcode, '')) <> '~'
          THEN 1
        ELSE 0
      END AS has_direct_sku_key
    FROM {schema}.stg_wb_finance w
    WHERE COALESCE(w.marketplace, 'wb') = 'wb'
      AND w.operation_date::date BETWEEN %s AND %s
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_fact_dataset(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      split_part(source_record_id, ':', 1) AS finance_id,
      money_bucket,
      SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_level_amount,
      SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_level_amount
    FROM {schema}.fact_product_money_attribution
    WHERE marketplace = 'wb'
      AND money_component_type = 'expense'
      AND source_table = 'stg_wb_finance'
      AND event_date BETWEEN %s AND %s
    GROUP BY 1, 2
    """
    return query_rows(conn, sql, (period_start, period_end))


def build_fact_maps(fact_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_finance: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "buckets": set(),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
        }
    )
    for row in fact_rows:
        finance_id = str(row["finance_id"])
        item = by_finance[finance_id]
        item["buckets"].add(str(row["money_bucket"]))
        item["product_level_amount"] += money(row["product_level_amount"])
        item["store_level_amount"] += money(row["store_level_amount"])
    final_map: dict[str, dict[str, Any]] = {}
    for finance_id, item in by_finance.items():
        final_map[finance_id] = {
            "current_bucket": " | ".join(sorted(item["buckets"])) if item["buckets"] else "unmapped",
            "product_level_amount": item["product_level_amount"],
            "store_level_amount": item["store_level_amount"],
            "buckets": item["buckets"],
        }
    normalized_fact_rows = [
        {
            "finance_id": str(row["finance_id"]),
            "money_bucket": str(row["money_bucket"]),
            "product_level_amount": money(row["product_level_amount"]),
            "store_level_amount": money(row["store_level_amount"]),
            "fact_amount": money(row["product_level_amount"]) + money(row["store_level_amount"]),
        }
        for row in fact_rows
    ]
    return final_map, normalized_fact_rows


def aggregate_slice(
    finance_rows: list[dict[str, Any]],
    fact_map: dict[str, dict[str, Any]],
    amount_field: str,
    source_field: str,
    raw_label: str,
    suspected_business_meaning: str,
) -> dict[str, Any]:
    buckets: set[str] = set()
    product_total = Decimal("0.00")
    store_total = Decimal("0.00")
    amount_total = Decimal("0.00")
    all_have_sku = True
    for row in finance_rows:
        amount_total += money(row[amount_field])
        if not row["has_direct_sku_key"]:
            all_have_sku = False
        fact = fact_map.get(str(row["finance_id"]))
        if fact:
            buckets.update(fact["buckets"])
            product_total += money(fact["product_level_amount"])
            store_total += money(fact["store_level_amount"])
    if product_total > 0 and store_total == 0:
        level = "product"
    elif product_total == 0 and store_total > 0:
        level = "store"
    elif product_total > 0 and store_total > 0:
        level = "mixed"
    else:
        level = "unmapped"
    return {
        "source_field": source_field,
        "raw_label": raw_label,
        "rows": len(finance_rows),
        "amount": amount_total,
        "current_bucket": " | ".join(sorted(buckets)) if buckets else "unmapped",
        "suspected_business_meaning": suspected_business_meaning,
        "product_store_level": level,
        "has_direct_sku_key_yes_no": "yes" if all_have_sku and finance_rows else "no",
    }


def build_matrix_rows_from_datasets(
    finance_rows: list[dict[str, Any]],
    fact_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    def pick(predicate: Any) -> list[dict[str, Any]]:
        return [row for row in finance_rows if predicate(row)]

    result.append(
        aggregate_slice(
            pick(
                lambda r: r["commission_amount_abs"] > 0
                and r["operation_type"] == "Продажа"
                and r["supplier_oper_name"] == "Продажа"
                and r["doc_type_name"] == "Продажа"
            ),
            fact_map,
            "commission_amount_abs",
            "commission_amount",
            "Продажа | Продажа | Продажа",
            "Комиссия маркетплейса WB по продажам",
        )
    )
    result.append(
        aggregate_slice(
            pick(
                lambda r: r["commission_amount_abs"] > 0
                and r["operation_type"] == "Возврат"
                and r["supplier_oper_name"] == "Возврат"
                and r["doc_type_name"] == "Возврат"
            ),
            fact_map,
            "commission_amount_abs",
            "commission_amount",
            "Возврат | Возврат | Возврат",
            "Сторно / возвратная часть комиссии WB",
        )
    )
    result.append(
        aggregate_slice(
            pick(
                lambda r: r["ppvz_reward_abs"] > 0
                and r["amount_abs"] == r["ppvz_reward_abs"]
                and r["operation_type"] == "Продажа"
                and r["supplier_oper_name"] == "Продажа"
                and r["doc_type_name"] == "Продажа"
            ),
            fact_map,
            "amount_abs",
            "amount = raw_payload.ppvz_reward",
            "Продажа | Продажа | Продажа",
            "Вознаграждение Wildberries / ppvz_reward на продаже",
        )
    )
    result.append(
        aggregate_slice(
            pick(
                lambda r: r["ppvz_reward_abs"] > 0
                and r["amount_abs"] == r["ppvz_reward_abs"]
                and r["supplier_oper_name"] == "Возмещение за выдачу и возврат товаров на ПВЗ"
            ),
            fact_map,
            "amount_abs",
            "amount = raw_payload.ppvz_reward",
            "Продажа | Возмещение за выдачу и возврат товаров на ПВЗ | Продажа",
            "Возмещение ПВЗ, пришедшее через ppvz_reward/amount",
        )
    )
    result.append(
        aggregate_slice(
            pick(
                lambda r: r["ppvz_reward_abs"] > 0
                and r["amount_abs"] == r["ppvz_reward_abs"]
                and r["operation_type"] == "Возврат"
                and r["supplier_oper_name"] == "Возврат"
                and r["doc_type_name"] == "Возврат"
            ),
            fact_map,
            "amount_abs",
            "amount = raw_payload.ppvz_reward",
            "Возврат | Возврат | Возврат",
            "Корректировка / сторно вознаграждения Wildberries на возврате",
        )
    )
    for label, meaning in [
        ("Перевыставление эквайринга", "Перевыставленный эквайринг"),
        ("Компенсация платёжных услуг", "Компенсация платёжных услуг"),
        ("Комиссия за организацию платежа с НДС", "Комиссия за организацию платежа"),
    ]:
        rows = pick(lambda r, label=label: r["acquiring_fee_abs"] > 0 and r["payment_processing_label"] == label)
        result.append(
            aggregate_slice(
                rows,
                fact_map,
                "acquiring_fee_abs",
                "acquiring_fee",
                label,
                meaning,
            )
        )
    result.append(
        aggregate_slice(
            pick(lambda r: r["supplier_oper_name"] == "Возмещение за выдачу и возврат товаров на ПВЗ"),
            fact_map,
            "amount_abs",
            "amount",
            "Продажа | Возмещение за выдачу и возврат товаров на ПВЗ | Продажа",
            "Возмещение ПВЗ",
        )
    )
    result.append(
        aggregate_slice(
            pick(lambda r: r["bonus_type_name"] == "Платное хранение возвратов на ПВЗ более 3 дней"),
            fact_map,
            "amount_abs",
            "amount",
            "Штраф | Платное хранение возвратов на ПВЗ более 3 дней",
            "ПВЗ-связанный штраф / хранение возвратов на ПВЗ",
        )
    )
    return [row for row in result if row["rows"] > 0]


def build_ppvz_summary_from_dataset(finance_rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "rows_with_ppvz_reward": 0,
        "ppvz_reward_total_any": Decimal("0.00"),
        "rows_amount_eq_ppvz": 0,
        "amount_eq_ppvz_total": Decimal("0.00"),
        "sale_ppvz_total": Decimal("0.00"),
        "pvz_ppvz_total": Decimal("0.00"),
        "return_ppvz_total": Decimal("0.00"),
        "commission_on_same_rows": Decimal("0.00"),
        "acquiring_on_same_rows": Decimal("0.00"),
        "service_on_same_rows": Decimal("0.00"),
    }
    for row in finance_rows:
        if row["ppvz_reward_abs"] > 0:
            summary["rows_with_ppvz_reward"] += 1
            summary["ppvz_reward_total_any"] += money(row["ppvz_reward_abs"])
        if row["ppvz_reward_abs"] > 0 and row["amount_abs"] == row["ppvz_reward_abs"] and row["amount_abs"] > 0:
            summary["rows_amount_eq_ppvz"] += 1
            summary["amount_eq_ppvz_total"] += money(row["amount_abs"])
            summary["commission_on_same_rows"] += money(row["commission_amount_abs"])
            summary["acquiring_on_same_rows"] += money(row["acquiring_fee_abs"])
            summary["service_on_same_rows"] += money(row["service_amount_abs"])
            if (
                row["operation_type"] == "Продажа"
                and row["supplier_oper_name"] == "Продажа"
                and row["doc_type_name"] == "Продажа"
            ):
                summary["sale_ppvz_total"] += money(row["amount_abs"])
            if row["supplier_oper_name"] == "Возмещение за выдачу и возврат товаров на ПВЗ":
                summary["pvz_ppvz_total"] += money(row["amount_abs"])
            if (
                row["operation_type"] == "Возврат"
                and row["supplier_oper_name"] == "Возврат"
                and row["doc_type_name"] == "Возврат"
            ):
                summary["return_ppvz_total"] += money(row["amount_abs"])
    return summary


def build_ppvz_fact_overlap_from_dataset(
    finance_rows: list[dict[str, Any]],
    fact_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    target_ids = {
        str(row["finance_id"])
        for row in finance_rows
        if row["ppvz_reward_abs"] > 0 and row["amount_abs"] == row["ppvz_reward_abs"] and row["amount_abs"] > 0
    }
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "money_bucket": "",
            "finance_ids": set(),
            "fact_amount": Decimal("0.00"),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
        }
    )
    for row in fact_rows:
        if row["finance_id"] not in target_ids:
            continue
        item = agg[row["money_bucket"]]
        item["money_bucket"] = row["money_bucket"]
        item["finance_ids"].add(row["finance_id"])
        item["fact_amount"] += money(row["fact_amount"])
        item["product_level_amount"] += money(row["product_level_amount"])
        item["store_level_amount"] += money(row["store_level_amount"])
    result = []
    for item in agg.values():
        result.append(
            {
                "money_bucket": item["money_bucket"],
                "finance_rows": len(item["finance_ids"]),
                "fact_amount": item["fact_amount"],
                "product_level_amount": item["product_level_amount"],
                "store_level_amount": item["store_level_amount"],
            }
        )
    result.sort(key=lambda x: (-money(x["fact_amount"]), str(x["money_bucket"])))
    return result


def build_pvz_summary_from_dataset(
    finance_rows: list[dict[str, Any]],
    fact_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = {
        "pvz_reimbursement": [row for row in finance_rows if row["supplier_oper_name"] == "Возмещение за выдачу и возврат товаров на ПВЗ"],
        "pvz_return_storage_penalty": [row for row in finance_rows if row["bonus_type_name"] == "Платное хранение возвратов на ПВЗ более 3 дней"],
    }
    result = []
    for key, rows in groups.items():
        if not rows:
            continue
        agg = aggregate_slice(rows, fact_map, "amount_abs", "amount", key, key)
        result.append(
            {
                "pvz_slice": key,
                "rows": agg["rows"],
                "amount": agg["amount"],
                "current_bucket": agg["current_bucket"],
                "product_store_level": agg["product_store_level"],
                "has_direct_sku_key_yes_no": agg["has_direct_sku_key_yes_no"],
            }
        )
    return result


def build_acquiring_summary_from_dataset(
    finance_rows: list[dict[str, Any]],
    fact_map: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for label in [
        "Перевыставление эквайринга",
        "Компенсация платёжных услуг",
        "Комиссия за организацию платежа с НДС",
    ]:
        rows = [row for row in finance_rows if row["acquiring_fee_abs"] > 0 and row["payment_processing_label"] == label]
        if not rows:
            continue
        agg = aggregate_slice(rows, fact_map, "acquiring_fee_abs", "acquiring_fee", label, label)
        amount_total_on_same_rows = sum(money(row["amount_abs"]) for row in rows)
        result.append(
            {
                "payment_processing_label": label,
                "rows": agg["rows"],
                "acquiring_fee_total": agg["amount"],
                "amount_total_on_same_rows": amount_total_on_same_rows,
                "current_bucket": agg["current_bucket"],
                "product_store_level": agg["product_store_level"],
                "has_direct_sku_key_yes_no": agg["has_direct_sku_key_yes_no"],
            }
        )
    return result


def build_matrix_output(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_field": row["source_field"],
            "raw_label": row["raw_label"],
            "rows": row["rows"],
            "amount": money_text(row["amount"]),
            "current_bucket": row["current_bucket"],
            "suspected_business_meaning": row["suspected_business_meaning"],
            "product/store level": row["product_store_level"],
            "has_direct_sku_key_yes_no": row["has_direct_sku_key_yes_no"],
        }
        for row in rows
    ]


def build_ppvz_report(
    schema: str,
    period_start: str,
    period_end: str,
    ppvz_summary: dict[str, Any],
    ppvz_fact_overlap: list[dict[str, Any]],
    matrix_by_code: dict[str, dict[str, Any]],
) -> str:
    overlap_lines = "\n".join(
        f"- `{row['money_bucket']}`: fact_amount `{money_text(row['fact_amount'])}`, finance_rows `{row['finance_rows']}`"
        for row in ppvz_fact_overlap
    )
    other_amount = money(matrix_by_code["other_sale_adjustment"]["amount"]) + money(matrix_by_code["other_pvz_reimbursement"]["amount"])
    return f"""
# WB PPVZ Reward Check

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Raw totals

- rows with non-zero `ppvz_reward`: `{ppvz_summary['rows_with_ppvz_reward']}`
- total `ppvz_reward` from raw field: `{money_text(ppvz_summary['ppvz_reward_total_any'])}`
- rows where `amount = ppvz_reward`: `{ppvz_summary['rows_amount_eq_ppvz']}`
- total where `amount = ppvz_reward`: `{money_text(ppvz_summary['amount_eq_ppvz_total'])}`

Split of `amount = ppvz_reward`:
- sale rows `Продажа/Продажа/Продажа`: `{money_text(ppvz_summary['sale_ppvz_total'])}`
- PVZ reimbursement rows: `{money_text(ppvz_summary['pvz_ppvz_total'])}`
- return rows `Возврат/Возврат/Возврат`: `{money_text(ppvz_summary['return_ppvz_total'])}`

## 2. How it lands in the current model

Primary placement of the `amount = ppvz_reward` component:
- current refined `other_sale_adjustment`: `{money_text(matrix_by_code['other_sale_adjustment']['amount'])}`
- current refined `other_pvz_reimbursement`: `{money_text(matrix_by_code['other_pvz_reimbursement']['amount'])}`
- current refined `returns`: includes ppvz-related return reversal `{money_text(ppvz_summary['return_ppvz_total'])}`

Raw-row overlap in fact buckets for the same finance ids:
{overlap_lines}

Interpretation:
- `ppvz_reward` currently lands primarily in `other`
- return-side `ppvz_reward` reversal lands in `returns`
- the same raw rows also carry separate `commission_amount` and `acquiring_fee`, so row-level overlap with `commission` and `services` exists, but this is not proof that `ppvz_reward` itself is those components

## 3. Overlap answers

- `overlaps_with_commission_yes_no`: `yes` on the same raw rows, `no` as the same source field
- `overlaps_with_other_yes_no`: `yes`, this is the main current placement

Same-row neighboring fields on the `amount = ppvz_reward` rows:
- `commission_amount` on same rows: `{money_text(ppvz_summary['commission_on_same_rows'])}`
- `acquiring_fee` on same rows: `{money_text(ppvz_summary['acquiring_on_same_rows'])}`
- `service_amount` on same rows: `{money_text(ppvz_summary['service_on_same_rows'])}`

## 4. Can it be put into commission / WB reward bucket

- as plain `commission_core`: `no`
- as separate visible subtype `WB reward / ppvz_reward` inside the broader commission family: `yes`

Why:
- official WB semantics treat WB reward, acquiring rebill, payment organization commission, and WB reward correction as separate articles
- raw field name is directly `ppvz_reward`
- on the strongest slice, `amount` matches `ppvz_reward` 1:1 on sale rows
- current `other` placement is mechanical, not semantically clean

Conclusion:
- the ambiguous block should not stay in `other` under a random name
- the cleanest target is a dedicated subtype like `Вознаграждение Wildberries (ppvz_reward)` with a separate return-side correction line
- current `other` contamination from ppvz-related components is `{money_text(other_amount)}`
"""


def build_pvz_report(
    schema: str,
    period_start: str,
    period_end: str,
    pvz_summary: list[dict[str, Any]],
    matrix_by_code: dict[str, dict[str, Any]],
) -> str:
    total_pvz = sum(money(row["amount"]) for row in pvz_summary)
    summary_lines = "\n".join(
        f"- `{row['pvz_slice']}`: amount `{money_text(row['amount'])}`, rows `{row['rows']}`, current_bucket `{row['current_bucket']}`"
        for row in pvz_summary
    )
    return f"""
# WB PVZ Check

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. All raw rows with PVZ markers

- total PVZ-marked amount: `{money_text(total_pvz)}`

Breakdown:
{summary_lines}

Human-readable interpretation:
- `pvz_reimbursement` = `Возмещение ПВЗ`
- `pvz_return_storage_penalty` = `ПВЗ-связанный штраф / хранение возвратов на ПВЗ`

## 2. What is confirmed

- the small block `{money_text(matrix_by_code['other_pvz_reimbursement']['amount'])}` is real and confirmed by raw label `Возмещение за выдачу и возврат товаров на ПВЗ`
- but it is not all PVZ-related economics in the period
- there is an extra PVZ-related block outside that reimbursement: `260.00` from `Платное хранение возвратов на ПВЗ более 3 дней`

## 3. Conclusion

- `2011.39` must not be treated as the whole PVZ economy
- the reimbursement can stay as a separate visible subtype `Возмещение ПВЗ`
- the additional PVZ-related `260.00` should be shown separately, not silently absorbed into a generic tail
"""


def build_acquiring_report(
    schema: str,
    period_start: str,
    period_end: str,
    acquiring_summary: list[dict[str, Any]],
    payment_processing_fact_total: dict[str, Any],
    matrix_by_code: dict[str, dict[str, Any]],
) -> str:
    acquiring_total = sum(money(row["acquiring_fee_total"]) for row in acquiring_summary)
    detail_lines = "\n".join(
        f"- `{row['payment_processing_label']}`: acquiring_fee `{money_text(row['acquiring_fee_total'])}`, rows `{row['rows']}`, current_bucket `{row['current_bucket']}`"
        for row in acquiring_summary
    )
    return f"""
# WB Acquiring And Payment Processing Check

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Raw totals

- total `acquiring_fee`: `{money_text(acquiring_total)}`
- current refined `acquiring` bucket: `{money_text(matrix_by_code['acquiring']['amount'])}`
- current refined `payment_processing` bucket: `{money_text(matrix_by_code['payment_processing']['amount'])}`
- fact `money_bucket='payment_processing'`: `{money_text(payment_processing_fact_total['amount'])}`

## 2. Payment-related raw labels

{detail_lines}

Interpretation:
- `Перевыставление эквайринга` is the dominant payment-related label
- `Комиссия за организацию платежа с НДС` exists as a distinct raw label
- but the current model does not create a separate monetary `payment_processing` layer from these rows

## 3. What can be separated cleanly

- `acquiring_fee` can be separated cleanly as `Эквайринг`
- `Комиссия за организацию платежа с НДС` is textually present, but in the current raw/model setup it does not arrive as its own standalone fact bucket
- therefore:
  - `Эквайринг` = `yes, separate bucket`
  - `Организация платежа` = `label confirmed, standalone bucket not yet evidenced by fact amount`

## 4. Leakage into other

- no evidence that `acquiring_fee` itself is being booked into `other` as a standalone monetary component
- what does happen is row-level coexistence: sale rows carrying `ppvz_reward` may also carry `acquiring_fee`
- so `other` contamination here is semantic only if rows are interpreted carelessly, not because the source field `acquiring_fee` is itself routed into `other`
"""


def main() -> int:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        matrix_by_code = {str(row["bucket_subtype"]): row for row in matrix_rows}
        finance_rows = fetch_finance_dataset(conn, SCHEMA, period_start, period_end)
        fact_dataset = fetch_fact_dataset(conn, SCHEMA, period_start, period_end)
        fact_map, fact_rows = build_fact_maps(fact_dataset)

        matrix_rows_raw = build_matrix_rows_from_datasets(finance_rows, fact_map)
        ppvz_summary = build_ppvz_summary_from_dataset(finance_rows)
        ppvz_fact_overlap = build_ppvz_fact_overlap_from_dataset(finance_rows, fact_rows)
        pvz_summary = build_pvz_summary_from_dataset(finance_rows, fact_map)
        acquiring_summary = build_acquiring_summary_from_dataset(finance_rows, fact_map)
        payment_processing_fact_total = fetch_payment_processing_fact_total(conn, SCHEMA, period_start, period_end)

    write_csv(
        MATRIX_CSV,
        build_matrix_output(matrix_rows_raw),
        [
            "source_field",
            "raw_label",
            "rows",
            "amount",
            "current_bucket",
            "suspected_business_meaning",
            "product/store level",
            "has_direct_sku_key_yes_no",
        ],
    )
    write_text(
        PPVZ_REPORT,
        build_ppvz_report(
            SCHEMA,
            period_start,
            period_end,
            ppvz_summary,
            ppvz_fact_overlap,
            matrix_by_code,
        ),
    )
    write_text(
        PVZ_REPORT,
        build_pvz_report(
            SCHEMA,
            period_start,
            period_end,
            pvz_summary,
            matrix_by_code,
        ),
    )
    write_text(
        ACQUIRING_REPORT,
        build_acquiring_report(
            SCHEMA,
            period_start,
            period_end,
            acquiring_summary,
            payment_processing_fact_total,
            matrix_by_code,
        ),
    )
    print(f"Wrote {MATRIX_CSV}")
    print(f"Wrote {PPVZ_REPORT}")
    print(f"Wrote {PVZ_REPORT}")
    print(f"Wrote {ACQUIRING_REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
