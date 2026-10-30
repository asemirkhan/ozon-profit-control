from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

logger = logging.getLogger(__name__)

PRODUCT_LIST_KEYS: tuple[str, ...] = (
    "products",
    "items",
    "offers",
    "product_stats",
    "sku_stats",
)
STATS_LIST_KEYS: tuple[str, ...] = (
    "stats",
    "statistics",
    "days",
    "daily",
    "dates",
    "time_series",
    "timeline",
)
METRIC_CONTAINER_KEYS: tuple[str, ...] = ("metrics", "stat", "totals", "summary", "performance")


def normalize_ads(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    skipped_without_date = 0
    skipped_without_spend = 0
    unknown_pages = 0

    for page in pages:
        response = _extract_response(page)
        raw_rows = list(_iter_raw_rows(response))
        if not raw_rows:
            unknown_pages += 1
            logger.warning("Unknown ads page format. Top-level keys: %s", sorted(response.keys()))
            continue

        for campaign_ctx, product_ctx, stat_ctx in raw_rows:
            row = _build_row(campaign_ctx, product_ctx, stat_ctx, now)
            if row is None:
                raw_date = _pick_from_sources((stat_ctx, product_ctx, campaign_ctx), "date", "day", "report_date")
                raw_spend = _pick_from_sources(
                    (stat_ctx, product_ctx, campaign_ctx),
                    "spend",
                    "cost",
                    "sum",
                    "amount",
                )
                if raw_date is None:
                    skipped_without_date += 1
                if raw_spend is None:
                    skipped_without_spend += 1
                continue
            rows.append(row)

    if skipped_without_date:
        logger.warning("Skipped ads rows without valid date: %s", skipped_without_date)
    if skipped_without_spend:
        logger.warning("Skipped ads rows without valid spend: %s", skipped_without_spend)
    if unknown_pages and not rows:
        logger.warning("No ads rows normalized. Unknown page count: %s", unknown_pages)

    return rows


def _extract_response(page: Mapping[str, Any]) -> Mapping[str, Any]:
    response = page.get("response")
    if isinstance(response, Mapping):
        return response
    return page


def _iter_raw_rows(payload: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]:
    result = payload.get("result")
    root = result if isinstance(result, Mapping) else payload
    if not isinstance(root, Mapping):
        return []

    rows: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []

    campaigns = _extract_mapping_list(root, ("campaigns", "ad_campaigns"))
    if campaigns:
        for campaign in campaigns:
            rows.extend(_expand_campaign(campaign))
        return rows

    root_items = _extract_mapping_list(root, ("items", "rows", "data", "statistics"))
    if root_items:
        for item in root_items:
            if _extract_mapping_list(item, PRODUCT_LIST_KEYS) or _extract_mapping_list(item, ("campaigns",)):
                rows.extend(_expand_campaign(item))
            else:
                rows.append((item, item, item))
        return rows

    if _pick_from_sources((root,), "date", "day", "spend", "cost", "amount") is not None:
        rows.append((root, root, root))

    return rows


def _expand_campaign(
    campaign: Mapping[str, Any],
) -> list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]]:
    rows: list[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]] = []

    products = _extract_mapping_list(campaign, PRODUCT_LIST_KEYS)
    campaign_stats = _extract_mapping_list(campaign, STATS_LIST_KEYS)

    if products:
        for product in products:
            product_stats = _extract_mapping_list(product, STATS_LIST_KEYS)
            if product_stats:
                for stat in product_stats:
                    rows.append((campaign, product, stat))
            elif campaign_stats:
                for stat in campaign_stats:
                    rows.append((campaign, product, stat))
            else:
                rows.append((campaign, product, product))
        return rows

    if campaign_stats:
        for stat in campaign_stats:
            rows.append((campaign, campaign, stat))
        return rows

    rows.append((campaign, campaign, campaign))
    return rows


def _build_row(
    campaign_ctx: Mapping[str, Any],
    product_ctx: Mapping[str, Any],
    stat_ctx: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    record_sources = (stat_ctx, product_ctx, campaign_ctx)

    parsed_date = _as_date(
        _pick_from_sources(
            record_sources,
            "date",
            "day",
            "report_date",
            "stat_date",
            "period_date",
        )
    )
    if parsed_date is None:
        return None

    spend = _as_decimal(
        _pick_from_sources(
            record_sources,
            "spend",
            "cost",
            "sum",
            "amount",
            "expense",
        )
    )
    if spend is None:
        return None

    campaign_id = _as_text(
        _pick_from_sources(record_sources, "campaign_id", "campaignId", "id")
    ) or "~"
    campaign_name = _as_text(
        _pick_from_sources(record_sources, "campaign_name", "campaignName", "name")
    )
    campaign_type = _as_text(
        _pick_from_sources(
            record_sources,
            "campaign_type",
            "campaignType",
            "type",
            "advert_type",
            "advertType",
        )
    )

    offer_id = _as_text(_pick_from_sources(record_sources, "offer_id", "offerId")) or "~"
    sku = _as_text(_pick_from_sources(record_sources, "sku", "sku_id", "skuId")) or "~"
    product_id = _as_text(
        _pick_from_sources(record_sources, "product_id", "productId", "id_product")
    ) or "~"

    return {
        "date": parsed_date,
        "campaign_id": campaign_id,
        "marketplace": "ozon",
        "campaign_name": campaign_name,
        "campaign_type": campaign_type,
        "offer_id": offer_id,
        "sku": sku,
        "product_id": product_id,
        "impressions": _as_decimal(_pick_from_sources(record_sources, "impressions", "shows", "views")),
        "clicks": _as_decimal(_pick_from_sources(record_sources, "clicks", "click")),
        "orders_count": _as_decimal(
            _pick_from_sources(
                record_sources,
                "orders_count",
                "orders",
                "attributed_orders",
                "ordersAttributed",
            )
        ),
        "revenue": _as_decimal(
            _pick_from_sources(
                record_sources,
                "revenue",
                "attributed_revenue",
                "orders_revenue",
                "sales",
            )
        ),
        "spend": spend,
        "currency": _as_text(_pick_from_sources(record_sources, "currency", "currency_code", "currencyCode")),
        "raw_updated_at": now,
    }


def _extract_mapping_list(data: Mapping[str, Any], keys: tuple[str, ...]) -> list[Mapping[str, Any]]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    return []


def _pick_from_sources(sources: tuple[Mapping[str, Any], ...], *keys: str) -> Any:
    for source in sources:
        value = _pick_value(source, *keys)
        if value is not None:
            return value
    return None


def _pick_value(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)

    for container_key in METRIC_CONTAINER_KEYS:
        nested = data.get(container_key)
        if isinstance(nested, Mapping):
            for key in keys:
                if key in nested and nested.get(key) is not None:
                    return nested.get(key)
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None

    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]

    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
