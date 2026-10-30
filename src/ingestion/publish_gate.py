from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from psycopg import sql
from psycopg.rows import dict_row

from src.utils.db import get_conn, run_in_transaction


@dataclass(frozen=True)
class SourcePublishRule:
    source: str
    critical_for_publish: bool
    allowed_warning: bool
    comment: str


@dataclass(frozen=True)
class SnapshotState:
    snapshot_id: str
    schema_name: str
    marketplace: str
    period_from: date
    period_to: date
    required_sources: list[str]
    completed_sources: list[str]
    failed_sources: list[str]
    warning_sources: list[str]
    completeness_status: str
    publish_status: str
    published_at: str | None


PUBLISH_RULES: dict[str, tuple[SourcePublishRule, ...]] = {
    "wb": (
        SourcePublishRule("products", critical_for_publish=True, allowed_warning=False, comment="catalog baseline"),
        SourcePublishRule("sales", critical_for_publish=True, allowed_warning=False, comment="core revenue grain"),
        SourcePublishRule("finance", critical_for_publish=True, allowed_warning=False, comment="core cost grain"),
        SourcePublishRule("ads", critical_for_publish=False, allowed_warning=True, comment="non-blocking for basic pnl"),
        SourcePublishRule("stock", critical_for_publish=False, allowed_warning=True, comment="inventory/risk soft dependency"),
        SourcePublishRule("orders", critical_for_publish=False, allowed_warning=True, comment="auxiliary quality source"),
    ),
    "ozon": (
        SourcePublishRule("products", critical_for_publish=True, allowed_warning=False, comment="catalog baseline"),
        SourcePublishRule("postings", critical_for_publish=True, allowed_warning=False, comment="commercial operations"),
        SourcePublishRule("transactions", critical_for_publish=True, allowed_warning=False, comment="financial operations"),
        SourcePublishRule("inventory", critical_for_publish=False, allowed_warning=True, comment="inventory/risk soft dependency"),
        SourcePublishRule("ads", critical_for_publish=False, allowed_warning=True, comment="non-blocking for basic pnl"),
    ),
}


def get_publish_rules(marketplace: str) -> tuple[SourcePublishRule, ...]:
    rules = PUBLISH_RULES.get(marketplace)
    if not rules:
        raise ValueError(f"Unsupported marketplace for publish rules: {marketplace}")
    return rules


def source_criticality(marketplace: str, source: str) -> str:
    for rule in get_publish_rules(marketplace):
        if rule.source == source:
            return "critical" if rule.critical_for_publish else "noncritical"
    return "noncritical"


def evaluate_and_upsert_snapshot_state(
    *,
    schema: str,
    marketplace: str,
    period_from: date,
    period_to: date,
) -> SnapshotState:
    rules = get_publish_rules(marketplace)
    required_sources = [rule.source for rule in rules if rule.critical_for_publish]

    source_state: dict[str, str] = {}
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            for rule in rules:
                cur.execute(
                    sql.SQL(
                        """
                        SELECT
                          r.run_id,
                          r.status AS run_status,
                          c.status AS completeness_status,
                          c.usable_for_publish
                        FROM {schema}.ingestion_run r
                        LEFT JOIN {schema}.ingestion_completeness c
                          ON c.run_id = r.run_id
                        WHERE r.marketplace = %s
                          AND r.source = %s
                          AND (
                            (r.date_from = %s AND r.date_to = %s)
                            OR (r.date_from IS NULL AND r.date_to IS NULL)
                          )
                        ORDER BY
                          CASE WHEN r.date_from = %s AND r.date_to = %s THEN 0 ELSE 1 END,
                          r.started_at DESC,
                          r.run_id DESC
                        LIMIT 1
                        """
                    ).format(schema=sql.Identifier(schema)),
                    (
                        marketplace,
                        rule.source,
                        period_from,
                        period_to,
                        period_from,
                        period_to,
                    ),
                )
                row = cur.fetchone()
                if not row:
                    source_state[rule.source] = "missing"
                    continue

                run_status = str(row.get("run_status") or "")
                comp_status = str(row.get("completeness_status") or "")
                usable_for_publish = bool(row.get("usable_for_publish"))

                if run_status == "error" or comp_status == "error":
                    source_state[rule.source] = "failed"
                elif comp_status == "warning":
                    source_state[rule.source] = "warning"
                elif usable_for_publish:
                    source_state[rule.source] = "ok"
                else:
                    source_state[rule.source] = "failed"

    completed_sources = sorted([src for src, state in source_state.items() if state == "ok"])
    failed_sources = sorted([src for src, state in source_state.items() if state in {"failed", "missing"}])
    warning_sources = sorted([src for src, state in source_state.items() if state == "warning"])

    critical_failed = [
        rule.source
        for rule in rules
        if rule.critical_for_publish and source_state.get(rule.source) in {"failed", "missing", "warning"}
    ]
    critical_missing_all = all(source_state.get(rule.source) == "missing" for rule in rules if rule.critical_for_publish)
    noncritical_problem = any(
        not rule.critical_for_publish and source_state.get(rule.source) in {"failed", "missing", "warning"}
        for rule in rules
    )

    if critical_missing_all:
        publish_status = "draft"
    elif critical_failed:
        publish_status = "blocked"
    elif noncritical_problem:
        publish_status = "partial"
    else:
        publish_status = "ready"

    if publish_status == "blocked":
        completeness_status = "error"
    elif publish_status == "partial":
        completeness_status = "warning"
    elif publish_status == "draft":
        completeness_status = "draft"
    else:
        completeness_status = "ok"

    initial_snapshot_id = f"snap_{uuid4().hex[:12]}"
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            run_in_transaction(
                conn,
                lambda current_conn: current_conn.execute(
                    sql.SQL(
                        """
                        INSERT INTO {schema}.ingestion_snapshot_state (
                          snapshot_id,
                          schema_name,
                          marketplace,
                          period_from,
                          period_to,
                          required_sources,
                          completed_sources,
                          failed_sources,
                          warning_sources,
                          completeness_status,
                          publish_status,
                          published_at,
                          created_at,
                          updated_at
                        )
                        VALUES (
                          %s, %s, %s, %s, %s,
                          %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                          %s, %s, NULL, now(), now()
                        )
                        ON CONFLICT (schema_name, marketplace, period_from, period_to)
                        DO UPDATE SET
                          required_sources = EXCLUDED.required_sources,
                          completed_sources = EXCLUDED.completed_sources,
                          failed_sources = EXCLUDED.failed_sources,
                          warning_sources = EXCLUDED.warning_sources,
                          completeness_status = EXCLUDED.completeness_status,
                          publish_status = CASE
                            WHEN {schema}.ingestion_snapshot_state.publish_status = 'published'
                                 AND EXCLUDED.publish_status IN ('ready', 'partial')
                              THEN {schema}.ingestion_snapshot_state.publish_status
                            ELSE EXCLUDED.publish_status
                          END,
                          updated_at = now()
                        """
                    ).format(schema=sql.Identifier(schema)),
                    (
                        initial_snapshot_id,
                        schema,
                        marketplace,
                        period_from,
                        period_to,
                        json.dumps(required_sources, ensure_ascii=False),
                        json.dumps(completed_sources, ensure_ascii=False),
                        json.dumps(failed_sources, ensure_ascii=False),
                        json.dumps(warning_sources, ensure_ascii=False),
                        completeness_status,
                        publish_status,
                    ),
                ),
            )
            cur.execute(
                sql.SQL(
                    """
                    SELECT
                      snapshot_id,
                      schema_name,
                      marketplace,
                      period_from,
                      period_to,
                      required_sources,
                      completed_sources,
                      failed_sources,
                      warning_sources,
                      completeness_status,
                      publish_status,
                      published_at
                    FROM {schema}.ingestion_snapshot_state
                    WHERE schema_name = %s
                      AND marketplace = %s
                      AND period_from = %s
                      AND period_to = %s
                    LIMIT 1
                    """
                ).format(schema=sql.Identifier(schema)),
                (schema, marketplace, period_from, period_to),
            )
            row = cur.fetchone()

    if not row:
        raise RuntimeError("Failed to upsert ingestion_snapshot_state")
    return SnapshotState(
        snapshot_id=str(row["snapshot_id"]),
        schema_name=str(row["schema_name"]),
        marketplace=str(row["marketplace"]),
        period_from=row["period_from"],
        period_to=row["period_to"],
        required_sources=list(row["required_sources"] or []),
        completed_sources=list(row["completed_sources"] or []),
        failed_sources=list(row["failed_sources"] or []),
        warning_sources=list(row["warning_sources"] or []),
        completeness_status=str(row["completeness_status"]),
        publish_status=str(row["publish_status"]),
        published_at=row["published_at"].isoformat() if row["published_at"] else None,
    )


def ensure_snapshot_publishable(
    *,
    schema: str,
    period_start: str,
    period_end: str,
    marketplace: str | None = None,
    snapshot_id: str | None = None,
) -> list[SnapshotState]:
    where_parts: list[str] = ["period_from = %s", "period_to = %s"]
    params: list[object] = [date.fromisoformat(period_start), date.fromisoformat(period_end)]
    if marketplace:
        where_parts.append("marketplace = %s")
        params.append(marketplace)
    if snapshot_id:
        where_parts.append("snapshot_id = %s")
        params.append(snapshot_id)

    where_sql = " AND ".join(where_parts)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql.SQL(
                    f"""
                    SELECT
                      snapshot_id,
                      schema_name,
                      marketplace,
                      period_from,
                      period_to,
                      required_sources,
                      completed_sources,
                      failed_sources,
                      warning_sources,
                      completeness_status,
                      publish_status,
                      published_at
                    FROM {{schema}}.ingestion_snapshot_state
                    WHERE {where_sql}
                    ORDER BY marketplace, updated_at DESC
                    """
                ).format(schema=sql.Identifier(schema)),
                tuple(params),
            )
            rows = cur.fetchall()

    if not rows:
        raise RuntimeError(
            f"No snapshot state found for schema={schema} period={period_start}..{period_end}. "
            "Run unified ingestion first."
        )

    states = [
        SnapshotState(
            snapshot_id=str(row["snapshot_id"]),
            schema_name=str(row["schema_name"]),
            marketplace=str(row["marketplace"]),
            period_from=row["period_from"],
            period_to=row["period_to"],
            required_sources=list(row["required_sources"] or []),
            completed_sources=list(row["completed_sources"] or []),
            failed_sources=list(row["failed_sources"] or []),
            warning_sources=list(row["warning_sources"] or []),
            completeness_status=str(row["completeness_status"]),
            publish_status=str(row["publish_status"]),
            published_at=row["published_at"].isoformat() if row["published_at"] else None,
        )
        for row in rows
    ]

    blocked = [s for s in states if s.publish_status not in {"ready", "published"}]
    if blocked:
        details = ", ".join(
            f"{s.marketplace}:{s.publish_status}"
            + (f" failed={s.failed_sources}" if s.failed_sources else "")
            + (f" warn={s.warning_sources}" if s.warning_sources else "")
            for s in blocked
        )
        raise RuntimeError(
            "Snapshot is not publish-ready. "
            f"Blocked states: {details}. "
            "Use diagnostic_build for non-production diagnostics."
        )
    return states


def mark_snapshot_published(
    *,
    schema: str,
    period_start: str,
    period_end: str,
    marketplace: str | None = None,
    snapshot_id: str | None = None,
) -> int:
    where_parts: list[str] = ["period_from = %s", "period_to = %s"]
    params: list[object] = [date.fromisoformat(period_start), date.fromisoformat(period_end)]
    if marketplace:
        where_parts.append("marketplace = %s")
        params.append(marketplace)
    if snapshot_id:
        where_parts.append("snapshot_id = %s")
        params.append(snapshot_id)
    where_sql = " AND ".join(where_parts)

    query = sql.SQL(
        f"""
        UPDATE {{schema}}.ingestion_snapshot_state
        SET publish_status = 'published',
            published_at = now(),
            updated_at = now()
        WHERE {where_sql}
          AND publish_status IN ('ready', 'published')
        """
    ).format(schema=sql.Identifier(schema))

    updated_rows: int = 0
    with get_conn() as conn:
        def _tx(current_conn: object) -> None:
            nonlocal updated_rows
            cur = current_conn.execute(query, tuple(params))  # type: ignore[attr-defined]
            updated_rows = int(getattr(cur, "rowcount", 0) or 0)

        run_in_transaction(conn, _tx)
    return updated_rows


def get_semantic_mismatch_states(
    *,
    schema: str,
    period_start: str,
    period_end: str,
    marketplace: str | None = None,
) -> list[tuple[str, str]]:
    start_dt = date.fromisoformat(period_start)
    end_dt = date.fromisoformat(period_end)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT to_regclass(%s)",
                (f"{schema}.mart_semantic_correctness",),
            )
            regclass_row = cur.fetchone()
            if not regclass_row or not regclass_row.get("to_regclass"):
                return []

            where_parts = ["period_start = %s", "period_end = %s", "semantic_mismatch = TRUE"]
            params: list[object] = [start_dt, end_dt]
            if marketplace:
                where_parts.append("marketplace = %s")
                params.append(marketplace)
            where_sql = " AND ".join(where_parts)
            cur.execute(
                sql.SQL(
                    f"""
                    SELECT marketplace, semantic_mismatch_reasons
                    FROM {{schema}}.mart_semantic_correctness
                    WHERE {where_sql}
                    ORDER BY marketplace
                    """
                ).format(schema=sql.Identifier(schema)),
                tuple(params),
            )
            rows = cur.fetchall()
    return [
        (
            str(row.get("marketplace") or ""),
            str(row.get("semantic_mismatch_reasons") or ""),
        )
        for row in rows
    ]


def block_snapshot_semantic_mismatch(
    *,
    schema: str,
    period_start: str,
    period_end: str,
    marketplace: str | None = None,
) -> int:
    start_dt = date.fromisoformat(period_start)
    end_dt = date.fromisoformat(period_end)
    updated_rows = 0
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            where_parts = ["period_from = %s", "period_to = %s"]
            params: list[object] = [start_dt, end_dt]
            if marketplace:
                where_parts.append("marketplace = %s")
                params.append(marketplace)
            where_sql = " AND ".join(where_parts)
            cur.execute(
                sql.SQL(
                    f"""
                    SELECT snapshot_id, failed_sources
                    FROM {{schema}}.ingestion_snapshot_state
                    WHERE {where_sql}
                    """
                ).format(schema=sql.Identifier(schema)),
                tuple(params),
            )
            rows = cur.fetchall()

        def _tx(current_conn: object) -> None:
            nonlocal updated_rows
            for row in rows:
                failed_sources = list(row.get("failed_sources") or [])
                if "semantic_mismatch" not in failed_sources:
                    failed_sources.append("semantic_mismatch")
                cur2 = current_conn.execute(  # type: ignore[attr-defined]
                    sql.SQL(
                        """
                        UPDATE {schema}.ingestion_snapshot_state
                        SET failed_sources = %s::jsonb,
                            completeness_status = 'error',
                            publish_status = 'blocked',
                            updated_at = now()
                        WHERE snapshot_id = %s
                        """
                    ).format(schema=sql.Identifier(schema)),
                    (
                        json.dumps(sorted(set(str(v) for v in failed_sources)), ensure_ascii=False),
                        row["snapshot_id"],
                    ),
                )
                updated_rows += int(getattr(cur2, "rowcount", 0) or 0)

        run_in_transaction(conn, _tx)
    return updated_rows


def get_last_published_snapshot(
    *,
    schema: str,
    marketplace: str | None = None,
) -> SnapshotState | None:
    where_sql = "publish_status = 'published'"
    params: list[object] = []
    if marketplace:
        where_sql += " AND marketplace = %s"
        params.append(marketplace)

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                sql.SQL(
                    f"""
                    SELECT
                      snapshot_id,
                      schema_name,
                      marketplace,
                      period_from,
                      period_to,
                      required_sources,
                      completed_sources,
                      failed_sources,
                      warning_sources,
                      completeness_status,
                      publish_status,
                      published_at
                    FROM {{schema}}.ingestion_snapshot_state
                    WHERE {where_sql}
                    ORDER BY published_at DESC NULLS LAST, updated_at DESC
                    LIMIT 1
                    """
                ).format(schema=sql.Identifier(schema)),
                tuple(params),
            )
            row = cur.fetchone()
    if not row:
        return None
    return SnapshotState(
        snapshot_id=str(row["snapshot_id"]),
        schema_name=str(row["schema_name"]),
        marketplace=str(row["marketplace"]),
        period_from=row["period_from"],
        period_to=row["period_to"],
        required_sources=list(row["required_sources"] or []),
        completed_sources=list(row["completed_sources"] or []),
        failed_sources=list(row["failed_sources"] or []),
        warning_sources=list(row["warning_sources"] or []),
        completeness_status=str(row["completeness_status"]),
        publish_status=str(row["publish_status"]),
        published_at=row["published_at"].isoformat() if row["published_at"] else None,
    )
