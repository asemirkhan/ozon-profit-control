from __future__ import annotations

import logging

from src.utils.db import execute_sql_file, get_conn, resolve_project_path, run_in_transaction

logger = logging.getLogger(__name__)


def apply_ddl(schema: str, ddl_path: str = "src/loaders/ddl.sql") -> None:
    resolved_ddl_path = resolve_project_path(ddl_path)
    logger.info("Applying DDL from %s for schema=%s", resolved_ddl_path, schema)

    with get_conn() as conn:
        run_in_transaction(
            conn,
            lambda current_conn: execute_sql_file(
                current_conn,
                resolved_ddl_path,
                {"schema": schema},
            ),
        )

    logger.info("DDL applied for schema: %s", schema)
