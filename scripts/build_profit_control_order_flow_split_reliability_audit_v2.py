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
SOURCE_JSON = ROOT / "profit_control_order_flow_paid_split_v1.json"
REPORT_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v2_report.md"
AUDIT_CSV_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v2_audit.csv"
SUMMARY_CSV_PATH = ROOT / "profit_control_order_flow_split_reliability_audit_v2_summary.csv"

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


def pct_text(value: Decimal) -> str:
    return f"{q2(value * Decimal('100')):.2f}%"


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


def fetch_all(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def load_source_context() -> tuple[list[str], dict[str, str]]:
    payload = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
    offer_ids = [row["offer_id"] for row in payload["rows"]]
    return offer_ids, payload["window"]


def classify_quality(excess_days: int, total_days: int, raw_total: Decimal, excess_total: Decimal) -> tuple[str, str]:
    excess_day_share = ratio(Decimal(excess_days), Decimal(total_days))
    excess_raw_share = ratio(excess_total, raw_total)

    if excess_days == 0:
        return (
            "reliable",
            "No cap days inside the comparison corridor; paid/non-paid split stays arithmetic rather than corrective.",
        )
    if excess_day_share > Decimal("0.40") or excess_raw_share > Decimal("0.60"):
        return (
            "weak",
            "Cap is too frequent or clips too much of raw ad orders; split becomes a coarse approximation.",
        )
    return (
        "usable_with_caution",
        "Cap exists but is not dominant; split is usable for direction, not for exact attribution.",
    )


def build_sql() -> str:
    return f"""
    WITH bridge AS (
      SELECT *
      FROM {SCHEMA}.dim_product_bridge
      WHERE marketplace = 'wb'
    ),
    ads_mapped AS (
      SELECT
        a.date::date AS day,
        COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
        CASE
          WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND bm.offer_id = a.offer_id THEN 'offer_id_match'
          WHEN NULLIF(a.nm_id, '~') IS NOT NULL AND bm.nm_id = a.nm_id THEN 'nm_id_match'
          WHEN bm.canonical_product_key IS NOT NULL THEN 'bridge_match'
          ELSE 'unmatched'
        END AS mapping_method,
        SUM(COALESCE(a.orders_count, 0))::numeric(18,2) AS ad_orders_raw,
        SUM(COALESCE(a.spend, 0))::numeric(18,2) AS ad_spend,
        COUNT(*)::int AS ads_rows,
        COUNT(
          DISTINCT COALESCE(
            NULLIF(a.campaign_id::text, ''),
            NULLIF(a.raw_payload->>'advertId', ''),
            NULLIF(a.campaign_name, ''),
            '~'
          )
        )::int AS campaigns
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
        AND a.date::date BETWEEN %s AND %s
      GROUP BY 1,2,3
    ),
    product_days AS (
      SELECT
        p.date AS day,
        p.offer_id,
        p.sku,
        p.product_name,
        p.canonical_product_key,
        SUM(COALESCE(p.orders_count, 0))::numeric(18,2) AS total_orders
      FROM {SCHEMA}.pc_v2_daily_product_fact p
      WHERE p.marketplace = 'wb'
        AND p.finance_confirmed_flag = TRUE
        AND p.date BETWEEN %s AND %s
        AND COALESCE(NULLIF(p.offer_id, ''), '~') <> '~'
      GROUP BY 1,2,3,4,5
    ),
    joined AS (
      SELECT
        pd.offer_id,
        pd.sku,
        pd.product_name,
        pd.day,
        pd.total_orders,
        COALESCE(am.ad_orders_raw, 0)::numeric(18,2) AS ad_orders_raw,
        LEAST(pd.total_orders, COALESCE(am.ad_orders_raw, 0))::numeric(18,2) AS paid_orders_capped,
        GREATEST(COALESCE(am.ad_orders_raw, 0) - pd.total_orders, 0)::numeric(18,2) AS ad_orders_excess,
        CASE WHEN COALESCE(am.ad_orders_raw, 0) > pd.total_orders THEN TRUE ELSE FALSE END AS cap_applied,
        COALESCE(am.ad_spend, 0)::numeric(18,2) AS ad_spend,
        COALESCE(am.ads_rows, 0)::int AS ads_rows,
        COALESCE(am.campaigns, 0)::int AS campaigns,
        COALESCE(am.mapping_method, 'none') AS mapping_method
      FROM product_days pd
      LEFT JOIN ads_mapped am
        ON am.day = pd.day
       AND am.canonical_product_key = pd.canonical_product_key
    )
    SELECT *
    FROM joined
    ORDER BY offer_id, day
    """


def main() -> int:
    focus_offer_ids, window = load_source_context()
    params = (
        window["previous_start"],
        window["current_end"],
        window["previous_start"],
        window["current_end"],
    )

    with get_conn() as conn:
        joined_rows = fetch_all(conn, build_sql(), params)

    total_days = len(
        {
            (row["offer_id"], str(row["day"]))
            for row in joined_rows
        }
    )

    per_sku: dict[str, dict[str, Any]] = {}
    audit_rows: list[dict[str, Any]] = []

    total_excess_days = 0
    total_product_days = 0
    total_raw_orders = Decimal("0")
    total_excess_orders = Decimal("0")
    total_excess_days_multicampaign = 0

    for row in joined_rows:
        offer_id = row["offer_id"]
        total_orders = as_decimal(row["total_orders"])
        ad_orders_raw = as_decimal(row["ad_orders_raw"])
        if total_orders == 0 and ad_orders_raw == 0:
            continue

        sku_state = per_sku.setdefault(
            offer_id,
            {
                "offer_id": offer_id,
                "sku": row["sku"],
                "product_name": row["product_name"],
                "total_days": 0,
                "excess_days": 0,
                "raw_ad_orders_56d": Decimal("0"),
                "capped_excess_orders_56d": Decimal("0"),
                "total_orders_56d": Decimal("0"),
                "max_excess_orders_day": Decimal("0"),
                "excess_days_multicampaign": 0,
                "max_campaigns_on_excess_day": 0,
                "mapping_methods_seen": set(),
                "sample_focus_yes_no": "yes" if offer_id in focus_offer_ids else "no",
            },
        )

        excess = as_decimal(row["ad_orders_excess"])
        cap_applied = bool(row["cap_applied"])
        campaigns = int(row["campaigns"] or 0)

        sku_state["total_days"] += 1
        sku_state["raw_ad_orders_56d"] += ad_orders_raw
        sku_state["capped_excess_orders_56d"] += excess
        sku_state["total_orders_56d"] += total_orders
        sku_state["mapping_methods_seen"].add(row["mapping_method"])
        if excess > sku_state["max_excess_orders_day"]:
            sku_state["max_excess_orders_day"] = excess
        if cap_applied:
            sku_state["excess_days"] += 1
            if campaigns > 1:
                sku_state["excess_days_multicampaign"] += 1
                total_excess_days_multicampaign += 1
            if campaigns > sku_state["max_campaigns_on_excess_day"]:
                sku_state["max_campaigns_on_excess_day"] = campaigns
            total_excess_days += 1

        total_product_days += 1
        total_raw_orders += ad_orders_raw
        total_excess_orders += excess

        audit_rows.append(
            {
                "sku": row["sku"],
                "offer_id": offer_id,
                "product_name": row["product_name"],
                "day": str(row["day"]),
                "total_orders": money_text(total_orders),
                "ad_orders_raw": money_text(ad_orders_raw),
                "ad_orders_excess": money_text(excess),
                "cap_applied_yes_no": "yes" if cap_applied else "no",
                "split_quality_class": "",
                "campaigns": campaigns,
                "ads_rows": int(row["ads_rows"] or 0),
                "mapping_method": row["mapping_method"],
                "sample_focus_yes_no": "yes" if offer_id in focus_offer_ids else "no",
            }
        )

    summary_rows: list[dict[str, Any]] = []
    reliable_count = 0
    caution_count = 0
    weak_count = 0
    focus_reliable = 0
    focus_caution = 0
    focus_weak = 0
    sku_with_excess = 0

    for offer_id, sku_state in sorted(per_sku.items()):
        if sku_state["product_name"] == "~":
            continue
        if sku_state["raw_ad_orders_56d"] == 0 and sku_state["total_orders_56d"] == 0:
            continue

        quality_class, quality_rule = classify_quality(
            sku_state["excess_days"],
            sku_state["total_days"],
            sku_state["raw_ad_orders_56d"],
            sku_state["capped_excess_orders_56d"],
        )
        sku_state["split_quality_class"] = quality_class
        if sku_state["excess_days"] > 0:
            sku_with_excess += 1

        if quality_class == "reliable":
            reliable_count += 1
            if sku_state["sample_focus_yes_no"] == "yes":
                focus_reliable += 1
        elif quality_class == "usable_with_caution":
            caution_count += 1
            if sku_state["sample_focus_yes_no"] == "yes":
                focus_caution += 1
        else:
            weak_count += 1
            if sku_state["sample_focus_yes_no"] == "yes":
                focus_weak += 1

        excess_day_share = ratio(Decimal(sku_state["excess_days"]), Decimal(sku_state["total_days"]))
        excess_raw_share = ratio(sku_state["capped_excess_orders_56d"], sku_state["raw_ad_orders_56d"])
        multicampaign_share = ratio(Decimal(sku_state["excess_days_multicampaign"]), Decimal(sku_state["excess_days"]))

        summary_rows.append(
            {
                "sku": sku_state["sku"],
                "offer_id": offer_id,
                "product_name": sku_state["product_name"],
                "sample_focus_yes_no": sku_state["sample_focus_yes_no"],
                "raw_ad_orders_56d": money_text(sku_state["raw_ad_orders_56d"]),
                "total_orders_56d": money_text(sku_state["total_orders_56d"]),
                "capped_excess_orders_56d": money_text(sku_state["capped_excess_orders_56d"]),
                "excess_days_56d": sku_state["excess_days"],
                "total_days_56d": sku_state["total_days"],
                "excess_day_share_56d": pct_text(excess_day_share),
                "excess_share_of_raw_ad_orders_56d": pct_text(excess_raw_share),
                "excess_days_multicampaign": sku_state["excess_days_multicampaign"],
                "excess_days_multicampaign_share": pct_text(multicampaign_share),
                "max_excess_orders_day": money_text(sku_state["max_excess_orders_day"]),
                "max_campaigns_on_excess_day": sku_state["max_campaigns_on_excess_day"],
                "mapping_methods_seen": ", ".join(sorted(sku_state["mapping_methods_seen"])),
                "split_quality_class": quality_class,
                "quality_rule": quality_rule,
            }
        )

    summary_by_offer = {row["offer_id"]: row for row in summary_rows}
    audit_rows = [row for row in audit_rows if row["offer_id"] in summary_by_offer]
    for row in audit_rows:
        row["split_quality_class"] = summary_by_offer[row["offer_id"]]["split_quality_class"]

    layer_excess_day_share = ratio(Decimal(total_excess_days), Decimal(total_product_days))
    layer_excess_raw_share = ratio(total_excess_orders, total_raw_orders)
    excess_sku_share = ratio(Decimal(sku_with_excess), Decimal(len(summary_rows)))
    excess_multicampaign_share = ratio(Decimal(total_excess_days_multicampaign), Decimal(total_excess_days))

    report_lines = [
        "# Profit Control Order Flow Split Reliability Audit V2",
        "",
        "- Scope: audit only the reliability of the existing `paid/non-paid` split inside `order_flow_effect_rub`.",
        "- Source layer: live `pc_v2_daily_product_fact` + `stg_wb_ads` + `dim_product_bridge`.",
        f"- Window: `{window['current_start']}` .. `{window['current_end']}` vs `{window['previous_start']}` .. `{window['previous_end']}`.",
        f"- Anchor: `{window['confirmed_through']}`.",
        "",
        "## 1. Method Under Audit",
        "",
        "- `total_orders` = `pc_v2_daily_product_fact.orders_count` on SKU/day.",
        "- `ad_orders_raw` = `stg_wb_ads.orders_count`, mapped to SKU/day through the WB bridge.",
        "- Current split rule: `paid_orders_capped = min(total_orders, ad_orders_raw)` and `non_paid_orders = total_orders - paid_orders_capped`.",
        "- Distortion appears exactly where `ad_orders_raw > total_orders` on the same SKU/day.",
        "",
        "## 2. Where the Distortion Comes From",
        "",
        f"- Audited SKU in full layer: `{len(summary_rows)}`.",
        f"- SKU with any excess day: `{sku_with_excess}` ({pct_text(excess_sku_share)}).",
        f"- Excess days in full layer: `{total_excess_days}` of `{total_product_days}` ({pct_text(layer_excess_day_share)}).",
        f"- Excess orders in full layer: `{money_text(total_excess_orders)}` of raw ad orders `{money_text(total_raw_orders)}` ({pct_text(layer_excess_raw_share)}).",
        f"- Excess days with more than one campaign on the same SKU/day: `{total_excess_days_multicampaign}` of `{total_excess_days}` ({pct_text(excess_multicampaign_share)}).",
        "",
        "Inference from live layer:",
        "- When excess happens together with multi-campaign presence on the same SKU/day, it is strong evidence that `ads.orders_count` behaves like campaign-attributed order credit, not like a strict unique order count one-to-one with product-day orders.",
        "- That means simple summation across campaigns can overshoot product-day observed orders even when bridge mapping itself is correct.",
        "",
        "## 3. Reliability Classes",
        "",
        "- `reliable`: zero cap days in the 56-day corridor.",
        "- `usable_with_caution`: cap exists, but not on a dominant share of days and not on a dominant share of raw ad orders.",
        "- `weak`: cap is frequent or clips a large share of raw ad orders.",
        "",
        "## 4. Full-Layer Class Summary",
        "",
        f"- `reliable = {reliable_count}`",
        f"- `usable_with_caution = {caution_count}`",
        f"- `weak = {weak_count}`",
        "",
        "## 5. Focus SKU Verdicts",
        "",
    ]

    for offer_id in focus_offer_ids:
        row = summary_by_offer[offer_id]
        report_lines.append(
            f"- `{row['offer_id']}` / `{row['sku']}`: class `{row['split_quality_class']}`, "
            f"excess days `{row['excess_days_56d']}` of `{row['total_days_56d']}` "
            f"({row['excess_day_share_56d']}), excess raw-ad share `{row['excess_share_of_raw_ad_orders_56d']}`, "
            f"max excess day `{row['max_excess_orders_day']}`, max campaigns on excess day `{row['max_campaigns_on_excess_day']}`. "
            f"{row['quality_rule']}"
        )

    report_lines.extend(
        [
            "",
            "## 6. Decision",
            "",
            f"- Focus SKU classes: `reliable={focus_reliable}`, `usable_with_caution={focus_caution}`, `weak={focus_weak}`.",
            "- We can go deeper into the `non-paid` branch for `reliable` SKU immediately.",
            "- We can go deeper for `usable_with_caution` SKU only if we keep the split as directional evidence, not exact attribution.",
            "- We should not go deeper for `weak` SKU until the split method is strengthened beyond simple daily cap.",
            "",
            "## 7. Method Improvement Check",
            "",
            "- The current daily cap is still the safest non-destructive guard against double counting.",
            "- But cap alone is not enough where campaign-attributed orders systematically overshoot product-day orders.",
            "- The most promising next hardening step is not a more aggressive cap, but a better paid-order proxy that respects campaign overlap before summation.",
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
            "total_orders",
            "ad_orders_raw",
            "ad_orders_excess",
            "cap_applied_yes_no",
            "split_quality_class",
            "campaigns",
            "ads_rows",
            "mapping_method",
            "sample_focus_yes_no",
        ],
    )
    write_csv(
        SUMMARY_CSV_PATH,
        summary_rows,
        [
            "sku",
            "offer_id",
            "product_name",
            "sample_focus_yes_no",
            "raw_ad_orders_56d",
            "total_orders_56d",
            "capped_excess_orders_56d",
            "excess_days_56d",
            "total_days_56d",
            "excess_day_share_56d",
            "excess_share_of_raw_ad_orders_56d",
            "excess_days_multicampaign",
            "excess_days_multicampaign_share",
            "max_excess_orders_day",
            "max_campaigns_on_excess_day",
            "mapping_methods_seen",
            "split_quality_class",
            "quality_rule",
        ],
    )
    print(f"report: {REPORT_PATH}")
    print(f"audit: {AUDIT_CSV_PATH}")
    print(f"summary: {SUMMARY_CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
