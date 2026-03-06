from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def normalize_transactions(
    pages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    now = datetime.now(timezone.utc)
    rows_tx: list[dict[str, Any]] = []
    rows_services: list[dict[str, Any]] = []
    rows_items: list[dict[str, Any]] = []

    for page in pages:
        response = _extract_response(page)
        operations = _extract_operations(response)
        if not operations:
            logger.warning("No operations in page. Top-level keys: %s", sorted(response.keys()))
            continue

        for operation in operations:
            operation_id = _as_str(_pick(operation, "operation_id", "operationId", "id"))
            if not operation_id:
                logger.warning("Skip operation without operation_id")
                continue

            operation_date = _as_str(_pick(operation, "operation_date", "operationDate", "date"))
            if not operation_date:
                logger.warning("Skip operation %s without operation_date", operation_id)
                continue

            operation_type = _as_str(_pick(operation, "operation_type", "operationType")) or "UNKNOWN"
            operation_type_name = _as_str(_pick(operation, "operation_type_name", "operationTypeName"))

            posting_number = _as_str(_pick(operation, "posting_number", "postingNumber"))
            posting = operation.get("posting")
            if not posting_number and isinstance(posting, Mapping):
                posting_number = _as_str(_pick(posting, "posting_number", "postingNumber"))

            rows_tx.append(
                {
                    "operation_id": operation_id,
                    "operation_date": operation_date,
                    "operation_type": operation_type,
                    "operation_type_name": operation_type_name,
                    "posting_number": posting_number,
                    "accruals_for_sale": _as_decimal(_pick(operation, "accruals_for_sale", "accrualsForSale")),
                    "sale_commission": _as_decimal(_pick(operation, "sale_commission", "saleCommission")),
                    "delivery_charge": _as_decimal(_pick(operation, "delivery_charge", "deliveryCharge")),
                    "return_delivery_charge": _as_decimal(
                        _pick(operation, "return_delivery_charge", "returnDeliveryCharge")
                    ),
                    "amount": _as_decimal(_pick(operation, "amount")),
                    "raw_updated_at": now,
                }
            )

            for service in _extract_services(operation):
                service_name = _as_str(_pick(service, "name", "service_name", "serviceName", "title"))
                if not service_name:
                    logger.warning("Skip service without name for operation %s", operation_id)
                    continue

                service_price = _as_decimal(_pick(service, "price", "amount", "value"))
                if service_price is None:
                    logger.warning("Service price missing for operation %s service %s, set 0", operation_id, service_name)
                    service_price = Decimal("0")

                rows_services.append(
                    {
                        "operation_id": operation_id,
                        "service_name": service_name,
                        "service_price": service_price,
                        "raw_updated_at": now,
                    }
                )

            for item in _extract_items(operation):
                sku = _as_str(_pick(item, "sku", "sku_id", "skuId"))
                if not sku:
                    logger.warning("Skip item without sku for operation %s", operation_id)
                    continue

                rows_items.append(
                    {
                        "operation_id": operation_id,
                        "sku": sku,
                        "item_name": _as_str(_pick(item, "name", "item_name", "itemName", "title")),
                        "raw_updated_at": now,
                    }
                )

    return rows_tx, rows_services, rows_items


def _extract_response(page: Mapping[str, Any]) -> Mapping[str, Any]:
    response = page.get("response")
    if isinstance(response, Mapping):
        return response
    return page


def _extract_operations(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, Mapping):
        for key in ("operations", "transactions", "items"):
            rows = result.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    for key in ("operations", "transactions"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _extract_services(operation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("services", "service"):
        rows = operation.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
    return []


def _extract_items(operation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    for key in ("items", "products"):
        rows = operation.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
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


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
