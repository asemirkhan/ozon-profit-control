from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from dotenv import load_dotenv

from src.connectors.ozon.auth import MissingOzonCredentialsError
from src.connectors.ozon.ads import fetch_ads_pages
from src.connectors.ozon.inventory import fetch_inventory_pages
from src.connectors.ozon.postings_fbs import fetch_postings_fbs_pages
from src.connectors.ozon.products import fetch_products_raw
from src.connectors.ozon.transactions import fetch_transactions_pages
from src.connectors.wb.ads import fetch_wb_ads_pages
from src.connectors.wb.auth import MissingWbCredentialsError
from src.connectors.wb.finance import fetch_wb_finance_pages
from src.connectors.wb.orders import fetch_wb_orders_pages
from src.connectors.wb.products import fetch_wb_products_pages
from src.connectors.wb.sales import fetch_wb_sales_pages
from src.connectors.wb.stock import fetch_wb_stock_pages
from src.ingestion.runner import (
    run_ingestion,
    run_wb_backfill_worker,
    seed_wb_backfill_queue,
)
from src.loaders.ddl_runner import apply_ddl
from src.loaders.upsert_ads import upsert_fact_ads_spend
from src.loaders.upsert_dim_product import upsert_dim_product
from src.loaders.upsert_inventory import upsert_fact_inventory_snapshot
from src.loaders.upsert_postings import (
    upsert_stg_posting,
    upsert_stg_posting_item,
    upsert_stg_posting_item_financial,
)
from src.loaders.upsert_transactions import (
    upsert_stg_transaction,
    upsert_stg_transaction_item,
    upsert_stg_transaction_service,
)
from src.loaders.upsert_wb import (
    upsert_stg_wb_ads,
    upsert_stg_wb_finance,
    upsert_stg_wb_order,
    upsert_stg_wb_product,
    upsert_stg_wb_sale,
    upsert_stg_wb_stock,
)
from src.marts.build_marts import build_marts
from src.normalizers.ozon.ads_normalizer import normalize_ads
from src.normalizers.ozon.inventory_normalizer import normalize_inventory
from src.normalizers.ozon.postings_fbs_normalizer import normalize_postings_fbs
from src.normalizers.ozon.products_normalizer import normalize_products
from src.normalizers.ozon.transactions_normalizer import normalize_transactions
from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.normalizers.wb.finance_normalizer import normalize_wb_finance
from src.normalizers.wb.orders_normalizer import normalize_wb_orders
from src.normalizers.wb.products_normalizer import normalize_wb_products
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.normalizers.wb.stock_normalizer import normalize_wb_stock
from src.raw_store.writer import write_raw_json
from src.services.live_probe_audit import export_live_probe_audit
from src.utils.db import execute_sql_file, get_conn, run_in_transaction

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SCHEMA_ENV_KEYS: tuple[str, ...] = ("OZON_SCHEMA", "SCHEMA")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        stream=sys.stdout,
    )


def parse_iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date '{value}'. Expected format: YYYY-MM-DD"
        ) from exc


def resolve_schema(cli_schema: str | None) -> str:
    if cli_schema:
        schema = cli_schema
    else:
        schema = ""
        for key in SCHEMA_ENV_KEYS:
            candidate = os.getenv(key)
            if candidate:
                schema = candidate
                break

    if not schema:
        keys = ", ".join(SCHEMA_ENV_KEYS)
        raise ValueError(f"Schema is required: pass --schema or set one of env vars: {keys}")

    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(
            "Invalid schema value. Expected pattern: ^[A-Za-z_][A-Za-z0-9_]*$"
        )

    return schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ozon Profit Control Postgres runners")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ddl_parser = subparsers.add_parser("ddl", help="Apply DDL")
    ddl_parser.add_argument("--schema", help="Target schema name")
    ddl_parser.add_argument(
        "--ddl-path",
        default="src/loaders/ddl.sql",
        help="Path to DDL SQL file",
    )

    marts_parser = subparsers.add_parser("marts", help="Build marts")
    marts_parser.add_argument("--schema", help="Target schema name")
    marts_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    marts_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    marts_parser.add_argument(
        "--sql-dir",
        default="src/marts/sql",
        help="Directory with mart SQL files",
    )
    marts_parser.add_argument(
        "--build-mode",
        choices=("published_build", "diagnostic_build"),
        default="published_build",
        help="published_build requires publish-ready snapshot; diagnostic_build skips publish gate",
    )
    marts_parser.add_argument(
        "--marketplace",
        choices=("ozon", "wb"),
        help="Optional marketplace filter for snapshot gate in published_build",
    )
    marts_parser.add_argument(
        "--snapshot-id",
        help="Optional explicit snapshot_id for published_build",
    )

    products_parser = subparsers.add_parser("products", help="Ingest Ozon products into dim_product")
    products_parser.add_argument("--schema", help="Target schema name")
    products_parser.add_argument("--limit", type=int, default=100, help="Page size for Ozon products API")
    products_parser.add_argument(
        "--pagination-mode",
        choices=("last_id", "offset"),
        default="last_id",
        help="Pagination mode for Ozon products API",
    )
    products_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    products_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    postings_parser = subparsers.add_parser("postings", help="Ingest Ozon FBS postings into staging tables")
    postings_parser.add_argument("--schema", help="Target schema name")
    postings_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    postings_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    postings_parser.add_argument("--limit", type=int, default=100, help="Page size for postings API")
    postings_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    postings_parser.add_argument("--offset", type=int, default=0, help="Start offset for offset pagination")
    postings_parser.add_argument(
        "--pagination-mode",
        choices=("offset", "cursor"),
        default="offset",
        help="Pagination mode for postings API",
    )
    postings_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    transactions_parser = subparsers.add_parser(
        "transactions",
        help="Ingest Ozon finance transactions into staging tables",
    )
    transactions_parser.add_argument("--schema", help="Target schema name")
    transactions_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    transactions_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    transactions_parser.add_argument(
        "--limit",
        "--page-size",
        dest="limit",
        type=int,
        default=100,
        help="Page size for transactions API",
    )
    transactions_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    transactions_parser.add_argument("--page", type=int, default=1, help="Start page for page pagination")
    transactions_parser.add_argument("--offset", type=int, default=0, help="Start offset for offset pagination")
    transactions_parser.add_argument(
        "--pagination-mode",
        choices=("page", "offset", "cursor"),
        default="page",
        help="Pagination mode for transactions API",
    )
    transactions_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    ads_parser = subparsers.add_parser("ads", help="Ingest Ozon ads spend into fact_ads_spend")
    ads_parser.add_argument("--schema", help="Target schema name")
    ads_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    ads_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    ads_parser.add_argument("--limit", type=int, default=100, help="Page size for ads API")
    ads_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    ads_parser.add_argument("--offset", type=int, default=0, help="Start offset for offset pagination")
    ads_parser.add_argument(
        "--pagination-mode",
        choices=("offset", "page"),
        default="offset",
        help="Pagination mode for ads API",
    )
    ads_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="Ingest Ozon inventory snapshots into fact_inventory_snapshot",
    )
    inventory_parser.add_argument("--schema", help="Target schema name")
    inventory_parser.add_argument(
        "--snapshot-date",
        type=parse_iso_date,
        help="Snapshot date override (YYYY-MM-DD). Default: today (UTC)",
    )
    inventory_parser.add_argument("--limit", type=int, default=100, help="Page size for inventory API")
    inventory_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    inventory_parser.add_argument("--offset", type=int, default=0, help="Start offset for offset pagination")
    inventory_parser.add_argument("--page", type=int, default=1, help="Start page for page pagination")
    inventory_parser.add_argument(
        "--pagination-mode",
        choices=("offset", "page", "cursor"),
        default="offset",
        help="Pagination mode for inventory API",
    )
    inventory_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    seed_rich_parser = subparsers.add_parser(
        "seed-demo-rich",
        help="Seed rich synthetic demo data for full pipeline checks",
    )
    seed_rich_parser.add_argument("--schema", help="Target schema name")
    seed_rich_parser.add_argument(
        "--from",
        dest="period_start",
        type=parse_iso_date,
        default=date(2026, 2, 1),
        help="Synthetic period start date (YYYY-MM-DD). Default: 2026-02-01",
    )
    seed_rich_parser.add_argument(
        "--to",
        dest="period_end",
        type=parse_iso_date,
        default=date(2026, 3, 1),
        help="Synthetic period end date, exclusive (YYYY-MM-DD). Default: 2026-03-01",
    )
    seed_rich_parser.add_argument(
        "--seed-sql-path",
        default="src/loaders/sql/seed_client_demo_rich.sql",
        help="Path to synthetic seed SQL file",
    )

    wb_products_parser = subparsers.add_parser(
        "wb-products",
        help="Ingest Wildberries products into WB staging and dim_product",
    )
    wb_products_parser.add_argument("--schema", help="Target schema name")
    wb_products_parser.add_argument("--limit", type=int, default=100, help="Page size for WB products API")
    wb_products_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    wb_products_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_orders_parser = subparsers.add_parser(
        "wb-orders",
        help="Ingest Wildberries orders into stg_wb_order",
    )
    wb_orders_parser.add_argument("--schema", help="Target schema name")
    wb_orders_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    wb_orders_parser.add_argument("--limit", type=int, default=100, help="Legacy offset page size for WB orders API")
    wb_orders_parser.add_argument("--max-pages", type=int, default=100, help="Safety cap for cursor/legacy pagination")
    wb_orders_parser.add_argument("--offset", type=int, default=0, help="Legacy start offset for offset pagination")
    wb_orders_parser.add_argument(
        "--pagination-mode",
        choices=("cursor", "offset", "single"),
        default="cursor",
        help="Pagination mode for WB orders API. Default: cursor by lastChangeDate",
    )
    wb_orders_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_paid_storage_parser = subparsers.add_parser(
        "wb-paid-storage",
        help="Ingest Wildberries paid storage report into stg_wb_paid_storage",
    )
    wb_paid_storage_parser.add_argument("--schema", help="Target schema name")
    wb_paid_storage_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD). Canonical chunk size: 8 days",
    )
    wb_paid_storage_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    wb_paid_storage_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_sales_parser = subparsers.add_parser(
        "wb-sales",
        help="Ingest Wildberries sales into stg_wb_sale",
    )
    wb_sales_parser.add_argument("--schema", help="Target schema name")
    wb_sales_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    wb_sales_parser.add_argument("--limit", type=int, default=100, help="Page size for WB sales API")
    wb_sales_parser.add_argument("--max-pages", type=int, default=100, help="Safety cap for page count")
    wb_sales_parser.add_argument("--offset", type=int, default=0, help="Start offset for offset pagination")
    wb_sales_parser.add_argument(
        "--pagination-mode",
        choices=("offset", "single"),
        default="offset",
        help="Pagination mode for WB sales API",
    )
    wb_sales_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_finance_parser = subparsers.add_parser(
        "wb-finance",
        help="Ingest Wildberries finance operations into stg_wb_finance",
    )
    wb_finance_parser.add_argument("--schema", help="Target schema name")
    wb_finance_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    wb_finance_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    wb_finance_parser.add_argument("--limit", type=int, default=100, help="Page size for WB finance API")
    wb_finance_parser.add_argument("--max-pages", type=int, default=100, help="Safety cap for page count")
    wb_finance_parser.add_argument("--offset", type=int, default=0, help="Start offset for pagination")
    wb_finance_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_ads_parser = subparsers.add_parser(
        "wb-ads",
        help="Ingest Wildberries ads spend into stg_wb_ads and fact_ads_spend",
    )
    wb_ads_parser.add_argument("--schema", help="Target schema name")
    wb_ads_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    wb_ads_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    wb_ads_parser.add_argument(
        "--advert-id",
        dest="advert_ids",
        type=int,
        action="append",
        help="Optional WB advert ID filter. Can be repeated.",
    )
    wb_ads_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    wb_stock_parser = subparsers.add_parser(
        "wb-stock",
        help="Ingest Wildberries stock snapshot into stg_wb_stock and fact_inventory_snapshot",
    )
    wb_stock_parser.add_argument("--schema", help="Target schema name")
    wb_stock_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="WB API dateFrom (YYYY-MM-DD)",
    )
    wb_stock_parser.add_argument(
        "--snapshot-date",
        type=parse_iso_date,
        help="Snapshot date override (YYYY-MM-DD). Default: today (UTC)",
    )
    wb_stock_parser.add_argument("--limit", type=int, default=100, help="Page size for WB stock API")
    wb_stock_parser.add_argument("--max-pages", type=int, default=100, help="Safety cap for page count")
    wb_stock_parser.add_argument("--offset", type=int, default=0, help="Start offset for pagination")
    wb_stock_parser.add_argument(
        "--pagination-mode",
        choices=("offset", "single"),
        default="offset",
        help="Pagination mode for WB stock API",
    )
    wb_stock_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")

    seed_wb_rich_parser = subparsers.add_parser(
        "seed-demo-wb-rich",
        help="Seed rich Wildberries synthetic demo data for full pipeline checks",
    )
    seed_wb_rich_parser.add_argument("--schema", help="Target schema name")
    seed_wb_rich_parser.add_argument(
        "--from",
        dest="period_start",
        type=parse_iso_date,
        default=date(2026, 2, 1),
        help="Synthetic period start date (YYYY-MM-DD). Default: 2026-02-01",
    )
    seed_wb_rich_parser.add_argument(
        "--to",
        dest="period_end",
        type=parse_iso_date,
        default=date(2026, 3, 1),
        help="Synthetic period end date, exclusive (YYYY-MM-DD). Default: 2026-03-01",
    )
    seed_wb_rich_parser.add_argument(
        "--seed-sql-path",
        default="src/loaders/sql/seed_client_demo_wb_rich.sql",
        help="Path to WB synthetic seed SQL file",
    )

    registry_parser = subparsers.add_parser(
        "registry-upsert",
        help="Upsert row into public.internal_clients_registry",
    )
    registry_parser.add_argument("--schema", help="Client DB schema name", required=True)
    registry_parser.add_argument("--client-code", required=True, help="Unique client code")
    registry_parser.add_argument("--client-name", required=True, help="Client display name")
    registry_parser.add_argument(
        "--marketplace",
        required=True,
        choices=("ozon", "wb"),
        help="Marketplace code",
    )
    registry_parser.add_argument(
        "--connection-status",
        default="new",
        help="Connection status label (default: new)",
    )
    registry_parser.add_argument("--notes", default="", help="Optional notes")

    export_audit_parser = subparsers.add_parser(
        "export_live_probe_audit",
        help="Export WB/Ozon live probe audit CSV + markdown diagnostics",
    )
    export_audit_parser.add_argument(
        "--schema-wb",
        dest="schema_wb",
        required=True,
        help="WB schema name (e.g. client_wb_probe_2)",
    )
    export_audit_parser.add_argument(
        "--schema-ozon",
        dest="schema_ozon",
        required=True,
        help="Ozon schema name (e.g. client_ozon_probe_2)",
    )
    export_audit_parser.add_argument(
        "--output-dir",
        default="exports/live_probe_audit",
        help="Output directory for audit artifacts",
    )

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Unified ingestion runner (marketplace + source adapter + audit/completeness)",
    )
    ingest_parser.add_argument("--schema", help="Target schema name")
    ingest_parser.add_argument(
        "--marketplace",
        required=True,
        choices=("ozon", "wb"),
        help="Marketplace code",
    )
    ingest_parser.add_argument(
        "--source",
        required=True,
        choices=(
            "products",
            "postings",
            "transactions",
            "inventory",
            "ads",
            "orders",
            "paid_storage",
            "sales",
            "finance",
            "stock",
        ),
        help="Source name in marketplace namespace",
    )
    ingest_parser.add_argument(
        "--mode",
        default="incremental",
        choices=("incremental", "backfill"),
        help="Ingestion mode label for audit",
    )
    ingest_parser.add_argument(
        "--from",
        dest="period_start",
        type=parse_iso_date,
        help="Period start date (YYYY-MM-DD)",
    )
    ingest_parser.add_argument(
        "--to",
        dest="period_end",
        type=parse_iso_date,
        help="Period end date (YYYY-MM-DD)",
    )
    ingest_parser.add_argument("--dry-run", action="store_true", help="Run fetch+normalize only, skip DB upserts")
    ingest_parser.add_argument("--sample", help="Local sample JSON path. If set, API is not called.")
    ingest_parser.add_argument("--limit", type=int, default=100, help="Page size")
    ingest_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    ingest_parser.add_argument("--offset", type=int, default=0, help="Start offset")
    ingest_parser.add_argument("--page", type=int, default=1, help="Start page")
    ingest_parser.add_argument(
        "--pagination-mode",
        default=None,
        help="Optional pagination mode override for selected source",
    )
    ingest_parser.add_argument(
        "--advert-id",
        dest="advert_ids",
        type=int,
        action="append",
        help="WB ads campaign ID filter. Can be repeated.",
    )
    ingest_parser.add_argument(
        "--snapshot-date",
        type=parse_iso_date,
        help="Snapshot date override for inventory/stock sources (YYYY-MM-DD)",
    )

    wb_bootstrap_parser = subparsers.add_parser(
        "wb-bootstrap",
        help="Bootstrap WB cabinet: quick light-source load + seed heavy-source day queue",
    )
    wb_bootstrap_parser.add_argument("--schema", help="Target schema name")
    wb_bootstrap_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Bootstrap period start (YYYY-MM-DD)",
    )
    wb_bootstrap_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Bootstrap period end (YYYY-MM-DD)",
    )
    wb_bootstrap_parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=("orders", "sales", "finance", "ads"),
        help="Heavy source to enqueue (can be repeated). Default: all heavy sources.",
    )
    wb_bootstrap_parser.add_argument(
        "--skip-light-sources",
        action="store_true",
        help="Skip immediate quick ingestion for WB products/stock",
    )
    wb_bootstrap_parser.add_argument("--limit", type=int, default=100, help="Page size override for quick sources")
    wb_bootstrap_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for quick sources")
    wb_bootstrap_parser.add_argument("--offset", type=int, default=0, help="Start offset for quick sources")
    wb_bootstrap_parser.add_argument("--page", type=int, default=1, help="Start page for quick sources")
    wb_bootstrap_parser.add_argument(
        "--pagination-mode",
        default=None,
        help="Optional pagination mode override for quick sources",
    )
    wb_bootstrap_parser.add_argument(
        "--snapshot-date",
        type=parse_iso_date,
        help="Snapshot date override for stock source",
    )

    wb_worker_parser = subparsers.add_parser(
        "wb-backfill-worker",
        help="Run WB backfill worker for day-level heavy source queue",
    )
    wb_worker_parser.add_argument("--schema", help="Target schema name")
    wb_worker_parser.add_argument(
        "--from",
        dest="period_start",
        required=True,
        type=parse_iso_date,
        help="Worker period start (YYYY-MM-DD)",
    )
    wb_worker_parser.add_argument(
        "--to",
        dest="period_end",
        required=True,
        type=parse_iso_date,
        help="Worker period end (YYYY-MM-DD)",
    )
    wb_worker_parser.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=("orders", "sales", "finance", "ads"),
        help="Heavy source to process (can be repeated). Default: all heavy sources.",
    )
    wb_worker_parser.add_argument(
        "--once",
        action="store_true",
        help="Process available jobs once and exit if queue becomes empty",
    )
    wb_worker_parser.add_argument(
        "--max-jobs",
        type=int,
        default=0,
        help="Max jobs per run (0 = unlimited until stopped)",
    )
    wb_worker_parser.add_argument(
        "--poll-interval-sec",
        type=int,
        default=10,
        help="Sleep between polls when queue is empty",
    )
    wb_worker_parser.add_argument("--limit", type=int, default=100, help="Page size")
    wb_worker_parser.add_argument("--max-pages", type=int, default=1000, help="Safety cap for page count")
    wb_worker_parser.add_argument("--offset", type=int, default=0, help="Start offset")
    wb_worker_parser.add_argument("--page", type=int, default=1, help="Start page")
    wb_worker_parser.add_argument(
        "--pagination-mode",
        default=None,
        help="Optional pagination mode override for selected source",
    )
    wb_worker_parser.add_argument(
        "--advert-id",
        dest="advert_ids",
        type=int,
        action="append",
        help="WB ads campaign ID filter. Can be repeated.",
    )
    wb_worker_parser.add_argument(
        "--snapshot-date",
        type=parse_iso_date,
        help="Snapshot date override for stock source",
    )

    return parser


def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid4().hex[:8]}"


def load_sample_pages(sample_path: str) -> list[dict[str, Any]]:
    path = Path(sample_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Sample file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise ValueError("Sample JSON must be an object or list of objects")


def split_page_payload(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Any] | None, int | None]:
    response = page.get("response")
    request_params = page.get("request_params")
    http_status = page.get("http_status")

    payload = response if isinstance(response, dict) else dict(page)
    req = request_params if isinstance(request_params, Mapping) else None
    status = http_status if isinstance(http_status, int) else None
    return payload, req, status


def handle_products(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    logger.info("Starting products ingestion for schema=%s run_id=%s", schema, run_id)

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample products payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_products_raw(
                limit=args.limit,
                pagination_mode=args.pagination_mode,
                max_pages=args.max_pages,
            )
        except MissingOzonCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched product pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="products",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status,
        )
        logger.info("Saved raw products page=%s path=%s", idx, raw_path)

    rows = normalize_products(pages)
    logger.info("Normalized products rows: %s", len(rows))
    upsert_dim_product(schema=schema, rows=rows)
    logger.info("Products ingestion completed for schema=%s run_id=%s", schema, run_id)
    return 0


def handle_postings(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    run_id = make_run_id()
    logger.info(
        "Starting postings ingestion for schema=%s run_id=%s period=%s..%s",
        schema,
        run_id,
        period_start.isoformat(),
        period_end.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample postings payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_postings_fbs_pages(
                date_from=period_start.isoformat(),
                date_to=period_end.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
                pagination_mode=args.pagination_mode,
            )
        except MissingOzonCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched postings pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="postings_fbs",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw postings page=%s path=%s", idx, raw_path)

    rows_posting, rows_items, rows_financial = normalize_postings_fbs(pages)
    postings_count = upsert_stg_posting(schema, rows_posting)
    items_count = upsert_stg_posting_item(schema, rows_items)
    financial_count = upsert_stg_posting_item_financial(schema, rows_financial)

    logger.info(
        "Postings ingestion completed: postings=%s items=%s financial_items=%s",
        postings_count,
        items_count,
        financial_count,
    )
    return 0


def handle_transactions(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    run_id = make_run_id()
    logger.info(
        "Starting transactions ingestion for schema=%s run_id=%s period=%s..%s",
        schema,
        run_id,
        period_start.isoformat(),
        period_end.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample transactions payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_transactions_pages(
                date_from=period_start.isoformat(),
                date_to=period_end.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                pagination_mode=args.pagination_mode,
                page=args.page,
                offset=args.offset,
            )
        except MissingOzonCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched transactions pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="transactions",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw transactions page=%s path=%s", idx, raw_path)

    rows_tx, rows_services, rows_items = normalize_transactions(pages)
    tx_count = upsert_stg_transaction(schema, rows_tx)
    services_count = upsert_stg_transaction_service(schema, rows_services)
    items_count = upsert_stg_transaction_item(schema, rows_items)

    logger.info(
        "Transactions ingestion completed: tx=%s services=%s items=%s",
        tx_count,
        services_count,
        items_count,
    )
    return 0


def handle_ads(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    run_id = make_run_id()
    logger.info(
        "Starting ads ingestion for schema=%s run_id=%s period=%s..%s",
        schema,
        run_id,
        period_start.isoformat(),
        period_end.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample ads payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_ads_pages(
                date_from=period_start.isoformat(),
                date_to=period_end.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
                pagination_mode=args.pagination_mode,
            )
        except MissingOzonCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched ads pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="ads",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw ads page=%s path=%s", idx, raw_path)

    rows = normalize_ads(pages)
    logger.info(
        "Normalized ads rows=%s for schema=%s period=%s..%s",
        len(rows),
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    upserted_count = upsert_fact_ads_spend(schema=schema, rows=rows)
    logger.info(
        "Ads ingestion completed: upserted=%s schema=%s period=%s..%s",
        upserted_count,
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    return 0


def handle_inventory(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    snapshot_date = args.snapshot_date or datetime.now(timezone.utc).date()
    logger.info(
        "Starting inventory ingestion for schema=%s run_id=%s snapshot_date=%s",
        schema,
        run_id,
        snapshot_date.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample inventory payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_inventory_pages(
                limit=args.limit,
                max_pages=args.max_pages,
                pagination_mode=args.pagination_mode,
                offset=args.offset,
                page=args.page,
            )
        except MissingOzonCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched inventory pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="inventory",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw inventory page=%s path=%s", idx, raw_path)

    rows = normalize_inventory(pages, snapshot_date=snapshot_date)
    logger.info(
        "Normalized inventory rows=%s schema=%s snapshot_date=%s",
        len(rows),
        schema,
        snapshot_date.isoformat(),
    )
    try:
        upserted_count = upsert_fact_inventory_snapshot(schema=schema, rows=rows)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "Inventory ingestion completed: upserted=%s schema=%s snapshot_date=%s",
        upserted_count,
        schema,
        snapshot_date.isoformat(),
    )
    return 0


def handle_seed_demo_rich(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    logger.info(
        "Seeding rich synthetic demo data schema=%s period=%s..%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    apply_ddl(schema=schema, ddl_path="src/loaders/ddl.sql")

    params = {
        "schema": schema,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    with get_conn() as conn:
        run_in_transaction(
            conn,
            lambda current_conn: execute_sql_file(current_conn, args.seed_sql_path, params),
        )

    logger.info(
        "Rich synthetic demo data seeded schema=%s period=%s..%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    return 0


def _wb_dim_rows_from_products(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_offer: dict[str, dict[str, Any]] = {}
    for row in rows:
        offer_id = str(row.get("offer_id") or "~")
        if offer_id == "~":
            continue
        by_offer[offer_id] = {
            "offer_id": offer_id,
            "marketplace": "wb",
            "product_id": row.get("nm_id"),
            "sku": row.get("barcode"),
            "product_name": row.get("product_name"),
            "brand": row.get("brand"),
            "category_id": None,
            "category_name": row.get("category_name"),
            "archived": False,
            "updated_at": row.get("raw_updated_at"),
        }
    return list(by_offer.values())


def handle_wb_products(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    logger.info("Starting WB products ingestion schema=%s run_id=%s", schema, run_id)

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB products payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_products_pages(limit=args.limit, max_pages=args.max_pages)
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB products pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_products",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB products page=%s path=%s", idx, raw_path)

    rows = normalize_wb_products(pages)
    stg_count = upsert_stg_wb_product(schema=schema, rows=rows)
    dim_rows = _wb_dim_rows_from_products(rows)
    if dim_rows:
        upsert_dim_product(schema=schema, rows=dim_rows)
    logger.info("WB products ingestion completed: stg_wb_product=%s dim_product=%s", stg_count, len(dim_rows))
    return 0


def handle_wb_orders(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    period_start: date = args.period_start
    logger.info(
        "Starting WB orders ingestion schema=%s run_id=%s from=%s",
        schema,
        run_id,
        period_start.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB orders payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_orders_pages(
                date_from=period_start.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
                pagination_mode=args.pagination_mode,
            )
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB orders pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_orders",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB orders page=%s path=%s", idx, raw_path)

    rows = normalize_wb_orders(pages)
    upserted = upsert_stg_wb_order(schema=schema, rows=rows)
    logger.info("WB orders ingestion completed: stg_wb_order=%s", upserted)
    return 0


def handle_wb_sales(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    period_start: date = args.period_start
    logger.info(
        "Starting WB sales ingestion schema=%s run_id=%s from=%s",
        schema,
        run_id,
        period_start.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB sales payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_sales_pages(
                date_from=period_start.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
                pagination_mode=args.pagination_mode,
            )
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB sales pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_sales",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB sales page=%s path=%s", idx, raw_path)

    rows = normalize_wb_sales(pages)
    upserted = upsert_stg_wb_sale(schema=schema, rows=rows)
    logger.info("WB sales ingestion completed: stg_wb_sale=%s", upserted)
    return 0


def handle_wb_finance(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )
    logger.info(
        "Starting WB finance ingestion schema=%s run_id=%s period=%s..%s",
        schema,
        run_id,
        period_start.isoformat(),
        period_end.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB finance payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_finance_pages(
                date_from=period_start.isoformat(),
                date_to=period_end.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
            )
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB finance pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_finance",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB finance page=%s path=%s", idx, raw_path)

    rows = normalize_wb_finance(pages)
    upserted = upsert_stg_wb_finance(schema=schema, rows=rows)
    logger.info("WB finance ingestion completed: stg_wb_finance=%s", upserted)
    return 0


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
            "app_type": row.get("app_type") or "~",
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


def handle_wb_ads(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )
    logger.info(
        "Starting WB ads ingestion schema=%s run_id=%s period=%s..%s",
        schema,
        run_id,
        period_start.isoformat(),
        period_end.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB ads payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_ads_pages(
                date_from=period_start.isoformat(),
                date_to=period_end.isoformat(),
                advert_ids=args.advert_ids,
            )
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB ads pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_ads",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB ads page=%s path=%s", idx, raw_path)

    rows = normalize_wb_ads(pages)
    stg_count = upsert_stg_wb_ads(schema=schema, rows=rows)
    fact_rows = _map_wb_ads_to_fact(rows)
    fact_count = upsert_fact_ads_spend(schema=schema, rows=fact_rows)
    logger.info("WB ads ingestion completed: stg_wb_ads=%s fact_ads_spend=%s", stg_count, fact_count)
    return 0


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
            "stock_available": row.get("stock_available"),
            "stock_reserved": row.get("stock_reserved"),
            "stock_defect": row.get("stock_defect"),
            "raw_updated_at": row.get("raw_updated_at"),
        }
        for row in rows
    ]


def handle_wb_stock(schema: str, args: argparse.Namespace) -> int:
    run_id = make_run_id()
    period_start: date = args.period_start
    snapshot_date = args.snapshot_date or datetime.now(timezone.utc).date()
    logger.info(
        "Starting WB stock ingestion schema=%s run_id=%s date_from=%s snapshot_date=%s",
        schema,
        run_id,
        period_start.isoformat(),
        snapshot_date.isoformat(),
    )

    if args.sample:
        pages = load_sample_pages(args.sample)
        logger.info("Loaded sample WB stock payload from %s pages=%s", args.sample, len(pages))
    else:
        try:
            pages = fetch_wb_stock_pages(
                date_from=period_start.isoformat(),
                limit=args.limit,
                max_pages=args.max_pages,
                offset=args.offset,
                pagination_mode=args.pagination_mode,
            )
        except MissingWbCredentialsError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("Fetched WB stock pages: %s", len(pages))

    for idx, page in enumerate(pages, start=1):
        payload, request_params, http_status = split_page_payload(page)
        raw_path = write_raw_json(
            schema=schema,
            source="wb_stock",
            run_id=run_id,
            payload=payload,
            page=idx,
            request_params=request_params,
            http_status=http_status or (200 if args.sample else None),
        )
        logger.info("Saved raw WB stock page=%s path=%s", idx, raw_path)

    rows = normalize_wb_stock(pages, snapshot_date=snapshot_date)
    stg_count = upsert_stg_wb_stock(schema=schema, rows=rows)
    fact_rows = _map_wb_stock_to_fact(rows)
    fact_count = upsert_fact_inventory_snapshot(schema=schema, rows=fact_rows)
    logger.info("WB stock ingestion completed: stg_wb_stock=%s fact_inventory_snapshot=%s", stg_count, fact_count)
    return 0


def handle_seed_demo_wb_rich(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    logger.info(
        "Seeding WB rich synthetic demo data schema=%s period=%s..%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    apply_ddl(schema=schema, ddl_path="src/loaders/ddl.sql")

    params = {
        "schema": schema,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    with get_conn() as conn:
        run_in_transaction(
            conn,
            lambda current_conn: execute_sql_file(current_conn, args.seed_sql_path, params),
        )

    logger.info(
        "WB rich synthetic demo data seeded schema=%s period=%s..%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
    )
    return 0


def handle_registry_upsert(args: argparse.Namespace) -> int:
    sql = """
    INSERT INTO public.internal_clients_registry (
      client_code,
      client_name,
      marketplace,
      db_schema,
      connection_status,
      notes,
      updated_at
    )
    VALUES (%s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (client_code) DO UPDATE
    SET
      client_name = EXCLUDED.client_name,
      marketplace = EXCLUDED.marketplace,
      db_schema = EXCLUDED.db_schema,
      connection_status = EXCLUDED.connection_status,
      notes = EXCLUDED.notes,
      updated_at = now()
    """
    params = (
        args.client_code,
        args.client_name,
        args.marketplace,
        args.schema,
        args.connection_status,
        args.notes or None,
    )

    with get_conn() as conn:
        run_in_transaction(
            conn,
            lambda current_conn: current_conn.execute(sql, params),
        )

    logger.info(
        "Registry upsert completed client_code=%s schema=%s marketplace=%s",
        args.client_code,
        args.schema,
        args.marketplace,
    )
    return 0


def handle_unified_ingest(schema: str, args: argparse.Namespace) -> int:
    period_start = getattr(args, "period_start", None)
    period_end = getattr(args, "period_end", None)
    if (
        args.marketplace == "wb"
        and args.source in {"orders", "sales", "stock"}
        and period_start is not None
        and period_end is None
    ):
        period_end = datetime.now(timezone.utc).date()

    result = run_ingestion(
        marketplace=args.marketplace,
        source=args.source,
        schema=schema,
        date_from=period_start,
        date_to=period_end,
        mode=args.mode,
        dry_run=bool(args.dry_run),
        sample_path=getattr(args, "sample", None),
        limit=getattr(args, "limit", 100),
        max_pages=getattr(args, "max_pages", 1000),
        offset=getattr(args, "offset", 0),
        page=getattr(args, "page", 1),
        pagination_mode=getattr(args, "pagination_mode", None),
        advert_ids=getattr(args, "advert_ids", None),
        snapshot_date=getattr(args, "snapshot_date", None),
    )
    logger.info(
        "Unified ingestion result run_id=%s status=%s source=%s:%s raw=%s normalized=%s loaded=%s",
        result.run_id,
        result.status,
        result.marketplace,
        result.source,
        result.raw_rows_count,
        result.normalized_rows_count,
        result.loaded_rows_count,
    )
    return 1 if result.status == "error" else 0


def handle_wb_bootstrap(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    light_failed = False
    if not bool(args.skip_light_sources):
        for source in ("products", "stock"):
            result = run_ingestion(
                marketplace="wb",
                source=source,
                schema=schema,
                date_from=period_start if source == "stock" else None,
                date_to=period_end if source == "stock" else None,
                mode="backfill" if source == "stock" else "incremental",
                dry_run=False,
                sample_path=None,
                limit=getattr(args, "limit", 100),
                max_pages=getattr(args, "max_pages", 1000),
                offset=getattr(args, "offset", 0),
                page=getattr(args, "page", 1),
                pagination_mode=getattr(args, "pagination_mode", None),
                snapshot_date=getattr(args, "snapshot_date", None),
            )
            logger.info(
                "WB bootstrap quick source=%s run_id=%s status=%s raw=%s normalized=%s loaded=%s",
                source,
                result.run_id,
                result.status,
                result.raw_rows_count,
                result.normalized_rows_count,
                result.loaded_rows_count,
            )
            if result.status == "error":
                light_failed = True

    selected_sources = tuple(args.sources) if getattr(args, "sources", None) else None
    queued = seed_wb_backfill_queue(
        schema=schema,
        date_from=period_start,
        date_to=period_end,
        sources=selected_sources,
    )
    logger.info(
        "WB bootstrap queue seeded schema=%s period=%s..%s rows=%s sources=%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
        queued,
        ",".join(selected_sources) if selected_sources else ",".join(sorted({"orders", "sales", "finance", "ads"})),
    )
    return 1 if light_failed else 0


def handle_wb_backfill_worker(schema: str, args: argparse.Namespace) -> int:
    period_start: date = args.period_start
    period_end: date = args.period_end
    if period_start > period_end:
        raise ValueError(
            f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
        )

    selected_sources = tuple(args.sources) if getattr(args, "sources", None) else None
    stats = run_wb_backfill_worker(
        schema=schema,
        date_from=period_start,
        date_to=period_end,
        sources=selected_sources,
        max_jobs=getattr(args, "max_jobs", 0),
        poll_interval_sec=getattr(args, "poll_interval_sec", 10),
        once=bool(getattr(args, "once", False)),
        limit=getattr(args, "limit", 100),
        max_pages=getattr(args, "max_pages", 1000),
        offset=getattr(args, "offset", 0),
        page=getattr(args, "page", 1),
        pagination_mode=getattr(args, "pagination_mode", None),
        advert_ids=tuple(getattr(args, "advert_ids", None) or ()),
        snapshot_date=getattr(args, "snapshot_date", None),
    )
    logger.info(
        "WB backfill worker finished schema=%s period=%s..%s processed=%s success=%s failed=%s",
        schema,
        period_start.isoformat(),
        period_end.isoformat(),
        stats.get("processed", 0),
        stats.get("success", 0),
        stats.get("failed", 0),
    )
    return 0 if int(stats.get("failed", 0)) == 0 else 1


def handle_legacy_ingest_via_unified(command: str, schema: str, args: argparse.Namespace) -> int:
    mapping: dict[str, tuple[str, str]] = {
        "products": ("ozon", "products"),
        "postings": ("ozon", "postings"),
        "transactions": ("ozon", "transactions"),
        "ads": ("ozon", "ads"),
        "inventory": ("ozon", "inventory"),
        "wb-products": ("wb", "products"),
        "wb-orders": ("wb", "orders"),
        "wb-paid-storage": ("wb", "paid_storage"),
        "wb-sales": ("wb", "sales"),
        "wb-finance": ("wb", "finance"),
        "wb-ads": ("wb", "ads"),
        "wb-stock": ("wb", "stock"),
    }
    if command not in mapping:
        raise ValueError(f"Unsupported legacy ingest command: {command}")
    marketplace, source = mapping[command]

    ingest_args = argparse.Namespace(
        marketplace=marketplace,
        source=source,
        mode="incremental",
        dry_run=False,
        period_start=getattr(args, "period_start", None),
        period_end=getattr(args, "period_end", None),
        sample=getattr(args, "sample", None),
        limit=getattr(args, "limit", 100),
        max_pages=getattr(args, "max_pages", 1000),
        offset=getattr(args, "offset", 0),
        page=getattr(args, "page", 1),
        pagination_mode=getattr(args, "pagination_mode", None),
        advert_ids=getattr(args, "advert_ids", None),
        snapshot_date=getattr(args, "snapshot_date", None),
    )
    return handle_unified_ingest(schema, ingest_args)


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "export_live_probe_audit":
            out_dir = export_live_probe_audit(
                schema_wb=args.schema_wb,
                schema_ozon=args.schema_ozon,
                output_dir=args.output_dir,
            )
            logger.info("Live probe audit exported to %s", out_dir)
            return 0

        schema = resolve_schema(getattr(args, "schema", None))

        if args.command == "ddl":
            apply_ddl(schema=schema, ddl_path=args.ddl_path)
            return 0

        if args.command == "marts":
            period_start: date = args.period_start
            period_end: date = args.period_end
            if period_start > period_end:
                raise ValueError(
                    f"Invalid period: --from {period_start.isoformat()} is after --to {period_end.isoformat()}"
                )
            build_marts(
                schema=schema,
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
                sql_dir=args.sql_dir,
                build_mode=args.build_mode,
                marketplace=args.marketplace,
                snapshot_id=args.snapshot_id,
            )
            return 0

        if args.command == "ingest":
            return handle_unified_ingest(schema, args)

        if args.command == "wb-bootstrap":
            return handle_wb_bootstrap(schema, args)

        if args.command == "wb-backfill-worker":
            return handle_wb_backfill_worker(schema, args)

        if args.command == "products":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "postings":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "transactions":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "ads":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "inventory":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "seed-demo-rich":
            return handle_seed_demo_rich(schema, args)

        if args.command == "wb-products":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-orders":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-paid-storage":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-sales":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-finance":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-ads":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "wb-stock":
            return handle_legacy_ingest_via_unified(args.command, schema, args)

        if args.command == "seed-demo-wb-rich":
            return handle_seed_demo_wb_rich(schema, args)

        if args.command == "registry-upsert":
            return handle_registry_upsert(args)

        raise ValueError(f"Unsupported command: {args.command}")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Command failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
