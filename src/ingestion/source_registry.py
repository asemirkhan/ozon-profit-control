from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal, Mapping

from src.connectors.ozon.ads import fetch_ads_pages
from src.connectors.ozon.inventory import fetch_inventory_pages
from src.connectors.ozon.postings_fbs import fetch_postings_fbs_pages
from src.connectors.ozon.products import fetch_products_raw
from src.connectors.ozon.transactions import fetch_transactions_pages
from src.connectors.wb.ads import fetch_wb_ads_pages
from src.connectors.wb.finance import fetch_wb_finance_pages
from src.connectors.wb.orders import fetch_wb_orders_pages
from src.connectors.wb.paid_storage import fetch_wb_paid_storage_pages
from src.connectors.wb.products import fetch_wb_products_pages
from src.connectors.wb.sales import fetch_wb_sales_pages
from src.connectors.wb.stock import fetch_wb_stock_pages
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
    upsert_stg_wb_paid_storage,
    upsert_stg_wb_product,
    upsert_stg_wb_sale,
    upsert_stg_wb_stock,
)
from src.normalizers.ozon.ads_normalizer import normalize_ads
from src.normalizers.ozon.inventory_normalizer import normalize_inventory
from src.normalizers.ozon.postings_fbs_normalizer import normalize_postings_fbs
from src.normalizers.ozon.products_normalizer import normalize_products
from src.normalizers.ozon.transactions_normalizer import normalize_transactions
from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.normalizers.wb.finance_normalizer import normalize_wb_finance
from src.normalizers.wb.orders_normalizer import normalize_wb_orders
from src.normalizers.wb.paid_storage_normalizer import normalize_wb_paid_storage as normalize_wb_paid_storage_rows
from src.normalizers.wb.products_normalizer import normalize_wb_products
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.normalizers.wb.stock_normalizer import normalize_wb_stock

Marketplace = Literal["ozon", "wb"]


@dataclass(frozen=True)
class LoaderSpec:
    dataset: str
    loader: Callable[[str, list[dict[str, Any]]], int]


@dataclass(frozen=True)
class SourceConfig:
    marketplace: Marketplace
    source: str
    raw_source: str
    fetcher: Callable[["SourceRunContext"], list[dict[str, Any]]]
    normalizer: Callable[["SourceRunContext", list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]
    loaders: tuple[LoaderSpec, ...]
    chunk_days: int | None = None
    chunk_by_calendar_month: bool = False
    requires_period: bool = False
    period_filter_fields: Mapping[str, str] = field(default_factory=dict)
    expected_days_mode: Literal["period", "snapshot", "none"] = "none"


@dataclass(frozen=True)
class SourceRunContext:
    marketplace: Marketplace
    source: str
    schema: str
    date_from: date | None
    date_to: date | None
    chunk_from: date | None
    chunk_to: date | None
    mode: str
    dry_run: bool
    limit: int
    max_pages: int
    offset: int
    page: int
    pagination_mode: str | None
    advert_ids: tuple[int, ...]
    snapshot_date: date | None
    extra: Mapping[str, Any]


def source_key(marketplace: str, source: str) -> str:
    return f"{marketplace}:{source}"


def get_source_config(marketplace: str, source: str) -> SourceConfig:
    key = source_key(marketplace, source)
    try:
        return SOURCE_REGISTRY[key]
    except KeyError as exc:
        available = ", ".join(sorted(SOURCE_REGISTRY.keys()))
        raise ValueError(f"Unsupported source '{key}'. Available: {available}") from exc


def _must_have_period(ctx: SourceRunContext) -> tuple[str, str]:
    if ctx.chunk_from is None or ctx.chunk_to is None:
        raise ValueError(f"{ctx.marketplace}:{ctx.source} requires --from and --to")
    return ctx.chunk_from.isoformat(), ctx.chunk_to.isoformat()


def _date_from_only(ctx: SourceRunContext) -> str:
    anchor = ctx.chunk_from or ctx.date_from
    if anchor is None:
        raise ValueError(f"{ctx.marketplace}:{ctx.source} requires --from")
    return anchor.isoformat()


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
            "stock_available": row.get("stock_available"),
            "stock_reserved": row.get("stock_reserved"),
            "stock_defect": row.get("stock_defect"),
            "raw_updated_at": row.get("raw_updated_at"),
        }
        for row in rows
    ]


def _normalize_ozon_products(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    rows = normalize_products(pages)
    return {"dim_product": rows}


def _normalize_ozon_postings(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    rows_posting, rows_items, rows_financial = normalize_postings_fbs(pages)
    return {
        "stg_posting": rows_posting,
        "stg_posting_item": rows_items,
        "stg_posting_item_financial": rows_financial,
    }


def _normalize_ozon_transactions(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    rows_tx, rows_services, rows_items = normalize_transactions(pages)
    return {
        "stg_transaction": rows_tx,
        "stg_transaction_service": rows_services,
        "stg_transaction_item": rows_items,
    }


def _normalize_ozon_ads(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    return {"fact_ads_spend": normalize_ads(pages)}


def _normalize_ozon_inventory(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    snapshot = ctx.snapshot_date or ctx.chunk_to or ctx.date_to or datetime.now(timezone.utc).date()
    return {"fact_inventory_snapshot": normalize_inventory(pages, snapshot_date=snapshot)}


def _normalize_wb_products(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    stg_rows = normalize_wb_products(pages)
    return {
        "stg_wb_product": stg_rows,
        "dim_product": _wb_dim_rows_from_products(stg_rows),
    }


def _normalize_wb_orders(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    return {"stg_wb_order": normalize_wb_orders(pages)}


def _normalize_wb_paid_storage(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    return {"stg_wb_paid_storage": normalize_wb_paid_storage_rows(pages)}


def _normalize_wb_sales(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows = normalize_wb_sales(pages)
    target_day = ctx.chunk_from or ctx.date_from
    if target_day is None:
        return {"stg_wb_sale": rows}

    filtered: list[dict[str, Any]] = []
    for row in rows:
        sale_dt = row.get("sale_date")
        sale_day = getattr(sale_dt, "date", lambda: sale_dt)() if sale_dt is not None else None
        if sale_day == target_day:
            filtered.append(row)
    return {"stg_wb_sale": filtered}


def _normalize_wb_finance(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    return {"stg_wb_finance": normalize_wb_finance(pages)}


def _normalize_wb_ads(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    _ = ctx
    stg_rows = normalize_wb_ads(pages)
    return {
        "stg_wb_ads": stg_rows,
        "fact_ads_spend": _map_wb_ads_to_fact(stg_rows),
    }


def _normalize_wb_stock(ctx: SourceRunContext, pages: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    snapshot = ctx.snapshot_date or ctx.chunk_to or ctx.date_to or datetime.now(timezone.utc).date()
    stg_rows = normalize_wb_stock(pages, snapshot_date=snapshot)
    return {
        "stg_wb_stock": stg_rows,
        "fact_inventory_snapshot": _map_wb_stock_to_fact(stg_rows),
    }


SOURCE_REGISTRY: dict[str, SourceConfig] = {
    source_key("ozon", "products"): SourceConfig(
        marketplace="ozon",
        source="products",
        raw_source="products",
        fetcher=lambda ctx: fetch_products_raw(
            limit=ctx.limit,
            pagination_mode=(ctx.pagination_mode or "last_id"),  # type: ignore[arg-type]
            max_pages=ctx.max_pages,
        ),
        normalizer=_normalize_ozon_products,
        loaders=(LoaderSpec("dim_product", upsert_dim_product),),
        expected_days_mode="none",
    ),
    source_key("ozon", "postings"): SourceConfig(
        marketplace="ozon",
        source="postings",
        raw_source="postings_fbs",
        fetcher=lambda ctx: fetch_postings_fbs_pages(
            date_from=_must_have_period(ctx)[0],
            date_to=_must_have_period(ctx)[1],
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
            pagination_mode=(ctx.pagination_mode or "offset"),  # type: ignore[arg-type]
        ),
        normalizer=_normalize_ozon_postings,
        loaders=(
            LoaderSpec("stg_posting", upsert_stg_posting),
            LoaderSpec("stg_posting_item", upsert_stg_posting_item),
            LoaderSpec("stg_posting_item_financial", upsert_stg_posting_item_financial),
        ),
        requires_period=True,
        chunk_days=31,
        period_filter_fields={"stg_posting": "created_at"},
        expected_days_mode="period",
    ),
    source_key("ozon", "transactions"): SourceConfig(
        marketplace="ozon",
        source="transactions",
        raw_source="transactions",
        fetcher=lambda ctx: fetch_transactions_pages(
            date_from=_must_have_period(ctx)[0],
            date_to=_must_have_period(ctx)[1],
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            pagination_mode=(ctx.pagination_mode or "page"),  # type: ignore[arg-type]
            page=ctx.page,
            offset=ctx.offset,
        ),
        normalizer=_normalize_ozon_transactions,
        loaders=(
            LoaderSpec("stg_transaction", upsert_stg_transaction),
            LoaderSpec("stg_transaction_service", upsert_stg_transaction_service),
            LoaderSpec("stg_transaction_item", upsert_stg_transaction_item),
        ),
        requires_period=True,
        chunk_days=31,
        chunk_by_calendar_month=True,
        period_filter_fields={"stg_transaction": "operation_date"},
        expected_days_mode="period",
    ),
    source_key("ozon", "ads"): SourceConfig(
        marketplace="ozon",
        source="ads",
        raw_source="ads",
        fetcher=lambda ctx: fetch_ads_pages(
            date_from=_must_have_period(ctx)[0],
            date_to=_must_have_period(ctx)[1],
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
            pagination_mode=(ctx.pagination_mode or "offset"),  # type: ignore[arg-type]
        ),
        normalizer=_normalize_ozon_ads,
        loaders=(LoaderSpec("fact_ads_spend", upsert_fact_ads_spend),),
        requires_period=True,
        chunk_days=31,
        period_filter_fields={"fact_ads_spend": "date"},
        expected_days_mode="period",
    ),
    source_key("ozon", "inventory"): SourceConfig(
        marketplace="ozon",
        source="inventory",
        raw_source="inventory",
        fetcher=lambda ctx: fetch_inventory_pages(
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            pagination_mode=(ctx.pagination_mode or "offset"),  # type: ignore[arg-type]
            offset=ctx.offset,
            page=ctx.page,
        ),
        normalizer=_normalize_ozon_inventory,
        loaders=(LoaderSpec("fact_inventory_snapshot", upsert_fact_inventory_snapshot),),
        expected_days_mode="snapshot",
        period_filter_fields={"fact_inventory_snapshot": "snapshot_date"},
    ),
    source_key("wb", "products"): SourceConfig(
        marketplace="wb",
        source="products",
        raw_source="wb_products",
        fetcher=lambda ctx: fetch_wb_products_pages(limit=ctx.limit, max_pages=ctx.max_pages),
        normalizer=_normalize_wb_products,
        loaders=(
            LoaderSpec("stg_wb_product", upsert_stg_wb_product),
            LoaderSpec("dim_product", upsert_dim_product),
        ),
        expected_days_mode="none",
    ),
    source_key("wb", "orders"): SourceConfig(
        marketplace="wb",
        source="orders",
        raw_source="wb_orders",
        fetcher=lambda ctx: fetch_wb_orders_pages(
            date_from=_date_from_only(ctx),
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
            pagination_mode=(ctx.pagination_mode or "cursor"),  # type: ignore[arg-type]
        ),
        normalizer=_normalize_wb_orders,
        loaders=(LoaderSpec("stg_wb_order", upsert_stg_wb_order),),
        requires_period=True,
        # Orders are requested from a cursor-style endpoint whose response may
        # contain a long tail after dateFrom.  Day-sized chunks keep coverage
        # auditable and prevent a partial 90-day response from looking full.
        chunk_days=1,
        period_filter_fields={"stg_wb_order": "order_date"},
        expected_days_mode="period",
    ),
    source_key("wb", "paid_storage"): SourceConfig(
        marketplace="wb",
        source="paid_storage",
        raw_source="wb_paid_storage",
        fetcher=lambda ctx: fetch_wb_paid_storage_pages(*_must_have_period(ctx)),
        normalizer=_normalize_wb_paid_storage,
        loaders=(LoaderSpec("stg_wb_paid_storage", upsert_stg_wb_paid_storage),),
        requires_period=True,
        chunk_days=8,
        period_filter_fields={"stg_wb_paid_storage": "report_date"},
        expected_days_mode="period",
    ),
    source_key("wb", "sales"): SourceConfig(
        marketplace="wb",
        source="sales",
        raw_source="wb_sales",
        fetcher=lambda ctx: fetch_wb_sales_pages(
            date_from=_date_from_only(ctx),
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
            pagination_mode=(ctx.pagination_mode or "offset"),  # type: ignore[arg-type]
        ),
        normalizer=_normalize_wb_sales,
        loaders=(LoaderSpec("stg_wb_sale", upsert_stg_wb_sale),),
        requires_period=True,
        chunk_days=1,
        period_filter_fields={"stg_wb_sale": "sale_date"},
        expected_days_mode="period",
    ),
    source_key("wb", "finance"): SourceConfig(
        marketplace="wb",
        source="finance",
        raw_source="wb_finance",
        fetcher=lambda ctx: fetch_wb_finance_pages(
            date_from=_must_have_period(ctx)[0],
            date_to=_must_have_period(ctx)[1],
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
        ),
        normalizer=_normalize_wb_finance,
        loaders=(LoaderSpec("stg_wb_finance", upsert_stg_wb_finance),),
        requires_period=True,
        chunk_days=1,
        period_filter_fields={"stg_wb_finance": "operation_date"},
        expected_days_mode="period",
    ),
    source_key("wb", "ads"): SourceConfig(
        marketplace="wb",
        source="ads",
        raw_source="wb_ads",
        fetcher=lambda ctx: fetch_wb_ads_pages(
            date_from=_must_have_period(ctx)[0],
            date_to=_must_have_period(ctx)[1],
            advert_ids=list(ctx.advert_ids) if ctx.advert_ids else None,
        ),
        normalizer=_normalize_wb_ads,
        loaders=(
            LoaderSpec("stg_wb_ads", upsert_stg_wb_ads),
            LoaderSpec("fact_ads_spend", upsert_fact_ads_spend),
        ),
        requires_period=True,
        chunk_days=1,
        period_filter_fields={
            "stg_wb_ads": "date",
            "fact_ads_spend": "date",
        },
        expected_days_mode="period",
    ),
    source_key("wb", "stock"): SourceConfig(
        marketplace="wb",
        source="stock",
        raw_source="wb_stock",
        fetcher=lambda ctx: fetch_wb_stock_pages(
            date_from=_date_from_only(ctx),
            limit=ctx.limit,
            max_pages=ctx.max_pages,
            offset=ctx.offset,
            pagination_mode=(ctx.pagination_mode or "offset"),  # type: ignore[arg-type]
        ),
        normalizer=_normalize_wb_stock,
        loaders=(
            LoaderSpec("stg_wb_stock", upsert_stg_wb_stock),
            LoaderSpec("fact_inventory_snapshot", upsert_fact_inventory_snapshot),
        ),
        requires_period=True,
        period_filter_fields={
            "stg_wb_stock": "snapshot_date",
            "fact_inventory_snapshot": "snapshot_date",
        },
        expected_days_mode="snapshot",
    ),
}
