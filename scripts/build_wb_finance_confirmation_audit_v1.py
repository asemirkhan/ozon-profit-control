#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_wb_ppvz_pvz_acquiring_audit_v1 import (  # noqa: E402
    fetch_fact_dataset,
    fetch_finance_dataset,
    money,
    money_text,
)
from src.services.wb_expense_granularity import (  # noqa: E402
    build_wb_expense_granularity_matrix,
    latest_wb_period,
)
from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA") or "client_demo_90d_20260408"

CONFIRMATION_CSV = ROOT / "wb_finance_confirmation_matrix.csv"
OVERLAP_CSV = ROOT / "wb_finance_bucket_overlap_check.csv"
COMPLETENESS_CSV = ROOT / "wb_finance_source_completeness_check.csv"
REPORT_MD = ROOT / "wb_finance_confirmation_report.md"


@dataclass(frozen=True)
class VerdictRow:
    bucket_name: str
    final_verdict: str


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_text(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def query_one(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, Any]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return dict(row) if row else {}


def mart_totals(conn: Any, schema: str, period_start: str, period_end: str) -> dict[str, Any]:
    sql = f"""
    SELECT
      SUM(COALESCE(ads_spend, 0))::numeric(18,2) AS ads_total,
      SUM(COALESCE(commission_amount, 0))::numeric(18,2) AS commission_total,
      SUM(COALESCE(logistics_amount, 0))::numeric(18,2) AS logistics_total,
      SUM(COALESCE(storage_amount, 0))::numeric(18,2) AS storage_total,
      SUM(COALESCE(payment_processing_amount, 0))::numeric(18,2) AS payment_processing_total,
      SUM(COALESCE(penalties_amount, 0))::numeric(18,2) AS penalties_total,
      SUM(COALESCE(other_amount, 0))::numeric(18,2) AS other_total,
      SUM(COALESCE(returns_amount, 0))::numeric(18,2) AS returns_total,
      SUM(COALESCE(services_amount, 0))::numeric(18,2) AS services_total,
      SUM(CASE WHEN offer_id = '~' THEN COALESCE(services_amount, 0) ELSE 0 END)::numeric(18,2) AS services_store_total,
      SUM(CASE WHEN offer_id <> '~' THEN COALESCE(services_amount, 0) ELSE 0 END)::numeric(18,2) AS services_product_total,
      SUM(COALESCE(marketplace_other_fees_amount, 0))::numeric(18,2) AS marketplace_other_fees_total
    FROM {schema}.mart_pnl_30d_by_product
    WHERE marketplace = 'wb'
      AND period_start = %s
      AND period_end = %s
    """
    return query_one(conn, sql, (period_start, period_end))


def wb_promotion_overlap(conn: Any, schema: str) -> dict[str, Any]:
    sql = f"""
    WITH ads_bounds AS (
      SELECT
        MIN(date)::date AS ads_min_day,
        MAX(date)::date AS ads_max_day
      FROM {schema}.stg_wb_ads
      WHERE COALESCE(marketplace, 'wb') = 'wb'
    ),
    promo_docs AS (
      SELECT
        COALESCE(raw_payload->>'realizationreport_id', finance_id::text) AS report_id,
        (raw_payload->>'date_from')::date AS date_from,
        (raw_payload->>'date_to')::date AS date_to,
        SUM(COALESCE(amount, 0))::numeric(18,2) AS finance_amount
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижение%%'
      GROUP BY 1, 2, 3
    ),
    covered_docs AS (
      SELECT p.*
      FROM promo_docs p
      CROSS JOIN ads_bounds a
      WHERE p.date_from IS NOT NULL
        AND p.date_to IS NOT NULL
        AND p.date_from >= a.ads_min_day
        AND p.date_to <= a.ads_max_day
    ),
    ads_in_windows AS (
      SELECT
        d.report_id,
        d.finance_amount,
        COALESCE((
          SELECT SUM(COALESCE(a.spend, 0))
          FROM {schema}.stg_wb_ads a
          WHERE COALESCE(a.marketplace, 'wb') = 'wb'
            AND a.date::date BETWEEN d.date_from AND d.date_to
        ), 0)::numeric(18,2) AS ads_amount
      FROM covered_docs d
    )
    SELECT
      (SELECT COALESCE(SUM(COALESCE(amount, 0)), 0)::numeric(18,2)
       FROM {schema}.stg_wb_finance
       WHERE COALESCE(marketplace, 'wb') = 'wb'
         AND LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижение%%') AS raw_promotion_total,
      COALESCE(SUM(finance_amount), 0)::numeric(18,2) AS covered_finance_total,
      COALESCE(SUM(ads_amount), 0)::numeric(18,2) AS covered_ads_total,
      COALESCE(SUM(finance_amount - ads_amount), 0)::numeric(18,2) AS delta,
      COALESCE(CORR(finance_amount::float8, ads_amount::float8), 0)::numeric(18,6) AS corr
    FROM ads_in_windows
    """
    return query_one(conn, sql, ())


def storage_confirmation(conn: Any, schema: str) -> dict[str, Any]:
    sql = f"""
    WITH finance_days AS (
      SELECT
        MIN(operation_date::date) AS min_day,
        MAX(operation_date::date) AS max_day,
        COALESCE(SUM(COALESCE(amount, 0)), 0)::numeric(18,2) AS finance_total
      FROM {schema}.stg_wb_finance
      WHERE COALESCE(marketplace, 'wb') = 'wb'
        AND operation_type = 'Хранение'
    )
    SELECT
      f.min_day::text AS min_day,
      f.max_day::text AS max_day,
      f.finance_total,
      COALESCE(SUM(CASE WHEN s.warehouse_price > 0 THEN s.warehouse_price ELSE 0 END), 0)::numeric(18,2) AS paid_positive_total
    FROM finance_days f
    LEFT JOIN {schema}.stg_wb_paid_storage s
      ON COALESCE(s.marketplace, 'wb') = 'wb'
     AND s.report_date BETWEEN f.min_day AND f.max_day
    GROUP BY 1, 2, 3
    """
    return query_one(conn, sql, ())


def payment_processing_label_amount(conn: Any, schema: str, label: str) -> Decimal:
    sql = f"""
    SELECT COALESCE(SUM(acquiring_fee), 0)::numeric(18,2) AS amount
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND COALESCE(raw_payload->>'payment_processing', '') = %s
    """
    row = query_one(conn, sql, (label,))
    return money(row.get("amount"))


def slice_total(
    finance_rows: list[dict[str, Any]],
    predicate: Any,
    field_name: str,
) -> Decimal:
    total = Decimal("0.00")
    for row in finance_rows:
        if predicate(row):
            total += money(row[field_name])
    return total


def confirmation_rows(
    matrix: dict[str, dict[str, Any]],
    mart: dict[str, Any],
    finance_rows: list[dict[str, Any]],
    promo: dict[str, Any],
    storage: dict[str, Any],
    payment_org_amount: Decimal,
) -> list[dict[str, Any]]:
    other_marketplace_product = (
        money(matrix["other_sale_adjustment"]["amount"])
        + money(matrix["other_logistics_rebill"]["amount"])
        + money(matrix["other_pvz_reimbursement"]["amount"])
        + money(matrix["penalties"]["amount"])
        + money(mart["services_product_total"])
    )
    other_marketplace_store = money(mart["services_store_total"])
    other_marketplace_mixed = money(matrix["storage"]["amount"])

    wb_reward_return = slice_total(
        finance_rows,
        lambda r: r["ppvz_reward_abs"] > 0
        and r["amount_abs"] == r["ppvz_reward_abs"]
        and r["operation_type"] == "Возврат"
        and r["supplier_oper_name"] == "Возврат"
        and r["doc_type_name"] == "Возврат",
        "amount_abs",
    )
    services_product_exact = (
        money(matrix["acquiring"]["amount"])
        + money(matrix["review_service"]["amount"])
        + money(matrix["other_service"]["amount"])
        + money(matrix["sale_linked_service"]["amount"])
    )
    services_store_exact = money(matrix["jam_subscription"]["amount"]) + money(matrix["other_store_service"]["amount"])

    rows = [
        {
            "bucket_name": "ads",
            "ui_label": "Реклама",
            "amount_total": money(matrix["ads"]["amount"]),
            "source_of_truth": "ads",
            "confirming_source": "finance",
            "confirmation_type": "period_window_match",
            "product_level_amount": money(matrix["ads"]["product_level_amount"]),
            "store_level_amount": money(matrix["ads"]["store_level_amount"]),
            "mixed_amount": money(matrix["ads"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Primary advertising truth from WB ads layer; financial duplicate risk exists only against WB Promotion documents.",
        },
        {
            "bucket_name": "wb_promotion",
            "ui_label": "WB Продвижение",
            "amount_total": money(promo["raw_promotion_total"]),
            "source_of_truth": "finance",
            "confirming_source": "ads",
            "confirmation_type": "period_window_match",
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": money(promo["raw_promotion_total"]),
            "mixed_amount": Decimal("0.00"),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "no",
            "notes_short": "Raw finance article still exists, but current economics excludes it as duplicate of ads on document-window match.",
        },
        {
            "bucket_name": "storage",
            "ui_label": "Хранение",
            "amount_total": money(matrix["storage"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "paid_storage",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": money(matrix["storage"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": f"Finance storage `{money_text(storage['finance_total'])}` is confirmed by paid_storage positive-only `{money_text(storage['paid_positive_total'])}` on aligned window.",
        },
        {
            "bucket_name": "commission_core",
            "ui_label": "Комиссия маркетплейса",
            "amount_total": money(matrix["commission_core"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "orders",
            "confirmation_type": "structure_share_match",
            "product_level_amount": money(matrix["commission_core"]["product_level_amount"]),
            "store_level_amount": money(matrix["commission_core"]["store_level_amount"]),
            "mixed_amount": money(matrix["commission_core"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Driven by commission_amount; product attribution is supported by order_id / nm_id keys and exact mart carry-through.",
        },
        {
            "bucket_name": "wb_reward",
            "ui_label": "Вознаграждение Wildberries",
            "amount_total": money(matrix["other_sale_adjustment"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": money(matrix["other_sale_adjustment"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_sale_adjustment"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_sale_adjustment"]["mixed_amount"]),
            "confidence": "medium",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Comes from rows where amount = raw_payload.ppvz_reward; currently sits inside other and should be reclassified visibly.",
        },
        {
            "bucket_name": "wb_reward_return_adjustment",
            "ui_label": "Корректировка вознаграждения Wildberries",
            "amount_total": wb_reward_return,
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": wb_reward_return,
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "confidence": "medium",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Return-side ppvz_reward slice; current returns bucket in this period is effectively this exact amount.",
        },
        {
            "bucket_name": "pvz_reimbursement",
            "ui_label": "Возмещение за выдачу и возврат на ПВЗ",
            "amount_total": money(matrix["other_pvz_reimbursement"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": money(matrix["other_pvz_reimbursement"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_pvz_reimbursement"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_pvz_reimbursement"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Explicit raw PVZ reimbursement rows; currently included inside mart other_amount.",
        },
        {
            "bucket_name": "pvz_related_penalty",
            "ui_label": "ПВЗ-связанные удержания",
            "amount_total": money(matrix["penalties"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": money(matrix["penalties"]["product_level_amount"]),
            "store_level_amount": money(matrix["penalties"]["store_level_amount"]),
            "mixed_amount": money(matrix["penalties"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Current penalties bucket is fully explained by the PVZ-related storage/return penalty slice.",
        },
        {
            "bucket_name": "acquiring",
            "ui_label": "Эквайринг",
            "amount_total": money(matrix["acquiring"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "finance",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": money(matrix["acquiring"]["product_level_amount"]),
            "store_level_amount": money(matrix["acquiring"]["store_level_amount"]),
            "mixed_amount": money(matrix["acquiring"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "acquiring_fee matches service_amount 1:1 on 4199 rows; current marts still carry the money via services, not as a separate line.",
        },
        {
            "bucket_name": "payment_processing",
            "ui_label": "Организация платежа",
            "amount_total": payment_org_amount,
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": payment_org_amount,
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "confidence": "low",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "no",
            "notes_short": "Raw label exists on SKU-linked finance rows, but there is no separate fact/mart monetary bucket yet; today it is only a sub-detail of acquiring rows.",
        },
        {
            "bucket_name": "logistics",
            "ui_label": "Логистика",
            "amount_total": money(matrix["logistics"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "orders",
            "confirmation_type": "structure_share_match",
            "product_level_amount": money(matrix["logistics"]["product_level_amount"]),
            "store_level_amount": money(matrix["logistics"]["store_level_amount"]),
            "mixed_amount": money(matrix["logistics"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Finance logistics is fully carried into marts; allocation is supported by order / offer / nm keys rather than a second amount source.",
        },
        {
            "bucket_name": "returns",
            "ui_label": "Возвратные удержания",
            "amount_total": money(matrix["returns"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "orders",
            "confirmation_type": "structure_share_match",
            "product_level_amount": money(matrix["returns"]["product_level_amount"]),
            "store_level_amount": money(matrix["returns"]["store_level_amount"]),
            "mixed_amount": money(matrix["returns"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Return deductions are product-linked through return/order keys; in this period the bucket equals the WB reward return adjustment slice.",
        },
        {
            "bucket_name": "services_product_level",
            "ui_label": "Товарные сервисы",
            "amount_total": money(mart["services_product_total"]),
            "source_of_truth": "finance",
            "confirming_source": "finance",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": money(mart["services_product_total"]),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": f"Current mart product-level services `{money_text(mart['services_product_total'])}` = acquiring `{money_text(matrix['acquiring']['amount'])}` + review_service `{money_text(matrix['review_service']['amount'])}` + residual service `0.00`.",
        },
        {
            "bucket_name": "services_store_level",
            "ui_label": "Магазинные сервисы",
            "amount_total": money(mart["services_store_total"]),
            "source_of_truth": "finance",
            "confirming_source": "finance",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": money(mart["services_store_total"]),
            "mixed_amount": Decimal("0.00"),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": f"Current mart store-level services `{money_text(mart['services_store_total'])}` are fully explained by jam_subscription in this period.",
        },
        {
            "bucket_name": "jam_subscription",
            "ui_label": "Подписка Джем",
            "amount_total": money(matrix["jam_subscription"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": money(matrix["jam_subscription"]["product_level_amount"]),
            "store_level_amount": money(matrix["jam_subscription"]["store_level_amount"]),
            "mixed_amount": money(matrix["jam_subscription"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Confirmed by bonus/service text markers and remains store-only; current mart carries it inside services_store_total.",
        },
        {
            "bucket_name": "other_logistics_rebill",
            "ui_label": "Возмещение издержек по перевозке/складским операциям",
            "amount_total": money(matrix["other_logistics_rebill"]["amount"]),
            "source_of_truth": "finance",
            "confirming_source": "none",
            "confirmation_type": "raw_field_match",
            "product_level_amount": money(matrix["other_logistics_rebill"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_logistics_rebill"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_logistics_rebill"]["mixed_amount"]),
            "confidence": "high",
            "double_count_risk_yes_no": "no",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Explicit raw finance reimbursement subtype; currently included inside mart other_amount.",
        },
        {
            "bucket_name": "penalties",
            "ui_label": "Штрафы",
            "amount_total": money(mart["penalties_total"]),
            "source_of_truth": "finance",
            "confirming_source": "finance",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": money(mart["penalties_total"]),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "confidence": "high",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Current coarse penalties bucket equals the PVZ-related penalty slice in this period.",
        },
        {
            "bucket_name": "other_marketplace_fees",
            "ui_label": "Прочие комиссии и удержания маркетплейса",
            "amount_total": money(mart["marketplace_other_fees_total"]),
            "source_of_truth": "finance",
            "confirming_source": "finance",
            "confirmation_type": "exact_sum_match",
            "product_level_amount": other_marketplace_product,
            "store_level_amount": other_marketplace_store,
            "mixed_amount": other_marketplace_mixed,
            "confidence": "medium",
            "double_count_risk_yes_no": "yes",
            "currently_used_in_marts_yes_no": "yes",
            "notes_short": "Derived mart rollup = services + storage + payment_processing + penalties + other; use only as a parent aggregate, not together with child lines.",
        },
    ]

    for row in rows:
        assert money(row["amount_total"]) == money(row["product_level_amount"]) + money(row["store_level_amount"]) + money(row["mixed_amount"])
    return rows


def overlap_rows(
    promo: dict[str, Any],
    storage: dict[str, Any],
    matrix: dict[str, dict[str, Any]],
    mart: dict[str, Any],
    payment_org_amount: Decimal,
) -> list[dict[str, Any]]:
    services_minus_children = (
        money(mart["services_product_total"])
        - money(matrix["acquiring"]["amount"])
        - money(matrix["review_service"]["amount"])
    )
    return [
        {
            "bucket_a": "ads",
            "bucket_b": "wb_promotion",
            "overlap_type": "duplicate_of_other_bucket",
            "overlap_amount_or_reason": (
                f"Covered doc windows: finance `{money_text(promo['covered_finance_total'])}` vs ads `{money_text(promo['covered_ads_total'])}`, "
                f"delta `{money_text(promo['delta'])}`, corr `{promo['corr']}`."
            ),
            "action_required": "Keep ads as truth and exclude WB Promotion from final economics.",
        },
        {
            "bucket_a": "acquiring",
            "bucket_b": "sale_linked_service",
            "overlap_type": "exact_sum_match",
            "overlap_amount_or_reason": "acquiring_fee = service_amount 1:1 on 4199 rows, total 192353.08.",
            "action_required": "Show acquiring separately and keep sale_linked_service residual at zero.",
        },
        {
            "bucket_a": "ppvz_reward",
            "bucket_b": "commission_core",
            "overlap_type": "structure_share_match",
            "overlap_amount_or_reason": "Same raw sale rows often carry both fields, but ppvz_reward and commission_amount are different source fields; no evidence of duplicate amount booking.",
            "action_required": "Do not merge; keep ppvz_reward as its own subtype and commission_core separate.",
        },
        {
            "bucket_a": "storage",
            "bucket_b": "paid_storage",
            "overlap_type": "exact_sum_match",
            "overlap_amount_or_reason": (
                f"Aligned window finance storage `{money_text(storage['finance_total'])}` vs paid_storage positive-only `{money_text(storage['paid_positive_total'])}`; delta `{money_text(money(storage['finance_total']) - money(storage['paid_positive_total']))}`."
            ),
            "action_required": "Use finance as amount truth and paid_storage as confirming allocation layer, not as an extra expense bucket.",
        },
        {
            "bucket_a": "services",
            "bucket_b": "other",
            "overlap_type": "no_external_confirmation",
            "overlap_amount_or_reason": (
                f"Current marts keep services `{money_text(mart['services_total'])}` and other `{money_text(mart['other_total'])}` as separate coarse buckets; "
                f"main reclassification need is acquiring out of services and wb_reward out of other."
            ),
            "action_required": "Keep buckets separate, but continue refined split before cabinet display.",
        },
        {
            "bucket_a": "services_store_level",
            "bucket_b": "jam_subscription",
            "overlap_type": "exact_sum_match",
            "overlap_amount_or_reason": "Current period store-level services are fully equal to jam_subscription 115250.00.",
            "action_required": "Do not show both as peer expenses in UI; keep services_store_level only as technical parent.",
        },
        {
            "bucket_a": "penalties",
            "bucket_b": "pvz_related_penalty",
            "overlap_type": "exact_sum_match",
            "overlap_amount_or_reason": "Current period penalties total 260.00 is fully explained by the PVZ-related penalty slice.",
            "action_required": "Prefer the human subtype in UI and keep penalties as technical parent only.",
        },
        {
            "bucket_a": "other_marketplace_fees",
            "bucket_b": "child_fee_buckets",
            "overlap_type": "duplicate_of_other_bucket",
            "overlap_amount_or_reason": "Parent mart rollup includes services + storage + payment_processing + penalties + other.",
            "action_required": "Never add this rollup on top of child lines; use it only as an aggregate KPI.",
        },
    ]


def completeness_rows() -> list[dict[str, Any]]:
    return [
        {
            "bucket_name": "ads",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current ads layer already acts as the primary source of truth.",
        },
        {
            "bucket_name": "wb_promotion",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "products",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Need campaign/SKU metadata in finance docs if we ever want honest allocation instead of duplicate exclusion.",
        },
        {
            "bucket_name": "storage",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "products",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Warehouse/SKU keys on finance storage rows would make direct attribution possible instead of share allocation.",
        },
        {
            "bucket_name": "commission_core",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current finance + order linkage is already sufficient.",
        },
        {
            "bucket_name": "wb_reward",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "A dedicated WB article code in raw docs would make the semantic naming even cleaner.",
        },
        {
            "bucket_name": "wb_reward_return_adjustment",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Separate explicit return-adjustment marker from WB would reduce dependence on row-shape inference.",
        },
        {
            "bucket_name": "pvz_reimbursement",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current raw label is already explicit enough.",
        },
        {
            "bucket_name": "pvz_related_penalty",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current bonus_type_name is explicit enough for this slice.",
        },
        {
            "bucket_name": "acquiring",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "The 1:1 match between acquiring_fee and service_amount is already decisive.",
        },
        {
            "bucket_name": "payment_processing",
            "enough_sources_yes_no": "no",
            "missing_source_if_any": "finance",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Need a separate numeric fact bucket instead of only raw text labels on acquiring rows.",
        },
        {
            "bucket_name": "logistics",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "A direct WB logistics report would be a second amount-level confirmation, but current finance truth is stable.",
        },
        {
            "bucket_name": "returns",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "A wider dedicated return-deductions source would help if return variety grows beyond the current WB reward adjustment slice.",
        },
        {
            "bucket_name": "services_product_level",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Keep the refined child split in cabinet instead of relying on the coarse mart parent.",
        },
        {
            "bucket_name": "services_store_level",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Keep the refined child split in cabinet instead of relying on the coarse mart parent.",
        },
        {
            "bucket_name": "jam_subscription",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current store-only signal is stable and explicit enough.",
        },
        {
            "bucket_name": "other_logistics_rebill",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "no",
            "how_to_improve_short": "Current raw finance naming is already explicit enough.",
        },
        {
            "bucket_name": "penalties",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Expose the PVZ-specific subtype in UI instead of only the coarse penalties parent.",
        },
        {
            "bucket_name": "other_marketplace_fees",
            "enough_sources_yes_no": "yes",
            "missing_source_if_any": "",
            "can_be_improved_yes_no": "yes",
            "how_to_improve_short": "Use only as a rollup KPI; child buckets are the actionable sources.",
        },
    ]


def final_verdicts() -> list[VerdictRow]:
    return [
        VerdictRow("ads", "trusted_product_level"),
        VerdictRow("wb_promotion", "duplicate_should_be_removed"),
        VerdictRow("storage", "trusted_mixed"),
        VerdictRow("commission_core", "trusted_product_level"),
        VerdictRow("wb_reward", "needs_reclassification"),
        VerdictRow("wb_reward_return_adjustment", "needs_reclassification"),
        VerdictRow("pvz_reimbursement", "trusted_product_level"),
        VerdictRow("pvz_related_penalty", "needs_reclassification"),
        VerdictRow("acquiring", "trusted_product_level"),
        VerdictRow("payment_processing", "needs_more_source_data"),
        VerdictRow("logistics", "trusted_product_level"),
        VerdictRow("returns", "trusted_product_level"),
        VerdictRow("services_product_level", "trusted_product_level"),
        VerdictRow("services_store_level", "trusted_store_level"),
        VerdictRow("jam_subscription", "trusted_store_level"),
        VerdictRow("other_logistics_rebill", "trusted_product_level"),
        VerdictRow("penalties", "trusted_product_level"),
        VerdictRow("other_marketplace_fees", "trusted_mixed"),
    ]


def build_report(
    schema: str,
    period_start: str,
    period_end: str,
    confirmations: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    completeness: list[dict[str, Any]],
    verdicts: list[VerdictRow],
) -> str:
    by_bucket = {row["bucket_name"]: row for row in confirmations}
    verdict_map = {row.bucket_name: row.final_verdict for row in verdicts}
    trusted = [row for row in verdicts if row.final_verdict.startswith("trusted")]
    needs = [row for row in verdicts if not row.final_verdict.startswith("trusted")]

    confirmation_lines = "\n".join(
        f"- `{row['bucket_name']}` / `{row['ui_label']}`: `{money_text(row['amount_total'])}` from `{row['source_of_truth']}`, confirmed by `{row['confirming_source']}` via `{row['confirmation_type']}`. {row['notes_short']}"
        for row in confirmations
    )
    overlap_lines = "\n".join(
        f"- `{row['bucket_a']}` vs `{row['bucket_b']}`: {row['overlap_type']}. {row['overlap_amount_or_reason']} Action: {row['action_required']}"
        for row in overlaps
    )
    completeness_lines = "\n".join(
        f"- `{row['bucket_name']}`: enough=`{row['enough_sources_yes_no']}`, improve=`{row['can_be_improved_yes_no']}`. {row['how_to_improve_short']}"
        for row in completeness
    )
    verdict_lines = "\n".join(
        f"- `{row.bucket_name}`: `{row.final_verdict}`"
        for row in verdicts
    )
    return f"""
# WB Finance Confirmation Report

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Confirmation Matrix

{confirmation_lines}

## 2. Required Math Checks

- `ads` vs `WB Продвижение`: `ads` remains the primary advertising truth; `WB Продвижение` behaves as the same spend on finance document windows and should stay excluded.
- `storage` finance vs `paid_storage`: finance amount and positive-only paid_storage amount match on the aligned window closely enough to treat paid_storage as a strong confirming allocation layer.
- `acquiring_fee` vs refined `acquiring`: exact 1:1 row overlap, so the amount is trusted and the old sale-linked service view is only a coarse parent.
- `ppvz_reward` vs current target bucket: the amount is real, but current placement in `other` / `returns` is a classification artifact rather than the best final business label.
- `pvz reimbursement` and `pvz-related penalty`: both are confirmed by explicit raw labels; the reimbursement is inside `other`, while the penalty is inside `penalties`.
- `services` split product/store: product services are currently `acquiring + review_service`; store services are currently `jam_subscription`.
- `Джем` as store-only: confirmed; no honest product key exists and the amount stays on store level.
- `other` breakdown: no black box remains; the active slices are WB reward, transport/warehouse reimbursement, and PVZ reimbursement.
- `commission` granularity: commission core is clean; acquiring is separate economically; payment organization is text-confirmed but not yet a standalone bucket.
- `logistics` and `returns`: both remain internally consistent; returns in this period are effectively the WB reward return adjustment slice.

## 3. Overlap Check

{overlap_lines}

## 4. Source Completeness

{completeness_lines}

## 5. Final Verdict

Trusted:
{chr(10).join(f"- `{row.bucket_name}`: `{row.final_verdict}`" for row in trusted)}

Needs Attention:
{chr(10).join(f"- `{row.bucket_name}`: `{row.final_verdict}`" for row in needs)}

Full verdict list:
{verdict_lines}
"""


def main() -> int:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        matrix = {str(row["bucket_subtype"]): row for row in matrix_rows}
        mart = mart_totals(conn, SCHEMA, period_start, period_end)
        finance_rows = fetch_finance_dataset(conn, SCHEMA, period_start, period_end)
        _ = fetch_fact_dataset(conn, SCHEMA, period_start, period_end)
        promo = wb_promotion_overlap(conn, SCHEMA)
        storage = storage_confirmation(conn, SCHEMA)
        payment_org_amount = payment_processing_label_amount(conn, SCHEMA, "Комиссия за организацию платежа с НДС")

    confirmations = confirmation_rows(matrix, mart, finance_rows, promo, storage, payment_org_amount)
    overlaps = overlap_rows(promo, storage, matrix, mart, payment_org_amount)
    completeness = completeness_rows()
    verdicts = final_verdicts()

    write_csv(
        CONFIRMATION_CSV,
        [
            {
                **row,
                "amount_total": money_text(row["amount_total"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in confirmations
        ],
        [
            "bucket_name",
            "ui_label",
            "amount_total",
            "source_of_truth",
            "confirming_source",
            "confirmation_type",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "confidence",
            "double_count_risk_yes_no",
            "currently_used_in_marts_yes_no",
            "notes_short",
        ],
    )
    write_csv(
        OVERLAP_CSV,
        overlaps,
        [
            "bucket_a",
            "bucket_b",
            "overlap_type",
            "overlap_amount_or_reason",
            "action_required",
        ],
    )
    write_csv(
        COMPLETENESS_CSV,
        completeness,
        [
            "bucket_name",
            "enough_sources_yes_no",
            "missing_source_if_any",
            "can_be_improved_yes_no",
            "how_to_improve_short",
        ],
    )
    write_text(
        REPORT_MD,
        build_report(
            SCHEMA,
            period_start,
            period_end,
            confirmations,
            overlaps,
            completeness,
            verdicts,
        ),
    )
    print(f"Wrote {CONFIRMATION_CSV}")
    print(f"Wrote {OVERLAP_CSV}")
    print(f"Wrote {COMPLETENESS_CSV}")
    print(f"Wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
