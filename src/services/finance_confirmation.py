from __future__ import annotations

import re
from datetime import date

from src.utils.db import get_conn

_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def restore_strict_days_from_reconciliation(*, schema: str, days: list[date]) -> list[date]:
    """Restore a day that a later failed retry incorrectly downgraded.

    A day is restored only when an earlier completed pass reconciles exactly
    with the current normalized finance table.  A rate limit or empty retry is
    never evidence for downgrading already reconciled data.
    """
    if not days:
        return []
    if not _SCHEMA.fullmatch(schema):
        raise ValueError(f"Invalid schema: {schema}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                WITH best_complete AS (
                  SELECT DISTINCT ON (target_date)
                    target_date,
                    normalized_rows_total,
                    stg_rows_total,
                    raw_amount_sum,
                    stg_amount_sum,
                    created_at
                  FROM {schema}.finance_reconciliation_day
                  WHERE target_date = ANY(%s)
                    AND pagination_complete = TRUE
                  ORDER BY target_date, created_at DESC
                ),
                actual AS (
                  SELECT
                    operation_date::date AS day,
                    COUNT(*)::int AS row_count,
                    COALESCE(SUM(amount), 0)::numeric(18,2) AS amount_sum
                  FROM {schema}.stg_wb_finance
                  WHERE operation_date::date = ANY(%s)
                  GROUP BY operation_date::date
                ),
                valid AS (
                  SELECT b.target_date
                  FROM best_complete b
                  JOIN actual a ON a.day = b.target_date
                  WHERE b.normalized_rows_total = a.row_count
                    AND b.stg_rows_total = a.row_count
                    AND ABS(b.raw_amount_sum - a.amount_sum) <= 0.05
                    AND ABS(b.stg_amount_sum - a.amount_sum) <= 0.05
                )
                UPDATE {schema}.finance_day_state s
                SET status = 'success',
                    raw_presence_flag = TRUE,
                    stg_presence_flag = TRUE,
                    finance_day_complete_flag = TRUE,
                    final_analytic_usable_flag = TRUE,
                    rows_done_total = GREATEST(s.rows_done_total, a.row_count),
                    no_progress_attempts = 0,
                    repeated_page_attempts = 0,
                    source_pressure_flag = FALSE,
                    reason_code = 'restored_from_complete_reconciliation',
                    terminal_reason_code = 'complete_reconciliation_exact_match',
                    terminal_decision_at = now(),
                    terminal_decision_basis = 'best_completed_pass_matches_current_staging',
                    terminal_confidence_level = 'high',
                    recheck_required_flag = FALSE,
                    last_success_at = now(),
                    updated_at = now()
                FROM valid v
                JOIN actual a ON a.day = v.target_date
                WHERE s.day = v.target_date
                RETURNING s.day
                """,
                (days, days),
            )
            restored = [row[0] for row in cur.fetchall()]
        conn.commit()
    return sorted(restored)
