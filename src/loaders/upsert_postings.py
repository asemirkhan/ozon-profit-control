from __future__ import annotations

import logging
import re
from typing import Any

from psycopg import sql

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert_stg_posting(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_posting (
            posting_number,
            status,
            created_at,
            in_process_at,
            shipped_at,
            delivered_at,
            cancelled_at,
            delivery_schema,
            warehouse_id,
            customer_region,
            is_multibox,
            raw_updated_at
        )
        VALUES (
            %(posting_number)s,
            %(status)s,
            %(created_at)s,
            %(in_process_at)s,
            %(shipped_at)s,
            %(delivered_at)s,
            %(cancelled_at)s,
            %(delivery_schema)s,
            %(warehouse_id)s,
            %(customer_region)s,
            %(is_multibox)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (posting_number) DO UPDATE
        SET
            status = EXCLUDED.status,
            created_at = EXCLUDED.created_at,
            in_process_at = EXCLUDED.in_process_at,
            shipped_at = EXCLUDED.shipped_at,
            delivered_at = EXCLUDED.delivered_at,
            cancelled_at = EXCLUDED.cancelled_at,
            delivery_schema = EXCLUDED.delivery_schema,
            warehouse_id = EXCLUDED.warehouse_id,
            customer_region = EXCLUDED.customer_region,
            is_multibox = EXCLUDED.is_multibox,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_posting rows: %s", len(rows))
    return len(rows)


def upsert_stg_posting_item(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_posting_item (
            posting_number,
            offer_id,
            sku,
            product_id,
            quantity,
            item_price,
            currency,
            raw_updated_at
        )
        VALUES (
            %(posting_number)s,
            %(offer_id)s,
            %(sku)s,
            %(product_id)s,
            %(quantity)s,
            %(item_price)s,
            %(currency)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (posting_number, offer_id, sku, product_id) DO UPDATE
        SET
            quantity = EXCLUDED.quantity,
            item_price = EXCLUDED.item_price,
            currency = EXCLUDED.currency,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_posting_item rows: %s", len(rows))
    return len(rows)


def upsert_stg_posting_item_financial(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_posting_item_financial (
            posting_number,
            product_id,
            sku,
            quantity,
            price,
            old_price,
            total_discount_value,
            total_discount_percent,
            commission_amount,
            commission_percent,
            payout,
            item_services_total,
            raw_updated_at
        )
        VALUES (
            %(posting_number)s,
            %(product_id)s,
            %(sku)s,
            %(quantity)s,
            %(price)s,
            %(old_price)s,
            %(total_discount_value)s,
            %(total_discount_percent)s,
            %(commission_amount)s,
            %(commission_percent)s,
            %(payout)s,
            %(item_services_total)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (posting_number, product_id) DO UPDATE
        SET
            sku = EXCLUDED.sku,
            quantity = EXCLUDED.quantity,
            price = EXCLUDED.price,
            old_price = EXCLUDED.old_price,
            total_discount_value = EXCLUDED.total_discount_value,
            total_discount_percent = EXCLUDED.total_discount_percent,
            commission_amount = EXCLUDED.commission_amount,
            commission_percent = EXCLUDED.commission_percent,
            payout = EXCLUDED.payout,
            item_services_total = EXCLUDED.item_services_total,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_posting_item_financial rows: %s", len(rows))
    return len(rows)


def _execute_many(query: sql.SQL | sql.Composed, rows: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, rows)

        run_in_transaction(conn, _run)
