from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

import scripts.build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1 as finance_strict
import scripts.wb_data_completion_cleanup_and_granular_prep_v1 as cleanup_granular
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status
from src.ingestion.runner import _finance_reconciliation_day_upsert
from src.loaders.upsert_wb import upsert_stg_wb_finance
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.raw_store.writer import write_raw_json
from src.services.wb_finance_canonical_method_v1 import apply_canonical_env
from src.utils.db import get_conn
from src.utils.rate_limit_coordinator import get_rate_limit_coordinator

OUTPUT_DIR = ROOT / "exports" / "wb_finance_canonical_bulk_runner_v1"
SUMMARY_CSV = OUTPUT_DIR / "finance_bulk_period_result.csv"
DAY_CSV = OUTPUT_DIR / "finance_bulk_day_result.csv"
DOC_PATH = ROOT / "artifacts" / "wb_finance_canonical_bulk_runner_v1.md"
FINANCE_V2_DAILY_URL = os.getenv(
    "WB_FINANCE_V2_DAILY_URL",
    "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed",
)
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo"
finance_strict.SCHEMA = SCHEMA
cleanup_granular.SCHEMA = SCHEMA
upsert_finance_day_state = cleanup_granular.upsert_finance_day_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical WB finance bulk runner.")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--days", nargs="*")
    parser.add_argument("--schema", default=SCHEMA)
    parser.add_argument("--limit", type=int, default=int(os.getenv("WB_FINANCE_BULK_LIMIT", "100000")))
    return parser.parse_args()


def _resolve_period(args: argparse.Namespace) -> tuple[date, date]:
    if args.days:
        parsed = sorted(date.fromisoformat(item) for item in args.days)
        return parsed[0], parsed[-1]
    if args.date_from and args.date_to:
        return date.fromisoformat(args.date_from), date.fromisoformat(args.date_to)
    raise ValueError("need --days or --from/--to")


def _write_raw_pages(schema: str, run_id: str, pages: list[dict[str, Any]]) -> None:
    for idx, page in enumerate(pages, start=1):
        payload = page.get("response")
        if isinstance(payload, list):
            payload = {"finance": payload}
        elif not isinstance(payload, dict):
            payload = {"finance": []}
        write_raw_json(
            schema=schema,
            source="wb_finance",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=page.get("request_params") if isinstance(page.get("request_params"), dict) else None,
            http_status=page.get("http_status") if isinstance(page.get("http_status"), int) else None,
            meta_extra={
                "request_page_no": page.get("request_page_no"),
                "request_attempt_no": page.get("request_attempt_no"),
                "request_started_at": page.get("request_started_at"),
                "request_finished_at": page.get("request_finished_at"),
                "request_url": page.get("request_url"),
                "request_endpoint": page.get("request_endpoint"),
                "response_body_size": page.get("response_body_size"),
                "finance_rows_in_response": page.get("finance_rows_in_response"),
                "finance_rrdid_request": page.get("finance_rrdid_request"),
                "finance_max_rrdid_in_response": page.get("finance_max_rrdid_in_response"),
                "finance_is_terminal_page": page.get("finance_is_terminal_page"),
                "finance_terminal_reason": page.get("finance_terminal_reason"),
                "rate_limit_retry": page.get("rate_limit_retry"),
                "rate_limit_reset": page.get("rate_limit_reset"),
                "rate_limit_limit": page.get("rate_limit_limit"),
            },
            file_stem=f"{run_id}_p{idx:03d}",
        )


def _build_v2_page(
    *,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
    status: int | None,
    page_num: int,
    request_no: int,
    request_url: str,
    request_endpoint: str,
    request_attempt_no: int = 1,
    request_started_at: str | None = None,
    request_finished_at: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    request_success: bool = True,
    rate_limit_retry: Any = None,
    rate_limit_limit: Any = None,
    rate_limit_reset: Any = None,
) -> dict[str, Any]:
    max_rrdid = max(
        (
            int(row.get("rrdId") or row.get("rrd_id") or row.get("id"))
            for row in rows
            if isinstance(row, dict) and (row.get("rrdId") or row.get("rrd_id") or row.get("id")) is not None
        ),
        default=None,
    )
    return {
        "response": rows,
        "request_params": dict(payload),
        "http_status": status,
        "request_attempt_no": request_attempt_no,
        "request_started_at": request_started_at,
        "request_finished_at": request_finished_at,
        "request_success": request_success,
        "request_page_no": page_num,
        "finance_request_no": request_no,
        "finance_rrdid_request": payload.get("rrdId"),
        "finance_rows_in_response": len(rows),
        "finance_max_rrdid_in_response": max_rrdid,
        "finance_is_terminal_page": False,
        "finance_terminal_reason": None,
        "error_type": error_type,
        "error_message": error_message,
        "rate_limit_retry": rate_limit_retry,
        "rate_limit_limit": rate_limit_limit,
        "rate_limit_reset": rate_limit_reset,
        "request_url": request_url,
        "request_endpoint": request_endpoint,
    }


def _mark_terminal_page(page: dict[str, Any], reason: str) -> None:
    page["finance_is_terminal_page"] = True
    page["finance_terminal_reason"] = reason


def _is_terminal_no_content(error_type: str | None) -> bool:
    return error_type in {
        "empty_response",
        "http_no_content",
        "http_no_content_after_429",
        "http_success_empty_body",
        "http_success_empty_body_after_429",
    }


def fetch_wb_finance_bulk_v2_pages(
    *,
    date_from: str,
    date_to: str,
    limit: int,
    max_pages: int,
) -> tuple[list[dict[str, Any]], str, bool]:
    pages: list[dict[str, Any]] = []
    current_rrdid = 0
    page_num = 0
    request_no = 0
    saw_data = False
    request_url = FINANCE_V2_DAILY_URL
    request_endpoint = "/api/finance/v1/sales-reports/detailed"
    inter_page_pause_sec = int(os.getenv("WB_FINANCE_SINGLE_PASS_INTER_PAGE_PAUSE_SEC", "25"))

    while page_num < max_pages:
        page_num += 1
        request_no += 1
        payload = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": limit,
            "rrdId": current_rrdid,
            "period": "daily",
        }
        diagnostics: list[dict[str, Any]] = []
        try:
            parsed, status = request_json_with_status(
                method="POST",
                url=request_url,
                headers=get_wb_headers(),
                payload=payload,
                timeout=90,
                diagnostics_collector=diagnostics.append,
                max_attempts_override=1,
                retry_delays_override=[],
                retry_after_buffer_sec=0,
                rate_api_category="finance",
                rate_endpoint=request_endpoint,
            )
        except Exception as exc:
            last = diagnostics[-1] if diagnostics else {}
            error_type = str(last.get("error_type") or "fetch_error")
            error_page = _build_v2_page(
                payload=payload,
                rows=[],
                status=last.get("http_status") if isinstance(last.get("http_status"), int) else None,
                page_num=page_num,
                request_no=request_no,
                request_url=str(last.get("request_url") or request_url),
                request_endpoint=str(last.get("request_endpoint") or request_endpoint),
                request_attempt_no=int(last.get("attempt_no") or 1),
                request_started_at=last.get("request_started_at"),
                request_finished_at=last.get("response_finished_at"),
                error_type=error_type,
                error_message=str(last.get("error_message") or exc),
                request_success=False,
                rate_limit_retry=last.get("rate_limit_retry"),
                rate_limit_limit=last.get("rate_limit_limit"),
                rate_limit_reset=last.get("rate_limit_reset"),
            )
            if _is_terminal_no_content(error_type):
                _mark_terminal_page(error_page, "http_no_content")
                pages.append(error_page)
                return pages, "", True
            pages.append(error_page)
            return pages, error_type, False

        response_rows = parsed if isinstance(parsed, list) else []
        page = _build_v2_page(
            payload=payload,
            rows=[row for row in response_rows if isinstance(row, dict)],
            status=status,
            page_num=page_num,
            request_no=request_no,
            request_url=request_url,
            request_endpoint=request_endpoint,
        )
        pages.append(page)
        rows = [row for row in response_rows if isinstance(row, dict)]
        if not rows:
            _mark_terminal_page(page, "empty_list_terminal")
            return pages, "", True

        saw_data = True
        max_rrdid = page.get("finance_max_rrdid_in_response")
        if len(rows) < limit:
            if max_rrdid is None or int(max_rrdid) <= int(current_rrdid):
                _mark_terminal_page(page, "short_page_terminal_no_cursor_advance")
                return pages, "", True
            current_rrdid = int(max_rrdid)
            if inter_page_pause_sec > 0:
                time.sleep(inter_page_pause_sec)
            continue

        if max_rrdid is None or int(max_rrdid) <= int(current_rrdid):
            return pages, "cursor_stall", False
        current_rrdid = int(max_rrdid)
        if inter_page_pause_sec > 0:
            time.sleep(inter_page_pause_sec)

    return pages, "max_pages_reached" if saw_data else "no_data_before_max_pages", False


def _stg_day_amount(schema: str, day_value: date) -> Decimal:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COALESCE(SUM(amount), 0)::numeric(18,2)
                FROM {schema}.stg_wb_finance
                WHERE operation_date::date = %s
                """,
                (day_value,),
            )
            return Decimal(str(cur.fetchone()[0] or 0))


def _sum_decimal(rows: list[dict[str, Any]], field: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        value = row.get(field)
        if isinstance(value, Decimal):
            total += value
        elif value not in (None, ""):
            total += Decimal(str(value))
    return total


def _group_by_day(rows: list[dict[str, Any]]) -> dict[date, list[dict[str, Any]]]:
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        op_dt = row.get("operation_date")
        day_value: date | None = None
        if hasattr(op_dt, "date"):
            day_value = op_dt.date()
        elif isinstance(op_dt, date):
            day_value = op_dt
        if day_value is None:
            continue
        grouped[day_value].append(row)
    return grouped


def _update_day_state(schema: str, run_id: str, day_value: date, rows: list[dict[str, Any]], pagination_complete: bool) -> dict[str, Any]:
    row_count = len(rows)
    amount_sum = _sum_decimal(rows, "amount")
    commission_sum = _sum_decimal(rows, "commission_amount")
    logistics_sum = _sum_decimal(rows, "logistics_amount")
    service_sum = _sum_decimal(rows, "service_amount")
    stg_amount_sum = _stg_day_amount(schema, day_value)

    _finance_reconciliation_day_upsert(
        schema=schema,
        target_date=day_value,
        run_id=run_id,
        raw_payload_files=1,
        raw_rows_total=row_count,
        normalized_rows_total=row_count,
        stg_rows_total=row_count,
        raw_amount_sum=amount_sum,
        normalized_amount_sum=amount_sum,
        stg_amount_sum=stg_amount_sum,
        raw_commission_sum=commission_sum,
        stg_commission_sum=commission_sum,
        raw_logistics_sum=logistics_sum,
        stg_logistics_sum=logistics_sum,
        raw_services_sum=service_sum,
        stg_services_sum=service_sum,
        pagination_complete=pagination_complete,
    )

    upsert_finance_day_state(
        day_value=day_value,
        status="success",
        complete=True,
        analytic_usable=True,
        raw_presence=True,
        stg_presence=True,
        rows_done=row_count,
        http_429_count=0,
        terminal_reason_code="bulk_period_terminal_204" if pagination_complete else "bulk_period_loaded_without_terminal",
        reason_code="finance_bulk_period_v1",
    )
    max_rrd = max((int(row["rrd_id"]) for row in rows if row.get("rrd_id") is not None), default=0)
    return {
        "day": day_value.isoformat(),
        "rows_loaded": row_count,
        "amount_sum": f"{amount_sum:.2f}",
        "max_rrd_id": max_rrd,
        "state_status": "success",
        "analytic_usable": "yes",
    }


def _mark_empty_day(day_value: date) -> dict[str, Any]:
    upsert_finance_day_state(
        day_value=day_value,
        status="started_but_not_finished",
        complete=False,
        analytic_usable=False,
        raw_presence=False,
        stg_presence=False,
        rows_done=0,
        http_429_count=0,
        terminal_reason_code="bulk_period_no_rows_204",
        reason_code="finance_bulk_period_v1_no_rows",
    )
    return {
        "day": day_value.isoformat(),
        "rows_loaded": 0,
        "amount_sum": "0.00",
        "max_rrd_id": 0,
        "state_status": "started_but_not_finished",
        "analytic_usable": "no",
    }


def _mark_partial_day(day_value: date, rows: list[dict[str, Any]], error_type: str) -> dict[str, Any]:
    """Record useful rows without ever presenting an interrupted pass as closed."""
    row_count = len(rows)
    upsert_finance_day_state(
        day_value=day_value,
        status="started_but_not_finished",
        complete=False,
        analytic_usable=False,
        raw_presence=bool(rows),
        stg_presence=bool(rows),
        rows_done=row_count,
        http_429_count=1 if error_type == "rate_limit" else 0,
        terminal_reason_code="bulk_period_interrupted",
        reason_code=error_type or "bulk_period_incomplete",
    )
    return {
        "day": day_value.isoformat(),
        "rows_loaded": row_count,
        "amount_sum": f"{_sum_decimal(rows, 'amount'):.2f}",
        "max_rrd_id": max((int(row["rrd_id"]) for row in rows if row.get("rrd_id") is not None), default=0),
        "state_status": "started_but_not_finished",
        "analytic_usable": "no",
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    load_dotenv(ROOT / ".env")
    apply_canonical_env("closed_period")
    os.environ["WB_FINANCE_STABLE_MODE"] = "0"
    os.environ["WB_FINANCE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS"] = "1"
    os.environ["WB_FINANCE_HTTP_RETRY_DELAYS_SEC"] = "0"
    os.environ.setdefault("WB_FINANCE_SINGLE_PASS_INTER_PAGE_PAUSE_SEC", "25")

    args = parse_args()
    start, end = _resolve_period(args)
    schema = args.schema
    limit = int(args.limit)

    get_rate_limit_coordinator()._states.clear()  # type: ignore[attr-defined]
    started = time.time()
    pages, error_type, pagination_complete = fetch_wb_finance_bulk_v2_pages(
        date_from=start.isoformat(),
        date_to=end.isoformat(),
        limit=limit,
        max_pages=1000,
    )

    run_id = f"wb_finance_bulk_{start.isoformat()}_{end.isoformat()}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    _write_raw_pages(schema, run_id, pages)

    normalization = build_wb_finance_normalization_result(pages)
    rows = normalization["rows"]
    loaded_rows = upsert_stg_wb_finance(schema, rows) if rows else 0
    grouped = _group_by_day(rows)
    pagination_complete = pagination_complete or any(bool(page.get("finance_is_terminal_page")) for page in pages) or any(int(page.get("http_status") or 0) == 204 for page in pages)

    day_rows: list[dict[str, Any]] = []
    covered_days = set(grouped)
    pass_complete = not error_type and pagination_complete
    for day_value in sorted(grouped):
        if pass_complete:
            day_rows.append(_update_day_state(schema, run_id, day_value, grouped[day_value], True))
        else:
            day_rows.append(_mark_partial_day(day_value, grouped[day_value], error_type))
    if pass_complete:
        current = start
        while current <= end:
            if current not in covered_days:
                day_rows.append(_mark_empty_day(current))
            current = current.fromordinal(current.toordinal() + 1)
    day_rows.sort(key=lambda row: row["day"])

    request_count = len(pages)
    elapsed_sec = round(time.time() - started, 2)
    summary_rows = [
        {
            "run_id": run_id,
            "schema": schema,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "limit": limit,
            "request_count": request_count,
            "raw_rows_total": normalization["raw_rows_total"],
            "normalized_rows_total": len(rows),
            "loaded_rows_total": loaded_rows,
            "days_loaded": len(grouped),
            "got_204_yes_no": "yes" if any(int(page.get("http_status") or 0) == 204 for page in pages) else "no",
            "status_429_count": sum(1 for page in pages if int(page.get("http_status") or 0) == 429),
            "error_type": error_type,
            "elapsed_sec": elapsed_sec,
        }
    ]

    _write_csv(SUMMARY_CSV, summary_rows)
    _write_csv(DAY_CSV, day_rows)

    doc = "\n".join(
        [
            "# WB Finance Canonical Bulk Runner v1",
            "",
            f"- run_id: `{run_id}`",
            f"- period: `{start.isoformat()} .. {end.isoformat()}`",
            f"- limit: `{limit}`",
            f"- request_count: `{request_count}`",
            f"- raw_rows_total: `{normalization['raw_rows_total']}`",
            f"- normalized_rows_total: `{len(rows)}`",
            f"- loaded_rows_total: `{loaded_rows}`",
            f"- days_loaded: `{len(grouped)}`",
            f"- got_204_yes_no: `{'yes' if any(int(page.get('http_status') or 0) == 204 for page in pages) else 'no'}`",
            f"- error_type: `{error_type or '-'}`",
            f"- elapsed_sec: `{elapsed_sec}`",
        ]
    )
    DOC_PATH.write_text(doc + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "run_id": run_id,
                "request_count": request_count,
                "rows_loaded": loaded_rows,
                "days_loaded": len(grouped),
                "elapsed_sec": elapsed_sec,
                "got_204": any(int(page.get("http_status") or 0) == 204 for page in pages),
            },
            ensure_ascii=False,
        )
    )
    if not pass_complete:
        print(
            f"finance_bulk_incomplete error_type={error_type or 'terminal_not_reached'} "
            f"days_loaded={len(grouped)}"
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
