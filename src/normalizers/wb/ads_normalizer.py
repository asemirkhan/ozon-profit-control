from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.normalizers.wb.common import as_date, as_decimal, as_str, extract_response


def normalize_wb_ads(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    for page in pages:
        response = extract_response(page)
        records = _extract_ads_records(response)
        for record in records:
            date_value = as_date(record.get("date") or record.get("day") or record.get("statDate"))
            campaign_id = as_str(record.get("advertId") or record.get("campaign_id") or record.get("campaignId") or record.get("id"))
            if date_value is None or campaign_id is None:
                continue

            nm_id = as_str(record.get("nm_id") or record.get("nmId") or record.get("nmID") or record.get("nm"))
            offer_id = (
                as_str(record.get("offer_id"))
                or as_str(record.get("supplierArticle"))
                or as_str(record.get("vendorCode"))
                or (f"wb_nm_{nm_id}" if nm_id else "~")
            )
            spend = as_decimal(
                record.get("sum")
                or record.get("spend")
                or record.get("cost")
                or record.get("sumAds")
            )
            if spend is None:
                continue

            rows.append(
                {
                    "date": date_value,
                    "campaign_id": campaign_id,
                    "marketplace": "wb",
                    "campaign_name": as_str(record.get("campaign_name") or record.get("name")),
                    "campaign_type": as_str(record.get("type") or record.get("campaign_type")),
                    "offer_id": offer_id,
                    "nm_id": nm_id,
                    "app_type": as_str(record.get("appType") or record.get("app_type")),
                    "impressions": as_decimal(
                        record.get("views")
                        or record.get("shows")
                        or record.get("impressions")
                    ),
                    "clicks": as_decimal(record.get("clicks")),
                    "ctr": as_decimal(record.get("ctr")),
                    "cpc": as_decimal(record.get("cpc")),
                    "cr": as_decimal(record.get("cr") or record.get("conversion")),
                    "atbs": as_decimal(record.get("atbs") or record.get("addToBaskets")),
                    "orders_count": as_decimal(
                        record.get("orders")
                        or record.get("orders_count")
                        or record.get("atbs")
                    ),
                    "revenue": as_decimal(record.get("sum_price") or record.get("revenue")),
                    "spend": spend,
                    "currency": as_str(record.get("currency")) or "RUB",
                    "raw_payload": dict(record),
                    "raw_updated_at": now,
                }
            )

    return rows


def _extract_ads_records(response: Mapping[str, Any] | list[Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    if isinstance(response, list):
        iterable = response
    elif isinstance(response, Mapping):
        raw_rows = response.get("ads")
        if isinstance(raw_rows, list):
            iterable = raw_rows
        else:
            iterable = [response]
    else:
        return records

    for item in iterable:
        if not isinstance(item, Mapping):
            continue

        campaign_base = dict(item)
        campaign_base.pop("days", None)

        stats = item.get("days")
        if isinstance(stats, list):
            for day in stats:
                if not isinstance(day, Mapping):
                    continue
                merged = dict(campaign_base)
                merged.update(day)
                apps = day.get("apps")
                if isinstance(apps, list) and apps:
                    for app in apps:
                        if not isinstance(app, Mapping):
                            continue
                        app_row = dict(merged)
                        app_row.update(app)
                        nms = app.get("nm") or app.get("nms")
                        if isinstance(nms, list) and nms:
                            for nm_row in nms:
                                if not isinstance(nm_row, Mapping):
                                    continue
                                nm_flat = dict(app_row)
                                nm_flat.update(nm_row)
                                nm_flat.pop("days", None)
                                nm_flat.pop("apps", None)
                                nm_flat.pop("nms", None)
                                nm_flat.pop("nm", None)
                                records.append(nm_flat)
                            continue

                        app_row.pop("days", None)
                        app_row.pop("apps", None)
                        app_row.pop("nms", None)
                        app_row.pop("nm", None)
                        records.append(app_row)
                else:
                    merged.pop("days", None)
                    merged.pop("apps", None)
                    records.append(merged)
        else:
            records.append(dict(item))

    return records
