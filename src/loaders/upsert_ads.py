from __future__ import annotations

import logging
import re
from typing import Any

from psycopg import sql

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert_fact_ads_spend(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        logger.info("No rows to upsert into %s.fact_ads_spend", schema)
        return 0
    for row in rows:
        row.setdefault("marketplace", "ozon")
        row.setdefault("app_type", "~")

    query = sql.SQL(
        """
        INSERT INTO {schema}.fact_ads_spend (
            date,
            campaign_id,
            marketplace,
            campaign_name,
            campaign_type,
            offer_id,
            sku,
            product_id,
            app_type,
            impressions,
            clicks,
            orders_count,
            revenue,
            spend,
            currency,
            raw_updated_at
        )
        VALUES (
            %(date)s,
            %(campaign_id)s,
            %(marketplace)s,
            %(campaign_name)s,
            %(campaign_type)s,
            %(offer_id)s,
            %(sku)s,
            %(product_id)s,
            %(app_type)s,
            %(impressions)s,
            %(clicks)s,
            %(orders_count)s,
            %(revenue)s,
            %(spend)s,
            %(currency)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (date, campaign_id, offer_id, sku, product_id, app_type) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            campaign_name = EXCLUDED.campaign_name,
            campaign_type = EXCLUDED.campaign_type,
            impressions = EXCLUDED.impressions,
            clicks = EXCLUDED.clicks,
            orders_count = EXCLUDED.orders_count,
            revenue = EXCLUDED.revenue,
            spend = EXCLUDED.spend,
            currency = EXCLUDED.currency,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, rows)

        run_in_transaction(conn, _run)

    logger.info("Upserted rows into %s.fact_ads_spend: %s", schema, len(rows))
    return len(rows)
