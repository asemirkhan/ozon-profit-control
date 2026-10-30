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
import scripts.build_profit_control_dynamic_analysis_layer_v1 as dyn

MOSCOW = ZoneInfo("Europe/Moscow")
MONEY = Decimal("0.01")
FOUR = Decimal("0.0001")
OUTPUT_DIR = ROOT / "exports" / "profit_control_calendar_seasonality_layer_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_calendar_seasonality_layer_v1.md"
STORE_DAILY_CSV = OUTPUT_DIR / "store_calendar_daily.csv"
STORE_TYPE_CSV = OUTPUT_DIR / "store_calendar_type_summary.csv"
STORE_SEGMENT_CSV = OUTPUT_DIR / "store_calendar_segment_summary.csv"
WINDOW_MIX_CSV = OUTPUT_DIR / "window_calendar_mix.csv"
WINDOW_COUNTS_CSV = OUTPUT_DIR / "window_calendar_counts.csv"
PRODUCT_ADJUSTED_CSV = OUTPUT_DIR / "product_calendar_adjusted_summary.csv"
ARCHETYPES_CSV = OUTPUT_DIR / "product_archetypes.csv"
ARCHETYPE_PROFILE_CSV = OUTPUT_DIR / "archetype_calendar_profiles.csv"
FAIRIES_CSV = OUTPUT_DIR / "fairies_calendar_check.csv"
REMAINING_DROP_CSV = OUTPUT_DIR / "products_remaining_weak_after_calendar.csv"
SEASONAL_CSV = OUTPUT_DIR / "products_likely_mostly_seasonal.csv"
CALENDAR_ANOMALIES_CSV = OUTPUT_DIR / "calendar_anomalies.csv"
WEEKLY_CALENDAR_CSV = OUTPUT_DIR / "weekly_calendar_patterns.csv"

HOLIDAY_DEFS = {
    date(2026, 2, 23): {"code": "23_feb", "name": "Defender_of_the_Fatherland_Day"},
    date(2026, 3, 8): {"code": "8_mar", "name": "International_Womens_Day"},
}
EVENT_WINDOW_DAYS = 3

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

PRODUCT_METRICS = [
    "revenue_net",
    "profit_after_ads",
    "order_count",
    "ad_spend",
    "logistics_total_cost",
    "withholding_cost",
]

RAW_NUMERIC_FIELDS = [
    "revenue_net",
    "sold_units_net",
    "sale_events",
    "return_events",
    "sold_order_count",
    "ordered_units",
    "order_events",
    "order_count",
    "ordered_gmv",
    "ad_spend",
    "impressions",
    "clicks",
    "ad_orders",
    "ad_attributed_revenue",
    "campaign_count",
    "commission_cost",
    "delivery_cost",
    "warehouse_logistics_tail_cost",
    "storage_cost",
    "acceptance_cost",
    "acquiring_cost",
    "penalty_cost",
    "withholding_cost",
    "positive_adjustment",
    "negative_adjustment",
]

DERIVED_ZERO_FIELDS = [
    "profit_before_ads",
    "profit_after_ads",
    "margin_after_ads",
    "profit_before_ads_margin",
    "logistics_total_cost",
    "average_check",
    "ctr",
    "cpc",
    "cr",
    "ad_cost_per_order",
    "ad_cost_per_revenue_rub",
    "ad_share_of_revenue",
    "ad_share_of_profit_before_ads",
    "logistics_per_order",
    "storage_per_order",
]


@dataclass(frozen=True)
class Archetype:
    key: str
    product_id: str
    product_name: str
    selection_reason: str


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


def build_calendar_metadata(corridor_days: list[date]) -> dict[date, dict[str, Any]]:
    metadata: dict[date, dict[str, Any]] = {}
    for day_value in corridor_days:
        weekday_idx = day_value.weekday()
        is_weekend = weekday_idx >= 5
        holiday_match = HOLIDAY_DEFS.get(day_value)
        holiday_code = holiday_match["code"] if holiday_match else ""
        holiday_name = holiday_match["name"] if holiday_match else ""
        segment = "ordinary_weekend" if is_weekend else "ordinary_weekday"
        day_type = segment
        event_code = ""
        event_phase = "ordinary"
        for holiday_day, holiday_meta in HOLIDAY_DEFS.items():
            if day_value == holiday_day:
                segment = f"holiday_{holiday_meta['code']}"
                day_type = "holiday_day"
                event_code = holiday_meta["code"]
                event_phase = "holiday"
                break
            if holiday_day - timedelta(days=EVENT_WINDOW_DAYS) <= day_value <= holiday_day - timedelta(days=1):
                segment = f"pre_{holiday_meta['code']}_period"
                day_type = "pre_holiday_day"
                event_code = holiday_meta["code"]
                event_phase = "pre_holiday"
                break
            if holiday_day + timedelta(days=1) <= day_value <= holiday_day + timedelta(days=EVENT_WINDOW_DAYS):
                segment = f"post_{holiday_meta['code']}_period"
                day_type = "post_holiday_day"
                event_code = holiday_meta["code"]
                event_phase = "post_holiday"
                break
        metadata[day_value] = {
            "day": day_value.isoformat(),
            "weekday_index": weekday_idx,
            "weekday_name": day_value.strftime("%A"),
            "is_weekend": is_weekend,
            "holiday_code": holiday_code,
            "holiday_name": holiday_name,
            "calendar_day_type": day_type,
            "calendar_segment": segment,
            "event_code": event_code,
            "event_phase": event_phase,
            "is_event_influenced": event_phase != "ordinary",
            "is_holiday": bool(holiday_code),
        }

    non_working_days = {day_value for day_value, meta in metadata.items() if meta["is_weekend"] or meta["is_holiday"]}
    sorted_days = sorted(non_working_days)
    clusters: list[list[date]] = []
    current: list[date] = []
    for day_value in sorted_days:
        if not current or day_value == current[-1] + timedelta(days=1):
            current.append(day_value)
        else:
            clusters.append(current)
            current = [day_value]
    if current:
        clusters.append(current)

    for cluster in clusters:
        cluster_label = ""
        if len(cluster) >= 3:
            holiday_codes = [metadata[day_value]["holiday_code"] for day_value in cluster if metadata[day_value]["holiday_code"]]
            if holiday_codes:
                cluster_label = f"long_weekend_{holiday_codes[0]}"
            else:
                cluster_label = f"long_weekend_{cluster[0].isoformat()}"
        for day_value in cluster:
            metadata[day_value]["long_weekend_label"] = cluster_label
            metadata[day_value]["long_weekend_days"] = len(cluster) if cluster_label else 0
    for day_value in corridor_days:
        metadata[day_value].setdefault("long_weekend_label", "")
        metadata[day_value].setdefault("long_weekend_days", 0)
    return metadata


def annotate_daily_rows(rows: list[dict[str, Any]], calendar_meta: dict[date, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        meta = calendar_meta[day_as_date(row["day"])]
        out.append({**row, **meta})
    return out


def fill_product_days(product_daily: list[dict[str, Any]], corridor_days: list[date]) -> list[dict[str, Any]]:
    by_product: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    templates: dict[str, dict[str, Any]] = {}
    for row in product_daily:
        product_id = row["product_id"]
        by_product[product_id][row["day"]] = row
        templates.setdefault(
            product_id,
            {
                "product_id": product_id,
                "product_name": row["product_name"],
                "brand": row["brand"],
                "category_name": row["category_name"],
                "snapshot_date": row.get("snapshot_date"),
                "stock_total": row.get("stock_total", Decimal("0.00")),
                "stock_available": row.get("stock_available", Decimal("0.00")),
                "stock_reserved": row.get("stock_reserved", Decimal("0.00")),
                "stock_defect": row.get("stock_defect", Decimal("0.00")),
                "is_unattributed_finance_bucket": row.get("is_unattributed_finance_bucket", False),
            },
        )

    filled: list[dict[str, Any]] = []
    for product_id, template in templates.items():
        for day_value in corridor_days:
            row = by_product[product_id].get(day_value)
            if row is not None:
                filled.append(row)
                continue
            zero_row = dict(template)
            zero_row["day"] = day_value
            for key in RAW_NUMERIC_FIELDS:
                zero_row[key] = 0
            for key in DERIVED_ZERO_FIELDS:
                zero_row[key] = Decimal("0.00") if "margin" not in key and key not in {"ctr", "cr", "ad_cost_per_revenue_rub", "ad_share_of_revenue", "ad_share_of_profit_before_ads"} else Decimal("0.0000")
            filled.append(zero_row)
    filled.sort(key=lambda item: (item["day"], item["product_id"]))
    return filled


def aggregate_avg_rows(rows: list[dict[str, Any]], metrics: list[str], label_key: str, label_value: str) -> dict[str, Any]:
    count = len(rows)
    out: dict[str, Any] = {label_key: label_value, "days_count": count}
    if count == 0:
        for metric in metrics:
            out[f"avg_{metric}_per_day"] = "0.00"
        return out
    for metric in metrics:
        values = [q2(row.get(metric, 0)) for row in rows]
        out[f"avg_{metric}_per_day"] = str(q2(sum(values) / Decimal(count)))
    return out


def build_store_calendar_summaries(store_daily_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in store_daily_rows:
        by_type[row["calendar_day_type"]].append(row)
        by_segment[row["calendar_segment"]].append(row)

    type_rows: list[dict[str, Any]] = []
    for label, items in sorted(by_type.items()):
        row = aggregate_avg_rows(items, STORE_METRICS, "calendar_day_type", label)
        row["long_weekend_days"] = sum(1 for item in items if item["long_weekend_label"])
        type_rows.append(row)

    segment_rows: list[dict[str, Any]] = []
    for label, items in sorted(by_segment.items()):
        row = aggregate_avg_rows(items, STORE_METRICS, "calendar_segment", label)
        first = items[0]
        row["event_phase"] = first["event_phase"]
        row["event_code"] = first["event_code"]
        row["long_weekend_days"] = sum(1 for item in items if item["long_weekend_label"])
        segment_rows.append(row)
    return type_rows, segment_rows


def mean_by_segment(rows: list[dict[str, Any]], metrics: list[str]) -> dict[str, dict[str, Decimal]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["calendar_segment"]].append(row)
    out: dict[str, dict[str, Decimal]] = {}
    for segment, items in grouped.items():
        out[segment] = {}
        count = len(items)
        for metric in metrics:
            out[segment][metric] = q2(sum(q2(item.get(metric, 0)) for item in items) / Decimal(count)) if count else Decimal("0.00")
    return out


def count_segments(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["calendar_segment"]] += 1
    return counts


def expected_per_day_from_mix(counts: dict[str, int], total_days: int, segment_means: dict[str, dict[str, Decimal]], metric: str) -> Decimal:
    if total_days <= 0:
        return Decimal("0.00")
    total = Decimal("0.00")
    for segment, count in counts.items():
        total += q2(segment_means.get(segment, {}).get(metric, Decimal("0.00"))) * Decimal(count)
    return q2(total / Decimal(total_days))


def build_window_calendar_tables(
    store_daily_rows: list[dict[str, Any]],
    windows: list[deep.Window],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segment_means = mean_by_segment(store_daily_rows, STORE_METRICS)
    mix_rows: list[dict[str, Any]] = []
    count_rows: list[dict[str, Any]] = []
    for window in windows:
        current_rows = [row for row in store_daily_rows if window.current_start <= day_as_date(row["day"]) <= window.current_end]
        base_rows = [row for row in store_daily_rows if window.base_start <= day_as_date(row["day"]) <= window.base_end]
        current_counts = count_segments(current_rows)
        base_counts = count_segments(base_rows)

        current_event_days = sum(1 for row in current_rows if row["is_event_influenced"])
        base_event_days = sum(1 for row in base_rows if row["is_event_influenced"])
        count_rows.append(
            {
                "window_label": window.label,
                "current_start": window.current_start.isoformat(),
                "current_end": window.current_end.isoformat(),
                "base_start": window.base_start.isoformat(),
                "base_end": window.base_end.isoformat(),
                "current_days": window.current_days,
                "base_days": window.base_days,
                "current_event_influenced_days": current_event_days,
                "base_event_influenced_days": base_event_days,
                "current_event_share": str(q4(safe_div(current_event_days, window.current_days))),
                "base_event_share": str(q4(safe_div(base_event_days, window.base_days))),
                "current_pre_23_feb_days": current_counts.get("pre_23_feb_period", 0),
                "base_pre_23_feb_days": base_counts.get("pre_23_feb_period", 0),
                "current_holiday_23_feb_days": current_counts.get("holiday_23_feb", 0),
                "base_holiday_23_feb_days": base_counts.get("holiday_23_feb", 0),
                "current_post_23_feb_days": current_counts.get("post_23_feb_period", 0),
                "base_post_23_feb_days": base_counts.get("post_23_feb_period", 0),
                "current_pre_8_mar_days": current_counts.get("pre_8_mar_period", 0),
                "base_pre_8_mar_days": base_counts.get("pre_8_mar_period", 0),
                "current_holiday_8_mar_days": current_counts.get("holiday_8_mar", 0),
                "base_holiday_8_mar_days": base_counts.get("holiday_8_mar", 0),
                "current_post_8_mar_days": current_counts.get("post_8_mar_period", 0),
                "base_post_8_mar_days": base_counts.get("post_8_mar_period", 0),
                "current_weekend_days": current_counts.get("ordinary_weekend", 0),
                "base_weekend_days": base_counts.get("ordinary_weekend", 0),
                "current_weekday_days": current_counts.get("ordinary_weekday", 0),
                "base_weekday_days": base_counts.get("ordinary_weekday", 0),
            }
        )

        for metric in STORE_METRICS:
            current_actual = q2(sum(q2(row[metric]) for row in current_rows) / Decimal(window.current_days))
            base_actual = q2(sum(q2(row[metric]) for row in base_rows) / Decimal(window.base_days))
            actual_delta = q2(current_actual - base_actual)
            expected_current = expected_per_day_from_mix(current_counts, window.current_days, segment_means, metric)
            expected_base = expected_per_day_from_mix(base_counts, window.base_days, segment_means, metric)
            calendar_mix_effect = q2(expected_current - expected_base)
            residual_after_calendar = q2(actual_delta - calendar_mix_effect)
            effect_share = (
                q4(abs(calendar_mix_effect) / abs(actual_delta))
                if actual_delta != 0
                else Decimal("0.0000")
            )
            mix_rows.append(
                {
                    "window_label": window.label,
                    "metric": metric,
                    "actual_current_per_day": str(current_actual),
                    "actual_base_per_day": str(base_actual),
                    "actual_delta_per_day": str(actual_delta),
                    "calendar_expected_current_per_day": str(expected_current),
                    "calendar_expected_base_per_day": str(expected_base),
                    "calendar_mix_effect_per_day": str(calendar_mix_effect),
                    "residual_after_calendar_per_day": str(residual_after_calendar),
                    "calendar_explained_share_abs": str(effect_share),
                }
            )
    return mix_rows, count_rows


def read_dynamic_context() -> list[dict[str, Any]]:
    path = ROOT / "exports" / "profit_control_dynamic_analysis_layer_v1" / "product_vs_store_context.csv"
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_product_segment_profiles(
    product_rows: list[dict[str, Any]],
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_rows:
        if row["product_id"] in selected_ids:
            grouped[row["product_id"]].append(row)
    rows: list[dict[str, Any]] = []
    for product_id, items in grouped.items():
        ordinary_rows = [item for item in items if item["calendar_segment"] == "ordinary_weekday"]
        ordinary_revenue = q2(sum(q2(item["revenue_net"]) for item in ordinary_rows) / Decimal(len(ordinary_rows))) if ordinary_rows else Decimal("0.00")
        ordinary_profit = q2(sum(q2(item["profit_after_ads"]) for item in ordinary_rows) / Decimal(len(ordinary_rows))) if ordinary_rows else Decimal("0.00")
        by_segment: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            by_segment[item["calendar_segment"]].append(item)
        for segment, segment_rows in sorted(by_segment.items()):
            count = len(segment_rows)
            revenue_avg = q2(sum(q2(item["revenue_net"]) for item in segment_rows) / Decimal(count))
            profit_avg = q2(sum(q2(item["profit_after_ads"]) for item in segment_rows) / Decimal(count))
            orders_avg = q2(sum(Decimal(str(item["order_count"])) for item in segment_rows) / Decimal(count))
            ads_avg = q2(sum(q2(item["ad_spend"]) for item in segment_rows) / Decimal(count))
            logistics_avg = q2(sum(q2(item["logistics_total_cost"]) for item in segment_rows) / Decimal(count))
            withhold_avg = q2(sum(q2(item["withholding_cost"]) for item in segment_rows) / Decimal(count))
            rows.append(
                {
                    "product_id": product_id,
                    "product_name": segment_rows[0]["product_name"],
                    "calendar_segment": segment,
                    "event_phase": segment_rows[0]["event_phase"],
                    "days_count": count,
                    "avg_revenue_per_day": str(revenue_avg),
                    "avg_profit_after_ads_per_day": str(profit_avg),
                    "avg_orders_per_day": str(orders_avg),
                    "avg_ad_spend_per_day": str(ads_avg),
                    "avg_logistics_per_day": str(logistics_avg),
                    "avg_withholding_per_day": str(withhold_avg),
                    "revenue_vs_ordinary_weekday_index": str(q4(safe_div(revenue_avg, ordinary_revenue))) if ordinary_revenue != 0 else "0.0000",
                    "profit_vs_ordinary_weekday_index": str(q4(safe_div(profit_avg, ordinary_profit))) if ordinary_profit != 0 else "0.0000",
                }
            )
    return rows


def build_product_calendar_adjusted(
    product_rows: list[dict[str, Any]],
    windows: list[deep.Window],
    store_mix_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    short_window = next(window for window in windows if window.label == "short_7d_vs_prev_28d")
    store_mix_index = {(row["window_label"], row["metric"]): row for row in store_mix_rows}
    store_residual_profit = q2(store_mix_index[("short_7d_vs_prev_28d", "profit_after_ads")]["residual_after_calendar_per_day"])
    store_actual_profit = q2(store_mix_index[("short_7d_vs_prev_28d", "profit_after_ads")]["actual_delta_per_day"])
    store_residual_revenue = q2(store_mix_index[("short_7d_vs_prev_28d", "revenue_net")]["residual_after_calendar_per_day"])

    by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_rows:
        if row["product_id"] == deep.UNATTRIBUTED_BUCKET:
            continue
        by_product[row["product_id"]].append(row)

    rows: list[dict[str, Any]] = []
    for product_id, items in by_product.items():
        segment_means = mean_by_segment(items, PRODUCT_METRICS)
        current_rows = [row for row in items if short_window.current_start <= day_as_date(row["day"]) <= short_window.current_end]
        base_rows = [row for row in items if short_window.base_start <= day_as_date(row["day"]) <= short_window.base_end]
        current_counts = count_segments(current_rows)
        base_counts = count_segments(base_rows)
        revenue_current = q2(sum(q2(row["revenue_net"]) for row in current_rows) / Decimal(short_window.current_days))
        revenue_base = q2(sum(q2(row["revenue_net"]) for row in base_rows) / Decimal(short_window.base_days))
        profit_current = q2(sum(q2(row["profit_after_ads"]) for row in current_rows) / Decimal(short_window.current_days))
        profit_base = q2(sum(q2(row["profit_after_ads"]) for row in base_rows) / Decimal(short_window.base_days))
        ads_current = q2(sum(q2(row["ad_spend"]) for row in current_rows) / Decimal(short_window.current_days))
        ads_base = q2(sum(q2(row["ad_spend"]) for row in base_rows) / Decimal(short_window.base_days))
        logistics_current = q2(sum(q2(row["logistics_total_cost"]) for row in current_rows) / Decimal(short_window.current_days))
        logistics_base = q2(sum(q2(row["logistics_total_cost"]) for row in base_rows) / Decimal(short_window.base_days))
        withholding_current = q2(sum(q2(row["withholding_cost"]) for row in current_rows) / Decimal(short_window.current_days))
        withholding_base = q2(sum(q2(row["withholding_cost"]) for row in base_rows) / Decimal(short_window.base_days))

        revenue_delta = q2(revenue_current - revenue_base)
        profit_delta = q2(profit_current - profit_base)
        ads_delta = q2(ads_current - ads_base)
        logistics_delta = q2(logistics_current - logistics_base)
        withholding_delta = q2(withholding_current - withholding_base)

        revenue_mix = q2(
            expected_per_day_from_mix(current_counts, short_window.current_days, segment_means, "revenue_net")
            - expected_per_day_from_mix(base_counts, short_window.base_days, segment_means, "revenue_net")
        )
        profit_mix = q2(
            expected_per_day_from_mix(current_counts, short_window.current_days, segment_means, "profit_after_ads")
            - expected_per_day_from_mix(base_counts, short_window.base_days, segment_means, "profit_after_ads")
        )
        revenue_residual = q2(revenue_delta - revenue_mix)
        profit_residual = q2(profit_delta - profit_mix)

        calendar_share = q4(abs(profit_mix) / abs(profit_delta)) if profit_delta != 0 else Decimal("0.0000")
        residual_profit_pct_vs_base = q4(safe_div(profit_residual, profit_base)) if profit_base != 0 else Decimal("0.0000")
        residual_revenue_pct_vs_base = q4(safe_div(revenue_residual, revenue_base)) if revenue_base != 0 else Decimal("0.0000")
        cost_pressure = max(ads_delta, Decimal("0.00")) + max(logistics_delta, Decimal("0.00")) + max(withholding_delta, Decimal("0.00"))
        revenue_pressure = max(-revenue_residual, Decimal("0.00"))

        if profit_delta >= 0:
            best_fit = "not_deteriorating_after_calendar"
        elif calendar_share >= Decimal("0.6000") and abs(profit_residual) <= abs(profit_delta) * Decimal("0.4000"):
            best_fit = "mostly_calendar_seasonal"
        elif revenue_pressure > cost_pressure * Decimal("1.5"):
            if store_actual_profit < 0 and abs(abs(profit_residual) - abs(store_residual_profit)) <= max(abs(store_residual_profit) * Decimal("0.25"), Decimal("300.00")):
                best_fit = "mostly_storewide_revenue_decline_after_calendar"
            else:
                best_fit = "local_demand_weakness_beyond_calendar"
        elif cost_pressure > revenue_pressure * Decimal("1.5"):
            best_fit = "cost_pressure_beyond_calendar"
        else:
            best_fit = "mixed_after_calendar"

        if store_residual_revenue < 0 and revenue_base != 0:
            store_residual_pct_vs_product_base = q4(safe_div(store_residual_revenue, revenue_base))
        else:
            store_residual_pct_vs_product_base = Decimal("0.0000")

        rows.append(
            {
                "product_id": product_id,
                "product_name": items[0]["product_name"],
                "category_name": items[0]["category_name"],
                "short_actual_revenue_delta_per_day": str(revenue_delta),
                "short_calendar_mix_effect_revenue_per_day": str(revenue_mix),
                "short_residual_revenue_after_calendar_per_day": str(revenue_residual),
                "short_actual_profit_delta_per_day": str(profit_delta),
                "short_calendar_mix_effect_profit_per_day": str(profit_mix),
                "short_residual_profit_after_calendar_per_day": str(profit_residual),
                "short_calendar_explained_share_profit_abs": str(calendar_share),
                "short_ads_delta_per_day": str(ads_delta),
                "short_logistics_delta_per_day": str(logistics_delta),
                "short_withholding_delta_per_day": str(withholding_delta),
                "residual_profit_pct_vs_base_per_day": str(residual_profit_pct_vs_base),
                "residual_revenue_pct_vs_base_per_day": str(residual_revenue_pct_vs_base),
                "store_residual_profit_after_calendar_per_day": str(store_residual_profit),
                "store_actual_profit_delta_per_day": str(store_actual_profit),
                "store_residual_revenue_after_calendar_per_day": str(store_residual_revenue),
                "store_residual_revenue_pct_vs_product_base": str(store_residual_pct_vs_product_base),
                "base_event_influenced_days": sum(1 for row in base_rows if row["is_event_influenced"]),
                "current_event_influenced_days": sum(1 for row in current_rows if row["is_event_influenced"]),
                "best_fit_explanation": best_fit,
            }
        )
    rows.sort(key=lambda item: q2(item["short_residual_profit_after_calendar_per_day"]))
    return rows


def build_weekly_calendar_patterns(store_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_week: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in store_daily_rows:
        by_week[deep.week_start(date.fromisoformat(row["day"]))].append(row)
    rows: list[dict[str, Any]] = []
    for week_value, items in sorted(by_week.items()):
        revenue_values = [q2(item["revenue_net"]) for item in items]
        profit_values = [q2(item["profit_after_ads"]) for item in items]
        withhold_values = [q2(item["withholding_cost"]) for item in items]
        ad_values = [q2(item["ad_spend"]) for item in items]
        rows.append(
            {
                "week_start": week_value.isoformat(),
                "days": len(items),
                "revenue_per_day": str(q2(sum(revenue_values) / Decimal(len(items)))),
                "profit_after_ads_per_day": str(q2(sum(profit_values) / Decimal(len(items)))),
                "withholding_per_day": str(q2(sum(withhold_values) / Decimal(len(items)))),
                "ad_spend_per_day": str(q2(sum(ad_values) / Decimal(len(items)))),
                "event_influenced_days": sum(1 for item in items if item["is_event_influenced"]),
                "long_weekend_days": sum(1 for item in items if item["long_weekend_label"]),
                "dominant_segments": ";".join(
                    segment
                    for segment, _count in sorted(
                        count_segments(items).items(),
                        key=lambda kv: (-kv[1], kv[0]),
                    )[:2]
                ),
            }
        )
    return rows


def detect_calendar_anomalies(store_daily_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    baselines: dict[str, dict[str, Decimal]] = defaultdict(dict)
    for day_type in sorted({row["calendar_day_type"] for row in store_daily_rows}):
        subset = [row for row in store_daily_rows if row["calendar_day_type"] == day_type]
        for metric in ["revenue_net", "profit_after_ads", "ad_spend", "withholding_cost", "storage_cost"]:
            values = [q2(item[metric]) for item in subset]
            baselines[day_type][metric] = q2(median(values)) if values else Decimal("0.00")
    for row in store_daily_rows:
        day_type = row["calendar_day_type"]
        for metric in ["revenue_net", "profit_after_ads", "ad_spend", "withholding_cost", "storage_cost"]:
            baseline = baselines[day_type][metric]
            value = q2(row[metric])
            ratio = safe_div(value, baseline) if baseline != 0 else Decimal("0.0000")
            if baseline > 0 and (ratio >= Decimal("1.8000") or ratio <= Decimal("0.5500")):
                rows.append(
                    {
                        "day": row["day"],
                        "calendar_day_type": day_type,
                        "calendar_segment": row["calendar_segment"],
                        "holiday_name": row["holiday_name"],
                        "metric": metric,
                        "value": str(value),
                        "segment_baseline_median": str(baseline),
                        "ratio_vs_type_baseline": str(q4(ratio)),
                        "anomaly_type": "spike" if ratio >= Decimal("1.8000") else "drop",
                    }
                )
    return rows


def choose_archetypes(
    dynamic_context_rows: list[dict[str, Any]],
    adjusted_rows: list[dict[str, Any]],
    product_rows: list[dict[str, Any]],
) -> list[Archetype]:
    by_context = {row["product_id"]: row for row in dynamic_context_rows}
    adjusted_index = {row["product_id"]: row for row in adjusted_rows}

    archetypes: list[Archetype] = []
    fairies_id = "669118440"
    archetypes.append(
        Archetype(
            key="fairies_main_candidate",
            product_id=fairies_id,
            product_name=by_context[fairies_id]["product_name"],
            selection_reason="gift_product_and_main_candidate",
        )
    )

    falling_with = [
        row for row in dynamic_context_rows
        if row["relative_to_store"] == "falling_with_store" and row["product_id"] != fairies_id
    ]
    falling_with.sort(key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]))
    if falling_with:
        row = falling_with[0]
        archetypes.append(
            Archetype(
                key="falling_with_store",
                product_id=row["product_id"],
                product_name=row["product_name"],
                selection_reason="declines_with_store_baseline",
            )
        )

    falling_faster = [
        row for row in dynamic_context_rows
        if row["relative_to_store"] == "falling_faster_than_store" and row["product_id"] != fairies_id
    ]
    falling_faster.sort(key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]))
    if falling_faster:
        row = falling_faster[0]
        archetypes.append(
            Archetype(
                key="falling_faster_than_store",
                product_id=row["product_id"],
                product_name=row["product_name"],
                selection_reason="declines_faster_than_store",
            )
        )

    resilient = [row for row in dynamic_context_rows if q2(row["short_profit_after_ads_delta_per_day"]) > 0]
    resilient.sort(key=lambda item: q2(item["short_profit_after_ads_delta_per_day"]), reverse=True)
    if resilient:
        row = resilient[0]
        archetypes.append(
            Archetype(
                key="resilient_or_growing",
                product_id=row["product_id"],
                product_name=row["product_name"],
                selection_reason="holds_or_grows_while_store_weakens",
            )
        )

    selected_ids = {item.product_id for item in archetypes}
    weekly_expense_by_product: dict[str, list[Decimal]] = defaultdict(list)
    tmp: dict[tuple[str, date], Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in product_rows:
        if row["product_id"] == deep.UNATTRIBUTED_BUCKET:
            continue
        tmp[(row["product_id"], deep.week_start(day_as_date(row["day"])))] += (
            q2(row["ad_spend"]) + q2(row["logistics_total_cost"]) + q2(row["withholding_cost"])
        )
    for (product_id, _week_start), amount in tmp.items():
        weekly_expense_by_product[product_id].append(q2(amount))

    best_wave_id = ""
    best_wave_score = Decimal("-1")
    for product_id, values in weekly_expense_by_product.items():
        if product_id in selected_ids or len(values) < 4:
            continue
        mean = sum(values) / Decimal(len(values))
        if mean <= Decimal("100.00"):
            continue
        variance = sum((value - mean) * (value - mean) for value in values) / Decimal(len(values))
        std = variance.sqrt() if variance >= 0 else Decimal("0")
        cv = safe_div(std, mean)
        if cv > best_wave_score:
            best_wave_score = cv
            best_wave_id = product_id
    if best_wave_id:
        archetypes.append(
            Archetype(
                key="expense_wave_case",
                product_id=best_wave_id,
                product_name=by_context[best_wave_id]["product_name"],
                selection_reason="highest_weekly_cost_volatility",
            )
        )
    return archetypes


def build_fairies_check(
    adjusted_rows: list[dict[str, Any]],
    archetype_profile_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fairies_id = "669118440"
    adjusted = next(row for row in adjusted_rows if row["product_id"] == fairies_id)
    profiles = [row for row in archetype_profile_rows if row["product_id"] == fairies_id]
    profile_index = {row["calendar_segment"]: row for row in profiles}
    rows = [
        {
            "check_name": "pre_8_mar_revenue_index_vs_ordinary_weekday",
            "value": profile_index.get("pre_8_mar_period", {}).get("revenue_vs_ordinary_weekday_index", "0.0000"),
        },
        {
            "check_name": "holiday_8_mar_revenue_index_vs_ordinary_weekday",
            "value": profile_index.get("holiday_8_mar", {}).get("revenue_vs_ordinary_weekday_index", "0.0000"),
        },
        {
            "check_name": "post_8_mar_revenue_index_vs_ordinary_weekday",
            "value": profile_index.get("post_8_mar_period", {}).get("revenue_vs_ordinary_weekday_index", "0.0000"),
        },
        {
            "check_name": "short_actual_revenue_delta_per_day",
            "value": adjusted["short_actual_revenue_delta_per_day"],
        },
        {
            "check_name": "short_calendar_mix_effect_revenue_per_day",
            "value": adjusted["short_calendar_mix_effect_revenue_per_day"],
        },
        {
            "check_name": "short_residual_revenue_after_calendar_per_day",
            "value": adjusted["short_residual_revenue_after_calendar_per_day"],
        },
        {
            "check_name": "short_actual_profit_delta_per_day",
            "value": adjusted["short_actual_profit_delta_per_day"],
        },
        {
            "check_name": "short_calendar_mix_effect_profit_per_day",
            "value": adjusted["short_calendar_mix_effect_profit_per_day"],
        },
        {
            "check_name": "short_residual_profit_after_calendar_per_day",
            "value": adjusted["short_residual_profit_after_calendar_per_day"],
        },
        {
            "check_name": "best_fit_explanation",
            "value": adjusted["best_fit_explanation"],
        },
    ]
    return rows


def build_report(
    *,
    corridor_start: date,
    corridor_end: date,
    store_type_rows: list[dict[str, Any]],
    store_segment_rows: list[dict[str, Any]],
    window_mix_rows: list[dict[str, Any]],
    window_count_rows: list[dict[str, Any]],
    adjusted_rows: list[dict[str, Any]],
    archetypes: list[Archetype],
    archetype_profile_rows: list[dict[str, Any]],
    fairies_rows: list[dict[str, Any]],
    remaining_drop_rows: list[dict[str, Any]],
    seasonal_rows: list[dict[str, Any]],
    weekly_pattern_rows: list[dict[str, Any]],
    anomaly_rows: list[dict[str, Any]],
) -> str:
    mix_index = {(row["window_label"], row["metric"]): row for row in window_mix_rows}
    count_index = {row["window_label"]: row for row in window_count_rows}
    short_revenue = mix_index[("short_7d_vs_prev_28d", "revenue_net")]
    short_profit = mix_index[("short_7d_vs_prev_28d", "profit_after_ads")]
    medium_revenue = mix_index[("medium_14d_vs_prev_42d", "revenue_net")]
    medium_profit = mix_index[("medium_14d_vs_prev_42d", "profit_after_ads")]
    fairies_adjusted = next(row for row in adjusted_rows if row["product_id"] == "669118440")
    archetype_lookup = {item.product_id: item.key for item in archetypes}
    archetype_table = [
        {
            "archetype_key": item.key,
            "product_name": item.product_name,
            "selection_reason": item.selection_reason,
        }
        for item in archetypes
    ]
    archetype_focus = [
        {
            "archetype_key": archetype_lookup[row["product_id"]],
            "product_name": row["product_name"],
            "calendar_segment": row["calendar_segment"],
            "avg_revenue_per_day": row["avg_revenue_per_day"],
            "avg_profit_after_ads_per_day": row["avg_profit_after_ads_per_day"],
            "revenue_vs_ordinary_weekday_index": row["revenue_vs_ordinary_weekday_index"],
            "profit_vs_ordinary_weekday_index": row["profit_vs_ordinary_weekday_index"],
        }
        for row in archetype_profile_rows
        if row["product_id"] in archetype_lookup and row["calendar_segment"] in {"ordinary_weekday", "ordinary_weekend", "pre_23_feb_period", "holiday_23_feb", "post_23_feb_period", "pre_8_mar_period", "holiday_8_mar", "post_8_mar_period"}
    ]
    focus_sorted = sorted(archetype_focus, key=lambda item: (item["archetype_key"], item["calendar_segment"]))
    lines = [
        "# PROFIT CONTROL — Calendar And Seasonality Layer v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Scope And Method",
        "",
        f"- Finance-complete corridor: `{corridor_start.isoformat()} .. {corridor_end.isoformat()}` (`{(corridor_end - corridor_start).days + 1}` days).",
        "- Calendar typing is split into ordinary weekday, ordinary weekend, pre-holiday day, holiday day, and post-holiday day.",
        "- Holiday-event windows use `3` days before and `3` days after each named holiday: `23 February` and `8 March`.",
        "- `long_weekend` is marked separately when a holiday/weekend cluster spans at least `3` consecutive non-working days inside the corridor.",
        "- Calendar-adjusted deltas compare actual short-window deterioration with the delta expected from a different calendar mix alone.",
        "",
        "## Store Calendar Layer",
        "",
        md_table(
            store_type_rows,
            [
                "calendar_day_type",
                "days_count",
                "avg_revenue_net_per_day",
                "avg_profit_after_ads_per_day",
                "avg_order_count_per_day",
                "avg_sold_units_net_per_day",
                "avg_average_check_per_day",
                "avg_ad_spend_per_day",
                "avg_logistics_total_cost_per_day",
                "avg_storage_cost_per_day",
                "avg_withholding_cost_per_day",
            ],
        ),
        "",
        "### Event Windows",
        "",
        md_table(
            store_segment_rows,
            [
                "calendar_segment",
                "days_count",
                "avg_revenue_net_per_day",
                "avg_profit_after_ads_per_day",
                "avg_order_count_per_day",
                "avg_ad_spend_per_day",
                "avg_logistics_total_cost_per_day",
                "avg_storage_cost_per_day",
                "avg_withholding_cost_per_day",
            ],
        ),
        "",
        "### Window Calendar Mix",
        "",
        md_table(
            window_count_rows,
            [
                "window_label",
                "current_event_influenced_days",
                "base_event_influenced_days",
                "current_event_share",
                "base_event_share",
                "current_pre_8_mar_days",
                "base_pre_8_mar_days",
                "current_holiday_8_mar_days",
                "base_holiday_8_mar_days",
                "current_post_8_mar_days",
                "base_post_8_mar_days",
            ],
        ),
        "",
        md_table(
            [row for row in window_mix_rows if row["metric"] in {"revenue_net", "profit_after_ads", "order_count", "sold_units_net"}],
            [
                "window_label",
                "metric",
                "actual_delta_per_day",
                "calendar_mix_effect_per_day",
                "residual_after_calendar_per_day",
                "calendar_explained_share_abs",
            ],
        ),
        "",
        f"- `short_7d_vs_prev_28d` store revenue delta is `{money(short_revenue['actual_delta_per_day'])}` per day; calendar mix alone explains `{money(short_revenue['calendar_mix_effect_per_day'])}` per day, leaving `{money(short_revenue['residual_after_calendar_per_day'])}` per day after adjustment.",
        f"- `short_7d_vs_prev_28d` store profit-after-ads delta is `{money(short_profit['actual_delta_per_day'])}` per day; calendar mix alone explains `{money(short_profit['calendar_mix_effect_per_day'])}` per day, leaving `{money(short_profit['residual_after_calendar_per_day'])}` per day after adjustment.",
        f"- `medium_14d_vs_prev_42d` revenue delta is `{money(medium_revenue['actual_delta_per_day'])}` per day with `{money(medium_revenue['calendar_mix_effect_per_day'])}` per day explained by calendar mix.",
        f"- `medium_14d_vs_prev_42d` profit-after-ads delta is `{money(medium_profit['actual_delta_per_day'])}` per day with `{money(medium_profit['calendar_mix_effect_per_day'])}` per day explained by calendar mix.",
        "",
        "## Product Layer With Calendar Adjustment",
        "",
        md_table(archetype_table, ["archetype_key", "product_name", "selection_reason"]),
        "",
        md_table(
            adjusted_rows[:12],
            [
                "product_name",
                "short_actual_profit_delta_per_day",
                "short_calendar_mix_effect_profit_per_day",
                "short_residual_profit_after_calendar_per_day",
                "short_actual_revenue_delta_per_day",
                "short_residual_revenue_after_calendar_per_day",
                "best_fit_explanation",
            ],
        ),
        "",
        "### Archetype Calendar Profiles",
        "",
        md_table(
            focus_sorted,
            [
                "archetype_key",
                "product_name",
                "calendar_segment",
                "avg_revenue_per_day",
                "avg_profit_after_ads_per_day",
                "revenue_vs_ordinary_weekday_index",
                "profit_vs_ordinary_weekday_index",
            ],
        ),
        "",
        "## Fairies Check",
        "",
        md_table(fairies_rows, ["check_name", "value"]),
        "",
        f"- `Феи Единороги` calendar-adjusted profit residual is `{money(fairies_adjusted['short_residual_profit_after_calendar_per_day'])}` per day and calendar-adjusted revenue residual is `{money(fairies_adjusted['short_residual_revenue_after_calendar_per_day'])}` per day.",
        f"- Best-fit explanation for `Феи` after calendar correction is `{fairies_adjusted['best_fit_explanation']}`.",
        "",
        "## Products Still Weak After Calendar",
        "",
        md_table(
            remaining_drop_rows[:12],
            [
                "product_name",
                "short_residual_profit_after_calendar_per_day",
                "short_residual_revenue_after_calendar_per_day",
                "best_fit_explanation",
            ],
        ),
        "",
        "## Products Likely Mostly Seasonal",
        "",
        md_table(
            seasonal_rows[:12],
            [
                "product_name",
                "short_actual_profit_delta_per_day",
                "short_calendar_mix_effect_profit_per_day",
                "short_residual_profit_after_calendar_per_day",
                "best_fit_explanation",
            ],
        ),
        "",
        "## Weekly And Daily Calendar Signals",
        "",
        md_table(
            weekly_pattern_rows[-6:],
            ["week_start", "revenue_per_day", "profit_after_ads_per_day", "withholding_per_day", "event_influenced_days", "dominant_segments"],
        ),
        "",
        md_table(
            anomaly_rows[:15],
            ["day", "calendar_day_type", "calendar_segment", "metric", "value", "segment_baseline_median", "ratio_vs_type_baseline", "anomaly_type"],
        ),
        "",
        "## Internal Analytical Conclusion",
        "",
        "- The base windows are not calendar-clean by default if they contain materially more pre-holiday and holiday-influenced days than the current window.",
        "- Calendar mix explains part of the March weakness, but not all of it; the residual after calendar correction is the stronger signal for the next causal stage.",
        "- Some products remain weak even after the holiday adjustment, while others look much closer to a seasonal normalization.",
        "- `Феи` should only move to the next causal stage if its residual weakness remains after the holiday mix is stripped out.",
        "- Any product that is mostly explained by holiday mix should not yet be treated as a local product problem without the calendar correction.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    load_dotenv(ROOT / ".env")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    corridor_start, corridor_end = deep.fetch_corridor_bounds()
    corridor_days = deep.daterange(corridor_start, corridor_end)
    calendar_meta = build_calendar_metadata(corridor_days)

    product_daily_raw = deep.fetch_product_daily(corridor_start, corridor_end)
    product_daily_full = fill_product_days(product_daily_raw, corridor_days)
    product_daily_annotated = annotate_daily_rows(product_daily_full, calendar_meta)

    store_daily_raw = deep.build_store_daily(product_daily_raw, corridor_days)
    store_daily_annotated = annotate_daily_rows(store_daily_raw, calendar_meta)

    windows = deep.build_windows(corridor_start, corridor_end)
    store_type_rows, store_segment_rows = build_store_calendar_summaries(store_daily_annotated)
    window_mix_rows, window_count_rows = build_window_calendar_tables(store_daily_annotated, windows)
    adjusted_rows = build_product_calendar_adjusted(product_daily_annotated, windows, window_mix_rows)
    dynamic_context_rows = read_dynamic_context()
    archetypes = choose_archetypes(dynamic_context_rows, adjusted_rows, product_daily_annotated)
    archetype_profile_rows = build_product_segment_profiles(product_daily_annotated, {item.product_id for item in archetypes})
    fairies_rows = build_fairies_check(adjusted_rows, archetype_profile_rows)
    anomaly_rows = detect_calendar_anomalies(store_daily_annotated)
    weekly_pattern_rows = build_weekly_calendar_patterns(store_daily_annotated)

    remaining_drop_rows = [
        row for row in adjusted_rows
        if q2(row["short_residual_profit_after_calendar_per_day"]) < 0
        and row["best_fit_explanation"] != "mostly_calendar_seasonal"
    ]
    seasonal_rows = [row for row in adjusted_rows if row["best_fit_explanation"] == "mostly_calendar_seasonal"]

    write_csv(STORE_DAILY_CSV, store_daily_annotated)
    write_csv(STORE_TYPE_CSV, store_type_rows)
    write_csv(STORE_SEGMENT_CSV, store_segment_rows)
    write_csv(WINDOW_MIX_CSV, window_mix_rows)
    write_csv(WINDOW_COUNTS_CSV, window_count_rows)
    write_csv(PRODUCT_ADJUSTED_CSV, adjusted_rows)
    write_csv(
        ARCHETYPES_CSV,
        [
            {
                "archetype_key": item.key,
                "product_id": item.product_id,
                "product_name": item.product_name,
                "selection_reason": item.selection_reason,
            }
            for item in archetypes
        ],
    )
    write_csv(ARCHETYPE_PROFILE_CSV, archetype_profile_rows)
    write_csv(FAIRIES_CSV, fairies_rows)
    write_csv(REMAINING_DROP_CSV, remaining_drop_rows)
    write_csv(SEASONAL_CSV, seasonal_rows)
    write_csv(CALENDAR_ANOMALIES_CSV, anomaly_rows)
    write_csv(WEEKLY_CALENDAR_CSV, weekly_pattern_rows)

    DOC_PATH.write_text(
        build_report(
            corridor_start=corridor_start,
            corridor_end=corridor_end,
            store_type_rows=store_type_rows,
            store_segment_rows=store_segment_rows,
            window_mix_rows=window_mix_rows,
            window_count_rows=window_count_rows,
            adjusted_rows=adjusted_rows,
            archetypes=archetypes,
            archetype_profile_rows=archetype_profile_rows,
            fairies_rows=fairies_rows,
            remaining_drop_rows=remaining_drop_rows,
            seasonal_rows=seasonal_rows,
            weekly_pattern_rows=weekly_pattern_rows,
            anomaly_rows=anomaly_rows,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {STORE_DAILY_CSV.relative_to(ROOT)}")
    print(f"Wrote {STORE_TYPE_CSV.relative_to(ROOT)}")
    print(f"Wrote {STORE_SEGMENT_CSV.relative_to(ROOT)}")
    print(f"Wrote {WINDOW_MIX_CSV.relative_to(ROOT)}")
    print(f"Wrote {WINDOW_COUNTS_CSV.relative_to(ROOT)}")
    print(f"Wrote {PRODUCT_ADJUSTED_CSV.relative_to(ROOT)}")
    print(f"Wrote {ARCHETYPES_CSV.relative_to(ROOT)}")
    print(f"Wrote {ARCHETYPE_PROFILE_CSV.relative_to(ROOT)}")
    print(f"Wrote {FAIRIES_CSV.relative_to(ROOT)}")
    print(f"Wrote {REMAINING_DROP_CSV.relative_to(ROOT)}")
    print(f"Wrote {SEASONAL_CSV.relative_to(ROOT)}")
    print(f"Wrote {CALENDAR_ANOMALIES_CSV.relative_to(ROOT)}")
    print(f"Wrote {WEEKLY_CALENDAR_CSV.relative_to(ROOT)}")
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
