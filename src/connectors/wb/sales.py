from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any, Literal

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_SALES_URL = os.getenv(
    "WB_SALES_URL",
    "https://statistics-api.wildberries.ru/api/v1/supplier/sales",
)

PaginationMode = Literal["offset", "single"]


def _sales_success_exhausted_cooldown_sec() -> float:
    raw = (os.getenv("WB_SALES_SUCCESS_EXHAUSTED_COOLDOWN_SEC") or "").strip()
    if not raw:
        return 60.0
    try:
        parsed = float(raw)
    except ValueError:
        return 60.0
    return max(parsed, 0.0)


def _header_int(response_headers: Mapping[str, Any] | None, name: str) -> int | None:
    if not response_headers:
        return None
    value = response_headers.get(name) or response_headers.get(name.lower())
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


class WbSalesFetchError(RuntimeError):
    def __init__(self, message: str, *, diagnostic_pages: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.diagnostic_pages = diagnostic_pages or []


def _build_attempt_pages(
    attempts: list[dict[str, Any]],
    params: Mapping[str, Any],
    page_num: int,
    status_override: int | None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for attempt in attempts:
        parsed = attempt.get("parsed_response")
        if isinstance(parsed, list):
            response_payload: dict[str, Any] = {"sales": [row for row in parsed if isinstance(row, dict)]}
        elif isinstance(parsed, Mapping):
            response_payload = dict(parsed)
        else:
            response_payload = {"sales": []}
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
            }
        )
    return pages


def fetch_wb_sales_pages(
    date_from: str,
    limit: int = 100,
    max_pages: int | None = 100,
    offset: int = 0,
    pagination_mode: PaginationMode = "offset",
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = offset
    page_num = 0

    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            break

        page_num += 1
        params: dict[str, Any] = {"dateFrom": date_from}
        if pagination_mode == "offset":
            params["limit"] = limit
            params["offset"] = current_offset

        attempt_diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_SALES_URL,
                headers=headers,
                params=params,
                diagnostics_collector=attempt_diagnostics.append,
                retry_after_buffer_sec=5,
                rate_api_category="sales",
                rate_endpoint="/api/v1/supplier/sales",
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
            pages.extend(_build_attempt_pages(attempt_diagnostics, params, page_num, status_override=None))
            raise WbSalesFetchError(str(exc), diagnostic_pages=pages) from exc

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
                    "error_message": "WB sales response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(_build_attempt_pages(attempt_diagnostics, params, page_num, status_override=status))
            raise WbSalesFetchError("WB sales response must be a JSON list", diagnostic_pages=pages)

        rows = [item for item in raw if isinstance(item, dict)]
        pages.extend(_build_attempt_pages(attempt_diagnostics, params, page_num, status_override=status))
        logger.info("Fetched WB sales page=%s rows=%s", page_num, len(rows))

        response_headers = attempt_diagnostics[-1].get("response_headers") if attempt_diagnostics else {}
        remaining = _header_int(response_headers, "X-Ratelimit-Remaining")
        if remaining == 0:
            cooldown_sec = _sales_success_exhausted_cooldown_sec()
            if cooldown_sec > 0:
                time.sleep(cooldown_sec)

        if pagination_mode == "single":
            break
        if not rows:
            break

        # Live WB probes showed that this endpoint returns a cumulative slice from
        # dateFrom and effectively ignores limit/offset. Re-requesting with a new
        # offset only increases pressure and leads to 429 without giving a cleaner
        # next page. Keep one request per day and filter exact-day rows downstream.
        _ = current_offset
        break

    return pages
