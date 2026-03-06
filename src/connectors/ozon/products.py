from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import get_ozon_headers
from src.connectors.ozon.http import post_json

logger = logging.getLogger(__name__)

PRODUCT_LIST_URL = "https://api-seller.ozon.ru/v3/product/list"
PaginationMode = Literal["last_id", "offset"]


def _extract_items(response: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidate_blocks: list[Any] = []
    if "result" in response:
        candidate_blocks.append(response.get("result"))
    candidate_blocks.append(response)

    for block in candidate_blocks:
        if isinstance(block, Mapping):
            for key in ("items", "products"):
                items = block.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
    return []


def fetch_products_raw(
    limit: int = 100,
    filter_payload: Mapping[str, Any] | None = None,
    pagination_mode: PaginationMode = "last_id",
    max_pages: int | None = 1000,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be > 0 when set")

    headers = get_ozon_headers()
    pages: list[dict[str, Any]] = []

    last_id = ""
    offset = 0
    page_num = 0
    req_filter = dict(filter_payload or {})

    while True:
        if max_pages is not None and page_num >= max_pages:
            logger.warning("Reached max_pages=%s, stopping pagination", max_pages)
            break

        page_num += 1
        request_params: dict[str, Any] = {"filter": req_filter, "limit": limit}
        if pagination_mode == "last_id":
            request_params["last_id"] = last_id
        else:
            request_params["offset"] = offset

        response = post_json(PRODUCT_LIST_URL, request_params, headers)
        items = _extract_items(response)
        pages.append(
            {
                "response": response,
                "request_params": request_params,
            }
        )

        logger.info("Fetched products page=%s items=%s", page_num, len(items))

        if not items:
            break

        result = response.get("result")
        if pagination_mode == "last_id":
            next_last_id = ""
            if isinstance(result, Mapping):
                raw_next_last_id = result.get("last_id")
                if raw_next_last_id is not None:
                    next_last_id = str(raw_next_last_id)
            if not next_last_id:
                break
            if next_last_id == last_id:
                logger.warning("Received same last_id '%s', stopping to avoid loop", next_last_id)
                break
            last_id = next_last_id
        else:
            offset += len(items)
            total: int | None = None
            if isinstance(result, Mapping):
                raw_total = result.get("total")
                if isinstance(raw_total, int):
                    total = raw_total
            if total is not None and offset >= total:
                break
            if len(items) < limit:
                break

    return pages
