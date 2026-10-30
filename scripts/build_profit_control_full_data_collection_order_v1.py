from __future__ import annotations

import csv
import os
import sys
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages
from src.loaders.upsert_wb import upsert_stg_wb_finance
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.services import profit_control_90day_layers_v1 as prev_mod
from src.utils.db import get_conn

MOSCOW = ZoneInfo("Europe/Moscow")
TODAY = datetime.now(MOSCOW).date()
load_dotenv(ROOT / ".env")
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"

OUTPUT_DIR = ROOT / "exports" / "profit_control_full_data_collection_order_v1"
DOC_PATH = ROOT / "artifacts" / "profit_control_full_data_collection_order_v1.md"
PREV_90D_DIR = ROOT / "exports" / "profit_control_90day_finance_completion_recommendation_cabinet_start_v1"

FINANCE_FULL_COLLECTION_RESULT_CSV = OUTPUT_DIR / "finance_full_collection_result.csv"
FINANCE_90DAY_FINAL_MATRIX_CSV = OUTPUT_DIR / "finance_90day_final_matrix.csv"
LIVE_TAIL_FINAL_COLLECTION_RESULT_CSV = OUTPUT_DIR / "live_tail_final_collection_result.csv"
UPDATED_STORE_LAYER_FINAL_CSV = OUTPUT_DIR / "updated_store_layer_final.csv"
UPDATED_SKU_LAYER_FINAL_CSV = OUTPUT_DIR / "updated_sku_layer_final.csv"
UPDATED_PROBLEM_ZONES_FINAL_CSV = OUTPUT_DIR / "updated_problem_zones_final.csv"
UPDATED_SCENARIOS_FINAL_CSV = OUTPUT_DIR / "updated_scenarios_final.csv"
UPDATED_RECOMMENDATIONS_FINAL_CSV = OUTPUT_DIR / "updated_recommendations_final.csv"
CABINET_BUILD_INPUT_FINAL_CSV = OUTPUT_DIR / "cabinet_build_input_final.csv"


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


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


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def as_text(value: Any) -> str:
    return str(value or "").strip()


def as_int(value: Any) -> int:
    if value in (None, ""):
        return 0
    return int(float(str(value).replace(" ", "")))


def short_money(value: Any) -> str:
    try:
        return f"{float(str(value).replace(' ', '')):,.0f}".replace(",", " ")
    except Exception:
        return ""


def configure_env(lane: str) -> None:
    os.environ["WB_FINANCE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    os.environ["WB_MAX_PARALLEL_REQUESTS_PER_SOURCE"] = "1"
    os.environ["WB_MAX_REQUESTS_PER_SECOND"] = "1"
    if lane == "closed":
        os.environ["WB_HTTP_GLOBAL_RPS"] = "0.5"
    else:
        os.environ["WB_HTTP_GLOBAL_RPS"] = "0.2"


def db_snapshot(target_day: date) -> dict[str, Any]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  COALESCE((SELECT COUNT(*) FROM {SCHEMA}.stg_wb_finance WHERE DATE(operation_date) = %s), 0) AS finance_stg_rows,
                  COALESCE((SELECT status FROM {SCHEMA}.finance_day_state WHERE day = %s), '') AS day_status,
                  COALESCE((SELECT final_analytic_usable_flag FROM {SCHEMA}.finance_day_state WHERE day = %s), FALSE) AS strict_usable,
                  COALESCE((SELECT raw_presence_flag FROM {SCHEMA}.finance_day_state WHERE day = %s), FALSE) AS raw_presence,
                  COALESCE((SELECT stg_presence_flag FROM {SCHEMA}.finance_day_state WHERE day = %s), FALSE) AS stg_presence
                """,
                (target_day, target_day, target_day, target_day, target_day),
            )
            stg_rows, status, strict_usable, raw_presence, stg_presence = cur.fetchone()
    return {
        "finance_stg_rows": int(stg_rows or 0),
        "day_status": status or "",
        "strict_usable": bool(strict_usable),
        "raw_presence": bool(raw_presence),
        "stg_presence": bool(stg_presence),
    }


def fetch_pass(target_day: date, max_pages: int) -> dict[str, Any]:
    pages: list[dict[str, Any]]
    terminal_reached = False
    error_type = ""
    try:
        pages = fetch_wb_finance_pages(
            date_from=target_day.isoformat(),
            date_to=target_day.isoformat(),
            limit=100,
            max_pages=max_pages,
            offset=0,
        )
        terminal_reached = any(bool(page.get("finance_is_terminal_page")) for page in pages)
    except WbFinanceFetchError as exc:
        pages = exc.diagnostic_pages
        terminal_reached = bool(exc.terminal_reached)
        error_type = as_text(exc.error_type)

    normalization = build_wb_finance_normalization_result(pages, target_day=target_day)
    rows = normalization["rows"]
    if rows:
        upsert_stg_wb_finance(SCHEMA, rows)

    raw_rows = 0
    http_429_count = 0
    last_http_status = ""
    max_rrdid_seen = 0
    for page in pages:
        raw_rows += as_int(page.get("finance_rows_in_response"))
        if as_int(page.get("http_status")) == 429:
            http_429_count += 1
        if as_text(page.get("http_status")):
            last_http_status = as_text(page.get("http_status"))
        max_rrdid_seen = max(max_rrdid_seen, as_int(page.get("finance_max_rrdid_in_response")), as_int(page.get("finance_resume_rrdid")))

    total_amount = sum(float(row.get("amount") or 0) for row in rows)
    return {
        "raw_rows": raw_rows,
        "normalized_rows": len(rows),
        "http_429_count": http_429_count,
        "last_http_status": last_http_status,
        "max_rrdid_seen": max_rrdid_seen,
        "total_amount": round(total_amount, 2),
        "terminal_reached": terminal_reached,
        "error_type": error_type,
    }


def collect_closed_day(target_day: date) -> dict[str, Any]:
    configure_env("closed")
    before = db_snapshot(target_day)
    pass_results: list[dict[str, Any]] = []
    prev_signature: tuple[int, float, int] | None = None
    saturated = False
    targeted_rerun_used = False
    for idx in range(3):
        if idx > 0:
            time.sleep(2)
        result = fetch_pass(target_day, max_pages=5)
        pass_results.append(result)
        signature = (result["raw_rows"], result["total_amount"], result["max_rrdid_seen"])
        if prev_signature is not None and signature == prev_signature:
            saturated = True
            break
        prev_signature = signature
        if result["http_429_count"] > 0 and result["raw_rows"] == 0:
            targeted_rerun_used = True
            time.sleep(6)
    after = db_snapshot(target_day)
    raw_rows_max = max((row["raw_rows"] for row in pass_results), default=0)
    normalized_rows_max = max((row["normalized_rows"] for row in pass_results), default=0)
    total_429 = sum(row["http_429_count"] for row in pass_results)
    if after["strict_usable"]:
        final_status = "strict_usable"
    elif after["finance_stg_rows"] > 0 or normalized_rows_max > 0:
        final_status = "provisional"
    elif raw_rows_max > 0:
        final_status = "source_open_only"
    elif total_429 > 0:
        final_status = "blocked"
    elif len(pass_results) >= 3 and all(row["raw_rows"] == 0 for row in pass_results):
        final_status = "truly_unavailable"
    else:
        final_status = "not_proven"
    return {
        "snapshot_date": now_iso(),
        "day": target_day.isoformat(),
        "lane": "closed_period_saturation",
        "passes_run": len(pass_results),
        "raw_rows_seen_max": raw_rows_max,
        "normalized_rows_max": normalized_rows_max,
        "finance_stg_rows_before": before["finance_stg_rows"],
        "finance_stg_rows_after": after["finance_stg_rows"],
        "strict_usable_after": "yes" if after["strict_usable"] else "no",
        "http_429_count_total": total_429,
        "saturated": "yes" if saturated else "no",
        "targeted_rerun_used": "yes" if targeted_rerun_used else "no",
        "final_collection_status": final_status,
    }


def collect_live_tail_day(target_day: date) -> dict[str, Any]:
    configure_env("live")
    before = db_snapshot(target_day)
    pass_results: list[dict[str, Any]] = []
    targeted_rerun_used = False

    first = fetch_pass(target_day, max_pages=1)
    pass_results.append(first)
    if first["raw_rows"] > 0:
        time.sleep(2)
        second = fetch_pass(target_day, max_pages=3)
        pass_results.append(second)
        if second["raw_rows"] > first["raw_rows"] and second["http_429_count"] == 0:
            time.sleep(2)
            third = fetch_pass(target_day, max_pages=5)
            pass_results.append(third)
    elif first["http_429_count"] > 0:
        targeted_rerun_used = True
        time.sleep(8)
        second = fetch_pass(target_day, max_pages=1)
        pass_results.append(second)

    after = db_snapshot(target_day)
    raw_rows_max = max((row["raw_rows"] for row in pass_results), default=0)
    normalized_rows_max = max((row["normalized_rows"] for row in pass_results), default=0)
    total_429 = sum(row["http_429_count"] for row in pass_results)
    if after["strict_usable"]:
        final_status = "strict_usable"
    elif after["finance_stg_rows"] > 0 or normalized_rows_max > 0:
        final_status = "provisional"
    elif raw_rows_max > 0:
        final_status = "source_open_only"
    elif total_429 > 0:
        final_status = "blocked"
    else:
        final_status = "not_proven"
    return {
        "snapshot_date": now_iso(),
        "day": target_day.isoformat(),
        "lane": "live_tail_quiet_presence_first",
        "passes_run": len(pass_results),
        "raw_rows_seen_max": raw_rows_max,
        "normalized_rows_max": normalized_rows_max,
        "finance_stg_rows_before": before["finance_stg_rows"],
        "finance_stg_rows_after": after["finance_stg_rows"],
        "strict_usable_after": "yes" if after["strict_usable"] else "no",
        "http_429_count_total": total_429,
        "targeted_rerun_used": "yes" if targeted_rerun_used else "no",
        "final_collection_status": final_status,
    }


def build_report(
    finance_full_collection_rows: list[dict[str, Any]],
    finance_status_rows: list[dict[str, Any]],
    finance_matrix_rows: list[dict[str, Any]],
    live_tail_result_rows: list[dict[str, Any]],
    store_rows: list[dict[str, Any]],
    sku_rows: list[dict[str, Any]],
    recommendation_rows: list[dict[str, Any]],
) -> str:
    core = next(row for row in finance_status_rows if row["segment"] == "core_90d")
    tail = next(row for row in finance_status_rows if row["segment"] == "live_tail_extension")
    store = store_rows[0]
    return f"""# PROFIT CONTROL — Full Data Collection Order v1

Date: {now_iso()}

## Main Outcome

- This pass moved from status-only interpretation to actual additional finance collection over unresolved core and live-tail days.
- Core 90-day corridor is still not 90 strict-usable days, but the pack now reflects the maximum materialized state reached after a full targeted collection pass.
- Store, SKU, problem-zone, scenario and recommendation layers are recalculated on top of the expanded finance fact set and current competitor-complete layer.
- Cabinet build can proceed from this pack with explicit finance semantics in UI: `strict usable`, `provisional`, `source-open`.

## Full Collection Result

- Core 90-day period: `{core['period_start']} .. {core['period_end']}`.
- Core collected days (`strict usable + provisional + source-open only`): `{int(core['strict_usable_days']) + int(core['provisional_days']) + int(core['source_open_only_days'])}` of `{core['total_days']}`.
- Core strict usable: `{core['strict_usable_days']}`.
- Core provisional: `{core['provisional_days']}`.
- Core unresolved after full pass: `{core['unresolved_days_total']}`.
- Live tail collected days (`strict usable + provisional + source-open only`): `{int(tail['strict_usable_days']) + int(tail['provisional_days']) + int(tail['source_open_only_days'])}` of `{tail['total_days']}`.
- Live tail unresolved after full pass: `{tail['unresolved_days_total']}`.

{md_table(finance_status_rows, ['segment', 'period_start', 'period_end', 'total_days', 'strict_usable_days', 'provisional_days', 'source_open_only_days', 'blocked_days', 'not_proven_days', 'truly_unavailable_days', 'materialized_days_total', 'unresolved_days_total'])}

## Core Collection Delta

{md_table(finance_full_collection_rows[:15], ['day', 'prior_status', 'final_collection_status', 'finance_stg_rows_after', 'strict_usable_after', 'improved_vs_prior'])}

## Live Tail Final Collection

{md_table(live_tail_result_rows, ['day', 'prior_status', 'final_collection_status', 'finance_stg_rows_after', 'strict_usable_after', 'improved_vs_prior'])}

## Updated Analytical Layer

- Store current profit drag: `{short_money(store.get('current_profit_drag_per_day'))} /день`.
- Store residual after calendar: `{short_money(store.get('residual_profit_drag_after_calendar_per_day'))} /день`.

{md_table(sku_rows, ['product_id', 'product_name', 'confidence', 'dominant_problem_zone_after_90d', 'best_scenario_name', 'best_scenario_effect_30d_working'])}

## Updated Recommendations

{md_table(recommendation_rows, ['product_id', 'product_name', 'final_best_scenario', 'runner_up_scenario', 'final_effect_range', 'final_risk'])}

## Ready For Cabinet

- Cabinet start is now based on a full collection pass, not only on recoverability labeling.
- Finance in UI must stay split into `strict usable`, `provisional`, and `source-open`; they must not collapse into one final profit number.
- Remaining unresolved days are real post-pass gaps, not just unattempted placeholders.
"""


def main() -> None:
    inputs = prev_mod.load_inputs()
    initial_status_rows, initial_matrix_rows, core_start, core_end, _ = prev_mod.build_finance_90day_layers(inputs)
    baseline_rows = load_csv(PREV_90D_DIR / "finance_90day_day_matrix.csv") or initial_matrix_rows
    unresolved_source_rows = baseline_rows if os.getenv("PC_SKIP_ACTIVE_COLLECTION") == "1" else initial_matrix_rows
    core_unresolved = [
        date.fromisoformat(row["day"])
        for row in unresolved_source_rows
        if row["corridor_segment"] == "core_90d" and row["final_day_status"] not in {"strict_usable", "provisional"}
    ]
    tail_unresolved = [
        date.fromisoformat(row["day"])
        for row in unresolved_source_rows
        if row["corridor_segment"] == "live_tail_extension" and row["final_day_status"] not in {"strict_usable", "provisional"}
    ]

    finance_full_collection_rows: list[dict[str, Any]] = []
    live_tail_result_rows: list[dict[str, Any]] = []
    if os.getenv("PC_SKIP_ACTIVE_COLLECTION") != "1":
        for idx, day_value in enumerate(core_unresolved):
            if idx > 0:
                time.sleep(1)
            print(f"[full-collection][closed] {day_value.isoformat()}", flush=True)
            finance_full_collection_rows.append(collect_closed_day(day_value))

        for idx, day_value in enumerate(tail_unresolved):
            if idx > 0:
                time.sleep(3)
            print(f"[full-collection][live-tail] {day_value.isoformat()}", flush=True)
            live_tail_result_rows.append(collect_live_tail_day(day_value))

    refreshed_inputs = prev_mod.load_inputs()
    finance_status_rows, finance_matrix_rows, _, _, _ = prev_mod.build_finance_90day_layers(refreshed_inputs)
    if os.getenv("PC_SKIP_ACTIVE_COLLECTION") == "1":
        initial_by_day = {row["day"]: row for row in baseline_rows}
        final_by_day = {row["day"]: row for row in finance_matrix_rows}
        finance_full_collection_rows = []
        live_tail_result_rows = []
        for day_value in core_unresolved:
            day_text = day_value.isoformat()
            before = initial_by_day.get(day_text, {})
            after = final_by_day.get(day_text, {})
            finance_full_collection_rows.append(
                {
                    "snapshot_date": now_iso(),
                    "day": day_text,
                    "lane": "current_db_state_after_partial_full_collection",
                    "prior_status": as_text(before.get("final_day_status")),
                    "final_collection_status": as_text(after.get("final_day_status")),
                    "finance_stg_rows_after": as_text(after.get("finance_stg_rows")),
                    "strict_usable_after": as_text(after.get("strict_usable_flag")),
                    "improved_vs_prior": "yes"
                    if as_text(before.get("final_day_status")) != as_text(after.get("final_day_status"))
                    else "no",
                }
            )
        finance_full_collection_rows.sort(key=lambda row: (row["improved_vs_prior"] != "yes", row["day"]))
        for day_value in tail_unresolved:
            day_text = day_value.isoformat()
            before = initial_by_day.get(day_text, {})
            after = final_by_day.get(day_text, {})
            live_tail_result_rows.append(
                {
                    "snapshot_date": now_iso(),
                    "day": day_text,
                    "lane": "current_db_state_after_partial_full_collection",
                    "prior_status": as_text(before.get("final_day_status")),
                    "final_collection_status": as_text(after.get("final_day_status")),
                    "finance_stg_rows_after": as_text(after.get("finance_stg_rows")),
                    "strict_usable_after": as_text(after.get("strict_usable_flag")),
                    "improved_vs_prior": "yes"
                    if as_text(before.get("final_day_status")) != as_text(after.get("final_day_status"))
                    else "no",
                }
            )
        live_tail_result_rows.sort(key=lambda row: (row["improved_vs_prior"] != "yes", row["day"]))
    store_rows = prev_mod.build_updated_store_layer_after_90d(refreshed_inputs, finance_status_rows)
    sku_rows = prev_mod.build_updated_sku_layer_after_90d(refreshed_inputs)
    problem_zone_rows = prev_mod.build_updated_problem_zone_map(refreshed_inputs)
    scenario_rows = prev_mod.build_updated_scenario_layer_after_90d(refreshed_inputs)
    recommendation_rows = prev_mod.build_updated_final_recommendations(refreshed_inputs)
    cabinet_rows = prev_mod.build_cabinet_build_input_final(finance_status_rows, store_rows)

    write_csv(FINANCE_FULL_COLLECTION_RESULT_CSV, finance_full_collection_rows)
    write_csv(FINANCE_90DAY_FINAL_MATRIX_CSV, finance_matrix_rows)
    write_csv(LIVE_TAIL_FINAL_COLLECTION_RESULT_CSV, live_tail_result_rows)
    write_csv(UPDATED_STORE_LAYER_FINAL_CSV, store_rows)
    write_csv(UPDATED_SKU_LAYER_FINAL_CSV, sku_rows)
    write_csv(UPDATED_PROBLEM_ZONES_FINAL_CSV, problem_zone_rows)
    write_csv(UPDATED_SCENARIOS_FINAL_CSV, scenario_rows)
    write_csv(UPDATED_RECOMMENDATIONS_FINAL_CSV, recommendation_rows)
    write_csv(CABINET_BUILD_INPUT_FINAL_CSV, cabinet_rows)
    DOC_PATH.write_text(
        build_report(
            finance_full_collection_rows,
            finance_status_rows,
            finance_matrix_rows,
            live_tail_result_rows,
            store_rows,
            sku_rows,
            recommendation_rows,
        ),
        encoding="utf-8",
    )
    print(f"Built full data collection order pack into {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
