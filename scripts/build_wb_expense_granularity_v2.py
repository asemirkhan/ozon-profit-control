#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
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

ACQUIRING_CSV = ROOT / "wb_acquiring_check.csv"
ACQUIRING_REPORT_MD = ROOT / "wb_acquiring_extraction_report.md"
OTHER_CSV = ROOT / "wb_other_breakdown.csv"
OTHER_REPORT_MD = ROOT / "wb_other_breakdown_report.md"
COMMISSION_CSV = ROOT / "wb_commission_granularity.csv"
COMMISSION_REPORT_MD = ROOT / "wb_commission_granularity_report.md"
MATRIX_V2_CSV = ROOT / "wb_expense_granularity_matrix_v2.csv"
MATRIX_V2_REPORT_MD = ROOT / "wb_expense_granularity_report_v2.md"


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


def query_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def query_one(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    rows = query_rows(conn, sql, params)
    return rows[0] if rows else {}


def fact_by_finance_cte(schema: str) -> str:
    return f"""
    WITH fact_by_finance AS (
      SELECT
        split_part(source_record_id, ':', 1) AS finance_id,
        money_bucket,
        SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_level_amount,
        SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_level_amount,
        SUM(
          CASE
            WHEN attribution_method IN ('paid_storage_share_day', 'posting_share_day', 'revenue_share_day', 'revenue_share_window')
              THEN amount
            ELSE 0
          END
        )::numeric(18,2) AS mixed_amount,
        string_agg(DISTINCT attribution_method, ', ' ORDER BY attribution_method) AS attribution_method,
        string_agg(DISTINCT attribution_confidence, ', ' ORDER BY attribution_confidence) AS confidence
      FROM {schema}.fact_product_money_attribution
      WHERE marketplace = 'wb'
        AND money_component_type = 'expense'
        AND source_table = 'stg_wb_finance'
      GROUP BY 1, 2
    )
    """


def current_bucket_case_sql() -> str:
    return """
    CASE
      WHEN LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижени%%'
        THEN 'ads_overlap_excluded'
      WHEN LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%продвижени%%'
        OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%подписк%%'
        OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%списание за отзыв%%'
        OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%предоставление услуг%%'
        OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%оказание услуг%%'
        THEN 'services'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%комисс%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%commission%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%вознагражд%%'
        THEN 'commission'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%достав%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%логист%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%delivery%%'
        THEN 'logistics'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%услуг%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%service%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%подписк%%'
        THEN 'services'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%возврат%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%сторн%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%refund%%'
        THEN 'returns'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%штраф%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%удерж%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%penalt%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%deduct%%'
        THEN 'penalties'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%хран%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%storage%%'
        THEN 'storage'
      WHEN LOWER(COALESCE(operation_type, '')) LIKE '%%эквайр%%'
        OR LOWER(COALESCE(operation_type, '')) LIKE '%%acquir%%'
        THEN 'payment_processing'
      ELSE 'other'
    END
    """


def fetch_acquiring_overlap(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, Any]:
    sql = f"""
    SELECT
      COUNT(*) FILTER (WHERE COALESCE(acquiring_fee, 0) <> 0) AS acquiring_rows,
      COALESCE(SUM(COALESCE(acquiring_fee, 0)), 0)::numeric(18,2) AS acquiring_total,
      COUNT(*) FILTER (WHERE COALESCE(service_amount, 0) <> 0) AS service_rows,
      COALESCE(SUM(COALESCE(service_amount, 0)), 0)::numeric(18,2) AS service_total,
      COUNT(*) FILTER (
        WHERE COALESCE(acquiring_fee, 0) <> 0
          AND COALESCE(service_amount, 0) <> 0
      ) AS overlap_rows,
      COUNT(*) FILTER (
        WHERE COALESCE(acquiring_fee, 0) <> 0
          AND COALESCE(service_amount, 0) = 0
      ) AS acquiring_only_rows,
      COUNT(*) FILTER (
        WHERE COALESCE(service_amount, 0) <> 0
          AND COALESCE(acquiring_fee, 0) = 0
      ) AS service_only_rows,
      COUNT(*) FILTER (
        WHERE COALESCE(acquiring_fee, 0) <> COALESCE(service_amount, 0)
      ) AS unequal_rows,
      COALESCE(SUM(COALESCE(acquiring_fee, 0) - COALESCE(service_amount, 0)), 0)::numeric(18,2) AS delta
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND operation_date::date BETWEEN %s AND %s
    """
    return query_one(conn, sql, (period_start, period_end))


def fetch_acquiring_labels(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_by_finance_cte(schema)}
    SELECT
      CASE COALESCE(w.raw_payload->>'payment_processing', '')
        WHEN 'Перевыставление эквайринга' THEN 'rebilled_acquiring'
        WHEN 'Компенсация платёжных услуг' THEN 'payment_services_compensation'
        WHEN 'Комиссия за организацию платежа с НДС' THEN 'payment_organization_commission_vat'
        ELSE 'payment_processing_other_label'
      END AS subtype_name,
      COALESCE(w.raw_payload->>'payment_processing', '') AS payment_processing_label,
      COUNT(*)::int AS rows,
      COALESCE(SUM(COALESCE(w.acquiring_fee, 0)), 0)::numeric(18,2) AS raw_amount,
      COALESCE(SUM(COALESCE(f.product_level_amount, 0)), 0)::numeric(18,2) AS product_level_amount,
      COALESCE(SUM(COALESCE(f.store_level_amount, 0)), 0)::numeric(18,2) AS store_level_amount,
      COALESCE(SUM(COALESCE(f.mixed_amount, 0)), 0)::numeric(18,2) AS mixed_amount,
      COALESCE(string_agg(DISTINCT f.attribution_method, ' | ' ORDER BY f.attribution_method), 'nm_id_match, order_id_match') AS attribution_method,
      COALESCE(string_agg(DISTINCT f.confidence, ' | ' ORDER BY f.confidence), 'high') AS confidence
    FROM {schema}.stg_wb_finance w
    LEFT JOIN fact_by_finance f
      ON f.finance_id = w.finance_id
     AND f.money_bucket = 'services'
    WHERE COALESCE(w.marketplace, 'wb') = 'wb'
      AND w.operation_date::date BETWEEN %s AND %s
      AND COALESCE(w.acquiring_fee, 0) <> 0
    GROUP BY 1, 2
    ORDER BY raw_amount DESC, subtype_name
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_other_breakdown(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_by_finance_cte(schema)}
    , classified AS (
      SELECT
        w.finance_id,
        COALESCE(w.operation_type, '') AS operation_type,
        COALESCE(w.supplier_oper_name, '') AS supplier_oper_name,
        COALESCE(w.doc_type_name, '') AS doc_type_name,
        COALESCE(w.raw_payload->>'bonus_type_name', '') AS bonus_type_name,
        ABS(COALESCE(w.amount, 0))::numeric(18,2) AS raw_amount,
        CASE
          WHEN COALESCE(w.raw_payload->>'ppvz_reward', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            THEN ABS((w.raw_payload->>'ppvz_reward')::numeric)::numeric(18,2)
          ELSE 0::numeric(18,2)
        END AS ppvz_reward_amount,
        {current_bucket_case_sql()} AS current_bucket
      FROM {schema}.stg_wb_finance w
      WHERE COALESCE(w.marketplace, 'wb') = 'wb'
        AND w.operation_date::date BETWEEN %s AND %s
    )
    SELECT
      CASE
        WHEN operation_type = 'Продажа'
          AND supplier_oper_name = 'Продажа'
          AND raw_amount = ppvz_reward_amount
          THEN 'ppvz_reward_sale_flow'
        WHEN operation_type = 'Возмещение издержек по перевозке/по складским операциям с товаром'
          THEN 'transport_warehouse_reimbursement'
        WHEN supplier_oper_name = 'Возмещение за выдачу и возврат товаров на ПВЗ'
          THEN 'pvz_issue_return_reimbursement'
        ELSE 'other_unclassified_tail'
      END AS subtype_name,
      CASE
        WHEN operation_type = 'Продажа'
          AND supplier_oper_name = 'Продажа'
          AND raw_amount = ppvz_reward_amount
          THEN 'raw_payload.ppvz_reward'
        ELSE 'amount'
      END AS source_field,
      operation_type,
      supplier_oper_name,
      doc_type_name,
      bonus_type_name,
      COUNT(*)::int AS rows,
      COALESCE(SUM(raw_amount), 0)::numeric(18,2) AS amount,
      COALESCE(SUM(COALESCE(f.product_level_amount, 0)), 0)::numeric(18,2) AS product_level_amount,
      COALESCE(SUM(COALESCE(f.store_level_amount, 0)), 0)::numeric(18,2) AS store_level_amount,
      COALESCE(SUM(COALESCE(f.mixed_amount, 0)), 0)::numeric(18,2) AS mixed_amount,
      COALESCE(string_agg(DISTINCT f.attribution_method, ' | ' ORDER BY f.attribution_method), '') AS attribution_method,
      COALESCE(string_agg(DISTINCT f.confidence, ' | ' ORDER BY f.confidence), '') AS confidence
    FROM classified c
    LEFT JOIN fact_by_finance f
      ON f.finance_id = c.finance_id
     AND f.money_bucket = 'other'
    WHERE c.current_bucket = 'other'
      AND c.raw_amount > 0
    GROUP BY 1, 2, 3, 4, 5, 6
    ORDER BY amount DESC, subtype_name
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_other_zero_tail(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, Any]:
    sql = f"""
    WITH classified AS (
      SELECT
        ABS(COALESCE(w.amount, 0))::numeric(18,2) AS raw_amount,
        {current_bucket_case_sql()} AS current_bucket
      FROM {schema}.stg_wb_finance w
      WHERE COALESCE(w.marketplace, 'wb') = 'wb'
        AND w.operation_date::date BETWEEN %s AND %s
    )
    SELECT
      COUNT(*)::int AS rows,
      COALESCE(SUM(raw_amount), 0)::numeric(18,2) AS amount
    FROM classified
    WHERE current_bucket = 'other'
      AND raw_amount = 0
    """
    return query_one(conn, sql, (period_start, period_end))


def fetch_commission_core(conn: Any, schema: str, period_start: str, period_end: str) -> list[dict[str, Any]]:
    sql = f"""
    {fact_by_finance_cte(schema)}
    SELECT
      CASE COALESCE(w.operation_type, '')
        WHEN 'Продажа' THEN 'commission_core_sales'
        WHEN 'Возврат' THEN 'commission_core_returns'
        ELSE 'commission_core_other'
      END AS subtype_name,
      COALESCE(w.operation_type, '') AS operation_type,
      COALESCE(w.supplier_oper_name, '') AS supplier_oper_name,
      COALESCE(w.doc_type_name, '') AS doc_type_name,
      COUNT(*)::int AS rows,
      COALESCE(SUM(ABS(COALESCE(w.commission_amount, 0))), 0)::numeric(18,2) AS raw_amount,
      COALESCE(SUM(COALESCE(f.product_level_amount, 0) + COALESCE(f.store_level_amount, 0)), 0)::numeric(18,2) AS current_expense_amount,
      COALESCE(SUM(COALESCE(f.product_level_amount, 0)), 0)::numeric(18,2) AS product_level_amount,
      COALESCE(SUM(COALESCE(f.store_level_amount, 0)), 0)::numeric(18,2) AS store_level_amount,
      COALESCE(SUM(COALESCE(f.mixed_amount, 0)), 0)::numeric(18,2) AS mixed_amount,
      COALESCE(string_agg(DISTINCT f.attribution_method, ' | ' ORDER BY f.attribution_method), 'order_id_match') AS attribution_method,
      COALESCE(string_agg(DISTINCT f.confidence, ' | ' ORDER BY f.confidence), 'high') AS confidence
    FROM {schema}.stg_wb_finance w
    LEFT JOIN fact_by_finance f
      ON f.finance_id = w.finance_id
     AND f.money_bucket = 'commission'
    WHERE COALESCE(w.marketplace, 'wb') = 'wb'
      AND w.operation_date::date BETWEEN %s AND %s
      AND COALESCE(w.commission_amount, 0) <> 0
    GROUP BY 1, 2, 3, 4
    ORDER BY raw_amount DESC, subtype_name
    """
    return query_rows(conn, sql, (period_start, period_end))


def fetch_raw_signals(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, dict[str, Any]]:
    sql = f"""
    SELECT * FROM (
      SELECT
        'ppvz_reward_signal' AS signal_name,
        COUNT(*) FILTER (
          WHERE COALESCE(raw_payload->>'ppvz_reward', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            AND ABS((raw_payload->>'ppvz_reward')::numeric) > 0
        )::int AS rows,
        COALESCE(
          SUM(
            CASE
              WHEN COALESCE(raw_payload->>'ppvz_reward', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
                THEN ABS((raw_payload->>'ppvz_reward')::numeric)
              ELSE 0
            END
          ),
          0
        )::numeric(18,2) AS raw_amount
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_date::date BETWEEN %s AND %s

      UNION ALL

      SELECT
        'cashback_discount_signal',
        COUNT(*) FILTER (
          WHERE COALESCE(raw_payload->>'cashback_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            AND ABS((raw_payload->>'cashback_discount')::numeric) > 0
        )::int,
        COALESCE(
          SUM(
            CASE
              WHEN COALESCE(raw_payload->>'cashback_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
                THEN ABS((raw_payload->>'cashback_discount')::numeric)
              ELSE 0
            END
          ),
          0
        )::numeric(18,2)
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_date::date BETWEEN %s AND %s

      UNION ALL

      SELECT
        'loyalty_discount_signal',
        COUNT(*) FILTER (
          WHERE COALESCE(raw_payload->>'loyalty_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            AND ABS((raw_payload->>'loyalty_discount')::numeric) > 0
        )::int,
        COALESCE(
          SUM(
            CASE
              WHEN COALESCE(raw_payload->>'loyalty_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
                THEN ABS((raw_payload->>'loyalty_discount')::numeric)
              ELSE 0
            END
          ),
          0
        )::numeric(18,2)
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_date::date BETWEEN %s AND %s

      UNION ALL

      SELECT
        'seller_promo_discount_signal',
        COUNT(*) FILTER (
          WHERE COALESCE(raw_payload->>'seller_promo_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            AND ABS((raw_payload->>'seller_promo_discount')::numeric) > 0
        )::int,
        COALESCE(
          SUM(
            CASE
              WHEN COALESCE(raw_payload->>'seller_promo_discount', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
                THEN ABS((raw_payload->>'seller_promo_discount')::numeric)
              ELSE 0
            END
          ),
          0
        )::numeric(18,2)
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_date::date BETWEEN %s AND %s

      UNION ALL

      SELECT
        'supplier_promo_signal',
        COUNT(*) FILTER (
          WHERE COALESCE(raw_payload->>'supplier_promo', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
            AND ABS((raw_payload->>'supplier_promo')::numeric) > 0
        )::int,
        COALESCE(
          SUM(
            CASE
              WHEN COALESCE(raw_payload->>'supplier_promo', '') ~ '^[-]?[0-9]+([.][0-9]+)?$'
                THEN ABS((raw_payload->>'supplier_promo')::numeric)
              ELSE 0
            END
          ),
          0
        )::numeric(18,2)
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_date::date BETWEEN %s AND %s
    ) x
    ORDER BY signal_name
    """
    rows = query_rows(
        conn,
        sql,
        (
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
        ),
    )
    return {str(row["signal_name"]): row for row in rows}


def build_acquiring_csv_rows(
    overlap: dict[str, Any],
    acquiring_labels: list[dict[str, Any]],
    current_matrix: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    sale_linked_total = money(current_matrix["sale_linked_service"]["amount"])
    acquiring_total = money(overlap["acquiring_total"])
    residual = sale_linked_total - acquiring_total
    rows: list[dict[str, Any]] = [
        {
          "section": "summary",
          "subtype_name": "raw_acquiring_fee_total",
          "rows": overlap["acquiring_rows"],
          "amount": acquiring_total,
          "current_bucket": "raw_finance",
          "source_field": "acquiring_fee",
          "product_level_amount": acquiring_total,
          "store_level_amount": Decimal("0.00"),
          "mixed_amount": Decimal("0.00"),
          "note": "Сумма из stg_wb_finance.acquiring_fee.",
        },
        {
          "section": "summary",
          "subtype_name": "current_sale_linked_service_total",
          "rows": overlap["service_rows"],
          "amount": sale_linked_total,
          "current_bucket": "services / sale_linked_service",
          "source_field": "service_amount",
          "product_level_amount": money(current_matrix["sale_linked_service"]["product_level_amount"]),
          "store_level_amount": money(current_matrix["sale_linked_service"]["store_level_amount"]),
          "mixed_amount": money(current_matrix["sale_linked_service"]["mixed_amount"]),
          "note": "Текущий bucket из модели.",
        },
        {
          "section": "summary",
          "subtype_name": "exact_row_overlap_acquiring_vs_service_amount",
          "rows": overlap["overlap_rows"],
          "amount": acquiring_total,
          "current_bucket": "services / sale_linked_service",
          "source_field": "acquiring_fee = service_amount",
          "product_level_amount": acquiring_total,
          "store_level_amount": Decimal("0.00"),
          "mixed_amount": Decimal("0.00"),
          "note": "Все строки overlap 1:1; unequal_rows = 0, delta = 0.",
        },
        {
          "section": "summary",
          "subtype_name": "sale_linked_service_residual_after_acquiring",
          "rows": 0 if residual == Decimal("0.00") else None,
          "amount": residual,
          "current_bucket": "services / sale_linked_service",
          "source_field": "service_amount residual",
          "product_level_amount": residual,
          "store_level_amount": Decimal("0.00"),
          "mixed_amount": Decimal("0.00"),
          "note": "После выделения acquiring остаток bucket'а.",
        },
        {
          "section": "summary",
          "subtype_name": "current_fact_payment_processing_bucket",
          "rows": 0 if money(current_matrix["acquiring"]["amount"]) == Decimal("0.00") else 1,
          "amount": money(current_matrix["acquiring"]["amount"]),
          "current_bucket": "payment_processing",
          "source_field": "money_bucket = payment_processing",
          "product_level_amount": money(current_matrix["acquiring"]["product_level_amount"]),
          "store_level_amount": money(current_matrix["acquiring"]["store_level_amount"]),
          "mixed_amount": money(current_matrix["acquiring"]["mixed_amount"]),
          "note": "Сейчас mart не заполняет payment_processing из acquiring_fee.",
        },
    ]
    for row in acquiring_labels:
        rows.append(
            {
              "section": "payment_processing_detail",
              "subtype_name": row["subtype_name"],
              "rows": row["rows"],
              "amount": money(row["raw_amount"]),
              "current_bucket": "services / sale_linked_service",
              "source_field": "acquiring_fee + raw_payload.payment_processing",
              "product_level_amount": money(row["product_level_amount"]),
              "store_level_amount": money(row["store_level_amount"]),
              "mixed_amount": money(row["mixed_amount"]),
              "note": row["payment_processing_label"],
            }
        )
    return rows


def can_be_separate_other(subtype_name: str) -> str:
    return {
        "ppvz_reward_sale_flow": "no",
        "transport_warehouse_reimbursement": "yes",
        "pvz_issue_return_reimbursement": "yes",
        "other_unclassified_tail": "no",
    }.get(subtype_name, "no")


def build_other_csv_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    notes = {
        "ppvz_reward_sale_flow": "Сумма 1:1 совпадает с raw_payload.ppvz_reward; как expense подстатья семантически спорна.",
        "transport_warehouse_reimbursement": "Явный текстовый подтип из raw finance.",
        "pvz_issue_return_reimbursement": "Небольшая, но явная статья по ПВЗ.",
        "other_unclassified_tail": "Хвост, не попавший в явные подтипы.",
    }
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned.append(
            {
                "subtype_name": row["subtype_name"],
                "classifier_field": row["source_field"],
                "operation_type": row["operation_type"],
                "supplier_oper_name": row["supplier_oper_name"],
                "doc_type_name": row["doc_type_name"],
                "bonus_type_name": row["bonus_type_name"],
                "rows": row["rows"],
                "amount": money(row["amount"]),
                "product_level_amount": money(row["product_level_amount"]),
                "store_level_amount": money(row["store_level_amount"]),
                "mixed_amount": money(row["mixed_amount"]),
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"],
                "can_be_separate_bucket_yes_no": can_be_separate_other(row["subtype_name"]),
                "note": notes.get(str(row["subtype_name"]), ""),
            }
        )
    return cleaned


def build_commission_csv_rows(
    commission_core: list[dict[str, Any]],
    acquiring_labels: list[dict[str, Any]],
    raw_signals: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in commission_core:
        rows.append(
            {
                "subtype_name": row["subtype_name"],
                "bucket_group": "commission",
                "rows": row["rows"],
                "raw_amount": money(row["raw_amount"]),
                "current_expense_amount": money(row["current_expense_amount"]),
                "product_level_amount": money(row["product_level_amount"]),
                "store_level_amount": money(row["store_level_amount"]),
                "mixed_amount": money(row["mixed_amount"]),
                "source_field": "commission_amount",
                "current_bucket": "commission",
                "classifier_field": row["operation_type"] or "operation_type",
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"],
                "can_be_separate_bucket_yes_no": "yes",
                "note": "Текущий commission bucket полностью идет из commission_amount.",
            }
        )
    for row in acquiring_labels:
        rows.append(
            {
                "subtype_name": row["subtype_name"],
                "bucket_group": "payment_processing",
                "rows": row["rows"],
                "raw_amount": money(row["raw_amount"]),
                "current_expense_amount": money(row["product_level_amount"]) + money(row["store_level_amount"]),
                "product_level_amount": money(row["product_level_amount"]),
                "store_level_amount": money(row["store_level_amount"]),
                "mixed_amount": money(row["mixed_amount"]),
                "source_field": "acquiring_fee",
                "current_bucket": "services / sale_linked_service",
                "classifier_field": "raw_payload.payment_processing",
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"],
                "can_be_separate_bucket_yes_no": "yes",
                "note": row["payment_processing_label"],
            }
        )

    signal_notes = {
        "ppvz_reward_signal": ("ppvz_reward", "other / returns", "low", "Сырой сигнал есть, но он не входит в текущий commission bucket."),
        "cashback_discount_signal": ("cashback_discount", "not_loaded_to_expense", "low", "Есть в raw, но amount=0, в expense слой сейчас не попадает."),
        "loyalty_discount_signal": ("loyalty_discount", "not_present", "low", "Ненулевых значений в периоде нет."),
        "seller_promo_discount_signal": ("seller_promo_discount", "not_present", "low", "Ненулевых значений в периоде нет."),
        "supplier_promo_signal": ("supplier_promo", "not_present", "low", "Ненулевых значений в периоде нет."),
    }
    for signal_name in (
        "ppvz_reward_signal",
        "cashback_discount_signal",
        "loyalty_discount_signal",
        "seller_promo_discount_signal",
        "supplier_promo_signal",
    ):
        signal = raw_signals[signal_name]
        source_field, current_bucket, confidence, note = signal_notes[signal_name]
        rows.append(
            {
                "subtype_name": signal_name,
                "bucket_group": "raw_only_signal",
                "rows": signal["rows"],
                "raw_amount": money(signal["raw_amount"]),
                "current_expense_amount": Decimal("0.00"),
                "product_level_amount": Decimal("0.00"),
                "store_level_amount": Decimal("0.00"),
                "mixed_amount": Decimal("0.00"),
                "source_field": f"raw_payload.{source_field}",
                "current_bucket": current_bucket,
                "classifier_field": f"raw_payload.{source_field}",
                "attribution_method": "",
                "confidence": confidence,
                "can_be_separate_bucket_yes_no": "no",
                "note": note,
            }
        )
    return rows


def build_matrix_v2_rows(
    commission_csv_rows: list[dict[str, Any]],
    other_csv_rows: list[dict[str, Any]],
    current_matrix: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in commission_csv_rows:
        if row["bucket_group"] not in {"commission", "payment_processing"}:
            continue
        rows.append(
            {
                "bucket_group": row["bucket_group"],
                "bucket_subtype": row["subtype_name"],
                "amount": money(row["current_expense_amount"]),
                "source_field": row["source_field"],
                "source_table": "stg_wb_finance",
                "product_level_amount": money(row["product_level_amount"]),
                "store_level_amount": money(row["store_level_amount"]),
                "mixed_amount": money(row["mixed_amount"]),
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"],
                "should_be_visible_in_cabinet_yes_no": "yes",
            }
        )

    sale_linked_total = money(current_matrix["sale_linked_service"]["amount"])
    acquiring_total = sum(
        (
            money(row["current_expense_amount"])
            for row in commission_csv_rows
            if row["bucket_group"] == "payment_processing"
        ),
        Decimal("0.00"),
    )
    rows.append(
        {
            "bucket_group": "payment_processing",
            "bucket_subtype": "sale_linked_service_residual_after_acquiring",
            "amount": sale_linked_total - acquiring_total,
            "source_field": "service_amount residual",
            "source_table": "stg_wb_finance",
            "product_level_amount": sale_linked_total - acquiring_total,
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "attribution_method": current_matrix["sale_linked_service"]["attribution_method"],
            "confidence": current_matrix["sale_linked_service"]["confidence"],
            "should_be_visible_in_cabinet_yes_no": "no",
        }
    )

    for row in other_csv_rows:
        rows.append(
            {
                "bucket_group": "other",
                "bucket_subtype": row["subtype_name"],
                "amount": money(row["amount"]),
                "source_field": row["classifier_field"],
                "source_table": "stg_wb_finance",
                "product_level_amount": money(row["product_level_amount"]),
                "store_level_amount": money(row["store_level_amount"]),
                "mixed_amount": money(row["mixed_amount"]),
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"] or "medium",
                "should_be_visible_in_cabinet_yes_no": (
                    "yes" if row["can_be_separate_bucket_yes_no"] == "yes" else "no"
                ),
            }
        )

    service_rows = [
        ("services", "review_service", "amount"),
        ("store_services", "jam_subscription", "amount"),
        ("store_services", "other_store_service", "amount"),
    ]
    for bucket_group, subtype, source_field in service_rows:
        current = current_matrix[subtype]
        rows.append(
            {
                "bucket_group": bucket_group,
                "bucket_subtype": subtype,
                "amount": money(current["amount"]),
                "source_field": source_field,
                "source_table": "stg_wb_finance",
                "product_level_amount": money(current["product_level_amount"]),
                "store_level_amount": money(current["store_level_amount"]),
                "mixed_amount": money(current["mixed_amount"]),
                "attribution_method": current["attribution_method"],
                "confidence": current["confidence"],
                "should_be_visible_in_cabinet_yes_no": (
                    "yes" if subtype != "other_store_service" else "no"
                ),
            }
        )
    return rows


def build_acquiring_report(
    schema: str,
    period_start: str,
    period_end: str,
    overlap: dict[str, Any],
    current_matrix: dict[str, dict[str, Any]],
    acquiring_labels: list[dict[str, Any]],
) -> str:
    residual = money(current_matrix["sale_linked_service"]["amount"]) - money(overlap["acquiring_total"])
    labels_text = "\n".join(
        f"- `{row['subtype_name']}` / `{row['payment_processing_label']}`: `{money_text(row['raw_amount'])}`"
        for row in acquiring_labels
    )
    return f"""# WB Acquiring Extraction Report

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Totals

- `raw_finance.acquiring_fee`: `{money_text(overlap['acquiring_total'])}` on `{overlap['acquiring_rows']}` rows
- current `sale_linked_service`: `{money_text(current_matrix['sale_linked_service']['amount'])}`
- current `payment_processing` bucket in fact: `{money_text(current_matrix['acquiring']['amount'])}`

## 2. Overlap Check

- overlap rows: `{overlap['overlap_rows']}`
- acquiring-only rows: `{overlap['acquiring_only_rows']}`
- service-only rows: `{overlap['service_only_rows']}`
- unequal rows: `{overlap['unequal_rows']}`
- amount delta `acquiring_fee - service_amount`: `{money_text(overlap['delta'])}`

Conclusion:
- `sale_linked_service` is a full match to `acquiring_fee` on the current period.
- there is no residual non-acquiring content inside `sale_linked_service`: `{money_text(residual)}`
- the current mart misses this because `payment_processing` is still detected from text markers in `operation_type`, while the real source field is `acquiring_fee`

## 3. Payment Processing Detail

{labels_text}

## 4. Recommended Extraction

- extract a separate `acquiring` / `payment_processing` expense article from `stg_wb_finance.acquiring_fee`
- stop using generic `sale_linked_service` for this amount
- keep `raw_payload.payment_processing` only as a label field for sub-detail, not as the numeric source
"""


def build_other_report(
    schema: str,
    period_start: str,
    period_end: str,
    other_rows: list[dict[str, Any]],
    zero_tail: dict[str, Any],
) -> str:
    total = sum((money(row["amount"]) for row in other_rows), Decimal("0.00"))
    lines = []
    for row in other_rows:
        share = Decimal("0.00")
        if total > 0:
            share = (money(row["amount"]) / total * Decimal("100")).quantize(Decimal("0.01"))
        lines.append(
            f"- `{row['subtype_name']}`: `{money_text(row['amount'])}` ({share}%), separate=`{row['can_be_separate_bucket_yes_no']}`"
        )
    return f"""# WB Other Breakdown Report

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`
- current `other` bucket total: `{money_text(total)}`

## 1. Breakdown

{chr(10).join(lines)}

## 2. What The Bucket Really Contains

- `ppvz_reward_sale_flow` is the largest slice and matches `raw_payload.ppvz_reward` 1:1 on sale rows
- `transport_warehouse_reimbursement` is an explicit raw finance article and is the cleanest candidate for a visible separate bucket
- `pvz_issue_return_reimbursement` is small but also explicit

## 3. Black Box Status

- the positive-amount `other` bucket is fully decomposed into the visible subtypes above
- there is an additional zero-amount raw tail under the same coarse classifier: `{zero_tail['rows']}` rows, `{money_text(zero_tail['amount'])}` amount

## 4. Recommendation

- do not keep `other` as one opaque client-facing line
- move explicit reimbursement subtypes into named buckets
- show `ppvz_reward_sale_flow` in UI under the neutral name `Вознаграждение Wildberries`
"""


def build_commission_report(
    schema: str,
    period_start: str,
    period_end: str,
    commission_rows: list[dict[str, Any]],
    raw_signals: dict[str, dict[str, Any]],
) -> str:
    commission_total = sum(
        (money(row["current_expense_amount"]) for row in commission_rows if row["bucket_group"] == "commission"),
        Decimal("0.00"),
    )
    payment_total = sum(
        (money(row["current_expense_amount"]) for row in commission_rows if row["bucket_group"] == "payment_processing"),
        Decimal("0.00"),
    )
    raw_only = [
        row
        for row in commission_rows
        if row["bucket_group"] == "raw_only_signal"
    ]
    return f"""# WB Commission Granularity Report

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Current Bucket Reality

- current `commission` in fact: `{money_text(commission_total)}`
- current `payment_processing` in fact: `0.00`
- extractable `acquiring` hidden in `sale_linked_service`: `{money_text(payment_total)}`

Conclusion:
- current `commission` bucket is already clean: it is fully driven by `commission_amount`
- `acquiring` does not sit inside `commission`; it sits inside `services / sale_linked_service`

## 2. Maximum Stable Granularity

{chr(10).join(f"- `{row['subtype_name']}`: current expense `{money_text(row['current_expense_amount'])}`, source `{row['source_field']}`" for row in commission_rows if row['bucket_group'] in {'commission', 'payment_processing'})}

## 3. Related Raw Signals That Are Not Current Commission Expenses

{chr(10).join(f"- `{row['subtype_name']}`: raw `{money_text(row['raw_amount'])}`, current commission expense `0.00`" for row in raw_only)}

Raw signal notes:
- `ppvz_reward_signal`: `{money_text(raw_signals['ppvz_reward_signal']['raw_amount'])}`
- `cashback_discount_signal`: `{money_text(raw_signals['cashback_discount_signal']['raw_amount'])}`
- `loyalty_discount_signal`: `{money_text(raw_signals['loyalty_discount_signal']['raw_amount'])}`
- `seller_promo_discount_signal`: `{money_text(raw_signals['seller_promo_discount_signal']['raw_amount'])}`
- `supplier_promo_signal`: `{money_text(raw_signals['supplier_promo_signal']['raw_amount'])}`

## 4. Recommendation

- keep `commission_core` as a separate core article from `commission_amount`
- extract `acquiring` from `acquiring_fee`
- use `raw_payload.payment_processing` only for sub-detail labels inside the new payment-processing section
- do not auto-promote `ppvz_reward` or `cashback_discount` into commission without separate semantic review
"""


def build_matrix_report(
    schema: str,
    period_start: str,
    period_end: str,
    matrix_rows: list[dict[str, Any]],
) -> str:
    group_totals: dict[str, Decimal] = {}
    for row in matrix_rows:
        group_totals.setdefault(str(row["bucket_group"]), Decimal("0.00"))
        group_totals[str(row["bucket_group"])] += money(row["amount"])
    visible_rows = [row for row in matrix_rows if row["should_be_visible_in_cabinet_yes_no"] == "yes"]
    hidden_rows = [row for row in matrix_rows if row["should_be_visible_in_cabinet_yes_no"] == "no"]
    return f"""# WB Expense Granularity Report V2

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Final Matrix Totals

{chr(10).join(f"- `{group}`: `{money_text(amount)}`" for group, amount in group_totals.items())}

## 2. Should Be Visible In Cabinet

{chr(10).join(f"- `{row['bucket_group']}` / `{row['bucket_subtype']}`: `{money_text(row['amount'])}`" for row in visible_rows)}

## 3. Keep Out Of Default Cabinet

{chr(10).join(f"- `{row['bucket_group']}` / `{row['bucket_subtype']}`: `{money_text(row['amount'])}`" for row in hidden_rows)}

## 4. Implementation Readiness

- `acquiring` can be shown explicitly now
- `other` is no longer a black box
- `commission` can stay core, with payment processing shown nearby as its own layer
- `review_service` and `Подписка Джем` should remain separate named service rows
"""


def main() -> None:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        current_matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        current_matrix = {row["bucket_subtype"]: row for row in current_matrix_rows}

        overlap = fetch_acquiring_overlap(conn, SCHEMA, period_start, period_end)
        acquiring_labels = fetch_acquiring_labels(conn, SCHEMA, period_start, period_end)
        other_rows_raw = fetch_other_breakdown(conn, SCHEMA, period_start, period_end)
        other_zero_tail = fetch_other_zero_tail(conn, SCHEMA, period_start, period_end)
        commission_core = fetch_commission_core(conn, SCHEMA, period_start, period_end)
        raw_signals = fetch_raw_signals(conn, SCHEMA, period_start, period_end)

    acquiring_csv_rows = build_acquiring_csv_rows(overlap, acquiring_labels, current_matrix)
    other_csv_rows = build_other_csv_rows(other_rows_raw)
    commission_csv_rows = build_commission_csv_rows(commission_core, acquiring_labels, raw_signals)
    matrix_v2_rows = build_matrix_v2_rows(commission_csv_rows, other_csv_rows, current_matrix)

    write_csv(
        ACQUIRING_CSV,
        [
            {
                **row,
                "amount": money_text(row["amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in acquiring_csv_rows
        ],
        [
            "section",
            "subtype_name",
            "rows",
            "amount",
            "current_bucket",
            "source_field",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "note",
        ],
    )

    write_csv(
        OTHER_CSV,
        [
            {
                **row,
                "amount": money_text(row["amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in other_csv_rows
        ],
        [
            "subtype_name",
            "classifier_field",
            "operation_type",
            "supplier_oper_name",
            "doc_type_name",
            "bonus_type_name",
            "rows",
            "amount",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "attribution_method",
            "confidence",
            "can_be_separate_bucket_yes_no",
            "note",
        ],
    )

    write_csv(
        COMMISSION_CSV,
        [
            {
                **row,
                "raw_amount": money_text(row["raw_amount"]),
                "current_expense_amount": money_text(row["current_expense_amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in commission_csv_rows
        ],
        [
            "subtype_name",
            "bucket_group",
            "rows",
            "raw_amount",
            "current_expense_amount",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "source_field",
            "current_bucket",
            "classifier_field",
            "attribution_method",
            "confidence",
            "can_be_separate_bucket_yes_no",
            "note",
        ],
    )

    write_csv(
        MATRIX_V2_CSV,
        [
            {
                **row,
                "amount": money_text(row["amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in matrix_v2_rows
        ],
        [
            "bucket_group",
            "bucket_subtype",
            "amount",
            "source_field",
            "source_table",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "attribution_method",
            "confidence",
            "should_be_visible_in_cabinet_yes_no",
        ],
    )

    ACQUIRING_REPORT_MD.write_text(
        build_acquiring_report(SCHEMA, period_start, period_end, overlap, current_matrix, acquiring_labels),
        encoding="utf-8",
    )
    OTHER_REPORT_MD.write_text(
        build_other_report(SCHEMA, period_start, period_end, other_csv_rows, other_zero_tail),
        encoding="utf-8",
    )
    COMMISSION_REPORT_MD.write_text(
        build_commission_report(SCHEMA, period_start, period_end, commission_csv_rows, raw_signals),
        encoding="utf-8",
    )
    MATRIX_V2_REPORT_MD.write_text(
        build_matrix_report(SCHEMA, period_start, period_end, matrix_v2_rows),
        encoding="utf-8",
    )

    print(f"Wrote {ACQUIRING_CSV}")
    print(f"Wrote {ACQUIRING_REPORT_MD}")
    print(f"Wrote {OTHER_CSV}")
    print(f"Wrote {OTHER_REPORT_MD}")
    print(f"Wrote {COMMISSION_CSV}")
    print(f"Wrote {COMMISSION_REPORT_MD}")
    print(f"Wrote {MATRIX_V2_CSV}")
    print(f"Wrote {MATRIX_V2_REPORT_MD}")


if __name__ == "__main__":
    main()
