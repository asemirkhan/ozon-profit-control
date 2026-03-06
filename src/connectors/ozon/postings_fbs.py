from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import get_ozon_headers
from src.connectors.ozon.http import post_json_with_status

logger = logging.getLogger(__name__)

POSTINGS_FBS_LIST_URL = "https://api-seller.ozon.ru/v3/posting/fbs/list"
PaginationMode = Literal["offset", "cursor"]


def _extract_postings(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result")
    if isinstance(result, Mapping):
        for key in ("postings", "items"):
            postings = result.get(key)
            if isinstance(postings, list):
                return [row for row in postings if isinstance(row, dict)]
    postings = response.get("postings")
    if isinstance(postings, list):
        return [row for row in postings if isinstance(row, dict)]
    return []


def fetch_postings_fbs_pages(
    date_from: str,
    date_to: str,
    limit: int = 100,
    max_pages: int = 1000,
    offset: int = 0,
    pagination_mode: PaginationMode = "offset",
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    headers = get_ozon_headers()
    pages: list[dict[str, Any]] = []
    current_offset = offset
    cursor = ""

    page_num = 0
    while page_num < max_pages:
        page_num += 1
        payload: dict[str, Any] = {
            "dir": "ASC",
            "filter": {
                "since": f"{date_from}T00:00:00Z",
                "to": f"{date_to}T23:59:59Z",
            },
            "limit": limit,
            "with": {
                "analytics_data": True,
                "financial_data": True,
            },
        }
        if statuses:
            payload["filter"]["status"] = statuses

        if pagination_mode == "offset":
            payload["offset"] = current_offset
        else:
            payload["last_id"] = cursor

        response, http_status = post_json_with_status(POSTINGS_FBS_LIST_URL, payload, headers)
        postings = _extract_postings(response)
        pages.append(
            {
                "response": response,
                "request_params": payload,
                "http_status": http_status,
            }
        )
        logger.info("Fetched FBS postings page=%s postings=%s", page_num, len(postings))

        if not postings:
            break

        if pagination_mode == "offset":
            current_offset += len(postings)
            if len(postings) < limit:
                break
        else:
            result = response.get("result")
            next_cursor = ""
            if isinstance(result, Mapping):
                raw_last_id = result.get("last_id")
                if raw_last_id is not None:
                    next_cursor = str(raw_last_id)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    if page_num >= max_pages:
        logger.warning("Reached max_pages=%s while fetching postings", max_pages)

    return pages
