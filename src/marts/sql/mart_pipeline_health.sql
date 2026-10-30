-- mart_pipeline_health
-- Последнее состояние ingestion + completeness + publish semantics по каждому source

DROP TABLE IF EXISTS {{schema}}.mart_pipeline_health;

WITH latest_run AS (
  SELECT
    r.run_id,
    r.marketplace,
    r.source,
    r.schema_name,
    r.mode,
    r.date_from,
    r.date_to,
    r.started_at,
    r.finished_at,
    r.status,
    ROW_NUMBER() OVER (
      PARTITION BY r.marketplace, r.source
      ORDER BY r.started_at DESC, r.run_id DESC
    ) AS rn
  FROM {{schema}}.ingestion_run r
),
selected_run AS (
  SELECT *
  FROM latest_run
  WHERE rn = 1
),
chunk_agg AS (
  WITH latest_chunk_attempt AS (
    SELECT
      c.*,
      regexp_replace(c.chunk_id, '_a[0-9]+$', '') AS chunk_group_id,
      ROW_NUMBER() OVER (
        PARTITION BY c.run_id, regexp_replace(c.chunk_id, '_a[0-9]+$', '')
        ORDER BY COALESCE(c.retry_attempts, 1) DESC, c.created_at DESC, c.chunk_id DESC
      ) AS rn
    FROM {{schema}}.ingestion_chunk_audit c
  )
  SELECT
    c.run_id,
    SUM(COALESCE(c.raw_files_count, 0))::bigint AS raw_files_count,
    SUM(COALESCE(c.raw_rows_count, 0))::bigint AS raw_rows_count,
    SUM(COALESCE(c.normalized_rows_count, 0))::bigint AS normalized_rows_count,
    SUM(COALESCE(c.loaded_rows_count, 0))::bigint AS loaded_rows_count,
    SUM(GREATEST(COALESCE(c.retry_attempts, 1) - 1, 0))::bigint AS retries_count
  FROM latest_chunk_attempt c
  WHERE c.rn = 1
  GROUP BY c.run_id
),
latest_dq AS (
  SELECT DISTINCT ON (d.marketplace)
    d.marketplace,
    d.unattributed_row_count,
    d.unattributed_cost_total
  FROM {{schema}}.mart_data_quality_30d d
  ORDER BY d.marketplace, d.period_end DESC, d.run_date DESC
),
attr_agg AS (
  SELECT
    sr.run_id,
    COUNT(*)::bigint AS attributed_rows_count
  FROM selected_run sr
  JOIN {{schema}}.fact_product_money_attribution a
    ON a.marketplace = sr.marketplace
   AND a.loaded_at >= sr.started_at - interval '1 hour'
  GROUP BY sr.run_id
),
snapshot_semantics AS (
  SELECT
    s.schema_name,
    s.marketplace,
    s.period_from,
    s.period_to,
    s.snapshot_id,
    s.publish_status,
    jsonb_array_length(COALESCE(s.required_sources, '[]'::jsonb)) AS required_count,
    jsonb_array_length(COALESCE(s.failed_sources, '[]'::jsonb)) AS failed_count,
    jsonb_array_length(COALESCE(s.warning_sources, '[]'::jsonb)) AS warning_count,
    (
      SELECT COUNT(*)::int
      FROM jsonb_array_elements_text(COALESCE(s.failed_sources, '[]'::jsonb)) AS f(source_name)
      WHERE f.source_name IN (
        SELECT req.source_name
        FROM jsonb_array_elements_text(COALESCE(s.required_sources, '[]'::jsonb)) AS req(source_name)
      )
    ) AS blocking_errors_count
  FROM {{schema}}.ingestion_snapshot_state s
),
marketplace_totals AS (
  SELECT
    sr.marketplace,
    SUM(COALESCE(comp.missing_days_count, 0))::bigint AS missing_days_total,
    SUM(COALESCE(comp.row_loss_ratio, 0))::numeric(18,6) AS row_loss_total
  FROM selected_run sr
  LEFT JOIN {{schema}}.ingestion_completeness comp
    ON comp.run_id = sr.run_id
  GROUP BY sr.marketplace
),
queue_primary AS (
  SELECT
    q.marketplace,
    q.source,
    q.state
  FROM {{schema}}.ingestion_backfill_queue q
),
queue_fallback AS (
  SELECT
    q.marketplace,
    q.source,
    CASE
      WHEN q.status = 'success' THEN 'success'
      WHEN q.status = 'failed' THEN 'manual_review'
      WHEN q.status = 'running' THEN 'running'
      ELSE 'pending'
    END AS state
  FROM {{schema}}.finance_backfill_queue q
  WHERE NOT EXISTS (
    SELECT 1
    FROM queue_primary p
    WHERE p.marketplace = q.marketplace
      AND p.source = q.source
  )
),
finance_queue AS (
  SELECT
    q.marketplace,
    q.source,
    q.state
  FROM queue_primary q
  UNION ALL
  SELECT
    q.marketplace,
    q.source,
    q.state
  FROM queue_fallback q
),
queue_stats AS (
  SELECT
    q.marketplace,
    q.source,
    COUNT(*) FILTER (
      WHERE q.state IN (
        'pending',
        'running',
        'retry',
        'waiting_rate_limit',
        'parse_error',
        'target_empty',
        'partial_progress',
        'resume_required'
      )
    )::bigint AS pending_days,
    COUNT(*) FILTER (WHERE q.state IN ('manual_review', 'stalled_day'))::bigint AS failed_days
  FROM finance_queue q
  GROUP BY q.marketplace, q.source
),
semantic_check AS (
  SELECT
    s.marketplace,
    s.period_start,
    s.period_end,
    COALESCE(s.semantic_mismatch, FALSE) AS semantic_mismatch,
    COALESCE(s.semantic_mismatch_reasons, '') AS semantic_mismatch_reasons
  FROM {{schema}}.mart_semantic_correctness s
),
unmatched_stats AS (
  SELECT
    u.marketplace,
    SUM(CASE WHEN u.incompleteness_stage IN ('source_gap', 'source_incomplete') THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS source_gap_amount,
    SUM(CASE WHEN u.incompleteness_stage IN ('parsing_gap', 'parsing_incomplete') THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS parsing_gap_amount,
    SUM(CASE WHEN u.incompleteness_stage IN ('match_gap', 'match_incomplete') THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS match_gap_amount,
    SUM(CASE WHEN u.match_status = 'unmatched_explainable' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS unmatched_explainable_amount,
    SUM(CASE WHEN u.match_status = 'unmatched_unclassified' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS unmatched_unclassified_amount,
    SUM(CASE WHEN u.match_status = 'ambiguous' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS ambiguous_amount
  FROM {{schema}}.fact_unmatched_money u
  GROUP BY u.marketplace
)
SELECT
  sr.marketplace,
  sr.source,
  COALESCE(sr.finished_at, sr.started_at) AS updated_at,
  COALESCE(ch.raw_files_count, 0) AS raw_files_count,
  COALESCE(ch.raw_rows_count, 0) AS raw_rows_count,
  COALESCE(ch.normalized_rows_count, 0) AS normalized_rows_count,
  COALESCE(ch.loaded_rows_count, 0) AS loaded_rows_count,
  COALESCE(ch.retries_count, 0) AS retries_count,
  COALESCE(comp.expected_days, 0) AS expected_days,
  COALESCE(comp.actual_days, 0) AS actual_days,
  COALESCE(comp.missing_days_count, 0) AS missing_days_count,
  (
    CASE
      WHEN COALESCE(comp.coverage_ratio, 0) = 0
        AND COALESCE(comp.expected_days, 0) > 0
        AND COALESCE(comp.actual_days, 0) > 0
        THEN COALESCE(comp.actual_days, 0)::numeric / NULLIF(COALESCE(comp.expected_days, 0)::numeric, 0)
      ELSE COALESCE(comp.coverage_ratio, 0)
    END
  )::numeric(9,6) AS coverage_ratio,
  COALESCE(comp.error_type, 'unknown') AS error_type,
  CASE
    WHEN COALESCE(sc.semantic_mismatch, FALSE) THEN COALESCE(sc.semantic_mismatch_reasons, 'semantic_mismatch')
    ELSE COALESCE(comp.partial_reason, comp.error_type, '')
  END AS error_reason,
  COALESCE(attr.attributed_rows_count, 0) AS attributed_rows_count,
  COALESCE(dq.unattributed_row_count, 0) AS unattributed_rows_count,
  COALESCE(dq.unattributed_cost_total, 0)::numeric(18,2) AS unattributed_money_total,
  COALESCE(comp.row_loss_ratio, 0)::numeric(9,6) AS row_loss_ratio,
  COALESCE(mt.missing_days_total, 0) AS missing_days_total,
  COALESCE(mt.row_loss_total, 0)::numeric(18,6) AS row_loss_total,
  snap.snapshot_id,
  COALESCE(snap.publish_status, 'draft') AS publish_status,
  CASE
    WHEN snap.required_count = 0 THEN NULL
    WHEN COALESCE(snap.blocking_errors_count, 0) = 0 THEN TRUE
    ELSE FALSE
  END AS critical_sources_ok,
  CASE
    WHEN snap.required_count = 0 THEN NULL
    WHEN (COALESCE(snap.failed_count, 0) - COALESCE(snap.blocking_errors_count, 0)) = 0 THEN TRUE
    ELSE FALSE
  END AS noncritical_sources_ok,
  CASE
    WHEN COALESCE(snap.publish_status, 'draft') IN ('published', 'ready') THEN TRUE
    ELSE FALSE
  END AS usable_for_ui,
  COALESCE(snap.warning_count, 0) AS warnings_count,
  COALESCE(snap.blocking_errors_count, 0) AS blocking_errors_count,
  COALESCE(fq.pending_days, 0) AS backfill_queue_pending_days,
  COALESCE(fq.failed_days, 0) AS backfill_queue_failed_days,
  COALESCE(sc.semantic_mismatch, FALSE) AS semantic_mismatch,
  COALESCE(sc.semantic_mismatch_reasons, '') AS semantic_mismatch_reasons,
  COALESCE(us.source_gap_amount, 0)::numeric(18,2) AS source_gap_amount,
  COALESCE(us.parsing_gap_amount, 0)::numeric(18,2) AS parsing_gap_amount,
  COALESCE(us.match_gap_amount, 0)::numeric(18,2) AS match_gap_amount,
  COALESCE(us.source_gap_amount, 0)::numeric(18,2) AS source_incomplete_amount,
  COALESCE(us.parsing_gap_amount, 0)::numeric(18,2) AS parsing_incomplete_amount,
  COALESCE(us.match_gap_amount, 0)::numeric(18,2) AS match_incomplete_amount,
  COALESCE(us.unmatched_explainable_amount, 0)::numeric(18,2) AS unmatched_explainable_amount,
  COALESCE(us.unmatched_unclassified_amount, 0)::numeric(18,2) AS unmatched_unclassified_amount,
  COALESCE(us.ambiguous_amount, 0)::numeric(18,2) AS ambiguous_amount,
  CASE
    WHEN COALESCE(sc.semantic_mismatch, FALSE) THEN 'error'
    WHEN sr.status = 'error' OR COALESCE(comp.status, '') = 'error' THEN 'error'
    WHEN COALESCE(us.unmatched_unclassified_amount, 0) >= 1000::numeric THEN 'warning'
    WHEN COALESCE(comp.status, '') = 'warning'
      OR COALESCE(comp.missing_days_count, 0) > 0
      OR COALESCE(comp.row_loss_ratio, 0) > 0.01
      OR COALESCE(snap.publish_status, 'draft') IN ('partial', 'blocked')
      THEN 'warning'
    ELSE 'ok'
  END AS status
INTO {{schema}}.mart_pipeline_health
FROM selected_run sr
LEFT JOIN chunk_agg ch
  ON ch.run_id = sr.run_id
LEFT JOIN {{schema}}.ingestion_completeness comp
  ON comp.run_id = sr.run_id
LEFT JOIN attr_agg attr
  ON attr.run_id = sr.run_id
LEFT JOIN latest_dq dq
  ON dq.marketplace = sr.marketplace
LEFT JOIN marketplace_totals mt
  ON mt.marketplace = sr.marketplace
LEFT JOIN snapshot_semantics snap
  ON snap.schema_name = sr.schema_name
 AND snap.marketplace = sr.marketplace
 AND snap.period_from = sr.date_from
 AND snap.period_to = sr.date_to
LEFT JOIN queue_stats fq
  ON fq.marketplace = sr.marketplace
 AND fq.source = sr.source
LEFT JOIN semantic_check sc
  ON sc.marketplace = sr.marketplace
 AND sc.period_start = sr.date_from
 AND sc.period_end = sr.date_to
LEFT JOIN unmatched_stats us
  ON us.marketplace = sr.marketplace;

CREATE INDEX mart_pipeline_health_mkt_source_idx
  ON {{schema}}.mart_pipeline_health (marketplace, source, updated_at DESC);
