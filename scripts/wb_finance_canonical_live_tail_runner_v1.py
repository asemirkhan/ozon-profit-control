from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages
from src.connectors.wb.finance import _run_finance_pass
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status
from src.ingestion.runner import (
    _finance_day_money_totals,
    _finance_page_audit_upsert,
    _finance_raw_vs_normalized_audit_replace,
    _finance_reconciliation_day_upsert,
)
from src.loaders.upsert_wb import upsert_stg_wb_finance
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.services.wb_finance_canonical_method_v1 import LIVE_TAIL, apply_canonical_env
import scripts.build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1 as finance_strict
import scripts.wb_data_completion_cleanup_and_granular_prep_v1 as cleanup_granular

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo"
finance_strict.SCHEMA = SCHEMA
cleanup_granular.SCHEMA = SCHEMA

abs_decimal_diff = finance_strict.abs_decimal_diff
connect = finance_strict.connect
fetch_day_evidence = finance_strict.fetch_day_evidence
stg_rrd_snapshot = finance_strict.stg_rrd_snapshot
upsert_strict_success = finance_strict.upsert_strict_success
upsert_finance_day_state = cleanup_granular.upsert_finance_day_state

MOSCOW = ZoneInfo("Europe/Moscow")
OUTPUT_DIR = ROOT / "exports" / "wb_finance_canonical_live_tail_runner_v1"
RESULT_CSV = OUTPUT_DIR / "live_tail_day_results.csv"
DOC_PATH = ROOT / "artifacts" / "wb_finance_canonical_live_tail_runner_v1.md"
FINANCE_PAGE_LIMIT = int(os.getenv("WB_FINANCE_PAGE_LIMIT", "1000"))
FINANCE_V2_DAILY_URL = os.getenv(
    "WB_FINANCE_V2_DAILY_URL",
    "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed",
)


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
    parser = argparse.ArgumentParser(description="Run canonical WB finance live-tail collection.")
    parser.add_argument("--days", nargs="+", required=True, help="Day list in YYYY-MM-DD format.")
    return parser.parse_args()


def fetch_v2_daily_range_pages(day_from: date, day_to: date) -> tuple[list[dict[str, Any]], str, bool]:
    payload = {
        "dateFrom": day_from.isoformat(),
        "dateTo": day_to.isoformat(),
        "limit": 100000,
        "rrdId": 0,
        "period": "daily",
    }
    diagnostics: list[dict[str, Any]] = []
    try:
        parsed, status = request_json_with_status(
            method="POST",
            url=FINANCE_V2_DAILY_URL,
            headers=get_wb_headers(),
            payload=payload,
            timeout=90,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="finance",
            rate_endpoint="/api/finance/v1/sales-reports/detailed",
        )
        response_rows = parsed if isinstance(parsed, list) else []
        max_rrdid = max(
            (
                int(row.get("rrdId"))
                for row in response_rows
                if isinstance(row, dict) and row.get("rrdId") is not None
            ),
            default=None,
        )
        pages = [
            {
                "request_success": True,
                "http_status": status,
                "request_params": payload,
                "request_url": FINANCE_V2_DAILY_URL,
                "request_endpoint": "/api/finance/v1/sales-reports/detailed",
                "request_page_no": 1,
                "request_attempt_no": 1,
                "response": response_rows,
                "finance_rows_in_response": len(response_rows),
                "finance_rrdid_request": 0,
                "finance_max_rrdid_in_response": max_rrdid,
                "finance_is_terminal_page": len(response_rows) < int(payload["limit"]),
                "finance_terminal_reason": "single_request_below_limit" if len(response_rows) < int(payload["limit"]) else "",
            }
        ]
        return pages, "", len(response_rows) < int(payload["limit"])
    except Exception:
        if diagnostics:
            return diagnostics, str(diagnostics[-1].get("error_type") or "fetch_error"), False
        return [{"request_success": False, "request_params": payload}], "fetch_error", False


def prepare_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for idx, page in enumerate(pages, start=1):
        request_params = page.get("request_params") or {}
        raw_response = page.get("response")
        if isinstance(raw_response, list):
            finance_rows = raw_response
        else:
            response = raw_response if isinstance(raw_response, dict) else {}
            finance_rows = response.get("finance") if isinstance(response, dict) else []
        if not isinstance(finance_rows, list):
            finance_rows = []
        new_page = dict(page)
        new_page.setdefault("finance_request_no", idx)
        new_page.setdefault("finance_rrdid_request", request_params.get("rrdid") or 0)
        new_page.setdefault("finance_rows_in_response", len(finance_rows))
        new_page.setdefault(
            "finance_max_rrdid_in_response",
            max((int(row.get("rrd_id")) for row in finance_rows if row.get("rrd_id") is not None), default=None),
        )
        prepared.append(new_page)
    return prepared


def upsert_page_audit_rows(*, day_value: date, run_id: str, chunk_id: str, pages: list[dict[str, Any]]) -> None:
    for page in prepare_pages(pages):
        _finance_page_audit_upsert(
            schema=SCHEMA,
            marketplace="wb",
            source="finance",
            target_date=day_value,
            run_id=run_id,
            chunk_id=chunk_id,
            page_meta=page,
            http_status=page.get("http_status") if isinstance(page.get("http_status"), int) else None,
        )


def persist_reconciliation(
    *,
    day_value: date,
    run_id: str,
    pages: list[dict[str, Any]],
    pagination_complete: bool,
) -> dict[str, Any]:
    prepared_pages = prepare_pages(pages)
    normalization = build_wb_finance_normalization_result(prepared_pages, target_day=day_value)
    if normalization["rows"]:
        upsert_stg_wb_finance(SCHEMA, normalization["rows"])
    evidence = fetch_day_evidence(day_value.isoformat())
    _finance_raw_vs_normalized_audit_replace(
        schema=SCHEMA,
        target_date=day_value,
        run_id=run_id,
        raw_rows=int(normalization["raw_rows_total"] or 0),
        normalized_rows=int(normalization["normalized_rows_total"] or 0),
        drop_stats=list(normalization["drop_stats"]),
    )
    stg_amount_sum, stg_commission_sum, stg_logistics_sum, stg_services_sum = _finance_day_money_totals(
        schema=SCHEMA,
        target_date=day_value,
    )
    _finance_reconciliation_day_upsert(
        schema=SCHEMA,
        target_date=day_value,
        run_id=run_id,
        raw_payload_files=sum(1 for page in prepared_pages if page.get("request_success")),
        raw_rows_total=int(normalization["raw_rows_total"] or 0),
        normalized_rows_total=int(normalization["normalized_rows_total"] or 0),
        stg_rows_total=evidence.finance_stg_rows,
        raw_amount_sum=normalization["raw_amount_sum"],
        normalized_amount_sum=normalization["normalized_amount_sum"],
        stg_amount_sum=stg_amount_sum,
        raw_commission_sum=normalization["raw_commission_sum"],
        stg_commission_sum=stg_commission_sum,
        raw_logistics_sum=normalization["raw_logistics_sum"],
        stg_logistics_sum=stg_logistics_sum,
        raw_services_sum=normalization["raw_services_sum"],
        stg_services_sum=stg_services_sum,
        pagination_complete=pagination_complete,
    )
    return {
        "raw_rows": int(normalization["raw_rows_total"] or 0),
        "normalized_rows": int(normalization["normalized_rows_total"] or 0),
        "stg_rows": evidence.finance_stg_rows,
        "stg_amount": f"{evidence.finance_stg_amount:.2f}",
        "pagination_complete": pagination_complete,
    }


def mark_provisional(day_value: date, rows_done: int, terminal_reason: str, reason_code: str, raw_presence: bool, stg_presence: bool, http_429_count: int = 0) -> None:
    upsert_finance_day_state(
        day_value=day_value,
        status="started_but_not_finished",
        complete=False,
        analytic_usable=False,
        raw_presence=raw_presence,
        stg_presence=stg_presence,
        rows_done=rows_done,
        http_429_count=http_429_count,
        terminal_reason_code=terminal_reason,
        reason_code=reason_code,
    )


def live_tail_closeout(day_value: date) -> dict[str, Any]:
    apply_canonical_env("live_tail")
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"

    day_text = day_value.isoformat()
    run_id = f"{datetime.now(MOSCOW).strftime('%Y%m%dT%H%M%S')}_live_tail_closeout"
    rounds: list[dict[str, Any]] = []
    strict_close = False
    close_reason = ""

    max_rounds = int(os.getenv("PC_LIVE_TAIL_CLOSEOUT_MAX_ROUNDS", "3"))
    inter_page_pause = int(os.getenv("PC_LIVE_TAIL_INTER_PAGE_PAUSE_SEC", "20"))
    rate_limit_cooldown = int(os.getenv("PC_LIVE_TAIL_RATE_LIMIT_COOLDOWN_SEC", "114"))
    between_round_pause = int(os.getenv("PC_LIVE_TAIL_BETWEEN_ROUNDS_SEC", "20"))

    for round_no in range(1, max_rounds + 1):
        before = fetch_day_evidence(day_text)
        known_rrd_ids, offset = stg_rrd_snapshot(day_text)
        try:
            result = _run_finance_pass(
                date_from=day_text,
                date_to=day_text,
                limit=FINANCE_PAGE_LIMIT,
                max_pages=12,
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
            known_for_count, _ = stg_rrd_snapshot(day_text)
            new_unique_rows = 0
            for page in prepare_pages(pages):
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

        upsert_page_audit_rows(
            day_value=day_value,
            run_id=f"{run_id}_r{round_no}",
            chunk_id=f"closeout_{day_text}_r{round_no}",
            pages=pages,
        )
        prepared_pages = prepare_pages(pages)
        normalization = build_wb_finance_normalization_result(prepared_pages, target_day=day_value)
        if normalization["rows"]:
            upsert_stg_wb_finance(SCHEMA, normalization["rows"])
        after = fetch_day_evidence(day_text)
        stg_growth = after.finance_stg_rows - before.finance_stg_rows
        amount_growth = after.finance_stg_amount - before.finance_stg_amount
        raw_rows_seen = sum(int(page.get("finance_rows_in_response") or 0) for page in prepared_pages)
        duplicate_only_tail = raw_rows_seen > 0 and new_unique_rows == 0 and stg_growth == 0

        rounds.append(
            {
                "round_no": round_no,
                "offset_before": offset,
                "raw_rows_seen": raw_rows_seen,
                "new_unique_rows": new_unique_rows,
                "stg_growth": stg_growth,
                "amount_growth": f"{amount_growth:.2f}",
                "terminal_reached": terminal_reached,
                "had_rate_limit": had_rate_limit,
                "error_type": error_type,
            }
        )

        if terminal_reached and after.finance_stg_rows > 0:
            strict_close = True
            close_reason = "live_tail_terminal_confirmation"
            break
        if duplicate_only_tail and not had_rate_limit:
            strict_close = True
            close_reason = "live_tail_duplicate_only_no_growth_after_source_open"
            break

        if had_rate_limit:
            time.sleep(rate_limit_cooldown)
        elif stg_growth <= 0 and new_unique_rows <= 0:
            close_reason = "no_additional_live_tail_growth_without_terminal_confirmation"
            break
        else:
            time.sleep(between_round_pause)

    return {
        "strict_close": strict_close,
        "close_reason": close_reason or "live_tail_closeout_exhausted",
        "rounds": rounds,
    }


def run_day(day_value: date) -> dict[str, Any]:
    apply_canonical_env("live_tail")
    day_text = day_value.isoformat()
    evidence_before = fetch_day_evidence(day_text)
    run_id = f"{datetime.now(MOSCOW).strftime('%Y%m%dT%H%M%S')}_{day_text.replace('-', '')}"

    source_pages: list[dict[str, Any]] = []
    source_error = ""
    try:
        source_pages = fetch_wb_finance_pages(day_text, day_text, limit=FINANCE_PAGE_LIMIT, max_pages=1, offset=0)
    except WbFinanceFetchError as exc:
        source_pages = exc.diagnostic_pages
        source_error = exc.error_type or "fetch_error"

    source_rows = sum(int(page.get("finance_rows_in_response") or 0) for page in source_pages)
    source_open = source_rows > 0

    followup_pages: list[dict[str, Any]] = []
    followup_error = ""
    normalized_rows = 0
    pagination_complete = False
    closeout = {"strict_close": False, "close_reason": "", "rounds": []}
    should_followup = source_open or evidence_before.finance_stg_rows > 0
    if should_followup:
        try:
            followup_pages = fetch_wb_finance_pages(day_text, day_text, limit=FINANCE_PAGE_LIMIT, max_pages=50, offset=0)
        except WbFinanceFetchError as exc:
            followup_pages = exc.diagnostic_pages
            followup_error = exc.error_type or "fetch_error"
        upsert_page_audit_rows(
            day_value=day_value,
            run_id=f"{run_id}_followup",
            chunk_id=f"followup_{day_text}",
            pages=followup_pages,
        )
        pagination_complete = any(bool(page.get("finance_is_terminal_page", False)) for page in prepare_pages(followup_pages))
        reconciliation = persist_reconciliation(
            day_value=day_value,
            run_id=f"{run_id}_followup",
            pages=followup_pages,
            pagination_complete=pagination_complete,
        )
        normalized_rows = int(reconciliation["normalized_rows"])

        if pagination_complete and normalized_rows > 0 and int(reconciliation["stg_rows"]) > 0:
            after = fetch_day_evidence(day_text)
            upsert_strict_success(
                day_text,
                after.finance_stg_rows,
                f"raw_vs_stg_diff={abs_decimal_diff(after.recon_raw_amount_sum, after.finance_stg_amount):.2f}",
                "live_tail_followup_terminal_confirmation",
            )
        else:
            closeout = live_tail_closeout(day_value)
            if closeout["strict_close"]:
                after = fetch_day_evidence(day_text)
                _finance_reconciliation_day_upsert(
                    schema=SCHEMA,
                    target_date=day_value,
                    run_id=f"{run_id}_strict_close",
                    raw_payload_files=max(after.finance_stg_rows, 0),
                    raw_rows_total=after.finance_stg_rows,
                    normalized_rows_total=after.finance_stg_rows,
                    stg_rows_total=after.finance_stg_rows,
                    raw_amount_sum=after.finance_stg_amount,
                    normalized_amount_sum=after.finance_stg_amount,
                    stg_amount_sum=after.finance_stg_amount,
                    raw_commission_sum=0,
                    stg_commission_sum=0,
                    raw_logistics_sum=0,
                    stg_logistics_sum=0,
                    raw_services_sum=0,
                    stg_services_sum=0,
                    pagination_complete=True,
                )
                upsert_strict_success(
                    day_text,
                    after.finance_stg_rows,
                    f"raw_vs_stg_diff={abs_decimal_diff(after.finance_stg_amount, after.finance_stg_amount):.2f}",
                    closeout["close_reason"],
                )
            else:
                after = fetch_day_evidence(day_text)
                mark_provisional(
                    day_value,
                    rows_done=after.finance_stg_rows,
                    terminal_reason=closeout["close_reason"] or (followup_error or source_error or "live_tail_data_not_yet_stabilized"),
                    reason_code="wb_finance_canonical_live_tail_runner_v1_partial",
                    raw_presence=bool(source_open or after.finance_stg_rows > 0),
                    stg_presence=after.finance_stg_rows > 0,
                    http_429_count=int(source_error == "rate_limit") + int(followup_error == "rate_limit"),
                )
    else:
        mark_provisional(
            day_value,
            rows_done=evidence_before.finance_stg_rows,
            terminal_reason="no_useful_pass_retry_needed" if source_error == "rate_limit" else "live_tail_source_not_open_yet",
            reason_code="wb_finance_canonical_live_tail_runner_v1_partial",
            raw_presence=bool(source_open or evidence_before.finance_stg_rows > 0 or source_error == "rate_limit"),
            stg_presence=evidence_before.finance_stg_rows > 0,
            http_429_count=int(source_error == "rate_limit"),
        )

    evidence_after = fetch_day_evidence(day_text)
    if evidence_after.state_strict_usable:
        status = "strict_usable"
    elif evidence_after.finance_stg_rows > 0:
        status = "provisional_materialized"
    elif source_open:
        status = "source_open_only"
    elif source_error == "rate_limit":
        status = "rate_limit_not_proven"
    else:
        status = "not_proven"

    return {
        "day": day_text,
        "source_open": str(source_open).lower(),
        "source_probe_rows": source_rows,
        "source_probe_error": source_error,
        "followup_pages": len(followup_pages),
        "followup_error": followup_error,
        "normalized_rows": normalized_rows,
        "pagination_complete": str(pagination_complete).lower(),
        "strict_close": str(bool(closeout["strict_close"])).lower(),
        "close_reason": closeout["close_reason"],
        "stg_rows_after": evidence_after.finance_stg_rows,
        "state_after": evidence_after.state_status,
        "final_live_tail_status": status,
    }


def run_days_batch(day_values: list[date]) -> list[dict[str, Any]]:
    if not day_values:
        return []
    apply_canonical_env("live_tail")
    ordered_days = sorted(day_values)
    range_pages, batch_error, pagination_complete = fetch_v2_daily_range_pages(ordered_days[0], ordered_days[-1])
    range_run_id = f"{datetime.now(MOSCOW).strftime('%Y%m%dT%H%M%S')}_range_{ordered_days[0].isoformat()}_{ordered_days[-1].isoformat()}"
    rows: list[dict[str, Any]] = []

    for day_value in ordered_days:
        day_text = day_value.isoformat()
        before = fetch_day_evidence(day_text)
        upsert_page_audit_rows(
            day_value=day_value,
            run_id=range_run_id,
            chunk_id=f"v2_range_{ordered_days[0].isoformat()}_{ordered_days[-1].isoformat()}",
            pages=range_pages,
        )
        reconciliation = persist_reconciliation(
            day_value=day_value,
            run_id=range_run_id,
            pages=range_pages,
            pagination_complete=pagination_complete,
        )
        normalized_rows = int(reconciliation["normalized_rows"])
        after = fetch_day_evidence(day_text)

        if normalized_rows > 0 and after.finance_stg_rows > 0:
            mark_provisional(
                day_value,
                rows_done=after.finance_stg_rows,
                terminal_reason="live_tail_v2_range_materialized",
                reason_code="wb_finance_canonical_live_tail_v2_range_partial",
                raw_presence=True,
                stg_presence=True,
                http_429_count=int(batch_error == "rate_limit"),
            )
        elif batch_error == "rate_limit":
            mark_provisional(
                day_value,
                rows_done=after.finance_stg_rows,
                terminal_reason="live_tail_v2_range_rate_limited",
                reason_code="wb_finance_canonical_live_tail_v2_range_partial",
                raw_presence=True,
                stg_presence=after.finance_stg_rows > 0,
                http_429_count=1,
            )
        elif before.finance_stg_rows == 0:
            mark_provisional(
                day_value,
                rows_done=after.finance_stg_rows,
                terminal_reason="live_tail_v2_range_not_proven",
                reason_code="wb_finance_canonical_live_tail_v2_range_partial",
                raw_presence=False,
                stg_presence=after.finance_stg_rows > 0,
                http_429_count=0,
            )

        if after.state_strict_usable:
            status = "strict_usable"
        elif after.finance_stg_rows > 0:
            status = "provisional_materialized"
        elif batch_error == "rate_limit":
            status = "rate_limit_not_proven"
        else:
            status = "not_proven"

        rows.append(
            {
                "day": day_text,
                "source_open": str(normalized_rows > 0).lower(),
                "source_probe_rows": normalized_rows,
                "source_probe_error": batch_error,
                "followup_pages": len(range_pages),
                "followup_error": batch_error,
                "normalized_rows": normalized_rows,
                "pagination_complete": str(bool(pagination_complete)).lower(),
                "strict_close": "false",
                "close_reason": "live_tail_v2_range_materialized" if normalized_rows > 0 else (batch_error or ""),
                "stg_rows_after": after.finance_stg_rows,
                "state_after": after.state_status,
                "final_live_tail_status": status,
            }
        )
    return rows


def build_report(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        [
            "# WB Finance Canonical Live-Tail Runner v1",
            "",
            f"Date: {now_iso()}",
            "",
            "## Canonical Mode",
            "",
            f"- mode: `{LIVE_TAIL.name}`",
            f"- lane: `{LIVE_TAIL.lane}`",
            f"- source_of_truth: `{', '.join(LIVE_TAIL.source_of_truth)}`",
            f"- phase_order: `{' -> '.join(LIVE_TAIL.phase_order)}`",
            "",
            "## Day Results",
            "",
            md_table(
                rows,
                [
                    "day",
                    "source_open",
                    "source_probe_rows",
                    "source_probe_error",
                    "followup_pages",
                    "followup_error",
                    "normalized_rows",
                    "pagination_complete",
                    "strict_close",
                    "close_reason",
                    "stg_rows_after",
                    "state_after",
                    "final_live_tail_status",
                ],
            ),
            "",
        ]
    ) + "\n"


def main() -> None:
    args = parse_args()
    day_values = [date.fromisoformat(item) for item in args.days]
    rows = run_days_batch(day_values)
    write_csv(RESULT_CSV, rows)
    DOC_PATH.write_text(build_report(rows), encoding="utf-8")
    print(f"Wrote {RESULT_CSV}")
    print(f"Wrote {DOC_PATH}")


if __name__ == "__main__":
    main()
