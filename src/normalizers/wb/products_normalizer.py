from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.normalizers.wb.common import as_decimal, as_str, extract_response


def normalize_wb_products(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        cards = _extract_cards(response)
        for card in cards:
            nm_id = as_str(card.get("nmID") or card.get("nm_id") or card.get("id"))
            if not nm_id:
                continue

            vendor_code = as_str(card.get("vendorCode") or card.get("vendor_code"))
            offer_id = (
                as_str(card.get("offer_id"))
                or vendor_code
                or as_str(card.get("supplierArticle"))
                or f"wb_nm_{nm_id}"
            )

            sizes = card.get("sizes") if isinstance(card.get("sizes"), list) else []
            first_size = sizes[0] if sizes and isinstance(sizes[0], Mapping) else {}
            skus = first_size.get("skus") if isinstance(first_size.get("skus"), list) else []
            barcode = as_str(skus[0]) if skus else as_str(card.get("barcode"))

            subject_name = card.get("subjectName") or card.get("subject") or card.get("category_name")
            dimensions = card.get("dimensions") if isinstance(card.get("dimensions"), Mapping) else {}
            product_name = card.get("title") or card.get("name") or card.get("imt_name")
            characteristics = card.get("characteristics")

            rows.append(
                {
                    "nm_id": nm_id,
                    "marketplace": "wb",
                    "offer_id": offer_id or "~",
                    "vendor_code": vendor_code,
                    "barcode": barcode,
                    "product_name": as_str(product_name),
                    "brand": as_str(card.get("brand")),
                    "category_name": as_str(subject_name),
                    "subject_id": as_str(card.get("subjectID") or card.get("subjectId")),
                    "subject_name": as_str(subject_name),
                    "length_cm": as_decimal(dimensions.get("length") or card.get("length")),
                    "width_cm": as_decimal(dimensions.get("width") or card.get("width")),
                    "height_cm": as_decimal(dimensions.get("height") or card.get("height")),
                    "weight_brutto_kg": as_decimal(
                        dimensions.get("weightBrutto")
                        or card.get("weightBrutto")
                        or card.get("weight")
                    ),
                    "characteristics_json": characteristics if isinstance(characteristics, (list, Mapping)) else None,
                    "raw_payload": dict(card),
                    "raw_updated_at": now,
                }
            )

    return rows


def _extract_cards(response: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]

    if not isinstance(response, Mapping):
        return []

    result = response.get("result")
    if isinstance(result, Mapping):
        cards = result.get("cards") or result.get("items")
        if isinstance(cards, list):
            return [item for item in cards if isinstance(item, dict)]

    cards = response.get("cards") or response.get("products")
    if isinstance(cards, list):
        return [item for item in cards if isinstance(item, dict)]

    return []
