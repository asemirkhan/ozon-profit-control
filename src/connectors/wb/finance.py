from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_FINANCE_URL = os.getenv(
    "WB_FINANCE_URL",
    "https://statistics-api.wildberries.ru/api/v5/supplier/reportDetailByPeriod",
)


class WbFinanceFetchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        diagnostic_pages: list[dict[str, Any]] | None = None,
        error_type: str | None = None,
        resume_rrdid: int = 0,
        pages_done: int = 0,
        rows_done: int = 0,
        terminal_reached: bool = False,
    ) -> None:
        super().__init__(message)
        self.diagnostic_pages = diagnostic_pages or []
        self.error_type = error_type
        self.resume_rrdid = max(int(resume_rrdid or 0), 0)
        self.pages_done = max(int(pages_done or 0), 0)
        self.rows_done = max(int(rows_done or 0), 0)
        self.terminal_reached = bool(terminal_reached)


@dataclass
class FinancePassResult:
    pages: list[dict[str, Any]]
    saw_data: bool
    terminal_reached: bool
    had_rate_limit: bool
    last_success_rrdid: int
    pages_done_total: int
    rows_done_total: int
    request_no_end: int
    pass_new_unique_rows: int
    cumulative_unique_rows: int


def _finance_http_max_attempts() -> int:
    raw = (os.getenv("WB_FINANCE_HTTP_MAX_ATTEMPTS") or "").strip()
    if not raw:
        return 5
    try:
        return max(int(raw), 1)
    except ValueError:
        return 5


def _finance_stable_http_max_attempts() -> int:
    raw = (os.getenv("WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS") or "").strip()
    if not raw:
        return 1
    try:
        return max(int(raw), 1)
    except ValueError:
        return 1


def _finance_retry_delays() -> list[int]:
    raw = (os.getenv("WB_FINANCE_HTTP_RETRY_DELAYS_SEC") or "").strip()
    if not raw:
        return [10, 30, 60, 120, 300]
    values: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            parsed = int(token)
        except ValueError:
            continue
        if parsed >= 0:
            values.append(parsed)
    return values or [10, 30, 60, 120, 300]


def _finance_stable_mode_enabled(date_from: str, date_to: str) -> bool:
    raw = (os.getenv("WB_FINANCE_STABLE_MODE") or "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return date_from == date_to


def _finance_stable_min_passes() -> int:
    raw = (os.getenv("WB_FINANCE_MIN_PASSES") or "").strip()
    if not raw:
        return 3
    try:
        return max(int(raw), 1)
    except ValueError:
        return 3


def _finance_stable_zero_growth_passes_required() -> int:
    raw = (os.getenv("WB_FINANCE_ZERO_GROWTH_PASSES_REQUIRED") or "").strip()
    if not raw:
        return 2
    try:
        return max(int(raw), 1)
    except ValueError:
        return 2


def _finance_stable_pass_pause_sec() -> int:
    raw = (os.getenv("WB_FINANCE_PASS_PAUSE_SEC") or "").strip()
    if not raw:
        return 120
    try:
        return max(int(raw), 0)
    except ValueError:
        return 120


def _finance_stable_inter_page_pause_sec() -> int:
    raw = (os.getenv("WB_FINANCE_INTER_PAGE_PAUSE_SEC") or "").strip()
    if not raw:
        return 80
    try:
        return max(int(raw), 0)
    except ValueError:
        return 80


def _finance_single_pass_inter_page_pause_sec() -> int:
    raw = (os.getenv("WB_FINANCE_SINGLE_PASS_INTER_PAGE_PAUSE_SEC") or "").strip()
    if not raw:
        return 0
    try:
        return max(int(raw), 0)
    except ValueError:
        return 0


def _finance_stable_rate_limit_pause_sec() -> int:
    raw = (os.getenv("WB_FINANCE_RATE_LIMIT_PASS_PAUSE_SEC") or "").strip()
    if not raw:
        return 150
    try:
        return max(int(raw), 0)
    except ValueError:
        return 150


def _finance_stable_max_passes() -> int:
    raw = (os.getenv("WB_FINANCE_STABLE_MAX_PASSES") or "").strip()
    if not raw:
        return 6
    try:
        return max(int(raw), 3)
    except ValueError:
        return 6


def _finance_stable_min_growth_ratio() -> float:
    raw = (os.getenv("WB_FINANCE_MIN_GROWTH_RATIO") or "").strip()
    if not raw:
        return 0.02
    try:
        parsed = float(raw)
    except ValueError:
        return 0.02
    if parsed < 0:
        return 0.02
    return parsed


def _sleep_if_needed(seconds: int) -> None:
    if seconds > 0:
        time.sleep(seconds)


def fetch_wb_finance_pages(
    date_from: str,
    date_to: str,
    limit: int = 100,
    max_pages: int | None = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if _finance_stable_mode_enabled(date_from, date_to):
        return _fetch_wb_finance_pages_stable_mode(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            max_pages=max_pages,
            offset=offset,
        )
    return _fetch_wb_finance_pages_single_pass(
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        max_pages=max_pages,
        offset=offset,
    )


def _fetch_wb_finance_pages_single_pass(
    *,
    date_from: str,
    date_to: str,
    limit: int,
    max_pages: int | None,
    offset: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = offset
    page_num = 0
    request_no = 0
    saw_data = False
    terminal_reached = False
    pages_done = 0
    rows_done = 0
    last_success_rrdid = max(int(offset or 0), 0)

    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            if saw_data:
                raise WbFinanceFetchError(
                    "finance_incomplete_pagination: max_pages reached before terminal page",
                    diagnostic_pages=pages,
                    error_type="finance_incomplete_pagination",
                    resume_rrdid=last_success_rrdid,
                    pages_done=pages_done,
                    rows_done=rows_done,
                    terminal_reached=False,
                )
            break

        page_num += 1
        request_no += 1
        params: dict[str, Any] = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": limit,
            "rrdid": current_offset,
        }
        attempt_diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_FINANCE_URL,
                headers=headers,
                params=params,
                diagnostics_collector=attempt_diagnostics.append,
                max_attempts_override=_finance_http_max_attempts(),
                retry_delays_override=_finance_retry_delays(),
                retry_after_buffer_sec=5,
                rate_api_category="finance",
                rate_endpoint="/api/v5/supplier/reportDetailByPeriod",
            )
        except Exception as exc:
            new_pages = _build_attempt_pages(
                attempt_diagnostics,
                params,
                page_num,
                request_no,
                status_override=None,
                pass_no=1,
            )
            pages.extend(new_pages)
            message = str(exc)
            error_type = _classify_finance_error_type(message, attempt_diagnostics)
            if saw_data and _is_finance_terminal_no_content_error(error_type):
                _mark_terminal_page(
                    new_pages=new_pages,
                    terminal_reason="http_no_content_after_data",
                    resume_rrdid=last_success_rrdid,
                    pages_done=pages_done,
                    rows_done=rows_done,
                )
                terminal_reached = True
                break
            raise WbFinanceFetchError(
                (
                    f"finance_incomplete_pagination: {message}; resume_rrdid={last_success_rrdid}"
                    if saw_data
                    else message
                ),
                diagnostic_pages=pages,
                error_type="finance_incomplete_pagination" if saw_data else error_type,
                resume_rrdid=last_success_rrdid,
                pages_done=pages_done,
                rows_done=rows_done,
                terminal_reached=False,
            ) from exc

        if not isinstance(raw, list):
            attempt_diagnostics.append(
                {
                    "attempt_no": len(attempt_diagnostics) + 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": status,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": True,
                    "parse_error": False,
                    "unexpected_shape": True,
                    "error_type": "unexpected_format",
                    "error_message": "WB finance response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(
                _build_attempt_pages(
                    attempt_diagnostics,
                    params,
                    page_num,
                    request_no,
                    status_override=status,
                    pass_no=1,
                )
            )
            raise WbFinanceFetchError(
                "WB finance response must be a JSON list",
                diagnostic_pages=pages,
                error_type="unexpected_format",
                resume_rrdid=last_success_rrdid,
                pages_done=pages_done,
                rows_done=rows_done,
                terminal_reached=False,
            )

        rows = [item for item in raw if isinstance(item, dict)]
        new_pages = _build_attempt_pages(
            attempt_diagnostics,
            params,
            page_num,
            request_no,
            status_override=status,
            pass_no=1,
        )
        pages.extend(new_pages)
        logger.info("Fetched WB finance page=%s rows=%s", page_num, len(rows))

        if not rows:
            if saw_data:
                _mark_terminal_page(
                    new_pages=new_pages,
                    terminal_reason="empty_json_after_data",
                    resume_rrdid=last_success_rrdid,
                    pages_done=pages_done,
                    rows_done=rows_done,
                )
                terminal_reached = True
            break

        saw_data = True
        pages_done += 1
        rows_done += len(rows)
        max_rrdid = _max_rrd_cursor(rows)
        if max_rrdid is not None:
            last_success_rrdid = max_rrdid
        _mark_progress_page(
            new_pages=new_pages,
            resume_rrdid=last_success_rrdid,
            pages_done=pages_done,
            rows_done=rows_done,
        )

        next_cursor = max_rrdid
        if next_cursor is None or next_cursor <= current_offset:
            current_offset += len(rows)
        else:
            current_offset = next_cursor

        if len(rows) < limit:
            # A short page still needs a confirming terminal request.
            logger.info(
                "WB finance short page=%s rows=%s last_rrdid=%s; requesting terminal confirmation",
                page_num,
                len(rows),
                last_success_rrdid,
            )
            _sleep_if_needed(_finance_single_pass_inter_page_pause_sec())
            continue

        _sleep_if_needed(_finance_single_pass_inter_page_pause_sec())

    if saw_data and not terminal_reached:
        raise WbFinanceFetchError(
            "finance_incomplete_pagination: page-loop finished without confirmed terminal state",
            diagnostic_pages=pages,
            error_type="finance_incomplete_pagination",
            resume_rrdid=last_success_rrdid,
            pages_done=pages_done,
            rows_done=rows_done,
            terminal_reached=False,
        )

    return pages


def _fetch_wb_finance_pages_stable_mode(
    *,
    date_from: str,
    date_to: str,
    limit: int,
    max_pages: int | None,
    offset: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    known_rrd_ids: set[int] = set()
    all_pages: list[dict[str, Any]] = []
    base_offset = max(int(offset or 0), 0)
    request_no = 0
    pages_done_total = 0
    rows_done_total = 0
    resume_rrdid = 0
    saw_any_data = False
    pass_no = 0
    no_growth_passes = 0
    min_passes = _finance_stable_min_passes()
    zero_growth_required = _finance_stable_zero_growth_passes_required()
    pass_pause_sec = _finance_stable_pass_pause_sec()
    min_growth_ratio = _finance_stable_min_growth_ratio()
    rate_limit_pause_sec = _finance_stable_rate_limit_pause_sec()
    max_passes = _finance_stable_max_passes()

    while True:
        pass_no += 1
        cumulative_before_pass = len(known_rrd_ids)
        pass_result = _run_finance_pass(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            max_pages=max_pages,
            offset=base_offset,
            request_no_start=request_no,
            pass_no=pass_no,
            known_rrd_ids=known_rrd_ids,
            pages_done_before=pages_done_total,
            rows_done_before=rows_done_total,
            inter_page_pause_sec=_finance_stable_inter_page_pause_sec(),
        )
        all_pages.extend(pass_result.pages)
        request_no = pass_result.request_no_end
        pages_done_total = pass_result.pages_done_total
        rows_done_total = pass_result.rows_done_total
        resume_rrdid = max(resume_rrdid, pass_result.last_success_rrdid)
        saw_any_data = saw_any_data or pass_result.saw_data

        if pass_result.had_rate_limit:
            no_growth_passes = 0
        elif pass_result.pass_new_unique_rows == 0:
            no_growth_passes += 1
        else:
            no_growth_passes = 0

        growth_ratio = _compute_pass_growth_ratio(
            pass_new_unique_rows=pass_result.pass_new_unique_rows,
            cumulative_before_pass=cumulative_before_pass,
        )
        logger.info(
            "WB finance stable pass=%s new_unique_rows=%s cumulative_unique_rows=%s growth_ratio=%.4f zero_growth_streak=%s",
            pass_no,
            pass_result.pass_new_unique_rows,
            pass_result.cumulative_unique_rows,
            growth_ratio,
            no_growth_passes,
        )

        if not saw_any_data and not pass_result.had_rate_limit:
            return all_pages

        if (
            pass_no >= min_passes
            and not pass_result.had_rate_limit
            and no_growth_passes >= zero_growth_required
        ):
            _mark_terminal_page(
                new_pages=all_pages[-1:],
                terminal_reason="confirmed_no_growth_after_multi_pass",
                resume_rrdid=resume_rrdid,
                pages_done=pages_done_total,
                rows_done=rows_done_total,
            )
            return all_pages

        if (
            pass_no >= min_passes
            and not pass_result.had_rate_limit
            and growth_ratio <= min_growth_ratio
        ):
            _mark_terminal_page(
                new_pages=all_pages[-1:],
                terminal_reason="confirmed_low_growth_after_multi_pass",
                resume_rrdid=resume_rrdid,
                pages_done=pages_done_total,
                rows_done=rows_done_total,
            )
            return all_pages

        if pass_no >= max_passes:
            raise WbFinanceFetchError(
                "finance_stable_mode_exhausted_max_passes_before_terminal_confirmation",
                diagnostic_pages=all_pages,
                error_type="finance_incomplete_pagination",
                resume_rrdid=resume_rrdid,
                pages_done=pages_done_total,
                rows_done=rows_done_total,
                terminal_reached=False,
            )

        if pass_result.had_rate_limit:
            _sleep_if_needed(rate_limit_pause_sec)
        else:
            _sleep_if_needed(pass_pause_sec)


def _run_finance_pass(
    *,
    date_from: str,
    date_to: str,
    limit: int,
    max_pages: int | None,
    offset: int,
    request_no_start: int,
    pass_no: int,
    known_rrd_ids: set[int],
    pages_done_before: int,
    rows_done_before: int,
    inter_page_pause_sec: int,
) -> FinancePassResult:
    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = max(int(offset or 0), 0)
    page_num = 0
    request_no = max(int(request_no_start or 0), 0)
    saw_data = False
    terminal_reached = False
    pages_done_total = max(int(pages_done_before or 0), 0)
    rows_done_total = max(int(rows_done_before or 0), 0)
    last_success_rrdid = current_offset
    pass_new_unique_rows = 0
    deferred_to_next_pass = False
    had_rate_limit = False

    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            if saw_data:
                raise WbFinanceFetchError(
                    "finance_incomplete_pagination: max_pages reached before terminal page",
                    diagnostic_pages=pages,
                    error_type="finance_incomplete_pagination",
                    resume_rrdid=last_success_rrdid,
                    pages_done=pages_done_total,
                    rows_done=rows_done_total,
                    terminal_reached=False,
                )
            break

        page_num += 1
        request_no += 1
        params: dict[str, Any] = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "limit": limit,
            "rrdid": current_offset,
        }
        attempt_diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_FINANCE_URL,
                headers=headers,
                params=params,
                diagnostics_collector=attempt_diagnostics.append,
                max_attempts_override=_finance_stable_http_max_attempts(),
                retry_delays_override=_finance_retry_delays(),
                retry_after_buffer_sec=5,
                rate_api_category="finance",
                rate_endpoint="/api/v5/supplier/reportDetailByPeriod",
            )
        except Exception as exc:
            new_pages = _build_attempt_pages(
                attempt_diagnostics,
                params,
                page_num,
                request_no,
                status_override=None,
                pass_no=pass_no,
            )
            pages.extend(new_pages)
            message = str(exc)
            error_type = _classify_finance_error_type(message, attempt_diagnostics)
            if _is_finance_terminal_no_content_error(error_type) and (saw_data or current_offset > 0):
                logger.info(
                    "WB finance pass=%s stopping after terminal-like empty/no-content response at rrdid=%s",
                    pass_no,
                    current_offset,
                )
                terminal_reached = True
                break
            if error_type == "rate_limit":
                had_rate_limit = True
                logger.warning(
                    "WB finance pass=%s hit rate_limit at rrdid=%s; ending pass and restarting from day start after cooldown",
                    pass_no,
                    current_offset,
                )
                deferred_to_next_pass = True
                break
            raise WbFinanceFetchError(
                (
                    f"finance_incomplete_pagination: {message}; resume_rrdid={last_success_rrdid}"
                    if saw_data
                    else message
                ),
                diagnostic_pages=pages,
                error_type="finance_incomplete_pagination" if saw_data else error_type,
                resume_rrdid=last_success_rrdid,
                pages_done=pages_done_total,
                rows_done=rows_done_total,
                terminal_reached=False,
            ) from exc

        if not isinstance(raw, list):
            attempt_diagnostics.append(
                {
                    "attempt_no": len(attempt_diagnostics) + 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": status,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": True,
                    "parse_error": False,
                    "unexpected_shape": True,
                    "error_type": "unexpected_format",
                    "error_message": "WB finance response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(
                _build_attempt_pages(
                    attempt_diagnostics,
                    params,
                    page_num,
                    request_no,
                    status_override=status,
                    pass_no=pass_no,
                )
            )
            raise WbFinanceFetchError(
                "WB finance response must be a JSON list",
                diagnostic_pages=pages,
                error_type="unexpected_format",
                resume_rrdid=last_success_rrdid,
                pages_done=pages_done_total,
                rows_done=rows_done_total,
                terminal_reached=False,
            )

        rows = [item for item in raw if isinstance(item, dict)]
        new_pages = _build_attempt_pages(
            attempt_diagnostics,
            params,
            page_num,
            request_no,
            status_override=status,
            pass_no=pass_no,
        )
        pages.extend(new_pages)
        logger.info("Fetched WB finance pass=%s page=%s rows=%s", pass_no, page_num, len(rows))

        if not rows:
            if saw_data:
                terminal_reached = True
            break

        saw_data = True
        pages_done_total += 1
        rows_done_total += len(rows)
        max_rrdid = _max_rrd_cursor(rows)
        if max_rrdid is not None:
            last_success_rrdid = max_rrdid
        _mark_progress_page(
            new_pages=new_pages,
            resume_rrdid=last_success_rrdid,
            pages_done=pages_done_total,
            rows_done=rows_done_total,
        )
        _mark_pass_metadata(
            new_pages=new_pages,
            pass_no=pass_no,
            known_rrd_ids=known_rrd_ids,
        )
        pass_new_unique_rows += _count_new_rrd_ids(rows=rows, known_rrd_ids=known_rrd_ids)

        next_cursor = max_rrdid
        if next_cursor is None or next_cursor <= current_offset:
            logger.warning(
                "WB finance pass=%s cursor stalled at page=%s current_rrdid=%s next_rrdid=%s; ending pass and restarting from day start on the next pass",
                pass_no,
                page_num,
                current_offset,
                next_cursor,
            )
            deferred_to_next_pass = True
            break
        current_offset = next_cursor

        if len(rows) < limit:
            logger.info(
                "WB finance pass=%s short page=%s rows=%s last_rrdid=%s; requesting terminal confirmation",
                pass_no,
                page_num,
                len(rows),
                last_success_rrdid,
            )
            continue

        _sleep_if_needed(inter_page_pause_sec)

    if saw_data and not terminal_reached and not deferred_to_next_pass:
        raise WbFinanceFetchError(
            "finance_incomplete_pagination: page-loop finished without confirmed terminal state",
            diagnostic_pages=pages,
            error_type="finance_incomplete_pagination",
            resume_rrdid=last_success_rrdid,
            pages_done=pages_done_total,
            rows_done=rows_done_total,
            terminal_reached=False,
        )

    _mark_pass_completion(
        pages=pages,
        pass_no=pass_no,
        pass_new_unique_rows=pass_new_unique_rows,
        cumulative_unique_rows=len(known_rrd_ids),
    )
    return FinancePassResult(
        pages=pages,
        saw_data=saw_data,
        terminal_reached=terminal_reached,
        had_rate_limit=had_rate_limit,
        last_success_rrdid=last_success_rrdid,
        pages_done_total=pages_done_total,
        rows_done_total=rows_done_total,
        request_no_end=request_no,
        pass_new_unique_rows=pass_new_unique_rows,
        cumulative_unique_rows=len(known_rrd_ids),
    )


def _build_attempt_pages(
    attempts: list[dict[str, Any]],
    params: Mapping[str, Any],
    page_num: int,
    request_no: int,
    status_override: int | None,
    pass_no: int,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for attempt in attempts:
        parsed = attempt.get("parsed_response")
        if isinstance(parsed, list):
            finance_rows = [row for row in parsed if isinstance(row, dict)]
            response_payload: dict[str, Any] = {"finance": finance_rows}
        elif isinstance(parsed, Mapping):
            response_payload = dict(parsed)
            finance_rows = _extract_finance_rows_from_payload(response_payload)
        else:
            finance_rows = []
            response_payload = {"finance": []}
        max_rrdid = _max_rrd_cursor(finance_rows)
        pages.append(
            {
                "response": response_payload,
                "request_params": dict(params),
                "http_status": attempt.get("http_status") if isinstance(attempt.get("http_status"), int) else status_override,
                "request_attempt_no": attempt.get("attempt_no"),
                "request_started_at": attempt.get("request_started_at"),
                "request_finished_at": attempt.get("response_finished_at"),
                "response_body_size": attempt.get("response_body_size"),
                "response_content_type": attempt.get("response_content_type"),
                "response_headers": attempt.get("response_headers"),
                "raw_body": attempt.get("raw_body"),
                "empty_body": bool(attempt.get("empty_body", False)),
                "parse_error": bool(attempt.get("parse_error", False)),
                "unexpected_shape": bool(attempt.get("unexpected_shape", False)),
                "error_type": attempt.get("error_type"),
                "error_message": attempt.get("error_message"),
                "request_success": bool(attempt.get("success", False)),
                "request_page_no": page_num,
                "finance_pass_no": pass_no,
                "finance_request_no": request_no,
                "finance_rrdid_request": params.get("rrdid"),
                "finance_rows_in_response": len(finance_rows),
                "finance_max_rrdid_in_response": max_rrdid,
                "finance_is_terminal_page": False,
                "finance_terminal_reason": None,
                "finance_resume_rrdid": None,
                "finance_pages_done": None,
                "finance_rows_done": None,
                "finance_pass_new_unique_rows": None,
                "finance_cumulative_unique_rows": None,
                "rate_limit_retry": attempt.get("rate_limit_retry"),
                "rate_limit_limit": attempt.get("rate_limit_limit"),
                "rate_limit_reset": attempt.get("rate_limit_reset"),
                "request_url": attempt.get("request_url"),
                "request_endpoint": attempt.get("request_endpoint"),
                "cooldown_wait_sec": attempt.get("cooldown_wait_sec"),
            }
        )
    return pages


def _mark_progress_page(
    *,
    new_pages: list[dict[str, Any]],
    resume_rrdid: int,
    pages_done: int,
    rows_done: int,
) -> None:
    for page in new_pages:
        page["finance_resume_rrdid"] = max(int(resume_rrdid or 0), 0)
        page["finance_pages_done"] = max(int(pages_done or 0), 0)
        page["finance_rows_done"] = max(int(rows_done or 0), 0)


def _mark_terminal_page(
    *,
    new_pages: list[dict[str, Any]],
    terminal_reason: str,
    resume_rrdid: int,
    pages_done: int,
    rows_done: int,
) -> None:
    if not new_pages:
        return
    target = new_pages[-1]
    target["finance_is_terminal_page"] = True
    target["finance_terminal_reason"] = terminal_reason
    target["finance_resume_rrdid"] = max(int(resume_rrdid or 0), 0)
    target["finance_pages_done"] = max(int(pages_done or 0), 0)
    target["finance_rows_done"] = max(int(rows_done or 0), 0)


def _count_new_rrd_ids(*, rows: list[dict[str, Any]], known_rrd_ids: set[int]) -> int:
    new_ids: set[int] = set()
    for row in rows:
        parsed = _extract_rrd_cursor(row)
        if parsed is None or parsed in known_rrd_ids:
            continue
        new_ids.add(parsed)
    if new_ids:
        known_rrd_ids.update(new_ids)
    return len(new_ids)


def _mark_pass_metadata(
    *,
    new_pages: list[dict[str, Any]],
    pass_no: int,
    known_rrd_ids: set[int],
) -> None:
    for page in new_pages:
        page["finance_pass_no"] = max(int(pass_no or 0), 0)
        page["finance_cumulative_unique_rows"] = max(int(len(known_rrd_ids)), 0)


def _mark_pass_completion(
    *,
    pages: list[dict[str, Any]],
    pass_no: int,
    pass_new_unique_rows: int,
    cumulative_unique_rows: int,
) -> None:
    for page in pages:
        if int(page.get("finance_pass_no") or 0) != int(pass_no):
            continue
        page["finance_pass_new_unique_rows"] = max(int(pass_new_unique_rows or 0), 0)
        page["finance_cumulative_unique_rows"] = max(int(cumulative_unique_rows or 0), 0)


def _compute_pass_growth_ratio(
    *,
    pass_new_unique_rows: int,
    cumulative_before_pass: int,
) -> float:
    new_rows = max(int(pass_new_unique_rows or 0), 0)
    base_rows = max(int(cumulative_before_pass or 0), 0)
    if new_rows <= 0:
        return 0.0
    if base_rows <= 0:
        return 1.0
    return new_rows / base_rows


def _extract_finance_rows_from_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("finance")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _max_rrd_cursor(rows: list[dict[str, Any]]) -> int | None:
    values = [_extract_rrd_cursor(row) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values)


def _is_finance_terminal_no_content_error(error_type: str | None) -> bool:
    return error_type in {
        "empty_response",
        "http_no_content",
        "http_no_content_after_429",
        "http_success_empty_body",
        "http_success_empty_body_after_429",
    }


def _extract_rrd_cursor(row: Mapping[str, Any]) -> int | None:
    value = row.get("rrd_id") or row.get("rrdId") or row.get("id")
    if value is None:
        return None
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _classify_finance_error_type(message: str, attempts: list[dict[str, Any]] | None = None) -> str:
    if attempts:
        typed = [str(item.get("error_type")) for item in attempts if item.get("error_type")]
        if typed:
            return typed[-1]
    text = message.lower()
    if "http_no_content_after_429" in text:
        return "http_no_content_after_429"
    if "http_no_content" in text:
        return "http_no_content"
    if "http_success_empty_body_after_429" in text:
        return "http_success_empty_body_after_429"
    if "http_success_empty_body" in text:
        return "http_success_empty_body"
    if "http_success_truncated_body" in text:
        return "http_success_truncated_body"
    if "http_success_non_json" in text:
        return "http_success_non_json"
    if "network_before_body" in text:
        return "network_before_body"
    if "empty response body" in text or "empty body" in text:
        return "empty_response"
    if "non-json" in text or "json" in text:
        return "parse_error"
    if "shape" in text or "must be" in text:
        return "unexpected_format"
    if "429" in text or "too many requests" in text:
        return "rate_limit"
    return "unknown"
