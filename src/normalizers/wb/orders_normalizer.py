from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.normalizers.wb.common import as_datetime, as_decimal, as_int, as_str, extract_response


def normalize_wb_orders(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        records = _extract_records(response, "orders")
        for record in records:
            order_id = as_str(record.get("srid") or record.get("orderId") or record.get("order_id"))
            order_dt = as_datetime(record.get("date") or record.get("createdAt") or record.get("order_date"))
            if not order_id or order_dt is None:
                continue

            nm_id = as_str(record.get("nmId") or record.get("nmID") or record.get("nm_id"))
            offer_id = (
                as_str(record.get("offer_id"))
                or as_str(record.get("supplierArticle"))
                or as_str(record.get("vendorCode"))
                or (f"wb_nm_{nm_id}" if nm_id else "~")
            )

            rows.append(
                {
                    "order_id": order_id,
                    "marketplace": "wb",
                    "order_date": order_dt,
                    "order_status": as_str(record.get("status")),
                    "cancel_dt": as_datetime(record.get("cancelDate") or record.get("cancel_dt")),
                    "nm_id": nm_id,
                    "offer_id": offer_id,
                    "barcode": as_str(record.get("barcode")),
                    "quantity": as_int(record.get("quantity"), default=1) or 1,
                    "sale_price": as_decimal(record.get("totalPrice") or record.get("salePrice") or record.get("price")),
                    "warehouse_name": as_str(record.get("warehouseName") or record.get("warehouse_name")),
                    "warehouse_type": as_str(record.get("warehouseType") or record.get("warehouse_type")),
                    "region_name": as_str(
                        record.get("regionName")
                        or record.get("oblastOkrugName")
                        or record.get("oblast")
                        or record.get("region_name")
                    ),
                    "tech_size": as_str(record.get("techSize") or record.get("tech_size")),
                    "subject_name": as_str(record.get("subject") or record.get("subjectName")),
                    "supplier_article": as_str(record.get("supplierArticle") or record.get("supplier_article")),
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
