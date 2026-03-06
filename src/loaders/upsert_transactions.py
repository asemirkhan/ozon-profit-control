from __future__ import annotations

import logging
import re
from typing import Any

from psycopg import sql

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert_stg_transaction(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_transaction (
            operation_id,
            operation_date,
            operation_type,
            operation_type_name,
            posting_number,
            accruals_for_sale,
            sale_commission,
            delivery_charge,
            return_delivery_charge,
            amount,
            raw_updated_at
        )
        VALUES (
            %(operation_id)s,
            %(operation_date)s,
            %(operation_type)s,
            %(operation_type_name)s,
            %(posting_number)s,
            %(accruals_for_sale)s,
            %(sale_commission)s,
            %(delivery_charge)s,
            %(return_delivery_charge)s,
            %(amount)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (operation_id) DO UPDATE
        SET
            operation_date = EXCLUDED.operation_date,
            operation_type = EXCLUDED.operation_type,
            operation_type_name = EXCLUDED.operation_type_name,
            posting_number = EXCLUDED.posting_number,
            accruals_for_sale = EXCLUDED.accruals_for_sale,
            sale_commission = EXCLUDED.sale_commission,
            delivery_charge = EXCLUDED.delivery_charge,
            return_delivery_charge = EXCLUDED.return_delivery_charge,
            amount = EXCLUDED.amount,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_transaction rows: %s", len(rows))
    return len(rows)


def upsert_stg_transaction_service(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_transaction_service (
            operation_id,
            service_name,
            service_price,
            raw_updated_at
        )
        VALUES (
            %(operation_id)s,
            %(service_name)s,
            %(service_price)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (operation_id, service_name) DO UPDATE
        SET
            service_price = EXCLUDED.service_price,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_transaction_service rows: %s", len(rows))
    return len(rows)


def upsert_stg_transaction_item(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_transaction_item (
            operation_id,
            sku,
            item_name,
            raw_updated_at
        )
        VALUES (
            %(operation_id)s,
            %(sku)s,
            %(item_name)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (operation_id, sku) DO UPDATE
        SET
            item_name = EXCLUDED.item_name,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    logger.info("Upserted stg_transaction_item rows: %s", len(rows))
    return len(rows)


def _execute_many(query: sql.SQL | sql.Composed, rows: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, rows)

        run_in_transaction(conn, _run)
