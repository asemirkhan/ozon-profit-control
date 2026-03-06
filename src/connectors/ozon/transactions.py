from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import get_ozon_headers
from src.connectors.ozon.http import post_json_with_status

logger = logging.getLogger(__name__)

TRANSACTIONS_LIST_URL = "https://api-seller.ozon.ru/v3/finance/transaction/list"
PaginationMode = Literal["page", "offset", "cursor"]


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
        logger.info("Fetched transactions page=%s operations=%s", page_num, len(operations))

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
        logger.warning("Reached max_pages=%s while fetching transactions", max_pages)

    return pages
