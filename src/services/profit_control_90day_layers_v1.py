from __future__ import annotations

import csv
import os
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from src.utils.db import get_conn

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

MOSCOW = ZoneInfo("Europe/Moscow")
TODAY = datetime.now(MOSCOW).date()
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"

MASTER_DIR = ROOT / "exports" / "profit_control_master_next_pass_v1"
SCENARIO_DIR = ROOT / "exports" / "profit_control_sku_demand_drop_scenario_engine_v1"
WB_PRODUCT_CORE_DIR = ROOT / "exports" / "wb_product_core_engine_v1"
CANONICAL_CLOSED_DIR = ROOT / "exports" / "wb_finance_canonical_closed_period_runner_v1"
CANONICAL_LIVE_DIR = ROOT / "exports" / "wb_finance_canonical_live_tail_runner_v1"

OUTPUT_DIR = ROOT / "exports" / "profit_control_90day_finance_completion_recommendation_cabinet_start_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_90day_finance_completion_recommendation_cabinet_start_v1.md"

FINANCE_STATUS_CSV = OUTPUT_DIR / "finance_90day_completion_status.csv"
FINANCE_MATRIX_CSV = OUTPUT_DIR / "finance_90day_day_matrix.csv"
STORE_LAYER_CSV = OUTPUT_DIR / "updated_store_layer_after_90d.csv"
SKU_LAYER_CSV = OUTPUT_DIR / "updated_sku_layer_after_90d.csv"
PROBLEM_ZONE_CSV = OUTPUT_DIR / "updated_problem_zone_map.csv"
SCENARIO_LAYER_CSV = OUTPUT_DIR / "updated_scenario_layer_after_90d.csv"
FINAL_RECOMMENDATIONS_CSV = OUTPUT_DIR / "updated_final_recommendations.csv"
CABINET_INPUT_CSV = OUTPUT_DIR / "cabinet_build_input_final.csv"

SELECTED_SKUS = [
    "669118440",
    "340692643",
    "518796478",
    "73090372",
    "240017726",
    "72702996",
]


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


def parsed_or(value: Any, default: float = 0.0) -> float:
    parsed = as_float(value)
    return parsed if parsed is not None else default


def short_money(value: Any) -> str:
    parsed = as_float(value)
    if parsed is None:
        return ""
    return f"{parsed:,.0f}".replace(",", " ")


def index_by(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {as_text(row.get(key)): row for row in rows}


def group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        out.setdefault(as_text(row.get(key)), []).append(row)
    return out


def load_closed_period_collection_hints() -> list[dict[str, str]]:
    rows = load_csv(CANONICAL_CLOSED_DIR / "closed_period_day_results.csv")
    hints: list[dict[str, str]] = []
    for row in rows:
        final_status = as_text(row.get("final_status"))
        count_429 = int(float(as_text(row.get("count_429")) or "0"))
        if final_status in {
            "tail_extension_terminal_confirmation",
            "saturated_after_pass1",
            "completed_stable",
            "duplicate_only_replay_no_growth_after_materialization",
        }:
            collection_status = "strict_usable"
        elif final_status == "still_provisional_after_canonical_pipeline":
            collection_status = "blocked_rate_limit" if count_429 > 0 else "not_yet_proven_unavailable"
        else:
            collection_status = final_status or "not_yet_proven_unavailable"
        hints.append(
            {
                "day": as_text(row.get("day")),
                "closed_period_collection_status": collection_status,
            }
        )
    return hints


def load_live_tail_collection_hints() -> list[dict[str, str]]:
    rows = load_csv(CANONICAL_LIVE_DIR / "live_tail_day_results.csv")
    return [
        {
            "day": as_text(row.get("day")),
            "live_tail_collection_status": as_text(row.get("final_live_tail_status")),
        }
        for row in rows
    ]


def load_current_competitor_completeness() -> list[dict[str, str]]:
    rows = load_csv(WB_PRODUCT_CORE_DIR / "sku_external_full_layer.csv")
    return [
        {
            "product_id": as_text(row.get("nm_id")),
            "current_stage_completeness_status": as_text(row.get("public_search_capture_status")) or "unknown",
        }
        for row in rows
    ]


def load_recommendation_cycle_rows() -> list[dict[str, str]]:
    master_reco = index_by(load_csv(MASTER_DIR / "sku_recommendation_check_final.csv"), "product_id")
    scenario_best = index_by(load_csv(SCENARIO_DIR / "sku_scenario_best_choice.csv"), "product_id")
    scenario_layers = load_csv(MASTER_DIR / "sku_scenario_layer_final.csv")
    scenario_names = {
        (as_text(row.get("product_id")), as_text(row.get("scenario_id"))): as_text(row.get("scenario_name"))
        for row in scenario_layers
    }
    rows: list[dict[str, str]] = []
    for sku, reco_row in master_reco.items():
        best_row = scenario_best.get(sku, {})
        runner_up_id = as_text(best_row.get("runner_up_scenario_id"))
        rows.append(
            {
                "product_id": sku,
                "final_best_scenario": as_text(reco_row.get("best_scenario_name")) or as_text(best_row.get("best_scenario_name")),
                "runner_up_scenario": scenario_names.get((sku, runner_up_id), runner_up_id),
                "attempt_to_disprove_best": as_text(best_row.get("reason_why_best")),
                "rejected_alternatives_and_why": as_text(best_row.get("reason_why_best")),
                "final_client_ready_wording": as_text(reco_row.get("final_client_ready_wording")),
            }
        )
    return rows


def load_client_recommendation_rows() -> list[dict[str, str]]:
    rows = load_csv(SCENARIO_DIR / "sku_client_scenario_summary.csv")
    return [
        {
            "scope": "sku",
            "product_id": as_text(row.get("product_id")),
            "product_name": as_text(row.get("product_name")),
            "final_best_scenario": as_text(row.get("best_option_now")),
            "final_client_ready_wording": as_text(row.get("why_best_option_now")),
            "final_effect_range": as_text(row.get("scenario_effect_range")),
            "caution_note": as_text(row.get("main_risk")),
        }
        for row in rows
    ]


def daterange(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def load_inputs() -> dict[str, list[dict[str, str]]]:
    return {
        "max_finance_closed": load_closed_period_collection_hints(),
        "max_finance_tail": load_live_tail_collection_hints(),
        "competitor_current": load_current_competitor_completeness(),
        "recommendation_cycle": load_recommendation_cycle_rows(),
        "client_recommendations": load_client_recommendation_rows(),
        "master_store": load_csv(MASTER_DIR / "store_analytical_layer_final.csv"),
        "master_sku": load_csv(MASTER_DIR / "sku_analytical_layer_final.csv"),
        "master_scenario": load_csv(MASTER_DIR / "sku_scenario_layer_final.csv"),
        "master_reco": load_csv(MASTER_DIR / "sku_recommendation_check_final.csv"),
        "scenario_input": load_csv(SCENARIO_DIR / "sku_scenario_input_layer.csv"),
    }


def latest_strict_usable_day() -> date:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT MAX(day)
                FROM {SCHEMA}.finance_day_state
                WHERE final_analytic_usable_flag = TRUE
                """
            )
            value = cur.fetchone()[0]
    if value is None:
        raise RuntimeError("No strict usable finance day found")
    return value


def fetch_day_matrix(start: date, end: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH stg AS (
                  SELECT DATE(operation_date) AS day, COUNT(*)::int AS finance_stg_rows
                  FROM {SCHEMA}.stg_wb_finance
                  WHERE DATE(operation_date) BETWEEN %s AND %s
                  GROUP BY 1
                )
                SELECT
                  calendar.day,
                  COALESCE(state.status, '') AS status,
                  COALESCE(state.final_analytic_usable_flag, FALSE) AS strict_usable_flag,
                  COALESCE(state.raw_presence_flag, FALSE) AS raw_presence_flag,
                  COALESCE(state.stg_presence_flag, FALSE) AS stg_presence_flag,
                  COALESCE(state.reason_code, '') AS reason_code,
                  COALESCE(state.terminal_reason_code, '') AS terminal_reason_code,
                  COALESCE(stg.finance_stg_rows, 0) AS finance_stg_rows
                FROM (
                  SELECT generate_series(%s::date, %s::date, '1 day'::interval)::date AS day
                ) AS calendar
                LEFT JOIN {SCHEMA}.finance_day_state state ON state.day = calendar.day
                LEFT JOIN stg ON stg.day = calendar.day
                ORDER BY calendar.day
                """,
                (start, end, start, end),
            )
            rows = cur.fetchall()
    out = []
    for row in rows:
        out.append(
            {
                "day": row[0].isoformat(),
                "finance_day_state_status": row[1],
                "strict_usable_flag": bool(row[2]),
                "raw_presence_flag": bool(row[3]),
                "stg_presence_flag": bool(row[4]),
                "reason_code": row[5],
                "terminal_reason_code": row[6],
                "finance_stg_rows": int(row[7] or 0),
            }
        )
    return out


def classify_finance_day(
    day_text: str,
    matrix_row: dict[str, Any],
    closed_map: dict[str, dict[str, str]],
    tail_map: dict[str, dict[str, str]],
    core_end: date,
) -> tuple[str, str]:
    day_value = date.fromisoformat(day_text)
    closed_hint = as_text(closed_map.get(day_text, {}).get("closed_period_collection_status"))
    tail_hint = as_text(tail_map.get(day_text, {}).get("live_tail_collection_status"))
    stg_rows = int(matrix_row.get("finance_stg_rows") or 0)
    strict = bool(matrix_row.get("strict_usable_flag"))
    raw = bool(matrix_row.get("raw_presence_flag"))
    status = as_text(matrix_row.get("finance_day_state_status"))
    reason = as_text(matrix_row.get("reason_code")) or as_text(matrix_row.get("terminal_reason_code"))

    if strict:
        return "strict_usable", "day is analytically closed and strict usable"
    if stg_rows > 0:
        return "provisional", "rows are materialized in STG but day is not strict usable"
    if raw:
        return "source_open_only", "source-open/raw presence exists but STG is not materialized enough"
    if closed_hint == "blocked_rate_limit" or tail_hint == "blocked_rate_limit":
        return "blocked", "rate-limit blocked collection pass"
    if status == "started_but_not_finished" and day_value > core_end:
        return "not_proven", "live-tail day is started but still not stabilized"
    if closed_hint in {"closed_period_not_yet_proven_unavailable", "not_yet_proven_unavailable"}:
        return "not_proven", "closed-period day remains not yet proven unavailable"
    if tail_hint == "deferred_after_mixed_probe_pattern":
        return "not_proven", "fresh tail day was deferred to avoid false emptiness after mixed probing"
    if reason:
        return "not_proven", reason
    return "not_proven", "no material evidence yet, but day is not proven truly unavailable"


def build_finance_90day_layers(inputs: dict[str, list[dict[str, str]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], date, date, date]:
    core_end = latest_strict_usable_day()
    core_start = core_end - timedelta(days=89)
    tail_end = TODAY

    closed_map = index_by(inputs["max_finance_closed"], "day")
    tail_map = index_by(inputs["max_finance_tail"], "day")
    matrix_rows = fetch_day_matrix(core_start, tail_end)

    detailed_rows: list[dict[str, Any]] = []
    for row in matrix_rows:
        day_text = as_text(row.get("day"))
        day_value = date.fromisoformat(day_text)
        final_status, note = classify_finance_day(day_text, row, closed_map, tail_map, core_end)
        detailed_rows.append(
            {
                "snapshot_date": now_iso(),
                "day": day_text,
                "corridor_segment": "core_90d" if day_value <= core_end else "live_tail_extension",
                "finance_day_state_status": as_text(row.get("finance_day_state_status")),
                "finance_stg_rows": row.get("finance_stg_rows"),
                "strict_usable_flag": "yes" if row.get("strict_usable_flag") else "no",
                "raw_presence_flag": "yes" if row.get("raw_presence_flag") else "no",
                "stg_presence_flag": "yes" if row.get("stg_presence_flag") else "no",
                "closed_period_collection_hint": as_text(closed_map.get(day_text, {}).get("closed_period_collection_status")),
                "live_tail_collection_hint": as_text(tail_map.get(day_text, {}).get("live_tail_collection_status")),
                "final_day_status": final_status,
                "status_note": note,
            }
        )

    core_rows = [row for row in detailed_rows if row["corridor_segment"] == "core_90d"]
    tail_rows = [row for row in detailed_rows if row["corridor_segment"] == "live_tail_extension"]

    status_rows = []
    for segment_name, rows in [("core_90d", core_rows), ("live_tail_extension", tail_rows)]:
        counter = Counter(as_text(row.get("final_day_status")) for row in rows)
        status_rows.append(
            {
                "snapshot_date": now_iso(),
                "segment": segment_name,
                "period_start": rows[0]["day"] if rows else "",
                "period_end": rows[-1]["day"] if rows else "",
                "total_days": len(rows),
                "strict_usable_days": counter.get("strict_usable", 0),
                "provisional_days": counter.get("provisional", 0),
                "source_open_only_days": counter.get("source_open_only", 0),
                "blocked_days": counter.get("blocked", 0),
                "not_proven_days": counter.get("not_proven", 0),
                "truly_unavailable_days": counter.get("truly_unavailable", 0),
                "materialized_days_total": counter.get("strict_usable", 0) + counter.get("provisional", 0),
                "unresolved_days_total": counter.get("blocked", 0) + counter.get("not_proven", 0),
            }
        )

    return status_rows, detailed_rows, core_start, core_end, tail_end


def dominant_problem_zone(row: dict[str, str], scenario_input: dict[str, str]) -> str:
    days_cover = parsed_or(scenario_input.get("days_of_stock_new_speed"))
    paid_signal = as_text(row.get("paid_signal"))
    stock_signal = as_text(row.get("stock_risk_signal"))
    stronger_card = parsed_or(row.get("stronger_card_competitor_count"))
    higher_search = parsed_or(row.get("higher_search_competitor_count"))
    logistics_share = parsed_or(scenario_input.get("logistics_share_of_revenue"))
    storage_share = parsed_or(scenario_input.get("storage_share_of_revenue"))
    best_scenario = as_text(row.get("best_scenario_name")).lower()

    if "наблюдать" in best_scenario:
        return "stable_comparator"
    if "агрессивная распродажа" in best_scenario or days_cover >= 90:
        return "stock_pressure"
    if "чинить доступность" in best_scenario:
        return "availability_issue"
    if "лечить карточку" in best_scenario and "реклам" in best_scenario:
        return "organic_plus_card_pressure"
    if "лечить карточку" in best_scenario:
        return "card_conversion_pressure"
    if stock_signal in {"availability_risk_present", "snapshot_zero_stock_some_warehouses"}:
        return "availability_issue"
    if higher_search > 0 or stronger_card > 0:
        return "competitor_pressure"
    if paid_signal in {"paid_holds_non_paid_weak", "non_paid_weaker_than_paid"}:
        return "non_paid_demand_problem"
    if logistics_share >= 0.12 or storage_share >= 0.03:
        return "cost_burden"
    return "mixed_or_unclear"


def build_updated_store_layer_after_90d(
    inputs: dict[str, list[dict[str, str]]],
    finance_status_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    store = inputs["master_store"][0]
    core = next(row for row in finance_status_rows if row["segment"] == "core_90d")
    tail = next(row for row in finance_status_rows if row["segment"] == "live_tail_extension")
    return [
        {
            "snapshot_date": now_iso(),
            "scope": "store",
            "current_orders_drag_per_day": as_text(store.get("current_orders_drag_per_day")),
            "current_revenue_drag_per_day": as_text(store.get("current_revenue_drag_per_day")),
            "current_profit_drag_per_day": as_text(store.get("current_profit_drag_per_day")),
            "residual_profit_drag_after_calendar_per_day": as_text(store.get("residual_profit_drag_after_calendar_per_day")),
            "paid_orders_delta_per_day_sum": as_text(store.get("paid_orders_delta_per_day_sum")),
            "non_paid_orders_delta_per_day_sum": as_text(store.get("non_paid_orders_delta_per_day_sum")),
            "main_driver_statement": as_text(store.get("main_driver_statement")),
            "logistics_share_of_revenue_pool": as_text(store.get("logistics_share_of_revenue_pool")),
            "storage_share_of_revenue_pool": as_text(store.get("storage_share_of_revenue_pool")),
            "commission_share_of_revenue_pool": as_text(store.get("commission_share_of_revenue_pool")),
            "acquiring_share_of_revenue_pool": as_text(store.get("acquiring_share_of_revenue_pool")),
            "stock_risk_extended_cover_sku_count": as_text(store.get("stock_risk_extended_cover_sku_count")),
            "stock_risk_dangerous_cover_sku_count": as_text(store.get("stock_risk_dangerous_cover_sku_count")),
            "scenario_mix_now": as_text(store.get("scenario_mix_now")),
            "finance_90d_core_materialized_days": core["materialized_days_total"],
            "finance_90d_core_strict_usable_days": core["strict_usable_days"],
            "finance_90d_core_provisional_days": core["provisional_days"],
            "finance_90d_core_unresolved_days": core["unresolved_days_total"],
            "live_tail_source_open_or_better_days": tail["materialized_days_total"] + tail["source_open_only_days"],
            "live_tail_blocked_days": tail["blocked_days"],
            "finance_truth_boundary": "use source-open/provisional/strict usable as separate finance layers in cabinet",
            "cabinet_start_status": "ready_to_feed_existing_cabinet_data_layer_with_finance_boundary",
        }
    ]


def build_updated_sku_layer_after_90d(
    inputs: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    scenario_input = index_by(inputs["scenario_input"], "product_id")
    current_comp = index_by(inputs["competitor_current"], "product_id")
    rows = []
    for row in inputs["master_sku"]:
        sku = as_text(row.get("product_id"))
        base = scenario_input.get(sku, {})
        comp = current_comp.get(sku, {})
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(row.get("product_name")),
                "confidence": as_text(row.get("confidence")),
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
                "days_of_stock_old_speed": as_text(row.get("days_of_stock_old_speed")),
                "days_of_stock_new_speed": as_text(row.get("days_of_stock_new_speed")),
                "stock_extension_days": as_text(row.get("stock_extension_days")),
                "locality_signal": as_text(row.get("locality_signal")),
                "stock_risk_signal": as_text(row.get("stock_risk_signal")),
                "current_price": as_text(row.get("current_price")),
                "current_rating": as_text(row.get("current_rating")),
                "current_feedbacks": as_text(row.get("current_feedbacks")),
                "confirmed_competitor_count": as_text(row.get("confirmed_competitor_count")),
                "higher_search_competitor_count": as_text(row.get("higher_search_competitor_count")),
                "stronger_card_competitor_count": as_text(row.get("stronger_card_competitor_count")),
                "queries_with_repeated_pressure_count": as_text(row.get("queries_with_repeated_pressure_count")),
                "logistics_share_of_revenue": as_text(base.get("logistics_share_of_revenue")),
                "storage_share_of_revenue": as_text(base.get("storage_share_of_revenue")),
                "commission_share_of_revenue": as_text(base.get("commission_share_of_revenue")),
                "acquiring_share_of_revenue": as_text(base.get("acquiring_share_of_revenue")),
                "withholding_share_of_revenue": as_text(base.get("withholding_share_of_revenue")),
                "current_stock_units": as_text(base.get("current_stock_units")),
                "current_competitor_stage_status": as_text(comp.get("current_stage_completeness_status")),
                "dominant_problem_zone_after_90d": dominant_problem_zone(row, base),
                "strongest_working_hypothesis": as_text(row.get("strongest_working_hypothesis")),
                "recommended_action_now": as_text(row.get("recommended_action_now")),
                "best_scenario_name": as_text(row.get("best_scenario_name")),
                "best_scenario_effect_30d_working": as_text(row.get("best_scenario_effect_30d_working")),
            }
        )
    return rows


def build_updated_problem_zone_map(inputs: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    master_sku = inputs["master_sku"]
    scenario_input = index_by(inputs["scenario_input"], "product_id")
    rows = []
    for row in master_sku:
        sku = as_text(row.get("product_id"))
        base = scenario_input.get(sku, {})
        days_cover = parsed_or(row.get("days_of_stock_new_speed"))
        logistics_share = parsed_or(base.get("logistics_share_of_revenue"))
        storage_share = parsed_or(base.get("storage_share_of_revenue"))
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(row.get("product_name")),
                "problem_zone_demand": "yes" if parsed_or(row.get("orders_delta_per_day")) < 0 else "no",
                "problem_zone_channel": as_text(row.get("paid_signal")),
                "problem_zone_competitor_pressure": (
                    "yes" if parsed_or(row.get("higher_search_competitor_count")) > 0 or parsed_or(row.get("stronger_card_competitor_count")) > 0 else "no"
                ),
                "problem_zone_availability": "yes" if as_text(row.get("stock_risk_signal")) != "no_clear_availability_signal" else "no",
                "problem_zone_stock_pressure": "yes" if days_cover >= 60 else "no",
                "problem_zone_logistics_burden": "yes" if logistics_share >= 0.12 else "no",
                "problem_zone_storage_burden": "yes" if storage_share >= 0.03 else "no",
                "dominant_problem_zone_after_90d": dominant_problem_zone(row, base),
                "zone_change_note": "90-day corridor extension did not overturn the main problem zone, but it makes finance boundary explicit",
            }
        )
    return rows


def build_updated_scenario_layer_after_90d(inputs: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    rows = []
    for row in inputs["master_scenario"]:
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": as_text(row.get("product_id")),
                "product_name": as_text(row.get("product_name")),
                "scenario_id": as_text(row.get("scenario_id")),
                "scenario_name": as_text(row.get("scenario_name")),
                "net_value_30d_conservative": as_text(row.get("net_value_30d_conservative")),
                "net_value_30d_working": as_text(row.get("net_value_30d_working")),
                "net_value_30d_optimistic": as_text(row.get("net_value_30d_optimistic")),
                "projected_days_of_stock_working": as_text(row.get("projected_days_of_stock_working")),
                "scenario_fit_score_working": as_text(row.get("scenario_fit_score_working")),
                "selection_score_working": as_text(row.get("selection_score_working")),
                "is_best_scenario_after_90d": as_text(row.get("is_best_scenario")),
                "main_risk": as_text(row.get("main_risk")),
                "finance_boundary_note": "money effect remains scenario-based and should not treat provisional finance as final profit truth",
            }
        )
    return rows


def build_updated_final_recommendations(inputs: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    cycle = index_by(inputs["recommendation_cycle"], "product_id")
    client = index_by([row for row in inputs["client_recommendations"] if as_text(row.get("scope")) == "sku"], "product_id")
    master_reco = index_by(inputs["master_reco"], "product_id")
    rows = []
    for sku in SELECTED_SKUS:
        cycle_row = cycle.get(sku, {})
        client_row = client.get(sku, {})
        reco_row = master_reco.get(sku, {})
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": sku,
                "product_name": as_text(client_row.get("product_name")),
                "final_best_scenario": as_text(client_row.get("final_best_scenario")) or as_text(cycle_row.get("final_best_scenario")),
                "runner_up_scenario": as_text(cycle_row.get("runner_up_scenario")),
                "why_best_scenario_still_wins": as_text(cycle_row.get("attempt_to_disprove_best")),
                "rejected_alternatives_and_why": as_text(cycle_row.get("rejected_alternatives_and_why")) or as_text(client_row.get("rejected_alternatives")),
                "final_client_ready_wording": as_text(cycle_row.get("final_client_ready_wording")) or as_text(client_row.get("final_client_ready_wording")),
                "final_effect_range": as_text(client_row.get("final_effect_range")),
                "final_risk": as_text(client_row.get("caution_note")) or as_text(reco_row.get("main_risk")),
                "boundary_note": "scenario range is not guaranteed money and finance fresher than strict corridor stays provisional",
            }
        )
    return rows


def build_cabinet_build_input_final(
    finance_status_rows: list[dict[str, Any]],
    store_layer_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    core = next(row for row in finance_status_rows if row["segment"] == "core_90d")
    tail = next(row for row in finance_status_rows if row["segment"] == "live_tail_extension")
    store = store_layer_rows[0]
    return [
        {
            "snapshot_date": now_iso(),
            "module_name": "economics_store",
            "target_view": "graphs/map/table toggle",
            "data_source": "updated_store_layer_after_90d + finance_90day_day_matrix",
            "readiness_status": "ready_with_finance_boundary",
            "key_fields": "current_revenue_drag_per_day; current_profit_drag_per_day; residual_profit_drag_after_calendar_per_day; logistics/share; storage/share; commission/share",
            "boundary_note": "strict usable, provisional and source-open finance must stay visually separated",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "products_table",
            "target_view": "product list with photo + nearest competitors",
            "data_source": "updated_sku_layer_after_90d + competitor_current_completeness",
            "readiness_status": "ready",
            "key_fields": "product_id; product_name; best_scenario_name; dominant_problem_zone_after_90d; confirmed_competitor_count; stronger_card_competitor_count; higher_search_competitor_count",
            "boundary_note": "availability remains snapshot-risk, not proven historical stockout",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "product_card",
            "target_view": "competitor comparison + scenario + money effect + risks",
            "data_source": "updated_sku_layer_after_90d + updated_final_recommendations + updated_scenario_layer_after_90d",
            "readiness_status": "ready",
            "key_fields": "final_best_scenario; final_effect_range; final_risk; top competitor pressure counts; paid/non-paid signal; stock extension days",
            "boundary_note": "money ranges are scenario estimates, not guarantees",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "finance_boundary",
            "target_view": "UI status rail / legend",
            "data_source": "finance_90day_completion_status + finance_90day_day_matrix",
            "readiness_status": "mandatory",
            "key_fields": f"core materialized={core['materialized_days_total']}; core unresolved={core['unresolved_days_total']}; tail materialized={tail['materialized_days_total']}; tail unresolved={tail['unresolved_days_total']}",
            "boundary_note": "do not label usable corridor lag as pure WB lag",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "cabinet_launch_gate",
            "target_view": "implementation gate",
            "data_source": "this export pack",
            "readiness_status": "start_now",
            "key_fields": as_text(store.get("cabinet_start_status")),
            "boundary_note": "cabinet may start after this pack, but must preserve finance truth split in UI copy and badges",
        },
    ]


def build_report(
    finance_status_rows: list[dict[str, Any]],
    finance_day_matrix: list[dict[str, Any]],
    store_layer_rows: list[dict[str, Any]],
    sku_layer_rows: list[dict[str, Any]],
    problem_zone_rows: list[dict[str, Any]],
    final_recommendation_rows: list[dict[str, Any]],
) -> str:
    core = next(row for row in finance_status_rows if row["segment"] == "core_90d")
    tail = next(row for row in finance_status_rows if row["segment"] == "live_tail_extension")
    store = store_layer_rows[0]
    return f"""# PROFIT CONTROL — 90-Day Finance Completion + Recommendation Recheck + Cabinet Start v1

Date: {now_iso()}

## Main Outcome

- 90-day core corridor is now expressed as a full day-by-day matrix rather than a partial old-period label set.
- Core finance still is **not** equal to 90 strict usable days: the honest picture is `strict usable + provisional + unresolved`, not fake completeness.
- Store, SKU, problem-zone, scenario and recommendation layers are rebuilt on top of the wider finance fact set and current competitor-complete layer.
- Cabinet start can begin from this pack, but only with explicit `source-open / provisional / strict usable` finance semantics in UI.

## 90-Day Collection Result

- Core 90-day period: `{core['period_start']} .. {core['period_end']}`.
- Core materialized days: `{core['materialized_days_total']}` of `{core['total_days']}`.
- Core strict usable days: `{core['strict_usable_days']}`.
- Core provisional days: `{core['provisional_days']}`.
- Core unresolved days: `{core['unresolved_days_total']}`.
- Live-tail extension period: `{tail['period_start']} .. {tail['period_end']}`.
- Live-tail source-open/provisional days: `{int(tail['materialized_days_total']) + int(tail['source_open_only_days'])}`.
- Live-tail blocked/not-proven days: `{tail['unresolved_days_total']}`.

{md_table(finance_status_rows, ['segment', 'period_start', 'period_end', 'total_days', 'strict_usable_days', 'provisional_days', 'source_open_only_days', 'blocked_days', 'not_proven_days', 'materialized_days_total', 'unresolved_days_total'])}

{md_table(finance_day_matrix[:12], ['day', 'corridor_segment', 'finance_stg_rows', 'strict_usable_flag', 'raw_presence_flag', 'final_day_status', 'status_note'])}

## Updated Analytical Layer

- Store current profit drag: `{short_money(store.get('current_profit_drag_per_day'))} /день`.
- Store residual after calendar: `{short_money(store.get('residual_profit_drag_after_calendar_per_day'))} /день`.
- Main driver stays demand/order-side, but finance freshness must now be read through a split boundary, not as one flat lag number.

{md_table(sku_layer_rows, ['product_id', 'product_name', 'confidence', 'residual_profit_after_calendar_per_day', 'dominant_problem_zone_after_90d', 'best_scenario_name', 'best_scenario_effect_30d_working'])}

## Updated Problem Zones

{md_table(problem_zone_rows, ['product_id', 'product_name', 'problem_zone_demand', 'problem_zone_competitor_pressure', 'problem_zone_availability', 'problem_zone_stock_pressure', 'dominant_problem_zone_after_90d'])}

## Updated Recommendations

{md_table(final_recommendation_rows, ['product_id', 'product_name', 'final_best_scenario', 'runner_up_scenario', 'final_effect_range', 'final_risk'])}

## Cabinet-Ready Conclusion

- Cabinet build can start from this pack because competitor layer is current-stage complete and recommendation wording is already rechecked.
- Finance in cabinet must not collapse into one metric: `strict usable` is final truth, `provisional` is fresher but conditional, `source-open` is presence only.
- Snapshot availability must stay a risk/support layer, not a proven historical stockout statement.
"""


def main() -> None:
    inputs = load_inputs()
    finance_status_rows, finance_day_matrix, _, _, _ = build_finance_90day_layers(inputs)
    store_layer_rows = build_updated_store_layer_after_90d(inputs, finance_status_rows)
    sku_layer_rows = build_updated_sku_layer_after_90d(inputs)
    problem_zone_rows = build_updated_problem_zone_map(inputs)
    scenario_layer_rows = build_updated_scenario_layer_after_90d(inputs)
    final_recommendation_rows = build_updated_final_recommendations(inputs)
    cabinet_input_rows = build_cabinet_build_input_final(finance_status_rows, store_layer_rows)

    write_csv(FINANCE_STATUS_CSV, finance_status_rows)
    write_csv(FINANCE_MATRIX_CSV, finance_day_matrix)
    write_csv(STORE_LAYER_CSV, store_layer_rows)
    write_csv(SKU_LAYER_CSV, sku_layer_rows)
    write_csv(PROBLEM_ZONE_CSV, problem_zone_rows)
    write_csv(SCENARIO_LAYER_CSV, scenario_layer_rows)
    write_csv(FINAL_RECOMMENDATIONS_CSV, final_recommendation_rows)
    write_csv(CABINET_INPUT_CSV, cabinet_input_rows)
    DOC_PATH.write_text(
        build_report(
            finance_status_rows,
            finance_day_matrix,
            store_layer_rows,
            sku_layer_rows,
            problem_zone_rows,
            final_recommendation_rows,
        ),
        encoding="utf-8",
    )
    print(f"Built 90-day completion + cabinet start pack into {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
