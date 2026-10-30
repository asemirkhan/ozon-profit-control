from __future__ import annotations

import logging
import os
from typing import Any, Literal

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_STOCK_URL = os.getenv(
    "WB_STOCK_URL",
    "https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses",
)

PaginationMode = Literal["offset", "single"]


def fetch_wb_stock_pages(
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
            if pages and pages[-1]["response"].get("stocks"):
                pages[-1]["page_cap_reached"] = True
            break

        page_num += 1
        # date_from is kept in the public signature because the unified
        # ingestion registry calls every snapshot source with a date.  The
        # current WB warehouse-stock API is a snapshot and does not accept it.
        payload: dict[str, Any] = {
            "limit": min(limit, 250_000),
            "offset": current_offset if pagination_mode == "offset" else 0,
        }

        raw, status = request_json_with_status(
            method="POST",
            url=WB_STOCK_URL,
            headers=headers,
            payload=payload,
            rate_api_category="analytics",
            rate_endpoint="/api/analytics/v1/stocks-report/wb-warehouses",
        )

        if not isinstance(raw, dict):
            raise RuntimeError("WB stock response must be a JSON object")
        data = raw.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise RuntimeError("WB stock response must contain data.items")

        rows = [item for item in data["items"] if isinstance(item, dict)]
        pages.append(
            {
                "response": {"stocks": rows},
                "request_params": payload,
                "http_status": status,
            }
        )
        logger.info("Fetched WB stock page=%s rows=%s", page_num, len(rows))

        if pagination_mode == "single":
            break
        if not rows or len(rows) < limit:
            break

        current_offset += len(rows)

    return pages
