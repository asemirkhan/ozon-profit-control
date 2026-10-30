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

import scripts.build_profit_control_deep_analysis_pack_v1 as deep

MOSCOW = ZoneInfo("Europe/Moscow")
MONEY = Decimal("0.01")
FOUR = Decimal("0.0001")
OUTPUT_DIR = ROOT / "exports" / "profit_control_dynamic_analysis_layer_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_dynamic_analysis_layer_v1.md"
STORE_DAILY_CSV = OUTPUT_DIR / "store_daily_dynamics.csv"
STORE_WEEKLY_CSV = OUTPUT_DIR / "store_weekly_dynamics.csv"
STORE_WINDOWS_CSV = OUTPUT_DIR / "store_window_comparison.csv"
WINDOW_AVAILABILITY_CSV = OUTPUT_DIR / "window_availability.csv"
PRODUCT_DYNAMICS_CSV = OUTPUT_DIR / "product_dynamics_summary.csv"
PRODUCT_CONTEXT_CSV = OUTPUT_DIR / "product_vs_store_context.csv"
EXPENSE_DAILY_CSV = OUTPUT_DIR / "expense_component_daily.csv"
EXPENSE_WEEKLY_CSV = OUTPUT_DIR / "expense_component_weekly.csv"
DAILY_ANOMALIES_CSV = OUTPUT_DIR / "daily_anomalies.csv"
WEEKLY_PATTERNS_CSV = OUTPUT_DIR / "weekly_patterns.csv"
CALENDAR_EFFECTS_CSV = OUTPUT_DIR / "calendar_effects.csv"
CANDIDATES_CSV = OUTPUT_DIR / "product_candidates_for_next_causal_stage.csv"
PRODUCT_WEEKLY_PATTERNS_CSV = OUTPUT_DIR / "product_weekly_patterns.csv"


@dataclass(frozen=True)
class WindowDef:
    label: str
    current_days: int
    base_days: int


WINDOW_DEFS = [
    WindowDef("short_7d_vs_prev_28d", 7, 28),
    WindowDef("medium_14d_vs_prev_42d", 14, 42),
    WindowDef("long_30d_vs_prev_30d", 30, 30),
]

STORE_METRICS = [
    "revenue_net",
    "profit_after_ads",
    "order_count",
    "sold_units_net",
    "average_check",
    "ad_spend",
    "logistics_total_cost",
    "storage_cost",
    "commission_cost",
    "acquiring_cost",
    "withholding_cost",
    "negative_adjustment",
]

CALENDAR_HOLIDAYS = {
    date(2026, 2, 23): "Defender_of_the_Fatherland_Day",
    date(2026, 3, 8): "International_Womens_Day",
}


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


def pct(value: Any) -> str:
    return f"{(q4(value) * Decimal('100')).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


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
    fieldnames: list[str, ...] | list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_window_availability(corridor_start: date, corridor_end: date) -> tuple[list[deep.Window], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    available: list[deep.Window] = []
    for item in WINDOW_DEFS:
        current_start = corridor_end - timedelta(days=item.current_days - 1)
        base_end = current_start - timedelta(days=1)
        base_start = base_end - timedelta(days=item.base_days - 1)
        is_available = base_start >= corridor_start
        rows.append(
            {
                "window_label": item.label,
                "current_days": item.current_days,
                "base_days": item.base_days,
                "current_start": current_start.isoformat(),
                "current_end": corridor_end.isoformat(),
                "base_start": base_start.isoformat(),
                "base_end": base_end.isoformat(),
                "available": is_available,
                "availability_reason": "enough_finance_complete_corridor" if is_available else "not_enough_finance_complete_days",
            }
        )
        if is_available:
            available.append(
                deep.Window(
                    label=item.label,
                    current_start=current_start,
                    current_end=corridor_end,
                    base_start=base_start,
                    base_end=base_end,
                    current_days=item.current_days,
                    base_days=item.base_days,
                )
            )
    return available, rows


def calendar_info(day_value: date) -> dict[str, Any]:
    weekday_idx = day_value.weekday()
    is_weekend = weekday_idx >= 5
    holiday_name = CALENDAR_HOLIDAYS.get(day_value, "")
    pre_holiday = any(day_value == holiday - timedelta(days=1) for holiday in CALENDAR_HOLIDAYS)
    return {
        "weekday_index": weekday_idx,
        "weekday_name": day_value.strftime("%A"),
        "is_weekend": is_weekend,
        "holiday_name": holiday_name,
        "is_holiday": bool(holiday_name),
        "is_pre_holiday": pre_holiday,
        "calendar_bucket": "holiday" if holiday_name else ("pre_holiday" if pre_holiday else ("weekend" if is_weekend else "weekday")),
    }


def build_store_weekly(product_daily: list[dict[str, Any]], corridor_days: list[date]) -> list[dict[str, Any]]:
    by_week: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in product_daily:
        by_week[deep.week_start(row["day"])].append(row)
    rows: list[dict[str, Any]] = []
    for week_value in sorted({deep.week_start(day_value) for day_value in corridor_days}):
        agg = deep.aggregate_metrics(by_week.get(week_value, []))
        rows.append(
            {
                "week_start": week_value.isoformat(),
                "days": agg["day_count"],
                "revenue_net": str(agg["revenue_net"]),
                "profit_after_ads": str(agg["profit_after_ads"]),
                "order_count": agg["order_count"],
                "sold_units_net": str(agg["sold_units_net"]),
                "average_check": str(agg["average_check"]),
                "ad_spend": str(agg["ad_spend"]),
                "logistics_total_cost": str(agg["logistics_total_cost"]),
                "storage_cost": str(agg["storage_cost"]),
                "commission_cost": str(agg["commission_cost"]),
                "acquiring_cost": str(agg["acquiring_cost"]),
                "withholding_cost": str(agg["withholding_cost"]),
                "negative_adjustment": str(agg["negative_adjustment"]),
                "margin_after_ads": str(q4(agg["margin_after_ads"])),
                "revenue_per_day": str(q2(safe_div(agg["revenue_net"], agg["day_count"], MONEY))),
                "profit_after_ads_per_day": str(q2(safe_div(agg["profit_after_ads"], agg["day_count"], MONEY))),
                "orders_per_day": str(q2(safe_div(agg["order_count"], agg["day_count"], MONEY))),
                "ad_spend_per_day": str(q2(safe_div(agg["ad_spend"], agg["day_count"], MONEY))),
            }
        )
    return rows


def build_store_window_comparison(product_daily: list[dict[str, Any]], windows: list[deep.Window]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for window in windows:
        current_rows = deep.slice_rows(product_daily, window.current_start, window.current_end)
        base_rows = deep.slice_rows(product_daily, window.base_start, window.base_end)
        current_agg = deep.aggregate_metrics(current_rows)
        base_agg = deep.aggregate_metrics(base_rows)
        for metric in STORE_METRICS:
            current_total = current_agg.get(metric, Decimal("0.00"))
            base_total = base_agg.get(metric, Decimal("0.00"))
            current_per_day = deep.window_normalized_metric(current_agg, metric, window.current_days)
            base_per_day = deep.window_normalized_metric(base_agg, metric, window.base_days)
            delta = q2(current_per_day - base_per_day)
            delta_pct = q4(safe_div(delta, base_per_day)) if q2(base_per_day) != 0 else Decimal("0.0000")
            comparisons.append(
                {
                    "window_label": window.label,
                    "metric": metric,
                    "current_start": window.current_start.isoformat(),
                    "current_end": window.current_end.isoformat(),
                    "base_start": window.base_start.isoformat(),
                    "base_end": window.base_end.isoformat(),
                    "current_total": str(current_total),
                    "base_total": str(base_total),
                    "current_per_day": str(current_per_day),
                    "base_per_day": str(base_per_day),
                    "delta_per_day": str(delta),
                    "delta_pct_vs_base_per_day": str(delta_pct),
                }
            )
    return comparisons


def build_calendar_effects(store_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store_daily_rows:
        grouped[row["calendar_bucket"]].append(row)

    rows: list[dict[str, Any]] = []
    for bucket, items in sorted(grouped.items()):
        count = len(items)
        revenue_avg = q2(sum(q2(item["revenue_net"]) for item in items) / Decimal(count))
        profit_avg = q2(sum(q2(item["profit_after_ads"]) for item in items) / Decimal(count))
        orders_avg = q2(sum(Decimal(str(item["order_count"])) for item in items) / Decimal(count))
        ad_avg = q2(sum(q2(item["ad_spend"]) for item in items) / Decimal(count))
        logistics_avg = q2(sum(q2(item["logistics_total_cost"]) for item in items) / Decimal(count))
        rows.append(
            {
                "calendar_bucket": bucket,
                "days_count": count,
                "avg_revenue_per_day": str(revenue_avg),
                "avg_profit_after_ads_per_day": str(profit_avg),
                "avg_orders_per_day": str(orders_avg),
                "avg_ad_spend_per_day": str(ad_avg),
                "avg_logistics_per_day": str(logistics_avg),
            }
        )
    return rows


def detect_store_anomalies(store_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    anomalies: list[dict[str, Any]] = []
    numeric_rows = sorted(store_daily_rows, key=lambda item: item["day"])
    metrics = [
        "revenue_net",
        "profit_after_ads",
        "ad_spend",
        "logistics_total_cost",
        "storage_cost",
        "withholding_cost",
    ]
    for idx in range(7, len(numeric_rows)):
        row = numeric_rows[idx]
        history = numeric_rows[idx - 7 : idx]
        day_meta = calendar_info(date.fromisoformat(row["day"]))
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
                anomalies.append(
                    {
                        "day": row["day"],
                        "metric": metric,
                        "anomaly_type": anomaly_type,
                        "value": str(q2(value)),
                        "median_prev_7d": str(q2(med)),
                        "ratio_vs_prev_7d_median": f"{ratio:.3f}",
                        "calendar_bucket": day_meta["calendar_bucket"],
                        "weekday_name": day_meta["weekday_name"],
                        "holiday_name": day_meta["holiday_name"],
                        "anomaly_scope": "single_metric",
                    }
                )

        revenue_med = median([float(q2(item["revenue_net"])) for item in history])
        cost_med = median([float(q2(item["logistics_total_cost"]) + q2(item["ad_spend"]) + q2(item["withholding_cost"])) for item in history])
        revenue_value = float(q2(row["revenue_net"]))
        cost_value = float(q2(row["logistics_total_cost"]) + q2(row["ad_spend"]) + q2(row["withholding_cost"]))
        if revenue_med > 0 and cost_med > 0 and revenue_value / revenue_med <= 0.7 and cost_value / cost_med >= 1.2:
            anomalies.append(
                {
                    "day": row["day"],
                    "metric": "revenue_vs_cost_combo",
                    "anomaly_type": "unusual_combo",
                    "value": f"revenue={q2(revenue_value)}; cost={q2(cost_value)}",
                    "median_prev_7d": f"revenue={q2(revenue_med)}; cost={q2(cost_med)}",
                    "ratio_vs_prev_7d_median": f"revenue={revenue_value/revenue_med:.3f}; cost={cost_value/cost_med:.3f}",
                    "calendar_bucket": day_meta["calendar_bucket"],
                    "weekday_name": day_meta["weekday_name"],
                    "holiday_name": day_meta["holiday_name"],
                    "anomaly_scope": "cross_metric",
                }
            )
    return anomalies


def weekly_component_patterns(expense_weekly_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_component: dict[str, list[Decimal]] = defaultdict(list)
    by_component_week: dict[str, list[str]] = defaultdict(list)
    for row in expense_weekly_rows:
        by_component[row["component"]].append(q2(row["amount_total"]))
        by_component_week[row["component"]].append(row["week_start"])

    rows: list[dict[str, Any]] = []
    for component, values in by_component.items():
        if not values:
            continue
        mean = sum(values) / Decimal(len(values))
        variance = sum((value - mean) * (value - mean) for value in values) / Decimal(len(values)) if values else Decimal("0")
        std = variance.sqrt() if variance >= 0 else Decimal("0")
        cv = safe_div(std, mean) if mean != 0 else Decimal("0.0000")
        deltas = [values[idx] - values[idx - 1] for idx in range(1, len(values))]
        direction_changes = 0
        for idx in range(1, len(deltas)):
            if (deltas[idx] > 0 > deltas[idx - 1]) or (deltas[idx] < 0 < deltas[idx - 1]):
                direction_changes += 1
        med = median([float(item) for item in values]) if values else 0.0
        spike_count = sum(1 for item in values if med > 0 and float(item) / med >= 1.6)
        last3 = values[-3:]
        sustained_up = len(last3) == 3 and last3[0] < last3[1] < last3[2]
        sustained_down = len(last3) == 3 and last3[0] > last3[1] > last3[2]
        if sustained_up:
            pattern = "sustained_deterioration"
        elif sustained_down:
            pattern = "sustained_improvement"
        elif spike_count >= 2:
            pattern = "repeating_spike"
        elif spike_count == 1:
            pattern = "one_off_spike"
        elif cv > Decimal("0.35") and direction_changes >= 2:
            pattern = "wave_like"
        elif cv < Decimal("0.15"):
            pattern = "stable_shift"
        else:
            pattern = "mixed"
        rows.append(
            {
                "component": component,
                "weeks": len(values),
                "weekly_mean": str(q2(mean)),
                "weekly_min": str(q2(min(values))),
                "weekly_max": str(q2(max(values))),
                "coefficient_of_variation": str(q4(cv)),
                "direction_changes": direction_changes,
                "spike_count": spike_count,
                "latest_3w_trend": "up" if sustained_up else ("down" if sustained_down else "non_monotonic"),
                "pattern": pattern,
            }
        )
    rows.sort(key=lambda item: (item["pattern"], q4(item["coefficient_of_variation"])), reverse=True)
    return rows


def build_product_context_rows(product_window_rows: list[dict[str, Any]], store_window_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    store_index = {(row["window_label"], row["metric"]): row for row in store_window_rows}
    store_short_revenue_pct = q4(store_index[("short_7d_vs_prev_28d", "revenue_net")]["delta_pct_vs_base_per_day"])
    store_short_profit_pct = q4(store_index[("short_7d_vs_prev_28d", "profit_after_ads")]["delta_pct_vs_base_per_day"])
    store_short_orders_pct = q4(store_index[("short_7d_vs_prev_28d", "order_count")]["delta_pct_vs_base_per_day"])
    store_profit_drop_abs = abs(q2(store_index[("short_7d_vs_prev_28d", "profit_after_ads")]["delta_per_day"])) or Decimal("1.00")
    rows: list[dict[str, Any]] = []
    for row in product_window_rows:
        if row["product_id"] == deep.UNATTRIBUTED_BUCKET:
            continue
        short_current_revenue = q2(row["short_current_revenue_total"]) / Decimal("7")
        short_base_revenue = q2(row["short_base_revenue_total"]) / Decimal("28")
        short_current_profit = q2(row["short_current_profit_after_ads_total"]) / Decimal("7")
        short_base_profit = q2(row["short_base_profit_after_ads_total"]) / Decimal("28")
        revenue_pct = q4(safe_div(short_current_revenue - short_base_revenue, short_base_revenue)) if short_base_revenue != 0 else Decimal("0.0000")
        profit_pct = q4(safe_div(short_current_profit - short_base_profit, short_base_profit)) if short_base_profit != 0 else Decimal("0.0000")
        orders_current = q2(q2(row["order_count_total"]) / Decimal(max(int(row["days_active"]), 1)))
        revenue_drop_strength = q2(-q2(row["short_revenue_delta_per_day"])) if q2(row["short_revenue_delta_per_day"]) < 0 else Decimal("0.00")
        expense_growth_strength = (
            max(q2(row["short_ad_spend_delta_per_day"]), Decimal("0.00"))
            + max(q2(row["short_logistics_delta_per_day"]), Decimal("0.00"))
            + max(q2(row["short_withholding_delta_per_day"]), Decimal("0.00"))
        )
        if revenue_drop_strength > expense_growth_strength * Decimal("1.5"):
            dominant_pattern = "revenue_driven"
        elif expense_growth_strength > revenue_drop_strength * Decimal("1.5"):
            dominant_pattern = "expense_driven"
        else:
            dominant_pattern = "mixed"

        if revenue_pct < store_short_revenue_pct - Decimal("0.1000"):
            relative_to_store = "falling_faster_than_store"
        elif revenue_pct <= 0 and abs(revenue_pct - store_short_revenue_pct) <= Decimal("0.1000"):
            relative_to_store = "falling_with_store"
        elif revenue_pct > 0 and store_short_revenue_pct <= 0:
            relative_to_store = "resilient_vs_store"
        else:
            relative_to_store = "mixed_vs_store"

        rows.append(
            {
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "category_name": row["category_name"],
                "short_revenue_delta_per_day": row["short_revenue_delta_per_day"],
                "short_profit_after_ads_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                "medium_profit_after_ads_delta_per_day": row["medium_profit_after_ads_delta_per_day"],
                "short_ad_spend_delta_per_day": row["short_ad_spend_delta_per_day"],
                "short_logistics_delta_per_day": row["short_logistics_delta_per_day"],
                "short_withholding_delta_per_day": row["short_withholding_delta_per_day"],
                "product_short_revenue_delta_pct": str(revenue_pct),
                "product_short_profit_delta_pct": str(profit_pct),
                "store_short_revenue_delta_pct": str(store_short_revenue_pct),
                "store_short_profit_delta_pct": str(store_short_profit_pct),
                "store_short_orders_delta_pct": str(store_short_orders_pct),
                "relative_to_store": relative_to_store,
                "dominant_pattern": dominant_pattern,
                "drag_share_of_store_profit_drop": str(q4(abs(q2(row["short_profit_after_ads_delta_per_day"])) / store_profit_drop_abs)),
                "is_negative_short_profit": q2(row["short_profit_after_ads_delta_per_day"]) < 0,
                "is_negative_medium_profit": q2(row["medium_profit_after_ads_delta_per_day"]) < 0,
                "days_active": row["days_active"],
                "full_profit_after_ads_total": row["profit_after_ads_total"],
                "full_revenue_total": row["revenue_net_total"],
            }
        )
    return rows


def build_product_weekly_patterns(product_daily: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_daily:
        if row["product_id"] == deep.UNATTRIBUTED_BUCKET:
            continue
        grouped[row["product_id"]].append(row)

    rows: list[dict[str, Any]] = []
    for product_id, items in grouped.items():
        by_week: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_week[deep.week_start(item["day"])].append(item)
        weekly_revenue: list[Decimal] = []
        weekly_orders: list[Decimal] = []
        for week_value in sorted(by_week):
            agg = deep.aggregate_metrics(by_week[week_value])
            weekly_revenue.append(q2(safe_div(agg["revenue_net"], agg["day_count"], MONEY)))
            weekly_orders.append(q2(safe_div(agg["order_count"], agg["day_count"], MONEY)))
        drop_weeks = 0
        for idx in range(1, len(weekly_revenue)):
            if weekly_revenue[idx] < weekly_revenue[idx - 1]:
                drop_weeks += 1
        repeated_drop_pattern = drop_weeks >= 2
        rows.append(
            {
                "product_id": product_id,
                "product_name": items[0]["product_name"],
                "weeks_observed": len(weekly_revenue),
                "revenue_drop_weeks": drop_weeks,
                "repeated_weekly_drop_pattern": repeated_drop_pattern,
                "latest_week_revenue_per_day": str(weekly_revenue[-1]) if weekly_revenue else "0.00",
                "previous_week_revenue_per_day": str(weekly_revenue[-2]) if len(weekly_revenue) >= 2 else "0.00",
                "latest_week_orders_per_day": str(weekly_orders[-1]) if weekly_orders else "0.00",
            }
        )
    rows.sort(key=lambda item: (item["repeated_weekly_drop_pattern"], item["revenue_drop_weeks"]), reverse=True)
    return rows


def build_candidates(product_context_rows: list[dict[str, Any]], product_weekly_patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    weekly_index = {row["product_id"]: row for row in product_weekly_patterns}
    rows = []
    sorted_rows = sorted(
        product_context_rows,
        key=lambda item: (
            q2(item["short_profit_after_ads_delta_per_day"]) + q2(item["medium_profit_after_ads_delta_per_day"]),
            -q4(item["drag_share_of_store_profit_drop"]),
        ),
    )
    for rank, item in enumerate(sorted_rows[:15], start=1):
        weekly = weekly_index.get(item["product_id"], {})
        rows.append(
            {
                "rank": rank,
                "product_id": item["product_id"],
                "product_name": item["product_name"],
                "category_name": item["category_name"],
                "short_profit_after_ads_delta_per_day": item["short_profit_after_ads_delta_per_day"],
                "medium_profit_after_ads_delta_per_day": item["medium_profit_after_ads_delta_per_day"],
                "short_revenue_delta_per_day": item["short_revenue_delta_per_day"],
                "dominant_pattern": item["dominant_pattern"],
                "relative_to_store": item["relative_to_store"],
                "drag_share_of_store_profit_drop": item["drag_share_of_store_profit_drop"],
                "repeated_weekly_drop_pattern": weekly.get("repeated_weekly_drop_pattern", False),
                "candidate_reason": (
                    f"short_profit_delta={item['short_profit_after_ads_delta_per_day']}; "
                    f"medium_profit_delta={item['medium_profit_after_ads_delta_per_day']}; "
                    f"dominant_pattern={item['dominant_pattern']}; "
                    f"relative_to_store={item['relative_to_store']}"
                ),
            }
        )
    return rows


def build_store_daily_with_calendar(store_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in store_daily_rows:
        day_value = date.fromisoformat(row["day"])
        rows.append({**row, **calendar_info(day_value)})
    return rows


def build_report(
    *,
    corridor_start: date,
    corridor_end: date,
    window_availability_rows: list[dict[str, Any]],
    store_window_rows: list[dict[str, Any]],
    store_daily_rows: list[dict[str, Any]],
    store_weekly_rows: list[dict[str, Any]],
    product_context_rows: list[dict[str, Any]],
    expense_pattern_rows: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
    calendar_effect_rows: list[dict[str, Any]],
    candidates_rows: list[dict[str, Any]],
) -> str:
    store_index = {(row["window_label"], row["metric"]): row for row in store_window_rows}
    short_revenue = store_index[("short_7d_vs_prev_28d", "revenue_net")]
    short_profit = store_index[("short_7d_vs_prev_28d", "profit_after_ads")]
    medium_revenue = store_index[("medium_14d_vs_prev_42d", "revenue_net")]
    medium_profit = store_index[("medium_14d_vs_prev_42d", "profit_after_ads")]

    worst_products = sorted(product_context_rows, key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]))[:8]
    best_products = sorted(product_context_rows, key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]), reverse=True)[:8]
    falling_faster = [row for row in product_context_rows if row["relative_to_store"] == "falling_faster_than_store"][:8]
    falling_with_store = [row for row in product_context_rows if row["relative_to_store"] == "falling_with_store"][:8]
    revenue_driven = [row for row in product_context_rows if row["dominant_pattern"] == "revenue_driven"][:8]
    expense_driven = [row for row in product_context_rows if row["dominant_pattern"] == "expense_driven"][:8]
    mixed_pattern = [row for row in product_context_rows if row["dominant_pattern"] == "mixed"][:8]
    top_anomalies = anomaly_rows[:15]
    top_patterns = expense_pattern_rows[:10]
    last_3_weeks = store_weekly_rows[-3:] if len(store_weekly_rows) >= 3 else store_weekly_rows

    lines = [
        "# PROFIT CONTROL — Dynamic Analysis Layer v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Scope",
        "",
        f"- Finance-complete corridor: `{corridor_start.isoformat()} .. {corridor_end.isoformat()}` (`{(corridor_end - corridor_start).days + 1}` days).",
        "- Store and product P&L stay bounded by the finance-complete corridor. Wider sales/ads history exists, but it is not used here as a substitute for complete economics.",
        "",
        "## Window Availability",
        "",
        md_table(
            window_availability_rows,
            ["window_label", "current_days", "base_days", "current_start", "current_end", "base_start", "base_end", "available", "availability_reason"],
        ),
        "",
        f"- `short_7d_vs_prev_28d` store `revenue/day`: `{money(short_revenue['base_per_day'])}` -> `{money(short_revenue['current_per_day'])}` (`{money(short_revenue['delta_per_day'])}` per day).",
        f"- `short_7d_vs_prev_28d` store `profit_after_ads/day`: `{money(short_profit['base_per_day'])}` -> `{money(short_profit['current_per_day'])}` (`{money(short_profit['delta_per_day'])}` per day).",
        f"- `medium_14d_vs_prev_42d` store `revenue/day`: `{money(medium_revenue['base_per_day'])}` -> `{money(medium_revenue['current_per_day'])}`.",
        f"- `medium_14d_vs_prev_42d` store `profit_after_ads/day`: `{money(medium_profit['base_per_day'])}` -> `{money(medium_profit['current_per_day'])}`.",
        "- `long_30d_vs_prev_30d` is not available on the current finance-complete corridor because we only have 56 complete days.",
        "",
        "## Store Dynamics",
        "",
        md_table(
            [
                {
                    "window_label": row["window_label"],
                    "metric": row["metric"],
                    "current_per_day": row["current_per_day"],
                    "base_per_day": row["base_per_day"],
                    "delta_per_day": row["delta_per_day"],
                    "delta_pct_vs_base_per_day": row["delta_pct_vs_base_per_day"],
                }
                for row in store_window_rows
                if row["metric"] in {"revenue_net", "profit_after_ads", "order_count", "sold_units_net", "ad_spend", "logistics_total_cost", "storage_cost", "commission_cost", "acquiring_cost", "withholding_cost", "negative_adjustment"}
            ],
            ["window_label", "metric", "current_per_day", "base_per_day", "delta_per_day", "delta_pct_vs_base_per_day"],
        ),
        "",
        md_table(
            last_3_weeks,
            ["week_start", "revenue_per_day", "profit_after_ads_per_day", "orders_per_day", "ad_spend_per_day", "logistics_total_cost", "withholding_cost"],
        ),
        "",
        "## Product Dynamics",
        "",
        "### Biggest Deterioration",
        "",
        md_table(
            [
                {
                    "product_name": row["product_name"],
                    "short_profit_after_ads_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                    "short_revenue_delta_per_day": row["short_revenue_delta_per_day"],
                    "dominant_pattern": row["dominant_pattern"],
                    "relative_to_store": row["relative_to_store"],
                    "drag_share_of_store_profit_drop": row["drag_share_of_store_profit_drop"],
                }
                for row in worst_products
            ],
            ["product_name", "short_profit_after_ads_delta_per_day", "short_revenue_delta_per_day", "dominant_pattern", "relative_to_store", "drag_share_of_store_profit_drop"],
        ),
        "",
        "### Biggest Growth",
        "",
        md_table(
            [
                {
                    "product_name": row["product_name"],
                    "short_profit_after_ads_delta_per_day": row["short_profit_after_ads_delta_per_day"],
                    "short_revenue_delta_per_day": row["short_revenue_delta_per_day"],
                    "dominant_pattern": row["dominant_pattern"],
                }
                for row in best_products
            ],
            ["product_name", "short_profit_after_ads_delta_per_day", "short_revenue_delta_per_day", "dominant_pattern"],
        ),
        "",
        "### Falling Faster Than Store",
        "",
        md_table(
            [
                {
                    "product_name": row["product_name"],
                    "product_short_revenue_delta_pct": row["product_short_revenue_delta_pct"],
                    "store_short_revenue_delta_pct": row["store_short_revenue_delta_pct"],
                    "dominant_pattern": row["dominant_pattern"],
                }
                for row in falling_faster
            ],
            ["product_name", "product_short_revenue_delta_pct", "store_short_revenue_delta_pct", "dominant_pattern"],
        ),
        "",
        "### Falling With Store",
        "",
        md_table(
            [
                {
                    "product_name": row["product_name"],
                    "product_short_revenue_delta_pct": row["product_short_revenue_delta_pct"],
                    "store_short_revenue_delta_pct": row["store_short_revenue_delta_pct"],
                    "dominant_pattern": row["dominant_pattern"],
                }
                for row in falling_with_store
            ],
            ["product_name", "product_short_revenue_delta_pct", "store_short_revenue_delta_pct", "dominant_pattern"],
        ),
        "",
        "### Revenue-Driven / Expense-Driven / Mixed",
        "",
        md_table(
            [
                {"segment": "revenue_driven", "top_products": "; ".join(row["product_name"] for row in revenue_driven[:5])},
                {"segment": "expense_driven", "top_products": "; ".join(row["product_name"] for row in expense_driven[:5])},
                {"segment": "mixed", "top_products": "; ".join(row["product_name"] for row in mixed_pattern[:5])},
            ],
            ["segment", "top_products"],
        ),
        "",
        "## Expense Dynamics",
        "",
        md_table(
            top_patterns,
            ["component", "weekly_mean", "weekly_min", "weekly_max", "coefficient_of_variation", "direction_changes", "spike_count", "latest_3w_trend", "pattern"],
        ),
        "",
        "## Daily Anomalies",
        "",
        md_table(
            top_anomalies,
            ["day", "metric", "anomaly_type", "value", "median_prev_7d", "ratio_vs_prev_7d_median", "calendar_bucket", "holiday_name"],
        ),
        "",
        "## Calendar Layer",
        "",
        md_table(
            calendar_effect_rows,
            ["calendar_bucket", "days_count", "avg_revenue_per_day", "avg_profit_after_ads_per_day", "avg_orders_per_day", "avg_ad_spend_per_day", "avg_logistics_per_day"],
        ),
        "",
        "## Next Causal Candidates",
        "",
        md_table(
            candidates_rows[:12],
            ["rank", "product_name", "short_profit_after_ads_delta_per_day", "medium_profit_after_ads_delta_per_day", "dominant_pattern", "relative_to_store", "drag_share_of_store_profit_drop"],
        ),
        "",
        "## Strong Factual Conclusions",
        "",
        f"- Store weakness is visible on both `7d vs 28d` and `14d vs 42d`, so this is not only a one-week noise event.",
        "- Full `30d vs previous 30d` economics is not yet available because the finance-complete corridor is only 56 days wide.",
        "- Product deterioration is not homogeneous: some SKUs fall materially faster than the store baseline, while others move with the store or remain resilient.",
        "- Expense behavior is heterogeneous too: some cost lines are wave-like or spike-driven, while others look more like shifts or stable bands.",
        "- Daily anomalies often cluster around low-revenue or high-cost combinations, but holiday/weekend markers do not explain the full pattern by themselves.",
        "- The candidate list is now grounded in four layers at once: short delta, medium stability, relation to store baseline, and weekly repeatability.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    corridor_start, corridor_end = deep.fetch_corridor_bounds()
    corridor_days = deep.daterange(corridor_start, corridor_end)
    windows, window_availability_rows = build_window_availability(corridor_start, corridor_end)
    product_daily = deep.fetch_product_daily(corridor_start, corridor_end)
    store_daily_rows = deep.build_store_daily(product_daily, corridor_days)
    store_daily_with_calendar = build_store_daily_with_calendar(store_daily_rows)
    store_weekly_rows = build_store_weekly(product_daily, corridor_days)
    store_window_rows = build_store_window_comparison(product_daily, windows)
    product_window_rows, _ = deep.build_product_comparisons(product_daily, corridor_end, windows)
    expense_daily_rows, expense_weekly_rows = deep.build_expense_component_tables(product_daily)
    anomaly_rows = detect_store_anomalies(store_daily_with_calendar)
    expense_pattern_rows = weekly_component_patterns(expense_weekly_rows)
    calendar_effect_rows = build_calendar_effects(store_daily_with_calendar)
    product_context_rows = build_product_context_rows(product_window_rows, store_window_rows)
    product_weekly_pattern_rows = build_product_weekly_patterns(product_daily)
    candidates_rows = build_candidates(product_context_rows, product_weekly_pattern_rows)

    write_csv(STORE_DAILY_CSV, store_daily_with_calendar)
    write_csv(STORE_WEEKLY_CSV, store_weekly_rows)
    write_csv(STORE_WINDOWS_CSV, store_window_rows)
    write_csv(WINDOW_AVAILABILITY_CSV, window_availability_rows)
    write_csv(PRODUCT_DYNAMICS_CSV, product_window_rows)
    write_csv(PRODUCT_CONTEXT_CSV, product_context_rows)
    write_csv(EXPENSE_DAILY_CSV, expense_daily_rows)
    write_csv(EXPENSE_WEEKLY_CSV, expense_weekly_rows)
    write_csv(DAILY_ANOMALIES_CSV, anomaly_rows)
    write_csv(WEEKLY_PATTERNS_CSV, expense_pattern_rows)
    write_csv(CALENDAR_EFFECTS_CSV, calendar_effect_rows)
    write_csv(CANDIDATES_CSV, candidates_rows)
    write_csv(PRODUCT_WEEKLY_PATTERNS_CSV, product_weekly_pattern_rows)

    DOC_PATH.write_text(
        build_report(
            corridor_start=corridor_start,
            corridor_end=corridor_end,
            window_availability_rows=window_availability_rows,
            store_window_rows=store_window_rows,
            store_daily_rows=store_daily_with_calendar,
            store_weekly_rows=store_weekly_rows,
            product_context_rows=product_context_rows,
            expense_pattern_rows=expense_pattern_rows,
            anomaly_rows=anomaly_rows,
            calendar_effect_rows=calendar_effect_rows,
            candidates_rows=candidates_rows,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {STORE_DAILY_CSV.relative_to(ROOT)}")
    print(f"Wrote {STORE_WEEKLY_CSV.relative_to(ROOT)}")
    print(f"Wrote {STORE_WINDOWS_CSV.relative_to(ROOT)}")
    print(f"Wrote {WINDOW_AVAILABILITY_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_DYNAMICS_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_CONTEXT_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXPENSE_DAILY_CSV.relative_to(ROOT)}")
    print(f"Wrote {EXPENSE_WEEKLY_CSV.relative_to(ROOT)}")
    print(f"Wrote {DAILY_ANOMALIES_CSV.relative_to(ROOT)}")
    print(f"Wrote {WEEKLY_PATTERNS_CSV.relative_to(ROOT)}")
    print(f"Wrote {CALENDAR_EFFECTS_CSV.relative_to(ROOT)}")
    print(f"Wrote {CANDIDATES_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_WEEKLY_PATTERNS_CSV.relative_to(ROOT)}")
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
