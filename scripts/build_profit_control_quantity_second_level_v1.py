#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import sys
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

SOURCE_FRAME_JSON = ROOT / "profit_control_revenue_decomposition_frame_v1.json"
REPORT_PATH = ROOT / "profit_control_quantity_second_level_v1_report.md"
EXAMPLES_CSV_PATH = ROOT / "profit_control_quantity_second_level_v1_examples.csv"
JSON_PATH = ROOT / "profit_control_quantity_second_level_v1.json"

TWOPLACES = Decimal("0.01")


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


def load_selected_offer_ids() -> tuple[list[str], dict[str, Any]]:
    payload = json.loads(SOURCE_FRAME_JSON.read_text(encoding="utf-8"))
    offer_ids = [row["offer_id"] for row in payload["rows"]]
    return offer_ids, payload["window"]


def fetch_metrics(conn: Any, offer_ids: list[str]) -> list[dict[str, Any]]:
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
    ),
    base AS (
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
        SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.sales_count, 0) ELSE 0 END)::numeric(18,2) AS sales_prev,
        SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.sales_count, 0) ELSE 0 END)::numeric(18,2) AS sales_curr
      FROM {SCHEMA}.pc_v2_daily_product_fact p
      CROSS JOIN windows w
      WHERE p.marketplace = 'wb'
        AND p.finance_confirmed_flag = TRUE
        AND p.offer_id = ANY(%s)
      GROUP BY 1,2,3
    ),
    inventory AS (
      SELECT
        i.offer_id,
        COUNT(*) FILTER (WHERE i.snapshot_date BETWEEN w.current_start AND w.current_end) AS availability_snapshot_days_current,
        COUNT(*) FILTER (WHERE i.snapshot_date BETWEEN w.previous_start AND w.previous_end) AS availability_snapshot_days_previous,
        COUNT(*) FILTER (WHERE i.snapshot_date BETWEEN w.current_start AND w.current_end AND COALESCE(i.stock_available, 0) <= 0) AS availability_oos_days_current,
        COUNT(*) FILTER (WHERE i.snapshot_date BETWEEN w.previous_start AND w.previous_end AND COALESCE(i.stock_available, 0) <= 0) AS availability_oos_days_previous
      FROM {SCHEMA}.fact_inventory_snapshot i
      CROSS JOIN windows w
      WHERE i.marketplace = 'wb'
        AND i.offer_id = ANY(%s)
      GROUP BY 1
    )
    SELECT
      b.*,
      COALESCE(inv.availability_snapshot_days_current, 0) AS availability_snapshot_days_current,
      COALESCE(inv.availability_snapshot_days_previous, 0) AS availability_snapshot_days_previous,
      COALESCE(inv.availability_oos_days_current, 0) AS availability_oos_days_current,
      COALESCE(inv.availability_oos_days_previous, 0) AS availability_oos_days_previous
    FROM base b
    LEFT JOIN inventory inv
      ON inv.offer_id = b.offer_id
    ORDER BY b.offer_id
    """
    return fetch_all(conn, sql, (offer_ids, offer_ids))


def availability_confidence(current_snapshots: int, previous_snapshots: int, current_oos: int, previous_oos: int) -> tuple[str, str]:
    min_snapshots = min(current_snapshots, previous_snapshots)
    if current_oos > previous_oos and min_snapshots >= 14:
        return "high", "availability_effect_confirmed"
    if current_oos > previous_oos and min_snapshots >= 7:
        return "medium", "availability_effect_weak"
    if current_oos > previous_oos and (current_snapshots + previous_snapshots) > 0:
        return "low", "availability_effect_weak"
    if (current_snapshots + previous_snapshots) == 0:
        return "low", "availability_effect_not_proven"
    return "low", "availability_effect_not_proven"


def decompose_row(row: dict[str, Any]) -> dict[str, Any]:
    revenue_prev = as_decimal(row["revenue_prev"])
    units_prev = as_decimal(row["units_prev"])
    units_curr = as_decimal(row["units_curr"])
    orders_prev = as_decimal(row["orders_prev"])
    orders_curr = as_decimal(row["orders_curr"])
    sales_prev = as_decimal(row["sales_prev"])
    sales_curr = as_decimal(row["sales_curr"])

    revenue_per_unit_prev = q2(revenue_prev / units_prev) if units_prev > 0 else Decimal("0")
    quantity_effect_rub = q2((units_curr - units_prev) * revenue_per_unit_prev)

    conversion_prev = q2(sales_prev / orders_prev) if orders_prev > 0 else Decimal("0")
    conversion_curr = q2(sales_curr / orders_curr) if orders_curr > 0 else Decimal("0")
    units_per_sale_prev = q2(units_prev / sales_prev) if sales_prev > 0 else Decimal("0")

    gross_order_flow_effect = q2((orders_curr - orders_prev) * conversion_prev * units_per_sale_prev * revenue_per_unit_prev)
    order_to_sale_conversion_effect = q2(orders_curr * (conversion_curr - conversion_prev) * units_per_sale_prev * revenue_per_unit_prev)

    current_snapshots = int(row["availability_snapshot_days_current"] or 0)
    previous_snapshots = int(row["availability_snapshot_days_previous"] or 0)
    current_oos = int(row["availability_oos_days_current"] or 0)
    previous_oos = int(row["availability_oos_days_previous"] or 0)
    availability_conf, availability_status = availability_confidence(current_snapshots, previous_snapshots, current_oos, previous_oos)

    if availability_status == "availability_effect_confirmed":
        incremental_oos = max(current_oos - previous_oos, 0)
        prev_orders_per_day = q2(orders_prev / Decimal("28"))
        availability_raw = q2(Decimal(incremental_oos) * prev_orders_per_day * conversion_prev * units_per_sale_prev * revenue_per_unit_prev)
        availability_effect = q2(-min(abs(gross_order_flow_effect), abs(availability_raw)))
    else:
        availability_effect = Decimal("0.00")

    order_flow_effect = q2(gross_order_flow_effect - availability_effect)
    quantity_unexplained_tail = q2(quantity_effect_rub - order_flow_effect - order_to_sale_conversion_effect - availability_effect)
    quantity_math_difference = q2(quantity_effect_rub - order_flow_effect - order_to_sale_conversion_effect - availability_effect - quantity_unexplained_tail)

    if orders_curr != orders_prev:
        confidence_order_flow = "high"
    else:
        confidence_order_flow = "medium"

    if orders_prev > 0 and orders_curr > 0:
        confidence_conversion = "high"
    elif orders_prev > 0 or orders_curr > 0:
        confidence_conversion = "medium"
    else:
        confidence_conversion = "low"

    return {
        "offer_id": row["offer_id"],
        "sku": row["sku"],
        "product_name": row["product_name"],
        "revenue_prev": as_decimal(row["revenue_prev"]),
        "revenue_curr": as_decimal(row["revenue_curr"]),
        "units_prev": units_prev,
        "units_curr": units_curr,
        "orders_prev": orders_prev,
        "orders_curr": orders_curr,
        "sales_prev": sales_prev,
        "sales_curr": sales_curr,
        "revenue_per_unit_prev": revenue_per_unit_prev,
        "quantity_effect_rub": quantity_effect_rub,
        "order_flow_effect_rub": order_flow_effect,
        "order_to_sale_conversion_effect_rub": order_to_sale_conversion_effect,
        "availability_effect_rub": availability_effect,
        "quantity_unexplained_tail_rub": quantity_unexplained_tail,
        "quantity_math_difference": quantity_math_difference,
        "confidence_order_flow": confidence_order_flow,
        "confidence_conversion": confidence_conversion,
        "confidence_availability": availability_conf,
        "availability_status": availability_status,
        "availability_snapshot_days_current": current_snapshots,
        "availability_snapshot_days_previous": previous_snapshots,
        "availability_oos_days_current": current_oos,
        "availability_oos_days_previous": previous_oos,
        "conversion_prev": conversion_prev,
        "conversion_curr": conversion_curr,
        "units_per_sale_prev": units_per_sale_prev,
    }


def build_report(window: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    selected = ", ".join(f"`{row['offer_id']}`" for row in rows)
    lines = [
        "# Profit Control Quantity Second Level V1",
        "",
        "- Source block: `quantity_effect_rub` from revenue decomposition frame",
        "- Metric context: `revenue_gross`, but this step only decomposes the quantity block",
        "- Grain: product / SKU",
        f"- Current window: `{window['current_start']}` .. `{window['current_end']}`",
        f"- Previous window: `{window['previous_start']}` .. `{window['previous_end']}`",
        f"- Anchor: `{window['confirmed_through']}`",
        f"- Checked SKU: {selected}",
        "",
        "## 1. Formula",
        "",
        "- Operational identity used here: `units_sold ~= orders_count * (sales_count / orders_count) * units_per_sale_prev`.",
        "- Second-level bridge in rubles:",
        "  `quantity_effect_rub = order_flow_effect_rub + order_to_sale_conversion_effect_rub + availability_effect_rub + quantity_unexplained_tail_rub`",
        "- Fields used:",
        "  - `orders_count` from `pc_v2_daily_product_fact` for order-flow",
        "  - `sales_count` and `orders_count` from `pc_v2_daily_product_fact` for conversion proxy `sales_count / orders_count`",
        "  - `fact_inventory_snapshot.stock_available` for availability signal",
        "  - `revenue_per_unit_prev` from the first-level frame to keep one money scale",
        "",
        "## 2. Availability Rule",
        "",
        "- `availability_effect` is only filled when current out-of-stock days exceed previous out-of-stock days and snapshot density is strong enough.",
        "- If snapshots are sparse, the branch is explicitly marked `not_proven` or `weak` and stays at `0 ₽`.",
        "",
        "## 3. Confidence By Branch",
        "",
    ]
    for row in rows:
        lines.append(
            f"- `{row['offer_id']}`: order_flow `{row['confidence_order_flow']}`, conversion `{row['confidence_conversion']}`, availability `{row['confidence_availability']}` / `{row['availability_status']}`"
        )
    lines.extend(
        [
            "",
            "## 4. Tail Notes",
            "",
            "- `quantity_unexplained_tail_rub` keeps only the residual inside the quantity block after order-flow, conversion, and any proven availability allocation.",
            "- In these examples the tail mostly captures rounding and any leftover movement not covered by the sparse availability signal.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    offer_ids, window = load_selected_offer_ids()
    with get_conn() as conn:
        raw_rows = fetch_metrics(conn, offer_ids)

    rows = [decompose_row(row) for row in raw_rows]

    example_rows: list[dict[str, Any]] = []
    json_rows: list[dict[str, Any]] = []
    for row in rows:
        example_rows.append(
            {
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "quantity_effect_rub": money_text(row["quantity_effect_rub"]),
                "order_flow_effect_rub": money_text(row["order_flow_effect_rub"]),
                "order_to_sale_conversion_effect_rub": money_text(row["order_to_sale_conversion_effect_rub"]),
                "availability_effect_rub": money_text(row["availability_effect_rub"]),
                "quantity_unexplained_tail_rub": money_text(row["quantity_unexplained_tail_rub"]),
                "quantity_math_difference": money_text(row["quantity_math_difference"]),
                "confidence_order_flow": row["confidence_order_flow"],
                "confidence_conversion": row["confidence_conversion"],
                "confidence_availability": row["confidence_availability"],
            }
        )
        json_rows.append(
            {
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "window": window,
                "quantity_block": {
                    "quantity_effect_rub": money_text(row["quantity_effect_rub"]),
                    "order_flow_effect_rub": money_text(row["order_flow_effect_rub"]),
                    "order_to_sale_conversion_effect_rub": money_text(row["order_to_sale_conversion_effect_rub"]),
                    "availability_effect_rub": money_text(row["availability_effect_rub"]),
                    "quantity_unexplained_tail_rub": money_text(row["quantity_unexplained_tail_rub"]),
                    "quantity_math_difference": money_text(row["quantity_math_difference"]),
                },
                "operational_fields": {
                    "orders_prev": money_text(row["orders_prev"]),
                    "orders_curr": money_text(row["orders_curr"]),
                    "sales_prev": money_text(row["sales_prev"]),
                    "sales_curr": money_text(row["sales_curr"]),
                    "units_prev": money_text(row["units_prev"]),
                    "units_curr": money_text(row["units_curr"]),
                    "conversion_prev": money_text(row["conversion_prev"]),
                    "conversion_curr": money_text(row["conversion_curr"]),
                    "units_per_sale_prev": money_text(row["units_per_sale_prev"]),
                },
                "availability_signal": {
                    "status": row["availability_status"],
                    "confidence": row["confidence_availability"],
                    "snapshot_days_current": row["availability_snapshot_days_current"],
                    "snapshot_days_previous": row["availability_snapshot_days_previous"],
                    "oos_days_current": row["availability_oos_days_current"],
                    "oos_days_previous": row["availability_oos_days_previous"],
                },
            }
        )

    write_csv(
        EXAMPLES_CSV_PATH,
        example_rows,
        [
            "offer_id",
            "sku",
            "product_name",
            "quantity_effect_rub",
            "order_flow_effect_rub",
            "order_to_sale_conversion_effect_rub",
            "availability_effect_rub",
            "quantity_unexplained_tail_rub",
            "quantity_math_difference",
            "confidence_order_flow",
            "confidence_conversion",
            "confidence_availability",
        ],
    )
    JSON_PATH.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "window": window,
                "rows": json_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    REPORT_PATH.write_text(build_report(window, rows), encoding="utf-8")

    print(f"report: {REPORT_PATH}")
    print(f"examples: {EXAMPLES_CSV_PATH}")
    print(f"json: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
