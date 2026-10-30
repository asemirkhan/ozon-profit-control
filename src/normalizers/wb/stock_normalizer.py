from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from src.normalizers.wb.common import as_date, as_decimal, as_str, extract_response


def normalize_wb_stock(
    pages: list[dict[str, Any]],
    snapshot_date: date | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    resolved_snapshot_date = snapshot_date or now.date()
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        records = _extract_records(response, "stocks")
        for record in records:
            nm_id = as_str(record.get("nmId") or record.get("nmID") or record.get("nm_id"))
            offer_id = (
                as_str(record.get("offer_id"))
                or as_str(record.get("supplierArticle"))
                or (f"wb_nm_{nm_id}" if nm_id else "~")
            )

            row_snapshot = as_date(record.get("date") or record.get("snapshot_date")) or resolved_snapshot_date
            stock_total = as_decimal(record.get("quantity") or record.get("stock_total")) or Decimal("0")
            stock_available = as_decimal(record.get("quantityFull") or record.get("stock_available"))
            stock_reserved = as_decimal(record.get("inWayToClient") or record.get("stock_reserved"))
            # inWayFromClient is a customer return in transit, not defective
            # stock.  Calling it a defect distorted the stock-risk signal.
            stock_defect = as_decimal(record.get("stock_defect"))

            if stock_available is None:
                stock_available = stock_total

            rows.append(
                {
                    "snapshot_date": row_snapshot,
                    "marketplace": "wb",
                    "offer_id": offer_id,
                    "nm_id": nm_id,
                    "barcode": as_str(record.get("barcode")),
                    "warehouse_name": as_str(record.get("warehouseName") or record.get("warehouse_name")) or "~",
                    "warehouse_type": as_str(record.get("warehouseType") or record.get("warehouse_type")),
                    "stock_total": stock_total,
                    "stock_available": stock_available,
                    "stock_reserved": stock_reserved,
                    "stock_defect": stock_defect,
                    "raw_payload": dict(record),
                    "raw_updated_at": now,
                }
            )

    return rows


def _extract_records(response: Mapping[str, Any] | list[Any], key: str) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]

    if not isinstance(response, Mapping):
        return []

    rows = response.get(key)
    if isinstance(rows, list):
        return [item for item in rows if isinstance(item, dict)]

    result = response.get("result")
    if isinstance(result, Mapping):
        nested = result.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]

    return []
