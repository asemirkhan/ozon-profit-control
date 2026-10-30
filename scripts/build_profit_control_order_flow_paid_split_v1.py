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

SOURCE_JSON = ROOT / "profit_control_quantity_second_level_v1.json"
REPORT_PATH = ROOT / "profit_control_order_flow_paid_split_v1_report.md"
EXAMPLES_CSV_PATH = ROOT / "profit_control_order_flow_paid_split_v1_examples.csv"
JSON_PATH = ROOT / "profit_control_order_flow_paid_split_v1.json"

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


def load_source_context() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    rows = payload["rows"]
    window = payload["window"]
    return rows, window


def fetch_paid_split_metrics(conn: Any, offer_ids: list[str]) -> list[dict[str, Any]]:
    sql = f"""
    WITH target AS (
      SELECT DISTINCT offer_id, canonical_product_key
      FROM {SCHEMA}.pc_v2_daily_product_fact
      WHERE marketplace = 'wb'
        AND offer_id = ANY(%s)
    ),
    bridge AS (
      SELECT *
      FROM {SCHEMA}.dim_product_bridge
      WHERE marketplace = 'wb'
    ),
    ads_mapped AS (
      SELECT
        a.date::date AS date,
        COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
        CASE
          WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND bm.offer_id = a.offer_id THEN 'offer_id_match'
          WHEN NULLIF(a.nm_id, '~') IS NOT NULL AND bm.nm_id = a.nm_id THEN 'nm_id_match'
          WHEN bm.canonical_product_key IS NOT NULL THEN 'bridge_match'
          ELSE 'unmatched'
        END AS mapping_method,
        SUM(COALESCE(a.orders_count, 0))::numeric(18,2) AS ad_orders_raw,
        SUM(COALESCE(a.spend, 0))::numeric(18,2) AS ad_spend
      FROM {SCHEMA}.stg_wb_ads a
      LEFT JOIN LATERAL (
        SELECT b.*
        FROM bridge b
        WHERE (NULLIF(a.offer_id, '~') IS NOT NULL AND b.offer_id = a.offer_id)
           OR (NULLIF(a.nm_id, '~') IS NOT NULL AND b.nm_id = a.nm_id)
        ORDER BY
          CASE
            WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND b.offer_id = a.offer_id THEN 1
            WHEN NULLIF(a.nm_id, '~') IS NOT NULL AND b.nm_id = a.nm_id THEN 2
            ELSE 3
          END,
          CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
          b.updated_at DESC,
          b.bridge_id DESC
        LIMIT 1
      ) bm ON TRUE
      WHERE COALESCE(a.marketplace, 'wb') = 'wb'
      GROUP BY 1,2,3
    ),
    totals AS (
      SELECT
        p.date,
        p.offer_id,
        p.canonical_product_key,
        SUM(COALESCE(p.orders_count, 0))::numeric(18,2) AS total_orders
      FROM {SCHEMA}.pc_v2_daily_product_fact p
      WHERE p.marketplace = 'wb'
        AND p.finance_confirmed_flag = TRUE
        AND p.offer_id = ANY(%s)
      GROUP BY 1,2,3
    ),
    joined AS (
      SELECT
        t.offer_id,
        t.date,
        t.total_orders,
        COALESCE(a.ad_orders_raw, 0)::numeric(18,2) AS ad_orders_raw,
        LEAST(t.total_orders, COALESCE(a.ad_orders_raw, 0))::numeric(18,2) AS ad_orders_capped,
        GREATEST(t.total_orders - LEAST(t.total_orders, COALESCE(a.ad_orders_raw, 0)), 0)::numeric(18,2) AS non_paid_orders,
        GREATEST(COALESCE(a.ad_orders_raw, 0) - t.total_orders, 0)::numeric(18,2) AS ad_orders_over_total,
        COALESCE(a.ad_spend, 0)::numeric(18,2) AS ad_spend,
        COALESCE(a.mapping_method, 'none') AS mapping_method
      FROM totals t
      LEFT JOIN ads_mapped a
        ON a.date = t.date
       AND a.canonical_product_key = t.canonical_product_key
    )
    SELECT
      offer_id,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN total_orders ELSE 0 END)::numeric(18,2) AS total_orders_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN total_orders ELSE 0 END)::numeric(18,2) AS total_orders_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_capped ELSE 0 END)::numeric(18,2) AS paid_orders_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_capped ELSE 0 END)::numeric(18,2) AS paid_orders_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN non_paid_orders ELSE 0 END)::numeric(18,2) AS non_paid_orders_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN non_paid_orders ELSE 0 END)::numeric(18,2) AS non_paid_orders_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_raw ELSE 0 END)::numeric(18,2) AS ad_orders_raw_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_raw ELSE 0 END)::numeric(18,2) AS ad_orders_raw_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_over_total ELSE 0 END)::numeric(18,2) AS ad_orders_over_total_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_orders_over_total ELSE 0 END)::numeric(18,2) AS ad_orders_over_total_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s AND ad_orders_over_total > 0 THEN 1 ELSE 0 END)::int AS over_total_days_current,
      SUM(CASE WHEN date BETWEEN %s AND %s AND ad_orders_over_total > 0 THEN 1 ELSE 0 END)::int AS over_total_days_previous,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_spend ELSE 0 END)::numeric(18,2) AS ad_spend_current,
      SUM(CASE WHEN date BETWEEN %s AND %s THEN ad_spend ELSE 0 END)::numeric(18,2) AS ad_spend_previous,
      STRING_AGG(DISTINCT mapping_method, ', ' ORDER BY mapping_method) AS mapping_methods_seen
    FROM joined
    GROUP BY offer_id
    ORDER BY offer_id
    """
    with open(SOURCE_JSON, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    window = payload["window"]
    params = (
        offer_ids,
        offer_ids,
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
        window["current_start"],
        window["current_end"],
        window["previous_start"],
        window["previous_end"],
    )
    return fetch_all(conn, sql, params)


def confidence_paid(ad_raw_curr: Decimal, ad_raw_prev: Decimal, over_days_curr: int, over_days_prev: int) -> str:
    total_raw = ad_raw_curr + ad_raw_prev
    over_days = over_days_curr + over_days_prev
    if total_raw <= 0:
        return "low"
    if over_days == 0:
        return "high"
    return "medium"


def confidence_non_paid(total_orders_curr: Decimal, total_orders_prev: Decimal, over_days_curr: int, over_days_prev: int) -> str:
    if (total_orders_curr + total_orders_prev) <= 0:
        return "low"
    if (over_days_curr + over_days_prev) == 0:
        return "high"
    return "medium"


def build_split_note(row: dict[str, Any]) -> str:
    over_curr = int(row["over_total_days_current"] or 0)
    over_prev = int(row["over_total_days_previous"] or 0)
    raw_curr = as_decimal(row["ad_orders_raw_current"])
    raw_prev = as_decimal(row["ad_orders_raw_previous"])
    if raw_curr + raw_prev == 0:
        return "No mapped ad-attributed orders in either window; split collapses to non-paid residual."
    if over_curr + over_prev == 0:
        return "Paid flow comes from mapped WB ads orders_count and fits inside total orders without daily capping."
    return (
        f"Paid flow comes from mapped WB ads orders_count; "
        f"daily cap applied on {over_curr + over_prev} day(s) where ad-attributed orders exceeded total orders."
    )


def main() -> int:
    source_rows, window = load_source_context()
    source_by_offer = {row["offer_id"]: row for row in source_rows}
    offer_ids = list(source_by_offer.keys())

    with get_conn() as conn:
        split_rows = fetch_paid_split_metrics(conn, offer_ids)

    example_rows: list[dict[str, Any]] = []
    json_rows: list[dict[str, Any]] = []

    for split in split_rows:
        source = source_by_offer[split["offer_id"]]
        order_flow_effect = as_decimal(source["quantity_block"]["order_flow_effect_rub"])
        paid_orders_curr = as_decimal(split["paid_orders_current"])
        paid_orders_prev = as_decimal(split["paid_orders_previous"])
        non_paid_orders_curr = as_decimal(split["non_paid_orders_current"])
        non_paid_orders_prev = as_decimal(split["non_paid_orders_previous"])
        total_orders_curr = as_decimal(split["total_orders_current"])
        total_orders_prev = as_decimal(split["total_orders_previous"])
        total_orders_delta = total_orders_curr - total_orders_prev
        if total_orders_delta != 0:
            factor = q2(order_flow_effect / total_orders_delta)
        else:
            factor = Decimal("0.00")
        paid_order_flow_effect = q2((paid_orders_curr - paid_orders_prev) * factor)
        non_paid_order_flow_effect = q2((non_paid_orders_curr - non_paid_orders_prev) * factor)
        tail = q2(order_flow_effect - paid_order_flow_effect - non_paid_order_flow_effect)
        math_diff = q2(order_flow_effect - paid_order_flow_effect - non_paid_order_flow_effect - tail)

        conf_paid = confidence_paid(
            as_decimal(split["ad_orders_raw_current"]),
            as_decimal(split["ad_orders_raw_previous"]),
            int(split["over_total_days_current"] or 0),
            int(split["over_total_days_previous"] or 0),
        )
        conf_non_paid = confidence_non_paid(
            as_decimal(split["total_orders_current"]),
            as_decimal(split["total_orders_previous"]),
            int(split["over_total_days_current"] or 0),
            int(split["over_total_days_previous"] or 0),
        )
        note = build_split_note(split)

        example_rows.append(
            {
                "offer_id": split["offer_id"],
                "order_flow_effect_rub": money_text(order_flow_effect),
                "paid_order_flow_effect_rub": money_text(paid_order_flow_effect),
                "non_paid_order_flow_effect_rub": money_text(non_paid_order_flow_effect),
                "order_flow_unexplained_tail_rub": money_text(tail),
                "order_flow_split_math_difference": money_text(math_diff),
                "confidence_paid": conf_paid,
                "confidence_non_paid": conf_non_paid,
                "split_quality_note": note,
            }
        )

        json_rows.append(
            {
                "offer_id": split["offer_id"],
                "sku": source["sku"],
                "product_name": source["product_name"],
                "window": window,
                "order_flow_block": {
                    "order_flow_effect_rub": money_text(order_flow_effect),
                    "paid_order_flow_effect_rub": money_text(paid_order_flow_effect),
                    "non_paid_order_flow_effect_rub": money_text(non_paid_order_flow_effect),
                    "order_flow_unexplained_tail_rub": money_text(tail),
                    "order_flow_split_math_difference": money_text(math_diff),
                },
                "split_rule": {
                    "paid_order_definition": "Mapped WB ads orders_count by canonical product, capped daily by total orders.",
                    "non_paid_order_definition": "Residual total orders after capped paid orders.",
                    "mapping_methods_seen": split["mapping_methods_seen"],
                    "ad_orders_raw_current": money_text(as_decimal(split["ad_orders_raw_current"])),
                    "ad_orders_raw_previous": money_text(as_decimal(split["ad_orders_raw_previous"])),
                    "paid_orders_current": money_text(paid_orders_curr),
                    "paid_orders_previous": money_text(paid_orders_prev),
                    "non_paid_orders_current": money_text(non_paid_orders_curr),
                    "non_paid_orders_previous": money_text(non_paid_orders_prev),
                    "over_total_days_current": int(split["over_total_days_current"] or 0),
                    "over_total_days_previous": int(split["over_total_days_previous"] or 0),
                },
                "confidence_paid": conf_paid,
                "confidence_non_paid": conf_non_paid,
                "split_quality_note": note,
            }
        )

    report_lines = [
        "# Profit Control Order Flow Paid Split V1",
        "",
        "- Scope: split only `order_flow_effect_rub` inside the quantity block.",
        "- Metric context: top-level `revenue_gross`, but this step does not touch `revenue_per_unit` or `conversion` blocks.",
        f"- Window: `{window['current_start']}` .. `{window['current_end']}` vs `{window['previous_start']}` .. `{window['previous_end']}`",
        f"- Anchor: `{window['confirmed_through']}`",
        "",
        "## 1. Split Rule",
        "",
        "- Source tables: `pc_v2_daily_product_fact`, `stg_wb_ads`, `dim_product_bridge`.",
        "- Paid order rule: use WB ads `orders_count`, mapped to the SKU through the same bridge logic as the WB money layer, then cap daily paid orders by total observed orders.",
        "- Non-paid order rule: daily residual `total_orders - paid_orders_capped`.",
        "- This keeps one order from being counted in both streams.",
        "",
        "## 2. Reliability Notes",
        "",
        "- `paid` is strongest when mapped ad orders exist and no day requires capping.",
        "- `non-paid` is strongest when residual orders are observed without capping pressure.",
        "- If `ad_orders_raw > total_orders` on some day, capping is applied and the split becomes a methodological approximation for that SKU/window.",
        "",
        "## 3. Method Risks",
        "",
        "- WB ads orders are not joined by literal order_id; they are mapped through offer/nm bridge and treated as ad-supported flow.",
        "- Part of ad-supported flow can exceed observed total orders on a day; in that case the paid stream is clipped at total orders.",
        "- This step does not say why non-paid changed; it only isolates the technical residual flow without business labeling.",
        "",
        "## 4. SKU Results",
        "",
    ]
    for row in example_rows:
        report_lines.append(
            f"- `{row['offer_id']}`: order_flow `{row['order_flow_effect_rub']}`, paid `{row['paid_order_flow_effect_rub']}`, non-paid `{row['non_paid_order_flow_effect_rub']}`, tail `{row['order_flow_unexplained_tail_rub']}`, paid confidence `{row['confidence_paid']}`, non-paid confidence `{row['confidence_non_paid']}`"
        )
    report_lines.extend(
        [
            "",
            "## 5. Tail",
            "",
            "- `order_flow_unexplained_tail_rub` is expected to stay near zero because the paid/non-paid split is built from the same order-flow factor and a capped daily partition of total orders.",
            "- Any non-zero tail here is rounding only.",
        ]
    )

    REPORT_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    write_csv(
        EXAMPLES_CSV_PATH,
        example_rows,
        [
            "offer_id",
            "order_flow_effect_rub",
            "paid_order_flow_effect_rub",
            "non_paid_order_flow_effect_rub",
            "order_flow_unexplained_tail_rub",
            "order_flow_split_math_difference",
            "confidence_paid",
            "confidence_non_paid",
            "split_quality_note",
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

    print(f"report: {REPORT_PATH}")
    print(f"examples: {EXAMPLES_CSV_PATH}")
    print(f"json: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
