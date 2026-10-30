from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

logger = logging.getLogger(__name__)

INVENTORY_LIST_KEYS: tuple[str, ...] = (
    "rows",
    "items",
    "stocks",
    "products",
    "inventory",
)
WAREHOUSE_KEYS: tuple[str, ...] = ("warehouses", "warehouse_list", "locations")


def normalize_inventory(
    pages: list[dict[str, Any]],
    snapshot_date: date | str | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    snapshot_day = _coerce_snapshot_date(snapshot_date, now.date())

    rows: list[dict[str, Any]] = []
    unknown_pages = 0

    for page in pages:
        response = _extract_response(page)
        records = _extract_inventory_records(response)
        if not records:
            unknown_pages += 1
            logger.warning("Unknown inventory page format. Top-level keys: %s", sorted(response.keys()))
            continue

        for warehouse_ctx, record in records:
            row = _build_row(
                snapshot_day=snapshot_day,
                now=now,
                warehouse_ctx=warehouse_ctx,
                record=record,
            )
            rows.append(row)

    if unknown_pages and not rows:
        logger.warning("No inventory rows normalized. Unknown page count: %s", unknown_pages)

    return rows


def _build_row(
    snapshot_day: date,
    now: datetime,
    warehouse_ctx: Mapping[str, Any] | None,
    record: Mapping[str, Any],
) -> dict[str, Any]:
    sources = _record_sources(record, warehouse_ctx)
    offer_id = _as_text(_pick_from_sources(sources, "offer_id", "offerId", "item_code")) or "~"
    sku = _as_text(_pick_from_sources(sources, "sku", "sku_id", "skuId")) or "~"
    product_id = _as_text(_pick_from_sources(sources, "product_id", "productId", "id")) or "~"

    warehouse_name = _as_text(
        _pick_warehouse_value(warehouse_ctx, record, "warehouse_name", "warehouseName", "name")
    ) or "~"
    warehouse_type = _as_text(
        _pick_warehouse_value(warehouse_ctx, record, "warehouse_type", "warehouseType", "type")
    )

    stock_total = _coalesce_stock_total(record, sources)
    if stock_total is None:
        stock_total = Decimal("0")

    stock_available = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_available",
            "available",
            "available_stock_count",
            "free_to_sell_amount",
            "free",
            "present",
        )
    )
    stock_reserved = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_reserved",
            "reserved",
            "promised_amount",
            "reserve",
        )
    )
    stock_defect = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_defect",
            "defect",
            "defects",
            "defected",
        )
    )

    if stock_available is None and stock_reserved is not None:
        stock_available = stock_total - stock_reserved

    raw_updated_at = _as_datetime(
        _pick_from_sources(
            sources,
            "updated_at",
            "update_at",
            "updatedAt",
            "last_updated",
            "lastUpdated",
            "changed_at",
        )
    ) or now

    return {
        "snapshot_date": snapshot_day,
        "marketplace": "ozon",
        "offer_id": offer_id,
        "sku": sku,
        "product_id": product_id,
        "warehouse_name": warehouse_name,
        "warehouse_type": warehouse_type,
        "stock_total": stock_total,
        "stock_available": stock_available,
        "stock_reserved": stock_reserved,
        "stock_defect": stock_defect,
        "raw_updated_at": raw_updated_at,
    }


def _extract_response(page: Mapping[str, Any]) -> Mapping[str, Any]:
    response = page.get("response")
    if isinstance(response, Mapping):
        return response
    return page


def _extract_inventory_records(
    payload: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any] | None, Mapping[str, Any]]]:
    result = payload.get("result")
    roots: list[Any] = [result, payload] if result is not None else [payload]
    extracted: list[tuple[Mapping[str, Any] | None, Mapping[str, Any]]] = []

    for root in roots:
        extracted.extend(_extract_from_root(root))
        if extracted:
            break

    return extracted


def _extract_from_root(
    root: Any,
) -> list[tuple[Mapping[str, Any] | None, Mapping[str, Any]]]:
    records: list[tuple[Mapping[str, Any] | None, Mapping[str, Any]]] = []

    if isinstance(root, list):
        for item in root:
            if isinstance(item, Mapping):
                records.append((None, item))
        return records

    if not isinstance(root, Mapping):
        return records

    warehouses = _extract_mapping_list(root, WAREHOUSE_KEYS)
    for warehouse in warehouses:
        nested = _extract_first_mapping_list(warehouse, INVENTORY_LIST_KEYS)
        if nested:
            for item in nested:
                records.append((warehouse, item))
        elif _looks_like_inventory_row(warehouse):
            records.append((warehouse, warehouse))

    if records:
        return records

    for item in _extract_first_mapping_list(root, INVENTORY_LIST_KEYS):
        records.append((None, item))
    if records:
        return records

    if _looks_like_inventory_row(root):
        return [(None, root)]
    return records


def _extract_first_mapping_list(data: Mapping[str, Any], keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    for key in keys:
        items = data.get(key)
        if isinstance(items, list):
            return [item for item in items if isinstance(item, Mapping)]
    return []


def _extract_mapping_list(data: Mapping[str, Any], keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    return _extract_first_mapping_list(data, keys)


def _looks_like_inventory_row(item: Mapping[str, Any]) -> bool:
    if _pick_value(item, "offer_id", "offerId", "sku", "sku_id", "skuId", "product_id", "productId") is not None:
        return True
    if _pick_value(
        item,
        "stock_total",
        "stock_available",
        "stock_reserved",
        "free_to_sell_amount",
        "promised_amount",
        "present",
        "quantity",
    ) is not None:
        return True
    return False


def _record_sources(
    record: Mapping[str, Any],
    warehouse_ctx: Mapping[str, Any] | None,
) -> tuple[Mapping[str, Any], ...]:
    nested_sources: list[Mapping[str, Any]] = [record]
    for key in ("product", "item", "offer", "stock", "inventory", "totals", "summary"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            nested_sources.append(nested)
    if warehouse_ctx is not None:
        nested_sources.append(warehouse_ctx)
    return tuple(nested_sources)


def _pick_warehouse_value(
    warehouse_ctx: Mapping[str, Any] | None,
    record: Mapping[str, Any],
    *keys: str,
) -> Any:
    if warehouse_ctx is not None:
        value = _pick_value(warehouse_ctx, *keys)
        if value is not None:
            return value

    prefixed_keys = tuple(f"warehouse_{key}" for key in keys)
    value = _pick_value(record, *prefixed_keys)
    if value is not None:
        return value

    return _pick_value(record, *keys)


def _coalesce_stock_total(record: Mapping[str, Any], sources: tuple[Mapping[str, Any], ...]) -> Decimal | None:
    stock_total = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_total",
            "stock",
            "stock_on_hand",
            "total_stock",
            "quantity",
            "present",
        )
    )
    if stock_total is not None:
        return stock_total

    available = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_available",
            "available",
            "available_stock_count",
            "free_to_sell_amount",
            "free",
        )
    )
    reserved = _as_decimal(
        _pick_from_sources(
            sources,
            "stock_reserved",
            "reserved",
            "promised_amount",
            "reserve",
        )
    )
    defect = _as_decimal(_pick_from_sources(sources, "stock_defect", "defect", "defects"))

    if available is None and reserved is None and defect is None:
        return None

    return (available or Decimal("0")) + (reserved or Decimal("0")) + (defect or Decimal("0"))


def _pick_from_sources(sources: tuple[Mapping[str, Any], ...], *keys: str) -> Any:
    for source in sources:
        value = _pick_value(source, *keys)
        if value is not None:
            return value
    return None


def _pick_value(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return None


def _as_text(value: Any) -> str | None:
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


def _coerce_snapshot_date(value: date | str | None, default: date) -> date:
    if value is None:
        return default
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return default
    return date.fromisoformat(text)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
