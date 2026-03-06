from __future__ import annotations

import logging
import re
from typing import Any

from psycopg import sql

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert_dim_product(schema: str, rows: list[dict[str, Any]]) -> None:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")

    if not rows:
        logger.info("No rows to upsert into %s.dim_product", schema)
        return

    query = sql.SQL(
        """
        INSERT INTO {schema}.dim_product (
            offer_id,
            product_id,
            sku,
            product_name,
            brand,
            category_id,
            category_name,
            archived,
            updated_at
        )
        VALUES (
            %(offer_id)s,
            %(product_id)s,
            %(sku)s,
            %(product_name)s,
            %(brand)s,
            %(category_id)s,
            %(category_name)s,
            %(archived)s,
            %(updated_at)s
        )
        ON CONFLICT (offer_id) DO UPDATE
        SET
            product_id = EXCLUDED.product_id,
            sku = EXCLUDED.sku,
            product_name = EXCLUDED.product_name,
            brand = EXCLUDED.brand,
            category_id = EXCLUDED.category_id,
            category_name = EXCLUDED.category_name,
            archived = EXCLUDED.archived,
            updated_at = EXCLUDED.updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, rows)

        run_in_transaction(conn, _run)

    logger.info("Upserted rows into %s.dim_product: %s", schema, len(rows))
