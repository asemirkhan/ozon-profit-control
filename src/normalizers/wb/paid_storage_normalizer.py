from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from src.normalizers.wb.common import as_date, as_decimal, as_str, extract_response


def normalize_wb_paid_storage(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        records = _extract_records(response)
        for record in records:
            report_date = as_date(record.get("date"))
            if report_date is None:
                continue

            nm_id = as_str(record.get("nmId") or record.get("nmID") or record.get("nm_id"))
            offer_id = (
                as_str(record.get("vendorCode"))
                or as_str(record.get("supplierArticle"))
                or (f"wb_nm_{nm_id}" if nm_id else "~")
            )
            barcode = as_str(record.get("barcode"))
            chrt_id = as_str(record.get("chrtId") or record.get("chrtID"))
            gi_id = as_str(record.get("giId") or record.get("giID"))
            office_id = as_str(record.get("officeId") or record.get("officeID"))
            warehouse_name = as_str(record.get("warehouse"))
            original_date = as_date(record.get("originalDate"))
            source_record_id = _build_source_record_id(
                report_date=report_date.isoformat(),
                nm_id=nm_id,
                offer_id=offer_id,
                barcode=barcode,
                chrt_id=chrt_id,
                gi_id=gi_id,
                office_id=office_id,
                warehouse_name=warehouse_name,
                original_date=original_date.isoformat() if original_date else None,
            )

            rows.append(
                {
                    "source_record_id": source_record_id,
                    "marketplace": "wb",
                    "report_date": report_date,
                    "offer_id": offer_id,
                    "nm_id": nm_id,
                    "barcode": barcode,
                    "vendor_code": as_str(record.get("vendorCode")),
                    "subject_name": as_str(record.get("subject") or record.get("subjectName")),
                    "brand": as_str(record.get("brand")),
                    "size": as_str(record.get("size")),
                    "warehouse_name": warehouse_name,
                    "office_id": office_id,
                    "gi_id": gi_id,
                    "chrt_id": chrt_id,
                    "volume": as_decimal(record.get("volume")),
                    "calc_type": as_str(record.get("calcType")),
                    "warehouse_price": as_decimal(record.get("warehousePrice")) or 0,
                    "warehouse_coef": as_decimal(record.get("warehouseCoef")),
                    "log_warehouse_coef": as_decimal(record.get("logWarehouseCoef")),
                    "barcodes_count": as_decimal(record.get("barcodesCount")),
                    "pallet_place_code": as_decimal(record.get("palletPlaceCode")),
                    "pallet_count": as_decimal(record.get("palletCount")),
                    "loyalty_discount": as_decimal(record.get("loyaltyDiscount")),
                    "original_date": original_date,
                    "tariff_fix_date": as_date(record.get("tariffFixDate")),
                    "tariff_lower_date": as_date(record.get("tariffLowerDate")),
                    "raw_payload": dict(record),
                    "raw_updated_at": now,
                }
            )
    return rows


def _extract_records(response: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    if isinstance(response, Mapping):
        report = response.get("report")
        if isinstance(report, list):
            return [row for row in report if isinstance(row, dict)]
    return []


def _build_source_record_id(**parts: str | None) -> str:
    stable = "|".join((parts[key] or "") for key in sorted(parts))
    return hashlib.sha1(stable.encode("utf-8")).hexdigest()
