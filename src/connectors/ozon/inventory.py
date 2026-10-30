from __future__ import annotations

import logging
import os
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import get_ozon_headers
from src.connectors.ozon.http import post_json_with_status

logger = logging.getLogger(__name__)

DEFAULT_INVENTORY_URL = "https://api-seller.ozon.ru/v2/analytics/stock_on_warehouses"
PaginationMode = Literal["offset", "page", "cursor"]


def _extract_inventory_rows(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks: list[Any] = []
    result = response.get("result")
    if result is not None:
        blocks.append(result)
    blocks.append(response)

    for block in blocks:
        if not isinstance(block, Mapping):
            continue

        for key in ("rows", "items", "stocks", "products", "inventory"):
            rows = block.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]

        warehouses = block.get("warehouses")
        if isinstance(warehouses, list):
            rows_from_warehouses: list[dict[str, Any]] = []
            for warehouse in warehouses:
                if not isinstance(warehouse, Mapping):
                    continue
                for key in ("items", "products", "stocks", "rows"):
                    rows = warehouse.get(key)
                    if isinstance(rows, list):
                        rows_from_warehouses.extend(
                            [row for row in rows if isinstance(row, dict)]
                        )
            if rows_from_warehouses:
                return rows_from_warehouses

    return []


def fetch_inventory_pages(
    limit: int = 100,
    max_pages: int = 1000,
    pagination_mode: PaginationMode = "offset",
    offset: int = 0,
    page: int = 1,
    endpoint: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    if page <= 0:
        raise ValueError("page must be > 0")

    headers = get_ozon_headers()
    url = endpoint or os.getenv("OZON_INVENTORY_URL", DEFAULT_INVENTORY_URL)

    pages: list[dict[str, Any]] = []
    current_offset = offset
    current_page = page
    cursor = ""

    page_num = 0
    while page_num < max_pages:
        page_num += 1

        payload: dict[str, Any] = {"limit": limit}
        if extra_payload:
            payload.update(extra_payload)

        if pagination_mode == "offset":
            payload["offset"] = current_offset
        elif pagination_mode == "page":
            payload["page"] = current_page
        else:
            payload["last_id"] = cursor

        response, http_status = post_json_with_status(url, payload, headers)
        rows = _extract_inventory_rows(response)
        pages.append(
            {
                "response": response,
                "request_params": payload,
                "http_status": http_status,
            }
        )

        logger.info("Fetched inventory page=%s rows=%s", page_num, len(rows))

        if not rows:
            break

        if pagination_mode == "offset":
            current_offset += len(rows)
            if len(rows) < limit:
                break
        elif pagination_mode == "page":
            current_page += 1
            if len(rows) < limit:
                break
        else:
            result = response.get("result")
            next_cursor = ""
            if isinstance(result, Mapping):
                raw_cursor = result.get("last_id") or result.get("cursor")
                if raw_cursor is not None:
                    next_cursor = str(raw_cursor)
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    if page_num >= max_pages:
        logger.warning("Reached max_pages=%s while fetching inventory", max_pages)

    return pages
