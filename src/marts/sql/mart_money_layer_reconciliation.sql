-- mart_money_layer_reconciliation
-- Layer-by-layer money reconciliation with explicit source/parsing/match/semantic gaps.

DROP TABLE IF EXISTS {{schema}}.mart_money_layer_reconciliation;

WITH params AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
wb_stg AS (
  SELECT
    'wb'::text AS marketplace,
    money_type,
    SUM(amount_signed)::numeric(18,2) AS stg_total_signed,
    SUM(amount_abs)::numeric(18,2) AS stg_total_abs
  FROM (
    SELECT 'commission'::text AS money_type,
           COALESCE(f.commission_amount, 0)::numeric(18,2) AS amount_signed,
           ABS(COALESCE(f.commission_amount, 0))::numeric(18,2) AS amount_abs
    FROM {{schema}}.stg_wb_finance f
    JOIN params p ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'logistics'::text,
           COALESCE(f.logistics_amount, 0)::numeric(18,2),
           ABS(COALESCE(f.logistics_amount, 0))::numeric(18,2)
    FROM {{schema}}.stg_wb_finance f
    JOIN params p ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'services'::text,
           COALESCE(f.service_amount, 0)::numeric(18,2),
           ABS(COALESCE(f.service_amount, 0))::numeric(18,2)
    FROM {{schema}}.stg_wb_finance f
    JOIN params p ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT
      CASE
        WHEN LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%продвижени%'
          OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%подписк%'
          OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%списание за отзыв%'
          OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%предоставление услуг%'
          OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) LIKE '%оказание услуг%'
          THEN 'services'
        WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%хран%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%storage%'
          THEN 'storage'
        WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%эквайр%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%acquir%'
          THEN 'payment_processing'
        WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%штраф%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%удерж%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%penalt%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%deduct%'
          THEN 'penalties'
        WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%возврат%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%refund%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%сторн%'
          THEN 'returns'
        WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%компенс%'
          OR LOWER(COALESCE(f.operation_type, '')) LIKE '%compens%'
          THEN 'compensation'
        ELSE 'other'
      END AS money_type,
      COALESCE(f.amount, 0)::numeric(18,2) AS amount_signed,
      ABS(COALESCE(f.amount, 0))::numeric(18,2) AS amount_abs
    FROM {{schema}}.stg_wb_finance f
    JOIN params p ON (f.operation_date)::date BETWEEN p.period_start AND p.period_end
    WHERE LOWER(COALESCE(f.raw_payload->>'bonus_type_name', '')) NOT LIKE '%продвижени%'

    UNION ALL

    SELECT 'ads'::text,
           COALESCE(a.spend, 0)::numeric(18,2),
           ABS(COALESCE(a.spend, 0))::numeric(18,2)
    FROM {{schema}}.stg_wb_ads a
    JOIN params p ON a.date BETWEEN p.period_start AND p.period_end
  ) x
  GROUP BY money_type
),
ozon_stg AS (
  SELECT
    'ozon'::text AS marketplace,
    money_type,
    SUM(amount_signed)::numeric(18,2) AS stg_total_signed,
    SUM(amount_abs)::numeric(18,2) AS stg_total_abs
  FROM (
    SELECT 'commission'::text AS money_type,
           COALESCE(t.sale_commission, 0)::numeric(18,2) AS amount_signed,
           ABS(COALESCE(t.sale_commission, 0))::numeric(18,2) AS amount_abs
    FROM {{schema}}.stg_transaction t
    JOIN params p ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'logistics'::text,
           (COALESCE(t.delivery_charge, 0) + COALESCE(t.return_delivery_charge, 0))::numeric(18,2),
           ABS(COALESCE(t.delivery_charge, 0) + COALESCE(t.return_delivery_charge, 0))::numeric(18,2)
    FROM {{schema}}.stg_transaction t
    JOIN params p ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT
      CASE
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%достав%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%логист%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%delivery%'
          THEN 'logistics'
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%хран%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%storage%'
          THEN 'storage'
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%эквайр%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%acquir%'
          THEN 'payment_processing'
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%штраф%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%penalt%'
          THEN 'penalties'
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%компенс%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%compens%'
          THEN 'compensation'
        WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%возврат%'
          OR LOWER(COALESCE(s.service_name, '')) LIKE '%refund%'
          THEN 'returns'
        ELSE 'services'
      END AS money_type,
      COALESCE(s.service_price, 0)::numeric(18,2) AS amount_signed,
      ABS(COALESCE(s.service_price, 0))::numeric(18,2) AS amount_abs
    FROM {{schema}}.stg_transaction_service s
    JOIN {{schema}}.stg_transaction t
      ON t.operation_id = s.operation_id
     AND COALESCE(t.marketplace, 'ozon') = COALESCE(s.marketplace, 'ozon')
    JOIN params p ON (t.operation_date)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'commission'::text,
           COALESCE(f.commission_amount, 0)::numeric(18,2),
           ABS(COALESCE(f.commission_amount, 0))::numeric(18,2)
    FROM {{schema}}.stg_posting_item_financial f
    JOIN {{schema}}.stg_posting p0
      ON p0.posting_number = f.posting_number
     AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
    JOIN params p ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'services'::text,
           COALESCE(f.item_services_total, 0)::numeric(18,2),
           ABS(COALESCE(f.item_services_total, 0))::numeric(18,2)
    FROM {{schema}}.stg_posting_item_financial f
    JOIN {{schema}}.stg_posting p0
      ON p0.posting_number = f.posting_number
     AND COALESCE(p0.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
    JOIN params p ON (p0.created_at)::date BETWEEN p.period_start AND p.period_end

    UNION ALL

    SELECT 'ads'::text,
           COALESCE(a.spend, 0)::numeric(18,2),
           ABS(COALESCE(a.spend, 0))::numeric(18,2)
    FROM {{schema}}.fact_ads_spend a
    JOIN params p ON a.date BETWEEN p.period_start AND p.period_end
    WHERE COALESCE(a.marketplace, 'ozon') = 'ozon'
  ) x
  GROUP BY money_type
),
stg_totals AS (
  SELECT * FROM wb_stg
  UNION ALL
  SELECT * FROM ozon_stg
),
attr AS (
  SELECT
    a.marketplace,
    a.money_bucket AS money_type,
    SUM(COALESCE(a.amount, 0))::numeric(18,2) AS attribution_total,
    SUM(CASE WHEN a.canonical_product_key <> '~' THEN COALESCE(a.amount, 0) ELSE 0 END)::numeric(18,2) AS attributed_to_product,
    SUM(CASE WHEN a.canonical_product_key = '~' THEN COALESCE(a.amount, 0) ELSE 0 END)::numeric(18,2) AS attribution_unmatched,
    SUM(CASE WHEN a.canonical_product_key = '~' AND a.attribution_method = 'store_level' THEN COALESCE(a.amount, 0) ELSE 0 END)::numeric(18,2) AS intentional_store_level
  FROM {{schema}}.fact_product_money_attribution a
  JOIN params p
    ON a.event_date BETWEEN p.period_start AND p.period_end
  WHERE a.money_component_type = 'expense'
  GROUP BY a.marketplace, a.money_bucket
),
mart AS (
  SELECT
    m.marketplace,
    money_type,
    SUM(amount)::numeric(18,2) AS mart_total
  FROM (
    SELECT marketplace, 'commission'::text AS money_type, COALESCE(commission_amount, 0)::numeric(18,2) AS amount
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'logistics', COALESCE(logistics_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'services', COALESCE(services_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'storage', COALESCE(storage_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'payment_processing', COALESCE(payment_processing_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'penalties', COALESCE(penalties_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'other', COALESCE(other_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'ads', COALESCE(ads_spend, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end

    UNION ALL SELECT marketplace, 'returns', COALESCE(returns_amount, 0)::numeric(18,2)
    FROM {{schema}}.mart_pnl_daily_by_product
    JOIN params p ON date BETWEEN p.period_start AND p.period_end
  ) m
  GROUP BY m.marketplace, money_type
),
unmatched_explicit AS (
  SELECT
    u.marketplace,
    u.money_type,
    SUM(COALESCE(u.amount, 0))::numeric(18,2) AS unmatched_explicit,
    SUM(CASE WHEN u.incompleteness_stage = 'match_gap' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS match_gap,
    SUM(CASE WHEN u.incompleteness_stage = 'source_gap' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS source_gap_amount,
    SUM(CASE WHEN u.incompleteness_stage = 'parsing_gap' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS parsing_gap_amount,
    SUM(CASE WHEN u.match_status = 'ambiguous' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS ambiguous_amount,
    SUM(CASE WHEN u.match_status = 'unmatched_unclassified' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS unmatched_unclassified_amount,
    SUM(CASE WHEN u.match_status = 'unmatched_explainable' THEN COALESCE(u.amount, 0) ELSE 0 END)::numeric(18,2) AS unmatched_explainable_amount
  FROM {{schema}}.fact_unmatched_money u
  GROUP BY u.marketplace, u.money_type
),
latest_source_runs AS (
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
source_comp AS (
  SELECT
    lr.marketplace,
    lr.source,
    COALESCE(c.coverage_ratio, 0)::numeric(9,6) AS coverage_ratio,
    COALESCE(c.partial_reason, '') AS partial_reason,
    COALESCE(c.error_type, '') AS error_type
  FROM latest_source_runs lr
  LEFT JOIN {{schema}}.ingestion_completeness c
    ON c.run_id = lr.run_id
  WHERE lr.rn = 1
),
source_money_map AS (
  SELECT *
  FROM (
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
  ) v(marketplace, source, money_type)
),
coverage_by_money AS (
  SELECT
    sm.marketplace,
    sm.money_type,
    MIN(CASE WHEN sc.coverage_ratio > 0 THEN sc.coverage_ratio ELSE 1 END)::numeric(9,6) AS source_coverage_ratio,
    BOOL_OR(sc.partial_reason IN ('source_incomplete', 'source_gap') OR sc.error_type IN ('rate_limit', 'network_error', 'auth_error', 'period_limit', 'endpoint_missing')) AS has_source_gap,
    BOOL_OR(sc.partial_reason IN ('parsing_incomplete', 'parsing_gap') OR sc.error_type IN ('parse_error', 'unexpected_format', 'normalized_zero_rows', 'empty_response')) AS has_parsing_gap
  FROM source_money_map sm
  LEFT JOIN source_comp sc
    ON sc.marketplace = sm.marketplace
   AND sc.source = sm.source
  GROUP BY sm.marketplace, sm.money_type
),
semantic_by_money AS (
  SELECT
    s.marketplace,
    money_type,
    semantic_gap
  FROM (
    SELECT marketplace, 'commission'::text AS money_type, COALESCE(drift_fact_mart_commission, 0)::numeric(18,2) AS semantic_gap FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'logistics', COALESCE(drift_fact_mart_logistics, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'services', COALESCE(drift_fact_mart_services, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'storage', COALESCE(drift_fact_mart_storage, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'payment_processing', COALESCE(drift_fact_mart_payment_processing, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'penalties', COALESCE(drift_fact_mart_penalties, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
    UNION ALL
    SELECT marketplace, 'other', COALESCE(drift_fact_mart_other, 0)::numeric(18,2) FROM {{schema}}.mart_semantic_correctness
  ) s
),
keys AS (
  SELECT marketplace, money_type FROM stg_totals
  UNION
  SELECT marketplace, money_type FROM attr
  UNION
  SELECT marketplace, money_type FROM mart
  UNION
  SELECT marketplace, money_type FROM unmatched_explicit
  UNION
  SELECT marketplace, money_type FROM coverage_by_money
  UNION
  SELECT marketplace, money_type FROM semantic_by_money
),
joined AS (
  SELECT
    k.marketplace,
    k.money_type,
    COALESCE(s.stg_total_signed, 0)::numeric(18,2) AS stg_total_signed,
    COALESCE(s.stg_total_abs, 0)::numeric(18,2) AS stg_total_abs,
    COALESCE(a.attribution_total, 0)::numeric(18,2) AS attribution_total,
    COALESCE(a.attributed_to_product, 0)::numeric(18,2) AS matched_to_product,
    COALESCE(a.attribution_unmatched, 0)::numeric(18,2) AS attribution_unmatched,
    COALESCE(a.intentional_store_level, 0)::numeric(18,2) AS intentional_store_level,
    COALESCE(m.mart_total, 0)::numeric(18,2) AS mart_total,
    COALESCE(m.mart_total, 0)::numeric(18,2) AS ui_total,
    COALESCE(u.unmatched_explicit, 0)::numeric(18,2) AS unmatched_explicit,
    COALESCE(u.match_gap, 0)::numeric(18,2) AS match_gap,
    COALESCE(u.source_gap_amount, 0)::numeric(18,2) AS source_gap_amount,
    COALESCE(u.parsing_gap_amount, 0)::numeric(18,2) AS parsing_gap_amount,
    COALESCE(u.unmatched_explainable_amount, 0)::numeric(18,2) AS unmatched_explainable_amount,
    COALESCE(u.unmatched_unclassified_amount, 0)::numeric(18,2) AS unmatched_unclassified_amount,
    COALESCE(u.ambiguous_amount, 0)::numeric(18,2) AS ambiguous_amount,
    COALESCE(cbm.source_coverage_ratio, 1)::numeric(9,6) AS source_coverage_ratio,
    COALESCE(cbm.has_source_gap, FALSE) AS has_source_gap,
    COALESCE(cbm.has_parsing_gap, FALSE) AS has_parsing_gap,
    COALESCE(sb.semantic_gap, 0)::numeric(18,2) AS semantic_gap
  FROM keys k
  LEFT JOIN stg_totals s
    ON s.marketplace = k.marketplace
   AND s.money_type = k.money_type
  LEFT JOIN attr a
    ON a.marketplace = k.marketplace
   AND a.money_type = k.money_type
  LEFT JOIN mart m
    ON m.marketplace = k.marketplace
   AND m.money_type = k.money_type
  LEFT JOIN unmatched_explicit u
    ON u.marketplace = k.marketplace
   AND u.money_type = k.money_type
  LEFT JOIN coverage_by_money cbm
    ON cbm.marketplace = k.marketplace
   AND cbm.money_type = k.money_type
  LEFT JOIN semantic_by_money sb
    ON sb.marketplace = k.marketplace
   AND sb.money_type = k.money_type
)
SELECT
  j.marketplace,
  j.money_type,
  j.stg_total_signed AS raw_total_signed,
  (
    CASE
      WHEN j.source_coverage_ratio > 0
        THEN (j.stg_total_abs / j.source_coverage_ratio)
      ELSE j.stg_total_abs
    END
  )::numeric(18,2) AS raw_total_abs,
  j.stg_total_signed,
  j.stg_total_abs,
  j.attribution_total,
  j.matched_to_product,
  j.attribution_unmatched,
  j.mart_total,
  j.ui_total,
  j.unmatched_explicit,
  (
    CASE
      WHEN j.has_source_gap
        THEN GREATEST(
          j.source_gap_amount,
          (j.stg_total_abs * GREATEST(1 - j.source_coverage_ratio, 0))
        )
      ELSE j.source_gap_amount
    END
  )::numeric(18,2) AS source_gap,
  (
    CASE
      WHEN j.has_parsing_gap
        THEN GREATEST(
          j.parsing_gap_amount,
          (j.stg_total_abs * GREATEST(1 - j.source_coverage_ratio, 0))
        )
      ELSE j.parsing_gap_amount
    END
  )::numeric(18,2) AS parsing_gap,
  j.match_gap,
  j.semantic_gap,
  j.unmatched_explainable_amount,
  j.unmatched_unclassified_amount,
  j.ambiguous_amount,
  (
    CASE
      WHEN j.has_source_gap
        THEN GREATEST(
          j.source_gap_amount,
          (j.stg_total_abs * GREATEST(1 - j.source_coverage_ratio, 0))
        )
      ELSE j.source_gap_amount
    END
  )::numeric(18,2) AS blocked_by_source_gap,
  (
    CASE
      -- COGS is created from the effective unit-cost register rather than a
      -- WB staging money column.  A zero staging total therefore does not mean
      -- that product COGS is unmatched.
      WHEN j.money_type = 'cogs' AND j.stg_total_abs = 0 THEN 0
      ELSE j.stg_total_abs
      - j.matched_to_product
      - j.intentional_store_level
      - j.unmatched_explainable_amount
      - j.unmatched_unclassified_amount
      - j.ambiguous_amount
      - (
      CASE
        WHEN j.has_source_gap
          THEN GREATEST(
            j.source_gap_amount,
            (j.stg_total_abs * GREATEST(1 - j.source_coverage_ratio, 0))
          )
        ELSE j.source_gap_amount
      END
    )
    - (
      CASE
        WHEN j.has_parsing_gap
          THEN GREATEST(
            j.parsing_gap_amount,
            (j.stg_total_abs * GREATEST(1 - j.source_coverage_ratio, 0))
          )
        ELSE j.parsing_gap_amount
      END
    )
      - j.semantic_gap
    END
  )::numeric(18,2) AS unexplained_residual,
  j.source_coverage_ratio,
  now() AS created_at
INTO {{schema}}.mart_money_layer_reconciliation
FROM joined j;

CREATE INDEX mart_money_layer_reconciliation_idx
  ON {{schema}}.mart_money_layer_reconciliation (marketplace, money_type);
