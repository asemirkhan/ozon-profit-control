from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.normalizers.wb.common import as_bool, as_datetime, as_decimal, as_int, as_str, extract_response


def normalize_wb_sales(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        records = _extract_records(response, "sales")
        for record in records:
            sale_id = as_str(record.get("saleID") or record.get("saleId") or record.get("sale_id") or record.get("srid"))
            sale_dt = as_datetime(record.get("date") or record.get("saleDate") or record.get("sale_date"))
            if not sale_id or sale_dt is None:
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
                    "sale_id": sale_id,
                    "marketplace": "wb",
                    "sale_date": sale_dt,
                    "order_id": as_str(record.get("srid") or record.get("orderId") or record.get("order_id")),
                    "sale_status": as_str(record.get("status")),
                    "is_return": as_bool(record.get("isReturn") or record.get("is_return")),
                    "nm_id": nm_id,
                    "offer_id": offer_id,
                    "barcode": as_str(record.get("barcode")),
                    "quantity": as_int(record.get("quantity"), default=1) or 1,
                    "revenue_gross": as_decimal(record.get("forPay") or record.get("finishedPrice") or record.get("revenue")),
                    "warehouse_name": as_str(record.get("warehouseName") or record.get("warehouse_name")),
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
