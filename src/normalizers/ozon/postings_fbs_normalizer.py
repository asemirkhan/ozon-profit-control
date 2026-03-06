from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def normalize_postings_fbs(
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    rows_posting: list[dict[str, Any]] = []
    rows_items: list[dict[str, Any]] = []
    rows_financial: list[dict[str, Any]] = []

    for page in pages:
        response = _extract_response(page)
        postings = _extract_postings(response)
        if not postings:
            logger.warning("No postings in page. Top-level keys: %s", sorted(response.keys()))
            continue

        for posting in postings:
            posting_number = _as_str(_pick(posting, "posting_number", "postingNumber"))
            if not posting_number:
                logger.warning("Skip posting without posting_number")
                continue

            created_at = _as_str(_pick(posting, "created_at", "createdAt"))
            if not created_at:
                logger.warning("Skip posting %s without created_at", posting_number)
                continue

            status = _as_str(_pick(posting, "status")) or "UNKNOWN"

            analytics_data = posting.get("analytics_data")
            analytics = analytics_data if isinstance(analytics_data, Mapping) else {}
            delivery_method = posting.get("delivery_method")
            delivery = delivery_method if isinstance(delivery_method, Mapping) else {}

            rows_posting.append(
                {
                    "posting_number": posting_number,
                    "status": status,
                    "created_at": created_at,
                    "in_process_at": _as_str(_pick(posting, "in_process_at", "inProcessAt")),
                    "shipped_at": _as_str(_pick(posting, "shipped_at", "shippedAt")),
                    "delivered_at": _as_str(_pick(posting, "delivered_at", "deliveredAt")),
                    "cancelled_at": _as_str(_pick(posting, "cancelled_at", "cancelledAt")),
                    "delivery_schema": _as_str(_pick(posting, "delivery_schema"))
                    or _as_str(_pick(delivery, "schema")),
                    "warehouse_id": _as_str(_pick(posting, "warehouse_id"))
                    or _as_str(_pick(analytics, "warehouse_id", "warehouseId")),
                    "customer_region": _as_str(_pick(posting, "customer_region"))
                    or _as_str(_pick(analytics, "region")),
                    "is_multibox": _as_bool(_pick(posting, "is_multibox", "isMultiBox")),
                    "raw_updated_at": now,
                }
            )

            products = posting.get("products")
            posting_products = [p for p in products if isinstance(p, Mapping)] if isinstance(products, list) else []
            product_lookup = _build_product_lookup(posting_products)

            for product in posting_products:
                offer_id = _as_str(_pick(product, "offer_id", "offerId")) or "~"
                sku = _as_str(_pick(product, "sku", "sku_id", "skuId")) or "~"
                product_id = _as_str(_pick(product, "product_id", "productId", "id")) or "~"

                quantity_value = _pick(product, "quantity", "qty")
                quantity = _as_int(quantity_value)
                if quantity is None:
                    logger.warning("Posting %s product quantity missing, set 0", posting_number)
                    quantity = 0

                rows_items.append(
                    {
                        "posting_number": posting_number,
                        "offer_id": offer_id,
                        "sku": sku,
                        "product_id": product_id,
                        "quantity": quantity,
                        "item_price": _as_decimal(_pick(product, "price", "item_price", "itemPrice")),
                        "currency": _as_str(_pick(product, "currency", "currency_code", "currencyCode")),
                        "raw_updated_at": now,
                    }
                )

            financial_rows = _normalize_financial_rows(
                posting_number=posting_number,
                posting=posting,
                product_lookup=product_lookup,
                now=now,
            )
            rows_financial.extend(financial_rows)

    return rows_posting, rows_items, rows_financial


def _normalize_financial_rows(
    posting_number: str,
    posting: Mapping[str, Any],
    product_lookup: dict[str, str],
    now: datetime,
) -> list[dict[str, Any]]:
    financial_data = posting.get("financial_data")
    if not isinstance(financial_data, Mapping):
        return []

    raw_products = financial_data.get("products")
    if not isinstance(raw_products, list):
        return []

    rows: list[dict[str, Any]] = []
    for product in raw_products:
        if not isinstance(product, Mapping):
            continue

        sku = _as_str(_pick(product, "sku", "sku_id", "skuId"))
        product_id = _as_str(_pick(product, "product_id", "productId"))
        if not product_id and sku:
            product_id = product_lookup.get(sku)
        if not product_id:
            logger.warning("Skip financial row without product_id for posting %s", posting_number)
            continue

        quantity = _as_int(_pick(product, "quantity", "qty"))
        if quantity is None:
            logger.warning("Financial quantity missing for posting %s product %s, set 0", posting_number, product_id)
            quantity = 0

        commission_amount = _as_decimal(_pick(product, "commission_amount", "commissionAmount"))
        commission_percent = _as_decimal(_pick(product, "commission_percent", "commissionPercent"))
        commission = product.get("commission")
        if isinstance(commission, Mapping):
            commission_amount = commission_amount or _as_decimal(_pick(commission, "amount", "value"))
            commission_percent = commission_percent or _as_decimal(_pick(commission, "percent", "rate"))

        item_services_total = _as_decimal(_pick(product, "item_services_total", "itemServicesTotal"))
        services = product.get("services")
        if item_services_total is None and isinstance(services, list):
            sum_value = Decimal("0")
            has_values = False
            for service in services:
                if not isinstance(service, Mapping):
                    continue
                price = _as_decimal(_pick(service, "price", "amount", "value"))
                if price is not None:
                    has_values = True
                    sum_value += price
            if has_values:
                item_services_total = sum_value

        rows.append(
            {
                "posting_number": posting_number,
                "product_id": product_id,
                "sku": sku,
                "quantity": quantity,
                "price": _as_decimal(_pick(product, "price")),
                "old_price": _as_decimal(_pick(product, "old_price", "oldPrice")),
                "total_discount_value": _as_decimal(
                    _pick(product, "total_discount_value", "totalDiscountValue")
                ),
                "total_discount_percent": _as_decimal(
                    _pick(product, "total_discount_percent", "totalDiscountPercent")
                ),
                "commission_amount": commission_amount,
                "commission_percent": commission_percent,
                "payout": _as_decimal(_pick(product, "payout")),
                "item_services_total": item_services_total,
                "raw_updated_at": now,
            }
        )

    return rows


def _build_product_lookup(products: list[Mapping[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for product in products:
        sku = _as_str(_pick(product, "sku", "sku_id", "skuId"))
        product_id = _as_str(_pick(product, "product_id", "productId", "id"))
        if sku and product_id:
            lookup[sku] = product_id
    return lookup


def _extract_response(page: Mapping[str, Any]) -> Mapping[str, Any]:
    response = page.get("response")
    if isinstance(response, Mapping):
        return response
    return page


def _extract_postings(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, Mapping):
        for key in ("postings", "items"):
            postings = result.get(key)
            if isinstance(postings, list):
                return [row for row in postings if isinstance(row, dict)]
    postings = payload.get("postings")
    if isinstance(postings, list):
        return [row for row in postings if isinstance(row, dict)]
    return []


def _pick(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
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
