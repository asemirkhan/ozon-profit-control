from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any, Literal

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_ORDERS_URL = os.getenv(
    "WB_ORDERS_URL",
    "https://statistics-api.wildberries.ru/api/v1/supplier/orders",
)
WB_ORDERS_CURSOR_RESPONSE_CAP = 80_000

PaginationMode = Literal["offset", "single", "cursor"]


class WbOrdersFetchError(RuntimeError):
    def __init__(self, message: str, *, diagnostic_pages: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.diagnostic_pages = diagnostic_pages or []


def _build_attempt_pages(
    attempts: list[dict[str, Any]],
    params: Mapping[str, Any],
    page_num: int,
    status_override: int | None,
    *,
    page_cap_reached: bool = False,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for attempt in attempts:
        parsed = attempt.get("parsed_response")
        if isinstance(parsed, list):
            response_payload: dict[str, Any] = {"orders": [row for row in parsed if isinstance(row, dict)]}
        elif isinstance(parsed, Mapping):
            response_payload = dict(parsed)
        else:
            response_payload = {"orders": []}
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
                "raw_body": attempt.get("raw_body"),
                "empty_body": bool(attempt.get("empty_body", False)),
                "parse_error": bool(attempt.get("parse_error", False)),
                "unexpected_shape": bool(attempt.get("unexpected_shape", False)),
                "error_type": attempt.get("error_type"),
                "error_message": attempt.get("error_message"),
                "request_success": bool(attempt.get("success", False)),
                "request_page_no": page_num,
                "rate_limit_retry": attempt.get("rate_limit_retry"),
                "rate_limit_limit": attempt.get("rate_limit_limit"),
                "rate_limit_reset": attempt.get("rate_limit_reset"),
                "request_url": attempt.get("request_url"),
                "request_endpoint": attempt.get("request_endpoint"),
                "cooldown_wait_sec": attempt.get("cooldown_wait_sec"),
                "page_cap_reached": page_cap_reached,
            }
        )
    return pages


def _orders_page_sleep_sec() -> float:
    raw = (os.getenv("WB_ORDERS_PAGE_SLEEP_SEC") or "").strip()
    if not raw:
        return 3.0
    try:
        parsed = float(raw)
    except ValueError:
        return 3.0
    return max(parsed, 0.0)


def fetch_wb_orders_pages(
    date_from: str,
    limit: int = 100,
    max_pages: int | None = 100,
    offset: int = 0,
    pagination_mode: PaginationMode = "cursor",
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    effective_limit = min(limit, 100)

    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = offset
    current_cursor = date_from
    page_num = 0

    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            break

        page_num += 1
        params: dict[str, Any] = {"dateFrom": current_cursor}
        if pagination_mode == "offset":
            params["limit"] = effective_limit
            params["offset"] = current_offset
        else:
            # Official WB orders docs use a lastChangeDate cursor with flag=0.
            params["flag"] = 0

        attempt_diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_ORDERS_URL,
                headers=headers,
                params=params,
                diagnostics_collector=attempt_diagnostics.append,
                retry_after_buffer_sec=5,
                rate_api_category="orders",
                rate_endpoint="/api/v1/supplier/orders",
            )
        except Exception as exc:
            if not attempt_diagnostics:
                attempt_diagnostics.append(
                    {
                        "attempt_no": 1,
                        "request_started_at": None,
                        "response_finished_at": None,
                        "http_status": None,
                        "response_body_size": 0,
                        "response_content_type": None,
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "unknown",
                        "error_message": str(exc),
                        "success": False,
                        "parsed_response": None,
                    }
                )
            pages.extend(
                _build_attempt_pages(
                    attempt_diagnostics,
                    params,
                    page_num,
                    status_override=None,
                    page_cap_reached=False,
                )
            )
            raise WbOrdersFetchError(str(exc), diagnostic_pages=pages) from exc

        if not attempt_diagnostics:
            attempt_diagnostics.append(
                {
                    "attempt_no": 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": status,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": False,
                    "parse_error": False,
                    "unexpected_shape": False,
                    "error_type": None,
                    "error_message": None,
                    "success": True,
                    "parsed_response": raw,
                }
            )

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
                    "error_message": "WB orders response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(
                _build_attempt_pages(
                    attempt_diagnostics,
                    params,
                    page_num,
                    status_override=status,
                    page_cap_reached=False,
                )
            )
            raise WbOrdersFetchError("WB orders response must be a JSON list", diagnostic_pages=pages)

        rows = [item for item in raw if isinstance(item, dict)]
        if pagination_mode == "offset":
            reached_page_cap = (
                max_pages is not None
                and page_num >= max_pages
                and len(rows) >= effective_limit
            )
        elif pagination_mode == "cursor":
            # WB cursor mode is bounded by the documented ~80k-row response cap,
            # not by our legacy offset page size.
            reached_page_cap = (
                max_pages is not None
                and page_num >= max_pages
                and len(rows) >= WB_ORDERS_CURSOR_RESPONSE_CAP
            )
        else:
            reached_page_cap = False
        pages.extend(
            _build_attempt_pages(
                attempt_diagnostics,
                params,
                page_num,
                status_override=status,
                page_cap_reached=reached_page_cap,
            )
            )
        logger.info("Fetched WB orders page=%s rows=%s", page_num, len(rows))

        if pagination_mode == "single":
            break
        if pagination_mode == "offset":
            if not rows or len(rows) < effective_limit:
                break
            current_offset += len(rows)
        else:
            if not rows:
                break
            # In canonical cursor mode WB often returns the whole available
            # result set in the first response unless the documented ~80k cap
            # is hit. When the page is well below that cap, a follow-up
            # request is usually redundant and only increases 429 risk.
            if len(rows) < WB_ORDERS_CURSOR_RESPONSE_CAP:
                break
            last_change_date = _extract_last_change_date(rows)
            if not last_change_date:
                logger.warning(
                    "WB orders cursor pagination stopped because lastChangeDate is missing on page=%s",
                    page_num,
                )
                break
            if last_change_date == current_cursor:
                logger.warning(
                    "WB orders cursor pagination stopped because lastChangeDate did not advance on page=%s value=%s",
                    page_num,
                    last_change_date,
                )
                break
            current_cursor = last_change_date

        pause = _orders_page_sleep_sec()
        if pause > 0:
            time.sleep(pause)

    return pages


def _extract_last_change_date(rows: list[dict[str, Any]]) -> str | None:
    for row in reversed(rows):
        value = row.get("lastChangeDate")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
