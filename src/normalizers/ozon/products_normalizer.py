from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _extract_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    blocks: list[Any] = []
    if "result" in payload:
        blocks.append(payload.get("result"))
    blocks.append(payload)

    for block in blocks:
        if isinstance(block, Mapping):
            for key in ("items", "products"):
                items = block.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
    return []


def _page_response(page: Mapping[str, Any]) -> Mapping[str, Any]:
    response = page.get("response")
    if isinstance(response, Mapping):
        return response
    return page


def _pick(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item.get(key) is not None:
            return item.get(key)
    return None


def normalize_products(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    skipped_without_offer = 0
    unknown_pages = 0

    for page in pages:
        response = _page_response(page)
        items = _extract_items(response)
        if not items:
            unknown_pages += 1
            logger.warning(
                "Unknown products page format. Top-level keys: %s",
                sorted(response.keys()),
            )
            continue

        for item in items:
            offer_id = _pick(item, "offer_id", "offerId")
            if offer_id is None or str(offer_id).strip() == "":
                skipped_without_offer += 1
                continue

            row = {
                "offer_id": str(offer_id),
                "product_id": _as_str_or_none(_pick(item, "product_id", "productId", "id")),
                "sku": _as_str_or_none(_pick(item, "sku", "sku_id", "skuId")),
                "product_name": _as_str_or_none(_pick(item, "name", "product_name", "title")),
                "brand": _as_str_or_none(_pick(item, "brand", "brand_name", "brandName")),
                "category_id": _as_str_or_none(_pick(item, "category_id", "categoryId")),
                "category_name": _as_str_or_none(_pick(item, "category_name", "categoryName")),
                "archived": _as_bool_or_none(_pick(item, "archived", "is_archived", "isArchived")),
                "updated_at": now,
            }
            rows.append(row)

    if skipped_without_offer:
        logger.warning("Skipped products without offer_id: %s", skipped_without_offer)
    if unknown_pages and not rows:
        logger.warning("No rows normalized. Unknown page count: %s", unknown_pages)

    return rows


def _as_str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if isinstance(value, int):
        return bool(value)
    return None
