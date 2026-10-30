#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

SOURCE_JSON = ROOT / "profit_control_order_flow_paid_split_v1.json"
REPORT_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v1_report.md"
AUDIT_CSV_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v1_audit.csv"
SUMMARY_CSV_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v1_summary.csv"

TWOPLACES = Decimal("0.01")
WINDOW_DAYS = Decimal("28")
TOTAL_DAYS = Decimal("56")


def q2(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def pct_text(value: Decimal) -> str:
    return f"{q2(value * Decimal('100')):.2f}%"


def money_text(value: Decimal) -> str:
    return f"{q2(value):.2f}"


def ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    return numerator / denominator


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def classify_quality(over_days_total: int, raw_total: Decimal, excess_total: Decimal) -> tuple[str, str]:
    excess_day_share = ratio(Decimal(over_days_total), TOTAL_DAYS)
    excess_raw_share = ratio(excess_total, raw_total)

    if over_days_total == 0:
        return (
            "reliable",
            "No cap days inside the 56-day comparison corridor; paid/non-paid partition stays arithmetic rather than corrective.",
        )
    if excess_day_share > Decimal("0.40") or excess_raw_share > Decimal("0.60"):
        return (
            "weak",
            "Cap is applied too often or clips too much of raw ad orders; paid/non-paid split becomes a coarse approximation.",
        )
    return (
        "usable_with_caution",
        "Cap is present but not dominant; split is still useful for direction, not for strict causal attribution.",
    )


def main() -> int:
    payload = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    rows = payload["rows"]
    window = payload["window"]

    audit_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    reliable_count = 0
    caution_count = 0
    weak_count = 0
    sku_with_excess = 0
    total_excess_days = 0
    total_ad_raw = Decimal("0")
    total_excess_orders = Decimal("0")

    for row in rows:
        split = row["split_rule"]
        offer_id = row["offer_id"]
        sku = row["sku"]
        product_name = row["product_name"]

        raw_curr = as_decimal(split["ad_orders_raw_current"])
        raw_prev = as_decimal(split["ad_orders_raw_previous"])
        paid_curr = as_decimal(split["paid_orders_current"])
        paid_prev = as_decimal(split["paid_orders_previous"])
        non_paid_curr = as_decimal(split["non_paid_orders_current"])
        non_paid_prev = as_decimal(split["non_paid_orders_previous"])

        total_orders_curr = paid_curr + non_paid_curr
        total_orders_prev = paid_prev + non_paid_prev
        excess_curr = raw_curr - paid_curr
        excess_prev = raw_prev - paid_prev

        over_days_curr = int(split["over_total_days_current"] or 0)
        over_days_prev = int(split["over_total_days_previous"] or 0)
        over_days_total = over_days_curr + over_days_prev

        raw_total = raw_curr + raw_prev
        excess_total = excess_curr + excess_prev
        total_ad_raw += raw_total
        total_excess_orders += excess_total
        total_excess_days += over_days_total

        if over_days_total > 0:
            sku_with_excess += 1

        quality_class, quality_rule = classify_quality(over_days_total, raw_total, excess_total)
        if quality_class == "reliable":
            reliable_count += 1
        elif quality_class == "usable_with_caution":
            caution_count += 1
        else:
            weak_count += 1

        excess_day_share_total = ratio(Decimal(over_days_total), TOTAL_DAYS)
        excess_raw_share_total = ratio(excess_total, raw_total)

        for bucket, day_label, total_orders, raw_orders, excess_orders, over_days in (
            ("current_window", "current_window_aggregate", total_orders_curr, raw_curr, excess_curr, over_days_curr),
            ("previous_window", "previous_window_aggregate", total_orders_prev, raw_prev, excess_prev, over_days_prev),
        ):
            audit_rows.append(
                {
                    "sku": sku,
                    "offer_id": offer_id,
                    "product_name": product_name,
                    "day": day_label,
                    "window_bucket": bucket,
                    "total_orders": money_text(total_orders),
                    "ad_orders_raw": money_text(raw_orders),
                    "ad_orders_excess": money_text(excess_orders),
                    "cap_applied_yes_no": "yes" if excess_orders > 0 else "no",
                    "split_quality_class": quality_class,
                }
            )

        summary_rows.append(
            {
                "sku": sku,
                "offer_id": offer_id,
                "product_name": product_name,
                "raw_ad_orders_56d": money_text(raw_total),
                "capped_excess_orders_56d": money_text(excess_total),
                "excess_days_56d": over_days_total,
                "excess_day_share_56d": pct_text(excess_day_share_total),
                "excess_share_of_raw_ad_orders_56d": pct_text(excess_raw_share_total),
                "split_quality_class": quality_class,
                "quality_rule": quality_rule,
                "confidence_paid": row["confidence_paid"],
                "confidence_non_paid": row["confidence_non_paid"],
                "split_quality_note": row["split_quality_note"],
            }
        )

    sku_count = len(rows)
    product_days_total = sku_count * int(TOTAL_DAYS)
    excess_day_share_layer = ratio(Decimal(total_excess_days), Decimal(product_days_total))
    excess_sku_share = ratio(Decimal(sku_with_excess), Decimal(sku_count))
    excess_raw_share_layer = ratio(total_excess_orders, total_ad_raw)

    report_lines = [
        "# Profit Control Order Flow Split Reliability Audit V1",
        "",
        "- Scope: audit only the reliability of the existing `paid/non-paid` split inside `order_flow_effect_rub`.",
        "- Source artifact: `profit_control_order_flow_paid_split_v1.json`.",
        "- Window: "
        f"`{window['current_start']}` .. `{window['current_end']}` vs "
        f"`{window['previous_start']}` .. `{window['previous_end']}`.",
        f"- Anchor: `{window['confirmed_through']}`.",
        "- Important limitation: on `2026-05-09` the local Postgres service was unavailable, so this audit uses the persisted Stage 4V split artifact and produces a window-aggregate audit rather than a fresh day-by-day DB rehydration.",
        "",
        "## 1. Where the Method Distorts",
        "",
        "- `total_orders` comes from `pc_v2_daily_product_fact.orders_count`.",
        "- `ad_orders_raw` comes from `stg_wb_ads.orders_count`, mapped to SKU via `dim_product_bridge`.",
        "- Distortion appears when mapped `ad_orders_raw > total_orders` for the same SKU/day.",
        "- In that case the method applies `paid_orders_capped = min(total_orders, ad_orders_raw)`, and the residual becomes `non_paid_orders`.",
        "- This is safe against double counting, but it turns the split into an approximation rather than a literal order partition.",
        "",
        "## 2. Reliability Rules",
        "",
        "- `reliable`: zero cap days in the 56-day corridor.",
        "- `usable_with_caution`: cap exists, but not on a dominant share of days and not on a dominant share of raw ad orders.",
        "- `weak`: cap is frequent or clips a large share of raw ad orders.",
        "",
        "## 3. Layer Summary",
        "",
        f"- Analysed SKU count: `{sku_count}`.",
        f"- SKU with any cap days: `{sku_with_excess}` ({pct_text(excess_sku_share)}).",
        f"- Excess days across analysed split layer: `{total_excess_days}` of `{product_days_total}` ({pct_text(excess_day_share_layer)}).",
        f"- Capped excess orders across analysed split layer: `{money_text(total_excess_orders)}` of raw ad orders `{money_text(total_ad_raw)}` ({pct_text(excess_raw_share_layer)}).",
        f"- Quality classes: `reliable={reliable_count}`, `usable_with_caution={caution_count}`, `weak={weak_count}`.",
        "",
        "## 4. SKU Verdicts",
        "",
    ]

    for row in summary_rows:
        report_lines.append(
            f"- `{row['offer_id']}` / `{row['sku']}`: class `{row['split_quality_class']}`, "
            f"excess days `{row['excess_days_56d']}`, excess day share `{row['excess_day_share_56d']}`, "
            f"excess raw-ad share `{row['excess_share_of_raw_ad_orders_56d']}`. {row['quality_rule']}"
        )

    report_lines.extend(
        [
            "",
            "## 5. Decision",
            "",
            f"- `reliable` SKU can move deeper into `non-paid` analysis without first redesigning the split.",
            f"- `usable_with_caution` SKU can move deeper only if we keep the split as directional evidence, not as exact attribution.",
            f"- `weak` SKU should not drive a deeper `non-paid` tree until the split is strengthened beyond simple daily cap.",
            "",
            "## 6. Current Recommendation",
            "",
            "- The current split is usable as a next-step base for `KOT2024`.",
            "- It is usable with caution for `mshpp2025`.",
            "- It is too weak for `mshcm120621mshcm120621` because cap days are systemic, not occasional.",
            "- The next best step is not to go deeper into `non-paid` for all three SKU at once, but to first harden the split method for cap-heavy SKU.",
        ]
    )

    REPORT_PATH.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    write_csv(
        AUDIT_CSV_PATH,
        audit_rows,
        [
            "sku",
            "offer_id",
            "product_name",
            "day",
            "window_bucket",
            "total_orders",
            "ad_orders_raw",
            "ad_orders_excess",
            "cap_applied_yes_no",
            "split_quality_class",
        ],
    )
    write_csv(
        SUMMARY_CSV_PATH,
        summary_rows,
        [
            "sku",
            "offer_id",
            "product_name",
            "raw_ad_orders_56d",
            "capped_excess_orders_56d",
            "excess_days_56d",
            "excess_day_share_56d",
            "excess_share_of_raw_ad_orders_56d",
            "split_quality_class",
            "quality_rule",
            "confidence_paid",
            "confidence_non_paid",
            "split_quality_note",
        ],
    )
    print(f"report: {REPORT_PATH}")
    print(f"audit: {AUDIT_CSV_PATH}")
    print(f"summary: {SUMMARY_CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
