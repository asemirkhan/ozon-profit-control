from __future__ import annotations

import csv
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.finance import WbFinanceFetchError, _run_finance_pass, fetch_wb_finance_pages
from src.loaders.upsert_wb import upsert_stg_wb_finance
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.services.wb_finance_canonical_method_v1 import apply_canonical_env
from src.utils.db import get_conn
from src.utils.rate_limit_coordinator import get_rate_limit_coordinator

MOSCOW = ZoneInfo("Europe/Moscow")
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
FINANCE_PAGE_LIMIT = int(os.getenv("WB_FINANCE_PAGE_LIMIT", "1000"))

SOURCE_MATRIX = ROOT / "exports" / "profit_control_full_data_collection_order_v1" / "finance_90day_final_matrix.csv"
OUTPUT_DIR = ROOT / "exports" / "profit_control_force_convert_all_provisional_days_to_strict_usable_v1"
RESULT_CSV = OUTPUT_DIR / "provisional_to_strict_conversion_result.csv"
SUMMARY_CSV = OUTPUT_DIR / "provisional_to_strict_conversion_summary.csv"
DOC_PATH = ROOT / "artifacts" / "profit_control_force_convert_all_provisional_days_to_strict_usable_v1.md"


def connect():
    load_dotenv(ROOT / ".env")
    return get_conn()


@dataclass
class DayEvidence:
    day: str
    finance_stg_rows: int
    finance_stg_amount: Decimal
    state_status: str
    state_strict_usable: bool
    state_reason_code: str
    state_terminal_reason_code: str
    recon_raw_payload_files: int
    recon_raw_rows_total: int
    recon_normalized_rows_total: int
    recon_stg_rows_total: int
    recon_raw_amount_sum: Decimal
    recon_stg_amount_sum: Decimal
    recon_pagination_complete: bool


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


def load_provisional_core_days() -> list[str]:
    with SOURCE_MATRIX.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [
        row["day"]
        for row in rows
        if row["corridor_segment"] == "core_90d" and row["final_day_status"] == "provisional"
    ]


def current_state_rows(days: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day_text in days:
        evidence = fetch_day_evidence(day_text)
        rows.append(
            {
                "snapshot_date": now_iso(),
                "day": day_text,
                "prior_status": "provisional",
                "final_status_after_force_conversion": "strict_usable" if evidence.state_strict_usable else "provisional",
                "strict_usable_after": "yes" if evidence.state_strict_usable else "no",
                "blocking_reason_if_no": "" if evidence.state_strict_usable else (evidence.state_reason_code or evidence.state_terminal_reason_code),
                "finance_stg_rows_after": evidence.finance_stg_rows,
                "finance_stg_amount_after": f"{evidence.finance_stg_amount:.2f}",
                "reconciliation_complete_flag": "yes" if evidence.recon_pagination_complete else "no",
                "recon_raw_rows_total": evidence.recon_raw_rows_total,
                "recon_normalized_rows_total": evidence.recon_normalized_rows_total,
                "recon_stg_rows_total": evidence.recon_stg_rows_total,
                "state_status_before": evidence.state_status,
                "state_reason_before": evidence.state_reason_code or evidence.state_terminal_reason_code,
                "control_pass_raw_rows": "",
                "control_pass_http_429_count": "",
                "control_pass_details": "",
                "improved_vs_prior": "n/a",
            }
        )
    return rows


def configure_env() -> None:
    load_dotenv(ROOT / ".env")
    apply_canonical_env("closed_period")


def abs_decimal_diff(left: Decimal, right: Decimal) -> Decimal:
    return abs((left or Decimal("0")) - (right or Decimal("0")))


def fetch_day_evidence(day_text: str) -> DayEvidence:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                  COALESCE(COUNT(*), 0)::int,
                  COALESCE(SUM(amount), 0)::numeric(18,2)
                FROM {SCHEMA}.stg_wb_finance
                WHERE DATE(operation_date) = %s
                """,
                (day_text,),
            )
            stg_rows, stg_amount = cur.fetchone()

            cur.execute(
                f"""
                SELECT
                  COALESCE(status, ''),
                  COALESCE(final_analytic_usable_flag, FALSE),
                  COALESCE(reason_code, ''),
                  COALESCE(terminal_reason_code, '')
                FROM {SCHEMA}.finance_day_state
                WHERE day = %s
                """,
                (day_text,),
            )
            state_row = cur.fetchone()

            cur.execute(
                f"""
                SELECT
                  COALESCE(raw_payload_files, 0)::int,
                  COALESCE(raw_rows_total, 0)::int,
                  COALESCE(normalized_rows_total, 0)::int,
                  COALESCE(stg_rows_total, 0)::int,
                  COALESCE(raw_amount_sum, 0)::numeric(18,2),
                  COALESCE(stg_amount_sum, 0)::numeric(18,2),
                  COALESCE(pagination_complete, FALSE)
                FROM {SCHEMA}.finance_reconciliation_day
                WHERE target_date = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (day_text,),
            )
            recon_row = cur.fetchone()

    state_status = ""
    state_strict_usable = False
    state_reason_code = ""
    state_terminal_reason_code = ""
    if state_row:
        state_status, state_strict_usable, state_reason_code, state_terminal_reason_code = state_row

    recon_defaults = (0, 0, 0, 0, Decimal("0.00"), Decimal("0.00"), False)
    if recon_row is None:
        recon_row = recon_defaults

    return DayEvidence(
        day=day_text,
        finance_stg_rows=int(stg_rows or 0),
        finance_stg_amount=Decimal(str(stg_amount or 0)),
        state_status=state_status or "",
        state_strict_usable=bool(state_strict_usable),
        state_reason_code=state_reason_code or "",
        state_terminal_reason_code=state_terminal_reason_code or "",
        recon_raw_payload_files=int(recon_row[0] or 0),
        recon_raw_rows_total=int(recon_row[1] or 0),
        recon_normalized_rows_total=int(recon_row[2] or 0),
        recon_stg_rows_total=int(recon_row[3] or 0),
        recon_raw_amount_sum=Decimal(str(recon_row[4] or 0)),
        recon_stg_amount_sum=Decimal(str(recon_row[5] or 0)),
        recon_pagination_complete=bool(recon_row[6]),
    )


def stg_rrd_snapshot(day_text: str) -> tuple[set[int], int]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COALESCE(MAX(rrd_id), 0)::bigint
                FROM {SCHEMA}.stg_wb_finance
                WHERE DATE(operation_date) = %s
                """,
                (day_text,),
            )
            max_rrd = int(cur.fetchone()[0] or 0)
            cur.execute(
                f"""
                SELECT rrd_id
                FROM {SCHEMA}.stg_wb_finance
                WHERE DATE(operation_date) = %s
                  AND rrd_id IS NOT NULL
                """,
                (day_text,),
            )
            known = {int(row[0]) for row in cur.fetchall()}
    return known, max_rrd


def control_pass_duplicate_check(day_text: str) -> dict[str, Any]:
    configure_env()
    attempts: list[dict[str, Any]] = []
    for attempt_no in (1, 2):
        if attempt_no > 1:
            time.sleep(10)
        before = fetch_day_evidence(day_text)
        get_rate_limit_coordinator()._states.clear()  # type: ignore[attr-defined]
        try:
            pages = fetch_wb_finance_pages(day_text, day_text, limit=FINANCE_PAGE_LIMIT, max_pages=6, offset=0)
            error_type = ""
        except WbFinanceFetchError as exc:
            pages = exc.diagnostic_pages
            error_type = exc.error_type or "fetch_error"
        normalization = build_wb_finance_normalization_result(pages, target_day=datetime.fromisoformat(f"{day_text}T00:00:00").date())
        rows = normalization["rows"]
        if rows:
            upsert_stg_wb_finance(SCHEMA, rows)
        after = fetch_day_evidence(day_text)
        raw_rows = sum(int(page.get("finance_rows_in_response") or 0) for page in pages)
        http_429_count = sum(1 for page in pages if int(page.get("http_status") or 0) == 429)
        row = {
            "attempt_no": attempt_no,
            "raw_rows": raw_rows,
            "normalized_rows": len(rows),
            "http_429_count": http_429_count,
            "error_type": error_type,
            "before_stg_rows": before.finance_stg_rows,
            "after_stg_rows": after.finance_stg_rows,
            "before_stg_amount": before.finance_stg_amount,
            "after_stg_amount": after.finance_stg_amount,
            "no_growth": after.finance_stg_rows == before.finance_stg_rows
            and abs_decimal_diff(after.finance_stg_amount, before.finance_stg_amount) <= Decimal("0.05"),
        }
        attempts.append(row)
        if row["raw_rows"] > 0:
            break
        if row["http_429_count"] <= 0:
            break

    best = attempts[-1]
    return {
        "attempts": attempts,
        "raw_rows": max((row["raw_rows"] for row in attempts), default=0),
        "normalized_rows": max((row["normalized_rows"] for row in attempts), default=0),
        "http_429_count": sum(row["http_429_count"] for row in attempts),
        "error_type": " | ".join(row["error_type"] for row in attempts if row["error_type"]),
        "no_growth": all(row["no_growth"] for row in attempts if row["raw_rows"] > 0) and any(row["raw_rows"] > 0 for row in attempts),
        "attempt_details": " | ".join(
            f"a{row['attempt_no']}:raw={row['raw_rows']},norm={row['normalized_rows']},429={row['http_429_count']},no_growth={str(row['no_growth']).lower()},err={row['error_type'] or '-'}"
            for row in attempts
        ),
    }


def tail_extension_closeout(day_text: str) -> dict[str, Any]:
    configure_env()
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"

    rounds: list[dict[str, Any]] = []
    strict_close = False
    close_reason = ""

    max_rounds = int(os.getenv("PC_FORCE_TAIL_MAX_ROUNDS", "4"))
    inter_page_pause = int(os.getenv("PC_FORCE_TAIL_INTER_PAGE_PAUSE_SEC", "15"))
    rate_limit_cooldown = int(os.getenv("PC_FORCE_TAIL_RATE_LIMIT_COOLDOWN_SEC", "90"))
    between_round_pause = int(os.getenv("PC_FORCE_TAIL_BETWEEN_ROUNDS_SEC", "12"))

    for round_no in range(1, max_rounds + 1):
        before = fetch_day_evidence(day_text)
        known_rrd_ids, offset = stg_rrd_snapshot(day_text)
        try:
            result = _run_finance_pass(
                date_from=day_text,
                date_to=day_text,
                limit=FINANCE_PAGE_LIMIT,
                max_pages=20,
                offset=offset,
                request_no_start=0,
                pass_no=round_no,
                known_rrd_ids=known_rrd_ids,
                pages_done_before=0,
                rows_done_before=len(known_rrd_ids),
                inter_page_pause_sec=inter_page_pause,
            )
            pages = result.pages
            error_type = ""
            terminal_reached = result.terminal_reached
            had_rate_limit = result.had_rate_limit
            new_unique_rows = int(result.pass_new_unique_rows or 0)
        except WbFinanceFetchError as exc:
            pages = exc.diagnostic_pages
            error_type = exc.error_type or "fetch_error"
            terminal_reached = exc.terminal_reached
            had_rate_limit = error_type == "rate_limit" or any(int(page.get("http_status") or 0) == 429 for page in pages)
            # Seed known ids before replay, then count only ids we do not already have.
            known_for_count, _ = stg_rrd_snapshot(day_text)
            new_unique_rows = 0
            for page in pages:
                response = page.get("response") or {}
                rows = response.get("finance") if isinstance(response, dict) else []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    rrd_id = row.get("rrd_id")
                    try:
                        parsed = int(rrd_id)
                    except (TypeError, ValueError):
                        continue
                    if parsed in known_for_count:
                        continue
                    known_for_count.add(parsed)
                    new_unique_rows += 1

        normalization = build_wb_finance_normalization_result(
            pages,
            target_day=datetime.fromisoformat(f"{day_text}T00:00:00").date(),
        )
        rows = normalization["rows"]
        if rows:
            upsert_stg_wb_finance(SCHEMA, rows)
        after = fetch_day_evidence(day_text)
        stg_growth = after.finance_stg_rows - before.finance_stg_rows
        amount_growth = after.finance_stg_amount - before.finance_stg_amount

        round_row = {
            "round_no": round_no,
            "offset_before": offset,
            "pages_seen": len(pages),
            "raw_rows_seen": sum(int(page.get("finance_rows_in_response") or 0) for page in pages),
            "new_unique_rows": new_unique_rows,
            "stg_growth": stg_growth,
            "amount_growth": f"{amount_growth:.2f}",
            "terminal_reached": terminal_reached,
            "had_rate_limit": had_rate_limit,
            "error_type": error_type,
            "last_http_status": next((int(page.get("http_status") or 0) for page in reversed(pages) if page.get("http_status") is not None), 0),
        }
        rounds.append(round_row)

        duplicate_only_tail = round_row["raw_rows_seen"] > 0 and new_unique_rows == 0 and stg_growth == 0
        if terminal_reached and (new_unique_rows > 0 or duplicate_only_tail or round_row["raw_rows_seen"] == 0):
            strict_close = True
            close_reason = "tail_extension_terminal_confirmation"
            break
        if duplicate_only_tail and not had_rate_limit:
            strict_close = True
            close_reason = "tail_extension_duplicate_only_no_growth"
            break

        if had_rate_limit:
            time.sleep(rate_limit_cooldown)
        else:
            if stg_growth <= 0 and new_unique_rows <= 0:
                close_reason = "no_additional_tail_growth_without_terminal_confirmation"
                break
            time.sleep(between_round_pause)

    return {
        "rounds": rounds,
        "strict_close": strict_close,
        "close_reason": close_reason or ("tail_extension_exhausted_without_terminal" if rounds else "tail_extension_not_started"),
        "total_new_rows": sum(int(row["stg_growth"] or 0) for row in rounds),
        "had_any_rate_limit": any(bool(row["had_rate_limit"]) for row in rounds),
        "details": " | ".join(
            f"r{row['round_no']}:off={row['offset_before']},raw={row['raw_rows_seen']},new={row['new_unique_rows']},stg+={row['stg_growth']},term={str(bool(row['terminal_reached'])).lower()},429={str(bool(row['had_rate_limit'])).lower()},err={row['error_type'] or '-'}"
            for row in rounds
        ),
    }


def upsert_strict_success(day_text: str, rows_done: int, amount_note: str, terminal_reason: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.finance_day_state (
                  day, status, raw_presence_flag, stg_presence_flag, finance_day_complete_flag,
                  final_analytic_usable_flag, last_resume_rrdid, previous_resume_rrdid, pages_done_total,
                  rows_done_total, attempts_total, no_progress_attempts, repeated_page_attempts,
                  http_429_count_total, cumulative_wait_sec, last_progress_at, last_success_at,
                  last_http_code, last_rows_delta, last_page_checksum, suspected_trap_type,
                  suspected_trap_reason, reason_code, source_pressure_flag,
                  reconciliation_exception_explained, reconciliation_exception_reason, updated_at,
                  terminal_reason_code, terminal_decision_at, terminal_decision_basis,
                  terminal_confidence_level, recheck_required_flag, alternative_strategy_exhausted_flag
                ) VALUES (
                  %s, 'success', TRUE, TRUE, TRUE,
                  TRUE, 0, 0, 0,
                  %s, 1, 0, 0,
                  0, 0, now(), now(),
                  200, 0, NULL, NULL,
                  NULL, 'force_convert_provisional_to_strict_v1', FALSE,
                  TRUE, %s, now(),
                  %s, now(), 'force_convert_provisional_to_strict_v1',
                  'high', FALSE, TRUE
                )
                ON CONFLICT (day) DO UPDATE SET
                  status = EXCLUDED.status,
                  raw_presence_flag = EXCLUDED.raw_presence_flag,
                  stg_presence_flag = EXCLUDED.stg_presence_flag,
                  finance_day_complete_flag = EXCLUDED.finance_day_complete_flag,
                  final_analytic_usable_flag = EXCLUDED.final_analytic_usable_flag,
                  rows_done_total = GREATEST({SCHEMA}.finance_day_state.rows_done_total, EXCLUDED.rows_done_total),
                  attempts_total = GREATEST({SCHEMA}.finance_day_state.attempts_total, EXCLUDED.attempts_total),
                  no_progress_attempts = 0,
                  repeated_page_attempts = 0,
                  source_pressure_flag = FALSE,
                  reason_code = EXCLUDED.reason_code,
                  terminal_reason_code = EXCLUDED.terminal_reason_code,
                  terminal_decision_at = EXCLUDED.terminal_decision_at,
                  terminal_decision_basis = EXCLUDED.terminal_decision_basis,
                  terminal_confidence_level = EXCLUDED.terminal_confidence_level,
                  recheck_required_flag = FALSE,
                  alternative_strategy_exhausted_flag = TRUE,
                  last_success_at = now(),
                  updated_at = now()
                """,
                (day_text, rows_done, amount_note, terminal_reason),
            )
        conn.commit()


def upsert_blocked_provisional(day_text: str, rows_done: int, blocking_reason: str) -> None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {SCHEMA}.finance_day_state (
                  day, status, raw_presence_flag, stg_presence_flag, finance_day_complete_flag,
                  final_analytic_usable_flag, last_resume_rrdid, previous_resume_rrdid, pages_done_total,
                  rows_done_total, attempts_total, no_progress_attempts, repeated_page_attempts,
                  http_429_count_total, cumulative_wait_sec, last_progress_at, last_success_at,
                  last_http_code, last_rows_delta, last_page_checksum, suspected_trap_type,
                  suspected_trap_reason, reason_code, source_pressure_flag,
                  reconciliation_exception_explained, reconciliation_exception_reason, updated_at,
                  terminal_reason_code, terminal_decision_at, terminal_decision_basis,
                  terminal_confidence_level, recheck_required_flag, alternative_strategy_exhausted_flag
                ) VALUES (
                  %s, 'started_but_not_finished', TRUE, TRUE, FALSE,
                  FALSE, 0, 0, 0,
                  %s, 1, 1, 0,
                  0, 0, now(), NULL,
                  NULL, 0, NULL, 'strict_conversion_blocked',
                  %s, %s, FALSE,
                  TRUE, %s, now(),
                  %s, now(), 'force_convert_provisional_to_strict_v1_blocked',
                  'high', TRUE, TRUE
                )
                ON CONFLICT (day) DO UPDATE SET
                  status = EXCLUDED.status,
                  raw_presence_flag = EXCLUDED.raw_presence_flag,
                  stg_presence_flag = EXCLUDED.stg_presence_flag,
                  finance_day_complete_flag = EXCLUDED.finance_day_complete_flag,
                  final_analytic_usable_flag = EXCLUDED.final_analytic_usable_flag,
                  rows_done_total = GREATEST({SCHEMA}.finance_day_state.rows_done_total, EXCLUDED.rows_done_total),
                  no_progress_attempts = GREATEST({SCHEMA}.finance_day_state.no_progress_attempts, EXCLUDED.no_progress_attempts),
                  suspected_trap_type = EXCLUDED.suspected_trap_type,
                  suspected_trap_reason = EXCLUDED.suspected_trap_reason,
                  reason_code = EXCLUDED.reason_code,
                  terminal_reason_code = EXCLUDED.terminal_reason_code,
                  terminal_decision_at = EXCLUDED.terminal_decision_at,
                  terminal_decision_basis = EXCLUDED.terminal_decision_basis,
                  terminal_confidence_level = EXCLUDED.terminal_confidence_level,
                  recheck_required_flag = TRUE,
                  alternative_strategy_exhausted_flag = TRUE,
                  updated_at = now()
                """,
                (day_text, rows_done, blocking_reason, blocking_reason, blocking_reason, blocking_reason),
            )
        conn.commit()


def decide_conversion(evidence: DayEvidence) -> tuple[str, str]:
    if evidence.recon_pagination_complete:
        rows_match = (
            evidence.recon_raw_rows_total == evidence.recon_normalized_rows_total == evidence.recon_stg_rows_total == evidence.finance_stg_rows
        )
        amount_close = abs_decimal_diff(evidence.recon_raw_amount_sum, evidence.finance_stg_amount) <= Decimal("0.05")
        if rows_match and amount_close:
            return "strict_usable", "reconciliation_complete_exact_match"
        if not rows_match:
            return "provisional", "reconciliation_exists_but_row_counts_do_not_close_exactly"
        return "provisional", "reconciliation_exists_but_amounts_not_stable_enough"

    if evidence.day < "2026-02-11":
        return "provisional", "no_reconciliation_archive_for_closed_period_materialized_day"

    if evidence.state_status == "manual_hold":
        return "provisional", "manual_hold_without_complete_reconciliation_proof"

    return "provisional", "missing_close_condition_even_after_materialization"


def rebuild_full_outputs() -> None:
    env = os.environ.copy()
    env["PC_SKIP_ACTIVE_COLLECTION"] = "1"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_profit_control_full_data_collection_order_v1.py")],
        cwd=ROOT,
        env=env,
        check=True,
    )


def build_report(rows: list[dict[str, Any]]) -> str:
    converted = [row for row in rows if row["strict_usable_after"] == "yes"]
    remaining = [row for row in rows if row["strict_usable_after"] != "yes"]
    remaining_days = ", ".join(row["day"] for row in remaining) or "none"
    summary = {
        "prior_provisional_days": len(rows),
        "converted_to_strict_usable": len(converted),
        "remaining_provisional_days_count": len(remaining),
        "core_90d_all_strict_usable": "yes" if not remaining else "no",
        "remaining_provisional_days": remaining_days,
    }
    lines = [
        "# PROFIT CONTROL — Force Convert All Provisional Days To Strict Usable v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Main Result",
        "",
        f"- provisional core days before pass: `{summary['prior_provisional_days']}`",
        f"- converted to strict usable: `{summary['converted_to_strict_usable']}`",
        f"- remaining provisional after pass: `{summary['remaining_provisional_days_count']}`",
        f"- core_90d_all_strict_usable = `{summary['core_90d_all_strict_usable']}`",
        "",
        "## Conversion Table",
        "",
        md_table(
            rows,
            [
                "day",
                "prior_status",
                "final_status_after_force_conversion",
                "strict_usable_after",
                "blocking_reason_if_no",
                "finance_stg_rows_after",
                "improved_vs_prior",
            ],
        ),
        "",
        "## Final Answer",
        "",
        f"- remaining_provisional_days = `{summary['remaining_provisional_days']}`",
        "- if a day stayed provisional, the reason below is the exact blocking reason used in this pass.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    configure_env()
    provisional_days = load_provisional_core_days()
    if os.getenv("PC_SKIP_NETWORK_CONTROL") == "1":
        rows = current_state_rows(provisional_days)
        summary_rows = [
            {
                "snapshot_date": now_iso(),
                "prior_provisional_days": len(rows),
                "converted_to_strict_usable": sum(1 for row in rows if row["strict_usable_after"] == "yes"),
                "remaining_provisional_days_count": sum(1 for row in rows if row["strict_usable_after"] != "yes"),
                "core_90d_all_strict_usable": "yes" if all(row["strict_usable_after"] == "yes" for row in rows) else "no",
                "remaining_provisional_days": ", ".join(row["day"] for row in rows if row["strict_usable_after"] != "yes"),
            }
        ]
        write_csv(RESULT_CSV, rows)
        write_csv(SUMMARY_CSV, summary_rows)
        DOC_PATH.write_text(build_report(rows), encoding="utf-8")
        rebuild_full_outputs()
        print(f"Wrote {RESULT_CSV}")
        print(f"Wrote {SUMMARY_CSV}")
        print(f"Wrote {DOC_PATH}")
        return

    if provisional_days:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wb_finance_canonical_default_runner_v1.py"), "--days", *provisional_days],
            cwd=ROOT,
            check=True,
        )

    rows = current_state_rows(provisional_days)

    summary_rows = [
        {
            "snapshot_date": now_iso(),
            "prior_provisional_days": len(rows),
            "converted_to_strict_usable": sum(1 for row in rows if row["strict_usable_after"] == "yes"),
            "remaining_provisional_days_count": sum(1 for row in rows if row["strict_usable_after"] != "yes"),
            "core_90d_all_strict_usable": "yes" if all(row["strict_usable_after"] == "yes" for row in rows) else "no",
            "remaining_provisional_days": ", ".join(row["day"] for row in rows if row["strict_usable_after"] != "yes"),
        }
    ]

    write_csv(RESULT_CSV, rows)
    write_csv(SUMMARY_CSV, summary_rows)
    DOC_PATH.write_text(build_report(rows), encoding="utf-8")
    rebuild_full_outputs()
    print(f"Wrote {RESULT_CSV}")
    print(f"Wrote {SUMMARY_CSV}")
    print(f"Wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
