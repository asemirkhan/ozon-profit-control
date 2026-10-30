from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import get_ozon_headers
from src.connectors.ozon.http import post_json_with_status

logger = logging.getLogger(__name__)

TRANSACTIONS_LIST_URL = "https://api-seller.ozon.ru/v3/finance/transaction/list"
PaginationMode = Literal["page", "offset", "cursor"]
MAX_PERIOD_DAYS = 31


def _extract_operations(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result")
    if isinstance(result, Mapping):
        for key in ("operations", "transactions", "items"):
            rows = result.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    for key in ("operations", "transactions"):
        rows = response.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def fetch_transactions_pages(
    date_from: str,
    date_to: str,
    limit: int = 100,
    max_pages: int = 1000,
    pagination_mode: PaginationMode = "page",
    page: int = 1,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")
    if page <= 0:
        raise ValueError("page must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    headers = get_ozon_headers()
    pages: list[dict[str, Any]] = []

    date_ranges = _chunk_date_ranges(date_from, date_to, MAX_PERIOD_DAYS)
    logger.info(
        "Transactions date chunking: source=%s..%s chunks=%s max_days=%s",
        date_from,
        date_to,
        len(date_ranges),
        MAX_PERIOD_DAYS,
    )

    remaining_pages = max_pages
    for chunk_index, (chunk_from, chunk_to) in enumerate(date_ranges, start=1):
        if remaining_pages <= 0:
            logger.warning("Reached max_pages=%s before completing all transaction chunks", max_pages)
            break
        chunk_pages = _fetch_transactions_pages_single_range(
            headers=headers,
            date_from=chunk_from,
            date_to=chunk_to,
            limit=limit,
            max_pages=remaining_pages,
            pagination_mode=pagination_mode,
            page=page,
            offset=offset,
        )
        pages.extend(chunk_pages)
        remaining_pages -= len(chunk_pages)
        logger.info(
            "Transactions chunk %s/%s fetched pages=%s range=%s..%s",
            chunk_index,
            len(date_ranges),
            len(chunk_pages),
            chunk_from,
            chunk_to,
        )

    return pages


def _fetch_transactions_pages_single_range(
    headers: Mapping[str, str],
    date_from: str,
    date_to: str,
    limit: int,
    max_pages: int,
    pagination_mode: PaginationMode,
    page: int,
    offset: int,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    current_page = page
    current_offset = offset
    cursor = ""

    page_num = 0
    while page_num < max_pages:
        page_num += 1
        payload: dict[str, Any] = {
            "filter": {
                "date": {
                    "from": f"{date_from}T00:00:00.000Z",
                    "to": f"{date_to}T23:59:59.999Z",
                }
            }
        }

        if pagination_mode == "page":
            payload["page"] = current_page
            payload["page_size"] = limit
        elif pagination_mode == "offset":
            payload["offset"] = current_offset
            payload["limit"] = limit
        else:
            payload["last_id"] = cursor
            payload["limit"] = limit

        response, http_status = post_json_with_status(TRANSACTIONS_LIST_URL, payload, headers)
        operations = _extract_operations(response)
        pages.append(
            {
                "response": response,
                "request_params": payload,
                "http_status": http_status,
            }
        )
        logger.info(
            "Fetched transactions page=%s operations=%s range=%s..%s",
            page_num,
            len(operations),
            date_from,
            date_to,
        )

        if not operations:
            break

        if pagination_mode == "page":
            current_page += 1
            if len(operations) < limit:
                break
        elif pagination_mode == "offset":
            current_offset += len(operations)
            if len(operations) < limit:
                break
        else:
            result = response.get("result")
            next_cursor = ""
            if isinstance(result, Mapping):
                raw_last_id = result.get("last_id") or result.get("cursor")
                if raw_last_id is not None:
                    next_cursor = str(raw_last_id)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    if page_num >= max_pages:
        logger.warning("Reached max_pages=%s while fetching transactions range=%s..%s", max_pages, date_from, date_to)

    return pages


def _chunk_date_ranges(date_from: str, date_to: str, max_days: int) -> list[tuple[str, str]]:
    if max_days <= 0:
        raise ValueError("max_days must be > 0")
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if start > end:
        raise ValueError(f"Invalid transactions period: {date_from} > {date_to}")

    ranges: list[tuple[str, str]] = []
    current = start
    while current <= end:
        _, month_days = monthrange(current.year, current.month)
        month_end = date(current.year, current.month, month_days)
        month_window_end = min(month_end, end)

        while current <= month_window_end:
            chunk_end = min(current + timedelta(days=max_days - 1), month_window_end)
            ranges.append((current.isoformat(), chunk_end.isoformat()))
            current = chunk_end + timedelta(days=1)

    return ranges
