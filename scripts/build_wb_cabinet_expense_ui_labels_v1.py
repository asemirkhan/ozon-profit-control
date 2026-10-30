#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.wb_expense_granularity import (  # noqa: E402
    build_wb_expense_granularity_matrix,
    latest_wb_period,
)
from src.utils.db import get_conn  # noqa: E402
from scripts.build_wb_ppvz_pvz_acquiring_audit_v1 import (  # noqa: E402
    aggregate_slice,
    build_fact_maps,
    fetch_fact_dataset,
    fetch_finance_dataset,
    money,
    money_text,
)

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA") or "client_demo_90d_20260408"
OUTPUT_CSV = ROOT / "wb_cabinet_expense_ui_labels.csv"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        matrix = {str(row["bucket_subtype"]): row for row in matrix_rows}
        finance_rows = fetch_finance_dataset(conn, SCHEMA, period_start, period_end)
        fact_dataset = fetch_fact_dataset(conn, SCHEMA, period_start, period_end)
        fact_map, _ = build_fact_maps(fact_dataset)

    wb_reward_return_rows = [
        row
        for row in finance_rows
        if row["ppvz_reward_abs"] > 0
        and row["amount_abs"] == row["ppvz_reward_abs"]
        and row["operation_type"] == "Возврат"
        and row["supplier_oper_name"] == "Возврат"
        and row["doc_type_name"] == "Возврат"
    ]
    wb_reward_return = aggregate_slice(
        wb_reward_return_rows,
        fact_map,
        "amount_abs",
        "amount = raw_payload.ppvz_reward",
        "Возврат | Возврат | Возврат",
        "Корректировка / сторно вознаграждения Wildberries на возврате",
    )

    pvz_penalty_rows = [
        row
        for row in finance_rows
        if row["bonus_type_name"] == "Платное хранение возвратов на ПВЗ более 3 дней"
    ]
    pvz_penalty = aggregate_slice(
        pvz_penalty_rows,
        fact_map,
        "amount_abs",
        "amount",
        "Штраф | Платное хранение возвратов на ПВЗ более 3 дней",
        "ПВЗ-связанный штраф / хранение возвратов на ПВЗ",
    )

    rows = [
        {
            "technical_name": "wb_reward / ppvz_reward",
            "ui_label": "Вознаграждение Wildberries",
            "amount": money_text(matrix["other_sale_adjustment"]["amount"]),
            "product/store level": "product",
            "explanation_short": "Нейтральный UI-label для отдельного денежного компонента из raw поля ppvz_reward; источник в текущей модели - other_sale_adjustment.",
        },
        {
            "technical_name": "wb_reward_return_adjustment",
            "ui_label": "Корректировка вознаграждения Wildberries",
            "amount": money_text(wb_reward_return["amount"]),
            "product/store level": wb_reward_return["product_store_level"],
            "explanation_short": "Возвратная часть того же ppvz_reward-компонента; в текущей модели живет в return-side слайсе, но в UI лучше показывать отдельно.",
        },
        {
            "technical_name": "pvz_reimbursement",
            "ui_label": "Возмещение за выдачу и возврат на ПВЗ",
            "amount": money_text(matrix["other_pvz_reimbursement"]["amount"]),
            "product/store level": "product",
            "explanation_short": "Подтвержденная ПВЗ-статья по raw ярлыку возмещения за выдачу и возврат на ПВЗ; источник в текущей модели - other_pvz_reimbursement.",
        },
        {
            "technical_name": "pvz_related_penalty",
            "ui_label": "ПВЗ-связанные удержания",
            "amount": money_text(pvz_penalty["amount"]),
            "product/store level": pvz_penalty["product_store_level"],
            "explanation_short": "Отдельный ПВЗ-связанный штрафовой срез из общего penalties; подтвержден raw полем bonus_type_name про хранение возвратов на ПВЗ.",
        },
        {
            "technical_name": "acquiring",
            "ui_label": "Эквайринг",
            "amount": money_text(matrix["acquiring"]["amount"]),
            "product/store level": "product",
            "explanation_short": "Выделяется из acquiring_fee / service_amount и уже может показываться отдельной строкой без двусмысленности.",
        },
        {
            "technical_name": "payment_processing",
            "ui_label": "Организация платежа",
            "amount": money_text(matrix["payment_processing"]["amount"]),
            "product/store level": "product",
            "explanation_short": "UI-label подтвержден raw ярлыками, но в текущем периоде отдельный денежный слой в fact не сформирован.",
        },
        {
            "technical_name": "jam_subscription",
            "ui_label": "Подписка Джем",
            "amount": money_text(matrix["jam_subscription"]["amount"]),
            "product/store level": "store",
            "explanation_short": "Store-only подписка кабинета WB; в UI лучше показывать сразу как подписку, без внутреннего жаргона.",
        },
    ]

    write_csv(
        OUTPUT_CSV,
        rows,
        [
            "technical_name",
            "ui_label",
            "amount",
            "product/store level",
            "explanation_short",
        ],
    )
    print(f"Wrote {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
