from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.wb_finance_january_full_pass_with_saturation_closing_v1 as saturation
from scripts.build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1 import (
    abs_decimal_diff,
    control_pass_duplicate_check,
    fetch_day_evidence,
    tail_extension_closeout,
    upsert_blocked_provisional,
    upsert_strict_success,
)
from src.services.wb_finance_canonical_method_v1 import CLOSED_PERIOD, apply_canonical_env

MOSCOW = ZoneInfo("Europe/Moscow")
OUTPUT_DIR = ROOT / "exports" / "wb_finance_canonical_closed_period_runner_v1"
RESULT_CSV = OUTPUT_DIR / "closed_period_day_results.csv"
PASS_CSV = OUTPUT_DIR / "closed_period_pass_metrics.csv"
DOC_PATH = ROOT / "artifacts" / "wb_finance_canonical_closed_period_runner_v1.md"


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run canonical WB finance closed-period collection.")
    parser.add_argument("--days", nargs="+", required=True, help="Day list in YYYY-MM-DD format.")
    return parser.parse_args()


def configure_from_canonical() -> None:
    apply_canonical_env("closed_period")
    saturation.LIMIT = int(CLOSED_PERIOD.env["WB_FINANCE_PAGE_LIMIT"])
    saturation.INTER_REQUEST_PAUSE_SEC = int(CLOSED_PERIOD.env["WB_FINANCE_INTER_PAGE_PAUSE_SEC"])
    saturation.INTER_PASS_PAUSE_SEC = int(CLOSED_PERIOD.env["WB_FINANCE_PASS_PAUSE_SEC"])
    saturation.POST_429_COOLING_SEC = int(CLOSED_PERIOD.env["WB_FINANCE_RATE_LIMIT_PASS_PAUSE_SEC"])
    saturation.MAX_PASSES = int(CLOSED_PERIOD.env["WB_FINANCE_STABLE_MAX_PASSES"])


def run_one_day(day_value: date) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    day_started_at = now_iso()
    day_started_ts = datetime.now(MOSCOW).timestamp()
    day_text = day_value.isoformat()
    evidence_before = fetch_day_evidence(day_text)
    pass_rows: list[dict[str, Any]] = []
    notes: list[str] = []

    tail = tail_extension_closeout(day_text)
    notes.append(f"tail={tail['close_reason']}")
    pass_rows.extend(
        {
            "day": day_text,
            "phase": "tail_continuation",
            "round_no": row["round_no"],
            "offset_before": row["offset_before"],
            "raw_rows_seen": row["raw_rows_seen"],
            "new_unique_rows": row["new_unique_rows"],
            "stg_growth": row["stg_growth"],
            "amount_growth": row["amount_growth"],
            "terminal_reached": row["terminal_reached"],
            "had_rate_limit": row["had_rate_limit"],
            "error_type": row["error_type"],
            "last_http_status": row["last_http_status"],
        }
        for row in tail["rounds"]
    )
    evidence_after_tail = fetch_day_evidence(day_text)
    if tail["strict_close"]:
        upsert_strict_success(
            day_text,
            evidence_after_tail.finance_stg_rows,
            f"raw_vs_stg_diff={abs_decimal_diff(evidence_after_tail.recon_raw_amount_sum, evidence_after_tail.finance_stg_amount):.2f}",
            tail["close_reason"],
        )
        day_finished_at = now_iso()
        day_duration_sec = round(datetime.now(MOSCOW).timestamp() - day_started_ts, 2)
        return (
            {
                "day": day_text,
                "final_rows": evidence_after_tail.finance_stg_rows,
                "final_amount": f"{evidence_after_tail.finance_stg_amount:.2f}",
                "final_status": "tail_extension_terminal_confirmation",
                "total_passes": len(tail["rounds"]),
                "total_requests": sum(int(row["pages_seen"] or 0) for row in tail["rounds"]),
                "count_429": sum(1 for row in tail["rounds"] if row["had_rate_limit"]),
                "day_started_at": day_started_at,
                "day_finished_at": day_finished_at,
                "day_duration_sec": day_duration_sec,
                "notes": "; ".join(notes),
            },
            pass_rows,
        )

    saturation_result, saturation_passes = saturation.run_day(day_value)
    pass_rows.extend(
        {
            "day": item.day,
            "phase": "replay_saturation",
            "round_no": item.pass_number,
            "offset_before": 0,
            "raw_rows_seen": item.rows_total,
            "new_unique_rows": item.new_unique_rrd_id_count,
            "stg_growth": item.new_unique_rrd_id_count,
            "amount_growth": item.amount_delta_this_pass,
            "terminal_reached": item.stop_reason in {"http_no_content", "short_terminal_page", "empty_response"},
            "had_rate_limit": item.had_429,
            "error_type": item.stop_reason,
            "last_http_status": 429 if item.had_429 else 200,
        }
        for item in saturation_passes
    )
    notes.append(f"replay={saturation_result.final_status}")

    evidence_after_replay = fetch_day_evidence(day_text)
    if evidence_after_replay.state_strict_usable:
        day_finished_at = now_iso()
        day_duration_sec = round(datetime.now(MOSCOW).timestamp() - day_started_ts, 2)
        return (
            {
                "day": day_text,
                "final_rows": evidence_after_replay.finance_stg_rows,
                "final_amount": f"{evidence_after_replay.finance_stg_amount:.2f}",
                "final_status": saturation_result.final_status,
                "total_passes": len(tail["rounds"]) + len(saturation_passes),
                "total_requests": sum(int(row["pages_seen"] or 0) for row in tail["rounds"]) + sum(int(item.requests_in_pass or 0) for item in saturation_passes),
                "count_429": sum(1 for row in tail["rounds"] if row["had_rate_limit"]) + sum(1 for item in saturation_passes if item.had_429),
                "day_started_at": day_started_at,
                "day_finished_at": day_finished_at,
                "day_duration_sec": day_duration_sec,
                "notes": "; ".join(notes),
            },
            pass_rows,
        )

    control = control_pass_duplicate_check(day_text)
    pass_rows.extend(
        {
            "day": day_text,
            "phase": "duplicate_control",
            "round_no": row["attempt_no"],
            "offset_before": 0,
            "raw_rows_seen": row["raw_rows"],
            "new_unique_rows": row["normalized_rows"],
            "stg_growth": int(row["after_stg_rows"]) - int(row["before_stg_rows"]),
            "amount_growth": f"{Decimal(str(row['after_stg_amount'])) - Decimal(str(row['before_stg_amount'])):.2f}",
            "terminal_reached": bool(row["no_growth"]),
            "had_rate_limit": int(row["http_429_count"] or 0) > 0,
            "error_type": row["error_type"],
            "last_http_status": 429 if int(row["http_429_count"] or 0) > 0 else 200,
        }
        for row in control["attempts"]
    )
    if control["no_growth"]:
        evidence_after_control = fetch_day_evidence(day_text)
        upsert_strict_success(
            day_text,
            evidence_after_control.finance_stg_rows,
            f"raw_vs_stg_diff={abs_decimal_diff(evidence_after_control.recon_raw_amount_sum, evidence_after_control.finance_stg_amount):.2f}",
            "duplicate_only_replay_no_growth_after_materialization",
        )
        notes.append("control=duplicate_only_no_growth")
        day_finished_at = now_iso()
        day_duration_sec = round(datetime.now(MOSCOW).timestamp() - day_started_ts, 2)
        return (
            {
                "day": day_text,
                "final_rows": evidence_after_control.finance_stg_rows,
                "final_amount": f"{evidence_after_control.finance_stg_amount:.2f}",
                "final_status": "duplicate_only_replay_no_growth_after_materialization",
                "total_passes": len(tail["rounds"]) + len(saturation_passes) + len(control["attempts"]),
                "total_requests": sum(int(row["pages_seen"] or 0) for row in tail["rounds"]) + sum(int(item.requests_in_pass or 0) for item in saturation_passes) + len(control["attempts"]),
                "count_429": sum(1 for row in tail["rounds"] if row["had_rate_limit"]) + sum(1 for item in saturation_passes if item.had_429) + sum(1 for row in control["attempts"] if int(row["http_429_count"] or 0) > 0),
                "day_started_at": day_started_at,
                "day_finished_at": day_finished_at,
                "day_duration_sec": day_duration_sec,
                "notes": "; ".join(notes),
            },
            pass_rows,
        )

    evidence_final = fetch_day_evidence(day_text)
    upsert_blocked_provisional(day_text, evidence_final.finance_stg_rows, "rate_limit_before_duplicate_only_proof")
    notes.append("control=still_not_closed")
    day_finished_at = now_iso()
    day_duration_sec = round(datetime.now(MOSCOW).timestamp() - day_started_ts, 2)
    return (
        {
            "day": day_text,
            "final_rows": evidence_final.finance_stg_rows,
            "final_amount": f"{evidence_final.finance_stg_amount:.2f}",
            "final_status": "still_provisional_after_canonical_pipeline",
            "total_passes": len(tail["rounds"]) + len(saturation_passes) + len(control["attempts"]),
            "total_requests": sum(int(row["pages_seen"] or 0) for row in tail["rounds"]) + sum(int(item.requests_in_pass or 0) for item in saturation_passes) + len(control["attempts"]),
            "count_429": sum(1 for row in tail["rounds"] if row["had_rate_limit"]) + sum(1 for item in saturation_passes if item.had_429) + sum(1 for row in control["attempts"] if int(row["http_429_count"] or 0) > 0),
            "day_started_at": day_started_at,
            "day_finished_at": day_finished_at,
            "day_duration_sec": day_duration_sec,
            "notes": "; ".join(notes),
        },
        pass_rows,
    )


def run_days(day_values: list[date]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    configure_from_canonical()
    result_rows: list[dict[str, Any]] = []
    pass_rows: list[dict[str, Any]] = []
    for day_value in day_values:
        result, passes = run_one_day(day_value)
        result_rows.append(result)
        pass_rows.extend(passes)
    return result_rows, pass_rows


def build_report(day_rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "# WB Finance Canonical Closed-Period Runner v1",
            "",
            f"Date: {now_iso()}",
            "",
            "## Canonical Mode",
            "",
            f"- mode: `{CLOSED_PERIOD.name}`",
            f"- lane: `{CLOSED_PERIOD.lane}`",
            f"- source_of_truth: `{', '.join(CLOSED_PERIOD.source_of_truth)}`",
            f"- phase_order: `{' -> '.join(CLOSED_PERIOD.phase_order)}`",
            "",
            "## Day Results",
            "",
            md_table(
                day_rows,
                [
                    "day",
                    "final_rows",
                    "final_amount",
                    "final_status",
                    "total_passes",
                    "total_requests",
                    "count_429",
                    "notes",
                ],
            ),
            "",
        ]
    ) + "\n"


def main() -> None:
    args = parse_args()
    day_values = [date.fromisoformat(item) for item in args.days]
    day_rows, pass_rows = run_days(day_values)
    write_csv(RESULT_CSV, day_rows)
    write_csv(PASS_CSV, pass_rows)
    DOC_PATH.write_text(build_report(day_rows), encoding="utf-8")
    print(f"Wrote {RESULT_CSV}")
    print(f"Wrote {PASS_CSV}")
    print(f"Wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
