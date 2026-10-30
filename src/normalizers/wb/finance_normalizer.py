from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from src.normalizers.wb.common import as_datetime, as_decimal, as_str, extract_response


def normalize_wb_finance(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return build_wb_finance_normalization_result(pages)["rows"]


def build_wb_finance_normalization_result(
    pages: list[dict[str, Any]],
    *,
    target_day: date | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    seen_finance_ids: set[str] = set()
    raw_rows_total = 0
    raw_amount_sum = Decimal("0")
    raw_commission_sum = Decimal("0")
    raw_logistics_sum = Decimal("0")
    raw_services_sum = Decimal("0")
    normalized_amount_sum = Decimal("0")
    normalized_commission_sum = Decimal("0")
    normalized_logistics_sum = Decimal("0")
    normalized_services_sum = Decimal("0")
    drop_buckets: dict[tuple[str, str | None], dict[str, Any]] = defaultdict(
        lambda: {
            "drop_reason": None,
            "operation_date_source": None,
            "dropped_rows": 0,
            "rrd_id_min": None,
            "rrd_id_max": None,
        }
    )

    for page in pages:
        if page.get("request_success") is False:
            continue
        response = extract_response(page)
        records = _extract_records(response, "finance")
        for record in records:
            raw_rows_total += 1
            finance_id = _pick_finance_id(record)
            rrd_id_value = _rrd_id_int(record)
            operation_date_source, operation_date = _pick_operation_date(record)
            if operation_date is None and _has_any_operation_date_value(record):
                _register_drop(
                    buckets=drop_buckets,
                    reason="parser_error",
                    operation_date_source=operation_date_source,
                    rrd_id_value=rrd_id_value,
                )
                continue
            if not finance_id or operation_date is None:
                _register_drop(
                    buckets=drop_buckets,
                    reason="missing_required_fields",
                    operation_date_source=operation_date_source,
                    rrd_id_value=rrd_id_value,
                )
                continue
            if target_day is not None and operation_date.date() != target_day:
                _register_drop(
                    buckets=drop_buckets,
                    reason="date_out_of_day",
                    operation_date_source=operation_date_source,
                    rrd_id_value=rrd_id_value,
                )
                continue
            if finance_id in seen_finance_ids:
                _register_drop(
                    buckets=drop_buckets,
                    reason="duplicate_rrdid",
                    operation_date_source=operation_date_source,
                    rrd_id_value=rrd_id_value,
                )
                continue
            seen_finance_ids.add(finance_id)

            nm_id = as_str(record.get("nm_id") or record.get("nmId") or record.get("nmID"))
            offer_id = (
                as_str(record.get("offer_id"))
                or as_str(record.get("supplierArticle"))
                or as_str(record.get("sa_name"))
                or (f"wb_nm_{nm_id}" if nm_id else "~")
            )

            payout_amount = as_decimal(record.get("ppvz_for_pay") or record.get("payout_amount") or record.get("forPay"))
            commission_amount = _first_decimal(
                record.get("ppvz_commission"),
                record.get("ppvz_sales_commission"),
                record.get("commission_amount"),
                record.get("ppvzSalesCommission"),
            )
            logistics_amount = _first_decimal(
                record.get("delivery_rub"),
                record.get("rebill_logistic_cost"),
                record.get("logistics_amount"),
                record.get("deliveryService"),
                record.get("rebillLogisticCost"),
            )
            service_amount = _first_decimal(
                record.get("additional_payment"),
                record.get("acquiring_fee"),
                record.get("service_amount"),
                record.get("additionalPayment"),
                record.get("acquiringFee"),
            )
            amount = _first_decimal(
                record.get("amount"),
                record.get("penalty"),
                record.get("deduction"),
                record.get("storage_fee"),
                record.get("additional_payment"),
                record.get("rebill_logistic_cost"),
                record.get("ppvz_reward"),
                record.get("paidStorage"),
                record.get("paidAcceptance"),
                record.get("additionalPayment"),
                record.get("rebillLogisticCost"),
                record.get("ppvzReward"),
            )
            raw_amount_sum += amount or Decimal("0")
            raw_commission_sum += commission_amount or Decimal("0")
            raw_logistics_sum += logistics_amount or Decimal("0")
            raw_services_sum += service_amount or Decimal("0")
            normalized_amount_sum += amount or Decimal("0")
            normalized_commission_sum += commission_amount or Decimal("0")
            normalized_logistics_sum += logistics_amount or Decimal("0")
            normalized_services_sum += service_amount or Decimal("0")

            rows.append(
                {
                    "finance_id": finance_id,
                    "marketplace": "wb",
                    "operation_date": operation_date,
                    "operation_date_source": operation_date_source,
                    "source_operation_date": operation_date,
                    "operation_type": as_str(
                        record.get("doc_type_name")
                        or record.get("docTypeName")
                        or record.get("supplier_oper_name")
                        or record.get("sellerOperName")
                        or record.get("operation_type")
                    ),
                    "order_id": as_str(record.get("srid") or record.get("order_id") or record.get("orderId")),
                    "sale_id": as_str(record.get("saleID") or record.get("sale_id") or record.get("saleId")),
                    "nm_id": nm_id,
                    "offer_id": offer_id,
                    "barcode": as_str(
                        record.get("barcode")
                        or record.get("barcodeId")
                        or record.get("barcode_id")
                        or record.get("shk_id")
                        or record.get("shkId")
                    ),
                    "payout_amount": payout_amount,
                    "commission_amount": commission_amount,
                    "logistics_amount": logistics_amount,
                    "service_amount": service_amount,
                    "amount": amount,
                    "rrd_id": rrd_id_value,
                    "rr_dt": as_datetime(record.get("rr_dt") or record.get("rrDate")),
                    "sale_dt": as_datetime(record.get("sale_dt") or record.get("saleDt")),
                    "order_dt": as_datetime(record.get("order_dt") or record.get("orderDt")),
                    "doc_type_name": as_str(record.get("doc_type_name") or record.get("docTypeName")),
                    "supplier_oper_name": as_str(record.get("supplier_oper_name") or record.get("sellerOperName")),
                    "delivery_rub": as_decimal(record.get("delivery_rub") or record.get("deliveryService")),
                    "rebill_logistic_cost": as_decimal(record.get("rebill_logistic_cost") or record.get("rebillLogisticCost")),
                    "storage_fee": as_decimal(record.get("storage_fee") or record.get("paidStorage")),
                    "acceptance_amount": as_decimal(record.get("acceptance") or record.get("acceptance_amount") or record.get("paidAcceptance")),
                    "penalty_amount": as_decimal(record.get("penalty")),
                    "deduction_amount": as_decimal(record.get("deduction")),
                    "acquiring_fee": as_decimal(record.get("acquiring_fee") or record.get("acquiringFee")),
                    "commission_percent": as_decimal(record.get("commission_percent") or record.get("commissionPercent")),
                    "office_name": as_str(record.get("office_name") or record.get("officeName")),
                    "gi_box_type_name": as_str(record.get("gi_box_type_name") or record.get("giBoxTypeName")),
                    "subject_name": as_str(record.get("subject_name") or record.get("subjectName")),
                    "raw_payload": dict(record),
                    "raw_updated_at": now,
                }
            )

    drop_stats = []
    dropped_rows_total = 0
    for stats in drop_buckets.values():
        dropped_rows_total += int(stats["dropped_rows"] or 0)
        drop_stats.append(stats)
    drop_stats.sort(
        key=lambda item: (
            str(item.get("drop_reason") or ""),
            str(item.get("operation_date_source") or ""),
        )
    )
    return {
        "rows": rows,
        "raw_rows_total": raw_rows_total,
        "normalized_rows_total": len(rows),
        "dropped_rows_total": dropped_rows_total,
        "drop_stats": drop_stats,
        "raw_amount_sum": raw_amount_sum,
        "normalized_amount_sum": normalized_amount_sum,
        "raw_commission_sum": raw_commission_sum,
        "normalized_commission_sum": normalized_commission_sum,
        "raw_logistics_sum": raw_logistics_sum,
        "normalized_logistics_sum": normalized_logistics_sum,
        "raw_services_sum": raw_services_sum,
        "normalized_services_sum": normalized_services_sum,
    }


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


def _pick_finance_id(record: Mapping[str, Any]) -> str | None:
    return as_str(
        record.get("rrd_id")
        or record.get("rrdId")
        or record.get("finance_id")
        or record.get("id")
        or record.get("docNumber")
    )


def _rrd_id_int(record: Mapping[str, Any]) -> int | None:
    raw = record.get("rrd_id") or record.get("rrdId") or record.get("id")
    if raw is None:
        return None
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        return None


def _pick_operation_date(record: Mapping[str, Any]) -> tuple[str | None, datetime | None]:
    for field in (
        "rr_dt",
        "rrDate",
        "sale_dt",
        "saleDt",
        "order_dt",
        "orderDt",
        "date",
        "create_dt",
        "createDate",
        "operation_date",
        "dateFrom",
        "dateTo",
    ):
        if field not in record:
            continue
        parsed = as_datetime(record.get(field))
        if parsed is not None:
            return field, parsed
    return _first_present_operation_date_field(record), None


def _first_present_operation_date_field(record: Mapping[str, Any]) -> str | None:
    for field in (
        "rr_dt",
        "rrDate",
        "sale_dt",
        "saleDt",
        "order_dt",
        "orderDt",
        "date",
        "create_dt",
        "createDate",
        "operation_date",
        "dateFrom",
        "dateTo",
    ):
        if record.get(field) not in (None, ""):
            return field
    return None


def _has_any_operation_date_value(record: Mapping[str, Any]) -> bool:
    return _first_present_operation_date_field(record) is not None


def _register_drop(
    *,
    buckets: dict[tuple[str, str | None], dict[str, Any]],
    reason: str,
    operation_date_source: str | None,
    rrd_id_value: int | None,
) -> None:
    key = (reason, operation_date_source)
    bucket = buckets[key]
    bucket["drop_reason"] = reason
    bucket["operation_date_source"] = operation_date_source
    bucket["dropped_rows"] = int(bucket["dropped_rows"] or 0) + 1
    if rrd_id_value is None:
        return
    current_min = bucket.get("rrd_id_min")
    current_max = bucket.get("rrd_id_max")
    bucket["rrd_id_min"] = rrd_id_value if current_min is None else min(int(current_min), rrd_id_value)
    bucket["rrd_id_max"] = rrd_id_value if current_max is None else max(int(current_max), rrd_id_value)


def _first_decimal(*values: Any) -> Decimal | None:
    fallback: Decimal | None = None
    for value in values:
        parsed = as_decimal(value)
        if parsed is not None:
            if parsed != 0:
                return parsed
            if fallback is None:
                fallback = parsed
    return fallback
