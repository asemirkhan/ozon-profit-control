-- mart_unmatched_money
-- Explicit layer for non-product-allocated money with explainable candidate mapping.

DROP TABLE IF EXISTS {{schema}}.fact_unmatched_money;

WITH params AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
base AS (
  SELECT
    md5(
      m.marketplace || '|' || m.source_table || '|' || m.source_record_id || '|' || m.event_date::text || '|' || m.money_bucket
    ) AS unmatched_id,
    m.marketplace,
    m.event_date,
    m.money_bucket AS money_type,
    COALESCE(m.amount, 0)::numeric(18,2) AS amount,
    ABS(COALESCE(m.amount, 0))::numeric(18,2) AS amount_abs,
    m.source_table AS source_entity,
    m.source_record_id AS raw_record_id,
    m.attribution_method,
    m.attribution_confidence
  FROM {{schema}}.fact_product_money_attribution m
  JOIN params p
    ON m.event_date >= p.period_start
   AND m.event_date <= p.period_end
  WHERE m.canonical_product_key = '~'
    AND m.money_component_type = 'expense'
    -- Store-level expenses (for example the WB «Джем» subscription) are
    -- intentionally not assigned to a product and must not be reported as
    -- unmatched money.
    AND COALESCE(m.attribution_method, '') <> 'store_level'
),
source_name_map AS (
  SELECT
    b.*,
    CASE
      WHEN b.marketplace = 'wb' AND b.source_entity = 'stg_wb_ads' THEN 'ads'
      WHEN b.marketplace = 'wb' AND b.source_entity = 'stg_wb_finance' THEN 'finance'
      WHEN b.marketplace = 'ozon' AND b.source_entity = 'stg_transaction' THEN 'transactions'
      WHEN b.marketplace = 'ozon' AND b.source_entity = 'stg_posting_item_financial' THEN 'postings'
      ELSE b.source_entity
    END AS source_name
  FROM base b
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
source_state AS (
  SELECT
    lr.marketplace,
    lr.source,
    COALESCE(c.partial_reason, '') AS partial_reason,
    COALESCE(c.error_type, '') AS error_type,
    COALESCE(c.status, '') AS completeness_status
  FROM latest_runs lr
  LEFT JOIN {{schema}}.ingestion_completeness c
    ON c.run_id = lr.run_id
  WHERE lr.rn = 1
),
wb_finance_src AS (
  SELECT
    b.unmatched_id,
    f.operation_type,
    f.order_id,
    f.sale_id,
    NULL::text AS posting_id,
    NULL::text AS transaction_id,
    f.finance_id AS operation_id,
    NULL::text AS campaign_id,
    NULL::text AS advert_id,
    NULL::text AS ad_id,
    NULL::text AS document_id,
    NULLIF(TRIM(f.nm_id), '') AS nm_id,
    NULL::text AS sku,
    NULLIF(TRIM(f.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    NULL::text AS vendor_code,
    NULL::text AS supplier_article,
    NULL::text AS product_name_raw,
    NULL::text AS brand_raw,
    NULL::text AS subject_raw,
    f.raw_payload AS raw_json
  FROM source_name_map b
  JOIN {{schema}}.stg_wb_finance f
    ON b.source_entity = 'stg_wb_finance'
   AND f.finance_id = split_part(b.raw_record_id, ':', 1)
),
wb_ads_src AS (
  SELECT
    b.unmatched_id,
    NULL::text AS operation_type,
    NULL::text AS order_id,
    NULL::text AS sale_id,
    NULL::text AS posting_id,
    NULL::text AS transaction_id,
    NULL::text AS operation_id,
    x.campaign_id,
    x.campaign_id AS advert_id,
    x.campaign_id AS ad_id,
    NULL::text AS document_id,
    NULLIF(TRIM(x.nm_id), '') AS nm_id,
    NULL::text AS sku,
    NULLIF(TRIM(x.offer_id), '') AS offer_id,
    NULL::text AS barcode,
    NULL::text AS vendor_code,
    NULL::text AS supplier_article,
    NULLIF(TRIM(x.campaign_name), '') AS product_name_raw,
    NULL::text AS brand_raw,
    NULL::text AS subject_raw,
    x.raw_payload AS raw_json
  FROM source_name_map b
  JOIN LATERAL (
    SELECT a.*
    FROM {{schema}}.stg_wb_ads a
    WHERE b.source_entity = 'stg_wb_ads'
      AND a.date = b.event_date
      AND a.campaign_id = split_part(b.raw_record_id, ':', 1)
      AND COALESCE(a.offer_id, '~') = COALESCE(NULLIF(split_part(b.raw_record_id, ':', 2), ''), '~')
    ORDER BY COALESCE(a.spend, 0) DESC, COALESCE(a.nm_id, '~')
    LIMIT 1
  ) x ON TRUE
),
ozon_tx_src AS (
  SELECT
    b.unmatched_id,
    t.operation_type AS operation_type,
    NULL::text AS order_id,
    NULL::text AS sale_id,
    NULLIF(TRIM(t.posting_number), '') AS posting_id,
    t.operation_id AS transaction_id,
    t.operation_id AS operation_id,
    NULL::text AS campaign_id,
    NULL::text AS advert_id,
    NULL::text AS ad_id,
    NULL::text AS document_id,
    NULL::text AS nm_id,
    NULLIF(TRIM(ti.sku), '') AS sku,
    NULL::text AS offer_id,
    NULL::text AS barcode,
    NULL::text AS vendor_code,
    NULL::text AS supplier_article,
    NULLIF(TRIM(ti.item_name), '') AS product_name_raw,
    NULL::text AS brand_raw,
    NULL::text AS subject_raw,
    NULL::jsonb AS raw_json
  FROM source_name_map b
  JOIN {{schema}}.stg_transaction t
    ON b.source_entity = 'stg_transaction'
   AND t.operation_id = split_part(b.raw_record_id, ':', 1)
  LEFT JOIN LATERAL (
    SELECT i.sku, i.item_name
    FROM {{schema}}.stg_transaction_item i
    WHERE i.operation_id = t.operation_id
      AND COALESCE(i.marketplace, 'ozon') = COALESCE(t.marketplace, 'ozon')
    ORDER BY i.sku
    LIMIT 1
  ) ti ON TRUE
),
ozon_posting_fin_src AS (
  SELECT
    b.unmatched_id,
    NULL::text AS operation_type,
    NULL::text AS order_id,
    NULL::text AS sale_id,
    NULLIF(TRIM(f.posting_number), '') AS posting_id,
    NULL::text AS transaction_id,
    f.posting_number || ':' || COALESCE(f.product_id, '~') AS operation_id,
    NULL::text AS campaign_id,
    NULL::text AS advert_id,
    NULL::text AS ad_id,
    f.posting_number AS document_id,
    NULL::text AS nm_id,
    NULLIF(TRIM(f.sku), '') AS sku,
    NULLIF(TRIM(i.offer_id), '') AS offer_id,
    NULL::text AS barcode,
    NULL::text AS vendor_code,
    NULL::text AS supplier_article,
    NULL::text AS product_name_raw,
    NULL::text AS brand_raw,
    NULL::text AS subject_raw,
    NULL::jsonb AS raw_json
  FROM source_name_map b
  JOIN {{schema}}.stg_posting_item_financial f
    ON b.source_entity = 'stg_posting_item_financial'
   AND f.posting_number = split_part(b.raw_record_id, ':', 1)
   AND COALESCE(NULLIF(f.product_id, ''), '~') = COALESCE(NULLIF(split_part(b.raw_record_id, ':', 2), ''), '~')
  LEFT JOIN LATERAL (
    SELECT si.offer_id
    FROM {{schema}}.stg_posting_item si
    WHERE si.posting_number = f.posting_number
      AND (
        (NULLIF(si.product_id, '~') IS NOT NULL AND si.product_id = f.product_id)
        OR (NULLIF(si.sku, '~') IS NOT NULL AND si.sku = f.sku)
      )
    ORDER BY si.offer_id
    LIMIT 1
  ) i ON TRUE
),
source_union AS (
  SELECT * FROM wb_finance_src
  UNION ALL
  SELECT * FROM wb_ads_src
  UNION ALL
  SELECT * FROM ozon_tx_src
  UNION ALL
  SELECT * FROM ozon_posting_fin_src
),
enriched AS (
  SELECT
    b.unmatched_id,
    b.marketplace,
    b.source_entity,
    b.source_name,
    b.money_type,
    b.amount,
    b.amount_abs,
    b.event_date,
    b.raw_record_id,
    u.campaign_id,
    u.advert_id,
    u.ad_id,
    u.order_id,
    u.posting_id,
    u.document_id,
    u.operation_id,
    u.transaction_id,
    u.sale_id,
    u.nm_id,
    u.sku,
    u.offer_id,
    u.barcode,
    u.vendor_code,
    u.supplier_article,
    u.product_name_raw,
    u.brand_raw,
    u.subject_raw,
    u.raw_json,
    b.attribution_method,
    b.attribution_confidence,
    COALESCE(ss.partial_reason, '') AS source_partial_reason,
    COALESCE(ss.error_type, '') AS source_error_type,
    COALESCE(ss.completeness_status, '') AS source_completeness_status
  FROM source_name_map b
  LEFT JOIN source_union u
    ON u.unmatched_id = b.unmatched_id
  LEFT JOIN source_state ss
    ON ss.marketplace = b.marketplace
   AND ss.source = b.source_name
),
bridge_candidates AS (
  SELECT
    e.unmatched_id,
    b.canonical_product_key,
    CASE
      WHEN e.nm_id IS NOT NULL AND b.nm_id = e.nm_id THEN 'nm_id'
      WHEN e.offer_id IS NOT NULL AND e.offer_id <> '~' AND b.offer_id = e.offer_id THEN 'offer_id'
      WHEN e.sku IS NOT NULL AND b.sku = e.sku THEN 'sku'
      WHEN e.barcode IS NOT NULL AND b.barcode = e.barcode THEN 'barcode'
      WHEN e.vendor_code IS NOT NULL AND b.vendor_code = e.vendor_code THEN 'vendor_code'
      WHEN e.supplier_article IS NOT NULL AND b.item_code = e.supplier_article THEN 'supplier_article'
      ELSE 'bridge'
    END AS key_type,
    CASE
      WHEN e.nm_id IS NOT NULL AND b.nm_id = e.nm_id THEN 10
      WHEN e.offer_id IS NOT NULL AND e.offer_id <> '~' AND b.offer_id = e.offer_id THEN 9
      WHEN e.sku IS NOT NULL AND b.sku = e.sku THEN 8
      WHEN e.barcode IS NOT NULL AND b.barcode = e.barcode THEN 7
      WHEN e.vendor_code IS NOT NULL AND b.vendor_code = e.vendor_code THEN 6
      WHEN e.supplier_article IS NOT NULL AND b.item_code = e.supplier_article THEN 5
      ELSE 1
    END AS priority,
    1.0::numeric AS score
  FROM enriched e
  JOIN {{schema}}.dim_product_bridge b
    ON b.marketplace = e.marketplace
   AND (
     (e.nm_id IS NOT NULL AND b.nm_id = e.nm_id)
     OR (e.offer_id IS NOT NULL AND e.offer_id <> '~' AND b.offer_id = e.offer_id)
     OR (e.sku IS NOT NULL AND b.sku = e.sku)
     OR (e.barcode IS NOT NULL AND b.barcode = e.barcode)
     OR (e.vendor_code IS NOT NULL AND b.vendor_code = e.vendor_code)
     OR (e.supplier_article IS NOT NULL AND b.item_code = e.supplier_article)
   )
),
campaign_candidates AS (
  SELECT
    e.unmatched_id,
    b.canonical_product_key,
    'campaign_products'::text AS key_type,
    3 AS priority,
    (
      CASE
        WHEN SUM(COALESCE(a.spend, 0)) OVER (PARTITION BY e.unmatched_id) > 0
          THEN COALESCE(a.spend, 0) / SUM(COALESCE(a.spend, 0)) OVER (PARTITION BY e.unmatched_id)
        ELSE 0
      END
    )::numeric AS score
  FROM enriched e
  JOIN {{schema}}.stg_wb_ads a
    ON e.marketplace = 'wb'
   AND e.source_entity = 'stg_wb_ads'
   AND e.campaign_id IS NOT NULL
   AND a.date = e.event_date
   AND a.campaign_id = e.campaign_id
  JOIN {{schema}}.dim_product_bridge b
    ON b.marketplace = 'wb'
   AND (
     (NULLIF(a.nm_id, '~') IS NOT NULL AND b.nm_id = a.nm_id)
     OR (NULLIF(a.offer_id, '~') IS NOT NULL AND b.offer_id = a.offer_id)
   )
),
revenue_daily AS (
  SELECT
    f.marketplace,
    f.event_date,
    f.canonical_product_key,
    ABS(SUM(COALESCE(f.amount, 0)))::numeric(18,2) AS revenue_abs
  FROM {{schema}}.fact_product_money_attribution f
  WHERE f.money_bucket = 'revenue'
    AND f.canonical_product_key <> '~'
  GROUP BY f.marketplace, f.event_date, f.canonical_product_key
),
revenue_daily_share AS (
  SELECT
    r.marketplace,
    r.event_date,
    r.canonical_product_key,
    r.revenue_abs,
    CASE
      WHEN SUM(r.revenue_abs) OVER (PARTITION BY r.marketplace, r.event_date) > 0
        THEN r.revenue_abs / SUM(r.revenue_abs) OVER (PARTITION BY r.marketplace, r.event_date)
      ELSE 0
    END AS revenue_share
  FROM revenue_daily r
),
temporal_candidates AS (
  SELECT
    e.unmatched_id,
    r.canonical_product_key,
    'temporal_overlap'::text AS key_type,
    1 AS priority,
    COALESCE(r.revenue_share, 0)::numeric AS score
  FROM enriched e
  JOIN revenue_daily_share r
    ON r.marketplace = e.marketplace
   AND r.event_date BETWEEN (e.event_date - INTERVAL '3 day') AND (e.event_date + INTERVAL '3 day')
),
all_candidates AS (
  SELECT * FROM bridge_candidates
  UNION ALL
  SELECT * FROM campaign_candidates
  UNION ALL
  SELECT * FROM temporal_candidates
),
candidate_counts AS (
  SELECT
    unmatched_id,
    COUNT(DISTINCT canonical_product_key)::int AS candidate_count
  FROM all_candidates
  GROUP BY unmatched_id
),
candidate_best AS (
  SELECT
    x.unmatched_id,
    x.canonical_product_key,
    x.key_type,
    x.priority,
    x.score
  FROM (
    SELECT
      c.*,
      ROW_NUMBER() OVER (
        PARTITION BY c.unmatched_id
        ORDER BY c.priority DESC, c.score DESC, c.canonical_product_key
      ) AS rn
    FROM all_candidates c
  ) x
  WHERE x.rn = 1
),
base_rows AS (
  SELECT
    e.unmatched_id,
    e.marketplace,
    e.source_entity,
    e.money_type,
    e.amount,
    e.amount_abs,
    e.event_date,
    e.raw_record_id,
    e.campaign_id,
    e.advert_id,
    e.ad_id,
    e.order_id,
    e.posting_id,
    e.document_id,
    e.operation_id,
    e.nm_id,
    e.sku,
    e.offer_id,
    e.barcode,
    e.vendor_code,
    e.supplier_article,
    e.product_name_raw,
    e.brand_raw,
    e.subject_raw,
    CASE
      WHEN COALESCE(cc.candidate_count, 0) = 0 THEN 'unmatched_unclassified'
      WHEN COALESCE(cc.candidate_count, 0) = 1 THEN 'unmatched_explainable'
      ELSE 'ambiguous'
    END AS match_status,
    CASE
      WHEN e.source_partial_reason IN ('source_incomplete', 'source_gap') THEN 'source_gap'
      WHEN e.source_partial_reason IN ('parsing_incomplete', 'parsing_gap') THEN 'parsing_gap'
      WHEN e.campaign_id IS NOT NULL
        AND COALESCE(NULLIF(e.nm_id, ''), NULLIF(e.offer_id, ''), NULLIF(e.sku, ''), NULLIF(e.barcode, ''), NULLIF(e.vendor_code, ''), NULLIF(e.supplier_article, '')) IS NULL
        THEN 'campaign_level_only'
      WHEN COALESCE(NULLIF(e.nm_id, ''), NULLIF(e.offer_id, ''), NULLIF(e.sku, ''), NULLIF(e.barcode, ''), NULLIF(e.vendor_code, ''), NULLIF(e.supplier_article, '')) IS NULL
        THEN 'no_product_key'
      WHEN COALESCE(cc.candidate_count, 0) = 0 THEN 'product_key_not_found'
      WHEN COALESCE(cc.candidate_count, 0) > 1 THEN 'multiple_candidates'
      ELSE 'candidate_mapping_available'
    END AS unmatched_reason,
    COALESCE(cb.key_type, '') AS candidate_mapping_reason,
    COALESCE(cc.candidate_count, 0) AS candidate_product_count,
    cb.canonical_product_key AS most_likely_product_id,
    COALESCE(cb.key_type, '') AS most_likely_key_type,
    CASE
      WHEN cb.priority >= 9 THEN 'high'
      WHEN cb.priority >= 6 THEN 'medium'
      WHEN cb.priority >= 1 THEN 'low'
      ELSE 'low'
    END AS confidence_level,
    CASE
      WHEN e.source_partial_reason IN ('source_incomplete', 'source_gap') THEN 'source_gap'
      WHEN e.source_partial_reason IN ('parsing_incomplete', 'parsing_gap') THEN 'parsing_gap'
      ELSE 'match_gap'
    END AS incompleteness_stage,
    COALESCE(e.raw_json, jsonb_build_object(
      'source_entity', e.source_entity,
      'raw_record_id', e.raw_record_id,
      'attribution_method', e.attribution_method,
      'attribution_confidence', e.attribution_confidence,
      'source_error_type', e.source_error_type
    )) AS raw_json
  FROM enriched e
  LEFT JOIN candidate_counts cc
    ON cc.unmatched_id = e.unmatched_id
  LEFT JOIN candidate_best cb
    ON cb.unmatched_id = e.unmatched_id
),
source_gap_placeholders AS (
  SELECT
    md5(
      c.marketplace || '|placeholder|' || c.source || '|' || p.period_start::text || '|' || p.period_end::text || '|' || m.money_type
    ) AS unmatched_id,
    c.marketplace,
    c.source AS source_entity,
    m.money_type,
    0::numeric(18,2) AS amount,
    0::numeric(18,2) AS amount_abs,
    p.period_end AS event_date,
    c.source || ':placeholder:' || m.money_type || ':' || p.period_start::text || ':' || p.period_end::text AS raw_record_id,
    NULL::text AS campaign_id,
    NULL::text AS advert_id,
    NULL::text AS ad_id,
    NULL::text AS order_id,
    NULL::text AS posting_id,
    NULL::text AS document_id,
    NULL::text AS operation_id,
    NULL::text AS nm_id,
    NULL::text AS sku,
    NULL::text AS offer_id,
    NULL::text AS barcode,
    NULL::text AS vendor_code,
    NULL::text AS supplier_article,
    NULL::text AS product_name_raw,
    NULL::text AS brand_raw,
    NULL::text AS subject_raw,
    CASE
      WHEN c.partial_reason IN ('source_incomplete', 'source_gap') THEN 'source_gap_placeholder'
      ELSE 'parsing_gap_placeholder'
    END AS match_status,
    CASE
      WHEN c.partial_reason IN ('source_incomplete', 'source_gap') THEN 'source_gap'
      ELSE 'parsing_gap'
    END AS unmatched_reason,
    ''::text AS candidate_mapping_reason,
    0::int AS candidate_product_count,
    NULL::text AS most_likely_product_id,
    ''::text AS most_likely_key_type,
    'low'::text AS confidence_level,
    CASE
      WHEN c.partial_reason IN ('source_incomplete', 'source_gap') THEN 'source_gap'
      ELSE 'parsing_gap'
    END AS incompleteness_stage,
    jsonb_build_object(
      'placeholder', TRUE,
      'missing_days_count', c.missing_days_count,
      'coverage_ratio', c.coverage_ratio,
      'source', c.source
    ) AS raw_json
  FROM {{schema}}.ingestion_completeness c
  JOIN params p
    ON TRUE
  JOIN (
    VALUES
      ('wb', 'ads', 'ads'),
      ('wb', 'finance', 'commission'),
      ('wb', 'finance', 'logistics'),
      ('wb', 'finance', 'services'),
      ('wb', 'finance', 'storage'),
      ('wb', 'finance', 'payment_processing'),
      ('wb', 'finance', 'penalties'),
      ('wb', 'finance', 'other'),
      ('wb', 'finance', 'returns'),
      ('ozon', 'ads', 'ads'),
      ('ozon', 'transactions', 'commission'),
      ('ozon', 'transactions', 'logistics'),
      ('ozon', 'transactions', 'services'),
      ('ozon', 'transactions', 'storage'),
      ('ozon', 'transactions', 'payment_processing'),
      ('ozon', 'transactions', 'penalties'),
      ('ozon', 'transactions', 'other'),
      ('ozon', 'transactions', 'returns'),
      ('ozon', 'postings', 'commission'),
      ('ozon', 'postings', 'services')
  ) AS m(marketplace, source, money_type)
    ON m.marketplace = c.marketplace
   AND m.source = c.source
  WHERE c.partial_reason IN ('source_incomplete', 'source_gap', 'parsing_incomplete', 'parsing_gap')
    AND COALESCE(c.missing_days_count, 0) > 0
),
combined_rows AS (
  SELECT * FROM base_rows
  UNION ALL
  SELECT * FROM source_gap_placeholders
)
SELECT
  r.unmatched_id,
  r.marketplace,
  r.source_entity,
  r.money_type,
  r.amount,
  r.amount_abs,
  r.event_date,
  r.raw_record_id,
  r.campaign_id,
  r.advert_id,
  r.ad_id,
  r.order_id,
  r.posting_id,
  r.document_id,
  r.operation_id,
  r.nm_id,
  r.sku,
  r.offer_id,
  r.barcode,
  r.vendor_code,
  r.supplier_article,
  r.product_name_raw,
  r.brand_raw,
  r.subject_raw,
  r.match_status,
  r.unmatched_reason,
  r.candidate_mapping_reason,
  r.candidate_product_count,
  r.most_likely_product_id,
  r.most_likely_key_type,
  r.confidence_level,
  r.incompleteness_stage,
  r.raw_json,
  now() AS loaded_at
INTO {{schema}}.fact_unmatched_money
FROM combined_rows r;

CREATE INDEX fact_unmatched_money_date_idx
  ON {{schema}}.fact_unmatched_money (marketplace, event_date);

CREATE INDEX fact_unmatched_money_type_idx
  ON {{schema}}.fact_unmatched_money (marketplace, money_type, match_status);

CREATE INDEX fact_unmatched_money_reason_idx
  ON {{schema}}.fact_unmatched_money (incompleteness_stage, unmatched_reason);
