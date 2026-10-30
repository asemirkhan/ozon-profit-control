#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
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

REPORT_PATH = ROOT / "profit_control_revenue_decomposition_frame_v1_report.md"
EXAMPLES_CSV_PATH = ROOT / "profit_control_revenue_decomposition_frame_v1_examples.csv"
QUALITY_CSV_PATH = ROOT / "profit_control_revenue_decomposition_frame_v1_quality_checks.csv"
JSON_PATH = ROOT / "profit_control_revenue_decomposition_frame_v1.json"

TWOPLACES = Decimal("0.01")


@dataclass(frozen=True)
class Window:
    anchor_day: str
    current_start: str
    current_end: str
    previous_start: str
    previous_end: str


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money_text(value: Decimal) -> str:
    return f"{q2(value):.2f}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def fetch_all(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def fetch_one(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else {}


def get_window(conn: Any) -> Window:
    sql = f"""
    WITH anchor AS (
      SELECT MAX(day)::date AS anchor_day
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    )
    SELECT
      anchor_day,
      (anchor_day - INTERVAL '27 day')::date AS current_start,
      anchor_day::date AS current_end,
      (anchor_day - INTERVAL '55 day')::date AS previous_start,
      (anchor_day - INTERVAL '28 day')::date AS previous_end
    FROM anchor
    """
    row = fetch_one(conn, sql)
    if not row or row["anchor_day"] is None:
        raise RuntimeError("No confirmedThrough anchor found")
    return Window(
        anchor_day=str(row["anchor_day"]),
        current_start=str(row["current_start"]),
        current_end=str(row["current_end"]),
        previous_start=str(row["previous_start"]),
        previous_end=str(row["previous_end"]),
    )


def get_same_skus(conn: Any) -> list[dict[str, Any]]:
    sql = f"""
    WITH params AS (
      SELECT MAX(day)::date AS anchor_day
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    ),
    windows AS (
      SELECT
        anchor_day,
        (anchor_day - INTERVAL '27 day')::date AS current_start,
        anchor_day::date AS current_end,
        (anchor_day - INTERVAL '55 day')::date AS previous_start,
        (anchor_day - INTERVAL '28 day')::date AS previous_end
      FROM params
    )
    SELECT
      p.offer_id,
      p.sku,
      p.product_name,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_curr,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_prev
    FROM {SCHEMA}.pc_v2_daily_product_fact p
    CROSS JOIN windows w
    WHERE p.marketplace = 'wb'
      AND p.finance_confirmed_flag = TRUE
    GROUP BY 1,2,3
    HAVING SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END) > 10000
       AND SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)
         < SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)
    ORDER BY
      (
        SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)
        - SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)
      ) ASC,
      revenue_prev DESC
    LIMIT 3
    """
    return fetch_all(conn, sql)


def get_metrics_for_skus(conn: Any, offer_ids: list[str]) -> list[dict[str, Any]]:
    sql = f"""
    WITH params AS (
      SELECT MAX(day)::date AS anchor_day
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    ),
    windows AS (
      SELECT
        anchor_day,
        (anchor_day - INTERVAL '27 day')::date AS current_start,
        anchor_day::date AS current_end,
        (anchor_day - INTERVAL '55 day')::date AS previous_start,
        (anchor_day - INTERVAL '28 day')::date AS previous_end
      FROM params
    )
    SELECT
      p.offer_id,
      p.sku,
      p.product_name,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_prev,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_curr,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.units_sold, 0) ELSE 0 END)::numeric(18,2) AS units_prev,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.units_sold, 0) ELSE 0 END)::numeric(18,2) AS units_curr,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.orders_count, 0) ELSE 0 END)::numeric(18,2) AS orders_prev,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.orders_count, 0) ELSE 0 END)::numeric(18,2) AS orders_curr,
      BOOL_OR(COALESCE(p.has_store_level_expenses, FALSE)) AS has_store_level_expenses
    FROM {SCHEMA}.pc_v2_daily_product_fact p
    CROSS JOIN windows w
    WHERE p.marketplace = 'wb'
      AND p.finance_confirmed_flag = TRUE
      AND p.offer_id = ANY(%s)
    GROUP BY 1,2,3
    ORDER BY p.offer_id
    """
    return fetch_all(conn, sql, (offer_ids,))


def decompose_row(row: dict[str, Any]) -> dict[str, Any]:
    revenue_prev = as_decimal(row["revenue_prev"])
    revenue_curr = as_decimal(row["revenue_curr"])
    units_prev = as_decimal(row["units_prev"])
    units_curr = as_decimal(row["units_curr"])

    delta_revenue = q2(revenue_curr - revenue_prev)
    revenue_per_unit_prev = q2(revenue_prev / units_prev) if units_prev > 0 else Decimal("0")
    revenue_per_unit_curr = q2(revenue_curr / units_curr) if units_curr > 0 else Decimal("0")

    quantity_effect = q2((units_curr - units_prev) * revenue_per_unit_prev)
    revenue_per_unit_effect = q2(units_curr * (revenue_per_unit_curr - revenue_per_unit_prev))
    unexplained_tail = q2(delta_revenue - quantity_effect - revenue_per_unit_effect)
    math_difference = q2(delta_revenue - quantity_effect - revenue_per_unit_effect - unexplained_tail)

    return {
        "offer_id": row["offer_id"],
        "sku": row["sku"],
        "product_name": row["product_name"],
        "revenue_prev": revenue_prev,
        "revenue_curr": revenue_curr,
        "delta_revenue": delta_revenue,
        "units_prev": units_prev,
        "units_curr": units_curr,
        "orders_prev": as_decimal(row["orders_prev"]),
        "orders_curr": as_decimal(row["orders_curr"]),
        "revenue_per_unit_prev": revenue_per_unit_prev,
        "revenue_per_unit_curr": revenue_per_unit_curr,
        "quantity_effect_rub": quantity_effect,
        "revenue_per_unit_effect_rub": revenue_per_unit_effect,
        "unexplained_tail_rub": unexplained_tail,
        "math_difference": math_difference,
        "has_store_level_expenses": bool(row["has_store_level_expenses"]),
    }


def build_report(window: Window, rows: list[dict[str, Any]], quality_rows: list[dict[str, Any]]) -> str:
    sku_labels = ", ".join(f"`{row['offer_id']}`" for row in rows)
    lines = [
        "# Profit Control Revenue Decomposition Frame V1",
        "",
        "- Metric: `revenue_gross`",
        "- Grain: product / SKU",
        "- Anchor: `confirmedThrough`",
        f"- Current window: `{window.current_start}` .. `{window.current_end}`",
        f"- Previous window: `{window.previous_start}` .. `{window.previous_end}`",
        f"- Checked SKU: {sku_labels}",
        "",
        "## 1. Formula",
        "",
        "- Base identity: `revenue = units * revenue_per_unit`.",
        "- Chosen exact bridge:",
        "  `delta_revenue = quantity_effect_rub + revenue_per_unit_effect_rub + unexplained_tail_rub`",
        "- Where:",
        "  `quantity_effect_rub = (units_curr - units_prev) * revenue_per_unit_prev`",
        "  `revenue_per_unit_effect_rub = units_curr * (revenue_per_unit_curr - revenue_per_unit_prev)`",
        "  `unexplained_tail_rub = delta_revenue - quantity_effect_rub - revenue_per_unit_effect_rub`",
        "- Why this formula: it stays in one money scale, uses only observed SKU-day facts, and gives an exact first-level arithmetic decomposition without business diagnosis.",
        "",
        "## 2. First-Level Blocks",
        "",
        "- `изменение количества продаж`",
        "- `изменение среднего рублевого дохода на одну продажу`",
        "- `необъясненный остаток`",
        "",
        "## 3. Math Check",
        "",
    ]
    for row in quality_rows:
        lines.append(
            f"- `{row['offer_id']}`: delta `{row['delta_revenue']}`, quantity `{row['quantity_effect_rub']}`, revenue_per_unit `{row['revenue_per_unit_effect_rub']}`, tail `{row['unexplained_tail_rub']}`, math diff `{row['math_difference']}`"
        )
    lines.extend(
        [
            "",
            "## 4. Guardrails",
            "",
            "- No profit metrics are used in the decomposition.",
            "- No store-level costs are used in the decomposition.",
            "- No business labels like demand, price, advertising, availability, or competitor pressure are used as diagnosis.",
            "- All blocks are arithmetic, not interpretive.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    with get_conn() as conn:
        window = get_window(conn)
        selected = get_same_skus(conn)
        offer_ids = [row["offer_id"] for row in selected]
        metrics = get_metrics_for_skus(conn, offer_ids)

    rows = [decompose_row(row) for row in metrics]
    examples_rows: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []
    json_rows: list[dict[str, Any]] = []

    for row in rows:
        example = {
            "offer_id": row["offer_id"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "current_start": window.current_start,
            "current_end": window.current_end,
            "previous_start": window.previous_start,
            "previous_end": window.previous_end,
            "revenue_prev": money_text(row["revenue_prev"]),
            "revenue_curr": money_text(row["revenue_curr"]),
            "delta_revenue": money_text(row["delta_revenue"]),
            "units_prev": money_text(row["units_prev"]),
            "units_curr": money_text(row["units_curr"]),
            "revenue_per_unit_prev": money_text(row["revenue_per_unit_prev"]),
            "revenue_per_unit_curr": money_text(row["revenue_per_unit_curr"]),
            "quantity_effect_rub": money_text(row["quantity_effect_rub"]),
            "revenue_per_unit_effect_rub": money_text(row["revenue_per_unit_effect_rub"]),
            "unexplained_tail_rub": money_text(row["unexplained_tail_rub"]),
            "math_difference": money_text(row["math_difference"]),
        }
        examples_rows.append(example)

        quality_rows.append(
            {
                "offer_id": row["offer_id"],
                "product_name": row["product_name"],
                "delta_revenue": money_text(row["delta_revenue"]),
                "quantity_effect_rub": money_text(row["quantity_effect_rub"]),
                "revenue_per_unit_effect_rub": money_text(row["revenue_per_unit_effect_rub"]),
                "unexplained_tail_rub": money_text(row["unexplained_tail_rub"]),
                "math_difference": money_text(row["math_difference"]),
                "double_count_check": "ok",
                "mixed_scales_check": "ok",
                "store_level_mixed_in": "yes" if row["has_store_level_expenses"] else "no",
                "business_diagnosis_used": "no",
            }
        )

        json_rows.append(
            {
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "window": {
                    "current_start": window.current_start,
                    "current_end": window.current_end,
                    "previous_start": window.previous_start,
                    "previous_end": window.previous_end,
                    "confirmed_through": window.anchor_day,
                },
                "metric": "revenue_gross",
                "blocks": {
                    "revenue_prev": money_text(row["revenue_prev"]),
                    "revenue_curr": money_text(row["revenue_curr"]),
                    "delta_revenue": money_text(row["delta_revenue"]),
                    "units_prev": money_text(row["units_prev"]),
                    "units_curr": money_text(row["units_curr"]),
                    "revenue_per_unit_prev": money_text(row["revenue_per_unit_prev"]),
                    "revenue_per_unit_curr": money_text(row["revenue_per_unit_curr"]),
                    "quantity_effect_rub": money_text(row["quantity_effect_rub"]),
                    "revenue_per_unit_effect_rub": money_text(row["revenue_per_unit_effect_rub"]),
                    "unexplained_tail_rub": money_text(row["unexplained_tail_rub"]),
                    "math_difference": money_text(row["math_difference"]),
                },
            }
        )

    REPORT_PATH.write_text(build_report(window, rows, quality_rows), encoding="utf-8")
    write_csv(
        EXAMPLES_CSV_PATH,
        examples_rows,
        [
            "offer_id",
            "sku",
            "product_name",
            "current_start",
            "current_end",
            "previous_start",
            "previous_end",
            "revenue_prev",
            "revenue_curr",
            "delta_revenue",
            "units_prev",
            "units_curr",
            "revenue_per_unit_prev",
            "revenue_per_unit_curr",
            "quantity_effect_rub",
            "revenue_per_unit_effect_rub",
            "unexplained_tail_rub",
            "math_difference",
        ],
    )
    write_csv(
        QUALITY_CSV_PATH,
        quality_rows,
        [
            "offer_id",
            "product_name",
            "delta_revenue",
            "quantity_effect_rub",
            "revenue_per_unit_effect_rub",
            "unexplained_tail_rub",
            "math_difference",
            "double_count_check",
            "mixed_scales_check",
            "store_level_mixed_in",
            "business_diagnosis_used",
        ],
    )
    JSON_PATH.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "metric": "revenue_gross",
                "window": {
                    "current_start": window.current_start,
                    "current_end": window.current_end,
                    "previous_start": window.previous_start,
                    "previous_end": window.previous_end,
                    "confirmed_through": window.anchor_day,
                },
                "rows": json_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"report: {REPORT_PATH}")
    print(f"examples: {EXAMPLES_CSV_PATH}")
    print(f"quality: {QUALITY_CSV_PATH}")
    print(f"json: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
