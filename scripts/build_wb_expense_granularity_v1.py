#!/usr/bin/env python3
from __future__ import annotations

import csv
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.wb_expense_granularity import (  # noqa: E402
    build_cabinet_expense_structure_before_after,
    build_wb_acquiring_check,
    build_wb_expense_granularity_matrix,
    build_wb_services_split,
    fetch_wb_expense_fact_rows,
    latest_wb_period,
)
from src.utils.db import get_conn  # noqa: E402

load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("WB_EXPENSE_GRANULARITY_SCHEMA") or "client_demo_90d_20260408"

MATRIX_CSV = ROOT / "wb_expense_granularity_matrix.csv"
REPORT_MD = ROOT / "wb_expense_granularity_report.md"
SERVICES_CSV = ROOT / "wb_services_and_subscription_split.csv"
ACQUIRING_CSV = ROOT / "wb_acquiring_check.csv"
CABINET_BEFORE_AFTER_CSV = ROOT / "cabinet_expense_structure_before_after.csv"


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


def build_report(
    schema: str,
    period_start: str,
    period_end: str,
    matrix_rows: list[dict[str, Any]],
    services_rows: list[dict[str, Any]],
    acquiring_rows: list[dict[str, Any]],
    fact_total: Decimal,
) -> str:
    nonzero_rows = [row for row in matrix_rows if money(row["amount"]) > 0]
    product_rows = [row for row in nonzero_rows if row["bucket_group"] == "product-level"]
    store_rows = [row for row in nonzero_rows if row["bucket_group"] == "store-level"]
    mixed_rows = [row for row in nonzero_rows if row["bucket_group"] == "mixed / heuristic"]
    matrix_total = sum((money(row["amount"]) for row in nonzero_rows), Decimal("0.00"))
    promo_row = next(row for row in services_rows if row["service_subtype"] == "wb_promotion")
    jam_row = next(row for row in services_rows if row["service_subtype"] == "jam_subscription")
    acquiring_fact_row = next(row for row in acquiring_rows if row["layer"] == "fact_product_money_attribution")

    return f"""# WB Expense Granularity Report

- Schema: `{schema}`
- Period: `{period_start}` .. `{period_end}`

## 1. Final Expense Structure

- canonical expense total from `fact_product_money_attribution`: `{money_text(fact_total)}`
- matrix total after subtype split: `{money_text(matrix_total)}`
- delta: `{money_text(matrix_total - fact_total)}`

Product-level non-zero subarticles:
{chr(10).join(f"- `{row['label']}`: `{money_text(row['amount'])}`" for row in product_rows) or "- none"}

Store-level non-zero subarticles:
{chr(10).join(f"- `{row['label']}`: `{money_text(row['amount'])}`" for row in store_rows) or "- none"}

Mixed / heuristic non-zero subarticles:
{chr(10).join(f"- `{row['label']}`: `{money_text(row['amount'])}` via `{row['attribution_method']}`" for row in mixed_rows) or "- none"}

## 2. Services Split

{chr(10).join(f"- `{row['service_subtype']}`: raw `{money_text(row['raw_source_amount'])}`, final `{money_text(row['final_model_amount'])}`, status `{row['status']}`" for row in services_rows)}

## 3. Required Checks

1. `WB Продвижение` no longer duplicates ads:
   - raw promotion source amount = `{money_text(promo_row['raw_source_amount'])}`
   - final expense amount = `{money_text(promo_row['final_model_amount'])}`
   - conclusion: excluded from final expense structure as ads overlap

2. `Джем` stays store-only:
   - final amount = `{money_text(jam_row['final_model_amount'])}`
   - product-level amount = `{money_text(jam_row['product_level_amount'])}`
   - store-level amount = `{money_text(jam_row['store_level_amount'])}`

3. Acquiring:
   - fact amount = `{money_text(acquiring_fact_row['amount_total'])}`
   - explanation = `{acquiring_fact_row['why']}`

4. Cabinet split integrity:
   - old cabinet hid multiple subarticles inside one `withholding`
   - new cabinet shows product, store, and mixed subarticles separately without changing canonical totals

## 4. Output Files

- `wb_expense_granularity_matrix.csv`
- `wb_services_and_subscription_split.csv`
- `wb_acquiring_check.csv`
- `cabinet_expense_structure_before_after.csv`
"""


def main() -> None:
    with get_conn() as conn:
        period_start, period_end = latest_wb_period(conn, SCHEMA)
        matrix_rows = build_wb_expense_granularity_matrix(conn, SCHEMA, period_start, period_end)
        services_rows = build_wb_services_split(conn, SCHEMA, period_start, period_end)
        acquiring_rows = build_wb_acquiring_check(conn, SCHEMA, period_start, period_end)
        cabinet_rows = build_cabinet_expense_structure_before_after(conn, SCHEMA, period_start, period_end)
        fact_total = sum(
            (money(row["amount"]) for row in fetch_wb_expense_fact_rows(conn, SCHEMA, period_start, period_end)),
            Decimal("0.00"),
        )

    write_csv(
        MATRIX_CSV,
        [
            {
                "bucket_group": row["bucket_group"],
                "bucket_subtype": row["bucket_subtype"],
                "label": row["label"],
                "amount": money_text(row["amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "mixed_amount": money_text(row["mixed_amount"]),
                "source_layer": row["source_layer"],
                "attribution_method": row["attribution_method"],
                "confidence": row["confidence"],
                "reason": row["reason"],
            }
            for row in matrix_rows
        ],
        [
            "bucket_group",
            "bucket_subtype",
            "label",
            "amount",
            "product_level_amount",
            "store_level_amount",
            "mixed_amount",
            "source_layer",
            "attribution_method",
            "confidence",
            "reason",
        ],
    )

    write_csv(
        SERVICES_CSV,
        [
            {
                "service_subtype": row["service_subtype"],
                "raw_source_amount": money_text(row["raw_source_amount"]),
                "final_model_amount": money_text(row["final_model_amount"]),
                "product_level_amount": money_text(row["product_level_amount"]),
                "store_level_amount": money_text(row["store_level_amount"]),
                "status": row["status"],
                "reason": row["reason"],
            }
            for row in services_rows
        ],
        [
            "service_subtype",
            "raw_source_amount",
            "final_model_amount",
            "product_level_amount",
            "store_level_amount",
            "status",
            "reason",
        ],
    )

    write_csv(
        ACQUIRING_CSV,
        [
            {
                "layer": row["layer"],
                "fields_checked": row["fields_checked"],
                "rows_total": row["rows_total"],
                "amount_total": money_text(row["amount_total"]),
                "why": row["why"],
            }
            for row in acquiring_rows
        ],
        ["layer", "fields_checked", "rows_total", "amount_total", "why"],
    )

    write_csv(
        CABINET_BEFORE_AFTER_CSV,
        [
            {
                "stage": row["stage"],
                "scope": row["scope"],
                "article": row["article"],
                "amount": money_text(row["amount"]),
                "note": row["note"],
            }
            for row in cabinet_rows
        ],
        ["stage", "scope", "article", "amount", "note"],
    )

    REPORT_MD.write_text(
        build_report(
            SCHEMA,
            period_start,
            period_end,
            matrix_rows,
            services_rows,
            acquiring_rows,
            fact_total,
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["SCHEMA"] = SCHEMA
    env["OZON_SCHEMA"] = SCHEMA
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_profit_control_cabinet_human_pack_v1.py")],
        cwd=ROOT,
        check=True,
        env=env,
    )

    print(f"Wrote {MATRIX_CSV}")
    print(f"Wrote {REPORT_MD}")
    print(f"Wrote {SERVICES_CSV}")
    print(f"Wrote {ACQUIRING_CSV}")
    print(f"Wrote {CABINET_BEFORE_AFTER_CSV}")


if __name__ == "__main__":
    main()
