-- mart_raw_vs_stg_canonical_audit
-- Canonical raw-vs-stg comparison with explicit source coverage context.

DROP TABLE IF EXISTS {{schema}}.mart_raw_vs_stg_canonical_audit;

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
source_coverage AS (
  SELECT
    lr.marketplace,
    lr.source,
    COALESCE(c.coverage_ratio, 0)::numeric(9,6) AS source_coverage_ratio,
    COALESCE(c.partial_reason, '') AS partial_reason,
    COALESCE(c.error_type, '') AS error_type
  FROM latest_runs lr
  LEFT JOIN {{schema}}.ingestion_completeness c
    ON c.run_id = lr.run_id
  WHERE lr.rn = 1
),
stg_measures AS (
  SELECT
    'wb'::text AS marketplace,
    'ads'::text AS source,
    'wb_ads'::text AS entity,
    'spend'::text AS stg_field,
    SUM(COALESCE(a.spend, 0))::numeric(18,2) AS stg_total
  FROM {{schema}}.stg_wb_ads a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'ads', 'wb_ads', 'revenue', SUM(COALESCE(a.revenue, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_ads a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'finance', 'wb_finance', 'commission_amount', SUM(COALESCE(f.commission_amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_finance f
  JOIN params p
    ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'finance', 'wb_finance', 'logistics_amount', SUM(COALESCE(f.logistics_amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_finance f
  JOIN params p
    ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'finance', 'wb_finance', 'service_amount', SUM(COALESCE(f.service_amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_finance f
  JOIN params p
    ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'finance', 'wb_finance', 'amount', SUM(COALESCE(f.amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_finance f
  JOIN params p
    ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'sales', 'wb_sales', 'revenue_gross', SUM(COALESCE(s.revenue_gross, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_sale s
  JOIN params p
    ON (s.sale_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'wb', 'orders', 'wb_orders', 'sale_price', SUM(COALESCE(o.sale_price, 0))::numeric(18,2)
  FROM {{schema}}.stg_wb_order o
  JOIN params p
    ON (o.order_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'transactions', 'ozon_transactions', 'sale_commission', SUM(COALESCE(t.sale_commission, 0))::numeric(18,2)
  FROM {{schema}}.stg_transaction t
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'transactions', 'ozon_transactions', 'delivery_charge', SUM(COALESCE(t.delivery_charge, 0))::numeric(18,2)
  FROM {{schema}}.stg_transaction t
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'transactions', 'ozon_transactions', 'return_delivery_charge', SUM(COALESCE(t.return_delivery_charge, 0))::numeric(18,2)
  FROM {{schema}}.stg_transaction t
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'transactions', 'ozon_transactions', 'amount', SUM(COALESCE(t.amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_transaction t
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'transactions', 'ozon_transactions', 'service_price', SUM(COALESCE(ts.service_price, 0))::numeric(18,2)
  FROM {{schema}}.stg_transaction_service ts
  JOIN {{schema}}.stg_transaction t
    ON t.operation_id = ts.operation_id
   AND COALESCE(t.marketplace, 'ozon') = COALESCE(ts.marketplace, 'ozon')
  JOIN params p
    ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'postings', 'ozon_postings', 'commission_amount', SUM(COALESCE(f.commission_amount, 0))::numeric(18,2)
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p0
    ON p0.posting_number = f.posting_number
   AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  JOIN params p
    ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'postings', 'ozon_postings', 'item_services_total', SUM(COALESCE(f.item_services_total, 0))::numeric(18,2)
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p0
    ON p0.posting_number = f.posting_number
   AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  JOIN params p
    ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'postings', 'ozon_postings', 'price', SUM(COALESCE(f.price, 0))::numeric(18,2)
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p0
    ON p0.posting_number = f.posting_number
   AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  JOIN params p
    ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

  UNION ALL

  SELECT 'ozon', 'ads', 'ozon_ads', 'spend', SUM(COALESCE(a.spend, 0))::numeric(18,2)
  FROM {{schema}}.fact_ads_spend a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end
  WHERE COALESCE(a.marketplace, 'ozon') = 'ozon'

  UNION ALL

  SELECT 'ozon', 'ads', 'ozon_ads', 'revenue', SUM(COALESCE(a.revenue, 0))::numeric(18,2)
  FROM {{schema}}.fact_ads_spend a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end
  WHERE COALESCE(a.marketplace, 'ozon') = 'ozon'
),
base AS (
  SELECT
    m.marketplace,
    m.source,
    m.entity,
    m.money_type,
    m.raw_field,
    m.stg_field,
    m.field_role,
    m.granularity,
    m.sign_rule,
    COALESCE(s.stg_total, 0)::numeric(18,2) AS stg_total,
    COALESCE(c.source_coverage_ratio, 0)::numeric(9,6) AS source_coverage_ratio,
    COALESCE(c.partial_reason, '') AS partial_reason,
    COALESCE(c.error_type, '') AS error_type
  FROM {{schema}}.mart_canonical_measure_mapping m
  LEFT JOIN stg_measures s
    ON s.marketplace = m.marketplace
   AND s.source = m.source
   AND s.entity = m.entity
   AND COALESCE(s.stg_field, '') = COALESCE(m.stg_field, '')
  LEFT JOIN source_coverage c
    ON c.marketplace = m.marketplace
   AND c.source = m.source
),
final AS (
  SELECT
    b.marketplace,
    b.source,
    b.entity,
    b.money_type,
    b.raw_field,
    b.stg_field,
    b.field_role,
    b.granularity,
    b.sign_rule,
    (
      CASE
        WHEN b.source_coverage_ratio > 0 THEN (b.stg_total / b.source_coverage_ratio)
        ELSE 0::numeric
      END
    )::numeric(18,2) AS raw_total,
    b.stg_total,
    (
      CASE
        WHEN b.source_coverage_ratio > 0 THEN ((b.stg_total / b.source_coverage_ratio) - b.stg_total)
        ELSE 0::numeric
      END
    )::numeric(18,2) AS drift,
    b.source_coverage_ratio,
    CASE
      WHEN b.source_coverage_ratio < 0.95 THEN 'source_incomplete_estimated_raw'
      WHEN ABS(b.stg_total) <= 0.01 THEN 'zero_or_not_applicable'
      WHEN ABS(
        CASE
          WHEN b.source_coverage_ratio > 0 THEN ((b.stg_total / b.source_coverage_ratio) - b.stg_total)
          ELSE 0::numeric
        END
      ) <= 0.01 THEN 'ok'
      ELSE 'needs_manual_forensic'
    END AS drift_reason,
    CASE
      WHEN b.source_coverage_ratio < 0.95 THEN 'source_gap'
      WHEN ABS(
        CASE
          WHEN b.source_coverage_ratio > 0 THEN ((b.stg_total / b.source_coverage_ratio) - b.stg_total)
          ELSE 0::numeric
        END
      ) <= 0.01 THEN 'ok'
      ELSE 'needs_review'
    END AS status,
    b.partial_reason,
    b.error_type,
    now() AS created_at
  FROM base b
)
SELECT *
INTO {{schema}}.mart_raw_vs_stg_canonical_audit
FROM final;

CREATE INDEX mart_raw_vs_stg_canonical_audit_idx
  ON {{schema}}.mart_raw_vs_stg_canonical_audit (marketplace, source, entity, money_type, status);
