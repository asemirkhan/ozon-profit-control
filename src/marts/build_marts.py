from __future__ import annotations

import logging
from pathlib import Path

from src.utils.db import execute_sql_file, get_conn, resolve_project_path, run_in_transaction

logger = logging.getLogger(__name__)

MART_SQL_FILES: tuple[str, ...] = (
    "mart_pnl_daily_by_product.sql",
    "mart_pnl_30d_by_product.sql",
    "mart_leakage_summary_30d.sql",
    "mart_ads_daily.sql",
    "mart_executive_summary_30d.sql",
)


def build_marts(
    schema: str,
    period_start: str,
    period_end: str,
    sql_dir: str = "src/marts/sql",
) -> None:
    sql_dir_path = resolve_project_path(sql_dir)
    if not sql_dir_path.exists():
        raise FileNotFoundError(f"SQL directory not found: {sql_dir_path}")

    params = {
        "schema": schema,
        "period_start": period_start,
        "period_end": period_end,
    }

    logger.info(
        "Starting marts build for schema=%s period=%s..%s from %s",
        schema,
        period_start,
        period_end,
        sql_dir_path,
    )

    with get_conn() as conn:
        for file_name in MART_SQL_FILES:
            file_path = Path(sql_dir_path) / file_name
            if not file_path.exists():
                raise FileNotFoundError(f"Mart SQL file not found: {file_path}")

            logger.info("Running mart SQL: %s", file_path)
            run_in_transaction(
                conn,
                lambda current_conn, current_file=file_path: execute_sql_file(
                    current_conn,
                    current_file,
                    params,
                ),
            )
            logger.info("Completed mart SQL: %s", file_path)

    logger.info(
        "Marts build completed for schema=%s period=%s..%s",
        schema,
        period_start,
        period_end,
    )
