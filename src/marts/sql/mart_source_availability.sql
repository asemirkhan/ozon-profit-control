-- mart_source_availability
-- Source availability / ingestion completeness surface for forensic and publish gating.

DROP TABLE IF EXISTS {{schema}}.mart_source_availability;

WITH params AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
latest_runs AS (
  SELECT
    r.marketplace,
    r.source,
    r.run_id,
    r.schema_name,
    r.date_from,
    r.date_to,
    r.status AS run_status,
    r.started_at,
    r.finished_at,
    ROW_NUMBER() OVER (
      PARTITION BY r.marketplace, r.source
      ORDER BY r.started_at DESC, r.run_id DESC
    ) AS rn
  FROM {{schema}}.ingestion_run r
  JOIN params p
    ON r.date_from = p.period_start
   AND r.date_to = p.period_end
),
selected_runs AS (
  SELECT *
  FROM latest_runs
  WHERE rn = 1
),
chunk_latest AS (
  SELECT
    c.*,
    regexp_replace(c.chunk_id, '_a[0-9]+$', '') AS chunk_group_id,
    ROW_NUMBER() OVER (
      PARTITION BY c.run_id, regexp_replace(c.chunk_id, '_a[0-9]+$', '')
      ORDER BY COALESCE(c.retry_attempts, 1) DESC, c.created_at DESC, c.chunk_id DESC
    ) AS rn
  FROM {{schema}}.ingestion_chunk_audit c
  JOIN selected_runs sr
    ON sr.run_id = c.run_id
),
chunk_stats AS (
  SELECT
    c.run_id,
    COUNT(*)::bigint AS total_chunk_count,
    COUNT(*) FILTER (WHERE c.status = 'ok')::bigint AS success_chunk_count,
    COUNT(*) FILTER (WHERE c.status <> 'ok')::bigint AS failed_chunk_count,
    COUNT(*) FILTER (
      WHERE COALESCE(c.error_type, '') = 'rate_limit'
         OR LOWER(COALESCE(c.error_message, '')) LIKE '%429%'
    )::bigint AS rate_limit_chunk_count,
    SUM(COALESCE(c.raw_files_count, 0))::bigint AS raw_files_count,
    SUM(COALESCE(c.raw_rows_count, 0))::bigint AS raw_rows_count,
    SUM(COALESCE(c.normalized_rows_count, 0))::bigint AS normalized_rows_count,
    SUM(COALESCE(c.loaded_rows_count, 0))::bigint AS loaded_rows_count,
    SUM(GREATEST(COALESCE(c.retry_attempts, 1) - 1, 0))::bigint AS retry_attempts
  FROM chunk_latest c
  WHERE c.rn = 1
  GROUP BY c.run_id
),
request_stats AS (
  SELECT
    r.run_id,
    COUNT(*)::bigint AS request_count,
    COUNT(*) FILTER (WHERE q.http_status = 429)::bigint AS request_429_count,
    COUNT(*) FILTER (WHERE COALESCE(q.success, FALSE))::bigint AS request_success_count,
    COUNT(DISTINCT q.chunk_id) FILTER (WHERE q.http_status = 429)::bigint AS rate_limit_chunk_count_by_request,
    SUM(COALESCE(q.cooldown_wait_sec, 0))::numeric(18,3) AS cooldown_wait_sec_total
  FROM selected_runs r
  LEFT JOIN {{schema}}.ingestion_request_log q
    ON q.run_id = r.run_id
  GROUP BY r.run_id
),
queue_primary AS (
  SELECT
    marketplace,
    source,
    state
  FROM {{schema}}.ingestion_backfill_queue
),
queue_fallback AS (
  SELECT
    marketplace,
    source,
    CASE
      WHEN status = 'success' THEN 'success'
      WHEN status = 'failed' THEN 'manual_review'
      WHEN status = 'running' THEN 'running'
      ELSE 'pending'
    END AS state
  FROM {{schema}}.finance_backfill_queue
  WHERE NOT EXISTS (
    SELECT 1
    FROM queue_primary p
    WHERE p.marketplace = finance_backfill_queue.marketplace
      AND p.source = finance_backfill_queue.source
  )
),
queue_union AS (
  SELECT marketplace, source, state
  FROM queue_primary
  UNION ALL
  SELECT marketplace, source, state
  FROM queue_fallback
),
queue_state AS (
  SELECT
    marketplace,
    source,
    COUNT(*) FILTER (
      WHERE state IN (
        'pending',
        'retry',
        'waiting_rate_limit',
        'parse_error',
        'target_empty',
        'partial_progress',
        'resume_required'
      )
    )::bigint AS queue_pending,
    COUNT(*) FILTER (WHERE state = 'running')::bigint AS queue_running,
    COUNT(*) FILTER (WHERE state IN ('manual_review', 'stalled_day'))::bigint AS queue_failed,
    COUNT(*) FILTER (WHERE state = 'success')::bigint AS queue_success
  FROM queue_union
  GROUP BY marketplace, source
),
snapshot_state AS (
  SELECT
    s.schema_name,
    s.marketplace,
    s.period_from,
    s.period_to,
    s.snapshot_id,
    s.publish_status
  FROM {{schema}}.ingestion_snapshot_state s
),
availability AS (
  SELECT
    sr.run_id,
    sr.marketplace AS source_system,
    sr.source AS entity,
    sr.date_from AS period_start,
    sr.date_to AS period_end,
    COALESCE(c.expected_days, 0)::int AS expected_days,
    COALESCE(c.actual_days, 0)::int AS actual_days,
    COALESCE(c.missing_days_count, 0)::int AS missing_days_count,
    (
      CASE
        WHEN COALESCE(c.coverage_ratio, 0) = 0
          AND COALESCE(c.expected_days, 0) > 0
          AND COALESCE(c.actual_days, 0) > 0
          THEN COALESCE(c.actual_days, 0)::numeric / NULLIF(COALESCE(c.expected_days, 0)::numeric, 0)
        ELSE COALESCE(c.coverage_ratio, 0)
      END
    )::numeric(9,6) AS coverage_ratio,
    COALESCE(c.status, sr.run_status) AS status,
    COALESCE(c.error_type, '') AS error_type,
    COALESCE(c.partial_reason, c.error_type, '') AS error_reason,
    COALESCE(rs.request_count, 0)::bigint AS request_count,
    COALESCE(cs.success_chunk_count, 0)::bigint AS success_chunk_count,
    COALESCE(cs.failed_chunk_count, 0)::bigint AS failed_chunk_count,
    GREATEST(
      COALESCE(cs.rate_limit_chunk_count, 0),
      COALESCE(rs.rate_limit_chunk_count_by_request, 0)
    )::bigint AS rate_limit_chunk_count,
    COALESCE(cs.retry_attempts, 0)::bigint AS retry_attempts,
    COALESCE(cs.raw_files_count, 0)::bigint AS raw_files_count,
    COALESCE(cs.raw_rows_count, 0)::bigint AS raw_rows_count,
    COALESCE(cs.normalized_rows_count, 0)::bigint AS normalized_rows_count,
    COALESCE(cs.loaded_rows_count, 0)::bigint AS loaded_rows_count,
    (
      CASE
        WHEN COALESCE(cs.raw_rows_count, 0) <= 0 THEN 1::numeric
        ELSE COALESCE(cs.loaded_rows_count, 0)::numeric / NULLIF(COALESCE(cs.raw_rows_count, 0)::numeric, 0)
      END
    )::numeric(9,6) AS rows_coverage_ratio,
    COALESCE(rs.request_429_count, 0)::bigint AS request_429_count,
    COALESCE(rs.request_success_count, 0)::bigint AS request_success_count,
    COALESCE(rs.cooldown_wait_sec_total, 0)::numeric(18,3) AS cooldown_wait_sec_total,
    COALESCE(q.queue_pending, 0)::bigint AS queue_pending,
    COALESCE(q.queue_running, 0)::bigint AS queue_running,
    COALESCE(q.queue_failed, 0)::bigint AS queue_failed,
    COALESCE(q.queue_success, 0)::bigint AS queue_success,
    ss.snapshot_id,
    COALESCE(ss.publish_status, 'draft') AS publish_status,
    (
      CASE
        WHEN COALESCE(ss.publish_status, 'draft') IN ('published', 'ready')
          AND COALESCE(c.usable_for_publish, FALSE)
          THEN TRUE
        ELSE FALSE
      END
    ) AS usable_for_ui,
    now() AS created_at
  FROM selected_runs sr
  LEFT JOIN {{schema}}.ingestion_completeness c
    ON c.run_id = sr.run_id
  LEFT JOIN chunk_stats cs
    ON cs.run_id = sr.run_id
  LEFT JOIN request_stats rs
    ON rs.run_id = sr.run_id
  LEFT JOIN queue_state q
    ON q.marketplace = sr.marketplace
   AND q.source = sr.source
  LEFT JOIN snapshot_state ss
    ON ss.schema_name = sr.schema_name
   AND ss.marketplace = sr.marketplace
   AND ss.period_from = sr.date_from
   AND ss.period_to = sr.date_to
)
SELECT *
INTO {{schema}}.mart_source_availability
FROM availability;

CREATE INDEX mart_source_availability_idx
  ON {{schema}}.mart_source_availability (source_system, entity, period_start, period_end);
