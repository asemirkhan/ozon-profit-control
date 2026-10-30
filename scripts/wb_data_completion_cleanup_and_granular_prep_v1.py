from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.connectors.wb.ads import WbAdsFetchError, fetch_wb_ads_pages
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages
from src.connectors.wb.http import request_json_with_status
from src.connectors.wb.orders import WB_ORDERS_URL, WbOrdersFetchError, _build_attempt_pages as _build_order_attempt_pages
from src.connectors.wb.products import fetch_wb_products_pages
from src.connectors.wb.sales import WB_SALES_URL, WbSalesFetchError, _build_attempt_pages as _build_sale_attempt_pages
from src.connectors.wb.stock import WB_STOCK_URL
from src.loaders.ddl_runner import apply_ddl
from src.loaders.upsert_ads import upsert_fact_ads_spend
from src.loaders.upsert_inventory import upsert_fact_inventory_snapshot
from src.loaders.upsert_wb import (
    upsert_stg_wb_ads,
    upsert_stg_wb_finance,
    upsert_stg_wb_order,
    upsert_stg_wb_product,
    upsert_stg_wb_sale,
    upsert_stg_wb_stock,
)
from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.normalizers.wb.orders_normalizer import normalize_wb_orders
from src.normalizers.wb.products_normalizer import normalize_wb_products
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.normalizers.wb.stock_normalizer import normalize_wb_stock
from src.normalizers.wb.common import as_date
from src.raw_store.writer import write_raw_json
from src.utils.db import get_conn, project_root

ROOT = project_root()
load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
PERIOD_END = date(2026, 3, 26)
PERIOD_START = PERIOD_END - timedelta(days=59)
ALL_DAYS = [PERIOD_START + timedelta(days=idx) for idx in range((PERIOD_END - PERIOD_START).days + 1)]
CORE_SOURCES = ("finance", "ads", "sales", "orders")
ALL_SOURCES = ("products", "finance", "ads", "sales", "orders", "stock")
MANUAL_HOLD_DAYS = {date(2026, 3, 9), date(2026, 3, 11)}
MOSCOW = ZoneInfo("Europe/Moscow")

OUTPUT_DIR = ROOT / "exports" / "wb_data_completion_cleanup_and_granular_prep_v1"
DOC_SUMMARY = ROOT / "artifacts" / "wb_data_completion_summary_v1.md"
DOC_COVERAGE = ROOT / "artifacts" / "wb_source_coverage_v1.md"
DOC_QUALITY = ROOT / "artifacts" / "wb_data_quality_report_v1.md"
DOC_GRANULARITY = ROOT / "artifacts" / "wb_granularity_check_v2.md"
CSV_DAY_STATUS = OUTPUT_DIR / "day_status.csv"
CSV_SOURCE_COVERAGE = OUTPUT_DIR / "source_coverage.csv"
CSV_RECOVERY = OUTPUT_DIR / "recovery_results.csv"
CSV_FINANCE_PASSES = OUTPUT_DIR / "finance_passes.csv"
CSV_QUALITY = OUTPUT_DIR / "quality_checks.csv"
CSV_GRANULARITY = OUTPUT_DIR / "granularity_non_null_counts.csv"


@dataclass
class SourceStatus:
    status: str
    rows: int
    requests: int
    count_429: int
    total_time_minutes: float
    comments: str = ""


@dataclass
class FinancePassStatus:
    day: str
    pass_number: int
    request_count: int
    rows_after_pass: int
    new_rows: int
    growth_ratio: float
    had_429: bool
    duration_minutes: float
    terminal_reason: str


def configure_env() -> None:
    if Path("/etc/ssl/cert.pem").exists() and not os.getenv("WB_CA_BUNDLE"):
        os.environ["WB_CA_BUNDLE"] = "/etc/ssl/cert.pem"
    defaults = {
        "WB_MAX_REQUESTS_PER_SECOND": "0.5",
        "WB_MAX_PARALLEL_REQUESTS_PER_SOURCE": "1",
        "WB_FINANCE_HTTP_MAX_ATTEMPTS": "3",
        "WB_FINANCE_HTTP_RETRY_DELAYS_SEC": "15,45,90",
        "WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS": "1",
        "WB_FINANCE_STABLE_MODE": "1",
        "WB_FINANCE_MIN_PASSES": "3",
        "WB_FINANCE_STABLE_MAX_PASSES": "6",
        "WB_FINANCE_ZERO_GROWTH_PASSES_REQUIRED": "2",
        "WB_FINANCE_MIN_GROWTH_RATIO": "0.02",
        "WB_FINANCE_PASS_PAUSE_SEC": "120",
        "WB_FINANCE_INTER_PAGE_PAUSE_SEC": "50",
        "WB_FINANCE_RATE_LIMIT_PASS_PAUSE_SEC": "150",
        "WB_ORDERS_PAGE_SLEEP_SEC": "5",
    }
    for key, value in defaults.items():
        if not os.getenv(key):
            os.environ[key] = value


def now_moscow_date() -> str:
    return datetime.now(MOSCOW).date().isoformat()


def make_run_id(prefix: str, day_value: date) -> str:
    return f"{prefix}_{day_value.isoformat()}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def sleep_between_sources() -> None:
    time.sleep(5)


def sleep_between_days() -> None:
    time.sleep(20)


def count_429(pages: list[dict[str, Any]]) -> int:
    return sum(
        1
        for page in pages
        if int(page.get("http_status") or 0) == 429 or str(page.get("error_type") or "") == "rate_limit"
    )


def count_finance_requests(pages: list[dict[str, Any]]) -> int:
    values = {int(page.get("finance_request_no") or 0) for page in pages if int(page.get("finance_request_no") or 0) > 0}
    return len(values)


def count_generic_requests(pages: list[dict[str, Any]]) -> int:
    values = {int(page.get("request_page_no") or 0) for page in pages if int(page.get("request_page_no") or 0) > 0}
    return len(values) if values else len(pages)


def _page_dates_for_sales(rows: list[dict[str, Any]]) -> list[date]:
    values: list[date] = []
    for row in rows:
        parsed = as_date(row.get("date") or row.get("saleDate") or row.get("sale_date"))
        if parsed is not None:
            values.append(parsed)
    return values


def _page_dates_for_orders(rows: list[dict[str, Any]]) -> list[date]:
    values: list[date] = []
    for row in rows:
        parsed = as_date(row.get("date") or row.get("createdAt") or row.get("order_date"))
        if parsed is not None:
            values.append(parsed)
    return values


def _page_dates_for_stock(rows: list[dict[str, Any]]) -> list[date]:
    values: list[date] = []
    for row in rows:
        parsed = as_date(row.get("date") or row.get("snapshot_date"))
        if parsed is not None:
            values.append(parsed)
    return values


def fetch_sales_pages_for_day(day_value: date) -> list[dict[str, Any]]:
    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = 0
    page_num = 0
    saw_target_day = False
    while True:
        page_num += 1
        params = {"dateFrom": day_value.isoformat(), "limit": 100, "offset": current_offset}
        diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_SALES_URL,
                headers=headers,
                params=params,
                diagnostics_collector=diagnostics.append,
                retry_after_buffer_sec=5,
                rate_api_category="sales",
                rate_endpoint="/api/v1/supplier/sales",
            )
        except Exception as exc:
            if not diagnostics:
                diagnostics.append(
                    {
                        "attempt_no": 1,
                        "request_started_at": None,
                        "response_finished_at": None,
                        "http_status": None,
                        "response_body_size": 0,
                        "response_content_type": None,
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "unknown",
                        "error_message": str(exc),
                        "success": False,
                        "parsed_response": None,
                    }
                )
            pages.extend(_build_sale_attempt_pages(diagnostics, params, page_num, status_override=None))
            raise WbSalesFetchError(str(exc), diagnostic_pages=pages) from exc
        if not isinstance(raw, list):
            diagnostics.append(
                {
                    "attempt_no": len(diagnostics) + 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": status,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": True,
                    "parse_error": False,
                    "unexpected_shape": True,
                    "error_type": "unexpected_format",
                    "error_message": "WB sales response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(_build_sale_attempt_pages(diagnostics, params, page_num, status_override=status))
            raise WbSalesFetchError("WB sales response must be a JSON list", diagnostic_pages=pages)
        rows = [item for item in raw if isinstance(item, dict)]
        pages.extend(_build_sale_attempt_pages(diagnostics, params, page_num, status_override=status))
        if not rows:
            break
        page_dates = _page_dates_for_sales(rows)
        target_rows = [value for value in page_dates if value == day_value]
        later_rows = [value for value in page_dates if value > day_value]
        if target_rows:
            saw_target_day = True
        current_offset += len(rows)
        if len(rows) < 100:
            break
        if saw_target_day and later_rows:
            break
        time.sleep(5)
    return pages


def fetch_orders_pages_for_day(day_value: date) -> list[dict[str, Any]]:
    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = 0
    page_num = 0
    saw_target_day = False
    while True:
        page_num += 1
        params = {"dateFrom": day_value.isoformat(), "limit": 100, "offset": current_offset}
        diagnostics: list[dict[str, Any]] = []
        try:
            raw, status = request_json_with_status(
                method="GET",
                url=WB_ORDERS_URL,
                headers=headers,
                params=params,
                diagnostics_collector=diagnostics.append,
                retry_after_buffer_sec=5,
                rate_api_category="orders",
                rate_endpoint="/api/v1/supplier/orders",
            )
        except Exception as exc:
            if not diagnostics:
                diagnostics.append(
                    {
                        "attempt_no": 1,
                        "request_started_at": None,
                        "response_finished_at": None,
                        "http_status": None,
                        "response_body_size": 0,
                        "response_content_type": None,
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "unknown",
                        "error_message": str(exc),
                        "success": False,
                        "parsed_response": None,
                    }
                )
            pages.extend(_build_order_attempt_pages(diagnostics, params, page_num, status_override=None, page_cap_reached=False))
            raise WbOrdersFetchError(str(exc), diagnostic_pages=pages) from exc
        if not isinstance(raw, list):
            diagnostics.append(
                {
                    "attempt_no": len(diagnostics) + 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": status,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": True,
                    "parse_error": False,
                    "unexpected_shape": True,
                    "error_type": "unexpected_format",
                    "error_message": "WB orders response must be a JSON list",
                    "success": False,
                    "parsed_response": raw,
                }
            )
            pages.extend(_build_order_attempt_pages(diagnostics, params, page_num, status_override=status, page_cap_reached=False))
            raise WbOrdersFetchError("WB orders response must be a JSON list", diagnostic_pages=pages)
        rows = [item for item in raw if isinstance(item, dict)]
        pages.extend(_build_order_attempt_pages(diagnostics, params, page_num, status_override=status, page_cap_reached=False))
        if not rows:
            break
        page_dates = _page_dates_for_orders(rows)
        target_rows = [value for value in page_dates if value == day_value]
        later_rows = [value for value in page_dates if value > day_value]
        if target_rows:
            saw_target_day = True
        current_offset += len(rows)
        if len(rows) < 100:
            break
        if saw_target_day and later_rows:
            break
        time.sleep(5)
    return pages


def fetch_stock_pages_for_day(day_value: date) -> list[dict[str, Any]]:
    headers = get_wb_headers()
    pages: list[dict[str, Any]] = []
    current_offset = 0
    page_num = 0
    saw_target_day = False
    while True:
        page_num += 1
        params = {"dateFrom": day_value.isoformat(), "limit": 100, "offset": current_offset}
        diagnostics: list[dict[str, Any]] = []
        raw, status = request_json_with_status(
            method="GET",
            url=WB_STOCK_URL,
            headers=headers,
            params=params,
            diagnostics_collector=diagnostics.append,
            retry_after_buffer_sec=5,
            rate_api_category="stock",
            rate_endpoint="/api/v1/supplier/stocks",
        )
        if not isinstance(raw, list):
            raise RuntimeError("WB stock response must be a JSON list")
        rows = [item for item in raw if isinstance(item, dict)]
        pages.append(
            {
                "response": {"stocks": rows},
                "request_params": params,
                "http_status": status,
                "request_page_no": page_num,
                "request_success": True,
            }
        )
        if not rows:
            break
        page_dates = _page_dates_for_stock(rows)
        target_rows = [value for value in page_dates if value == day_value]
        later_rows = [value for value in page_dates if value > day_value]
        if target_rows:
            saw_target_day = True
        current_offset += len(rows)
        if len(rows) < 100:
            break
        if saw_target_day and later_rows:
            break
        time.sleep(5)
    return pages


def write_source_pages(schema: str, source: str, run_id: str, day_value: date, pages: list[dict[str, Any]]) -> None:
    for idx, page in enumerate(pages, start=1):
        payload = page.get("response")
        if not isinstance(payload, dict):
            payload = {"rows": []}
        request_params = page.get("request_params") if isinstance(page.get("request_params"), dict) else None
        http_status = int(page.get("http_status")) if isinstance(page.get("http_status"), int) else None
        meta_extra = {
            "target_day": day_value.isoformat(),
            "request_success": page.get("request_success"),
            "error_type": page.get("error_type"),
        }
        for key in (
            "finance_pass_no",
            "finance_request_no",
            "finance_pass_new_unique_rows",
            "finance_cumulative_unique_rows",
            "finance_terminal_reason",
        ):
            if page.get(key) is not None:
                meta_extra[key] = page.get(key)
        write_raw_json(
            schema=schema,
            source=source,
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status,
            meta_extra=meta_extra,
            file_stem=f"{run_id}_{day_value.isoformat()}_{idx}",
        )


def _map_wb_ads_to_fact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "date": row.get("date"),
            "campaign_id": row.get("campaign_id"),
            "marketplace": "wb",
            "campaign_name": row.get("campaign_name"),
            "campaign_type": row.get("campaign_type"),
            "offer_id": row.get("offer_id") or "~",
            "sku": row.get("nm_id") or "~",
            "product_id": row.get("nm_id") or "~",
            "impressions": row.get("impressions"),
            "clicks": row.get("clicks"),
            "orders_count": row.get("orders_count"),
            "revenue": row.get("revenue"),
            "spend": row.get("spend"),
            "currency": row.get("currency"),
            "raw_updated_at": row.get("raw_updated_at"),
        }
        for row in rows
    ]


def _map_wb_stock_to_fact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "snapshot_date": row.get("snapshot_date"),
            "marketplace": "wb",
            "offer_id": row.get("offer_id") or "~",
            "sku": row.get("barcode") or "~",
            "product_id": row.get("nm_id") or "~",
            "warehouse_name": row.get("warehouse_name") or "~",
            "warehouse_type": row.get("warehouse_type"),
            "stock_total": row.get("stock_total"),
            "stock_reserved": row.get("stock_reserved"),
            "stock_available": row.get("stock_available"),
            "stock_defect": row.get("stock_defect"),
            "raw_updated_at": row.get("raw_updated_at"),
        }
        for row in rows
    ]


def classify_source_status(*, rows: int, error: str | None, count_429_value: int, partial_hint: bool = False) -> str:
    if error and rows == 0:
        return "retry_needed"
    if count_429_value > 0 and rows == 0:
        return "retry_needed"
    if rows == 0 and not error:
        return "empty"
    if error or count_429_value > 0 or partial_hint:
        return "partial"
    return "completed"


def finance_pass_metrics(day_value: date, pages: list[dict[str, Any]]) -> list[FinancePassStatus]:
    metrics: list[FinancePassStatus] = []
    pass_numbers = sorted({int(page.get("finance_pass_no") or 0) for page in pages if int(page.get("finance_pass_no") or 0) > 0})
    previous_rows = 0
    for pass_number in pass_numbers:
        pass_pages = [page for page in pages if int(page.get("finance_pass_no") or 0) == pass_number]
        request_count = len({int(page.get("finance_request_no") or 0) for page in pass_pages if int(page.get("finance_request_no") or 0) > 0})
        rows_after_pass = max(int(page.get("finance_cumulative_unique_rows") or 0) for page in pass_pages) if pass_pages else previous_rows
        new_rows = max(int(page.get("finance_pass_new_unique_rows") or 0) for page in pass_pages) if pass_pages else 0
        growth_ratio = 1.0 if previous_rows <= 0 and new_rows > 0 else (float(new_rows) / float(previous_rows) if previous_rows > 0 else 0.0)
        had_429 = count_429(pass_pages) > 0
        timestamps = [str(page.get("request_started_at") or "") for page in pass_pages if page.get("request_started_at")]
        finished = [str(page.get("request_finished_at") or "") for page in pass_pages if page.get("request_finished_at")]
        duration_minutes = 0.0
        if timestamps and finished:
            try:
                started_at = min(datetime.fromisoformat(item.replace("Z", "+00:00")) for item in timestamps)
                ended_at = max(datetime.fromisoformat(item.replace("Z", "+00:00")) for item in finished)
                duration_minutes = round(max((ended_at - started_at).total_seconds(), 0.0) / 60.0, 2)
            except ValueError:
                duration_minutes = 0.0
        terminal_reason = ""
        for page in reversed(pass_pages):
            if page.get("finance_terminal_reason"):
                terminal_reason = str(page.get("finance_terminal_reason"))
                break
        metrics.append(
            FinancePassStatus(
                day=day_value.isoformat(),
                pass_number=pass_number,
                request_count=request_count,
                rows_after_pass=rows_after_pass,
                new_rows=new_rows,
                growth_ratio=round(growth_ratio, 4),
                had_429=had_429,
                duration_minutes=duration_minutes,
                terminal_reason=terminal_reason,
            )
        )
        previous_rows = rows_after_pass
    return metrics


def run_finance_day(day_value: date) -> tuple[SourceStatus, list[FinancePassStatus]]:
    started = time.monotonic()
    pages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        pages = fetch_wb_finance_pages(day_value.isoformat(), day_value.isoformat(), limit=100, max_pages=100, offset=0)
    except WbFinanceFetchError as exc:
        pages = exc.diagnostic_pages
        error = exc.error_type or str(exc)
    write_source_pages(SCHEMA, "wb_finance", make_run_id("wb_finance_completion", day_value), day_value, pages)
    normalization = build_wb_finance_normalization_result(pages, target_day=day_value)
    rows = normalization["rows"]
    if rows:
        upsert_stg_wb_finance(SCHEMA, rows)
    metrics = finance_pass_metrics(day_value, pages)
    last_terminal_reason = ""
    for page in reversed(pages):
        if page.get("finance_terminal_reason"):
            last_terminal_reason = str(page.get("finance_terminal_reason"))
            break
    if last_terminal_reason == "confirmed_no_growth_after_multi_pass":
        status = "completed_stable"
    elif last_terminal_reason == "confirmed_low_growth_after_multi_pass":
        status = "completed_low_growth"
    elif rows and error:
        status = "partial"
    elif error:
        status = "retry_needed"
    elif not rows:
        status = "empty"
    else:
        status = "partial"
    source_status = SourceStatus(
        status=status,
        rows=len(rows),
        requests=count_finance_requests(pages),
        count_429=count_429(pages),
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        comments=f"terminal_reason={last_terminal_reason or 'none'}" if last_terminal_reason or error else "",
    )
    return source_status, metrics


def run_ads_day(day_value: date) -> SourceStatus:
    started = time.monotonic()
    pages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        pages = fetch_wb_ads_pages(day_value.isoformat(), day_value.isoformat())
    except WbAdsFetchError as exc:
        pages = exc.diagnostic_pages
        error = str(exc)
    write_source_pages(SCHEMA, "wb_ads", make_run_id("wb_ads_completion", day_value), day_value, pages)
    all_rows = normalize_wb_ads(pages)
    rows = [row for row in all_rows if row.get("date") == day_value]
    if rows:
        upsert_stg_wb_ads(SCHEMA, rows)
        upsert_fact_ads_spend(SCHEMA, _map_wb_ads_to_fact(rows))
    requests = len(pages) + 1
    status = classify_source_status(rows=len(rows), error=error, count_429_value=count_429(pages))
    return SourceStatus(
        status=status,
        rows=len(rows),
        requests=requests,
        count_429=count_429(pages),
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        comments="includes campaign-list request in request count",
    )


def run_sales_day(day_value: date) -> SourceStatus:
    started = time.monotonic()
    pages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        pages = fetch_sales_pages_for_day(day_value)
    except WbSalesFetchError as exc:
        pages = exc.diagnostic_pages
        error = str(exc)
    write_source_pages(SCHEMA, "wb_sales", make_run_id("wb_sales_completion", day_value), day_value, pages)
    all_rows = normalize_wb_sales(pages)
    rows = [row for row in all_rows if row.get("sale_date") and row["sale_date"].date() == day_value]
    if rows:
        upsert_stg_wb_sale(SCHEMA, rows)
    partial_hint = any(bool(page.get("page_cap_reached")) for page in pages)
    status = classify_source_status(rows=len(rows), error=error, count_429_value=count_429(pages), partial_hint=partial_hint)
    return SourceStatus(
        status=status,
        rows=len(rows),
        requests=count_generic_requests(pages),
        count_429=count_429(pages),
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        comments="bounded by target day boundary",
    )


def run_orders_day(day_value: date) -> SourceStatus:
    started = time.monotonic()
    pages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        pages = fetch_orders_pages_for_day(day_value)
    except WbOrdersFetchError as exc:
        pages = exc.diagnostic_pages
        error = str(exc)
    write_source_pages(SCHEMA, "wb_orders", make_run_id("wb_orders_completion", day_value), day_value, pages)
    all_rows = normalize_wb_orders(pages)
    rows = [row for row in all_rows if row.get("order_date") and row["order_date"].date() == day_value]
    if rows:
        upsert_stg_wb_order(SCHEMA, rows)
    partial_hint = any(bool(page.get("page_cap_reached")) for page in pages)
    status = classify_source_status(rows=len(rows), error=error, count_429_value=count_429(pages), partial_hint=partial_hint)
    return SourceStatus(
        status=status,
        rows=len(rows),
        requests=count_generic_requests(pages),
        count_429=count_429(pages),
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        comments="bounded by target day boundary",
    )


def run_stock_day(day_value: date) -> SourceStatus:
    started = time.monotonic()
    pages: list[dict[str, Any]] = []
    error: str | None = None
    try:
        pages = fetch_stock_pages_for_day(day_value)
    except Exception as exc:
        error = str(exc)
    if pages:
        write_source_pages(SCHEMA, "wb_stock", make_run_id("wb_stock_completion", day_value), day_value, pages)
    all_rows = normalize_wb_stock(pages, snapshot_date=day_value)
    rows = [row for row in all_rows if row.get("snapshot_date") == day_value]
    if rows:
        upsert_stg_wb_stock(SCHEMA, rows)
        upsert_fact_inventory_snapshot(SCHEMA, _map_wb_stock_to_fact(rows))
    status = classify_source_status(rows=len(rows), error=error, count_429_value=count_429(pages))
    return SourceStatus(
        status=status,
        rows=len(rows),
        requests=count_generic_requests(pages),
        count_429=count_429(pages),
        total_time_minutes=round((time.monotonic() - started) / 60.0, 2),
        comments="best-effort optional stock snapshot",
    )


def raw_root(schema: str, source: str) -> Path:
    raw_map = {
        "products": "wb_products",
        "finance": "wb_finance",
        "ads": "wb_ads",
        "sales": "wb_sales",
        "orders": "wb_orders",
        "stock": "wb_stock",
    }
    return ROOT / "data" / "raw" / schema / raw_map[source]


def build_raw_day_index(schema: str) -> dict[tuple[str, date], dict[str, int]]:
    index: dict[tuple[str, date], dict[str, int]] = {}
    for source in ALL_SOURCES:
        root = raw_root(schema, source)
        for day_value in ALL_DAYS:
            folder = root / day_value.isoformat()
            raw_files = 0
            raw_rows = 0
            if folder.exists():
                for meta_path in folder.rglob("*.meta.json"):
                    raw_files += 1
                    try:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    payload_path = meta_path.with_suffix("")
                    try:
                        payload = json.loads(payload_path.read_text(encoding="utf-8"))
                    except Exception:
                        payload = {}
                    if source == "finance":
                        raw_rows += len(payload.get("finance", [])) if isinstance(payload, dict) else 0
                    elif source == "ads":
                        if isinstance(payload, dict):
                            raw_rows += len(payload.get("ads", []))
                        elif isinstance(payload, list):
                            raw_rows += len(payload)
                    elif source == "sales":
                        if isinstance(payload, dict):
                            raw_rows += len(payload.get("sales", []))
                        elif isinstance(payload, list):
                            raw_rows += len(payload)
                    elif source == "orders":
                        if isinstance(payload, dict):
                            raw_rows += len(payload.get("orders", []))
                        elif isinstance(payload, list):
                            raw_rows += len(payload)
                    elif source == "stock":
                        if isinstance(payload, dict):
                            raw_rows += len(payload.get("stocks", []))
                        elif isinstance(payload, list):
                            raw_rows += len(payload)
                    elif source == "products":
                        if isinstance(payload, dict):
                            cards = payload.get("cards") or (payload.get("result") or {}).get("cards") or []
                            raw_rows += len(cards) if isinstance(cards, list) else 0
            index[(source, day_value)] = {"raw_files": raw_files, "raw_rows": raw_rows}
    return index


def latest_finance_terminal_meta() -> dict[date, dict[str, Any]]:
    latest: dict[date, dict[str, Any]] = {}
    root = raw_root(SCHEMA, "finance")
    if not root.exists():
        return latest
    for meta_path in root.rglob("*.meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        target_day = meta.get("target_day")
        terminal_reason = meta.get("finance_terminal_reason")
        if not target_day or not terminal_reason:
            continue
        try:
            day_value = date.fromisoformat(str(target_day))
        except ValueError:
            continue
        ts_raw = meta.get("timestamp")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else datetime.min.replace(tzinfo=timezone.utc)
        except ValueError:
            ts = datetime.min.replace(tzinfo=timezone.utc)
        current = latest.get(day_value)
        if current is None or ts >= current["timestamp"]:
            latest[day_value] = {
                "timestamp": ts,
                "finance_terminal_reason": terminal_reason,
                "http_status": meta.get("http_status"),
            }
    return latest


def apply_granular_backfill() -> None:
    sql_statements = [
        f"""
        UPDATE {SCHEMA}.stg_wb_product
        SET
          subject_id = COALESCE(subject_id, raw_payload->>'subjectID', raw_payload->>'subjectId'),
          subject_name = COALESCE(subject_name, raw_payload->>'subjectName', raw_payload->>'subject'),
          length_cm = COALESCE(length_cm, NULLIF(raw_payload->'dimensions'->>'length', '')::numeric, NULLIF(raw_payload->>'length', '')::numeric),
          width_cm = COALESCE(width_cm, NULLIF(raw_payload->'dimensions'->>'width', '')::numeric, NULLIF(raw_payload->>'width', '')::numeric),
          height_cm = COALESCE(height_cm, NULLIF(raw_payload->'dimensions'->>'height', '')::numeric, NULLIF(raw_payload->>'height', '')::numeric),
          weight_brutto_kg = COALESCE(weight_brutto_kg, NULLIF(raw_payload->'dimensions'->>'weightBrutto', '')::numeric, NULLIF(raw_payload->>'weightBrutto', '')::numeric, NULLIF(raw_payload->>'weight', '')::numeric),
          characteristics_json = COALESCE(characteristics_json, raw_payload->'characteristics')
        WHERE raw_payload IS NOT NULL
        """,
        f"""
        UPDATE {SCHEMA}.stg_wb_order
        SET
          warehouse_name = COALESCE(warehouse_name, raw_payload->>'warehouseName', raw_payload->>'warehouse_name'),
          warehouse_type = COALESCE(warehouse_type, raw_payload->>'warehouseType', raw_payload->>'warehouse_type'),
          region_name = COALESCE(region_name, raw_payload->>'regionName', raw_payload->>'oblastOkrugName', raw_payload->>'oblast', raw_payload->>'region_name'),
          tech_size = COALESCE(tech_size, raw_payload->>'techSize', raw_payload->>'tech_size'),
          subject_name = COALESCE(subject_name, raw_payload->>'subject', raw_payload->>'subjectName'),
          supplier_article = COALESCE(supplier_article, raw_payload->>'supplierArticle', raw_payload->>'supplier_article')
        WHERE raw_payload IS NOT NULL
        """,
        f"""
        UPDATE {SCHEMA}.stg_wb_sale
        SET
          warehouse_name = COALESCE(warehouse_name, raw_payload->>'warehouseName', raw_payload->>'warehouse_name'),
          region_name = COALESCE(region_name, raw_payload->>'regionName', raw_payload->>'oblastOkrugName', raw_payload->>'oblast', raw_payload->>'region_name'),
          tech_size = COALESCE(tech_size, raw_payload->>'techSize', raw_payload->>'tech_size'),
          subject_name = COALESCE(subject_name, raw_payload->>'subject', raw_payload->>'subjectName'),
          supplier_article = COALESCE(supplier_article, raw_payload->>'supplierArticle', raw_payload->>'supplier_article')
        WHERE raw_payload IS NOT NULL
        """,
        f"""
        UPDATE {SCHEMA}.stg_wb_ads
        SET
          app_type = COALESCE(app_type, raw_payload->>'appType', raw_payload->>'app_type'),
          ctr = COALESCE(ctr, NULLIF(raw_payload->>'ctr', '')::numeric),
          cpc = COALESCE(cpc, NULLIF(raw_payload->>'cpc', '')::numeric),
          cr = COALESCE(cr, NULLIF(raw_payload->>'cr', '')::numeric, NULLIF(raw_payload->>'conversion', '')::numeric),
          atbs = COALESCE(atbs, NULLIF(raw_payload->>'atbs', '')::numeric, NULLIF(raw_payload->>'addToBaskets', '')::numeric)
        WHERE raw_payload IS NOT NULL
        """,
        f"""
        UPDATE {SCHEMA}.stg_wb_finance
        SET
          rrd_id = COALESCE(rrd_id, NULLIF(raw_payload->>'rrd_id', '')::bigint, NULLIF(raw_payload->>'rrdId', '')::bigint, NULLIF(raw_payload->>'id', '')::bigint),
          rr_dt = COALESCE(rr_dt, NULLIF(raw_payload->>'rr_dt', '')::timestamptz),
          sale_dt = COALESCE(sale_dt, NULLIF(raw_payload->>'sale_dt', '')::timestamptz),
          order_dt = COALESCE(order_dt, NULLIF(raw_payload->>'order_dt', '')::timestamptz),
          doc_type_name = COALESCE(doc_type_name, raw_payload->>'doc_type_name'),
          supplier_oper_name = COALESCE(supplier_oper_name, raw_payload->>'supplier_oper_name'),
          delivery_rub = COALESCE(delivery_rub, NULLIF(raw_payload->>'delivery_rub', '')::numeric),
          rebill_logistic_cost = COALESCE(rebill_logistic_cost, NULLIF(raw_payload->>'rebill_logistic_cost', '')::numeric),
          storage_fee = COALESCE(storage_fee, NULLIF(raw_payload->>'storage_fee', '')::numeric),
          acceptance_amount = COALESCE(acceptance_amount, NULLIF(raw_payload->>'acceptance', '')::numeric, NULLIF(raw_payload->>'acceptance_amount', '')::numeric),
          penalty_amount = COALESCE(penalty_amount, NULLIF(raw_payload->>'penalty', '')::numeric),
          deduction_amount = COALESCE(deduction_amount, NULLIF(raw_payload->>'deduction', '')::numeric),
          acquiring_fee = COALESCE(acquiring_fee, NULLIF(raw_payload->>'acquiring_fee', '')::numeric),
          commission_percent = COALESCE(commission_percent, NULLIF(raw_payload->>'commission_percent', '')::numeric),
          office_name = COALESCE(office_name, raw_payload->>'office_name'),
          gi_box_type_name = COALESCE(gi_box_type_name, raw_payload->>'gi_box_type_name'),
          subject_name = COALESCE(subject_name, raw_payload->>'subject_name', raw_payload->>'subjectName')
        WHERE raw_payload IS NOT NULL
        """,
    ]
    with get_conn() as conn, conn.cursor() as cur:
        for statement in sql_statements:
            cur.execute(statement)
        conn.commit()


def load_current_statuses() -> list[dict[str, Any]]:
    raw_index = build_raw_day_index(SCHEMA)
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH days AS (
              SELECT generate_series(%s::date, %s::date, '1 day'::interval)::date AS day
            ),
            finance AS (
              SELECT operation_date::date AS day, count(*)::int AS rows FROM {SCHEMA}.stg_wb_finance GROUP BY 1
            ),
            ads AS (
              SELECT date AS day, count(*)::int AS rows FROM {SCHEMA}.stg_wb_ads GROUP BY 1
            ),
            sales AS (
              SELECT sale_date::date AS day, count(*)::int AS rows FROM {SCHEMA}.stg_wb_sale GROUP BY 1
            ),
            orders AS (
              SELECT order_date::date AS day, count(*)::int AS rows FROM {SCHEMA}.stg_wb_order GROUP BY 1
            ),
            stock AS (
              SELECT snapshot_date AS day, count(*)::int AS rows FROM {SCHEMA}.stg_wb_stock GROUP BY 1
            ),
            fstate AS (
              SELECT day, status, finance_day_complete_flag, terminal_reason_code FROM {SCHEMA}.finance_day_state
            )
            SELECT
              d.day,
              coalesce(finance.rows, 0) AS finance_rows,
              coalesce(ads.rows, 0) AS ads_rows,
              coalesce(sales.rows, 0) AS sales_rows,
              coalesce(orders.rows, 0) AS orders_rows,
              coalesce(stock.rows, 0) AS stock_rows,
              fstate.status AS finance_state,
              fstate.finance_day_complete_flag,
              fstate.terminal_reason_code
            FROM days d
            LEFT JOIN finance USING(day)
            LEFT JOIN ads USING(day)
            LEFT JOIN sales USING(day)
            LEFT JOIN orders USING(day)
            LEFT JOIN stock USING(day)
            LEFT JOIN fstate USING(day)
            ORDER BY d.day
            """,
            (PERIOD_START, PERIOD_END),
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        for source in ALL_SOURCES:
            if source == "products":
                continue
            info = raw_index.get((source, row["day"]), {"raw_files": 0, "raw_rows": 0})
            row[f"{source}_raw_files"] = int(info["raw_files"])
            row[f"{source}_raw_rows"] = int(info["raw_rows"])
    return rows


def upsert_finance_day_state(*, day_value: date, status: str, complete: bool, analytic_usable: bool, raw_presence: bool, stg_presence: bool, rows_done: int, http_429_count: int, terminal_reason_code: str | None, reason_code: str | None) -> None:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.finance_day_state (
              day, status, raw_presence_flag, stg_presence_flag, finance_day_complete_flag,
              final_analytic_usable_flag, last_resume_rrdid, previous_resume_rrdid, pages_done_total,
              rows_done_total, attempts_total, no_progress_attempts, repeated_page_attempts,
              http_429_count_total, cumulative_wait_sec, last_progress_at, last_success_at,
              last_http_code, last_rows_delta, last_page_checksum, suspected_trap_type,
              suspected_trap_reason, reason_code, source_pressure_flag,
              reconciliation_exception_explained, reconciliation_exception_reason, updated_at,
              terminal_reason_code, terminal_decision_at, terminal_decision_basis,
              terminal_confidence_level, recheck_required_flag, alternative_strategy_exhausted_flag
            ) VALUES (
              %s, %s, %s, %s, %s,
              %s, 0, 0, 0,
              %s, 1, 0, 0,
              %s, 0, now(), CASE WHEN %s::boolean THEN now() ELSE NULL END,
              NULL, %s, NULL, NULL,
              NULL, %s, %s,
              FALSE, NULL, now(),
              %s, CASE WHEN %s::text IS NOT NULL THEN now() ELSE NULL END, %s,
              %s, %s, FALSE
            )
            ON CONFLICT (day) DO UPDATE SET
              status = CASE
                WHEN {SCHEMA}.finance_day_state.final_analytic_usable_flag
                     AND NOT EXCLUDED.final_analytic_usable_flag
                  THEN {SCHEMA}.finance_day_state.status
                ELSE EXCLUDED.status
              END,
              raw_presence_flag = {SCHEMA}.finance_day_state.raw_presence_flag OR EXCLUDED.raw_presence_flag,
              stg_presence_flag = {SCHEMA}.finance_day_state.stg_presence_flag OR EXCLUDED.stg_presence_flag,
              finance_day_complete_flag = {SCHEMA}.finance_day_state.finance_day_complete_flag OR EXCLUDED.finance_day_complete_flag,
              final_analytic_usable_flag = {SCHEMA}.finance_day_state.final_analytic_usable_flag OR EXCLUDED.final_analytic_usable_flag,
              rows_done_total = GREATEST({SCHEMA}.finance_day_state.rows_done_total, EXCLUDED.rows_done_total),
              attempts_total = GREATEST({SCHEMA}.finance_day_state.attempts_total, EXCLUDED.attempts_total),
              http_429_count_total = GREATEST({SCHEMA}.finance_day_state.http_429_count_total, EXCLUDED.http_429_count_total),
              last_progress_at = EXCLUDED.last_progress_at,
              last_success_at = COALESCE(EXCLUDED.last_success_at, {SCHEMA}.finance_day_state.last_success_at),
              last_rows_delta = EXCLUDED.last_rows_delta,
              reason_code = CASE
                WHEN {SCHEMA}.finance_day_state.final_analytic_usable_flag
                     AND NOT EXCLUDED.final_analytic_usable_flag
                  THEN {SCHEMA}.finance_day_state.reason_code
                ELSE EXCLUDED.reason_code
              END,
              source_pressure_flag = CASE
                WHEN {SCHEMA}.finance_day_state.final_analytic_usable_flag THEN FALSE
                ELSE EXCLUDED.source_pressure_flag
              END,
              terminal_reason_code = COALESCE(
                CASE WHEN EXCLUDED.final_analytic_usable_flag THEN EXCLUDED.terminal_reason_code END,
                {SCHEMA}.finance_day_state.terminal_reason_code
              ),
              terminal_decision_at = COALESCE(
                CASE WHEN EXCLUDED.final_analytic_usable_flag THEN EXCLUDED.terminal_decision_at END,
                {SCHEMA}.finance_day_state.terminal_decision_at
              ),
              terminal_decision_basis = COALESCE(
                CASE WHEN EXCLUDED.final_analytic_usable_flag THEN EXCLUDED.terminal_decision_basis END,
                {SCHEMA}.finance_day_state.terminal_decision_basis
              ),
              terminal_confidence_level = COALESCE(
                CASE WHEN EXCLUDED.final_analytic_usable_flag THEN EXCLUDED.terminal_confidence_level END,
                {SCHEMA}.finance_day_state.terminal_confidence_level
              ),
              recheck_required_flag = CASE
                WHEN {SCHEMA}.finance_day_state.final_analytic_usable_flag THEN FALSE
                ELSE EXCLUDED.recheck_required_flag
              END,
              updated_at = now()
            """,
            (
                day_value,
                status,
                raw_presence,
                stg_presence,
                complete,
                analytic_usable,
                rows_done,
                http_429_count,
                complete,
                rows_done,
                reason_code,
                status == "started_but_not_finished",
                terminal_reason_code,
                terminal_reason_code,
                "stable_mode_v2" if complete else "cleanup_or_recovery",
                "high" if complete else "medium",
                not complete,
            ),
        )
        conn.commit()


def upsert_queue_state(*, source: str, day_value: date, state: str, attempts: int = 0, last_http_status: int | None = None, last_error_type: str | None = None, last_error: str | None = None, day_close_reason: str | None = None) -> None:
    priority_map = {"finance": 10, "ads": 20, "sales": 30, "orders": 40, "stock": 50}
    lane = "quiet_lane"
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {SCHEMA}.ingestion_backfill_queue (
              schema_name, marketplace, source, target_date, lane, priority, state, attempts, max_attempts,
              retry_after_ts, last_attempt_at, last_http_status, last_error_type, last_error, rate_limit_retry,
              rate_limit_limit, rate_limit_reset, run_id, created_at, updated_at, worker_id, leased_at,
              lease_expires_at, heartbeat_at, order_resume_offset, order_pages_done, order_rows_done,
              order_last_progress_at, order_no_progress_passes, day_close_reason, finance_resume_rrdid,
              finance_pages_done, finance_rows_done, finance_last_progress_at, finance_no_progress_passes
            ) VALUES (
              %s, 'wb', %s, %s, %s, %s, %s, %s, 20,
              NULL, now(), %s, %s, %s, NULL,
              NULL, NULL, NULL, now(), now(), NULL, NULL,
              NULL, NULL, 0, 0, 0,
              NULL, 0, %s, 0,
              0, 0, NULL, 0
            )
            ON CONFLICT (schema_name, marketplace, source, target_date) DO UPDATE SET
              lane = EXCLUDED.lane,
              priority = EXCLUDED.priority,
              state = EXCLUDED.state,
              attempts = GREATEST({SCHEMA}.ingestion_backfill_queue.attempts, EXCLUDED.attempts),
              last_attempt_at = now(),
              last_http_status = EXCLUDED.last_http_status,
              last_error_type = EXCLUDED.last_error_type,
              last_error = EXCLUDED.last_error,
              day_close_reason = EXCLUDED.day_close_reason,
              updated_at = now()
            """,
            (
                SCHEMA,
                source,
                day_value,
                lane,
                priority_map.get(source, 90),
                state,
                attempts,
                last_http_status,
                last_error_type,
                last_error,
                day_close_reason,
            ),
        )
        conn.commit()


def reconcile_existing_states() -> dict[str, int]:
    stats = Counter()
    terminal_meta = latest_finance_terminal_meta()
    current = load_current_statuses()
    for row in current:
        day_value = row["day"]
        finance_rows = int(row["finance_rows"] or 0)
        finance_raw_files = int(row["finance_raw_files"] or 0)
        current_state = row.get("finance_state")
        terminal_meta_row = terminal_meta.get(day_value)
        if day_value in MANUAL_HOLD_DAYS:
            upsert_finance_day_state(
                day_value=day_value,
                status="manual_hold",
                complete=False,
                analytic_usable=False,
                raw_presence=finance_raw_files > 0,
                stg_presence=finance_rows > 0,
                rows_done=finance_rows,
                http_429_count=0,
                terminal_reason_code="manual_hold",
                reason_code="manual_hold",
            )
            upsert_queue_state(source="finance", day_value=day_value, state="manual_review", attempts=1, day_close_reason="manual_hold")
            stats["manual_hold_reconciled"] += 1
            continue
        if terminal_meta_row and finance_rows > 0:
            terminal_reason = str(terminal_meta_row["finance_terminal_reason"])
            upsert_finance_day_state(
                day_value=day_value,
                status="success",
                complete=True,
                analytic_usable=True,
                raw_presence=finance_raw_files > 0,
                stg_presence=True,
                rows_done=finance_rows,
                http_429_count=0,
                terminal_reason_code=terminal_reason,
                reason_code="reconciled_from_raw_terminal_meta",
            )
            upsert_queue_state(source="finance", day_value=day_value, state="success", attempts=1, day_close_reason=terminal_reason)
            if current_state != "success":
                stats["finance_success_state_fixed"] += 1
            continue
        if finance_rows > 0 and current_state not in {"success", "manual_hold"}:
            upsert_finance_day_state(
                day_value=day_value,
                status="started_but_not_finished",
                complete=False,
                analytic_usable=False,
                raw_presence=finance_raw_files > 0,
                stg_presence=True,
                rows_done=finance_rows,
                http_429_count=0,
                terminal_reason_code=None,
                reason_code="stg_present_without_terminal_confirmation",
            )
            upsert_queue_state(source="finance", day_value=day_value, state="partial_progress", attempts=1, day_close_reason="stg_present_without_terminal_confirmation")
            stats["finance_partial_state_fixed"] += 1
        elif finance_rows == 0 and finance_raw_files > 0 and current_state not in {"manual_hold", "no_data_confirmed"}:
            upsert_queue_state(source="finance", day_value=day_value, state="retry", attempts=1, day_close_reason="raw_seen_without_stg")
            stats["finance_retry_queue_fixed"] += 1

    for source in ("ads", "sales", "orders"):
        key = f"{source}_rows"
        for row in current:
            day_value = row["day"]
            rows = int(row[key] or 0)
            raw_files = int(row[f"{source}_raw_files"] or 0)
            if rows > 0:
                upsert_queue_state(source=source, day_value=day_value, state="success", attempts=1, day_close_reason="stg_rows_present")
                stats[f"{source}_success_queue_fixed"] += 1
            elif raw_files > 0:
                upsert_queue_state(source=source, day_value=day_value, state="retry", attempts=1, day_close_reason="raw_seen_without_stg")
                stats[f"{source}_retry_queue_fixed"] += 1
    return dict(stats)


def recover_products_snapshot() -> int:
    pages = fetch_wb_products_pages(limit=100, max_pages=5)
    rows = normalize_wb_products(pages)
    if rows:
        upsert_stg_wb_product(SCHEMA, rows)
    run_id = make_run_id("wb_products_completion", PERIOD_END)
    write_source_pages(SCHEMA, "wb_products", run_id, PERIOD_END, pages)
    return len(rows)


def determine_targets(status_rows: list[dict[str, Any]]) -> dict[str, list[date]]:
    targets = {source: [] for source in CORE_SOURCES}
    for row in status_rows:
        day_value = row["day"]
        if day_value in MANUAL_HOLD_DAYS:
            if int(row["ads_rows"] or 0) == 0:
                targets["ads"].append(day_value)
            if int(row["sales_rows"] or 0) == 0:
                targets["sales"].append(day_value)
            if int(row["orders_rows"] or 0) == 0:
                targets["orders"].append(day_value)
            continue
        if row.get("finance_state") != "success":
            targets["finance"].append(day_value)
        if int(row["ads_rows"] or 0) == 0:
            targets["ads"].append(day_value)
        if int(row["sales_rows"] or 0) == 0:
            targets["sales"].append(day_value)
        if int(row["orders_rows"] or 0) == 0:
            targets["orders"].append(day_value)
    return {source: sorted(set(days)) for source, days in targets.items()}


def run_recovery(targets: dict[str, list[date]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    recovery_rows: list[dict[str, Any]] = []
    finance_pass_rows: list[dict[str, Any]] = []
    target_days = sorted(set(day for days in targets.values() for day in days))
    for idx, day_value in enumerate(target_days, start=1):
        print(f"[recovery] day {idx}/{len(target_days)} {day_value.isoformat()}", flush=True)
        attempted_any = False
        if day_value in targets["finance"]:
            print(f"[recovery] finance start {day_value.isoformat()}", flush=True)
            attempted_any = True
            finance_status, finance_metrics = run_finance_day(day_value)
            print(
                f"[recovery] finance done {day_value.isoformat()} status={finance_status.status} rows={finance_status.rows} requests={finance_status.requests} 429={finance_status.count_429}",
                flush=True,
            )
            terminal_reason = ""
            if finance_status.comments.startswith("terminal_reason="):
                terminal_reason = finance_status.comments.split("=", 1)[1]
            if finance_status.status in {"completed_stable", "completed_low_growth"}:
                upsert_finance_day_state(
                    day_value=day_value,
                    status="success",
                    complete=True,
                    analytic_usable=True,
                    raw_presence=True,
                    stg_presence=finance_status.rows > 0,
                    rows_done=finance_status.rows,
                    http_429_count=finance_status.count_429,
                    terminal_reason_code=terminal_reason or "confirmed_no_growth_after_multi_pass",
                    reason_code="stable_mode_v2_recovery",
                )
                upsert_queue_state(source="finance", day_value=day_value, state="success", attempts=1, day_close_reason=terminal_reason or finance_status.status)
            elif finance_status.status == "empty":
                upsert_finance_day_state(
                    day_value=day_value,
                    status="no_data_confirmed",
                    complete=False,
                    analytic_usable=False,
                    raw_presence=True,
                    stg_presence=False,
                    rows_done=0,
                    http_429_count=finance_status.count_429,
                    terminal_reason_code="confirmed_empty_after_multi_pass",
                    reason_code="stable_mode_v2_recovery",
                )
                upsert_queue_state(source="finance", day_value=day_value, state="target_empty", attempts=1, day_close_reason="confirmed_empty_after_multi_pass")
            else:
                upsert_finance_day_state(
                    day_value=day_value,
                    status="started_but_not_finished",
                    complete=False,
                    analytic_usable=False,
                    raw_presence=True,
                    stg_presence=finance_status.rows > 0,
                    rows_done=finance_status.rows,
                    http_429_count=finance_status.count_429,
                    terminal_reason_code=terminal_reason or None,
                    reason_code="stable_mode_v2_partial_or_retry",
                )
                queue_state = "partial_progress" if finance_status.rows > 0 else "retry"
                upsert_queue_state(source="finance", day_value=day_value, state=queue_state, attempts=1, last_http_status=429 if finance_status.count_429 else None, last_error_type=finance_status.status, last_error=finance_status.comments or None, day_close_reason=terminal_reason or finance_status.status)
            recovery_rows.append(
                {
                    "day": day_value.isoformat(),
                    "source": "finance",
                    "status": finance_status.status,
                    "rows": finance_status.rows,
                    "requests": finance_status.requests,
                    "count_429": finance_status.count_429,
                    "time_minutes": finance_status.total_time_minutes,
                    "comments": finance_status.comments,
                }
            )
            for metric in finance_metrics:
                finance_pass_rows.append(metric.__dict__)
            sleep_between_sources()
        if day_value in targets["ads"]:
            print(f"[recovery] ads start {day_value.isoformat()}", flush=True)
            attempted_any = True
            status = run_ads_day(day_value)
            print(f"[recovery] ads done {day_value.isoformat()} status={status.status} rows={status.rows}", flush=True)
            queue_state = "success" if status.rows > 0 and status.status == "completed" else ("target_empty" if status.status == "empty" else "retry")
            upsert_queue_state(source="ads", day_value=day_value, state=queue_state, attempts=1, last_http_status=429 if status.count_429 else None, last_error_type=status.status if status.status != "completed" else None, last_error=status.comments or None, day_close_reason=status.status)
            recovery_rows.append(
                {
                    "day": day_value.isoformat(),
                    "source": "ads",
                    "status": status.status,
                    "rows": status.rows,
                    "requests": status.requests,
                    "count_429": status.count_429,
                    "time_minutes": status.total_time_minutes,
                    "comments": status.comments,
                }
            )
            sleep_between_sources()
        if day_value in targets["sales"]:
            print(f"[recovery] sales start {day_value.isoformat()}", flush=True)
            attempted_any = True
            status = run_sales_day(day_value)
            print(f"[recovery] sales done {day_value.isoformat()} status={status.status} rows={status.rows}", flush=True)
            queue_state = "success" if status.rows > 0 and status.status == "completed" else ("target_empty" if status.status == "empty" else "retry")
            upsert_queue_state(source="sales", day_value=day_value, state=queue_state, attempts=1, last_http_status=429 if status.count_429 else None, last_error_type=status.status if status.status != "completed" else None, last_error=status.comments or None, day_close_reason=status.status)
            recovery_rows.append(
                {
                    "day": day_value.isoformat(),
                    "source": "sales",
                    "status": status.status,
                    "rows": status.rows,
                    "requests": status.requests,
                    "count_429": status.count_429,
                    "time_minutes": status.total_time_minutes,
                    "comments": status.comments,
                }
            )
            sleep_between_sources()
        if day_value in targets["orders"]:
            print(f"[recovery] orders start {day_value.isoformat()}", flush=True)
            attempted_any = True
            status = run_orders_day(day_value)
            print(f"[recovery] orders done {day_value.isoformat()} status={status.status} rows={status.rows}", flush=True)
            queue_state = "success" if status.rows > 0 and status.status == "completed" else ("target_empty" if status.status == "empty" else "retry")
            upsert_queue_state(source="orders", day_value=day_value, state=queue_state, attempts=1, last_http_status=429 if status.count_429 else None, last_error_type=status.status if status.status != "completed" else None, last_error=status.comments or None, day_close_reason=status.status)
            recovery_rows.append(
                {
                    "day": day_value.isoformat(),
                    "source": "orders",
                    "status": status.status,
                    "rows": status.rows,
                    "requests": status.requests,
                    "count_429": status.count_429,
                    "time_minutes": status.total_time_minutes,
                    "comments": status.comments,
                }
            )
            sleep_between_sources()
        if attempted_any and idx < len(target_days):
            sleep_between_days()
    return recovery_rows, finance_pass_rows


def build_source_coverage(status_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    coverage_rows: list[dict[str, Any]] = []
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT source, target_date, state
            FROM {SCHEMA}.ingestion_backfill_queue
            WHERE schema_name = %s AND marketplace = 'wb' AND target_date BETWEEN %s AND %s
            """,
            (SCHEMA, PERIOD_START, PERIOD_END),
        )
        queue_rows = [dict(row) for row in cur.fetchall()]
    queue_map = {(row["source"], row["target_date"]): row["state"] for row in queue_rows}
    for source in CORE_SOURCES + ("stock",):
        days_with_data = 0
        full_days = 0
        partial_days = 0
        empty_days = 0
        unresolved_days = 0
        for row in status_rows:
            stg_rows = int(row[f"{source}_rows"] or 0) if source != "stock" else int(row["stock_rows"] or 0)
            queue_state = queue_map.get((source, row["day"]))
            if stg_rows > 0:
                days_with_data += 1
            if source == "finance":
                if row.get("finance_state") == "success":
                    full_days += 1
                elif row.get("finance_state") == "manual_hold":
                    empty_days += 1
                elif stg_rows > 0:
                    partial_days += 1
                else:
                    unresolved_days += 1
            else:
                if stg_rows > 0:
                    full_days += 1
                elif queue_state == "target_empty":
                    empty_days += 1
                elif queue_state in {"retry", "partial_progress"}:
                    partial_days += 1
                else:
                    unresolved_days += 1
        coverage_rows.append(
            {
                "source": source,
                "days_with_data": days_with_data,
                "full_days": full_days,
                "partial_days": partial_days,
                "empty_days": empty_days,
                "unresolved_days": unresolved_days,
            }
        )
    return coverage_rows


def build_quality_checks(before_rows: list[dict[str, Any]], after_rows: list[dict[str, Any]], cleanup_stats: dict[str, int], recovered_products: int, recovery_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    before_map = {row["day"]: row for row in before_rows}
    after_map = {row["day"]: row for row in after_rows}
    finance_false_closed_before = 0
    finance_false_closed_after = 0
    stale_started_before = 0
    stale_started_after = 0
    raw_stg_gaps_after = Counter()
    for day_value, row in before_map.items():
        if int(row["finance_rows"] or 0) == 100 and row.get("finance_state") != "success":
            finance_false_closed_before += 1
        if row.get("finance_state") == "started_but_not_finished" and int(row["finance_rows"] or 0) > 0:
            stale_started_before += 1
    for day_value, row in after_map.items():
        if int(row["finance_rows"] or 0) == 100 and row.get("finance_state") != "success":
            finance_false_closed_after += 1
        if row.get("finance_state") == "started_but_not_finished" and int(row["finance_rows"] or 0) > 0:
            stale_started_after += 1
        for source in CORE_SOURCES:
            if int(row[f"{source}_raw_files"] or 0) > 0 and int(row[f"{source}_rows"] or 0) == 0:
                raw_stg_gaps_after[source] += 1
    checks.append({"check": "cleanup_stats_json", "value": json.dumps(cleanup_stats, ensure_ascii=False, sort_keys=True)})
    checks.append({"check": "recovered_products_rows", "value": recovered_products})
    checks.append({"check": "recovery_source_days_attempted", "value": len(recovery_rows)})
    checks.append({"check": "finance_false_closed_candidate_days_before", "value": finance_false_closed_before})
    checks.append({"check": "finance_false_closed_candidate_days_after", "value": finance_false_closed_after})
    checks.append({"check": "finance_started_but_not_finished_with_rows_before", "value": stale_started_before})
    checks.append({"check": "finance_started_but_not_finished_with_rows_after", "value": stale_started_after})
    for source, value in sorted(raw_stg_gaps_after.items()):
        checks.append({"check": f"{source}_raw_present_but_stg_absent_days_after", "value": value})
    return checks


def build_granularity_counts() -> list[dict[str, Any]]:
    checks = [
        ("stg_wb_product", "nm_id", "subject_id"),
        ("stg_wb_product", "nm_id", "length_cm"),
        ("stg_wb_product", "nm_id", "weight_brutto_kg"),
        ("stg_wb_order", "order_id", "warehouse_name"),
        ("stg_wb_order", "order_id", "region_name"),
        ("stg_wb_order", "order_id", "supplier_article"),
        ("stg_wb_sale", "sale_id", "warehouse_name"),
        ("stg_wb_sale", "sale_id", "tech_size"),
        ("stg_wb_finance", "finance_id", "rrd_id"),
        ("stg_wb_finance", "finance_id", "delivery_rub"),
        ("stg_wb_finance", "finance_id", "storage_fee"),
        ("stg_wb_finance", "finance_id", "office_name"),
        ("stg_wb_ads", "campaign_id", "app_type"),
        ("stg_wb_ads", "campaign_id", "ctr"),
        ("stg_wb_ads", "campaign_id", "cpc"),
        ("stg_wb_ads", "campaign_id", "cr"),
        ("stg_wb_stock", "offer_id", "warehouse_name"),
    ]
    rows: list[dict[str, Any]] = []
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        for table_name, pk_field, field_name in checks:
            cur.execute(
                f"""
                SELECT
                  %s AS table_name,
                  %s AS pk_field,
                  %s AS field_name,
                  COUNT(*)::bigint AS total_rows,
                  COUNT(*) FILTER (WHERE {field_name} IS NOT NULL)::bigint AS non_null_rows
                FROM {SCHEMA}.{table_name}
                """,
                (table_name, pk_field, field_name),
            )
            rows.append(dict(cur.fetchone()))
    return rows


def day_completion_category(row: dict[str, Any]) -> str:
    finance_ready = row.get("finance_state") == "success"
    secondary = all(int(row[f"{source}_rows"] or 0) > 0 for source in ("ads", "sales", "orders"))
    any_core = any(int(row[f"{source}_rows"] or 0) > 0 for source in CORE_SOURCES)
    if finance_ready and secondary:
        return "completed_fully"
    if any_core:
        return "partial"
    return "unusable"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_summary(after_rows: list[dict[str, Any]], recovery_rows: list[dict[str, Any]], cleanup_stats: dict[str, int]) -> str:
    counts = Counter(day_completion_category(row) for row in after_rows)
    lines = [
        "# WB Data Completion Summary v1",
        "",
        f"Date: {now_moscow_date()}",
        f"Schema: `{SCHEMA}`",
        f"Period: `{PERIOD_START.isoformat()} .. {PERIOD_END.isoformat()}`",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total days | {len(after_rows)} |",
        f"| Completed fully | {counts['completed_fully']} |",
        f"| Partial | {counts['partial']} |",
        f"| Unusable | {counts['unusable']} |",
        f"| Recovery source-days attempted | {len(recovery_rows)} |",
        f"| Cleanup finance success fixes | {cleanup_stats.get('finance_success_state_fixed', 0)} |",
        f"| Cleanup finance partial fixes | {cleanup_stats.get('finance_partial_state_fixed', 0)} |",
        "",
        "## Day Categories",
        "",
        "| Day | Category | Finance state | Finance rows | Ads rows | Sales rows | Orders rows |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for row in after_rows:
        lines.append(
            f"| {row['day'].isoformat()} | {day_completion_category(row)} | {row.get('finance_state') or ''} | "
            f"{row['finance_rows']} | {row['ads_rows']} | {row['sales_rows']} | {row['orders_rows']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_coverage(coverage_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# WB Source Coverage v1",
        "",
        f"Date: {now_moscow_date()}",
        f"Schema: `{SCHEMA}`",
        "",
        "| Source | Days with data | Full days | Partial days | Empty days | Unresolved days |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in coverage_rows:
        lines.append(
            f"| {row['source']} | {row['days_with_data']} | {row['full_days']} | {row['partial_days']} | {row['empty_days']} | {row['unresolved_days']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_quality(quality_rows: list[dict[str, Any]], recovery_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# WB Data Quality Report v1",
        "",
        f"Date: {now_moscow_date()}",
        f"Schema: `{SCHEMA}`",
        "",
        "## Checks",
        "",
        "| Check | Value |",
        "| --- | --- |",
    ]
    for row in quality_rows:
        lines.append(f"| {row['check']} | {row['value']} |")
    lines.extend(
        [
            "",
            "## Recovery Attempts",
            "",
            "| Day | Source | Status | Rows | Requests | 429 | Time minutes | Comments |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for row in recovery_rows:
        lines.append(
            f"| {row['day']} | {row['source']} | {row['status']} | {row['rows']} | {row['requests']} | {row['count_429']} | {row['time_minutes']} | {row['comments']} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_granularity(granularity_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# WB Granularity Check v2",
        "",
        f"Date: {now_moscow_date()}",
        f"Schema: `{SCHEMA}`",
        "",
        "Row-level staging is preserved. This check confirms which typed fields are now materially populated after DDL expansion and raw-payload backfill.",
        "",
        "| Table | Field | Total rows | Non-null rows |",
        "| --- | --- | ---: | ---: |",
    ]
    for row in granularity_rows:
        lines.append(f"| {row['table_name']} | {row['field_name']} | {row['total_rows']} | {row['non_null_rows']} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    configure_env()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    skip_recovery = (os.getenv("WB_SKIP_RECOVERY") or "").strip().lower() in {"1", "true", "yes", "on"}

    print("[setup] apply ddl", flush=True)
    apply_ddl(schema=SCHEMA, ddl_path="src/loaders/ddl.sql")
    print("[setup] recover products snapshot", flush=True)
    recovered_products = recover_products_snapshot()
    print(f"[setup] recovered products rows={recovered_products}", flush=True)
    print("[setup] granular backfill pass 1", flush=True)
    apply_granular_backfill()

    print("[setup] load current statuses", flush=True)
    before_rows = load_current_statuses()
    print("[setup] reconcile existing states", flush=True)
    cleanup_stats = reconcile_existing_states()
    print(f"[setup] cleanup stats={json.dumps(cleanup_stats, ensure_ascii=False, sort_keys=True)}", flush=True)
    before_targets = determine_targets(before_rows)
    print(
        "[setup] targets="
        + json.dumps({source: len(days) for source, days in before_targets.items()}, ensure_ascii=False, sort_keys=True),
        flush=True,
    )
    recovery_rows: list[dict[str, Any]] = []
    finance_pass_rows: list[dict[str, Any]] = []
    if not skip_recovery:
        recovery_rows, finance_pass_rows = run_recovery(before_targets)
    else:
        print("[setup] skip recovery enabled", flush=True)
    print("[finalize] granular backfill pass 2", flush=True)
    apply_granular_backfill()
    print("[finalize] load final statuses", flush=True)
    after_rows = load_current_statuses()

    print("[finalize] build reports", flush=True)
    coverage_rows = build_source_coverage(after_rows)
    quality_rows = build_quality_checks(before_rows, after_rows, cleanup_stats, recovered_products, recovery_rows)
    granularity_rows = build_granularity_counts()

    day_status_rows = []
    for row in after_rows:
        out = {"day": row["day"].isoformat(), "category": day_completion_category(row)}
        for key, value in row.items():
            if key == "day":
                continue
            out[key] = value.isoformat() if isinstance(value, date) else value
        day_status_rows.append(out)

    write_csv(CSV_DAY_STATUS, day_status_rows)
    write_csv(CSV_SOURCE_COVERAGE, coverage_rows)
    write_csv(CSV_RECOVERY, recovery_rows)
    write_csv(CSV_FINANCE_PASSES, finance_pass_rows)
    write_csv(CSV_QUALITY, quality_rows)
    write_csv(CSV_GRANULARITY, granularity_rows)

    DOC_SUMMARY.write_text(render_summary(after_rows, recovery_rows, cleanup_stats), encoding="utf-8")
    DOC_COVERAGE.write_text(render_coverage(coverage_rows), encoding="utf-8")
    DOC_QUALITY.write_text(render_quality(quality_rows, recovery_rows), encoding="utf-8")
    DOC_GRANULARITY.write_text(render_granularity(granularity_rows), encoding="utf-8")
    print("[done] reports written", flush=True)


if __name__ == "__main__":
    main()
