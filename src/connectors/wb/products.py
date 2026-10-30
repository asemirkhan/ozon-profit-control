from __future__ import annotations

import logging
import os
from typing import Any, Mapping

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_PRODUCTS_URL = os.getenv(
    "WB_PRODUCTS_URL",
    "https://content-api.wildberries.ru/content/v2/get/cards/list",
)


def _extract_cards(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = response.get("result")
    if isinstance(result, Mapping):
        cards = result.get("cards") or result.get("items")
        if isinstance(cards, list):
            return [item for item in cards if isinstance(item, dict)]
    cards = response.get("cards")
    if isinstance(cards, list):
        return [item for item in cards if isinstance(item, dict)]
    return []


def fetch_wb_products_pages(limit: int = 100, max_pages: int | None = 1000) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be > 0 when set")

    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    cursor_updated_at: str | None = None
    cursor_nm_id: int | None = None

    page_num = 0
    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            if pages and _extract_cards(pages[-1]["response"]):
                pages[-1]["page_cap_reached"] = True
            break

        page_num += 1
        cursor: dict[str, Any] = {"limit": limit}
        if cursor_updated_at:
            cursor["updatedAt"] = cursor_updated_at
        if cursor_nm_id is not None:
            cursor["nmID"] = cursor_nm_id

        payload = {
            "settings": {
                "cursor": cursor,
                "filter": {"withPhoto": -1},
            }
        }

        raw, status = request_json_with_status(
            method="POST",
            url=WB_PRODUCTS_URL,
            headers=headers,
            payload=payload,
        )
        if not isinstance(raw, Mapping):
            raise RuntimeError("WB products response must be JSON object")

        cards = _extract_cards(raw)
        pages.append(
            {
                "response": dict(raw),
                "request_params": payload,
                "http_status": status,
            }
        )

        logger.info("Fetched WB products page=%s cards=%s", page_num, len(cards))

        if not cards:
            break

        result = raw.get("result")
        next_updated_at: str | None = None
        next_nm_id: int | None = None
        if isinstance(result, Mapping):
            next_cursor = result.get("cursor")
            if isinstance(next_cursor, Mapping):
                raw_updated_at = next_cursor.get("updatedAt")
                raw_nm_id = next_cursor.get("nmID")
                if raw_updated_at is not None:
                    next_updated_at = str(raw_updated_at)
                if isinstance(raw_nm_id, int):
                    next_nm_id = raw_nm_id

        if not next_updated_at and next_nm_id is None:
            if len(cards) >= limit:
                pages[-1]["page_cap_reached"] = True
            break
        if next_updated_at == cursor_updated_at and next_nm_id == cursor_nm_id:
            logger.warning("WB products cursor did not change, stopping")
            pages[-1]["page_cap_reached"] = True
            break

        cursor_updated_at = next_updated_at
        cursor_nm_id = next_nm_id

        if len(cards) < limit:
            break

    return pages
