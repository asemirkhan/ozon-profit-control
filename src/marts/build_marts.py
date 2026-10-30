from __future__ import annotations

import logging
from pathlib import Path

from src.ingestion.publish_gate import (
    block_snapshot_semantic_mismatch,
    ensure_snapshot_publishable,
    get_semantic_mismatch_states,
    mark_snapshot_published,
)
from src.utils.db import execute_sql_file, get_conn, resolve_project_path, run_in_transaction

logger = logging.getLogger(__name__)

MART_SQL_FILES: tuple[str, ...] = (
    "mart_dim_product_bridge.sql",
    "mart_product_money_attribution.sql",
    "mart_unmatched_money.sql",
    "mart_pnl_daily_by_product.sql",
    "mart_inventory_snapshot.sql",
    "mart_pnl_30d_by_product.sql",
    "mart_semantic_correctness.sql",
    "mart_canonical_measure_mapping.sql",
    "mart_raw_vs_stg_canonical_audit.sql",
    "mart_granularity_reconciliation_report.sql",
    "mart_wb_ads_key_presence_audit.sql",
    "mart_rule_hits_30d.sql",
    "mart_data_quality_30d.sql",
    "mart_money_layer_reconciliation.sql",
    "mart_source_availability.sql",
    "mart_pipeline_health.sql",
    "mart_store_summary_30d.sql",
    "mart_action_queue_14d.sql",
    "mart_action_playbook_14d.sql",
    "mart_leakage_summary_30d.sql",
    "mart_ads_daily.sql",
    "mart_executive_summary_30d.sql",
)


def build_marts(
    schema: str,
    period_start: str,
    period_end: str,
    sql_dir: str = "src/marts/sql",
    build_mode: str = "published_build",
    marketplace: str | None = None,
    snapshot_id: str | None = None,
) -> None:
    sql_dir_path = resolve_project_path(sql_dir)
    if not sql_dir_path.exists():
        raise FileNotFoundError(f"SQL directory not found: {sql_dir_path}")

    params = {
        "schema": schema,
        "period_start": period_start,
        "period_end": period_end,
    }

    if build_mode not in {"diagnostic_build", "published_build"}:
        raise ValueError("build_mode must be diagnostic_build or published_build")

    if build_mode == "published_build":
        states = ensure_snapshot_publishable(
            schema=schema,
            period_start=period_start,
            period_end=period_end,
            marketplace=marketplace,
            snapshot_id=snapshot_id,
        )
        logger.info(
            "Publish gate passed for %s snapshot(s): %s",
            len(states),
            ", ".join(f"{s.marketplace}:{s.snapshot_id}:{s.publish_status}" for s in states),
        )
    else:
        logger.warning(
            "Running diagnostic marts build on potentially incomplete snapshot data "
            "(schema=%s period=%s..%s)",
            schema,
            period_start,
            period_end,
        )

    logger.info(
        "Starting marts build for schema=%s period=%s..%s from %s mode=%s",
        schema,
        period_start,
        period_end,
        sql_dir_path,
        build_mode,
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

    if build_mode == "published_build":
        semantic_mismatch = get_semantic_mismatch_states(
            schema=schema,
            period_start=period_start,
            period_end=period_end,
            marketplace=marketplace,
        )
        if semantic_mismatch:
            block_snapshot_semantic_mismatch(
                schema=schema,
                period_start=period_start,
                period_end=period_end,
                marketplace=marketplace,
            )
            details = ", ".join(f"{m}:{reason}" for m, reason in semantic_mismatch)
            raise RuntimeError(
                "Snapshot is not publish-ready due semantic mismatch. "
                f"Details: {details}. Use diagnostic_build for investigation."
            )
        updated = mark_snapshot_published(
            schema=schema,
            period_start=period_start,
            period_end=period_end,
            marketplace=marketplace,
            snapshot_id=snapshot_id,
        )
        logger.info(
            "Snapshot publish mark updated rows=%s schema=%s period=%s..%s",
            updated,
            schema,
            period_start,
            period_end,
        )
