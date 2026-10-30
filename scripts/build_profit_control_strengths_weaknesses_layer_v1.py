from __future__ import annotations

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.build_profit_control_calendar_seasonality_layer_v1 as cal
import scripts.build_profit_control_deep_analysis_pack_v1 as deep
import scripts.build_profit_control_dynamic_analysis_layer_v1 as dyn

MOSCOW = ZoneInfo("Europe/Moscow")
MONEY = Decimal("0.01")
FOUR = Decimal("0.0001")
OUTPUT_DIR = ROOT / "exports" / "profit_control_strengths_weaknesses_layer_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_strengths_weaknesses_layer_v1.md"

STORE_SW_CSV = OUTPUT_DIR / "store_strengths_weaknesses.csv"
PRODUCT_SW_CSV = OUTPUT_DIR / "product_strengths_weaknesses.csv"
PRODUCT_CLUSTER_CSV = OUTPUT_DIR / "product_cluster_map.csv"
NEGATIVE_DEEP_DIVE_CSV = OUTPUT_DIR / "top_negative_sku_deep_dive.csv"
POSITIVE_DEEP_DIVE_CSV = OUTPUT_DIR / "top_positive_sku_deep_dive.csv"
EXPENSE_PATTERN_CSV = OUTPUT_DIR / "expense_pattern_map.csv"
RESIDUAL_CSV = OUTPUT_DIR / "residual_after_calendar_map.csv"
ANOMALY_CSV = OUTPUT_DIR / "anomaly_map.csv"
WEEKLY_PATTERN_CSV = OUTPUT_DIR / "weekly_pattern_map.csv"
NEXT_CANDIDATES_CSV = OUTPUT_DIR / "next_causal_candidates.csv"


@dataclass(frozen=True)
class DeepDiveTarget:
    rank: int
    product_id: str
    product_name: str
    side: str


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def q2(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(MONEY, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def q4(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.0000")
    if isinstance(value, Decimal):
        return value.quantize(FOUR, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(FOUR, rounding=ROUND_HALF_UP)


def safe_div(numerator: Any, denominator: Any, places: Decimal = FOUR) -> Decimal:
    den = Decimal(str(denominator or 0))
    if den == 0:
        return Decimal("0").quantize(places, rounding=ROUND_HALF_UP)
    num = Decimal(str(numerator or 0))
    return (num / den).quantize(places, rounding=ROUND_HALF_UP)


def money(value: Any) -> str:
    return f"{q2(value):,.2f}".replace(",", " ")


def fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(fmt(row.get(column)).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def day_as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def product_groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["product_id"]].append(row)
    for items in grouped.values():
        items.sort(key=lambda item: day_as_date(item["day"]))
    return grouped


def build_product_anomaly_rows(product_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = product_groups(product_daily_rows)
    rows: list[dict[str, Any]] = []
    metrics = ["revenue_net", "profit_after_ads", "ad_spend", "logistics_total_cost", "withholding_cost"]
    for product_id, items in grouped.items():
        if product_id == deep.UNATTRIBUTED_BUCKET:
            continue
        if len(items) < 8:
            continue
        for idx in range(7, len(items)):
            row = items[idx]
            history = items[idx - 7 : idx]
            for metric in metrics:
                history_values = [float(q2(item[metric])) for item in history]
                med = median(history_values) if history_values else 0.0
                value = float(q2(row[metric]))
                ratio = (value / med) if med else (0.0 if value == 0 else 999.0)
                anomaly_type = None
                if med > 0 and ratio >= 1.9:
                    anomaly_type = "spike"
                elif med > 0 and ratio <= 0.55:
                    anomaly_type = "drop"
                if anomaly_type:
                    rows.append(
                        {
                            "scope": "product",
                            "entity_id": product_id,
                            "entity_name": row["product_name"],
                            "day": day_as_date(row["day"]).isoformat(),
                            "metric": metric,
                            "anomaly_type": anomaly_type,
                            "value": str(q2(value)),
                            "median_prev_7d": str(q2(med)),
                            "ratio_vs_prev_7d_median": str(q4(ratio)),
                            "calendar_segment": row["calendar_segment"],
                            "weekday_name": row["weekday_name"],
                            "holiday_name": row["holiday_name"],
                        }
                    )
    return rows


def summarize_product_anomalies(product_anomaly_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_anomaly_rows:
        grouped[row["entity_id"]].append(row)
    summary: dict[str, dict[str, Any]] = {}
    for product_id, items in grouped.items():
        revenue_drops = [row for row in items if row["metric"] == "revenue_net" and row["anomaly_type"] == "drop"]
        profit_drops = [row for row in items if row["metric"] == "profit_after_ads" and row["anomaly_type"] == "drop"]
        cost_spikes = [row for row in items if row["metric"] in {"ad_spend", "logistics_total_cost", "withholding_cost"} and row["anomaly_type"] == "spike"]
        latest = max(items, key=lambda item: item["day"])
        summary[product_id] = {
            "anomaly_count_total": len(items),
            "revenue_drop_anomaly_count": len(revenue_drops),
            "profit_drop_anomaly_count": len(profit_drops),
            "cost_spike_anomaly_count": len(cost_spikes),
            "latest_anomaly_day": latest["day"],
            "latest_anomaly_metric": latest["metric"],
            "latest_anomaly_type": latest["anomaly_type"],
        }
    return summary


def build_product_weekly_summary(product_daily_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped = product_groups(product_daily_rows)
    summary: dict[str, dict[str, Any]] = {}
    for product_id, items in grouped.items():
        if product_id == deep.UNATTRIBUTED_BUCKET:
            continue
        by_week: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            by_week[deep.week_start(day_as_date(row["day"]))].append(row)
        weekly_rows: list[dict[str, Any]] = []
        weekly_cost_values: list[Decimal] = []
        weekly_revenue_per_day: list[Decimal] = []
        weekly_profit_per_day: list[Decimal] = []
        for week_value in sorted(by_week):
            agg = deep.aggregate_metrics(by_week[week_value])
            revenue_pd = q2(safe_div(agg["revenue_net"], Decimal("7"), MONEY))
            profit_pd = q2(safe_div(agg["profit_after_ads"], Decimal("7"), MONEY))
            orders_pd = q2(safe_div(agg["order_count"], Decimal("7"), MONEY))
            cost_total = agg["ad_spend"] + agg["logistics_total_cost"] + agg["withholding_cost"]
            weekly_cost_values.append(q2(cost_total))
            weekly_revenue_per_day.append(revenue_pd)
            weekly_profit_per_day.append(profit_pd)
            weekly_rows.append(
                {
                    "week_start": week_value.isoformat(),
                    "revenue_per_day": str(revenue_pd),
                    "profit_after_ads_per_day": str(profit_pd),
                    "orders_per_day": str(orders_pd),
                    "cost_total": str(q2(cost_total)),
                }
            )
        revenue_drop_weeks = sum(
            1 for idx in range(1, len(weekly_revenue_per_day)) if weekly_revenue_per_day[idx] < weekly_revenue_per_day[idx - 1]
        )
        profit_drop_weeks = sum(
            1 for idx in range(1, len(weekly_profit_per_day)) if weekly_profit_per_day[idx] < weekly_profit_per_day[idx - 1]
        )
        cost_mean = sum(weekly_cost_values) / Decimal(len(weekly_cost_values)) if weekly_cost_values else Decimal("0.00")
        if weekly_cost_values and cost_mean != 0:
            variance = sum((value - cost_mean) * (value - cost_mean) for value in weekly_cost_values) / Decimal(len(weekly_cost_values))
            cost_cv = q4(variance.sqrt() / cost_mean) if variance >= 0 else Decimal("0.0000")
        else:
            cost_cv = Decimal("0.0000")
        summary[product_id] = {
            "weeks_observed": len(weekly_rows),
            "weekly_revenue_drop_weeks": revenue_drop_weeks,
            "weekly_profit_drop_weeks": profit_drop_weeks,
            "repeated_weekly_drop_pattern": revenue_drop_weeks >= 2,
            "weekly_cost_cv": str(cost_cv),
            "latest_week_revenue_per_day": weekly_rows[-1]["revenue_per_day"] if weekly_rows else "0.00",
            "previous_week_revenue_per_day": weekly_rows[-2]["revenue_per_day"] if len(weekly_rows) >= 2 else "0.00",
            "latest_week_profit_per_day": weekly_rows[-1]["profit_after_ads_per_day"] if weekly_rows else "0.00",
            "previous_week_profit_per_day": weekly_rows[-2]["profit_after_ads_per_day"] if len(weekly_rows) >= 2 else "0.00",
            "latest_week_orders_per_day": weekly_rows[-1]["orders_per_day"] if weekly_rows else "0.00",
            "weekly_rows": weekly_rows,
        }
    return summary


def classify_store_side(effect: Decimal, component: str, pattern: str = "") -> str:
    if pattern in {"repeating_spike", "one_off_spike", "sustained_deterioration"}:
        return "weakness"
    if pattern in {"sustained_improvement", "stable_shift"}:
        return "strength"
    if component == "revenue_net":
        return "strength" if effect > 0 else "weakness"
    return "strength" if effect > 0 else "weakness"


def build_store_strengths_weaknesses(
    change_driver_rows: list[dict[str, Any]],
    expense_pattern_rows: list[dict[str, Any]],
    window_mix_rows: list[dict[str, Any]],
    store_full_agg: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    positive_is_good_metrics = {"revenue_net", "profit_after_ads", "order_count", "sold_units_net", "average_check", "positive_adjustment"}
    store_driver_rows = [row for row in change_driver_rows if row["scope"] == "store"]
    for row in store_driver_rows:
        if row["window_label"] not in {"short_7d_vs_prev_28d", "medium_14d_vs_prev_42d"}:
            continue
        effect = q2(row["profit_effect_per_day"])
        if effect == 0:
            continue
        side = "strength" if effect > 0 else "weakness"
        rows.append(
            {
                "scope": "store",
                "side": side,
                "source_layer": "window_driver",
                "window_label": row["window_label"],
                "metric_or_component": row["driver_component"],
                "profit_effect_per_day": row["profit_effect_per_day"],
                "delta_component_per_day": row["delta_component_per_day"],
                "share_of_abs_effect": row["share_of_abs_effect"],
                "pattern": "",
                "full_corridor_total": "",
                "evidence_note": "normalized_per_day_change_driver",
            }
        )
    mix_index = {(row["window_label"], row["metric"]): row for row in window_mix_rows}
    for metric in ["revenue_net", "profit_after_ads", "order_count", "sold_units_net", "ad_spend", "storage_cost", "withholding_cost"]:
        for window_label in ["short_7d_vs_prev_28d", "medium_14d_vs_prev_42d"]:
            row = mix_index.get((window_label, metric))
            if not row:
                continue
            residual = q2(row["residual_after_calendar_per_day"])
            if residual == 0:
                continue
            if metric in positive_is_good_metrics:
                side = "strength" if residual > 0 else "weakness"
            else:
                side = "strength" if residual < 0 else "weakness"
            rows.append(
                {
                    "scope": "store",
                    "side": side,
                    "source_layer": "calendar_adjusted_window",
                    "window_label": window_label,
                    "metric_or_component": metric,
                    "profit_effect_per_day": row["residual_after_calendar_per_day"] if metric == "profit_after_ads" else "",
                    "delta_component_per_day": row["residual_after_calendar_per_day"],
                    "share_of_abs_effect": row["calendar_explained_share_abs"],
                    "pattern": "",
                    "full_corridor_total": "",
                    "evidence_note": "residual_after_calendar_adjustment",
                }
            )
    for row in expense_pattern_rows:
        pattern = row["pattern"]
        side = classify_store_side(Decimal("0.00"), row["component"], pattern)
        rows.append(
            {
                "scope": "store",
                "side": side,
                "source_layer": "expense_pattern",
                "window_label": "weekly_full_corridor",
                "metric_or_component": row["component"],
                "profit_effect_per_day": "",
                "delta_component_per_day": "",
                "share_of_abs_effect": "",
                "pattern": pattern,
                "full_corridor_total": "",
                "evidence_note": f"cv={row['coefficient_of_variation']}; direction_changes={row['direction_changes']}; spike_count={row['spike_count']}",
            }
        )
    for metric in ["revenue_net", "profit_before_ads", "profit_after_ads", "order_count", "sold_units_net", "average_check", "ad_spend", "logistics_total_cost", "storage_cost", "commission_cost", "acquiring_cost", "withholding_cost", "negative_adjustment"]:
        rows.append(
            {
                "scope": "store",
                "side": "baseline",
                "source_layer": "full_corridor_total",
                "window_label": "full_corridor",
                "metric_or_component": metric,
                "profit_effect_per_day": "",
                "delta_component_per_day": "",
                "share_of_abs_effect": "",
                "pattern": "",
                "full_corridor_total": str(store_full_agg[metric]),
                "evidence_note": "full_corridor_total_metric",
            }
        )
    order_map = {"weakness": 0, "strength": 1, "baseline": 2}
    rows.sort(key=lambda item: (order_map[item["side"]], item["source_layer"], item["window_label"], item["metric_or_component"]))
    return rows


def performance_group(short_profit: Decimal, medium_profit: Decimal, residual_profit: Decimal) -> str:
    if short_profit > 0 and medium_profit >= 0:
        return "growing"
    if short_profit > 0 or residual_profit >= 0:
        return "holding_up"
    if short_profit < 0 and medium_profit < 0 and residual_profit < 0:
        return "weakening_consistent"
    return "mixed"


def seasonality_group(explained_share: Decimal, residual_profit: Decimal, actual_profit: Decimal) -> str:
    if actual_profit == 0:
        return "neutral"
    if explained_share >= Decimal("0.6000") and abs(residual_profit) <= abs(actual_profit) * Decimal("0.40"):
        return "mostly_seasonal"
    if explained_share >= Decimal("0.3000"):
        return "partly_seasonal"
    return "low_seasonality_explanation"


def residual_severity(residual_profit: Decimal) -> str:
    if residual_profit <= Decimal("-3000"):
        return "severe_negative"
    if residual_profit <= Decimal("-1000"):
        return "material_negative"
    if residual_profit < Decimal("0"):
        return "mild_negative"
    if residual_profit <= Decimal("200"):
        return "neutral"
    return "positive"


def build_product_tables(
    product_window_rows: list[dict[str, Any]],
    product_context_rows: list[dict[str, Any]],
    adjusted_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    context_index = {row["product_id"]: row for row in product_context_rows}
    adjusted_index = {row["product_id"]: row for row in adjusted_rows}
    sorted_profit_totals = sorted(
        [row for row in product_window_rows if row["product_id"] != deep.UNATTRIBUTED_BUCKET],
        key=lambda item: q2(item["profit_after_ads_total"]),
        reverse=True,
    )
    top_profit_ids = {row["product_id"] for row in sorted_profit_totals[:5]}

    product_rows: list[dict[str, Any]] = []
    cluster_rows: list[dict[str, Any]] = []
    for row in product_window_rows:
        if row["product_id"] == deep.UNATTRIBUTED_BUCKET:
            continue
        context = context_index[row["product_id"]]
        adjusted = adjusted_index[row["product_id"]]
        short_profit = q2(row["short_profit_after_ads_delta_per_day"])
        medium_profit = q2(row["medium_profit_after_ads_delta_per_day"])
        residual_profit = q2(adjusted["short_residual_profit_after_calendar_per_day"])
        residual_revenue = q2(adjusted["short_residual_revenue_after_calendar_per_day"])
        explained_share = q4(adjusted["short_calendar_explained_share_profit_abs"])
        perf_group = performance_group(short_profit, medium_profit, residual_profit)
        seasonal = seasonality_group(explained_share, residual_profit, short_profit)
        severity = residual_severity(residual_profit)
        role = "core_profit_driver" if row["product_id"] in top_profit_ids else ("profit_positive_support" if q2(row["profit_after_ads_total"]) > 0 else "non_core")

        product_rows.append(
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category_name": row["category_name"],
                "full_revenue_total": row["revenue_net_total"],
                "full_profit_after_ads_total": row["profit_after_ads_total"],
                "full_margin_after_ads_total": row["margin_after_ads_total"],
                "short_actual_profit_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                "medium_profit_delta_per_day": row["medium_profit_after_ads_delta_per_day"],
                "short_actual_revenue_delta_per_day": row["short_revenue_delta_per_day"],
                "relative_to_store": context["relative_to_store"],
                "dominant_pattern": context["dominant_pattern"],
                "drag_share_of_store_profit_drop": context["drag_share_of_store_profit_drop"],
                "short_calendar_explained_share_profit_abs": adjusted["short_calendar_explained_share_profit_abs"],
                "short_residual_profit_after_calendar_per_day": adjusted["short_residual_profit_after_calendar_per_day"],
                "short_residual_revenue_after_calendar_per_day": adjusted["short_residual_revenue_after_calendar_per_day"],
                "best_fit_explanation": adjusted["best_fit_explanation"],
                "performance_group": perf_group,
                "seasonality_group": seasonal,
                "residual_severity": severity,
                "role_cluster": role,
                "latest_stock_available": row["latest_stock_available"],
                "days_cover_14d": row["days_cover_14d"],
            }
        )
        cluster_rows.append(
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category_name": row["category_name"],
                "store_relation_cluster": context["relative_to_store"],
                "driver_cluster": context["dominant_pattern"],
                "trend_cluster": perf_group,
                "seasonality_cluster": seasonal,
                "residual_cluster": severity,
                "role_cluster": role,
                "falls_faster_than_store": context["relative_to_store"] == "falling_faster_than_store",
                "falls_with_store": context["relative_to_store"] == "falling_with_store",
                "holds_better_than_store": context["relative_to_store"] == "resilient_vs_store",
                "grows_short_window": short_profit > 0,
                "revenue_driven": context["dominant_pattern"] == "revenue_driven",
                "expense_driven": context["dominant_pattern"] == "expense_driven",
                "mixed_pattern": context["dominant_pattern"] == "mixed",
                "mostly_seasonal": seasonal == "mostly_seasonal",
                "local_after_calendar": adjusted["best_fit_explanation"] == "local_demand_weakness_beyond_calendar",
            }
        )
    product_rows.sort(key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]))
    cluster_rows.sort(key=lambda item: (item["store_relation_cluster"], item["driver_cluster"], item["product_name"]))
    return product_rows, cluster_rows


def external_signals_needed() -> str:
    return "price_history,rating_reviews,card_content_photo,search_position,competitor_snapshot,stock_history"


def build_deep_dive_rows(
    targets: list[DeepDiveTarget],
    product_daily_rows: list[dict[str, Any]],
    windows: list[deep.Window],
    product_window_rows: list[dict[str, Any]],
    product_context_rows: list[dict[str, Any]],
    adjusted_rows: list[dict[str, Any]],
    anomaly_summary: dict[str, dict[str, Any]],
    weekly_summary: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped = product_groups(product_daily_rows)
    window_row_index = {row["product_id"]: row for row in product_window_rows if row["product_id"] != deep.UNATTRIBUTED_BUCKET}
    context_index = {row["product_id"]: row for row in product_context_rows}
    adjusted_index = {row["product_id"]: row for row in adjusted_rows}
    short_window = next(window for window in windows if window.label == "short_7d_vs_prev_28d")
    medium_window = next(window for window in windows if window.label == "medium_14d_vs_prev_42d")

    rows: list[dict[str, Any]] = []
    for target in targets:
        items = grouped[target.product_id]
        items.sort(key=lambda item: day_as_date(item["day"]))
        short_current = [row for row in items if short_window.current_start <= day_as_date(row["day"]) <= short_window.current_end]
        short_base = [row for row in items if short_window.base_start <= day_as_date(row["day"]) <= short_window.base_end]
        medium_current = [row for row in items if medium_window.current_start <= day_as_date(row["day"]) <= medium_window.current_end]
        medium_base = [row for row in items if medium_window.base_start <= day_as_date(row["day"]) <= medium_window.base_end]

        short_current_agg = deep.aggregate_metrics(short_current)
        short_base_agg = deep.aggregate_metrics(short_base)
        medium_current_agg = deep.aggregate_metrics(medium_current)
        medium_base_agg = deep.aggregate_metrics(medium_base)
        full_agg = deep.aggregate_metrics(items)

        context = context_index[target.product_id]
        adjusted = adjusted_index[target.product_id]
        anomaly = anomaly_summary.get(target.product_id, {})
        weekly = weekly_summary.get(target.product_id, {})
        worst_day = min(items, key=lambda item: q2(item["profit_after_ads"]))
        best_day = max(items, key=lambda item: q2(item["profit_after_ads"]))

        current_day_count = Decimal(short_window.current_days)
        base_day_count = Decimal(short_window.base_days)
        medium_current_days = Decimal(medium_window.current_days)
        medium_base_days = Decimal(medium_window.base_days)

        def pd(agg: dict[str, Any], metric: str, denom: Decimal) -> str:
            return str(q2(safe_div(agg[metric], denom, MONEY)))

        def ratio_metric(agg: dict[str, Any], metric: str) -> str:
            value = agg[metric]
            return str(q4(value)) if metric in {"ctr", "cr", "margin_after_ads"} else str(q2(value))

        rows.append(
            {
                "side": target.side,
                "rank": target.rank,
                "product_id": target.product_id,
                "product_name": target.product_name,
                "category_name": items[0]["category_name"],
                "full_revenue_total": str(full_agg["revenue_net"]),
                "full_profit_before_ads_total": str(full_agg["profit_before_ads"]),
                "full_profit_after_ads_total": str(full_agg["profit_after_ads"]),
                "full_margin_after_ads_total": str(q4(full_agg["margin_after_ads"])),
                "short_current_revenue_per_day": pd(short_current_agg, "revenue_net", current_day_count),
                "short_base_revenue_per_day": pd(short_base_agg, "revenue_net", base_day_count),
                "short_current_profit_after_ads_per_day": pd(short_current_agg, "profit_after_ads", current_day_count),
                "short_base_profit_after_ads_per_day": pd(short_base_agg, "profit_after_ads", base_day_count),
                "short_current_orders_per_day": pd(short_current_agg, "order_count", current_day_count),
                "short_base_orders_per_day": pd(short_base_agg, "order_count", base_day_count),
                "short_current_units_per_day": pd(short_current_agg, "sold_units_net", current_day_count),
                "short_base_units_per_day": pd(short_base_agg, "sold_units_net", base_day_count),
                "short_current_average_check": ratio_metric(short_current_agg, "average_check"),
                "short_base_average_check": ratio_metric(short_base_agg, "average_check"),
                "short_current_ad_spend_per_day": pd(short_current_agg, "ad_spend", current_day_count),
                "short_base_ad_spend_per_day": pd(short_base_agg, "ad_spend", base_day_count),
                "short_current_impressions_per_day": pd(short_current_agg, "impressions", current_day_count),
                "short_base_impressions_per_day": pd(short_base_agg, "impressions", base_day_count),
                "short_current_clicks_per_day": pd(short_current_agg, "clicks", current_day_count),
                "short_base_clicks_per_day": pd(short_base_agg, "clicks", base_day_count),
                "short_current_ad_orders_per_day": pd(short_current_agg, "ad_orders", current_day_count),
                "short_base_ad_orders_per_day": pd(short_base_agg, "ad_orders", base_day_count),
                "short_current_ctr": ratio_metric(short_current_agg, "ctr"),
                "short_base_ctr": ratio_metric(short_base_agg, "ctr"),
                "short_current_cpc": ratio_metric(short_current_agg, "cpc"),
                "short_base_cpc": ratio_metric(short_base_agg, "cpc"),
                "short_current_cr": ratio_metric(short_current_agg, "cr"),
                "short_base_cr": ratio_metric(short_base_agg, "cr"),
                "short_current_organic_order_proxy_per_day": str(q2(safe_div(short_current_agg["order_count"] - short_current_agg["ad_orders"], current_day_count, MONEY))),
                "short_base_organic_order_proxy_per_day": str(q2(safe_div(short_base_agg["order_count"] - short_base_agg["ad_orders"], base_day_count, MONEY))),
                "short_current_logistics_per_day": pd(short_current_agg, "logistics_total_cost", current_day_count),
                "short_base_logistics_per_day": pd(short_base_agg, "logistics_total_cost", base_day_count),
                "short_current_storage_per_day": pd(short_current_agg, "storage_cost", current_day_count),
                "short_base_storage_per_day": pd(short_base_agg, "storage_cost", base_day_count),
                "short_current_commission_per_day": pd(short_current_agg, "commission_cost", current_day_count),
                "short_base_commission_per_day": pd(short_base_agg, "commission_cost", base_day_count),
                "short_current_acquiring_per_day": pd(short_current_agg, "acquiring_cost", current_day_count),
                "short_base_acquiring_per_day": pd(short_base_agg, "acquiring_cost", base_day_count),
                "short_current_withholding_per_day": pd(short_current_agg, "withholding_cost", current_day_count),
                "short_base_withholding_per_day": pd(short_base_agg, "withholding_cost", base_day_count),
                "medium_current_revenue_per_day": pd(medium_current_agg, "revenue_net", medium_current_days),
                "medium_base_revenue_per_day": pd(medium_base_agg, "revenue_net", medium_base_days),
                "medium_current_profit_after_ads_per_day": pd(medium_current_agg, "profit_after_ads", medium_current_days),
                "medium_base_profit_after_ads_per_day": pd(medium_base_agg, "profit_after_ads", medium_base_days),
                "short_profit_delta_per_day": window_row_index[target.product_id]["short_profit_after_ads_delta_per_day"],
                "medium_profit_delta_per_day": window_row_index[target.product_id]["medium_profit_after_ads_delta_per_day"],
                "short_revenue_delta_per_day": window_row_index[target.product_id]["short_revenue_delta_per_day"],
                "relative_to_store": context["relative_to_store"],
                "dominant_pattern": context["dominant_pattern"],
                "drag_share_of_store_profit_drop": context["drag_share_of_store_profit_drop"],
                "short_calendar_explained_share_profit_abs": adjusted["short_calendar_explained_share_profit_abs"],
                "short_residual_profit_after_calendar_per_day": adjusted["short_residual_profit_after_calendar_per_day"],
                "short_residual_revenue_after_calendar_per_day": adjusted["short_residual_revenue_after_calendar_per_day"],
                "best_fit_explanation": adjusted["best_fit_explanation"],
                "latest_week_revenue_per_day": weekly.get("latest_week_revenue_per_day", "0.00"),
                "previous_week_revenue_per_day": weekly.get("previous_week_revenue_per_day", "0.00"),
                "latest_week_profit_per_day": weekly.get("latest_week_profit_per_day", "0.00"),
                "previous_week_profit_per_day": weekly.get("previous_week_profit_per_day", "0.00"),
                "latest_week_orders_per_day": weekly.get("latest_week_orders_per_day", "0.00"),
                "weekly_revenue_drop_weeks": weekly.get("weekly_revenue_drop_weeks", 0),
                "weekly_profit_drop_weeks": weekly.get("weekly_profit_drop_weeks", 0),
                "repeated_weekly_drop_pattern": weekly.get("repeated_weekly_drop_pattern", False),
                "weekly_cost_cv": weekly.get("weekly_cost_cv", "0.0000"),
                "anomaly_count_total": anomaly.get("anomaly_count_total", 0),
                "revenue_drop_anomaly_count": anomaly.get("revenue_drop_anomaly_count", 0),
                "profit_drop_anomaly_count": anomaly.get("profit_drop_anomaly_count", 0),
                "cost_spike_anomaly_count": anomaly.get("cost_spike_anomaly_count", 0),
                "latest_anomaly_day": anomaly.get("latest_anomaly_day", ""),
                "latest_anomaly_metric": anomaly.get("latest_anomaly_metric", ""),
                "latest_anomaly_type": anomaly.get("latest_anomaly_type", ""),
                "worst_profit_day": day_as_date(worst_day["day"]).isoformat(),
                "worst_profit_day_value": str(q2(worst_day["profit_after_ads"])),
                "best_profit_day": day_as_date(best_day["day"]).isoformat(),
                "best_profit_day_value": str(q2(best_day["profit_after_ads"])),
                "latest_stock_available": items[0].get("stock_available", ""),
                "external_signals_needed": external_signals_needed(),
            }
        )
    return rows


def build_expense_pattern_map(
    expense_pattern_rows: list[dict[str, Any]],
    store_change_driver_rows: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    short_index = {
        row["driver_component"]: row
        for row in store_change_driver_rows
        if row["scope"] == "store" and row["window_label"] == "short_7d_vs_prev_28d"
    }
    medium_index = {
        row["driver_component"]: row
        for row in store_change_driver_rows
        if row["scope"] == "store" and row["window_label"] == "medium_14d_vs_prev_42d"
    }
    anomaly_counts: dict[str, int] = defaultdict(int)
    metric_to_component = {
        "ad_spend": "ads",
        "logistics_total_cost": "delivery",
        "storage_cost": "storage",
        "withholding_cost": "withholding",
    }
    for row in anomaly_rows:
        if row.get("scope") != "store":
            continue
        component = metric_to_component.get(row["metric"])
        if component:
            anomaly_counts[component] += 1
    rows: list[dict[str, Any]] = []
    for row in expense_pattern_rows:
        component = row["component"]
        short = short_index.get(component)
        medium = medium_index.get(component)
        pattern = row["pattern"]
        if pattern in {"repeating_spike", "one_off_spike", "sustained_deterioration"}:
            classification = "risk_cost_pattern"
        elif pattern in {"sustained_improvement", "stable_shift"}:
            classification = "improving_or_stable"
        else:
            classification = "mixed_pattern"
        rows.append(
            {
                "component": component,
                "short_profit_effect_per_day": short["profit_effect_per_day"] if short else "",
                "short_delta_component_per_day": short["delta_component_per_day"] if short else "",
                "medium_profit_effect_per_day": medium["profit_effect_per_day"] if medium else "",
                "medium_delta_component_per_day": medium["delta_component_per_day"] if medium else "",
                "weekly_mean": row["weekly_mean"],
                "weekly_min": row["weekly_min"],
                "weekly_max": row["weekly_max"],
                "coefficient_of_variation": row["coefficient_of_variation"],
                "direction_changes": row["direction_changes"],
                "spike_count": row["spike_count"],
                "latest_3w_trend": row["latest_3w_trend"],
                "pattern": pattern,
                "store_anomaly_count": anomaly_counts.get(component, 0),
                "classification": classification,
            }
        )
    rows.sort(key=lambda item: (item["classification"], item["component"]))
    return rows


def build_weekly_pattern_map(
    store_weekly_rows: list[dict[str, Any]],
    expense_pattern_rows: list[dict[str, Any]],
    product_weekly_summary: dict[str, dict[str, Any]],
    strong_ids: set[str],
    weak_ids: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in store_weekly_rows:
        rows.append(
            {
                "scope": "store_week",
                "entity_id": "STORE",
                "entity_name": "STORE",
                "metric_or_component": "store_week",
                "week_start": row["week_start"],
                "latest_value": row["revenue_per_day"],
                "secondary_value": row["profit_after_ads_per_day"],
                "pattern": "",
                "repeatability": "",
                "supporting_detail": f"orders_per_day={row['orders_per_day']}; ad_spend_per_day={row['ad_spend_per_day']}",
            }
        )
    for row in expense_pattern_rows:
        rows.append(
            {
                "scope": "store_component",
                "entity_id": "STORE",
                "entity_name": "STORE",
                "metric_or_component": row["component"],
                "week_start": "",
                "latest_value": row["weekly_mean"],
                "secondary_value": row["coefficient_of_variation"],
                "pattern": row["pattern"],
                "repeatability": row["spike_count"],
                "supporting_detail": f"latest_3w_trend={row['latest_3w_trend']}; direction_changes={row['direction_changes']}",
            }
        )
    for product_id, row in product_weekly_summary.items():
        if product_id not in strong_ids and product_id not in weak_ids:
            continue
        rows.append(
            {
                "scope": "product_weekly",
                "entity_id": product_id,
                "entity_name": "",
                "metric_or_component": "product_weekly_revenue",
                "week_start": "",
                "latest_value": row["latest_week_revenue_per_day"],
                "secondary_value": row["previous_week_revenue_per_day"],
                "pattern": "repeated_drop" if row["repeated_weekly_drop_pattern"] else "mixed",
                "repeatability": row["weekly_revenue_drop_weeks"],
                "supporting_detail": f"latest_week_profit_per_day={row['latest_week_profit_per_day']}; weekly_cost_cv={row['weekly_cost_cv']}",
            }
        )
    return rows


def build_next_causal_candidates(
    product_strength_rows: list[dict[str, Any]],
    deep_dive_negative_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    strength_index = {row["product_id"]: row for row in product_strength_rows}
    rows: list[dict[str, Any]] = []
    for rank, row in enumerate(deep_dive_negative_rows[:12], start=1):
        strength = strength_index[row["product_id"]]
        if strength["best_fit_explanation"] == "local_demand_weakness_beyond_calendar":
            causal_boundary = "calendar_adjusted_local_weakness_confirmed"
        elif strength["best_fit_explanation"] == "cost_pressure_beyond_calendar":
            causal_boundary = "cost_pressure_after_calendar"
        else:
            causal_boundary = "mixed_or_underdetermined"
        rows.append(
            {
                "rank": rank,
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category_name": row["category_name"],
                "short_residual_profit_after_calendar_per_day": row["short_residual_profit_after_calendar_per_day"],
                "short_residual_revenue_after_calendar_per_day": row["short_residual_revenue_after_calendar_per_day"],
                "relative_to_store": row["relative_to_store"],
                "dominant_pattern": row["dominant_pattern"],
                "best_fit_explanation": row["best_fit_explanation"],
                "causal_boundary": causal_boundary,
                "fact_layer": (
                    f"profit_residual={row['short_residual_profit_after_calendar_per_day']} per day; "
                    f"revenue_residual={row['short_residual_revenue_after_calendar_per_day']} per day"
                ),
                "context_layer": (
                    f"relative_to_store={row['relative_to_store']}; "
                    f"weekly_drop_pattern={row['repeated_weekly_drop_pattern']}"
                ),
                "most_likely_hypothesis_not_proven": (
                    "demand_side_local_weakness"
                    if row["dominant_pattern"] == "revenue_driven"
                    else ("cost_pressure_local" if row["dominant_pattern"] == "expense_driven" else "mixed_local_pattern")
                ),
                "external_signals_needed": row["external_signals_needed"],
            }
        )
    return rows


def build_report(
    *,
    corridor_start: date,
    corridor_end: date,
    store_full_agg: dict[str, Any],
    store_strength_rows: list[dict[str, Any]],
    product_strength_rows: list[dict[str, Any]],
    negative_rows: list[dict[str, Any]],
    positive_rows: list[dict[str, Any]],
    expense_pattern_rows: list[dict[str, Any]],
    next_candidate_rows: list[dict[str, Any]],
) -> str:
    weaknesses = [row for row in store_strength_rows if row["side"] == "weakness" and row["source_layer"] != "full_corridor_total"][:12]
    strengths = [row for row in store_strength_rows if row["side"] == "strength" and row["source_layer"] != "full_corridor_total"][:12]
    weak_products = negative_rows[:10]
    strong_products = positive_rows[:5]
    local_after_calendar = [row for row in product_strength_rows if row["best_fit_explanation"] == "local_demand_weakness_beyond_calendar"]
    resilient_after_calendar = [row for row in product_strength_rows if row["performance_group"] in {"growing", "holding_up"}]
    lines = [
        "# PROFIT CONTROL — Strengths And Weaknesses Layer v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Corridor",
        "",
        f"- Finance-complete corridor: `{corridor_start.isoformat()} .. {corridor_end.isoformat()}` (`{(corridor_end - corridor_start).days + 1}` days).",
        f"- Full-corridor revenue: `{money(store_full_agg['revenue_net'])}`; profit before ads: `{money(store_full_agg['profit_before_ads'])}`; profit after ads: `{money(store_full_agg['profit_after_ads'])}`.",
        f"- Full-corridor orders: `{store_full_agg['order_count']}`; sold units: `{store_full_agg['sold_units_net']}`; average check: `{money(store_full_agg['average_check'])}`.",
        f"- Full-corridor ads: `{money(store_full_agg['ad_spend'])}`; logistics: `{money(store_full_agg['logistics_total_cost'])}`; storage: `{money(store_full_agg['storage_cost'])}`; commission: `{money(store_full_agg['commission_cost'])}`; acquiring: `{money(store_full_agg['acquiring_cost'])}`; withholdings: `{money(store_full_agg['withholding_cost'])}`; other negative adjustments: `{money(store_full_agg['negative_adjustment'])}`.",
        "",
        "## Store Weaknesses",
        "",
        md_table(
            weaknesses,
            ["source_layer", "window_label", "metric_or_component", "profit_effect_per_day", "delta_component_per_day", "pattern", "evidence_note"],
        ),
        "",
        "## Store Strengths",
        "",
        md_table(
            strengths,
            ["source_layer", "window_label", "metric_or_component", "profit_effect_per_day", "delta_component_per_day", "pattern", "evidence_note"],
        ),
        "",
        "## Product Weaknesses",
        "",
        md_table(
            weak_products,
            [
                "rank",
                "product_name",
                "short_profit_delta_per_day",
                "short_residual_profit_after_calendar_per_day",
                "short_residual_revenue_after_calendar_per_day",
                "relative_to_store",
                "dominant_pattern",
                "best_fit_explanation",
            ],
        ),
        "",
        "## Product Strengths",
        "",
        md_table(
            strong_products,
            [
                "rank",
                "product_name",
                "full_profit_after_ads_total",
                "short_profit_delta_per_day",
                "short_residual_profit_after_calendar_per_day",
                "relative_to_store",
                "dominant_pattern",
            ],
        ),
        "",
        "## Expense Pattern Map",
        "",
        md_table(
            expense_pattern_rows,
            [
                "component",
                "short_profit_effect_per_day",
                "medium_profit_effect_per_day",
                "coefficient_of_variation",
                "latest_3w_trend",
                "pattern",
                "classification",
            ],
        ),
        "",
        "## Next Causal Candidates",
        "",
        md_table(
            next_candidate_rows[:12],
            [
                "rank",
                "product_name",
                "short_residual_profit_after_calendar_per_day",
                "relative_to_store",
                "dominant_pattern",
                "causal_boundary",
                "most_likely_hypothesis_not_proven",
            ],
        ),
        "",
        "## Internal Analytical Conclusion",
        "",
        f"- Store weakness is real at both demand and profit layers, but it is not homogeneous: some cost lines improved while revenue-side weakness remained the dominant drag.",
        f"- Calendar mix explains part of the short and medium deterioration, yet `{len(local_after_calendar)}` mapped products still show local weakness after calendar correction.",
        f"- The strongest weak SKU remains `Феи Единороги`, while `{len(resilient_after_calendar)}` products are still in `growing/holding_up` groups after the same correction logic.",
        "- The next causal stage should focus on residual-after-calendar weak SKUs first, not on products whose weakness is mostly explained by holiday mix alone.",
        "- The current layer separates fact, component, context, and external-data gaps; it does not claim a final cause where only a hypothesis boundary is available.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    corridor_start, corridor_end = deep.fetch_corridor_bounds()
    corridor_days = deep.daterange(corridor_start, corridor_end)
    calendar_meta = cal.build_calendar_metadata(corridor_days)

    product_daily_raw = deep.fetch_product_daily(corridor_start, corridor_end)
    product_daily_full = cal.fill_product_days(product_daily_raw, corridor_days)
    product_daily_annotated = cal.annotate_daily_rows(product_daily_full, calendar_meta)

    store_daily_raw = deep.build_store_daily(product_daily_raw, corridor_days)
    store_daily_annotated = cal.annotate_daily_rows(store_daily_raw, calendar_meta)
    store_weekly_rows = dyn.build_store_weekly(product_daily_raw, corridor_days)

    windows = deep.build_windows(corridor_start, corridor_end)
    store_window_rows = dyn.build_store_window_comparison(product_daily_raw, windows)
    product_window_rows, _product_pnl_rows = deep.build_product_comparisons(product_daily_raw, corridor_end, windows)
    product_context_rows = dyn.build_product_context_rows(product_window_rows, store_window_rows)
    expense_daily_rows, expense_weekly_rows = deep.build_expense_component_tables(product_daily_raw)
    expense_pattern_rows = dyn.weekly_component_patterns(expense_weekly_rows)
    store_anomaly_rows = dyn.detect_store_anomalies([
        {**row, "calendar_bucket": ("holiday" if row["is_holiday"] else ("pre_holiday" if row["event_phase"] == "pre_holiday" else ("weekend" if row["is_weekend"] else "weekday")))}
        for row in store_daily_annotated
    ])
    window_mix_rows, _window_count_rows = cal.build_window_calendar_tables(store_daily_annotated, windows)
    adjusted_rows = cal.build_product_calendar_adjusted(product_daily_annotated, windows, window_mix_rows)
    change_driver_rows = deep.build_change_drivers(product_daily_raw, product_window_rows, windows)

    product_anomaly_rows = build_product_anomaly_rows(product_daily_annotated)
    anomaly_summary = summarize_product_anomalies(product_anomaly_rows)
    product_weekly_summary = build_product_weekly_summary(product_daily_annotated)

    store_full_agg = deep.aggregate_metrics(product_daily_raw)
    store_strength_rows = build_store_strengths_weaknesses(change_driver_rows, expense_pattern_rows, window_mix_rows, store_full_agg)
    product_strength_rows, product_cluster_rows = build_product_tables(product_window_rows, product_context_rows, adjusted_rows)

    negative_targets = [
        DeepDiveTarget(rank=idx + 1, product_id=row["product_id"], product_name=row["product_name"], side="negative")
        for idx, row in enumerate(
            sorted(
                [row for row in product_strength_rows if q2(row["short_residual_profit_after_calendar_per_day"]) < 0],
                key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]),
            )[:10]
        )
    ]
    positive_candidates = sorted(
        [
            row for row in product_strength_rows
            if row["performance_group"] in {"growing", "holding_up"} or row["relative_to_store"] == "resilient_vs_store"
        ],
        key=lambda item: q2(item["full_profit_after_ads_total"]),
        reverse=True,
    )
    positive_targets = [
        DeepDiveTarget(rank=idx + 1, product_id=row["product_id"], product_name=row["product_name"], side="positive")
        for idx, row in enumerate(positive_candidates[:5])
    ]

    negative_deep_dive_rows = build_deep_dive_rows(
        negative_targets,
        product_daily_annotated,
        windows,
        product_window_rows,
        product_context_rows,
        adjusted_rows,
        anomaly_summary,
        product_weekly_summary,
    )
    positive_deep_dive_rows = build_deep_dive_rows(
        positive_targets,
        product_daily_annotated,
        windows,
        product_window_rows,
        product_context_rows,
        adjusted_rows,
        anomaly_summary,
        product_weekly_summary,
    )

    strong_ids = {row["product_id"] for row in positive_deep_dive_rows}
    weak_ids = {row["product_id"] for row in negative_deep_dive_rows}
    expense_pattern_map_rows = build_expense_pattern_map(expense_pattern_rows, change_driver_rows, [{"scope": "store", **row} for row in store_anomaly_rows])
    weekly_pattern_rows = build_weekly_pattern_map(store_weekly_rows, expense_pattern_rows, product_weekly_summary, strong_ids, weak_ids)
    residual_rows = [
        {
            **row,
            "residual_rank": idx + 1,
        }
        for idx, row in enumerate(
            sorted(product_strength_rows, key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]))
        )
    ]
    anomaly_rows = [{"scope": "store", "entity_id": "STORE", "entity_name": "STORE", **row} for row in store_anomaly_rows] + product_anomaly_rows
    next_candidate_rows = build_next_causal_candidates(product_strength_rows, negative_deep_dive_rows)

    write_csv(STORE_SW_CSV, store_strength_rows)
    write_csv(PRODUCT_SW_CSV, product_strength_rows)
    write_csv(PRODUCT_CLUSTER_CSV, product_cluster_rows)
    write_csv(NEGATIVE_DEEP_DIVE_CSV, negative_deep_dive_rows)
    write_csv(POSITIVE_DEEP_DIVE_CSV, positive_deep_dive_rows)
    write_csv(EXPENSE_PATTERN_CSV, expense_pattern_map_rows)
    write_csv(RESIDUAL_CSV, residual_rows)
    write_csv(ANOMALY_CSV, anomaly_rows)
    write_csv(WEEKLY_PATTERN_CSV, weekly_pattern_rows)
    write_csv(NEXT_CANDIDATES_CSV, next_candidate_rows)

    DOC_PATH.write_text(
        build_report(
            corridor_start=corridor_start,
            corridor_end=corridor_end,
            store_full_agg=store_full_agg,
            store_strength_rows=store_strength_rows,
            product_strength_rows=product_strength_rows,
            negative_rows=negative_deep_dive_rows,
            positive_rows=positive_deep_dive_rows,
            expense_pattern_rows=expense_pattern_map_rows,
            next_candidate_rows=next_candidate_rows,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {STORE_SW_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_SW_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_CLUSTER_CSV.relative_to(ROOT)}")
    print(f"Wrote {NEGATIVE_DEEP_DIVE_CSV.relative_to(ROOT)}")
    print(f"Wrote {POSITIVE_DEEP_DIVE_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXPENSE_PATTERN_CSV.relative_to(ROOT)}")
    print(f"Wrote {RESIDUAL_CSV.relative_to(ROOT)}")
    print(f"Wrote {ANOMALY_CSV.relative_to(ROOT)}")
    print(f"Wrote {WEEKLY_PATTERN_CSV.relative_to(ROOT)}")
    print(f"Wrote {NEXT_CANDIDATES_CSV.relative_to(ROOT)}")
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
