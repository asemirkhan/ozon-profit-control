-- mart_granularity_reconciliation_report
-- Compares row/grain continuity across raw(stored) -> stg -> attribution -> mart layers.

DROP TABLE IF EXISTS {{schema}}.mart_granularity_reconciliation_report;

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
  SELECT marketplace, source, run_id
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
raw_proxy AS (
  SELECT
    sr.marketplace,
    sr.source,
    SUM(COALESCE(c.raw_rows_count, 0))::bigint AS raw_rows_proxy,
    SUM(COALESCE(c.raw_files_count, 0))::bigint AS raw_files_count,
    COUNT(*) FILTER (WHERE c.status = 'ok')::bigint AS success_chunk_count,
    COUNT(*) FILTER (WHERE c.status <> 'ok')::bigint AS failed_chunk_count
  FROM selected_runs sr
  LEFT JOIN chunk_latest c
    ON c.run_id = sr.run_id
   AND c.rn = 1
  GROUP BY sr.marketplace, sr.source
),
source_cov AS (
  SELECT
    sr.marketplace,
    sr.source,
    COALESCE(c.coverage_ratio, 0)::numeric(9,6) AS source_coverage_ratio,
    COALESCE(c.missing_days_count, 0)::int AS missing_days_count,
    COALESCE(c.partial_reason, '') AS partial_reason,
    COALESCE(c.error_type, '') AS error_type
  FROM selected_runs sr
  LEFT JOIN {{schema}}.ingestion_completeness c
    ON c.run_id = sr.run_id
),
entity_layer AS (
  SELECT
    'wb'::text AS marketplace,
    'ads'::text AS source,
    'wb_ads'::text AS entity,
    'date+campaign_id+offer_id+nm_id+app_type'::text AS expected_grain,
    COUNT(*)::bigint AS stg_rows,
    COUNT(DISTINCT (a.date::text || '|' || a.campaign_id || '|' || a.offer_id || '|' || a.nm_id || '|' || COALESCE(a.app_type, '~')))::bigint AS stg_distinct_grain,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution f
      JOIN params p ON f.event_date BETWEEN p.period_start AND p.period_end
      WHERE f.marketplace = 'wb'
        AND f.source_table = 'stg_wb_ads'
        AND f.money_bucket = 'ads'
    ) AS attribution_rows,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'wb'
        AND COALESCE(m.ads_spend, 0) <> 0
    ) AS mart_rows
  FROM {{schema}}.stg_wb_ads a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'wb',
    'finance',
    'wb_finance',
    'finance_id',
    COUNT(*)::bigint,
    COUNT(DISTINCT f.finance_id)::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'wb'
        AND fa.source_table = 'stg_wb_finance'
    ),
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'wb'
        AND (
          COALESCE(m.commission_amount, 0) <> 0
          OR COALESCE(m.logistics_amount, 0) <> 0
          OR COALESCE(m.services_amount, 0) <> 0
          OR COALESCE(m.storage_amount, 0) <> 0
          OR COALESCE(m.payment_processing_amount, 0) <> 0
          OR COALESCE(m.penalties_amount, 0) <> 0
          OR COALESCE(m.other_amount, 0) <> 0
          OR COALESCE(m.returns_amount, 0) <> 0
        )
    )
  FROM {{schema}}.stg_wb_finance f
  JOIN params p
    ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'wb',
    'sales',
    'wb_sales',
    'sale_id',
    COUNT(*)::bigint,
    COUNT(DISTINCT s.sale_id)::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'wb'
        AND fa.source_table = 'stg_wb_sale'
    ),
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'wb'
        AND COALESCE(m.revenue_gross, 0) <> 0
    )
  FROM {{schema}}.stg_wb_sale s
  JOIN params p
    ON (s.sale_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'wb',
    'orders',
    'wb_orders',
    'order_id',
    COUNT(*)::bigint,
    COUNT(DISTINCT o.order_id)::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'wb'
        AND fa.source_table = 'stg_wb_order'
    ),
    0::bigint
  FROM {{schema}}.stg_wb_order o
  JOIN params p
    ON (o.order_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'ozon',
    'transactions',
    'ozon_transactions',
    'operation_id',
    COUNT(*)::bigint,
    COUNT(DISTINCT t.operation_id)::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'ozon'
        AND fa.source_table IN ('stg_transaction', 'stg_transaction_service')
    ),
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'ozon'
        AND (
          COALESCE(m.commission_amount, 0) <> 0
          OR COALESCE(m.logistics_amount, 0) <> 0
          OR COALESCE(m.services_amount, 0) <> 0
          OR COALESCE(m.storage_amount, 0) <> 0
          OR COALESCE(m.payment_processing_amount, 0) <> 0
          OR COALESCE(m.penalties_amount, 0) <> 0
          OR COALESCE(m.other_amount, 0) <> 0
          OR COALESCE(m.returns_amount, 0) <> 0
        )
    )
  FROM {{schema}}.stg_transaction t
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'ozon',
    'postings',
    'ozon_postings',
    'posting_number+product_id',
    COUNT(*)::bigint,
    COUNT(DISTINCT (f.posting_number || '|' || COALESCE(f.product_id, '~')))::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'ozon'
        AND fa.source_table = 'stg_posting_item_financial'
    ),
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'ozon'
        AND COALESCE(m.revenue_gross, 0) <> 0
    )
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p0
    ON p0.posting_number = f.posting_number
   AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  JOIN params p
    ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT
    'ozon',
    'ads',
    'ozon_ads',
    'date+campaign_id+offer_id+sku',
    COUNT(*)::bigint,
    COUNT(DISTINCT (a.date::text || '|' || a.campaign_id || '|' || a.offer_id || '|' || a.sku))::bigint,
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.fact_product_money_attribution fa
      JOIN params p ON fa.event_date BETWEEN p.period_start AND p.period_end
      WHERE fa.marketplace = 'ozon'
        AND fa.money_bucket = 'ads'
    ),
    (
      SELECT COUNT(*)::bigint
      FROM {{schema}}.mart_pnl_daily_by_product m
      JOIN params p ON m.date BETWEEN p.period_start AND p.period_end
      WHERE m.marketplace = 'ozon'
        AND COALESCE(m.ads_spend, 0) <> 0
    )
  FROM {{schema}}.fact_ads_spend a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end
  WHERE COALESCE(a.marketplace, 'ozon') = 'ozon'
)
SELECT
  e.marketplace,
  e.source,
  e.entity,
  e.expected_grain,
  COALESCE(r.raw_rows_proxy, 0)::bigint AS raw_rows_proxy,
  COALESCE(r.raw_files_count, 0)::bigint AS raw_files_count,
  COALESCE(r.success_chunk_count, 0)::bigint AS success_chunk_count,
  COALESCE(r.failed_chunk_count, 0)::bigint AS failed_chunk_count,
  e.stg_rows,
  e.stg_distinct_grain,
  e.attribution_rows,
  e.mart_rows,
  (COALESCE(r.raw_rows_proxy, 0) - e.stg_rows)::bigint AS row_drift_raw_to_stg,
  (e.stg_rows - e.stg_distinct_grain)::bigint AS row_drift_stg_to_grain,
  COALESCE(c.source_coverage_ratio, 0)::numeric(9,6) AS source_coverage_ratio,
  COALESCE(c.missing_days_count, 0)::int AS missing_days_count,
  COALESCE(c.partial_reason, '') AS partial_reason,
  COALESCE(c.error_type, '') AS error_type,
  CASE
    WHEN COALESCE(c.source_coverage_ratio, 0) < 0.95 THEN 'source_gap'
    WHEN (e.stg_rows - e.stg_distinct_grain) <> 0 THEN 'granularity_mismatch'
    WHEN e.stg_rows > 0 AND e.attribution_rows = 0 THEN 'match_gap_or_mapping_gap'
    ELSE 'ok'
  END AS status,
  now() AS created_at
INTO {{schema}}.mart_granularity_reconciliation_report
FROM entity_layer e
LEFT JOIN raw_proxy r
  ON r.marketplace = e.marketplace
 AND r.source = e.source
LEFT JOIN source_cov c
  ON c.marketplace = e.marketplace
 AND c.source = e.source;

CREATE INDEX mart_granularity_reconciliation_report_idx
  ON {{schema}}.mart_granularity_reconciliation_report (marketplace, source, entity, status);
