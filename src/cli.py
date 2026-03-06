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
from src.connectors.ozon.postings_fbs import fetch_postings_fbs_pages
from src.connectors.ozon.products import fetch_products_raw
from src.connectors.ozon.transactions import fetch_transactions_pages
from src.loaders.ddl_runner import apply_ddl
from src.loaders.upsert_dim_product import upsert_dim_product
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
from src.marts.build_marts import build_marts
from src.normalizers.ozon.postings_fbs_normalizer import normalize_postings_fbs
from src.normalizers.ozon.products_normalizer import normalize_products
from src.normalizers.ozon.transactions_normalizer import normalize_transactions
from src.raw_store.writer import write_raw_json

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


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
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
            )
            return 0

        if args.command == "products":
            return handle_products(schema, args)

        if args.command == "postings":
            return handle_postings(schema, args)

        if args.command == "transactions":
            return handle_transactions(schema, args)

        raise ValueError(f"Unsupported command: {args.command}")
    except (FileNotFoundError, ValueError) as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Command failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
