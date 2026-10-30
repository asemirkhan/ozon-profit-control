from __future__ import annotations

import csv
import hashlib
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from psycopg.rows import dict_row

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.finance import _extract_finance_rows_from_payload, _extract_rrd_cursor
from src.connectors.wb.http import request_json_with_status
from src.loaders.upsert_wb import upsert_stg_wb_finance
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.utils.db import get_conn

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
MOSCOW = ZoneInfo("Europe/Moscow")
JANUARY_START = date(2026, 1, 26)
JANUARY_END = date(2026, 1, 31)
PERIOD_START = date(2026, 1, 26)
PERIOD_END = date(2026, 3, 26)

OUTPUT_DIR = ROOT / "exports" / "wb_finance_january_full_pass_with_saturation_closing_v1"
DOC_PATH = ROOT / "artifacts" / "wb_finance_january_full_pass_with_saturation_closing_v1.md"
DAY_CSV = OUTPUT_DIR / "january_day_results.csv"
PASS_CSV = OUTPUT_DIR / "january_pass_metrics.csv"
TIMING_CSV = OUTPUT_DIR / "january_timing_summary.csv"
COVERAGE_CSV = OUTPUT_DIR / "coverage_snapshot_after_january_full_pass.csv"
TARGET_DAYS_CSV = OUTPUT_DIR / "january_target_days.csv"

LIMIT = int(os.getenv("WB_FINANCE_PAGE_LIMIT", "1000"))
INTER_REQUEST_PAUSE_SEC = 50
INTER_PASS_PAUSE_SEC = 120
POST_429_COOLING_SEC = 150
MAX_PASSES = 3
DAY_BUDGET_SEC = 20 * 60
BETWEEN_DAY_COOLDOWN_SEC = 180
ADAPTIVE_BACKOFF_ENABLED = os.getenv("PC_FINANCE_ADAPTIVE_BACKOFF", "1") not in {"0", "false", "False"}
ADAPTIVE_MIN_RPS = float(os.getenv("PC_FINANCE_ADAPTIVE_MIN_RPS", "0.08"))
ADAPTIVE_RPS_STEP = float(os.getenv("PC_FINANCE_ADAPTIVE_RPS_STEP", "0.02"))
ADAPTIVE_MAX_INTER_REQUEST_PAUSE_SEC = int(os.getenv("PC_FINANCE_ADAPTIVE_MAX_INTER_REQUEST_PAUSE_SEC", "180"))
ADAPTIVE_MAX_POST_429_COOLING_SEC = int(os.getenv("PC_FINANCE_ADAPTIVE_MAX_POST_429_COOLING_SEC", "600"))
ADAPTIVE_COOLING_STEP_SEC = int(os.getenv("PC_FINANCE_ADAPTIVE_COOLING_STEP_SEC", "20"))
ADAPTIVE_INTER_REQUEST_STEP_SEC = int(os.getenv("PC_FINANCE_ADAPTIVE_INTER_REQUEST_STEP_SEC", "5"))


@dataclass
class PassMetric:
    day: str
    pass_number: int
    rows_total: int
    new_unique_rrd_id_count: int
    duplicate_rows_count: int
    total_amount: str
    amount_delta_this_pass: str
    min_rrd_id: int | None
    max_rrd_id: int | None
    unique_rrd_id_hash: str
    growth_ratio_rows: float
    growth_ratio_amount: float
    duplicate_ratio: float
    requests_in_pass: int
    pass_time_minutes: float
    had_429: bool
    stop_reason: str


@dataclass
class DayResult:
    day: str
    final_rows: int
    final_amount: str
    final_status: str
    total_time_minutes: float
    total_passes: int
    total_requests: int
    count_429: int
    saturated_after_pass1: bool
    had_new_rrd_id_after_pass1: bool
    amount_changed_after_pass1: bool
    min_rrd_id: int | None
    max_rrd_id: int | None
    notes: str


@dataclass
class DayClassification:
    final_status: str
    saturated_after_pass1: bool
    had_new_rrd_id_after_pass1: bool
    amount_changed_after_pass1: bool
    notes: list[str]


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def configure_env() -> None:
    os.environ["WB_FINANCE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"
    os.environ["WB_MAX_REQUESTS_PER_SECOND"] = "1"
    os.environ["WB_MAX_PARALLEL_REQUESTS_PER_SOURCE"] = "1"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


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


def decimal_to_str(value: Decimal) -> str:
    return f"{value:.2f}"


def growth_ratio_int(delta: int, previous_total: int) -> float:
    if previous_total <= 0:
        return 1.0 if delta > 0 else 0.0
    return delta / previous_total


def growth_ratio_amount(delta: Decimal, previous_total: Decimal) -> float:
    if previous_total == 0:
        return 1.0 if delta != 0 else 0.0
    return float(delta / abs(previous_total))


def duplicate_ratio(new_unique_rrd_id_count: int, duplicate_rows_count: int) -> float:
    total = new_unique_rrd_id_count + duplicate_rows_count
    if total <= 0:
        return 0.0
    return duplicate_rows_count / total


def unique_rrd_id_hash(rrd_ids: set[int]) -> str:
    encoded = ",".join(str(item) for item in sorted(rrd_ids)).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:16]


def fetch_page(day: str, cursor: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    diagnostics: list[dict[str, Any]] = []
    try:
        payload, status = request_json_with_status(
            method="GET",
            url=os.getenv("WB_FINANCE_URL", "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod"),
            headers=get_wb_headers(),
            params={"dateFrom": day, "dateTo": day, "limit": LIMIT, "rrdid": cursor},
            timeout=30,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="finance",
            rate_endpoint="/api/v5/supplier/reportDetailByPeriod",
        )
    except Exception:
        status = int(diagnostics[-1].get("http_status") or 0) if diagnostics else None
        payload = []

    if isinstance(payload, dict):
        rows = _extract_finance_rows_from_payload(payload)
    elif isinstance(payload, list):
        rows = [row for row in payload if isinstance(row, dict)]
    else:
        rows = []
    return diagnostics, rows, status


def make_success_page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"request_success": True, "response": {"finance": rows}}


def normalized_snapshot(day: str, successful_pages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], Decimal, set[int], int | None, int | None]:
    result = build_wb_finance_normalization_result(
        successful_pages,
        target_day=date.fromisoformat(day),
    )
    normalized_rows = result["rows"]
    total_amount = sum(((row.get("amount") or Decimal("0")) for row in normalized_rows), Decimal("0"))
    rrd_ids = {int(row["rrd_id"]) for row in normalized_rows if row.get("rrd_id") is not None}
    min_rrd_id = min(rrd_ids) if rrd_ids else None
    max_rrd_id = max(rrd_ids) if rrd_ids else None
    return normalized_rows, total_amount, rrd_ids, min_rrd_id, max_rrd_id


def classify_day(pass_metrics: list[PassMetric]) -> DayClassification:
    if not pass_metrics:
        return DayClassification(
            final_status="still_unresolved_after_control_passes",
            saturated_after_pass1=False,
            had_new_rrd_id_after_pass1=False,
            amount_changed_after_pass1=False,
            notes=["no_pass_metrics"],
        )

    final_status = "still_unresolved_after_control_passes"
    saturated_after_pass1 = False
    had_new_rrd_id_after_pass1 = False
    amount_changed_after_pass1 = False
    notes: list[str] = []

    useful_index = next((idx for idx, item in enumerate(pass_metrics) if item.rows_total > 0), None)
    if useful_index is None:
        return DayClassification(
            final_status="no_useful_pass_data",
            saturated_after_pass1=False,
            had_new_rrd_id_after_pass1=False,
            amount_changed_after_pass1=False,
            notes=["no_useful_pass_found"],
        )

    useful_pass = pass_metrics[useful_index]
    later_passes = pass_metrics[useful_index + 1 :]
    if useful_index == 0:
        had_new_rrd_id_after_pass1 = any(item.new_unique_rrd_id_count > 0 for item in later_passes)
        amount_changed_after_pass1 = any(item.total_amount != useful_pass.total_amount for item in later_passes)
        range_changed_after_pass1 = any(
            item.min_rrd_id != useful_pass.min_rrd_id or item.max_rrd_id != useful_pass.max_rrd_id
            for item in later_passes
        )
        notes.append(f"range_changed_after_pass1={range_changed_after_pass1}")
        if later_passes and not had_new_rrd_id_after_pass1 and not amount_changed_after_pass1 and not range_changed_after_pass1:
            final_status = "saturated_after_pass1"
            saturated_after_pass1 = True
    else:
        had_new_after_first_useful = any(item.new_unique_rrd_id_count > 0 for item in later_passes)
        amount_changed_after_first_useful = any(item.total_amount != useful_pass.total_amount for item in later_passes)
        range_changed_after_first_useful = any(
            item.min_rrd_id != useful_pass.min_rrd_id or item.max_rrd_id != useful_pass.max_rrd_id
            for item in later_passes
        )
        notes.append(f"first_useful_pass={useful_pass.pass_number}")
        notes.append(f"range_changed_after_first_useful={range_changed_after_first_useful}")
        if later_passes and not had_new_after_first_useful and not amount_changed_after_first_useful and not range_changed_after_first_useful:
            final_status = "completed_stable"

    return DayClassification(
        final_status=final_status,
        saturated_after_pass1=saturated_after_pass1,
        had_new_rrd_id_after_pass1=had_new_rrd_id_after_pass1,
        amount_changed_after_pass1=amount_changed_after_pass1,
        notes=notes,
    )


def unresolved_january_days() -> list[date]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                WITH days AS (
                  SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS day
                ),
                stg AS (
                  SELECT operation_date::date AS day, COUNT(*)::int AS stg_rows
                  FROM {SCHEMA}.stg_wb_finance
                  WHERE operation_date BETWEEN %s AND %s
                  GROUP BY 1
                )
                SELECT d.day
                FROM days d
                LEFT JOIN {SCHEMA}.finance_day_state f ON f.day = d.day
                LEFT JOIN stg ON stg.day = d.day
                WHERE COALESCE(f.status, '') <> 'success'
                ORDER BY d.day
                """,
                (JANUARY_START, JANUARY_END, JANUARY_START, JANUARY_END),
            )
            return [row["day"] for row in cur.fetchall()]


def coverage_snapshot() -> dict[str, int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH days AS (
                  SELECT generate_series(%s::date, %s::date, interval '1 day')::date AS day
                ),
                stg AS (
                  SELECT operation_date::date AS day, COUNT(*)::int AS stg_rows
                  FROM {SCHEMA}.stg_wb_finance
                  WHERE operation_date BETWEEN %s AND %s
                  GROUP BY 1
                )
                SELECT
                  COUNT(*) FILTER (WHERE COALESCE(stg.stg_rows, 0) > 0)::int AS days_with_data,
                  COUNT(*) FILTER (WHERE COALESCE(f.status, '') = 'success')::int AS full_days,
                  COUNT(*) FILTER (
                    WHERE COALESCE(f.status, '') <> 'success'
                      AND COALESCE(f.status, '') <> 'manual_hold'
                      AND COALESCE(stg.stg_rows, 0) > 0
                  )::int AS partial_days,
                  COUNT(*) FILTER (WHERE COALESCE(f.status, '') = 'manual_hold')::int AS empty_days,
                  COUNT(*) FILTER (
                    WHERE COALESCE(f.status, '') <> 'success'
                      AND COALESCE(f.status, '') <> 'manual_hold'
                      AND COALESCE(stg.stg_rows, 0) = 0
                  )::int AS unresolved_days
                FROM days d
                LEFT JOIN stg ON stg.day = d.day
                LEFT JOIN {SCHEMA}.finance_day_state f ON f.day = d.day
                """,
                (PERIOD_START, PERIOD_END, PERIOD_START, PERIOD_END),
            )
            row = cur.fetchone()
    return {
        "source": "finance",
        "expected_days": (PERIOD_END - PERIOD_START).days + 1,
        "days_with_data": int(row[0] or 0),
        "full_days": int(row[1] or 0),
        "partial_days": int(row[2] or 0),
        "empty_days": int(row[3] or 0),
        "unresolved_days": int(row[4] or 0),
    }


def update_finance_day_state(
    *,
    day_value: date,
    status: str,
    complete: bool,
    analytic_usable: bool,
    raw_presence: bool,
    stg_presence: bool,
    rows_done: int,
    http_429_count: int,
    terminal_reason_code: str | None,
    reason_code: str | None,
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
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
              %s, %s, %s, %s, %s,
              %s, 0, 0, 0,
              %s, 1, 0, 0,
              %s, 0, now(), CASE WHEN %s::boolean THEN now() ELSE NULL END,
              NULL, %s, NULL, NULL,
              NULL, %s, %s,
              FALSE, NULL, now(),
              %s, CASE WHEN %s::text IS NOT NULL THEN now() ELSE NULL END, %s,
              %s, %s, FALSE
            )
            ON CONFLICT (day) DO UPDATE SET
              status = EXCLUDED.status,
              raw_presence_flag = EXCLUDED.raw_presence_flag,
              stg_presence_flag = EXCLUDED.stg_presence_flag,
              finance_day_complete_flag = EXCLUDED.finance_day_complete_flag,
              final_analytic_usable_flag = EXCLUDED.final_analytic_usable_flag,
              rows_done_total = EXCLUDED.rows_done_total,
              attempts_total = GREATEST({SCHEMA}.finance_day_state.attempts_total, EXCLUDED.attempts_total),
              http_429_count_total = GREATEST({SCHEMA}.finance_day_state.http_429_count_total, EXCLUDED.http_429_count_total),
              last_progress_at = EXCLUDED.last_progress_at,
              last_success_at = EXCLUDED.last_success_at,
              last_rows_delta = EXCLUDED.last_rows_delta,
              reason_code = EXCLUDED.reason_code,
              source_pressure_flag = EXCLUDED.source_pressure_flag,
              terminal_reason_code = EXCLUDED.terminal_reason_code,
              terminal_decision_at = EXCLUDED.terminal_decision_at,
              terminal_decision_basis = EXCLUDED.terminal_decision_basis,
              terminal_confidence_level = EXCLUDED.terminal_confidence_level,
              recheck_required_flag = EXCLUDED.recheck_required_flag,
              updated_at = now()
            """,
            (
                day_value,
                status,
                raw_presence,
                stg_presence,
                complete,
                analytic_usable,
                rows_done,
                http_429_count,
                complete,
                rows_done,
                reason_code,
                status == "started_but_not_finished",
                terminal_reason_code,
                terminal_reason_code,
                "january_saturation_full_pass_v1" if complete else "january_saturation_partial_v1",
                "high" if complete else "medium",
                not complete,
            ),
        )
        conn.commit()


def update_queue_state(
    *,
    day_value: date,
    state: str,
    last_http_status: int | None,
    last_error_type: str | None,
    last_error: str | None,
    day_close_reason: str | None,
) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.ingestion_backfill_queue (
              schema_name, marketplace, source, target_date, lane, priority, state, attempts, max_attempts,
              retry_after_ts, last_attempt_at, last_http_status, last_error_type, last_error, rate_limit_retry,
              rate_limit_limit, rate_limit_reset, run_id, created_at, updated_at, worker_id, leased_at,
              lease_expires_at, heartbeat_at, order_resume_offset, order_pages_done, order_rows_done,
              order_last_progress_at, order_no_progress_passes, day_close_reason, finance_resume_rrdid,
              finance_pages_done, finance_rows_done, finance_last_progress_at, finance_no_progress_passes
            ) VALUES (
              %s, 'wb', 'finance', %s, 'quiet_lane', 10, %s, 1, 20,
              NULL, now(), %s, %s, %s, NULL,
              NULL, NULL, NULL, now(), now(), NULL, NULL,
              NULL, NULL, 0, 0, 0,
              NULL, 0, %s, 0,
              0, 0, NULL, 0
            )
            ON CONFLICT (schema_name, marketplace, source, target_date) DO UPDATE SET
              lane = EXCLUDED.lane,
              priority = EXCLUDED.priority,
              state = EXCLUDED.state,
              attempts = GREATEST({SCHEMA}.ingestion_backfill_queue.attempts, EXCLUDED.attempts),
              last_attempt_at = now(),
              last_http_status = EXCLUDED.last_http_status,
              last_error_type = EXCLUDED.last_error_type,
              last_error = EXCLUDED.last_error,
              day_close_reason = EXCLUDED.day_close_reason,
              updated_at = now()
            """,
            (
                SCHEMA,
                day_value,
                state,
                last_http_status,
                last_error_type,
                last_error,
                day_close_reason,
            ),
        )
        conn.commit()


def run_day(day_value: date) -> tuple[DayResult, list[PassMetric]]:
    day = day_value.isoformat()
    started = time.monotonic()
    request_no = 0
    known_rrd_ids: set[int] = set()
    successful_pages: list[dict[str, Any]] = []
    pass_metrics: list[PassMetric] = []
    previous_rows_total = 0
    previous_amount_total = Decimal("0")
    base_inter_request_pause = INTER_REQUEST_PAUSE_SEC
    base_post_429_cooldown = POST_429_COOLING_SEC
    base_rps = float(os.getenv("WB_HTTP_GLOBAL_RPS") or "0.2")
    adaptive_inter_request_pause = base_inter_request_pause
    adaptive_post_429 = base_post_429_cooldown
    adaptive_rps = base_rps

    def _apply_rps(value: float) -> None:
        os.environ["WB_HTTP_GLOBAL_RPS"] = f"{value:.3f}"
        os.environ["WB_MAX_REQUESTS_PER_SECOND"] = f"{value:.3f}"

    for pass_number in range(1, MAX_PASSES + 1):
        cursor = 0
        page_number = 0
        pass_request_count = 0
        pass_duplicate_rows = 0
        pass_had_429 = False
        pass_started = time.monotonic()
        stop_reason = "max_passes_reached"

        while True:
            if time.monotonic() - started >= DAY_BUDGET_SEC:
                stop_reason = "day_budget_exhausted"
                break

            page_number += 1
            request_no += 1
            pass_request_count += 1
            diagnostics, rows, status = fetch_page(day, cursor)
            if not diagnostics:
                diagnostics = [{"attempt_no": 1, "http_status": status, "error_type": None}]

            raw_rrd_ids = [rrdid for row in rows if (rrdid := _extract_rrd_cursor(row)) is not None]
            unique_rrd_ids = set(raw_rrd_ids)
            known_before_page = set(known_rrd_ids)
            new_unique_ids = unique_rrd_ids - known_before_page
            page_duplicate_rows = sum(1 for rrdid in raw_rrd_ids if rrdid in known_before_page)
            max_rrd_id = max(unique_rrd_ids) if unique_rrd_ids else None

            had_429 = False
            for attempt in diagnostics:
                http_status = int(attempt.get("http_status") or 0) if attempt.get("http_status") is not None else status
                error_type = str(attempt.get("error_type") or "") or None
                if http_status == 429 or error_type == "rate_limit":
                    had_429 = True
                    break

            if had_429:
                pass_had_429 = True
            else:
                successful_pages.append(make_success_page(rows))
                known_rrd_ids.update(new_unique_ids)
            pass_duplicate_rows += page_duplicate_rows

            if had_429:
                if ADAPTIVE_BACKOFF_ENABLED:
                    adaptive_post_429 = min(
                        ADAPTIVE_MAX_POST_429_COOLING_SEC,
                        adaptive_post_429 + ADAPTIVE_COOLING_STEP_SEC,
                    )
                    adaptive_inter_request_pause = min(
                        ADAPTIVE_MAX_INTER_REQUEST_PAUSE_SEC,
                        adaptive_inter_request_pause + ADAPTIVE_INTER_REQUEST_STEP_SEC,
                    )
                    adaptive_rps = max(ADAPTIVE_MIN_RPS, adaptive_rps - ADAPTIVE_RPS_STEP)
                    _apply_rps(adaptive_rps)
                stop_reason = "rate_limit"
                break
            if len(rows) == 0:
                error_type = str(diagnostics[-1].get("error_type") or "") or ""
                stop_reason = "http_no_content" if int(diagnostics[-1].get("http_status") or 0) == 204 or error_type.startswith("http_no_content") else "empty_response"
                break
            if len(rows) < LIMIT:
                stop_reason = "short_terminal_page"
                break
            if max_rrd_id is None or max_rrd_id <= cursor:
                stop_reason = "cursor_stall"
                break

            cursor = max_rrd_id
            time.sleep(adaptive_inter_request_pause)

        normalized_rows, amount_total, normalized_rrd_ids, min_rrd_id, max_rrd_id = normalized_snapshot(day, successful_pages)
        rows_total = len(normalized_rows)
        new_rows_total = rows_total - previous_rows_total
        amount_delta = amount_total - previous_amount_total
        pass_metrics.append(
            PassMetric(
                day=day,
                pass_number=pass_number,
                rows_total=rows_total,
                new_unique_rrd_id_count=new_rows_total,
                duplicate_rows_count=pass_duplicate_rows,
                total_amount=decimal_to_str(amount_total),
                amount_delta_this_pass=decimal_to_str(amount_delta),
                min_rrd_id=min_rrd_id,
                max_rrd_id=max_rrd_id,
                unique_rrd_id_hash=unique_rrd_id_hash(normalized_rrd_ids),
                growth_ratio_rows=growth_ratio_int(new_rows_total, previous_rows_total),
                growth_ratio_amount=growth_ratio_amount(amount_delta, previous_amount_total),
                duplicate_ratio=duplicate_ratio(new_rows_total, pass_duplicate_rows),
                requests_in_pass=pass_request_count,
                pass_time_minutes=round((time.monotonic() - pass_started) / 60.0, 2),
                had_429=pass_had_429,
                stop_reason=stop_reason,
            )
        )

        previous_rows_total = rows_total
        previous_amount_total = amount_total

        if pass_number < MAX_PASSES:
            if ADAPTIVE_BACKOFF_ENABLED and not pass_had_429:
                adaptive_post_429 = max(base_post_429_cooldown, adaptive_post_429 - ADAPTIVE_COOLING_STEP_SEC)
                adaptive_inter_request_pause = max(base_inter_request_pause, adaptive_inter_request_pause - ADAPTIVE_INTER_REQUEST_STEP_SEC)
                adaptive_rps = min(base_rps, adaptive_rps + ADAPTIVE_RPS_STEP)
                _apply_rps(adaptive_rps)
            time.sleep(adaptive_post_429 if pass_had_429 else INTER_PASS_PAUSE_SEC)

    final_rows = pass_metrics[-1].rows_total if pass_metrics else 0
    final_amount = pass_metrics[-1].total_amount if pass_metrics else "0.00"
    classification = classify_day(pass_metrics)
    saturated_after_pass1 = classification.saturated_after_pass1
    amount_changed_after_pass1 = classification.amount_changed_after_pass1
    had_new_rrd_id_after_pass1 = classification.had_new_rrd_id_after_pass1
    notes = list(classification.notes)
    final_status = classification.final_status

    count_429 = sum(1 for item in pass_metrics if item.had_429)

    normalized_rows, _, _, min_rrd_id, max_rrd_id = normalized_snapshot(day, successful_pages)
    if normalized_rows:
        upsert_stg_wb_finance(SCHEMA, normalized_rows)

    if final_status in {"saturated_after_pass1", "completed_stable"}:
        update_finance_day_state(
            day_value=day_value,
            status="success",
            complete=True,
            analytic_usable=True,
            raw_presence=True,
            stg_presence=final_rows > 0,
            rows_done=final_rows,
            http_429_count=count_429,
            terminal_reason_code=final_status,
            reason_code="january_saturation_full_pass_v1",
        )
        update_queue_state(
            day_value=day_value,
            state="success",
            last_http_status=429 if count_429 else None,
            last_error_type=None,
            last_error=None,
            day_close_reason=final_status,
        )
        notes.append("queue/day_state_updated_to_success")
    elif final_rows > 0:
        update_finance_day_state(
            day_value=day_value,
            status="started_but_not_finished",
            complete=False,
            analytic_usable=False,
            raw_presence=True,
            stg_presence=True,
            rows_done=final_rows,
            http_429_count=count_429,
            terminal_reason_code=None,
            reason_code="january_saturation_full_pass_v1_partial",
        )
        update_queue_state(
            day_value=day_value,
            state="partial_progress",
            last_http_status=429 if count_429 else None,
            last_error_type=final_status,
            last_error="non_saturated_after_control_passes",
            day_close_reason=final_status,
        )
        notes.append("partial_progress_persisted")
    else:
        update_queue_state(
            day_value=day_value,
            state="retry",
            last_http_status=429 if count_429 else None,
            last_error_type=final_status,
            last_error="no_rows_after_control_passes",
            day_close_reason=final_status,
        )
        notes.append("retry_persisted")

    result = DayResult(
        day=day,
        final_rows=final_rows,
        final_amount=final_amount,
        final_status=final_status,
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        total_passes=len(pass_metrics),
        total_requests=sum(item.requests_in_pass for item in pass_metrics),
        count_429=count_429,
        saturated_after_pass1=saturated_after_pass1,
        had_new_rrd_id_after_pass1=had_new_rrd_id_after_pass1,
        amount_changed_after_pass1=amount_changed_after_pass1,
        min_rrd_id=min_rrd_id,
        max_rrd_id=max_rrd_id,
        notes="; ".join(notes),
    )
    return result, pass_metrics


def build_report(
    *,
    target_days: list[date],
    day_results: list[DayResult],
    timing_summary: dict[str, Any],
    coverage_before: dict[str, int],
    coverage_after: dict[str, int],
) -> str:
    unresolved_after = [row.day for row in day_results if row.final_status not in {"saturated_after_pass1", "completed_stable"}]
    lines = [
        "# WB Finance January Full Pass With Saturation Closing v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Target Days",
        "",
        f"- january unresolved days before run: `{', '.join(day.isoformat() for day in target_days) or 'none'}`",
        f"- total january days taken in work: `{len(target_days)}`",
        "",
        "## January Full Pass Report",
        "",
        md_table(
            [asdict(row) for row in day_results],
            [
                "day",
                "final_rows",
                "final_amount",
                "final_status",
                "total_time_minutes",
                "total_passes",
                "total_requests",
                "count_429",
                "saturated_after_pass1",
                "had_new_rrd_id_after_pass1",
                "amount_changed_after_pass1",
                "notes",
            ],
        ),
        "",
        "## January Timing Summary",
        "",
        md_table(
            [timing_summary],
            [
                "total_days",
                "closed_days",
                "still_unresolved_days",
                "total_runtime_minutes",
                "avg_minutes_per_day",
                "days_per_hour",
            ],
        ),
        "",
        "## Coverage Update",
        "",
        md_table(
            [
                {"phase": "before", **coverage_before},
                {"phase": "after", **coverage_after},
            ],
            ["phase", "source", "expected_days", "days_with_data", "full_days", "partial_days", "empty_days", "unresolved_days"],
        ),
        "",
        "## Final Notes",
        "",
        f"- january days closed as saturated/completed: `{timing_summary['closed_days']}` / `{timing_summary['total_days']}`",
        f"- january days still unresolved after control passes: `{', '.join(unresolved_after) or 'none'}`",
        "- replay-tail without new `rrd_id`, without amount growth, and without `rrd_id` range expansion is treated as factual saturation rather than retry-needed failure.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    load_dotenv(ROOT / ".env")
    configure_env()

    target_days = unresolved_january_days()
    coverage_before = coverage_snapshot()
    day_results: list[DayResult] = []
    pass_rows: list[PassMetric] = []

    overall_started = time.monotonic()
    for index, day_value in enumerate(target_days, start=1):
        result, pass_metrics = run_day(day_value)
        day_results.append(result)
        pass_rows.extend(pass_metrics)
        print(
            f"[{index}/{len(target_days)}] {result.day} status={result.final_status} "
            f"rows={result.final_rows} amount={result.final_amount} time={result.total_time_minutes}m",
            flush=True,
        )
        if index < len(target_days):
            time.sleep(BETWEEN_DAY_COOLDOWN_SEC)

    total_runtime_minutes = round((time.monotonic() - overall_started) / 60.0, 2)
    closed_days = sum(1 for row in day_results if row.final_status in {"saturated_after_pass1", "completed_stable"})
    total_days = len(day_results)
    avg_minutes_per_day = round(total_runtime_minutes / total_days, 2) if total_days else 0.0
    days_per_hour = round((closed_days * 60.0) / total_runtime_minutes, 2) if total_runtime_minutes > 0 else 0.0
    timing_summary = {
        "total_days": total_days,
        "closed_days": closed_days,
        "still_unresolved_days": total_days - closed_days,
        "total_runtime_minutes": total_runtime_minutes,
        "avg_minutes_per_day": avg_minutes_per_day,
        "days_per_hour": days_per_hour,
    }

    coverage_after = coverage_snapshot()

    write_csv(TARGET_DAYS_CSV, [{"day": day.isoformat()} for day in target_days])
    write_csv(DAY_CSV, [asdict(row) for row in day_results])
    write_csv(PASS_CSV, [asdict(row) for row in pass_rows])
    write_csv(TIMING_CSV, [timing_summary])
    write_csv(COVERAGE_CSV, [coverage_after])
    DOC_PATH.write_text(
        build_report(
            target_days=target_days,
            day_results=day_results,
            timing_summary=timing_summary,
            coverage_before=coverage_before,
            coverage_after=coverage_after,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {TARGET_DAYS_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {DAY_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {PASS_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {TIMING_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {COVERAGE_CSV.relative_to(ROOT)}", flush=True)
    print(f"Wrote {DOC_PATH.relative_to(ROOT)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
