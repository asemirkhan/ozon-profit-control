#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_wb_finance_confirmation_audit_v1 import (  # noqa: E402
    mart_totals,
    money,
    money_text,
    query_one,
    wb_promotion_overlap,
)
from src.services.wb_expense_granularity import (  # noqa: E402
    build_wb_expense_granularity_matrix,
    latest_wb_period,
)
from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA") or "client_demo_90d_20260408"
TARGET_CSV = ROOT / "wb_cabinet_target_expense_structure.csv"
TARGET_MD = ROOT / "wb_cabinet_target_expense_structure.md"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_text(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def fetch_raw_wb_promotion(conn: Any, schema: str) -> Decimal:
    sql = f"""
    SELECT COALESCE(SUM(amount), 0)::numeric(18,2) AS amount
    FROM {schema}.stg_wb_finance
    WHERE COALESCE(marketplace, 'wb') = 'wb'
      AND LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижение%%'
    """
    row = query_one(conn, sql, ())
    return money(row.get("amount"))


def build_rows(
    matrix: dict[str, dict[str, Any]],
    mart: dict[str, Any],
    raw_wb_promotion: Decimal,
) -> list[dict[str, Any]]:
    return [
        {
            "target_order": 10,
            "technical_name": "ads",
            "ui_label": "Реклама",
            "amount_total": money(matrix["ads"]["amount"]),
            "product_level_amount": money(matrix["ads"]["product_level_amount"]),
            "store_level_amount": money(matrix["ads"]["store_level_amount"]),
            "mixed_amount": money(matrix["ads"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "ads",
            "notes_short": "Основной рекламный расход. Финансовый дубль WB Продвижение должен оставаться скрытым.",
        },
        {
            "target_order": 20,
            "technical_name": "commission_core",
            "ui_label": "Комиссия маркетплейса",
            "amount_total": money(matrix["commission_core"]["amount"]),
            "product_level_amount": money(matrix["commission_core"]["product_level_amount"]),
            "store_level_amount": money(matrix["commission_core"]["store_level_amount"]),
            "mixed_amount": money(matrix["commission_core"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "commission",
            "notes_short": "Самостоятельная товарная статья из commission_amount.",
        },
        {
            "target_order": 30,
            "technical_name": "acquiring",
            "ui_label": "Эквайринг",
            "amount_total": money(matrix["acquiring"]["amount"]),
            "product_level_amount": money(matrix["acquiring"]["product_level_amount"]),
            "store_level_amount": money(matrix["acquiring"]["store_level_amount"]),
            "mixed_amount": money(matrix["acquiring"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "services -> acquiring refinement",
            "notes_short": "Показывать отдельно. Не дублировать общим sale_linked_service.",
        },
        {
            "target_order": 40,
            "technical_name": "payment_processing",
            "ui_label": "Организация платежа",
            "amount_total": money(matrix["payment_processing"]["amount"]),
            "product_level_amount": money(matrix["payment_processing"]["product_level_amount"]),
            "store_level_amount": money(matrix["payment_processing"]["store_level_amount"]),
            "mixed_amount": money(matrix["payment_processing"]["mixed_amount"]),
            "cabinet_role": "future_line_after_source_fix",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "payment_processing",
            "notes_short": "Лейбл подтвержден, но отдельного денежного bucket в текущем факте нет.",
        },
        {
            "target_order": 50,
            "technical_name": "logistics",
            "ui_label": "Логистика",
            "amount_total": money(matrix["logistics"]["amount"]),
            "product_level_amount": money(matrix["logistics"]["product_level_amount"]),
            "store_level_amount": money(matrix["logistics"]["store_level_amount"]),
            "mixed_amount": money(matrix["logistics"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "logistics",
            "notes_short": "Товарная статья логистики без текущего риска дубля.",
        },
        {
            "target_order": 60,
            "technical_name": "storage",
            "ui_label": "Хранение",
            "amount_total": money(matrix["storage"]["amount"]),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": money(matrix["storage"]["amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "storage",
            "notes_short": "Показывать как распределяемую mixed-статью, а не как прямую SKU-строку.",
        },
        {
            "target_order": 70,
            "technical_name": "other_sale_adjustment",
            "ui_label": "Вознаграждение Wildberries",
            "amount_total": money(matrix["other_sale_adjustment"]["amount"]),
            "product_level_amount": money(matrix["other_sale_adjustment"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_sale_adjustment"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_sale_adjustment"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "other -> wb_reward refinement",
            "notes_short": "Вытащить из coarse other и показывать отдельной человеческой строкой.",
        },
        {
            "target_order": 80,
            "technical_name": "wb_reward_return_adjustment",
            "ui_label": "Корректировка вознаграждения Wildberries",
            "amount_total": money(matrix["wb_reward_return_adjustment"]["amount"]),
            "product_level_amount": money(matrix["wb_reward_return_adjustment"]["product_level_amount"]),
            "store_level_amount": money(matrix["wb_reward_return_adjustment"]["store_level_amount"]),
            "mixed_amount": money(matrix["wb_reward_return_adjustment"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "returns -> wb_reward refinement",
            "notes_short": "Показывать отдельно от общего bucket возвратных удержаний.",
        },
        {
            "target_order": 90,
            "technical_name": "returns",
            "ui_label": "Прочие возвратные удержания",
            "amount_total": money(matrix["returns"]["amount"]),
            "product_level_amount": money(matrix["returns"]["product_level_amount"]),
            "store_level_amount": money(matrix["returns"]["store_level_amount"]),
            "mixed_amount": money(matrix["returns"]["mixed_amount"]),
            "cabinet_role": "conditional_fallback",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "returns",
            "notes_short": "Fallback-строка для прочих возвратных удержаний, если они остаются после выделения WB reward correction.",
        },
        {
            "target_order": 100,
            "technical_name": "other_pvz_reimbursement",
            "ui_label": "Возмещение за выдачу и возврат на ПВЗ",
            "amount_total": money(matrix["other_pvz_reimbursement"]["amount"]),
            "product_level_amount": money(matrix["other_pvz_reimbursement"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_pvz_reimbursement"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_pvz_reimbursement"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "other -> pvz refinement",
            "notes_short": "Подтвержденная ПВЗ-статья с явным raw ярлыком.",
        },
        {
            "target_order": 110,
            "technical_name": "pvz_related_penalty",
            "ui_label": "ПВЗ-связанные удержания",
            "amount_total": money(matrix["pvz_related_penalty"]["amount"]),
            "product_level_amount": money(matrix["pvz_related_penalty"]["product_level_amount"]),
            "store_level_amount": money(matrix["pvz_related_penalty"]["store_level_amount"]),
            "mixed_amount": money(matrix["pvz_related_penalty"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "penalties -> pvz refinement",
            "notes_short": "Показывать отдельной строкой вместо грубого penalties, когда raw явно указывает на ПВЗ.",
        },
        {
            "target_order": 120,
            "technical_name": "penalties",
            "ui_label": "Прочие штрафы",
            "amount_total": money(matrix["penalties"]["amount"]),
            "product_level_amount": money(matrix["penalties"]["product_level_amount"]),
            "store_level_amount": money(matrix["penalties"]["store_level_amount"]),
            "mixed_amount": money(matrix["penalties"]["mixed_amount"]),
            "cabinet_role": "conditional_fallback",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "penalties",
            "notes_short": "Fallback-подтип для штрафов, не попавших в более точные подстатьи.",
        },
        {
            "target_order": 130,
            "technical_name": "other_logistics_rebill",
            "ui_label": "Возмещение издержек по перевозке/складским операциям",
            "amount_total": money(matrix["other_logistics_rebill"]["amount"]),
            "product_level_amount": money(matrix["other_logistics_rebill"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_logistics_rebill"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_logistics_rebill"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "other -> logistics rebill refinement",
            "notes_short": "Явная отдельная подстатья из raw finance.",
        },
        {
            "target_order": 140,
            "technical_name": "review_service",
            "ui_label": "Отзывы",
            "amount_total": money(matrix["review_service"]["amount"]),
            "product_level_amount": money(matrix["review_service"]["product_level_amount"]),
            "store_level_amount": money(matrix["review_service"]["store_level_amount"]),
            "mixed_amount": money(matrix["review_service"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "services",
            "notes_short": "Небольшая, но понятная товарная сервисная статья.",
        },
        {
            "target_order": 150,
            "technical_name": "sale_linked_service",
            "ui_label": "Товарные сервисы, связанные с продажей",
            "amount_total": money(matrix["sale_linked_service"]["amount"]),
            "product_level_amount": money(matrix["sale_linked_service"]["product_level_amount"]),
            "store_level_amount": money(matrix["sale_linked_service"]["store_level_amount"]),
            "mixed_amount": money(matrix["sale_linked_service"]["mixed_amount"]),
            "cabinet_role": "conditional_fallback",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "services",
            "notes_short": "Fallback для product service_amount-строк, не объясненных эквайрингом или отдельными named services.",
        },
        {
            "target_order": 160,
            "technical_name": "other_service",
            "ui_label": "Прочие товарные сервисы",
            "amount_total": money(matrix["other_service"]["amount"]),
            "product_level_amount": money(matrix["other_service"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_service"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_service"]["mixed_amount"]),
            "cabinet_role": "conditional_fallback",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "services",
            "notes_short": "Fallback для прочих товарных сервисов, если они появятся в будущем.",
        },
        {
            "target_order": 170,
            "technical_name": "jam_subscription",
            "ui_label": "Подписка Джем",
            "amount_total": money(matrix["jam_subscription"]["amount"]),
            "product_level_amount": money(matrix["jam_subscription"]["product_level_amount"]),
            "store_level_amount": money(matrix["jam_subscription"]["store_level_amount"]),
            "mixed_amount": money(matrix["jam_subscription"]["mixed_amount"]),
            "cabinet_role": "visible_line",
            "show_in_cabinet_yes_no": "yes",
            "source_bucket": "services_store",
            "notes_short": "Store-only статья; показывать отдельно от общего магазина-сервисов.",
        },
        {
            "target_order": 180,
            "technical_name": "other_store_service",
            "ui_label": "Прочие магазинные сервисы",
            "amount_total": money(matrix["other_store_service"]["amount"]),
            "product_level_amount": money(matrix["other_store_service"]["product_level_amount"]),
            "store_level_amount": money(matrix["other_store_service"]["store_level_amount"]),
            "mixed_amount": money(matrix["other_store_service"]["mixed_amount"]),
            "cabinet_role": "conditional_fallback",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "services_store",
            "notes_short": "Technical fallback для store-only service tail; не показывать без явного бизнес-смысла.",
        },
        {
            "target_order": 190,
            "technical_name": "services_product_level",
            "ui_label": "Товарные сервисы",
            "amount_total": money(mart["services_product_total"]),
            "product_level_amount": money(mart["services_product_total"]),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": Decimal("0.00"),
            "cabinet_role": "technical_parent_hidden",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "mart services_amount",
            "notes_short": "Technical parent-rollup. Не показывать одновременно с дочерними строками.",
        },
        {
            "target_order": 200,
            "technical_name": "services_store_level",
            "ui_label": "Магазинные сервисы",
            "amount_total": money(mart["services_store_total"]),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": money(mart["services_store_total"]),
            "mixed_amount": Decimal("0.00"),
            "cabinet_role": "technical_parent_hidden",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "mart services_amount",
            "notes_short": "Technical parent-rollup. Сейчас полностью объясняется строкой Подписка Джем.",
        },
        {
            "target_order": 210,
            "technical_name": "other_marketplace_fees",
            "ui_label": "Прочие комиссии и удержания маркетплейса",
            "amount_total": money(mart["marketplace_other_fees_total"]),
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": Decimal("0.00"),
            "mixed_amount": money(mart["marketplace_other_fees_total"]),
            "cabinet_role": "technical_parent_hidden",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "mart marketplace_other_fees_amount",
            "notes_short": "Parent KPI-rollup над services + storage + penalties + other. Нельзя показывать рядом с детьми как отдельный расход.",
        },
        {
            "target_order": 220,
            "technical_name": "wb_promotion_overlap_excluded",
            "ui_label": "WB Продвижение",
            "amount_total": raw_wb_promotion,
            "product_level_amount": Decimal("0.00"),
            "store_level_amount": raw_wb_promotion,
            "mixed_amount": Decimal("0.00"),
            "cabinet_role": "excluded_duplicate_hidden",
            "show_in_cabinet_yes_no": "no",
            "source_bucket": "finance raw only",
            "notes_short": "Raw finance duplicate of ads. Держать вне финальной экономики и вне клиентского breakdown.",
        },
    ]


def build_report(schema: str, period_start: str, period_end: str, rows: list[dict[str, Any]]) -> str:
    visible = [row for row in rows if row["show_in_cabinet_yes_no"] == "yes" and money(row["amount_total"]) > Decimal("0.00")]
    hidden = [row for row in rows if row["show_in_cabinet_yes_no"] == "no" and money(row["amount_total"]) > Decimal("0.00")]
    fallbacks = [row for row in rows if row["cabinet_role"] == "conditional_fallback"]
    return f"""
# WB Cabinet Target Expense Structure

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Show In Cabinet

{chr(10).join(f"- `{row['ui_label']}`: `{money_text(row['amount_total'])}` ({row['cabinet_role']})" for row in visible)}

## 2. Keep Hidden As Technical Or Duplicate

{chr(10).join(f"- `{row['ui_label']}`: `{money_text(row['amount_total'])}` ({row['cabinet_role']})" for row in hidden)}

## 3. Fallback Rows

{chr(10).join(f"- `{row['ui_label']}`: current `{money_text(row['amount_total'])}`. {row['notes_short']}" for row in fallbacks)}

## 4. Target Principles

- show child expense lines with human labels
- hide coarse parent rollups like `services_store_level` and `other_marketplace_fees`
- hide excluded duplicates like `WB Продвижение`
- keep fallback rows only for future non-zero tails that are not yet explained by narrower subtypes
"""


def main() -> int:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        matrix = {str(row["bucket_subtype"]): row for row in matrix_rows}
        mart = mart_totals(conn, SCHEMA, period_start, period_end)
        raw_wb_promotion = fetch_raw_wb_promotion(conn, SCHEMA)

    rows = build_rows(matrix, mart, raw_wb_promotion)
    rows.sort(key=lambda row: int(row["target_order"]))
    write_csv(
        TARGET_CSV,
        [
            {
                **row,
                "amount_total": money_text(row["amount_total"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
            }
            for row in rows
        ],
        [
            "target_order",
            "technical_name",
            "ui_label",
            "amount_total",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "cabinet_role",
            "show_in_cabinet_yes_no",
            "source_bucket",
            "notes_short",
        ],
    )
    write_text(TARGET_MD, build_report(SCHEMA, period_start, period_end, rows))
    print(f"Wrote {TARGET_CSV}")
    print(f"Wrote {TARGET_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
