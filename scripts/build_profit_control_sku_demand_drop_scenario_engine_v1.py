from __future__ import annotations

import csv
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

CORE_DIR = ROOT / "exports" / "wb_product_core_engine_v1"
FINAL_DIR = ROOT / "exports" / "wb_final_causal_pack_v1"
RECOVERY_DIR = ROOT / "exports" / "wb_missing_data_recovery_pass_v1"
DYNAMIC_DIR = ROOT / "exports" / "profit_control_dynamic_analysis_layer_v1"
CAUSAL_V1_DIR = ROOT / "exports" / "profit_control_weak_sku_causal_drilldown_v1"

OUTPUT_DIR = ROOT / "exports" / "profit_control_sku_demand_drop_scenario_engine_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_sku_demand_drop_scenario_engine_v1.md"

INPUT_LAYER_CSV = OUTPUT_DIR / "sku_scenario_input_layer.csv"
STATE_CLASSIFICATION_CSV = OUTPUT_DIR / "sku_state_classification.csv"
SCENARIO_A_CSV = OUTPUT_DIR / "sku_scenario_A_watch.csv"
SCENARIO_B_CSV = OUTPUT_DIR / "sku_scenario_B_supply_slowdown.csv"
SCENARIO_C_CSV = OUTPUT_DIR / "sku_scenario_C_ads_acceleration.csv"
SCENARIO_D_CSV = OUTPUT_DIR / "sku_scenario_D_ads_plus_card_fix.csv"
SCENARIO_E_CSV = OUTPUT_DIR / "sku_scenario_E_card_fix_only.csv"
SCENARIO_F_CSV = OUTPUT_DIR / "sku_scenario_F_availability_fix.csv"
SCENARIO_G_CSV = OUTPUT_DIR / "sku_scenario_G_new_normal.csv"
SCENARIO_H_CSV = OUTPUT_DIR / "sku_scenario_H_clearance_mode.csv"
SCENARIO_COMPARISON_CSV = OUTPUT_DIR / "sku_scenario_comparison.csv"
BEST_CHOICE_CSV = OUTPUT_DIR / "sku_scenario_best_choice.csv"
SWITCH_RULES_CSV = OUTPUT_DIR / "sku_scenario_switch_rules.csv"
CLIENT_SUMMARY_CSV = OUTPUT_DIR / "sku_client_scenario_summary.csv"
MONEY_AT_RISK_CSV = OUTPUT_DIR / "sku_money_at_risk_summary.csv"
STOCK_EXTENSION_CSV = OUTPUT_DIR / "sku_stock_extension_summary.csv"

SELECTED_SKUS = [
    "669118440",
    "340692643",
    "518796478",
    "73090372",
    "240017726",
    "72702996",
]

SCENARIOS = [
    ("A", "watch", "наблюдать"),
    ("B", "supply_slowdown", "сократить будущие поставки"),
    ("C", "ads_acceleration", "ускорить продажи рекламой"),
    ("D", "ads_plus_card_fix", "ускорить продажи рекламой и параллельно лечить карточку"),
    ("E", "card_fix_only", "лечить карточку без усиления рекламы"),
    ("F", "availability_fix", "чинить доступность и распределение"),
    ("G", "new_normal", "принять новый уровень спроса"),
    ("H", "clearance_mode", "агрессивная распродажа"),
]
SCENARIO_CSV_MAP = {
    "A": SCENARIO_A_CSV,
    "B": SCENARIO_B_CSV,
    "C": SCENARIO_C_CSV,
    "D": SCENARIO_D_CSV,
    "E": SCENARIO_E_CSV,
    "F": SCENARIO_F_CSV,
    "G": SCENARIO_G_CSV,
    "H": SCENARIO_H_CSV,
}
MODE_SHARES = {
    "conservative": 0.25,
    "working": 0.45,
    "optimistic": 0.65,
}
MODE_CARD_SHARES = {
    "conservative": 0.08,
    "working": 0.18,
    "optimistic": 0.30,
}
MODE_AVAILABILITY_SHARES = {
    "availability_risk_present": {"conservative": 0.15, "working": 0.30, "optimistic": 0.45},
    "availability_snapshot_only": {"conservative": 0.05, "working": 0.12, "optimistic": 0.20},
    "no_clear_availability_signal": {"conservative": 0.00, "working": 0.00, "optimistic": 0.00},
}
MODE_CLEARANCE_NEW_SHARE = {
    "conservative": 0.35,
    "working": 0.55,
    "optimistic": 0.75,
}
MODE_CAPITAL_COST_RATE = {
    "conservative": 0.008,
    "working": 0.012,
    "optimistic": 0.016,
}
CONFIDENCE_SCORE = {"high": 0.85, "medium": 0.60, "weak": 0.35, "low": 0.30}
MOSCOW = ZoneInfo("Europe/Moscow")


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


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


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


def as_text(value: Any) -> str:
    return str(value or "").strip()


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def short_money(value: Any) -> str:
    parsed = as_float(value)
    if parsed is None:
        return ""
    return f"{parsed:,.0f}".replace(",", " ")


def safe_div(numerator: float | None, denominator: float | None, default: float = 0.0) -> float:
    if numerator is None or denominator in (None, 0):
        return default
    return numerator / denominator


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def parsed_or(value: Any, default: float = 0.0) -> float:
    parsed = as_float(value)
    return parsed if parsed is not None else default


def parse_count_from_gap(text: str) -> int:
    raw = as_text(text)
    if "=" not in raw:
        return 0
    try:
        return int(raw.split("=")[-1])
    except ValueError:
        return 0


def money_fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}".replace(",", " ")


def signed_money_fmt(value: float | None) -> str:
    if value is None:
        return ""
    prefix = "+" if value > 0 else ""
    return prefix + money_fmt(value)


def pct_fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value * 100:.1f}%"


def load_inputs() -> dict[str, list[dict[str, str]]]:
    return {
        "internal": load_csv(CORE_DIR / "sku_internal_full_layer.csv"),
        "paid_nonpaid": load_csv(CORE_DIR / "sku_paid_vs_nonpaid.csv"),
        "availability": load_csv(CORE_DIR / "sku_availability_layer.csv"),
        "dynamics": load_csv(DYNAMIC_DIR / "product_dynamics_summary.csv"),
        "causal_v1": load_csv(CAUSAL_V1_DIR / "sku_causal_summary.csv"),
        "region_breakdown": load_csv(CAUSAL_V1_DIR / "sku_region_breakdown.csv"),
        "warehouse_breakdown": load_csv(CAUSAL_V1_DIR / "sku_warehouse_breakdown.csv"),
        "final_profiles": load_csv(FINAL_DIR / "final_sku_causal_profiles.csv"),
        "card_cmp": load_csv(FINAL_DIR / "sku_card_comparison.csv"),
        "adv_map": load_csv(FINAL_DIR / "sku_competitor_advantage_map.csv"),
        "recovery_profiles": load_csv(RECOVERY_DIR / "updated_partial_sku_causal_profiles.csv"),
        "manual_confirmed": load_csv(RECOVERY_DIR / "manual_anchor_confirmed_competitors.csv"),
        "card_recovery": load_csv(RECOVERY_DIR / "competitor_card_recovery_comparison.csv"),
    }


def index_by(rows: list[dict[str, str]], key: str, *, filter_fn=None) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        if filter_fn and not filter_fn(row):
            continue
        indexed[as_text(row.get(key))] = row
    return indexed


def group_by(rows: list[dict[str, str]], key: str, *, filter_fn=None) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if filter_fn and not filter_fn(row):
            continue
        grouped[as_text(row.get(key))].append(row)
    return grouped


def find_own_card_rows(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return index_by(rows, "own_nm_id", filter_fn=lambda row: as_text(row.get("card_role")) == "own")


def best_top_contributors(rows: list[dict[str, str]], value_key: str, top_n: int = 3) -> tuple[str, float, float]:
    filtered = []
    total_negative = 0.0
    for row in rows:
        delta = parsed_or(row.get(value_key))
        if delta < 0:
            filtered.append((as_text(row.get("dimension_value")), delta))
            total_negative += abs(delta)
    filtered.sort(key=lambda item: item[1])
    label = "; ".join(f"{name} ({money_fmt(delta)})" for name, delta in filtered[:top_n])
    worst = filtered[0][1] if filtered else 0.0
    return label, worst, total_negative


def build_external_summary(
    sku: str,
    own_card: dict[str, str] | None,
    adv_rows: list[dict[str, str]],
    manual_rows: list[dict[str, str]],
    recovery_card_rows: list[dict[str, str]],
    final_profile: dict[str, str] | None,
    recovery_profile: dict[str, str] | None,
) -> dict[str, Any]:
    own_price = parsed_or((own_card or {}).get("current_sale_price"))
    own_rating = parsed_or((own_card or {}).get("rating"))
    own_feedbacks = parsed_or((own_card or {}).get("feedbacks"))

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in adv_rows:
        nm_id = as_text(row.get("candidate_nm_id"))
        if not nm_id:
            continue
        seen.add(nm_id)
        candidates.append(
            {
                "candidate_nm_id": nm_id,
                "candidate_name": as_text(row.get("candidate_name")),
                "source": as_text(row.get("candidate_source")) or "auto_capture",
                "comparability_status": as_text(row.get("comparability_status")) or "confirmed_comparable",
                "price": None,
                "rating": None,
                "feedbacks": None,
                "higher_in_search": as_text(row.get("higher_in_search")) == "yes",
                "stronger_by_card_overall": as_text(row.get("stronger_by_card_overall")) == "yes",
                "danger_type": as_text(row.get("danger_type")),
            }
        )

    recovery_lookup = {
        as_text(row.get("candidate_nm_id")): row
        for row in recovery_card_rows
        if as_text(row.get("own_nm_id")) == sku
    }

    for row in manual_rows:
        nm_id = as_text(row.get("candidate_nm_id"))
        if not nm_id or nm_id in seen:
            pass
        base = {
            "candidate_nm_id": nm_id,
            "candidate_name": as_text(row.get("candidate_name")),
            "source": as_text(row.get("candidate_source")) or "manual_curated_hint",
            "comparability_status": as_text(row.get("comparability_status")) or "likely_comparable",
            "price": parsed_or(row.get("candidate_price")),
            "rating": parsed_or(row.get("candidate_rating")),
            "feedbacks": parsed_or(row.get("candidate_feedbacks")),
            "higher_in_search": as_int(row.get("higher_than_own_count")) > 0,
            "stronger_by_card_overall": False,
            "danger_type": "",
        }
        rec = recovery_lookup.get(nm_id)
        if rec:
            gaps = []
            if as_text(rec.get("price_gap")) == "competitor_lower_price":
                gaps.append("price")
            if as_text(rec.get("rating_gap")) == "competitor_higher":
                gaps.append("rating")
            if as_text(rec.get("review_gap")) == "competitor_more_reviews":
                gaps.append("reviews")
            if as_text(rec.get("content_gap")) == "competitor_richer":
                gaps.append("content")
            if as_text(rec.get("visual_gap")) == "competitor_richer":
                gaps.append("visual")
            base["danger_type"] = "; ".join(gaps)
            base["stronger_by_card_overall"] = as_text(rec.get("substantial_or_cosmetic")) == "substantial"
        if nm_id not in seen:
            candidates.append(base)
            seen.add(nm_id)

    for row in recovery_card_rows:
        nm_id = as_text(row.get("candidate_nm_id"))
        if nm_id in seen:
            continue
        gaps = []
        if as_text(row.get("price_gap")) == "competitor_lower_price":
            gaps.append("price")
        if as_text(row.get("rating_gap")) == "competitor_higher":
            gaps.append("rating")
        if as_text(row.get("review_gap")) == "competitor_more_reviews":
            gaps.append("reviews")
        if as_text(row.get("content_gap")) == "competitor_richer":
            gaps.append("content")
        if as_text(row.get("visual_gap")) == "competitor_richer":
            gaps.append("visual")
        candidates.append(
            {
                "candidate_nm_id": nm_id,
                "candidate_name": as_text(row.get("candidate_name")),
                "source": "recovery_card_layer",
                "comparability_status": "confirmed_comparable",
                "price": parsed_or(row.get("candidate_price")),
                "rating": parsed_or(row.get("candidate_rating")),
                "feedbacks": parsed_or(row.get("candidate_feedbacks")),
                "higher_in_search": False,
                "stronger_by_card_overall": as_text(row.get("substantial_or_cosmetic")) == "substantial",
                "danger_type": "; ".join(gaps),
            }
        )
        seen.add(nm_id)

    if not candidates and final_profile:
        cheaper = parse_count_from_gap(as_text(final_profile.get("price_gap_vs_competitors")))
        higher_rating = parse_count_from_gap(as_text(final_profile.get("rating_gap_vs_competitors")))
        more_reviews = parse_count_from_gap(as_text(final_profile.get("review_gap_vs_competitors")))
        stronger_card = parse_count_from_gap(as_text(final_profile.get("card_gap_vs_competitors")))
        higher_search = parse_count_from_gap(as_text(final_profile.get("search_gap_vs_competitors")))
    else:
        cheaper = sum(
            1
            for candidate in candidates
            if candidate.get("price") not in (None, 0) and own_price not in (None, 0) and candidate["price"] < own_price
        )
        higher_rating = sum(
            1
            for candidate in candidates
            if candidate.get("rating") not in (None, 0) and own_rating not in (None, 0) and candidate["rating"] > own_rating
        )
        more_reviews = sum(
            1
            for candidate in candidates
            if candidate.get("feedbacks") not in (None, 0) and own_feedbacks not in (None, 0) and candidate["feedbacks"] > own_feedbacks
        )
        stronger_card = sum(1 for candidate in candidates if candidate.get("stronger_by_card_overall"))
        higher_search = sum(1 for candidate in candidates if candidate.get("higher_in_search"))

    search_coverage_status = "partial"
    if recovery_profile:
        success = as_int(recovery_profile.get("recovered_search_success_queries")) or 0
        failed = as_int(recovery_profile.get("recovered_search_failed_queries")) or 0
        if success > 0 and failed == 0:
            search_coverage_status = "full_for_current_query_set"
        elif success > 0:
            search_coverage_status = "partial"
        else:
            search_coverage_status = "failed"
    elif final_profile and parse_count_from_gap(as_text(final_profile.get("search_gap_vs_competitors"))) >= 0:
        search_coverage_status = "full_for_current_query_set"

    def danger_score(candidate: dict[str, Any]) -> int:
        score = 0
        if candidate.get("price") not in (None, 0) and own_price not in (None, 0) and candidate["price"] < own_price:
            score += 1
        if candidate.get("rating") not in (None, 0) and own_rating not in (None, 0) and candidate["rating"] > own_rating:
            score += 1
        if candidate.get("feedbacks") not in (None, 0) and own_feedbacks not in (None, 0) and candidate["feedbacks"] > own_feedbacks:
            score += 1
        if candidate.get("higher_in_search"):
            score += 1
        if candidate.get("stronger_by_card_overall"):
            score += 1
        return score

    ranked_candidates = sorted(
        candidates,
        key=lambda row: (
            -danger_score(row),
            -parsed_or(row.get("feedbacks")),
            parsed_or(row.get("price")),
        ),
    )
    top_competitors = "; ".join(
        f"{row['candidate_nm_id']} ({row['source']}; score={danger_score(row)})"
        for row in ranked_candidates[:3]
    )

    return {
        "own_price": own_price,
        "own_rating": own_rating,
        "own_feedbacks": own_feedbacks,
        "confirmed_competitor_count": len(ranked_candidates),
        "cheaper_competitor_count": cheaper,
        "higher_rating_competitor_count": higher_rating,
        "more_review_competitor_count": more_reviews,
        "higher_search_competitor_count": higher_search,
        "stronger_card_competitor_count": stronger_card,
        "search_coverage_status": search_coverage_status,
        "external_pressure_present": (
            len(ranked_candidates) > 0
            and (cheaper > 0 or higher_rating > 0 or more_reviews > 0 or higher_search > 0 or stronger_card > 0)
        ),
        "top_dangerous_competitors": top_competitors,
        "candidate_count_for_layer": len(ranked_candidates),
    }


def build_input_layer(inputs: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    internal_by = index_by(inputs["internal"], "product_id")
    dynamic_by = index_by(inputs["dynamics"], "product_id")
    causal_by = index_by(inputs["causal_v1"], "product_id")
    final_profile_by = index_by(inputs["final_profiles"], "product_id")
    recovery_by = index_by(inputs["recovery_profiles"], "product_id")
    own_card_by = find_own_card_rows(inputs["card_cmp"])

    paid_short_by = index_by(
        inputs["paid_nonpaid"],
        "entity_id",
        filter_fn=lambda row: as_text(row.get("entity_scope")) == "product"
        and as_text(row.get("window_label")) == "short_7d_vs_prev_28d",
    )
    paid_medium_by = index_by(
        inputs["paid_nonpaid"],
        "entity_id",
        filter_fn=lambda row: as_text(row.get("entity_scope")) == "product"
        and as_text(row.get("window_label")) == "medium_14d_vs_prev_42d",
    )
    availability_by = group_by(inputs["availability"], "product_id")
    region_by = group_by(
        inputs["region_breakdown"],
        "product_id",
        filter_fn=lambda row: as_text(row.get("window_label")) == "short_7d_vs_prev_28d",
    )
    warehouse_by = group_by(
        inputs["warehouse_breakdown"],
        "product_id",
        filter_fn=lambda row: as_text(row.get("window_label")) == "short_7d_vs_prev_28d",
    )
    adv_by = group_by(inputs["adv_map"], "own_nm_id")
    manual_by = group_by(inputs["manual_confirmed"], "own_nm_id")
    recovery_card_by = group_by(inputs["card_recovery"], "own_nm_id")

    rows: list[dict[str, Any]] = []
    for sku in SELECTED_SKUS:
        internal = internal_by.get(sku, {})
        dynamic = dynamic_by.get(sku, {})
        causal = causal_by.get(sku, {})
        paid_short = paid_short_by.get(sku, {})
        paid_medium = paid_medium_by.get(sku, {})
        final_profile = final_profile_by.get(sku, {})
        recovery_profile = recovery_by.get(sku, {})
        own_card = own_card_by.get(sku, {})
        availability_rows = availability_by.get(sku, [])
        region_rows = region_by.get(sku, [])
        warehouse_rows = warehouse_by.get(sku, [])

        current_orders = parsed_or(causal.get("short_current_orders_per_day"), parsed_or(paid_short.get("current_orders_per_day")))
        base_orders = parsed_or(causal.get("short_base_orders_per_day"), parsed_or(paid_short.get("base_orders_per_day")))
        medium_current_orders = parsed_or(paid_medium.get("current_orders_per_day"))
        medium_base_orders = parsed_or(paid_medium.get("base_orders_per_day"))
        current_units = parsed_or(causal.get("short_current_units_per_day"))
        if current_units <= 0:
            current_units = parsed_or(dynamic.get("avg_daily_units_7d"))
        units_per_order = safe_div(current_units, current_orders, default=safe_div(parsed_or(dynamic.get("sold_units_net_total")), parsed_or(dynamic.get("order_count_total")), 1.0))
        base_units = parsed_or(causal.get("short_base_units_per_day"))
        if base_units <= 0:
            base_units = base_orders * units_per_order
        current_revenue_day = parsed_or(causal.get("short_current_revenue_per_day"), parsed_or(internal.get("short_current_revenue_total")) / 7.0)
        base_revenue_day = parsed_or(causal.get("short_base_revenue_per_day"), parsed_or(internal.get("short_base_revenue_total")) / 28.0)
        medium_current_revenue_day = parsed_or(internal.get("medium_revenue_delta_per_day")) + parsed_or(internal.get("medium_base_revenue_total")) / 42.0
        medium_base_revenue_day = parsed_or(internal.get("medium_base_revenue_total")) / 42.0

        current_profit_after_day = parsed_or(internal.get("short_current_profit_after_ads_total")) / 7.0
        base_profit_after_day = parsed_or(internal.get("short_base_profit_after_ads_total")) / 28.0
        current_ad_spend_day = parsed_or(paid_short.get("current_ad_spend_per_day"), parsed_or(causal.get("short_current_ad_spend_per_day")))
        base_ad_spend_day = parsed_or(paid_short.get("base_ad_spend_per_day"), parsed_or(causal.get("short_base_ad_spend_per_day")))
        current_profit_before_day = current_profit_after_day + current_ad_spend_day
        base_profit_before_day = base_profit_after_day + base_ad_spend_day

        profit_before_delta_day = current_profit_before_day - base_profit_before_day
        profit_after_delta_day = current_profit_after_day - base_profit_after_day
        revenue_delta_day = current_revenue_day - base_revenue_day
        units_delta_day = current_units - base_units
        orders_delta_day = current_orders - base_orders

        residual_profit_calendar_day = parsed_or(internal.get("short_residual_profit_after_calendar_per_day"), parsed_or(final_profile.get("residual_profit_after_calendar_signed_per_day")))
        calendar_explained_profit_day = parsed_or(final_profile.get("calendar_explained_profit_per_day"))

        current_paid_orders = parsed_or(paid_short.get("current_paid_orders_per_day"))
        base_paid_orders = parsed_or(paid_short.get("base_paid_orders_per_day"))
        paid_delta = parsed_or(paid_short.get("paid_orders_delta_per_day"))
        current_non_paid_orders = parsed_or(paid_short.get("current_non_paid_orders_per_day"))
        base_non_paid_orders = parsed_or(paid_short.get("base_non_paid_orders_per_day"))
        non_paid_delta = parsed_or(paid_short.get("non_paid_orders_delta_per_day"))

        revenue_per_order = safe_div(current_revenue_day, current_orders, default=safe_div(current_revenue_day, current_units, 0.0))
        revenue_per_unit = safe_div(current_revenue_day, current_units, default=revenue_per_order)
        profit_before_per_order = safe_div(current_profit_before_day, current_orders, default=0.0)
        profit_after_per_order = safe_div(current_profit_after_day, current_orders, default=0.0)
        ad_orders_day = parsed_or(causal.get("short_current_ad_orders_per_day"), current_paid_orders)
        impressions_day = parsed_or(causal.get("short_current_impressions_per_day"))
        clicks_day = parsed_or(causal.get("short_current_clicks_per_day"))
        ctr = parsed_or(causal.get("short_current_ctr"), parsed_or(paid_short.get("current_ctr")))
        cpc = parsed_or(causal.get("short_current_cpc"), parsed_or(paid_short.get("current_cpc")))
        cr = parsed_or(causal.get("short_current_cr"), parsed_or(paid_short.get("current_cr")))
        cpo = parsed_or(causal.get("short_current_cost_per_order"), parsed_or(paid_short.get("current_cost_per_order")))
        if cpo <= 0:
            cpo = parsed_or(paid_short.get("base_cost_per_order"))
        if cpo <= 0:
            cpo = max(5.0, revenue_per_order * 0.10)

        current_stock_units = parsed_or(dynamic.get("latest_stock_available"), parsed_or(internal.get("latest_stock_available_sum")))
        days_cover_old = safe_div(current_stock_units, base_units, default=999.0 if current_stock_units > 0 else 0.0)
        days_cover_new = safe_div(current_stock_units, current_units, default=999.0 if current_stock_units > 0 else 0.0)
        stock_extension_days = days_cover_new - days_cover_old

        total_available = sum(parsed_or(row.get("stock_available")) for row in availability_rows)
        max_stock_warehouse = max((parsed_or(row.get("stock_available")) for row in availability_rows), default=0.0)
        stock_concentration = safe_div(max_stock_warehouse, total_available, default=0.0)
        max_sales_warehouse = max((parsed_or(row.get("product_current_sold_units_per_day")) for row in warehouse_rows), default=0.0)
        total_sales_warehouse = sum(parsed_or(row.get("product_current_sold_units_per_day")) for row in warehouse_rows)
        sales_concentration = safe_div(max_sales_warehouse, total_sales_warehouse, default=0.0)

        risky_availability_rows = [
            row
            for row in availability_rows
            if "snapshot_zero" in as_text(row.get("availability_risk_flag"))
            or "overlap" in as_text(row.get("availability_risk_flag"))
        ]
        overlap_negative_warehouse = any(
            as_text(row.get("warehouse_name")) in as_text(final_profile.get("main_negative_warehouse"))
            for row in risky_availability_rows
        )

        top_regions, worst_region_delta, total_negative_region = best_top_contributors(region_rows, "product_revenue_delta_per_day")
        top_warehouses, worst_wh_delta, total_negative_wh = best_top_contributors(warehouse_rows, "product_revenue_delta_per_day")

        external_summary = build_external_summary(
            sku,
            own_card,
            adv_by.get(sku, []),
            manual_by.get(sku, []),
            recovery_card_by.get(sku, []),
            final_profile,
            recovery_profile,
        )

        full_revenue_total = parsed_or(internal.get("full_revenue_total"), parsed_or(dynamic.get("revenue_net_total")))
        logistics_total = parsed_or(internal.get("delivery_cost_total")) + parsed_or(internal.get("warehouse_logistics_tail_cost_total"))
        storage_total = parsed_or(internal.get("storage_cost_total"), parsed_or(dynamic.get("storage_cost_total")))
        commission_total = parsed_or(internal.get("commission_cost_total"), parsed_or(dynamic.get("commission_cost_total")))
        acquiring_total = parsed_or(internal.get("acquiring_cost_total"), parsed_or(dynamic.get("acquiring_cost_total")))
        withholding_total = parsed_or(internal.get("withholding_cost_total"), parsed_or(dynamic.get("withholding_cost_total")))
        observed_daily_storage = safe_div(storage_total, parsed_or(dynamic.get("days_active"), 56.0), default=0.0)

        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(internal.get("product_name") or final_profile.get("product_name")),
                "comparison_role": as_text(internal.get("comparison_role") or final_profile.get("comparison_role")),
                "current_units_per_day": round(current_units, 2),
                "base_units_per_day": round(base_units, 2),
                "units_delta_per_day": round(units_delta_day, 2),
                "current_orders_per_day": round(current_orders, 2),
                "base_orders_per_day": round(base_orders, 2),
                "orders_delta_per_day": round(orders_delta_day, 2),
                "medium_current_orders_per_day": round(medium_current_orders, 2),
                "medium_base_orders_per_day": round(medium_base_orders, 2),
                "current_revenue_per_day": round(current_revenue_day, 2),
                "base_revenue_per_day": round(base_revenue_day, 2),
                "revenue_delta_per_day": round(revenue_delta_day, 2),
                "medium_current_revenue_per_day": round(medium_current_revenue_day, 2),
                "medium_base_revenue_per_day": round(medium_base_revenue_day, 2),
                "current_profit_before_ads_per_day": round(current_profit_before_day, 2),
                "base_profit_before_ads_per_day": round(base_profit_before_day, 2),
                "profit_before_ads_delta_per_day": round(profit_before_delta_day, 2),
                "current_profit_after_ads_per_day": round(current_profit_after_day, 2),
                "base_profit_after_ads_per_day": round(base_profit_after_day, 2),
                "profit_after_ads_delta_per_day": round(profit_after_delta_day, 2),
                "residual_profit_after_calendar_per_day": round(residual_profit_calendar_day, 2),
                "calendar_explained_profit_per_day": round(calendar_explained_profit_day, 2),
                "current_average_check": round(parsed_or(causal.get("short_current_avg_check"), revenue_per_order), 2),
                "base_average_check": round(parsed_or(causal.get("short_base_avg_check"), safe_div(base_revenue_day, base_orders)), 2),
                "current_paid_orders_per_day": round(current_paid_orders, 2),
                "base_paid_orders_per_day": round(base_paid_orders, 2),
                "paid_orders_delta_per_day": round(paid_delta, 2),
                "current_non_paid_orders_per_day": round(current_non_paid_orders, 2),
                "base_non_paid_orders_per_day": round(base_non_paid_orders, 2),
                "non_paid_orders_delta_per_day": round(non_paid_delta, 2),
                "current_ad_spend_per_day": round(current_ad_spend_day, 2),
                "base_ad_spend_per_day": round(base_ad_spend_day, 2),
                "current_impressions_per_day": round(impressions_day, 2),
                "current_clicks_per_day": round(clicks_day, 2),
                "current_ctr": round(ctr, 4),
                "current_cpc": round(cpc, 2),
                "current_ad_orders_per_day": round(ad_orders_day, 2),
                "current_cr": round(cr, 4),
                "current_cost_per_order": round(cpo, 2),
                "full_revenue_total": round(full_revenue_total, 2),
                "full_profit_after_ads_total": round(parsed_or(internal.get("full_profit_after_ads_total"), parsed_or(dynamic.get("profit_after_ads_total"))), 2),
                "logistics_total": round(logistics_total, 2),
                "storage_total": round(storage_total, 2),
                "commission_total": round(commission_total, 2),
                "acquiring_total": round(acquiring_total, 2),
                "withholding_total": round(withholding_total, 2),
                "logistics_share_of_revenue": round(safe_div(logistics_total, full_revenue_total), 4),
                "storage_share_of_revenue": round(safe_div(storage_total, full_revenue_total), 4),
                "commission_share_of_revenue": round(safe_div(commission_total, full_revenue_total), 4),
                "acquiring_share_of_revenue": round(safe_div(acquiring_total, full_revenue_total), 4),
                "withholding_share_of_revenue": round(safe_div(withholding_total, full_revenue_total), 4),
                "current_stock_units": round(current_stock_units, 2),
                "days_of_stock_old_speed": round(days_cover_old, 2),
                "days_of_stock_new_speed": round(days_cover_new, 2),
                "stock_extension_days": round(stock_extension_days, 2),
                "stock_concentration_share": round(stock_concentration, 4),
                "sales_concentration_share": round(sales_concentration, 4),
                "stock_snapshot_date": as_text((availability_rows[0] if availability_rows else {}).get("snapshot_date")) or as_text(dynamic.get("latest_stock_snapshot_date")),
                "availability_risk_count": len(risky_availability_rows),
                "availability_overlap_main_negative_warehouse": "yes" if overlap_negative_warehouse else "no",
                "availability_note": as_text((availability_rows[0] if availability_rows else {}).get("availability_note")),
                "main_negative_regions": top_regions,
                "main_negative_warehouses": top_warehouses,
                "worst_region_revenue_delta_per_day": round(worst_region_delta, 2),
                "worst_warehouse_revenue_delta_per_day": round(worst_wh_delta, 2),
                "negative_region_pool_per_day": round(total_negative_region, 2),
                "negative_warehouse_pool_per_day": round(total_negative_wh, 2),
                "locality_signal": as_text(final_profile.get("local_or_everywhere") or internal.get("geo_signal")),
                "stock_risk_signal": as_text(final_profile.get("stock_risk") or internal.get("availability_signal")),
                "paid_signal": as_text(final_profile.get("paid_signal") or paid_short.get("flow_signal")),
                "ad_verdict": as_text(final_profile.get("ad_verdict")),
                "current_price": round(external_summary["own_price"], 2),
                "current_rating": round(external_summary["own_rating"], 2),
                "current_feedbacks": int(external_summary["own_feedbacks"]),
                "confirmed_competitor_count": external_summary["confirmed_competitor_count"],
                "cheaper_competitor_count": external_summary["cheaper_competitor_count"],
                "higher_rating_competitor_count": external_summary["higher_rating_competitor_count"],
                "more_review_competitor_count": external_summary["more_review_competitor_count"],
                "higher_search_competitor_count": external_summary["higher_search_competitor_count"],
                "stronger_card_competitor_count": external_summary["stronger_card_competitor_count"],
                "search_coverage_status": external_summary["search_coverage_status"],
                "external_pressure_present": "yes" if external_summary["external_pressure_present"] else "no",
                "top_dangerous_competitors": external_summary["top_dangerous_competitors"],
                "confidence": as_text(recovery_profile.get("confidence_after") or final_profile.get("confidence") or "medium"),
                "strongest_working_hypothesis": as_text(
                    recovery_profile.get("strongest_working_hypothesis") or final_profile.get("strongest_working_hypothesis")
                ),
                "units_per_order": round(units_per_order, 4),
                "revenue_per_order": round(revenue_per_order, 2),
                "revenue_per_unit": round(revenue_per_unit, 2),
                "profit_before_ads_per_order": round(profit_before_per_order, 2),
                "profit_after_ads_per_order": round(profit_after_per_order, 2),
                "observed_daily_storage_cost": round(observed_daily_storage, 2),
            }
        )
    return rows


def classify_channel(row: dict[str, Any]) -> str:
    paid_delta = parsed_or(row.get("paid_orders_delta_per_day"))
    non_paid_delta = parsed_or(row.get("non_paid_orders_delta_per_day"))
    if paid_delta >= 0 and non_paid_delta < 0:
        return "paid_holds_non_paid_weak"
    if paid_delta < 0 and non_paid_delta < 0:
        return "paid_and_non_paid_both_weak"
    if paid_delta < 0 and non_paid_delta >= 0:
        return "paid_weak_non_paid_holds"
    return "no_clear_channel_signal"


def classify_locality(row: dict[str, Any]) -> str:
    locality = as_text(row.get("locality_signal"))
    if locality == "local_concentrated_problem":
        return "local_concentrated_problem"
    if locality in {"distributed_problem", "broad_region_drop", "broad_warehouse_drop"}:
        return "distributed_problem"
    return "insufficient_local_signal"


def classify_external(row: dict[str, Any]) -> str:
    if as_text(row.get("external_pressure_present")) == "yes":
        return "external_competitor_pressure_present"
    if as_text(row.get("search_coverage_status")) != "full_for_current_query_set":
        return "external_layer_partial"
    return "no_clear_external_pressure"


def classify_availability(row: dict[str, Any]) -> str:
    risk_count = as_int(row.get("availability_risk_count")) or 0
    overlap = as_text(row.get("availability_overlap_main_negative_warehouse")) == "yes"
    if risk_count > 0 and overlap:
        return "availability_risk_present"
    if as_text(row.get("stock_risk_signal")):
        return "availability_snapshot_only"
    return "no_clear_availability_signal"


def classify_economics(row: dict[str, Any]) -> str:
    logistics_share = parsed_or(row.get("logistics_share_of_revenue"))
    storage_share = parsed_or(row.get("storage_share_of_revenue"))
    withholding_share = parsed_or(row.get("withholding_share_of_revenue"))
    if logistics_share >= 0.12:
        return "high_logistics_burden"
    if storage_share >= 0.03:
        return "high_storage_burden"
    if withholding_share >= 0.03:
        return "high_withholding_burden"
    return "balanced_cost_structure"


def classify_stock(row: dict[str, Any]) -> str:
    days_cover = parsed_or(row.get("days_of_stock_new_speed"))
    if days_cover > 90:
        return "dangerous_stock_cover"
    if days_cover > 45:
        return "extended_stock_cover"
    return "normal_stock_cover"


def classify_seasonality(row: dict[str, Any]) -> str:
    raw_loss = abs(parsed_or(row.get("profit_after_ads_delta_per_day")))
    explained = abs(parsed_or(row.get("calendar_explained_profit_per_day")))
    residual = abs(parsed_or(row.get("residual_profit_after_calendar_per_day")))
    if raw_loss <= 0:
        return "insufficient_calendar_signal"
    explained_share = safe_div(explained, raw_loss)
    if explained_share >= 0.5:
        return "likely_seasonal"
    if residual > 0:
        return "likely_beyond_seasonality"
    return "insufficient_calendar_signal"


def build_state_classification(input_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in input_rows:
        rows.append(
            {
                "snapshot_date": row["snapshot_date"],
                "product_id": row["product_id"],
                "product_name": row["product_name"],
                "channel_state": classify_channel(row),
                "locality_state": classify_locality(row),
                "external_state": classify_external(row),
                "availability_state": classify_availability(row),
                "economics_state": classify_economics(row),
                "stock_state": classify_stock(row),
                "seasonality_state": classify_seasonality(row),
                "confidence": row["confidence"],
                "strongest_working_hypothesis": row["strongest_working_hypothesis"],
            }
        )
    return rows


def scenario_fit(scenario_id: str, row: dict[str, Any], state: dict[str, Any]) -> float:
    channel = as_text(state.get("channel_state"))
    locality = as_text(state.get("locality_state"))
    external = as_text(state.get("external_state"))
    availability = as_text(state.get("availability_state"))
    stock = as_text(state.get("stock_state"))
    seasonality = as_text(state.get("seasonality_state"))

    profit_before_per_order = parsed_or(row.get("profit_before_ads_per_order"))
    cpo = parsed_or(row.get("current_cost_per_order"))
    ads_viable = profit_before_per_order > cpo * 1.15

    if scenario_id == "A":
        if seasonality == "likely_seasonal" or stock == "normal_stock_cover":
            return 0.80
        return 0.35
    if scenario_id == "B":
        if stock in {"extended_stock_cover", "dangerous_stock_cover"}:
            return 0.85
        return 0.30
    if scenario_id == "C":
        if channel in {"paid_holds_non_paid_weak", "no_clear_channel_signal"} and ads_viable:
            return 0.75 if stock == "normal_stock_cover" else 0.88
        return 0.30
    if scenario_id == "D":
        if channel == "paid_holds_non_paid_weak" and external == "external_competitor_pressure_present":
            return 0.92
        if external == "external_layer_partial":
            return 0.45
        return 0.50
    if scenario_id == "E":
        if external == "external_competitor_pressure_present":
            return 0.82
        if external == "external_layer_partial":
            return 0.50
        return 0.35
    if scenario_id == "F":
        if locality == "local_concentrated_problem" and availability == "availability_risk_present":
            return 0.95
        if locality == "local_concentrated_problem" and availability == "availability_snapshot_only":
            return 0.65
        return 0.20
    if scenario_id == "G":
        if seasonality == "likely_beyond_seasonality" and stock in {"extended_stock_cover", "dangerous_stock_cover"}:
            return 0.90
        if seasonality == "likely_beyond_seasonality":
            return 0.55
        return 0.35
    if scenario_id == "H":
        if stock == "dangerous_stock_cover":
            return 0.95
        if stock == "extended_stock_cover":
            return 0.45
        return 0.10
    return 0.30


def target_cover_days(stock_state: str, *, scenario_id: str) -> float:
    if scenario_id == "B":
        return 55.0 if stock_state == "extended_stock_cover" else 45.0
    if scenario_id == "G":
        return 40.0 if stock_state == "dangerous_stock_cover" else 35.0
    if scenario_id == "H":
        return 25.0
    return 45.0


def common_scenario_metrics(row: dict[str, Any]) -> dict[str, float]:
    return {
        "current_orders": parsed_or(row.get("current_orders_per_day")),
        "current_units": parsed_or(row.get("current_units_per_day")),
        "current_revenue": parsed_or(row.get("current_revenue_per_day")),
        "current_profit_before": parsed_or(row.get("current_profit_before_ads_per_day")),
        "current_profit_after": parsed_or(row.get("current_profit_after_ads_per_day")),
        "current_ad_spend": parsed_or(row.get("current_ad_spend_per_day")),
        "current_paid_orders": parsed_or(row.get("current_paid_orders_per_day")),
        "current_non_paid_orders": parsed_or(row.get("current_non_paid_orders_per_day")),
        "paid_delta": parsed_or(row.get("paid_orders_delta_per_day")),
        "non_paid_delta": parsed_or(row.get("non_paid_orders_delta_per_day")),
        "orders_lost": abs(parsed_or(row.get("orders_delta_per_day"))),
        "units_per_order": max(0.01, parsed_or(row.get("units_per_order"), 1.0)),
        "revenue_per_order": parsed_or(row.get("revenue_per_order")),
        "profit_before_per_order": parsed_or(row.get("profit_before_ads_per_order")),
        "profit_after_per_order": parsed_or(row.get("profit_after_ads_per_order")),
        "cpo": max(1.0, parsed_or(row.get("current_cost_per_order"))),
        "days_cover_new": parsed_or(row.get("days_of_stock_new_speed")),
        "current_stock_units": parsed_or(row.get("current_stock_units")),
        "observed_daily_storage": parsed_or(row.get("observed_daily_storage_cost")),
    }


def build_scenario_rows(input_rows: list[dict[str, Any]], state_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    state_by = index_by(state_rows, "product_id")
    rows: list[dict[str, Any]] = []

    for input_row in input_rows:
        sku = as_text(input_row.get("product_id"))
        state = state_by.get(sku, {})
        metrics = common_scenario_metrics(input_row)
        confidence = CONFIDENCE_SCORE.get(as_text(input_row.get("confidence")), 0.50)
        stock_state = as_text(state.get("stock_state"))
        availability_state = as_text(state.get("availability_state"))
        external_state = as_text(state.get("external_state"))
        channel_state = as_text(state.get("channel_state"))

        for scenario_id, scenario_slug, scenario_name in SCENARIOS:
            fit = scenario_fit(scenario_id, input_row, state)
            for mode in ["conservative", "working", "optimistic"]:
                projected_orders = metrics["current_orders"]
                projected_units = metrics["current_units"]
                projected_revenue = metrics["current_revenue"]
                projected_ad_spend = metrics["current_ad_spend"]
                extra_orders_net = 0.0
                extra_orders_raw = 0.0
                extra_ad_spend = 0.0
                extra_profit_before = 0.0
                extra_profit_after = 0.0
                storage_saving = 0.0
                capital_relief = 0.0
                cannibalization_penalty = 0.0
                hypothesis_error_penalty = 0.0
                target_future_cover = target_cover_days(stock_state, scenario_id=scenario_id)
                cash_not_frozen_proxy = 0.0
                main_risk = ""

                if scenario_id == "A":
                    main_risk = "если слабость устойчива, watch-сценарий оставит длинный запас и не вернет потерянный объем"

                elif scenario_id == "B":
                    excess_units = max(0.0, metrics["current_stock_units"] - metrics["current_units"] * target_future_cover)
                    cash_not_frozen_proxy = excess_units * metrics["revenue_per_order"] / max(metrics["units_per_order"], 0.01)
                    capital_relief = cash_not_frozen_proxy * MODE_CAPITAL_COST_RATE[mode]
                    storage_saving = metrics["observed_daily_storage"] * 30.0 * clamp(excess_units / max(metrics["current_stock_units"], 1.0), 0.0, 1.0)
                    hypothesis_error_penalty = cash_not_frozen_proxy * (1.0 - confidence) * (0.010 if mode == "conservative" else 0.015 if mode == "working" else 0.020)
                    main_risk = "если спрос внезапно вернется, более короткий inbound даст риск недопоставки"

                elif scenario_id == "C":
                    raw_share = 0.35 if channel_state != "paid_weak_non_paid_holds" else 0.20
                    extra_orders_raw = max(metrics["current_paid_orders"] * 0.25, metrics["orders_lost"] * raw_share, 0.4)
                    net_new_share = MODE_SHARES[mode]
                    extra_orders_net = extra_orders_raw * net_new_share
                    inflated_cpo = metrics["cpo"] * 1.15
                    extra_ad_spend = extra_orders_raw * inflated_cpo
                    extra_profit_before = extra_orders_net * metrics["profit_before_per_order"]
                    extra_profit_after = extra_profit_before - extra_ad_spend
                    cannibalization_penalty = (extra_orders_raw - extra_orders_net) * metrics["profit_before_per_order"] * 0.15
                    speed_relief_days = max(0.0, metrics["days_cover_new"] - safe_div(metrics["current_stock_units"], metrics["current_units"] + extra_orders_net * metrics["units_per_order"], metrics["days_cover_new"]))
                    capital_relief = metrics["current_stock_units"] * metrics["revenue_per_order"] * safe_div(speed_relief_days, max(metrics["days_cover_new"], 1.0)) * MODE_CAPITAL_COST_RATE[mode] * 0.25
                    main_risk = "часть paid-роста может съесть органику и не дать достаточно нового объема"

                elif scenario_id == "D":
                    extra_orders_raw = max(metrics["current_paid_orders"] * 0.18, metrics["orders_lost"] * 0.22, 0.3)
                    net_new_share = clamp(MODE_SHARES[mode] + 0.05, 0.0, 0.80)
                    extra_paid_net = extra_orders_raw * net_new_share
                    non_paid_pool = max(0.0, -metrics["non_paid_delta"])
                    card_share = MODE_CARD_SHARES[mode] * (1.0 if external_state == "external_competitor_pressure_present" else 0.6)
                    non_paid_return = non_paid_pool * card_share * 0.70
                    extra_orders_net = extra_paid_net + non_paid_return
                    inflated_cpo = metrics["cpo"] * 1.10
                    extra_ad_spend = extra_orders_raw * inflated_cpo
                    extra_profit_before = extra_orders_net * metrics["profit_before_per_order"]
                    extra_profit_after = extra_profit_before - extra_ad_spend
                    cannibalization_penalty = (extra_orders_raw - extra_paid_net) * metrics["profit_before_per_order"] * 0.10
                    speed_relief_days = max(0.0, metrics["days_cover_new"] - safe_div(metrics["current_stock_units"], metrics["current_units"] + extra_orders_net * metrics["units_per_order"], metrics["days_cover_new"]))
                    capital_relief = metrics["current_stock_units"] * metrics["revenue_per_order"] * safe_div(speed_relief_days, max(metrics["days_cover_new"], 1.0)) * MODE_CAPITAL_COST_RATE[mode] * 0.25
                    main_risk = "если карточка не дает возврата non-paid, товар просто сядет на более дорогую paid-зависимость"

                elif scenario_id == "E":
                    non_paid_pool = max(0.0, -metrics["non_paid_delta"])
                    external_factor = 1.0 if external_state == "external_competitor_pressure_present" else 0.5
                    extra_orders_net = non_paid_pool * MODE_CARD_SHARES[mode] * external_factor * 0.60
                    extra_profit_before = extra_orders_net * metrics["profit_before_per_order"]
                    extra_profit_after = extra_profit_before
                    speed_relief_days = max(0.0, metrics["days_cover_new"] - safe_div(metrics["current_stock_units"], metrics["current_units"] + extra_orders_net * metrics["units_per_order"], metrics["days_cover_new"]))
                    capital_relief = metrics["current_stock_units"] * metrics["revenue_per_order"] * safe_div(speed_relief_days, max(metrics["days_cover_new"], 1.0)) * MODE_CAPITAL_COST_RATE[mode] * 0.18
                    main_risk = "карточка и контент обычно возвращают organic медленнее, чем paid-ускорение"

                elif scenario_id == "F":
                    local_factor = 0.7 if as_text(state.get("locality_state")) == "local_concentrated_problem" else 0.3
                    recoverable_pool = metrics["orders_lost"] * local_factor
                    availability_share = MODE_AVAILABILITY_SHARES[availability_state][mode]
                    extra_orders_net = recoverable_pool * availability_share
                    extra_profit_before = extra_orders_net * metrics["profit_before_per_order"]
                    extra_profit_after = extra_profit_before
                    speed_relief_days = max(0.0, metrics["days_cover_new"] - safe_div(metrics["current_stock_units"], metrics["current_units"] + extra_orders_net * metrics["units_per_order"], metrics["days_cover_new"]))
                    capital_relief = metrics["current_stock_units"] * metrics["revenue_per_order"] * safe_div(speed_relief_days, max(metrics["days_cover_new"], 1.0)) * MODE_CAPITAL_COST_RATE[mode] * 0.20
                    main_risk = "stock-сигнал у нас snapshot-only, поэтому часть локального эффекта может оказаться ложной"

                elif scenario_id == "G":
                    excess_units = max(0.0, metrics["current_stock_units"] - metrics["current_units"] * target_future_cover)
                    cash_not_frozen_proxy = excess_units * metrics["revenue_per_order"] / max(metrics["units_per_order"], 0.01)
                    capital_relief = cash_not_frozen_proxy * MODE_CAPITAL_COST_RATE[mode] * 1.25
                    storage_saving = metrics["observed_daily_storage"] * 30.0 * clamp(excess_units / max(metrics["current_stock_units"], 1.0), 0.0, 1.0)
                    hypothesis_error_penalty = cash_not_frozen_proxy * (1.0 - confidence) * (0.014 if mode == "conservative" else 0.018 if mode == "working" else 0.024)
                    main_risk = "если старый спрос частично вернется, новый normal даст слишком защитную supply-модель"

                elif scenario_id == "H":
                    extra_orders_raw = max(metrics["orders_lost"] * 0.80, metrics["current_orders"] * 0.40, 1.0)
                    net_new_share = MODE_CLEARANCE_NEW_SHARE[mode]
                    extra_orders_net = extra_orders_raw * net_new_share
                    discount_hit = {"conservative": 0.08, "working": 0.12, "optimistic": 0.18}[mode]
                    adjusted_profit_before = max(0.0, metrics["profit_before_per_order"] * (1.0 - discount_hit))
                    extra_ad_spend = extra_orders_raw * metrics["cpo"] * 1.25
                    extra_profit_before = extra_orders_net * adjusted_profit_before
                    extra_profit_after = extra_profit_before - extra_ad_spend
                    cannibalization_penalty = (extra_orders_raw - extra_orders_net) * adjusted_profit_before * 0.20
                    speed_relief_days = max(0.0, metrics["days_cover_new"] - safe_div(metrics["current_stock_units"], metrics["current_units"] + extra_orders_net * metrics["units_per_order"], metrics["days_cover_new"]))
                    capital_relief = metrics["current_stock_units"] * metrics["revenue_per_order"] * safe_div(speed_relief_days, max(metrics["days_cover_new"], 1.0)) * MODE_CAPITAL_COST_RATE[mode] * 0.35
                    main_risk = "ускорение clearance может быстро съесть маржу и переоценить реальный net-new effect"

                projected_orders = metrics["current_orders"] + extra_orders_net
                projected_units = metrics["current_units"] + extra_orders_net * metrics["units_per_order"]
                projected_revenue = metrics["current_revenue"] + extra_orders_net * metrics["revenue_per_order"]
                projected_ad_spend = metrics["current_ad_spend"] + extra_ad_spend
                projected_profit_before = metrics["current_profit_before"] + extra_profit_before
                projected_profit_after = metrics["current_profit_after"] + extra_profit_after
                projected_days_of_stock = safe_div(metrics["current_stock_units"], projected_units, default=999.0 if metrics["current_stock_units"] > 0 else 0.0)

                hypothesis_error_penalty += max(0.0, extra_profit_before) * (1.0 - confidence * fit) * 0.25
                if scenario_id in {"C", "D", "H"}:
                    hypothesis_error_penalty += extra_ad_spend * (1.0 - fit) * 0.20

                incremental_profit_after_30d = extra_profit_after * 30.0
                incremental_storage_benefit_30d = storage_saving + capital_relief
                scenario_net_value_30d = incremental_profit_after_30d + incremental_storage_benefit_30d - cannibalization_penalty * 30.0 - hypothesis_error_penalty

                if scenario_id == "A":
                    scenario_net_value_30d = 0.0
                    incremental_profit_after_30d = 0.0
                    incremental_storage_benefit_30d = 0.0
                    cannibalization_penalty = 0.0
                    hypothesis_error_penalty = 0.0

                rows.append(
                    {
                        "snapshot_date": input_row["snapshot_date"],
                        "product_id": sku,
                        "product_name": input_row["product_name"],
                        "comparison_role": input_row["comparison_role"],
                        "scenario_id": scenario_id,
                        "scenario_slug": scenario_slug,
                        "scenario_name": scenario_name,
                        "evaluation_mode": mode,
                        "scenario_fit_score": round(fit, 4),
                        "confidence_score": round(confidence, 4),
                        "projected_units_per_day": round(projected_units, 2),
                        "projected_orders_per_day": round(projected_orders, 2),
                        "projected_revenue_per_day": round(projected_revenue, 2),
                        "projected_ad_spend_per_day": round(projected_ad_spend, 2),
                        "incremental_ad_spend_per_day": round(extra_ad_spend, 2),
                        "incremental_paid_orders_raw_per_day": round(extra_orders_raw, 2),
                        "incremental_net_new_orders_per_day": round(extra_orders_net, 2),
                        "incremental_profit_before_ads_per_day": round(extra_profit_before, 2),
                        "incremental_profit_after_ads_per_day": round(extra_profit_after, 2),
                        "incremental_profit_after_ads_30d": round(incremental_profit_after_30d, 2),
                        "storage_saving_or_carry_relief_30d": round(incremental_storage_benefit_30d, 2),
                        "cash_not_frozen_proxy": round(cash_not_frozen_proxy, 2),
                        "projected_days_of_stock_on_current_stock": round(projected_days_of_stock, 2),
                        "target_future_cover_days": round(target_future_cover, 2),
                        "stock_cover_change_days": round(projected_days_of_stock - metrics["days_cover_new"], 2),
                        "cannibalization_penalty_30d": round(cannibalization_penalty * 30.0, 2),
                        "hypothesis_error_penalty_30d": round(hypothesis_error_penalty, 2),
                        "scenario_net_value_30d_vs_watch": round(scenario_net_value_30d, 2),
                        "main_risk": main_risk,
                        "channel_state": state.get("channel_state"),
                        "locality_state": state.get("locality_state"),
                        "external_state": state.get("external_state"),
                        "availability_state": state.get("availability_state"),
                        "stock_state": state.get("stock_state"),
                    }
                )
    return rows


def build_scenario_comparison_rows(scenario_rows: list[dict[str, Any]], input_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    input_by = index_by(input_rows, "product_id")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in scenario_rows:
        grouped[(as_text(row.get("product_id")), as_text(row.get("scenario_id")))].append(row)

    rows = []
    for (sku, scenario_id), variants in grouped.items():
        mode_by = {as_text(row.get("evaluation_mode")): row for row in variants}
        working = mode_by["working"]
        input_row = input_by.get(sku, {})
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": input_row.get("product_name"),
                "scenario_id": scenario_id,
                "scenario_name": working.get("scenario_name"),
                "net_value_30d_conservative": mode_by["conservative"].get("scenario_net_value_30d_vs_watch"),
                "net_value_30d_working": working.get("scenario_net_value_30d_vs_watch"),
                "net_value_30d_optimistic": mode_by["optimistic"].get("scenario_net_value_30d_vs_watch"),
                "projected_days_of_stock_working": working.get("projected_days_of_stock_on_current_stock"),
                "target_future_cover_days": working.get("target_future_cover_days"),
                "scenario_fit_score_working": working.get("scenario_fit_score"),
                "selection_score_working": round(parsed_or(working.get("scenario_net_value_30d_vs_watch")) * parsed_or(working.get("scenario_fit_score")), 2),
                "main_risk": working.get("main_risk"),
            }
        )
    return rows


def build_best_choice_rows(comparison_rows: list[dict[str, Any]], state_rows: list[dict[str, Any]], input_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comparison_rows:
        by_sku[as_text(row.get("product_id"))].append(row)
    state_by = index_by(state_rows, "product_id")
    input_by = index_by(input_rows, "product_id")

    rows = []
    for sku, variants in by_sku.items():
        input_row = input_by.get(sku, {})
        variants.sort(key=lambda row: parsed_or(row.get("selection_score_working")), reverse=True)
        best = variants[0]
        best_value = parsed_or(best.get("net_value_30d_working"))
        state = state_by.get(sku, {})
        is_positive_comparator = (
            as_text(input_row.get("comparison_role")) == "stable_comparator"
            or parsed_or(input_row.get("profit_after_ads_delta_per_day")) > 0
            or parsed_or(input_row.get("residual_profit_after_calendar_per_day")) > 0
        )

        if is_positive_comparator:
            best = next((row for row in variants if as_text(row.get("scenario_id")) == "A"), best)

        if best_value <= 0:
            best = next((row for row in variants if as_text(row.get("scenario_id")) == "A"), best)

        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": input_row.get("product_name"),
                "best_scenario_id": best.get("scenario_id"),
                "best_scenario_name": best.get("scenario_name"),
                "best_scenario_net_value_30d_working": best.get("net_value_30d_working"),
                "best_scenario_net_value_30d_conservative": best.get("net_value_30d_conservative"),
                "best_scenario_net_value_30d_optimistic": best.get("net_value_30d_optimistic"),
                "best_scenario_fit_score": best.get("scenario_fit_score_working"),
                "best_scenario_selection_score": best.get("selection_score_working"),
                "runner_up_scenario_id": variants[1].get("scenario_id") if len(variants) > 1 else "",
                "runner_up_net_value_30d_working": variants[1].get("net_value_30d_working") if len(variants) > 1 else "",
                "reason_why_best": explain_best_choice(best, state, input_row),
                "main_risk": best.get("main_risk"),
            }
        )
    return rows


def explain_best_choice(best: dict[str, Any], state: dict[str, Any], input_row: dict[str, Any]) -> str:
    scenario_id = as_text(best.get("best_scenario_id") or best.get("scenario_id"))
    if (
        as_text(input_row.get("comparison_role")) == "stable_comparator"
        or parsed_or(input_row.get("profit_after_ads_delta_per_day")) > 0
        or parsed_or(input_row.get("residual_profit_after_calendar_per_day")) > 0
    ):
        return "товар сейчас держится лучше слабых SKU, поэтому модуль оставляет его как comparator и не предлагает агрессивное вмешательство"
    if scenario_id == "A":
        return "сейчас сценарная математика не дает более сильного и безопасного хода, чем watch-режим"
    if scenario_id == "B":
        return "основная ценность не в разгоне спроса, а в сокращении лишнего будущего запаса и заморозки денег"
    if scenario_id == "C":
        return "текущая экономика рекламы еще допускает ускорение, а запас достаточно длинный для controlled sell-through"
    if scenario_id == "D":
        return "товар теряет non-paid, поэтому лучший ход сочетает краткосрочный paid-подпор и среднесрочный возврат органики"
    if scenario_id == "E":
        return "проблема выглядит внешней и non-paid, поэтому отдельный card-fix выгоднее, чем еще сильнее садить товар на paid"
    if scenario_id == "F":
        return "локальность и availability-layer дают самый осмысленный сценарий именно через устранение локальных провалов"
    if scenario_id == "G":
        return "слабость выглядит устойчивой, поэтому безопаснее принять новый темп и перестроить supply под него"
    if scenario_id == "H":
        return "запас слишком длинный, и быстрая разгрузка дает больше ценности, чем сохранение старой маржи"
    return as_text(input_row.get("strongest_working_hypothesis"))


def build_switch_rules(best_rows: list[dict[str, Any]], input_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    input_by = index_by(input_rows, "product_id")
    rows = []
    for row in best_rows:
        sku = as_text(row.get("product_id"))
        best = as_text(row.get("best_scenario_id"))
        input_row = input_by.get(sku, {})
        switch_rule = ""
        if best in {"C", "D"}:
            switch_rule = "если за 7-10 дней paid-ускорение не улучшает orders/day или ухудшает CPA, сценарий ослаблять и уходить в более защитный режим"
        elif best == "F":
            switch_rule = "если после проверки локальных складов и 7-10 дней наблюдения возврат заказов не случается, availability-гипотезу понижать"
        elif best in {"B", "G"}:
            switch_rule = "если запас вернулся в безопасный коридор или спрос начал восстанавливаться, supply-cut пересматривать вверх"
        elif best == "H":
            switch_rule = "если clearance съедает маржу быстрее, чем сокращает cover, сценарий выключать и возвращаться к supply-защитному режиму"
        else:
            switch_rule = "если слабость держится еще 7-14 дней и residual не сужается, переходить от watch к активному сценарию по главной причине"
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": input_row.get("product_name"),
                "best_scenario_id": best,
                "best_scenario_name": row.get("best_scenario_name"),
                "switch_rule": switch_rule,
            }
        )
    return rows


def build_client_summary(best_rows: list[dict[str, Any]], input_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    input_by = index_by(input_rows, "product_id")
    rows = []
    for row in best_rows:
        sku = as_text(row.get("product_id"))
        source = input_by.get(sku, {})
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": source.get("product_name"),
                "what_happened": (
                    f"изменение заказов {parsed_or(source.get('orders_delta_per_day')):+.2f}/день, "
                    f"изменение прибыли после рекламы {signed_money_fmt(parsed_or(source.get('profit_after_ads_delta_per_day')))} /день, "
                    f"остаток после календарной поправки {signed_money_fmt(parsed_or(source.get('residual_profit_after_calendar_per_day')))} /день"
                ),
                "why_it_is_dangerous": (
                    f"days of stock сейчас {parsed_or(source.get('days_of_stock_new_speed')):.1f} дней "
                    f"против {parsed_or(source.get('days_of_stock_old_speed')):.1f} по старой скорости; "
                    "это влияет на хранение и на заморозку денег в запасе"
                ),
                "reasonable_options_now": "watch; supply_slowdown; ads_acceleration; ads_plus_card_fix; card_fix_only; availability_fix; new_normal; clearance_mode",
                "best_option_now": row.get("best_scenario_name"),
                "why_best_option_now": row.get("reason_why_best"),
                "scenario_effect_range": (
                    f"conservative {money_fmt(parsed_or(row.get('best_scenario_net_value_30d_conservative')))} / "
                    f"working {money_fmt(parsed_or(row.get('best_scenario_net_value_30d_working')))} / "
                    f"optimistic {money_fmt(parsed_or(row.get('best_scenario_net_value_30d_optimistic')))} за 30 дней; "
                    "это сценарная оценка, не гарантия"
                ),
                "main_risk": row.get("main_risk"),
            }
        )
    return rows


def build_money_and_stock_summaries(input_rows: list[dict[str, Any]], best_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    best_by = index_by(best_rows, "product_id")
    money_rows = []
    stock_rows = []
    for row in input_rows:
        sku = as_text(row.get("product_id"))
        best = best_by.get(sku, {})
        money_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": row.get("product_name"),
                "current_profit_loss_per_day": round(max(0.0, -parsed_or(row.get("profit_after_ads_delta_per_day"))), 2),
                "residual_profit_loss_after_calendar_per_day": round(max(0.0, -parsed_or(row.get("residual_profit_after_calendar_per_day"))), 2),
                "signed_profit_after_ads_delta_per_day": round(parsed_or(row.get("profit_after_ads_delta_per_day")), 2),
                "signed_residual_after_calendar_per_day": round(parsed_or(row.get("residual_profit_after_calendar_per_day")), 2),
                "best_scenario_effect_30d_conservative": round(parsed_or(best.get("best_scenario_net_value_30d_conservative")), 2),
                "best_scenario_effect_30d_working": round(parsed_or(best.get("best_scenario_net_value_30d_working")), 2),
                "best_scenario_effect_30d_optimistic": round(parsed_or(best.get("best_scenario_net_value_30d_optimistic")), 2),
                "note": "scenario estimate only; not a guaranteed return",
            }
        )
        stock_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": row.get("product_name"),
                "current_stock_units": row.get("current_stock_units"),
                "days_of_stock_old_speed": row.get("days_of_stock_old_speed"),
                "days_of_stock_new_speed": row.get("days_of_stock_new_speed"),
                "stock_extension_days": row.get("stock_extension_days"),
                "stock_concentration_share": row.get("stock_concentration_share"),
                "sales_concentration_share": row.get("sales_concentration_share"),
            }
        )
    return money_rows, stock_rows


def build_markdown_report(
    input_rows: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    best_rows: list[dict[str, Any]],
) -> str:
    state_by = index_by(state_rows, "product_id")
    best_by = index_by(best_rows, "product_id")
    comparison_by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comparison_rows:
        comparison_by_sku[as_text(row.get("product_id"))].append(row)
    for rows in comparison_by_sku.values():
        rows.sort(key=lambda row: parsed_or(row.get("selection_score_working")), reverse=True)

    overview_rows = []
    for input_row in input_rows:
        sku = as_text(input_row.get("product_id"))
        best = best_by.get(sku, {})
        overview_rows.append(
            {
                "product_id": sku,
                "product_name": input_row.get("product_name"),
                "residual_profit_after_calendar_per_day": signed_money_fmt(parsed_or(input_row.get("residual_profit_after_calendar_per_day"))),
                "days_of_stock_new_speed": f"{parsed_or(input_row.get('days_of_stock_new_speed')):.1f}",
                "channel_state": state_by.get(sku, {}).get("channel_state"),
                "best_scenario": best.get("best_scenario_name"),
                "working_effect_30d": money_fmt(parsed_or(best.get("best_scenario_net_value_30d_working"))),
            }
        )

    parts = [
        "# PROFIT CONTROL — SKU Demand Drop Scenario Engine v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Goal",
        "",
        "- This module turns the causal SKU layer into a transparent scenario engine.",
        "- It does not assume one fixed recommendation: it compares watch, supply, ads, card, availability, new-normal and clearance scenarios.",
        "- All money figures here are scenario estimates, not guaranteed outcomes.",
        "",
        "## How The Module Works",
        "",
        "- Input layer merges demand drop, calendar-adjusted residual, paid/non-paid split, economics, stock cover, availability snapshot, own card and comparable competitor layer.",
        "- State classification first labels channel, locality, external pressure, availability signal, economics burden, stock cover and seasonality.",
        "- Scenarios A–H are then calculated on the same rule-based frame.",
        "- Ad scenarios use conservative / working / optimistic net-new shares, so paid growth is not treated as 100% incremental sales.",
        "- Best-scenario choice is based on working-mode net value plus scenario-fit logic and risk penalties, not on raw revenue uplift alone.",
        "",
        "## SKU Overview",
        "",
        md_table(
            overview_rows,
            [
                "product_id",
                "product_name",
                "residual_profit_after_calendar_per_day",
                "days_of_stock_new_speed",
                "channel_state",
                "best_scenario",
                "working_effect_30d",
            ],
        ),
        "",
    ]

    for input_row in input_rows:
        sku = as_text(input_row.get("product_id"))
        state = state_by.get(sku, {})
        best = best_by.get(sku, {})
        top_variants = sorted(
            comparison_by_sku.get(sku, []),
            key=lambda row: parsed_or(row.get("selection_score_working")),
            reverse=True,
        )[:3]
        orders_delta = parsed_or(input_row.get("orders_delta_per_day"))
        revenue_delta = parsed_or(input_row.get("revenue_delta_per_day"))
        profit_delta = parsed_or(input_row.get("profit_after_ads_delta_per_day"))
        residual_delta = parsed_or(input_row.get("residual_profit_after_calendar_per_day"))
        change_phrase = "просело" if profit_delta < 0 else "держится лучше базы"
        parts.extend(
            [
                f"### {input_row['product_name']} [{sku}]",
                "",
                f"- Что произошло: {change_phrase}; заказы `{orders_delta:+.2f}/день`, выручка `{signed_money_fmt(revenue_delta)}/день`, прибыль после рекламы `{signed_money_fmt(profit_delta)}/день`, остаток после календаря `{signed_money_fmt(residual_delta)}/день`.",
                f"- Что с запасом: текущий cover `{
                    parsed_or(input_row.get('days_of_stock_new_speed')):.1f}` дней против `{
                    parsed_or(input_row.get('days_of_stock_old_speed')):.1f}` по старой скорости; удлинение `{
                    parsed_or(input_row.get('stock_extension_days')):.1f}` дней.",
                f"- Что с каналом: `{state.get('channel_state')}`; paid delta `{
                    parsed_or(input_row.get('paid_orders_delta_per_day')):.2f}`, non-paid delta `{
                    parsed_or(input_row.get('non_paid_orders_delta_per_day')):.2f}`.",
                f"- Что с внешним слоем: `{state.get('external_state')}`, confirmed competitors `{
                    as_int(input_row.get('confirmed_competitor_count')) or 0}`, stronger cards `{
                    as_int(input_row.get('stronger_card_competitor_count')) or 0}`, higher-search competitors `{
                    as_int(input_row.get('higher_search_competitor_count')) or 0}`.",
                f"- Что с локальностью и доступностью: `{state.get('locality_state')}` / `{state.get('availability_state')}`; main regions `{
                    input_row.get('main_negative_regions') or ''}`; main warehouses `{
                    input_row.get('main_negative_warehouses') or ''}`.",
                f"- Лучший сценарий сейчас: `{best.get('best_scenario_name')}`. Почему: {best.get('reason_why_best')}.",
                "",
                md_table(
                    top_variants,
                    [
                        "scenario_id",
                        "scenario_name",
                        "net_value_30d_conservative",
                        "net_value_30d_working",
                        "net_value_30d_optimistic",
                        "scenario_fit_score_working",
                        "selection_score_working",
                    ],
                ),
                "",
            ]
        )

    parts.extend(
        [
            "## Main Risks",
            "",
            "- Storage and inventory relief use observed storage where it exists, and carrying-cost proxy where explicit storage is near zero.",
            "- Availability remains snapshot-based: the engine treats it as probabilistic support, not as proven historical stockout.",
            "- Ad scenarios model cannibalization explicitly, but real marketplace response can still be weaker or slower than the working case.",
            "- External card/search pressure is strongest where comparable competitors are already confirmed; where coverage is weaker, scenario-fit is penalized.",
        ]
    )
    return "\n".join(parts) + "\n"


def main() -> None:
    inputs = load_inputs()
    input_rows = build_input_layer(inputs)
    state_rows = build_state_classification(input_rows)
    scenario_rows = build_scenario_rows(input_rows, state_rows)
    comparison_rows = build_scenario_comparison_rows(scenario_rows, input_rows)
    best_rows = build_best_choice_rows(comparison_rows, state_rows, input_rows)
    switch_rows = build_switch_rules(best_rows, input_rows)
    client_rows = build_client_summary(best_rows, input_rows)
    money_rows, stock_rows = build_money_and_stock_summaries(input_rows, best_rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(INPUT_LAYER_CSV, input_rows)
    write_csv(STATE_CLASSIFICATION_CSV, state_rows)
    for scenario_id, _slug, _name in SCENARIOS:
        write_csv(
            SCENARIO_CSV_MAP[scenario_id],
            [row for row in scenario_rows if as_text(row.get("scenario_id")) == scenario_id],
        )
    write_csv(SCENARIO_COMPARISON_CSV, comparison_rows)
    write_csv(BEST_CHOICE_CSV, best_rows)
    write_csv(SWITCH_RULES_CSV, switch_rows)
    write_csv(CLIENT_SUMMARY_CSV, client_rows)
    write_csv(MONEY_AT_RISK_CSV, money_rows)
    write_csv(STOCK_EXTENSION_CSV, stock_rows)
    DOC_PATH.write_text(build_markdown_report(input_rows, state_rows, comparison_rows, best_rows), encoding="utf-8")


if __name__ == "__main__":
    main()
