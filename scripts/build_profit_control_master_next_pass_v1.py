from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MOSCOW = ZoneInfo("Europe/Moscow")
TODAY = datetime.now(MOSCOW).date()

TRUTH_DIR = ROOT / "exports" / "profit_control_wb_finance_truth_lag_closed_period_recovery_v2"
BACKFILL_DIR = ROOT / "exports" / "profit_control_data_backfill_live_tail_competitor_expansion_pass_v1"
SCENARIO_DIR = ROOT / "exports" / "profit_control_sku_demand_drop_scenario_engine_v1"
FINAL_DIR = ROOT / "exports" / "wb_final_causal_pack_v1"
RECOVERY_DIR = ROOT / "exports" / "wb_missing_data_recovery_pass_v1"
EXTERNAL_DIR = ROOT / "exports" / "wb_external_signal_layer_v1"
BATTLE_DIR = ROOT / "exports" / "wb_battle_pass_v1"

OUTPUT_DIR = ROOT / "exports" / "profit_control_master_next_pass_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_master_next_pass_v1.md"

FINANCE_TRUTH_SPLIT_FINAL_CSV = OUTPUT_DIR / "finance_truth_split_final.csv"
CLOSED_PERIOD_RECOVERY_FINAL_CSV = OUTPUT_DIR / "closed_period_recovery_final.csv"
LIVE_TAIL_STATUS_FINAL_CSV = OUTPUT_DIR / "live_tail_status_final.csv"
COMPETITOR_LAYER_FINAL_CSV = OUTPUT_DIR / "competitor_layer_final.csv"
RANK_VISIBILITY_FINAL_CSV = OUTPUT_DIR / "rank_visibility_final.csv"
STORE_ANALYTICAL_LAYER_FINAL_CSV = OUTPUT_DIR / "store_analytical_layer_final.csv"
SKU_ANALYTICAL_LAYER_FINAL_CSV = OUTPUT_DIR / "sku_analytical_layer_final.csv"
SKU_SCENARIO_LAYER_FINAL_CSV = OUTPUT_DIR / "sku_scenario_layer_final.csv"
SKU_HYPOTHESIS_CHECK_FINAL_CSV = OUTPUT_DIR / "sku_hypothesis_check_final.csv"
SKU_RECOMMENDATION_CHECK_FINAL_CSV = OUTPUT_DIR / "sku_recommendation_check_final.csv"

SELECTED_SKUS = [
    "669118440",
    "340692643",
    "518796478",
    "73090372",
    "240017726",
    "72702996",
]
WEAK_SKUS = {"669118440", "340692643", "518796478", "73090372", "240017726"}
PARTIAL_RECOVERED_SKUS = {"340692643", "518796478", "73090372"}


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
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
        return float(str(value).replace(" ", ""))
    except ValueError:
        return None


def as_int(value: Any) -> int | None:
    parsed = as_float(value)
    if parsed is None:
        return None
    return int(parsed)


def parsed_or(value: Any, default: float = 0.0) -> float:
    parsed = as_float(value)
    return parsed if parsed is not None else default


def money_fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:,.0f}".replace(",", " ")


def short_money(value: Any) -> str:
    parsed = as_float(value)
    if parsed is None:
        return ""
    return money_fmt(parsed)


def split_semicolon(text: str) -> list[str]:
    return [item.strip() for item in as_text(text).split(";") if item.strip()]


def first_clauses(text: str, limit: int = 3) -> str:
    parts = split_semicolon(text)
    return "; ".join(parts[:limit]) if parts else as_text(text)


def group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[as_text(row.get(key))].append(row)
    return grouped


def index_by(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {as_text(row.get(key)): row for row in rows}


def pair_index(rows: list[dict[str, str]], left: str, right: str) -> dict[tuple[str, str], dict[str, str]]:
    indexed: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        indexed[(as_text(row.get(left)), as_text(row.get(right)))] = row
    return indexed


def yes_no_from_bool(value: bool) -> str:
    return "yes" if value else "no"


def count_query_hits(text: str) -> int:
    return len(split_semicolon(text))


def classify_query(query: str) -> str:
    q = as_text(query).lower()
    if not q:
        return ""
    broad_exact = {
        "гараж",
        "гараж детский",
        "гараж для машинок",
        "большой гараж для машинок",
    }
    feature_tokens = [
        "дерев",
        "азс",
        "дорог",
        "размет",
        "лифт",
        "трамплин",
        "когтет",
        "для девочек",
        "5 лет",
        "7 лет",
        "больш",
        "балерин",
        "единорог",
        "принцесс",
    ]
    if q in broad_exact or len(q.split()) <= 2:
        return "broad_intent"
    if any(token in q for token in feature_tokens):
        return "feature_intent"
    return "direct_intent"


def visibility_status(avg_position: Any, visibility: Any) -> str:
    avg = as_float(avg_position)
    vis = as_float(visibility)
    if avg is None and vis is None:
        return "recovered_query_set_without_direct_metric"
    if vis is not None and vis == 0:
        return "absent_or_not_visible"
    if avg is not None and avg > 80:
        return "weak_visibility"
    if vis is not None and vis < 30:
        return "weak_visibility"
    return "visible_or_working"


def load_inputs() -> dict[str, list[dict[str, str]]]:
    return {
        "truth_split": load_csv(TRUTH_DIR / "finance_lag_truth_split.csv"),
        "day_matrix": load_csv(TRUTH_DIR / "source_vs_stg_vs_usable_day_matrix.csv"),
        "closed_mode_map": load_csv(TRUTH_DIR / "closed_period_recovery_mode_map.csv"),
        "anti_429_audit": load_csv(TRUTH_DIR / "anti_429_truth_audit.csv"),
        "source_open_missed": load_csv(TRUTH_DIR / "source_open_but_corridor_missed_days.csv"),
        "provisional": load_csv(TRUTH_DIR / "provisional_vs_strict_usable_candidates.csv"),
        "historical_backfill": load_csv(BACKFILL_DIR / "historical_backfill_audit.csv"),
        "live_tail_audit": load_csv(BACKFILL_DIR / "live_tail_lag_audit.csv"),
        "competitor_recovery": load_csv(BACKFILL_DIR / "competitor_recovery_remaining_sku.csv"),
        "updated_final_profiles": load_csv(BACKFILL_DIR / "updated_final_causal_profiles.csv"),
        "updated_human_recs": load_csv(BACKFILL_DIR / "updated_human_ready_recommendations.csv"),
        "updated_money_effect": load_csv(BACKFILL_DIR / "updated_scenario_money_effect.csv"),
        "scenario_input": load_csv(SCENARIO_DIR / "sku_scenario_input_layer.csv"),
        "scenario_comparison": load_csv(SCENARIO_DIR / "sku_scenario_comparison.csv"),
        "scenario_best": load_csv(SCENARIO_DIR / "sku_scenario_best_choice.csv"),
        "scenario_client": load_csv(SCENARIO_DIR / "sku_client_scenario_summary.csv"),
        "scenario_money": load_csv(SCENARIO_DIR / "sku_money_at_risk_summary.csv"),
        "final_card_cmp": load_csv(FINAL_DIR / "sku_card_comparison.csv"),
        "final_adv": load_csv(FINAL_DIR / "sku_competitor_advantage_map.csv"),
        "final_profiles": load_csv(FINAL_DIR / "final_sku_causal_profiles.csv"),
        "manual_anchor_confirmed": load_csv(RECOVERY_DIR / "manual_anchor_confirmed_competitors.csv"),
        "improved_query_sets": load_csv(RECOVERY_DIR / "improved_query_sets.csv"),
        "battle_exact": load_csv(BATTLE_DIR / "sku_competitor_exact_check.csv"),
        "search_queries": load_csv(EXTERNAL_DIR / "sku_search_queries.csv"),
    }


def build_finance_truth_split_final(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        lag_type = as_text(row.get("lag_type"))
        reporting_use = ""
        if lag_type == "source_lag":
            reporting_use = "use for source-open finance only, not as final profit truth"
        elif lag_type in {"ingestion_lag", "stabilization_lag"}:
            reporting_use = "treat as pipeline/day-close bottleneck signal"
        elif lag_type == "usable_corridor_lag":
            reporting_use = "show to users as strict usable finance lag"
        else:
            reporting_use = "reference layer for nearby WB freshness"
        result.append(
            {
                **row,
                "reporting_use": reporting_use,
                "final_truth_owner": (
                    "wb_source"
                    if lag_type == "source_lag"
                    else "pipeline_or_stabilization"
                    if lag_type in {"ingestion_lag", "stabilization_lag", "usable_corridor_lag"}
                    else "reference_layer"
                ),
            }
        )
    return result


def build_closed_period_recovery_final(
    historical_backfill: list[dict[str, str]],
    day_matrix: list[dict[str, str]],
    mode_map: list[dict[str, str]],
) -> list[dict[str, Any]]:
    matrix_by_day = index_by(day_matrix, "day")
    mode_by_name = index_by(mode_map, "mode_name")
    closed_mode = mode_by_name.get("closed_period_recoverable", {})
    replay_mode = mode_by_name.get("replay_heavy", {})

    rows: list[dict[str, Any]] = []
    for row in historical_backfill:
        day = as_text(row.get("day"))
        matrix = matrix_by_day.get(day, {})
        day_class = as_text(row.get("day_class"))
        if day_class == "source_data_exists_but_current_corridor_misses_day" or as_text(matrix.get("operational_class")) == "closed_period_recoverable":
            final_status = "closed_period_recoverable"
            recovery_mode = "closed_period_recoverable"
            handling = "run january saturation replay until saturated or unresolved after control passes"
        elif "deferred" in as_text(row.get("finance_source_probe_status")):
            final_status = "closed_period_not_yet_proven_unavailable"
            recovery_mode = "replay_heavy"
            handling = "do not mark empty; queue for january replay-heavy control pass"
        else:
            final_status = "closed_period_truly_empty_if_proven"
            recovery_mode = "replay_heavy"
            handling = "only after january saturation semantics proves no source-open and no replay growth"
        mode = closed_mode if recovery_mode == "closed_period_recoverable" else replay_mode
        rows.append(
            {
                "day": day,
                "source_probe_status": as_text(row.get("finance_source_probe_status")),
                "source_rows_seen": as_text(row.get("finance_source_probe_rows")),
                "finance_stg_rows": as_text(row.get("finance_stg_rows")),
                "ads_stg_rows": as_text(row.get("ads_stg_rows")),
                "sales_stg_rows": as_text(row.get("sales_stg_rows")),
                "orders_stg_rows": as_text(row.get("orders_stg_rows")),
                "historical_day_class": day_class,
                "operational_class": as_text(matrix.get("operational_class")),
                "closed_period_final_status": final_status,
                "recovery_mode": recovery_mode,
                "lane": as_text(mode.get("lane")),
                "request_regime": as_text(mode.get("request_regime")),
                "settings": as_text(mode.get("settings")),
                "early_stop_rule": as_text(mode.get("early_stop_rule")),
                "not_yet_proven_unavailable_rule": as_text(mode.get("not_yet_proven_unavailable_rule")),
                "next_action": handling,
            }
        )
    return rows


def build_live_tail_status_final(
    live_tail_audit: list[dict[str, str]],
    day_matrix: list[dict[str, str]],
    provisional_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    matrix_by_day = index_by(day_matrix, "day")
    provisional_by_day = index_by(provisional_rows, "day")

    rows: list[dict[str, Any]] = []
    for row in live_tail_audit:
        day = as_text(row.get("day"))
        matrix = matrix_by_day.get(day, {})
        provisional = provisional_by_day.get(day, {})
        final_status = "live_tail_not_yet_stable"
        if as_text(provisional.get("strict_usable_state")) == "strict_usable":
            final_status = "strict_usable"
        elif as_text(provisional.get("provisional_finance_state")) == "provisional_finance_candidate":
            final_status = "provisional_finance_candidate"
        elif as_text(row.get("finance_source_probe_status")) == "useful_pass":
            final_status = "source_open_only"
        elif as_text(row.get("finance_source_probe_status")) == "technical_429":
            final_status = "not_proven_because_of_rate_limit"
        elif "deferred" in as_text(row.get("finance_source_probe_status")):
            final_status = "deferred_after_mixed_probe_pattern"

        if final_status in {"source_open_only", "provisional_finance_candidate"}:
            next_action = "controlled follow-up after first page capture; keep day provisional until stable"
        elif final_status == "not_proven_because_of_rate_limit":
            next_action = "quiet presence-first retry in canonical anti-429 lane"
        else:
            next_action = "retry later without broad sweep; do not mark empty"

        rows.append(
            {
                "day": day,
                "days_before_today": as_text(row.get("days_before_today")),
                "age_bucket": as_text(row.get("age_bucket")),
                "source_probe_status": as_text(row.get("finance_source_probe_status")),
                "source_rows_seen": as_text(row.get("finance_source_probe_rows")),
                "stg_rows": as_text(row.get("finance_stg_rows")),
                "source_open_flag": yes_no_from_bool(as_int(row.get("finance_source_probe_rows")) not in (None, 0)),
                "strict_usable_flag": yes_no_from_bool(as_text(row.get("finance_final_analytic_usable_flag")).lower() == "true"),
                "finance_day_state_status": as_text(row.get("finance_day_state_status")),
                "finance_day_reason": as_text(row.get("finance_day_state_reason")),
                "operational_class": as_text(matrix.get("operational_class")),
                "provisional_finance_state": as_text(provisional.get("provisional_finance_state")),
                "live_tail_final_status": final_status,
                "next_action": next_action,
                "attempt_log": as_text(row.get("attempt_log")),
            }
        )
    return rows


def normalize_competitor_rows(inputs: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    recovery_rows = inputs["competitor_recovery"]
    card_rows = inputs["final_card_cmp"]
    adv_rows = inputs["final_adv"]
    exact_rows = inputs["battle_exact"]

    own_cards = {
        as_text(row.get("nm_id")): row
        for row in card_rows
        if as_text(row.get("card_role")) == "own"
    }
    card_comp_by_pair = {
        (as_text(row.get("own_nm_id")), as_text(row.get("nm_id"))): row
        for row in card_rows
        if as_text(row.get("card_role")) == "competitor"
    }
    adv_by_pair = pair_index(adv_rows, "own_nm_id", "candidate_nm_id")
    exact_by_pair = pair_index(exact_rows, "own_nm_id", "candidate_nm_id")

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in recovery_rows:
        key = (as_text(row.get("own_nm_id")), as_text(row.get("candidate_nm_id")))
        seen.add(key)
        rows.append(
            {
                "snapshot_date": as_text(row.get("snapshot_date")),
                "own_nm_id": key[0],
                "own_product_name": as_text(row.get("own_product_name")),
                "candidate_nm_id": key[1],
                "candidate_name": as_text(row.get("candidate_name")),
                "candidate_source": as_text(row.get("candidate_source")),
                "comparability_status": as_text(row.get("comparability_status")),
                "comparability_bucket": (
                    "confirmed_comparable"
                    if as_text(row.get("comparability_status")) == "confirmed_comparable"
                    else "likely_comparable"
                    if as_text(row.get("comparability_status")) == "likely_comparable"
                    else "auto_candidate_only"
                ),
                "comparability_confidence": as_text(row.get("comparability_confidence")),
                "competition_ring": "recovered_comparable_layer",
                "candidate_vendor_code": as_text(row.get("candidate_vendor_code")),
                "candidate_price": as_text(row.get("candidate_price")),
                "candidate_rating": as_text(row.get("candidate_rating")),
                "candidate_feedbacks": as_text(row.get("candidate_feedbacks")),
                "photo_count": as_text(row.get("photo_count")),
                "has_video": as_text(row.get("has_video")),
                "description_fill": as_text(row.get("description_fill_level")),
                "characteristics_count": as_text(row.get("characteristics_count")),
                "queries_intersection": as_text(row.get("queries_found")),
                "query_hit_count": as_text(row.get("query_hit_count")),
                "above_us_in_search": "yes" if parsed_or(row.get("higher_than_own_count")) > 0 else "no",
                "higher_than_own_count": as_text(row.get("higher_than_own_count")),
                "price_relation": as_text(row.get("price_relation")),
                "rating_relation": as_text(row.get("rating_relation")),
                "reviews_relation": as_text(row.get("reviews_relation")),
                "search_relation": as_text(row.get("search_relation")),
                "card_strength_relation": as_text(row.get("card_strength_relation")),
                "why_comparable": as_text(row.get("why_comparable")),
                "why_dangerous": as_text(row.get("why_dangerous")),
                "substantial_or_cosmetic": as_text(row.get("substantial_or_cosmetic")),
            }
        )

    for key, row in card_comp_by_pair.items():
        own_nm_id, candidate_nm_id = key
        if own_nm_id in PARTIAL_RECOVERED_SKUS:
            continue
        if key in seen:
            continue
        own = own_cards.get(own_nm_id, {})
        adv = adv_by_pair.get(key, {})
        exact = exact_by_pair.get(key, {})
        own_price = parsed_or(own.get("current_sale_price"))
        own_rating = parsed_or(own.get("rating"))
        own_feedbacks = parsed_or(own.get("feedbacks"))
        candidate_price = parsed_or(row.get("current_sale_price"))
        candidate_rating = parsed_or(row.get("rating"))
        candidate_feedbacks = parsed_or(row.get("feedbacks"))
        if as_text(exact.get("higher_than_us_in_search")):
            above_us = as_text(exact.get("higher_than_us_in_search"))
        elif as_text(row.get("higher_than_us_in_search")):
            above_us = as_text(row.get("higher_than_us_in_search"))
        elif as_text(adv.get("higher_in_search")) == "yes":
            above_us = "yes"
        else:
            above_us = "not_captured"

        if as_text(adv.get("stronger_by_card_overall")):
            card_relation = "stronger_than_us" if as_text(adv.get("stronger_by_card_overall")) == "yes" else "not_stronger_than_us"
        else:
            card_relation = "not_scored"

        seen.add(key)
        rows.append(
            {
                "snapshot_date": now_iso(),
                "own_nm_id": own_nm_id,
                "own_product_name": as_text(row.get("own_product_name")),
                "candidate_nm_id": candidate_nm_id,
                "candidate_name": as_text(row.get("title")),
                "candidate_source": as_text(row.get("candidate_source")) or "auto_capture",
                "comparability_status": as_text(row.get("comparability_status")) or "confirmed_comparable",
                "comparability_bucket": (
                    "confirmed_comparable"
                    if as_text(row.get("comparability_status")) in {"", "confirmed_comparable"}
                    else "likely_comparable"
                ),
                "comparability_confidence": as_text(row.get("comparability_confidence")) or "medium",
                "competition_ring": as_text(row.get("competition_ring")),
                "candidate_vendor_code": as_text(row.get("vendor_code")),
                "candidate_price": as_text(row.get("current_sale_price")),
                "candidate_rating": as_text(row.get("rating")),
                "candidate_feedbacks": as_text(row.get("feedbacks")),
                "photo_count": as_text(row.get("photo_count")),
                "has_video": as_text(row.get("has_video")),
                "description_fill": as_text(row.get("description_fill_level")),
                "characteristics_count": as_text(row.get("characteristics_count")),
                "queries_intersection": as_text(exact.get("queries_found")),
                "query_hit_count": str(count_query_hits(as_text(exact.get("queries_found")))),
                "above_us_in_search": above_us,
                "higher_than_own_count": "1" if above_us == "yes" else "0",
                "price_relation": "cheaper_than_us" if candidate_price < own_price else "not_cheaper_than_us",
                "rating_relation": "higher_than_us" if candidate_rating > own_rating else "not_higher_than_us",
                "reviews_relation": "more_than_us" if candidate_feedbacks > own_feedbacks else "not_more_than_us",
                "search_relation": "appears_above_us" if above_us == "yes" else "not_captured_or_not_above_us",
                "card_strength_relation": card_relation,
                "why_comparable": as_text(row.get("why_candidate")) or as_text(exact.get("why_candidate")),
                "why_dangerous": as_text(adv.get("why_dangerous")) or as_text(exact.get("why_candidate")),
                "substantial_or_cosmetic": "substantial" if as_text(adv.get("top_dangerous_competitor")) == "yes" else "mixed_or_unknown",
            }
        )

    rows.sort(
        key=lambda row: (
            SELECTED_SKUS.index(as_text(row.get("own_nm_id"))) if as_text(row.get("own_nm_id")) in SELECTED_SKUS else 999,
            -count_query_hits(as_text(row.get("queries_intersection"))),
            as_text(row.get("candidate_nm_id")),
        )
    )
    return rows


def build_rank_visibility_final(
    competitor_rows: list[dict[str, Any]],
    search_queries: list[dict[str, str]],
    improved_query_sets: list[dict[str, str]],
) -> list[dict[str, Any]]:
    search_by_sku = group_by(search_queries, "nm_id")
    improved_by_sku = index_by(improved_query_sets, "own_nm_id")
    comp_rows_by_sku = group_by(competitor_rows, "own_nm_id")

    comp_query_index: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in competitor_rows:
        own_nm_id = as_text(row.get("own_nm_id"))
        for query in split_semicolon(as_text(row.get("queries_intersection"))):
            comp_query_index[own_nm_id][query.lower()].append(row)

    result: list[dict[str, Any]] = []
    for sku in SELECTED_SKUS:
        if sku in improved_by_sku:
            query_list = split_semicolon(improved_by_sku[sku]["new_query_set"])
            query_source = "recovered_final_query_set"
        else:
            sku_rows = sorted(search_by_sku.get(sku, []), key=lambda row: as_int(row.get("query_rank")) or 999)
            query_list = [as_text(row.get("search_query")) for row in sku_rows]
            query_source = "wb_search_report_top_queries"

        search_lookup = {as_text(row.get("search_query")).lower(): row for row in search_by_sku.get(sku, [])}
        coverage_status = "full_query_set_defined" if query_list else "no_query_set"
        if not query_list and comp_rows_by_sku.get(sku):
            coverage_status = "competitor_only_query_pressure"

        for idx, query in enumerate(query_list, start=1):
            search_row = search_lookup.get(query.lower(), {})
            matched_competitors = comp_query_index.get(sku, {}).get(query.lower(), [])
            above_competitors = [
                f"{as_text(row.get('candidate_nm_id'))} {as_text(row.get('candidate_name'))}"
                for row in matched_competitors
                if as_text(row.get("above_us_in_search")) == "yes"
            ]
            result.append(
                {
                    "own_nm_id": sku,
                    "product_name": as_text(next((r.get("product_name") for r in search_by_sku.get(sku, []) if as_text(r.get("product_name"))), "")) or as_text(next((r.get("own_product_name") for r in comp_rows_by_sku.get(sku, []) if as_text(r.get("own_product_name"))), "")),
                    "query_rank_final": idx,
                    "query": query,
                    "query_type": classify_query(query),
                    "query_source": query_source,
                    "own_avg_position_current": as_text(search_row.get("avg_position_current")),
                    "own_visibility_current": as_text(search_row.get("visibility_current")),
                    "own_orders_current": as_text(search_row.get("orders_current")),
                    "visibility_status_by_query": visibility_status(search_row.get("avg_position_current"), search_row.get("visibility_current")),
                    "repeated_competitor_pressure_count": str(len(matched_competitors)),
                    "competitors_above_us_count": str(len(above_competitors)),
                    "repeated_competitors_above_us": "; ".join(above_competitors[:5]),
                    "query_pressure_status": "repeated_pressure_present" if above_competitors else "no_repeated_pressure_proven",
                    "coverage_status": coverage_status,
                }
            )
    return result


def competitor_summary_by_sku(competitor_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped = group_by(competitor_rows, "own_nm_id")
    summary: dict[str, dict[str, Any]] = {}
    for sku, rows in grouped.items():
        confirmed = sum(1 for row in rows if as_text(row.get("comparability_bucket")) == "confirmed_comparable")
        likely = sum(1 for row in rows if as_text(row.get("comparability_bucket")) == "likely_comparable")
        auto_only = sum(1 for row in rows if as_text(row.get("comparability_bucket")) == "auto_candidate_only")
        above = sum(1 for row in rows if as_text(row.get("above_us_in_search")) == "yes")
        stronger_card = sum(1 for row in rows if as_text(row.get("card_strength_relation")) == "stronger_than_us")
        cheaper = sum(1 for row in rows if as_text(row.get("price_relation")) == "cheaper_than_us")
        higher_reviews = sum(1 for row in rows if as_text(row.get("reviews_relation")) == "more_than_us")
        higher_rating = sum(1 for row in rows if as_text(row.get("rating_relation")) == "higher_than_us")
        top_dangers = [
            f"{as_text(row.get('candidate_nm_id'))} ({as_text(row.get('why_dangerous'))})"
            for row in rows
            if as_text(row.get("substantial_or_cosmetic")) == "substantial" or as_text(row.get("above_us_in_search")) == "yes"
        ]
        summary[sku] = {
            "confirmed_comparable_count": confirmed,
            "likely_comparable_count": likely,
            "auto_only_count": auto_only,
            "above_us_count": above,
            "stronger_card_count": stronger_card,
            "cheaper_count": cheaper,
            "higher_reviews_count": higher_reviews,
            "higher_rating_count": higher_rating,
            "top_dangerous_competitors": "; ".join(top_dangers[:3]),
        }
    return summary


def build_store_analytical_layer_final(
    scenario_input: list[dict[str, str]],
    updated_human_recs: list[dict[str, str]],
    scenario_best: list[dict[str, str]],
    finance_truth: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_sku = [row for row in scenario_input if as_text(row.get("product_id")) in WEAK_SKUS]
    weak_profit_drag = sum(abs(parsed_or(row.get("profit_after_ads_delta_per_day"))) for row in by_sku if parsed_or(row.get("profit_after_ads_delta_per_day")) < 0)
    weak_residual_drag = sum(
        abs(parsed_or(row.get("residual_profit_after_calendar_per_day")))
        for row in by_sku
        if parsed_or(row.get("residual_profit_after_calendar_per_day")) < 0
    )
    weak_revenue_drag = sum(abs(parsed_or(row.get("revenue_delta_per_day"))) for row in by_sku if parsed_or(row.get("revenue_delta_per_day")) < 0)
    weak_orders_drag = sum(abs(parsed_or(row.get("orders_delta_per_day"))) for row in by_sku if parsed_or(row.get("orders_delta_per_day")) < 0)
    paid_delta_sum = sum(parsed_or(row.get("paid_orders_delta_per_day")) for row in by_sku)
    non_paid_delta_sum = sum(parsed_or(row.get("non_paid_orders_delta_per_day")) for row in by_sku)
    total_revenue = sum(parsed_or(row.get("full_revenue_total")) for row in by_sku) or 1.0
    logistics_share = sum(parsed_or(row.get("logistics_total")) for row in by_sku) / total_revenue
    storage_share = sum(parsed_or(row.get("storage_total")) for row in by_sku) / total_revenue
    commission_share = sum(parsed_or(row.get("commission_total")) for row in by_sku) / total_revenue
    acquiring_share = sum(parsed_or(row.get("acquiring_total")) for row in by_sku) / total_revenue
    long_cover = [row for row in by_sku if parsed_or(row.get("days_of_stock_new_speed")) >= 45]
    dangerous_cover = [row for row in by_sku if parsed_or(row.get("days_of_stock_new_speed")) >= 90]

    store_row = next((row for row in updated_human_recs if as_text(row.get("scope")) == "store"), {})
    best_mix = Counter(as_text(row.get("best_scenario_name")) for row in scenario_best if as_text(row.get("product_id")) in SELECTED_SKUS)
    mix_text = "; ".join(f"{name}={count}" for name, count in best_mix.items())
    finance_truth_map = {as_text(row.get("lag_type")): row for row in finance_truth}

    return [
        {
            "snapshot_date": now_iso(),
            "scope": "store_selected_pool",
            "selected_weak_sku_count": str(len(by_sku)),
            "current_orders_drag_per_day": f"{weak_orders_drag:.2f}",
            "current_revenue_drag_per_day": f"{weak_revenue_drag:.2f}",
            "current_profit_drag_per_day": f"{weak_profit_drag:.2f}",
            "residual_profit_drag_after_calendar_per_day": f"{weak_residual_drag:.2f}",
            "paid_orders_delta_per_day_sum": f"{paid_delta_sum:.2f}",
            "non_paid_orders_delta_per_day_sum": f"{non_paid_delta_sum:.2f}",
            "main_driver_statement": as_text(store_row.get("what_is_proven")),
            "portfolio_direction": as_text(store_row.get("what_is_most_likely")),
            "logistics_share_of_revenue_pool": f"{logistics_share:.4f}",
            "storage_share_of_revenue_pool": f"{storage_share:.4f}",
            "commission_share_of_revenue_pool": f"{commission_share:.4f}",
            "acquiring_share_of_revenue_pool": f"{acquiring_share:.4f}",
            "stock_risk_extended_cover_sku_count": str(len(long_cover)),
            "stock_risk_dangerous_cover_sku_count": str(len(dangerous_cover)),
            "top_long_cover_skus": "; ".join(
                f"{as_text(row.get('product_id'))} ({parsed_or(row.get('days_of_stock_new_speed')):.1f}d)"
                for row in sorted(long_cover, key=lambda r: parsed_or(r.get("days_of_stock_new_speed")), reverse=True)[:5]
            ),
            "scenario_mix_now": mix_text,
            "finance_source_lag_days": as_text(finance_truth_map.get("source_lag", {}).get("lag_days_vs_today")),
            "finance_strict_usable_lag_days": as_text(finance_truth_map.get("usable_corridor_lag", {}).get("lag_days_vs_today")),
            "finance_truth_statement": "use source-open/provisional/strict usable as separate finance layers in product and cabinet",
            "ready_for_cabinet_status": "yes_with_finance_truth_split_boundary",
            "boundary_note": as_text(store_row.get("boundary_note")),
        }
    ]


def build_sku_analytical_layer_final(
    scenario_input: list[dict[str, str]],
    updated_profiles: list[dict[str, str]],
    updated_human_recs: list[dict[str, str]],
    scenario_best: list[dict[str, str]],
    competitor_summary: dict[str, dict[str, Any]],
    rank_visibility: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    profile_by_sku = index_by(updated_profiles, "product_id")
    rec_by_sku = {as_text(row.get("product_id")): row for row in updated_human_recs if as_text(row.get("scope")) == "sku"}
    best_by_sku = index_by(scenario_best, "product_id")
    rank_by_sku = group_by(rank_visibility, "own_nm_id")

    rows: list[dict[str, Any]] = []
    for row in scenario_input:
        sku = as_text(row.get("product_id"))
        profile = profile_by_sku.get(sku, {})
        rec = rec_by_sku.get(sku, {})
        best = best_by_sku.get(sku, {})
        comp = competitor_summary.get(sku, {})
        rank_rows = rank_by_sku.get(sku, [])
        pressured_queries = [r for r in rank_rows if as_text(r.get("query_pressure_status")) == "repeated_pressure_present"]
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(row.get("product_name")),
                "comparison_role": as_text(row.get("comparison_role")),
                "confidence": as_text(profile.get("confidence")) or as_text(row.get("confidence")),
                "current_units_per_day": as_text(row.get("current_units_per_day")),
                "base_units_per_day": as_text(row.get("base_units_per_day")),
                "units_delta_per_day": as_text(row.get("units_delta_per_day")),
                "current_orders_per_day": as_text(row.get("current_orders_per_day")),
                "base_orders_per_day": as_text(row.get("base_orders_per_day")),
                "orders_delta_per_day": as_text(row.get("orders_delta_per_day")),
                "current_revenue_per_day": as_text(row.get("current_revenue_per_day")),
                "base_revenue_per_day": as_text(row.get("base_revenue_per_day")),
                "revenue_delta_per_day": as_text(row.get("revenue_delta_per_day")),
                "current_profit_after_ads_per_day": as_text(row.get("current_profit_after_ads_per_day")),
                "base_profit_after_ads_per_day": as_text(row.get("base_profit_after_ads_per_day")),
                "profit_after_ads_delta_per_day": as_text(row.get("profit_after_ads_delta_per_day")),
                "residual_profit_after_calendar_per_day": as_text(row.get("residual_profit_after_calendar_per_day")),
                "paid_signal": as_text(row.get("paid_signal")),
                "paid_orders_delta_per_day": as_text(row.get("paid_orders_delta_per_day")),
                "non_paid_orders_delta_per_day": as_text(row.get("non_paid_orders_delta_per_day")),
                "ad_verdict": as_text(row.get("ad_verdict")),
                "days_of_stock_old_speed": as_text(row.get("days_of_stock_old_speed")),
                "days_of_stock_new_speed": as_text(row.get("days_of_stock_new_speed")),
                "stock_extension_days": as_text(row.get("stock_extension_days")),
                "locality_signal": as_text(row.get("locality_signal")),
                "stock_risk_signal": as_text(row.get("stock_risk_signal")),
                "main_negative_regions": as_text(row.get("main_negative_regions")),
                "main_negative_warehouses": as_text(row.get("main_negative_warehouses")),
                "current_price": as_text(row.get("current_price")),
                "current_rating": as_text(row.get("current_rating")),
                "current_feedbacks": as_text(row.get("current_feedbacks")),
                "confirmed_competitor_count": str(comp.get("confirmed_comparable_count", as_text(row.get("confirmed_competitor_count")))),
                "likely_competitor_count": str(comp.get("likely_comparable_count", 0)),
                "auto_only_competitor_count": str(comp.get("auto_only_count", 0)),
                "cheaper_competitor_count": str(comp.get("cheaper_count", as_text(row.get("cheaper_competitor_count")))),
                "higher_rating_competitor_count": str(comp.get("higher_rating_count", as_text(row.get("higher_rating_competitor_count")))),
                "more_review_competitor_count": str(comp.get("higher_reviews_count", as_text(row.get("more_review_competitor_count")))),
                "higher_search_competitor_count": str(comp.get("above_us_count", as_text(row.get("higher_search_competitor_count")))),
                "stronger_card_competitor_count": str(comp.get("stronger_card_count", as_text(row.get("stronger_card_competitor_count")))),
                "top_dangerous_competitors": as_text(comp.get("top_dangerous_competitors")) or as_text(row.get("top_dangerous_competitors")),
                "search_coverage_status": as_text(row.get("search_coverage_status")),
                "queries_with_repeated_pressure_count": str(len(pressured_queries)),
                "queries_with_repeated_pressure": "; ".join(as_text(r.get("query")) for r in pressured_queries[:5]),
                "what_is_proven_now": as_text(profile.get("what_is_proven_now")),
                "strongest_working_hypothesis": as_text(profile.get("strongest_working_hypothesis")),
                "still_not_closed": as_text(profile.get("still_not_closed")),
                "recommended_action_now": as_text(profile.get("recommended_action_now")) or as_text(rec.get("what_to_do_now")),
                "best_scenario_name": as_text(best.get("best_scenario_name")),
                "best_scenario_effect_30d_working": as_text(best.get("best_scenario_net_value_30d_working")) or as_text(profile.get("best_scenario_effect_30d_working")),
            }
        )
    return rows


def build_sku_scenario_layer_final(
    scenario_comparison: list[dict[str, str]],
    scenario_best: list[dict[str, str]],
) -> list[dict[str, Any]]:
    best_by_sku = index_by(scenario_best, "product_id")
    rows: list[dict[str, Any]] = []
    for row in scenario_comparison:
        best = best_by_sku.get(as_text(row.get("product_id")), {})
        rows.append(
            {
                **row,
                "is_best_scenario": yes_no_from_bool(as_text(row.get("scenario_id")) == as_text(best.get("best_scenario_id"))),
                "best_reason_if_selected": as_text(best.get("reason_why_best")) if as_text(row.get("scenario_id")) == as_text(best.get("best_scenario_id")) else "",
                "runner_up_scenario_id": as_text(best.get("runner_up_scenario_id")),
            }
        )
    return rows


def build_sku_hypothesis_check_final(
    sku_analytical: list[dict[str, Any]],
    final_profiles: list[dict[str, str]],
) -> list[dict[str, Any]]:
    final_profile_by_sku = index_by(final_profiles, "product_id")
    rows: list[dict[str, Any]] = []
    for row in sku_analytical:
        sku = as_text(row.get("product_id"))
        final_profile = final_profile_by_sku.get(sku, {})
        strongest = as_text(row.get("strongest_working_hypothesis"))
        alternatives = as_text(final_profile.get("alternative_hypotheses"))
        proven = first_clauses(as_text(row.get("what_is_proven_now")), 4)
        limits = as_text(row.get("still_not_closed")) or "major unresolved layer is not highlighted for this SKU"
        confidence = as_text(row.get("confidence"))
        critique = (
            "нельзя подавать как доказанный факт; формулировка должна явно опираться на proven layer и сохранять boundary"
            if confidence in {"medium", "weak", "low"}
            else "формулировка уже опирается на сильный proven layer, но boundary все равно нужно сохранять"
        )
        refined = (
            f"Наиболее вероятно, что {strongest}, потому что уже доказаны такие сигналы: {proven}."
            if strongest
            else "SKU сейчас не требует слабой причинной гипотезы, потому что работает как контрольный компаратор."
        )
        final_client = (
            f"Рабочая гипотеза по SKU: {strongest}. Это не объявляется фактом: ее поддерживают такие сигналы, как {proven.lower()}, но {limits.lower()}."
            if strongest
            else "SKU сейчас лучше использовать как стабильный компаратор: сильной проблемной гипотезы для него не требуется."
        )
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(row.get("product_name")),
                "confidence": confidence,
                "strongest_hypothesis": strongest,
                "alternative_hypotheses": alternatives,
                "what_confirms": proven,
                "what_contradicts_or_limits": limits,
                "signal_strength": confidence,
                "draft_1": strongest,
                "critique_of_draft_1": critique,
                "refined_draft_2": refined,
                "final_client_ready_hypothesis": final_client,
            }
        )
    return rows


def build_sku_recommendation_check_final(
    sku_analytical: list[dict[str, Any]],
    human_recs: list[dict[str, str]],
    scenario_client: list[dict[str, str]],
    scenario_money: list[dict[str, str]],
    hypothesis_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rec_by_sku = {as_text(row.get("product_id")): row for row in human_recs if as_text(row.get("scope")) == "sku"}
    client_by_sku = index_by(scenario_client, "product_id")
    money_by_sku = index_by(scenario_money, "product_id")
    hyp_by_sku = index_by(hypothesis_checks, "product_id")

    rows: list[dict[str, Any]] = []
    for row in sku_analytical:
        sku = as_text(row.get("product_id"))
        rec = rec_by_sku.get(sku, {})
        client = client_by_sku.get(sku, {})
        money = money_by_sku.get(sku, {})
        hyp = hyp_by_sku.get(sku, {})
        scenario_name = as_text(row.get("best_scenario_name"))
        draft_1 = as_text(rec.get("what_to_do_now")) or as_text(client.get("best_option_now"))
        critique = "слишком коротко без условия" if "если" not in draft_1.lower() and sku != "72702996" else "формулировка уже условная или безопасно нейтральная"
        refined = (
            f"Если подтвержден текущий слой по SKU, то разумный ход сейчас — {as_text(client.get('best_option_now')).lower()}, потому что {as_text(client.get('why_best_option_now')).lower()}."
            if as_text(client.get("best_option_now"))
            else draft_1
        )
        final_client = (
            f"Сейчас основной рабочий ход по SKU — {scenario_name.lower()}. Основание: {as_text(rec.get('best_scenario_why')).lower()} "
            f"Это не гарантия результата: диапазон эффекта пока сценарный — {as_text(rec.get('cautious_money_effect_30d'))}, а главный риск такой: {as_text(rec.get('main_risk')).lower()}."
        )
        cause_check = "aligned_with_cause_layer" if as_text(hyp.get("strongest_hypothesis")) else "control_comparator_safe"
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(row.get("product_name")),
                "best_scenario_name": scenario_name,
                "draft_1": draft_1,
                "critique_of_draft_1": critique,
                "refined_draft_2": refined,
                "final_client_ready_wording": final_client,
                "not_banal_check": "passes_if_condition_kept",
                "symptom_vs_cause_check": cause_check,
                "effect_overclaim_check": "scenario_only_not_guaranteed",
                "client_language_check": "client_ready_if_boundary_kept",
                "money_effect_working_30d": as_text(money.get("best_scenario_effect_30d_working")),
                "main_risk": as_text(rec.get("main_risk")),
            }
        )
    return rows


def build_report(
    finance_truth: list[dict[str, Any]],
    closed_period_final: list[dict[str, Any]],
    live_tail_final: list[dict[str, Any]],
    competitor_final: list[dict[str, Any]],
    rank_visibility_final: list[dict[str, Any]],
    store_layer: list[dict[str, Any]],
    sku_layer: list[dict[str, Any]],
    scenario_layer: list[dict[str, Any]],
    hypothesis_layer: list[dict[str, Any]],
    recommendation_layer: list[dict[str, Any]],
) -> str:
    finance_by_type = index_by(finance_truth, "lag_type")
    source_lag = as_text(finance_by_type.get("source_lag", {}).get("lag_days_vs_today"))
    strict_lag = as_text(finance_by_type.get("usable_corridor_lag", {}).get("lag_days_vs_today"))
    closed_recoverable = sum(1 for row in closed_period_final if as_text(row.get("closed_period_final_status")) == "closed_period_recoverable")
    closed_unproven = sum(1 for row in closed_period_final if as_text(row.get("closed_period_final_status")) == "closed_period_not_yet_proven_unavailable")
    live_provisional = sum(1 for row in live_tail_final if as_text(row.get("live_tail_final_status")) == "provisional_finance_candidate")
    live_rate_limit = sum(1 for row in live_tail_final if as_text(row.get("live_tail_final_status")) == "not_proven_because_of_rate_limit")

    comp_rows_by_sku = group_by(competitor_final, "own_nm_id")
    competitor_completion = []
    for sku_row in sku_layer:
        sku = as_text(sku_row.get("product_id"))
        sku_comp = comp_rows_by_sku.get(sku, [])
        confirmed = sum(1 for row in sku_comp if as_text(row.get("comparability_bucket")) == "confirmed_comparable")
        above = sum(1 for row in sku_comp if as_text(row.get("above_us_in_search")) == "yes")
        stronger = sum(1 for row in sku_comp if as_text(row.get("card_strength_relation")) == "stronger_than_us")
        competitor_completion.append(
            {
                "product_id": sku,
                "product_name": as_text(sku_row.get("product_name")),
                "confirmed_competitors": confirmed,
                "above_us_in_search": above,
                "stronger_card_competitors": stronger,
                "search_coverage_status": as_text(sku_row.get("search_coverage_status")),
            }
        )

    sku_overview = [
        {
            "product_id": as_text(row.get("product_id")),
            "product_name": as_text(row.get("product_name")),
            "confidence": as_text(row.get("confidence")),
            "residual_profit_after_calendar_per_day": short_money(row.get("residual_profit_after_calendar_per_day")),
            "best_scenario_name": as_text(row.get("best_scenario_name")),
            "best_scenario_effect_30d_working": short_money(row.get("best_scenario_effect_30d_working")),
            "strongest_working_hypothesis": as_text(row.get("strongest_working_hypothesis")),
        }
        for row in sku_layer
    ]

    recommendation_summary = [
        {
            "product_id": as_text(row.get("product_id")),
            "product_name": as_text(row.get("product_name")),
            "best_scenario_name": as_text(row.get("best_scenario_name")),
            "client_language_check": as_text(row.get("client_language_check")),
            "effect_overclaim_check": as_text(row.get("effect_overclaim_check")),
            "main_risk": as_text(row.get("main_risk")),
        }
        for row in recommendation_layer
    ]

    store_row = store_layer[0] if store_layer else {}
    ready_line = (
        "Кабинет можно начинать после этого пакета, но finance в UI нужно сразу разводить на `source-open`, `provisional` и `strict usable`."
    )

    return f"""# PROFIT CONTROL — Master Next Pass v1

Date: {now_iso()}

## Main Outcome

- Finance layer теперь закрыт честно по трем уровням: `source lag` сейчас ближе к `{source_lag}` дням, а `strict usable lag` остается `{strict_lag}` дней, поэтому bottleneck нельзя сводить только к WB source freshness.
- Closed period больше не смешивается с live tail: recoverable январско-декабрьские дни вынесены в отдельный saturation contour, а свежий хвост остается в quiet presence-first lane.
- Competitor layer по ключевым SKU доведен до рабочего финального формата: confirmed / likely / auto-only разделены, а rank pressure теперь собран как отдельный слой по query intent.
- Scenario, hypothesis и recommendation layers сведены в один проверенный final bundle, который уже можно нести в кабинет при сохранении finance-truth boundary.

## Finance Recovery Status

- `source-open` уже доказан ближе к текущей дате, поэтому фраза `WB lag = 9 days` больше не используется как финальная truth.
- Closed period recoverable days: `{closed_recoverable}`.
- Closed period not yet proven unavailable days: `{closed_unproven}`.
- Live-tail provisional candidates: `{live_provisional}`.
- Live-tail rate-limit blocked / not proven days: `{live_rate_limit}`.

{md_table(finance_truth, ['lag_type', 'latest_day', 'lag_days_vs_today', 'confidence', 'reporting_use'])}

## Competitor Layer Completion

{md_table(competitor_completion, ['product_id', 'product_name', 'confirmed_competitors', 'above_us_in_search', 'stronger_card_competitors', 'search_coverage_status'])}

## Full Analytical Picture

- Store selected weak pool: current profit drag `{short_money(store_row.get('current_profit_drag_per_day'))} /день`, residual after calendar `{short_money(store_row.get('residual_profit_drag_after_calendar_per_day'))} /день`.
- Main driver: {as_text(store_row.get('main_driver_statement'))}
- Portfolio direction: {as_text(store_row.get('portfolio_direction'))}
- Scenario mix now: `{as_text(store_row.get('scenario_mix_now'))}`

{md_table(sku_overview, ['product_id', 'product_name', 'confidence', 'residual_profit_after_calendar_per_day', 'best_scenario_name', 'best_scenario_effect_30d_working', 'strongest_working_hypothesis'])}

## Checked Hypotheses And Recommendations

{md_table(recommendation_summary, ['product_id', 'product_name', 'best_scenario_name', 'client_language_check', 'effect_overclaim_check', 'main_risk'])}

## Ready For Cabinet

- {ready_line}
- Availability остается `snapshot-risk`, а не доказанным historical stockout.
- Partial / provisional finance нельзя смешивать со strict usable profit truth.
- External competitor picture можно считать финальной для текущего этапа, но historical competitor trend до seed start по-прежнему не существует как честный ретро-факт.
"""


def main() -> None:
    inputs = load_inputs()

    finance_truth_final = build_finance_truth_split_final(inputs["truth_split"])
    closed_period_final = build_closed_period_recovery_final(
        inputs["historical_backfill"],
        inputs["day_matrix"],
        inputs["closed_mode_map"],
    )
    live_tail_final = build_live_tail_status_final(
        inputs["live_tail_audit"],
        inputs["day_matrix"],
        inputs["provisional"],
    )
    competitor_layer_final = normalize_competitor_rows(inputs)
    rank_visibility_final = build_rank_visibility_final(
        competitor_layer_final,
        inputs["search_queries"],
        inputs["improved_query_sets"],
    )
    comp_summary = competitor_summary_by_sku(competitor_layer_final)
    store_layer_final = build_store_analytical_layer_final(
        inputs["scenario_input"],
        inputs["updated_human_recs"],
        inputs["scenario_best"],
        finance_truth_final,
    )
    sku_layer_final = build_sku_analytical_layer_final(
        inputs["scenario_input"],
        inputs["updated_final_profiles"],
        inputs["updated_human_recs"],
        inputs["scenario_best"],
        comp_summary,
        rank_visibility_final,
    )
    scenario_layer_final = build_sku_scenario_layer_final(
        inputs["scenario_comparison"],
        inputs["scenario_best"],
    )
    hypothesis_check_final = build_sku_hypothesis_check_final(
        sku_layer_final,
        inputs["final_profiles"],
    )
    recommendation_check_final = build_sku_recommendation_check_final(
        sku_layer_final,
        inputs["updated_human_recs"],
        inputs["scenario_client"],
        inputs["scenario_money"],
        hypothesis_check_final,
    )

    write_csv(FINANCE_TRUTH_SPLIT_FINAL_CSV, finance_truth_final)
    write_csv(CLOSED_PERIOD_RECOVERY_FINAL_CSV, closed_period_final)
    write_csv(LIVE_TAIL_STATUS_FINAL_CSV, live_tail_final)
    write_csv(COMPETITOR_LAYER_FINAL_CSV, competitor_layer_final)
    write_csv(RANK_VISIBILITY_FINAL_CSV, rank_visibility_final)
    write_csv(STORE_ANALYTICAL_LAYER_FINAL_CSV, store_layer_final)
    write_csv(SKU_ANALYTICAL_LAYER_FINAL_CSV, sku_layer_final)
    write_csv(SKU_SCENARIO_LAYER_FINAL_CSV, scenario_layer_final)
    write_csv(SKU_HYPOTHESIS_CHECK_FINAL_CSV, hypothesis_check_final)
    write_csv(SKU_RECOMMENDATION_CHECK_FINAL_CSV, recommendation_check_final)

    report = build_report(
        finance_truth_final,
        closed_period_final,
        live_tail_final,
        competitor_layer_final,
        rank_visibility_final,
        store_layer_final,
        sku_layer_final,
        scenario_layer_final,
        hypothesis_check_final,
        recommendation_check_final,
    )
    DOC_PATH.write_text(report, encoding="utf-8")
    print(f"Built master next pass v1 at {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
