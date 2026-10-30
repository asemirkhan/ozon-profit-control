from __future__ import annotations

import logging
import re
from typing import Any

from psycopg import sql

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert_fact_inventory_snapshot(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        logger.info("No rows to upsert into %s.fact_inventory_snapshot", schema)
        return 0
    for row in rows:
        row.setdefault("marketplace", "ozon")

    snapshot_value = rows[0].get("snapshot_date")
    logger.info(
        "Upserting inventory rows=%s schema=%s snapshot_date=%s",
        len(rows),
        schema,
        snapshot_value,
    )

    query = sql.SQL(
        """
        INSERT INTO {schema}.fact_inventory_snapshot (
            snapshot_date,
            marketplace,
            offer_id,
            sku,
            product_id,
            warehouse_name,
            warehouse_type,
            stock_total,
            stock_available,
            stock_reserved,
            stock_defect,
            raw_updated_at
        )
        VALUES (
            %(snapshot_date)s,
            %(marketplace)s,
            %(offer_id)s,
            %(sku)s,
            %(product_id)s,
            %(warehouse_name)s,
            %(warehouse_type)s,
            %(stock_total)s,
            %(stock_available)s,
            %(stock_reserved)s,
            %(stock_defect)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (snapshot_date, offer_id, sku, product_id, warehouse_name) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            warehouse_type = EXCLUDED.warehouse_type,
            stock_total = EXCLUDED.stock_total,
            stock_available = EXCLUDED.stock_available,
            stock_reserved = EXCLUDED.stock_reserved,
            stock_defect = EXCLUDED.stock_defect,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, rows)

        run_in_transaction(conn, _run)

    logger.info(
        "Inventory upsert completed: upserted=%s schema=%s snapshot_date=%s",
        len(rows),
        schema,
        snapshot_value,
    )
    return len(rows)
