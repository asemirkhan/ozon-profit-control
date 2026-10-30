#!/usr/bin/env python3
"""Build the client-facing recommendation snapshot with engine v2."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.recommendation_engine_v2 import (  # noqa: E402
    DataQuality,
    PeriodMetrics,
    ProductContext,
    evaluate_product,
)

SOURCE_DIR = ROOT / "artifacts" / "profit_control_recommendation_simulation_pack_v1"
OUTPUT_PATH = ROOT / "cabinet" / "data" / "profit_control_recommendations_v2.json"


def rows(name: str) -> list[dict[str, str]]:
    with (SOURCE_DIR / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def number(value: str | None) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def nullable(value: str | None) -> float | None:
    if not value or value == "unavailable":
        return None
    return number(value)


def metric(product: dict[str, str], unit: dict[str, str], period: str, start: str, end: str) -> PeriodMetrics:
    suffix = "prev" if period == "previous" else "curr"
    units = number(product.get(f"sales_units_{suffix}"))
    return PeriodMetrics(
        period_start=start,
        period_end=end,
        days=(datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1,
        revenue=number(product.get(f"revenue_{suffix}")),
        total_expenses=number(product.get(f"expenses_{suffix}")),
        result=number(product.get(f"result_{suffix}")),
        sales_units=units,
        orders=number(product.get(f"orders_{suffix}")),
        average_selling_price=number(product.get(f"avg_realized_price_{suffix}")),
        result_per_unit=nullable(unit.get(f"result_per_sold_unit_{suffix}")),
        expense_per_unit=nullable(unit.get(f"expense_per_sold_unit_{suffix}")),
        logistics=number(product.get(f"logistics_{suffix}")),
        logistics_per_unit=nullable(unit.get(f"logistics_per_sold_unit_{suffix}")),
        storage=number(product.get(f"storage_{suffix}")),
        storage_per_unit=nullable(unit.get(f"storage_per_sold_unit_{suffix}")),
        ads_spend=number(product.get(f"ads_spend_{suffix}")),
        ads_per_unit=nullable(unit.get(f"ads_per_sold_unit_{suffix}")),
        wb_attributed_orders=number(product.get(f"ad_attributed_orders_{suffix}")),
        wb_attributed_revenue=number(product.get(f"ad_attributed_revenue_{suffix}")),
        cancellations=number(product.get(f"cancelled_orders_{suffix}")),
    )


def weekly_metric(product: dict[str, str], signal: dict[str, str], period: str, start: str, end: str) -> PeriodMetrics:
    suffix = "prev" if period == "previous" else "curr"
    return PeriodMetrics(
        period_start=start,
        period_end=end,
        days=7,
        revenue=number(signal.get(f"revenue_{suffix}_7d")),
        total_expenses=0,
        result=number(signal.get(f"result_{suffix}_7d")),
        sales_units=0,
        orders=0,
        average_selling_price=0,
    )


def main() -> None:
    if os.getenv("PROFIT_CONTROL_REBUILD_RECOMMENDATION_DATASET", "1") == "1":
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "build_profit_control_recommendation_simulation_pack_v1.py")],
            cwd=ROOT,
            check=True,
            env=dict(os.environ),
        )

    horizons = {item["horizon"]: item for item in rows("store_horizons.csv")}
    month = horizons["28d"]
    week = horizons["7d"]
    units = {item["normalized_product_key"]: item for item in rows("product_unit_economics.csv")}
    signals = {item["normalized_product_key"]: item for item in rows("product_signals_28d_7d_1d.csv")}
    recommendations: list[dict[str, object]] = []

    snapshot_id = datetime.now().astimezone().isoformat(timespec="seconds")
    for product in rows("all_products_28d_normalized.csv"):
        key = product.get("normalized_product_key", "")
        if (
            not key
            or key == "~"
            or (product.get("product_name") or "").strip() == "~"
            or (product.get("offer_id") or "").strip() == "~"
        ):
            continue
        unit = units.get(key, {})
        signal = signals.get(key, {})
        sales_sample = min(number(product.get("sales_units_prev")), number(product.get("sales_units_curr")))
        ads_spend = number(product.get("ads_spend_prev")) + number(product.get("ads_spend_curr"))
        ads_attribution = (
            number(product.get("ad_attributed_orders_prev"))
            + number(product.get("ad_attributed_orders_curr"))
            + number(product.get("ad_attributed_revenue_prev"))
            + number(product.get("ad_attributed_revenue_curr"))
        )
        context = ProductContext(
            product_key=key,
            product_name=product.get("product_name") or product.get("sku") or key,
            current_month=metric(product, unit, "current", month["current_start"], month["current_end"]),
            previous_month=metric(product, unit, "previous", month["previous_start"], month["previous_end"]),
            current_7d=weekly_metric(product, signal, "current", week["current_start"], week["current_end"]),
            previous_7d=weekly_metric(product, signal, "previous", week["previous_start"], week["previous_end"]),
            quality=DataQuality(
                snapshot_id=snapshot_id,
                confirmed_through=month.get("confirmed_through"),
                last_full_operational_day=month.get("confirmed_through"),
                current_period_complete=datetime.now().date() > datetime.fromisoformat(month["current_end"]).date(),
                previous_period_complete=datetime.now().date() > datetime.fromisoformat(month["previous_end"]).date(),
                consistent_snapshot=True,
                minimum_sample_passed=sales_sample >= 3,
                cogs_quality="not_used",
                ads_reconciliation_status="reconciled" if ads_spend == 0 or ads_attribution > 0 else "spend_only",
                storage_allocation_method="calculated",
                product_identity_status="confirmed" if key != "~" else "ambiguous",
            ),
            stock_limited_days_current=int(number(product.get("stock_days_limited_curr"))),
        )
        recommendations.append(evaluate_product(context).to_dict())

    payload = {
        "version": 2,
        "generatedAt": snapshot_id,
        "confirmedThrough": month.get("confirmed_through"),
        "primaryPeriod": {
            "currentStart": month["current_start"],
            "currentEnd": month["current_end"],
            "previousStart": month["previous_start"],
            "previousEnd": month["previous_end"],
            "method": "completed_calendar_month_vs_previous_completed_calendar_month",
        },
        "weeklyPeriod": {
            "currentStart": week["current_start"],
            "currentEnd": week["current_end"],
            "previousStart": week["previous_start"],
            "previousEnd": week["previous_end"],
        },
        "recommendations": sorted(recommendations, key=lambda item: float(item["priority_score"]), reverse=True),
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT_PATH)
    print(f"recommendations_v2={len(recommendations)} output={OUTPUT_PATH}")


if __name__ == "__main__":
    main()
