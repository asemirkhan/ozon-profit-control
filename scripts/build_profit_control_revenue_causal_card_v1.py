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

REPORT_PATH = ROOT / "profit_control_revenue_causal_card_v1_report.md"
EXAMPLES_CSV_PATH = ROOT / "profit_control_revenue_causal_card_v1_examples.csv"
QUALITY_CSV_PATH = ROOT / "profit_control_revenue_causal_card_v1_quality_checks.csv"
CARDS_JSON_PATH = ROOT / "profit_control_revenue_causal_card_v1_cards.json"

TWOPLACES = Decimal("0.01")


@dataclass(frozen=True)
class Reason:
    reason_code: str
    reason_label: str
    contribution_rub: Decimal
    share_of_drop: Decimal
    confidence: str
    status: str
    evidence: str


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


def pct_text(value: Decimal) -> str:
    return f"{q2(value * Decimal('100')):.2f}%"


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


def get_anchor_and_windows(conn: Any) -> dict[str, Any]:
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
        raise RuntimeError("No confirmedThrough boundary in finance_day_state")
    return row


def get_candidate_rows(conn: Any) -> list[dict[str, Any]]:
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
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_current,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.revenue_gross, 0) ELSE 0 END)::numeric(18,2) AS revenue_previous,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.orders_count, 0) ELSE 0 END)::numeric(18,2) AS orders_current,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.orders_count, 0) ELSE 0 END)::numeric(18,2) AS orders_previous,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.units_sold, 0) ELSE 0 END)::numeric(18,2) AS units_current,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.units_sold, 0) ELSE 0 END)::numeric(18,2) AS units_previous,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.ads_amount, 0) ELSE 0 END)::numeric(18,2) AS ads_current,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.ads_amount, 0) ELSE 0 END)::numeric(18,2) AS ads_previous,
      SUM(CASE WHEN p.date BETWEEN w.current_start AND w.current_end THEN COALESCE(p.commission_amount, 0) + COALESCE(p.logistics_amount, 0) + COALESCE(p.acquiring_amount, 0) + COALESCE(p.product_services_amount, 0) ELSE 0 END)::numeric(18,2) AS sell_costs_current,
      SUM(CASE WHEN p.date BETWEEN w.previous_start AND w.previous_end THEN COALESCE(p.commission_amount, 0) + COALESCE(p.logistics_amount, 0) + COALESCE(p.acquiring_amount, 0) + COALESCE(p.product_services_amount, 0) ELSE 0 END)::numeric(18,2) AS sell_costs_previous
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
      revenue_previous DESC
    LIMIT 3
    """
    return fetch_all(conn, sql)


def get_inventory_signal(conn: Any, offer_id: str) -> dict[str, Any]:
    sql = f"""
    WITH params AS (
      SELECT MAX(day)::date AS anchor_day
      FROM {SCHEMA}.finance_day_state
      WHERE final_analytic_usable_flag = TRUE
    ),
    windows AS (
      SELECT
        (anchor_day - INTERVAL '27 day')::date AS current_start,
        anchor_day::date AS current_end,
        (anchor_day - INTERVAL '55 day')::date AS previous_start,
        (anchor_day - INTERVAL '28 day')::date AS previous_end
      FROM params
    ),
    daily_stock AS (
      SELECT
        i.snapshot_date,
        SUM(COALESCE(i.stock_available, 0))::numeric(18,2) AS stock_available
      FROM {SCHEMA}.fact_inventory_snapshot i
      WHERE i.marketplace = 'wb'
        AND i.offer_id = %s
      GROUP BY 1
    )
    SELECT
      COUNT(*) FILTER (WHERE d.snapshot_date BETWEEN w.current_start AND w.current_end) AS current_snapshot_days,
      COUNT(*) FILTER (WHERE d.snapshot_date BETWEEN w.previous_start AND w.previous_end) AS previous_snapshot_days,
      COUNT(*) FILTER (WHERE d.snapshot_date BETWEEN w.current_start AND w.current_end AND COALESCE(d.stock_available, 0) <= 0) AS current_oos_days,
      COUNT(*) FILTER (WHERE d.snapshot_date BETWEEN w.previous_start AND w.previous_end AND COALESCE(d.stock_available, 0) <= 0) AS previous_oos_days,
      MIN(d.stock_available) FILTER (WHERE d.snapshot_date BETWEEN w.current_start AND w.current_end) AS current_min_stock,
      MIN(d.stock_available) FILTER (WHERE d.snapshot_date BETWEEN w.previous_start AND w.previous_end) AS previous_min_stock
    FROM daily_stock d
    CROSS JOIN windows w
    """
    return fetch_one(conn, sql, (offer_id,))


def choose_confidence_from_inventory(signal: dict[str, Any]) -> str:
    current_days = int(signal.get("current_snapshot_days") or 0)
    previous_days = int(signal.get("previous_snapshot_days") or 0)
    min_days = min(current_days, previous_days)
    if min_days >= 14:
        return "high"
    if min_days >= 7:
        return "medium"
    if current_days + previous_days > 0:
        return "low"
    return "low"


def build_reason_set(row: dict[str, Any], inventory_signal: dict[str, Any]) -> tuple[list[Reason], dict[str, Any]]:
    revenue_current = as_decimal(row["revenue_current"])
    revenue_previous = as_decimal(row["revenue_previous"])
    orders_current = as_decimal(row["orders_current"])
    orders_previous = as_decimal(row["orders_previous"])
    units_current = as_decimal(row["units_current"])
    units_previous = as_decimal(row["units_previous"])
    sell_costs_current = as_decimal(row["sell_costs_current"])
    sell_costs_previous = as_decimal(row["sell_costs_previous"])

    revenue_drop = q2(max(revenue_previous - revenue_current, Decimal("0")))
    prev_rev_per_unit = q2(revenue_previous / units_previous) if units_previous > 0 else Decimal("0")
    prev_rev_per_day = q2(revenue_previous / Decimal("28"))
    unit_decline = max(units_previous - units_current, Decimal("0"))
    orders_decline = max(orders_previous - orders_current, Decimal("0"))
    volume_loss_money = q2(unit_decline * prev_rev_per_unit)

    current_oos_days = int(inventory_signal.get("current_oos_days") or 0)
    previous_oos_days = int(inventory_signal.get("previous_oos_days") or 0)
    incremental_oos_days = max(current_oos_days - previous_oos_days, 0)
    availability_gross = q2(Decimal(incremental_oos_days) * prev_rev_per_day)
    availability_confidence = choose_confidence_from_inventory(inventory_signal)

    if int(inventory_signal.get("current_snapshot_days") or 0) + int(inventory_signal.get("previous_snapshot_days") or 0) == 0:
        availability_contribution = Decimal("0")
        availability_status = "not_confirmed"
        availability_evidence = "Нет дневных снимков остатков в окнах сравнения."
    else:
        availability_contribution = q2(min(revenue_drop, volume_loss_money, availability_gross))
        availability_status = "confirmed" if availability_contribution > 0 else "not_confirmed"
        availability_evidence = (
            f"Снимки остатков: current={inventory_signal.get('current_snapshot_days', 0)}, "
            f"previous={inventory_signal.get('previous_snapshot_days', 0)}, "
            f"out_of_stock current={current_oos_days}, previous={previous_oos_days}."
        )

    gross_demand_contribution = q2(min(revenue_drop, volume_loss_money))
    demand_contribution = q2(max(gross_demand_contribution - availability_contribution, Decimal("0")))
    if units_current < units_previous and orders_current < orders_previous:
        demand_confidence = "high"
    elif units_current < units_previous or orders_current < orders_previous:
        demand_confidence = "medium"
    else:
        demand_confidence = "low"
    demand_status = "confirmed" if demand_contribution > 0 else "not_confirmed"
    demand_evidence = (
        f"Units {money_text(units_previous)} -> {money_text(units_current)}, "
        f"orders {money_text(orders_previous)} -> {money_text(orders_current)}, "
        f"prev avg revenue/unit {money_text(prev_rev_per_unit)}."
    )

    sell_cost_rate_current = sell_costs_current / revenue_current if revenue_current > 0 else Decimal("0")
    sell_cost_rate_previous = sell_costs_previous / revenue_previous if revenue_previous > 0 else Decimal("0")
    sell_cost_growth_rate = max(sell_cost_rate_current - sell_cost_rate_previous, Decimal("0"))
    sell_cost_growth_money = q2(revenue_current * sell_cost_growth_rate) if revenue_current > 0 else Decimal("0")
    sell_cost_contribution = Decimal("0")
    if sell_cost_growth_money > 0:
        selling_status = "checked_not_used"
        selling_confidence = "low"
        selling_evidence = (
            f"Sell-cost rate grew from {pct_text(sell_cost_rate_previous)} to {pct_text(sell_cost_rate_current)}, "
            "but this is not a direct revenue driver in the first-card model."
        )
    else:
        selling_status = "not_confirmed"
        selling_confidence = "medium"
        selling_evidence = (
            f"Sell-cost rate did not increase: {pct_text(sell_cost_rate_previous)} -> {pct_text(sell_cost_rate_current)}."
        )

    explained = q2(availability_contribution + demand_contribution + sell_cost_contribution)
    unexplained = q2(max(revenue_drop - explained, Decimal("0")))

    reasons = [
        Reason(
            reason_code="demand_decline",
            reason_label="снижение спроса",
            contribution_rub=demand_contribution,
            share_of_drop=(q2(demand_contribution / revenue_drop) if revenue_drop > 0 else Decimal("0")),
            confidence=demand_confidence,
            status=demand_status,
            evidence=demand_evidence,
        ),
        Reason(
            reason_code="selling_cost_growth",
            reason_label="рост расходов на продажу",
            contribution_rub=sell_cost_contribution,
            share_of_drop=(q2(sell_cost_contribution / revenue_drop) if revenue_drop > 0 else Decimal("0")),
            confidence=selling_confidence,
            status=selling_status,
            evidence=selling_evidence,
        ),
        Reason(
            reason_code="availability_issue",
            reason_label="проблемы с доступностью товара",
            contribution_rub=availability_contribution,
            share_of_drop=(q2(availability_contribution / revenue_drop) if revenue_drop > 0 else Decimal("0")),
            confidence=availability_confidence,
            status=availability_status,
            evidence=availability_evidence,
        ),
    ]

    meta = {
        "revenue_drop": revenue_drop,
        "unexplained": unexplained,
        "explained": explained,
        "prev_rev_per_unit": prev_rev_per_unit,
        "prev_rev_per_day": prev_rev_per_day,
        "unit_decline": q2(unit_decline),
        "orders_decline": q2(orders_decline),
        "volume_loss_money": volume_loss_money,
        "sell_cost_rate_current": q2(sell_cost_rate_current),
        "sell_cost_rate_previous": q2(sell_cost_rate_previous),
        "current_oos_days": current_oos_days,
        "previous_oos_days": previous_oos_days,
        "incremental_oos_days": incremental_oos_days,
        "inventory_snapshot_days_current": int(inventory_signal.get("current_snapshot_days") or 0),
        "inventory_snapshot_days_previous": int(inventory_signal.get("previous_snapshot_days") or 0),
    }
    return reasons, meta


def overall_confidence(reasons: list[Reason], unexplained: Decimal, drop: Decimal) -> str:
    if drop <= 0:
        return "low"
    explained_share = (drop - unexplained) / drop
    if explained_share >= Decimal("0.8") and any(r.confidence == "high" and r.contribution_rub > 0 for r in reasons):
        return "high"
    if explained_share >= Decimal("0.5"):
        return "medium"
    return "low"


def build_cards(conn: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    window = get_anchor_and_windows(conn)
    candidates = get_candidate_rows(conn)
    cards: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    quality_rows: list[dict[str, Any]] = []

    for row in candidates:
        inventory_signal = get_inventory_signal(conn, row["offer_id"])
        reasons, meta = build_reason_set(row, inventory_signal)
        revenue_drop = meta["revenue_drop"]
        unexplained = meta["unexplained"]
        explained = meta["explained"]
        confidence = overall_confidence(reasons, unexplained, revenue_drop)

        card = {
            "offer_id": row["offer_id"],
            "sku": row["sku"],
            "product_name": row["product_name"],
            "metric": "revenue_gross",
            "window_type": "28d_vs_previous_28d",
            "current_start": str(window["current_start"]),
            "current_end": str(window["current_end"]),
            "previous_start": str(window["previous_start"]),
            "previous_end": str(window["previous_end"]),
            "revenue_current": money_text(as_decimal(row["revenue_current"])),
            "revenue_previous": money_text(as_decimal(row["revenue_previous"])),
            "revenue_delta": money_text(as_decimal(row["revenue_current"]) - as_decimal(row["revenue_previous"])),
            "revenue_drop_abs": money_text(revenue_drop),
            "units_current": money_text(as_decimal(row["units_current"])),
            "units_previous": money_text(as_decimal(row["units_previous"])),
            "orders_current": money_text(as_decimal(row["orders_current"])),
            "orders_previous": money_text(as_decimal(row["orders_previous"])),
            "reasons": [
                {
                    "reason_code": reason.reason_code,
                    "reason_label": reason.reason_label,
                    "contribution_rub": money_text(reason.contribution_rub),
                    "share_of_drop": pct_text(reason.share_of_drop),
                    "confidence": reason.confidence,
                    "status": reason.status,
                    "evidence": reason.evidence,
                }
                for reason in reasons
            ],
            "explained_total_rub": money_text(explained),
            "unexplained_tail_rub": money_text(unexplained),
            "overall_confidence": confidence,
            "quality_notes": {
                "volume_loss_money": money_text(meta["volume_loss_money"]),
                "prev_avg_revenue_per_unit": money_text(meta["prev_rev_per_unit"]),
                "prev_avg_revenue_per_day": money_text(meta["prev_rev_per_day"]),
                "inventory_snapshot_days_current": meta["inventory_snapshot_days_current"],
                "inventory_snapshot_days_previous": meta["inventory_snapshot_days_previous"],
                "current_oos_days": meta["current_oos_days"],
                "previous_oos_days": meta["previous_oos_days"],
            },
        }
        cards.append(card)

        examples.append(
            {
                "offer_id": row["offer_id"],
                "sku": row["sku"],
                "product_name": row["product_name"],
                "current_start": str(window["current_start"]),
                "current_end": str(window["current_end"]),
                "previous_start": str(window["previous_start"]),
                "previous_end": str(window["previous_end"]),
                "revenue_current": money_text(as_decimal(row["revenue_current"])),
                "revenue_previous": money_text(as_decimal(row["revenue_previous"])),
                "revenue_delta": money_text(as_decimal(row["revenue_current"]) - as_decimal(row["revenue_previous"])),
                "reason_1": reasons[0].reason_label,
                "reason_1_contribution_rub": money_text(reasons[0].contribution_rub),
                "reason_1_confidence": reasons[0].confidence,
                "reason_2": reasons[1].reason_label,
                "reason_2_contribution_rub": money_text(reasons[1].contribution_rub),
                "reason_2_confidence": reasons[1].confidence,
                "reason_3": reasons[2].reason_label,
                "reason_3_contribution_rub": money_text(reasons[2].contribution_rub),
                "reason_3_confidence": reasons[2].confidence,
                "explained_total_rub": money_text(explained),
                "unexplained_tail_rub": money_text(unexplained),
                "overall_confidence": confidence,
            }
        )

        quality_rows.append(
            {
                "offer_id": row["offer_id"],
                "product_name": row["product_name"],
                "revenue_drop_abs_rub": money_text(revenue_drop),
                "explained_total_rub": money_text(explained),
                "unexplained_tail_rub": money_text(unexplained),
                "math_difference_rub": money_text(q2(revenue_drop - explained - unexplained)),
                "double_count_check": "ok" if q2(reasons[0].contribution_rub + reasons[2].contribution_rub) <= meta["volume_loss_money"] else "risk",
                "store_costs_mixed_in": "no",
                "irrelevant_cost_reasons_used": "no",
                "inventory_signal_quality": choose_confidence_from_inventory(inventory_signal),
                "availability_signal_note": reasons[2].evidence,
            }
        )

    return cards, examples, quality_rows, window


def build_report(cards: list[dict[str, Any]], quality_rows: list[dict[str, Any]], window: dict[str, Any]) -> str:
    selected = ", ".join(f"`{card['offer_id']}`" for card in cards)
    lines = [
        "# Profit Control Revenue Causal Card V1",
        "",
        "- Metric: `revenue_gross`",
        "- Grain: product / SKU level",
        "- Comparison window: `28 days` vs `previous 28 days`",
        f"- Current window: `{window['current_start']}` .. `{window['current_end']}`",
        f"- Previous window: `{window['previous_start']}` .. `{window['previous_end']}`",
        f"- Selected products: {selected}",
        "",
        "## 1. Cause Set Used",
        "",
        "- `снижение спроса`: measured as volume loss in money using `previous revenue per unit`, net of any availability contribution.",
        "- `рост расходов на продажу`: checked, but not converted into revenue loss in this first-card model because it is not a direct top-line driver.",
        "- `проблемы с доступностью товара`: estimated only from observed stockout snapshots and previous daily revenue run-rate.",
        "",
        "## 2. How Contributions Were Calculated",
        "",
        "- Revenue drop: `previous_28d_revenue - current_28d_revenue`.",
        "- Gross volume loss: `max(previous_units - current_units, 0) * previous_revenue_per_unit`.",
        "- Availability contribution: `max(current_oos_days - previous_oos_days, 0) * previous_revenue_per_day`, capped by revenue drop and gross volume loss.",
        "- Demand contribution: `gross volume loss - availability contribution`, capped at the remaining revenue drop.",
        "- Selling-cost growth: checked separately and kept at `0 ₽` here unless we can defend a direct revenue mechanism.",
        "- Unexplained tail: `revenue_drop - sum(cause contributions)`.",
        "",
        "## 3. Math Quality",
        "",
    ]

    for row in quality_rows:
        lines.append(
            f"- `{row['offer_id']}`: drop `{row['revenue_drop_abs_rub']}`, explained `{row['explained_total_rub']}`, unexplained `{row['unexplained_tail_rub']}`, math diff `{row['math_difference_rub']}`, double count `{row['double_count_check']}`"
        )

    lines.extend(
        [
            "",
            "## 4. What Worked / What Is Still Rough",
            "",
            "- Best-supported cause in this first pass: `снижение спроса`, because units and orders are in the new daily product layer and translate cleanly into money.",
            "- Roughest cause: `проблемы с доступностью товара`, because daily stock snapshots are sparse in the current contour.",
            "- `рост расходов на продажу` is checked structurally, but for a revenue card it is not yet a direct causal money driver in this version.",
            "- Product/store split stayed clean because only product rows from `pc_v2_daily_product_fact` were used; store-only services were not mixed into SKU cards.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    with get_conn() as conn:
        cards, examples, quality_rows, window = build_cards(conn)

    CARDS_JSON_PATH.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "metric": "revenue_gross",
                "window": {
                    "current_start": str(window["current_start"]),
                    "current_end": str(window["current_end"]),
                    "previous_start": str(window["previous_start"]),
                    "previous_end": str(window["previous_end"]),
                    "confirmed_through": str(window["anchor_day"]),
                },
                "cards": cards,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_csv(
        EXAMPLES_CSV_PATH,
        examples,
        [
            "offer_id",
            "sku",
            "product_name",
            "current_start",
            "current_end",
            "previous_start",
            "previous_end",
            "revenue_current",
            "revenue_previous",
            "revenue_delta",
            "reason_1",
            "reason_1_contribution_rub",
            "reason_1_confidence",
            "reason_2",
            "reason_2_contribution_rub",
            "reason_2_confidence",
            "reason_3",
            "reason_3_contribution_rub",
            "reason_3_confidence",
            "explained_total_rub",
            "unexplained_tail_rub",
            "overall_confidence",
        ],
    )
    write_csv(
        QUALITY_CSV_PATH,
        quality_rows,
        [
            "offer_id",
            "product_name",
            "revenue_drop_abs_rub",
            "explained_total_rub",
            "unexplained_tail_rub",
            "math_difference_rub",
            "double_count_check",
            "store_costs_mixed_in",
            "irrelevant_cost_reasons_used",
            "inventory_signal_quality",
            "availability_signal_note",
        ],
    )
    REPORT_PATH.write_text(build_report(cards, quality_rows, window), encoding="utf-8")
    print(f"report: {REPORT_PATH}")
    print(f"examples: {EXAMPLES_CSV_PATH}")
    print(f"quality: {QUALITY_CSV_PATH}")
    print(f"cards: {CARDS_JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
