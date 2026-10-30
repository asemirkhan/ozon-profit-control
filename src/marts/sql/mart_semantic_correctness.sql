-- mart_semantic_correctness
-- Проверка семантического соответствия money buckets между слоями:
-- stg -> fact_product_money_attribution -> mart_pnl_daily_by_product

DROP TABLE IF EXISTS {{schema}}.mart_semantic_correctness;

WITH period AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
fact_bucket AS (
  SELECT
    m.marketplace,
    SUM(CASE WHEN m.money_bucket = 'commission' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_commission,
    SUM(CASE WHEN m.money_bucket = 'logistics' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_logistics,
    SUM(CASE WHEN m.money_bucket = 'services' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_services,
    SUM(CASE WHEN m.money_bucket = 'storage' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_storage,
    SUM(CASE WHEN m.money_bucket = 'payment_processing' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_payment_processing,
    SUM(CASE WHEN m.money_bucket = 'penalties' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_penalties,
    SUM(CASE WHEN m.money_bucket = 'other' THEN m.amount ELSE 0 END)::numeric(18,2) AS fact_other
  FROM {{schema}}.fact_product_money_attribution m
  CROSS JOIN period p
  WHERE m.event_date >= p.period_start
    AND m.event_date <= p.period_end
  GROUP BY m.marketplace
),
mart_bucket AS (
  SELECT
    d.marketplace,
    SUM(COALESCE(d.commission_amount, 0))::numeric(18,2) AS mart_commission,
    SUM(COALESCE(d.logistics_amount, 0))::numeric(18,2) AS mart_logistics,
    SUM(COALESCE(d.services_amount, 0))::numeric(18,2) AS mart_services,
    SUM(COALESCE(d.storage_amount, 0))::numeric(18,2) AS mart_storage,
    SUM(COALESCE(d.payment_processing_amount, 0))::numeric(18,2) AS mart_payment_processing,
    SUM(COALESCE(d.penalties_amount, 0))::numeric(18,2) AS mart_penalties,
    SUM(COALESCE(d.other_amount, 0))::numeric(18,2) AS mart_other
  FROM {{schema}}.mart_pnl_daily_by_product d
  CROSS JOIN period p
  WHERE d.date >= p.period_start
    AND d.date <= p.period_end
  GROUP BY d.marketplace
),
wb_stg AS (
  SELECT
    'wb'::text AS marketplace,
    SUM(ABS(COALESCE(f.commission_amount, 0)))::numeric(18,2) AS stg_commission_abs,
    SUM(ABS(COALESCE(f.logistics_amount, 0)))::numeric(18,2) AS stg_logistics_abs,
    SUM(ABS(COALESCE(f.service_amount, 0)))::numeric(18,2) AS stg_services_abs
  FROM {{schema}}.stg_wb_finance f
  CROSS JOIN period p
  WHERE COALESCE(f.marketplace, 'wb') = 'wb'
    AND (f.operation_date)::date >= p.period_start
    AND (f.operation_date)::date <= p.period_end
),
marketplaces AS (
  SELECT marketplace FROM fact_bucket
  UNION
  SELECT marketplace FROM mart_bucket
  UNION
  SELECT marketplace FROM wb_stg
),
joined AS (
  SELECT
    p.period_start,
    p.period_end,
    m.marketplace,
    COALESCE(f.fact_commission, 0)::numeric(18,2) AS fact_commission,
    COALESCE(f.fact_logistics, 0)::numeric(18,2) AS fact_logistics,
    COALESCE(f.fact_services, 0)::numeric(18,2) AS fact_services,
    COALESCE(f.fact_storage, 0)::numeric(18,2) AS fact_storage,
    COALESCE(f.fact_payment_processing, 0)::numeric(18,2) AS fact_payment_processing,
    COALESCE(f.fact_penalties, 0)::numeric(18,2) AS fact_penalties,
    COALESCE(f.fact_other, 0)::numeric(18,2) AS fact_other,
    COALESCE(d.mart_commission, 0)::numeric(18,2) AS mart_commission,
    COALESCE(d.mart_logistics, 0)::numeric(18,2) AS mart_logistics,
    COALESCE(d.mart_services, 0)::numeric(18,2) AS mart_services,
    COALESCE(d.mart_storage, 0)::numeric(18,2) AS mart_storage,
    COALESCE(d.mart_payment_processing, 0)::numeric(18,2) AS mart_payment_processing,
    COALESCE(d.mart_penalties, 0)::numeric(18,2) AS mart_penalties,
    COALESCE(d.mart_other, 0)::numeric(18,2) AS mart_other,
    COALESCE(s.stg_commission_abs, 0)::numeric(18,2) AS stg_commission_abs,
    COALESCE(s.stg_logistics_abs, 0)::numeric(18,2) AS stg_logistics_abs,
    COALESCE(s.stg_services_abs, 0)::numeric(18,2) AS stg_services_abs
  FROM marketplaces m
  CROSS JOIN period p
  LEFT JOIN fact_bucket f
    ON f.marketplace = m.marketplace
  LEFT JOIN mart_bucket d
    ON d.marketplace = m.marketplace
  LEFT JOIN wb_stg s
    ON s.marketplace = m.marketplace
),
drift AS (
  SELECT
    j.*,
    ABS(j.fact_commission - j.mart_commission)::numeric(18,2) AS drift_fact_mart_commission,
    ABS(j.fact_logistics - j.mart_logistics)::numeric(18,2) AS drift_fact_mart_logistics,
    ABS(j.fact_services - j.mart_services)::numeric(18,2) AS drift_fact_mart_services,
    ABS(j.fact_storage - j.mart_storage)::numeric(18,2) AS drift_fact_mart_storage,
    ABS(j.fact_payment_processing - j.mart_payment_processing)::numeric(18,2) AS drift_fact_mart_payment_processing,
    ABS(j.fact_penalties - j.mart_penalties)::numeric(18,2) AS drift_fact_mart_penalties,
    ABS(j.fact_other - j.mart_other)::numeric(18,2) AS drift_fact_mart_other,
    ABS(j.stg_commission_abs - j.fact_commission)::numeric(18,2) AS drift_stg_fact_commission_abs,
    ABS(j.stg_logistics_abs - j.fact_logistics)::numeric(18,2) AS drift_stg_fact_logistics_abs,
    ABS(j.stg_services_abs - j.fact_services)::numeric(18,2) AS drift_stg_fact_services_abs
  FROM joined j
),
final AS (
  SELECT
    d.*,
    (
      d.drift_fact_mart_commission > 0.01
      OR d.drift_fact_mart_logistics > 0.01
      OR d.drift_fact_mart_services > 0.01
      OR d.drift_fact_mart_storage > 0.01
      OR d.drift_fact_mart_payment_processing > 0.01
      OR d.drift_fact_mart_penalties > 0.01
      OR d.drift_fact_mart_other > 0.01
      OR (
        d.marketplace = 'wb'
        AND (
          d.drift_stg_fact_commission_abs > 0.01
          OR d.drift_stg_fact_logistics_abs > 0.01
          OR d.drift_stg_fact_services_abs > 0.01
        )
      )
    ) AS semantic_mismatch,
    NULLIF(
      concat_ws(
        '; ',
        CASE WHEN d.drift_fact_mart_commission > 0.01 THEN 'commission_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_logistics > 0.01 THEN 'logistics_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_services > 0.01 THEN 'services_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_storage > 0.01 THEN 'storage_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_payment_processing > 0.01 THEN 'payment_processing_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_penalties > 0.01 THEN 'penalties_fact_vs_mart' END,
        CASE WHEN d.drift_fact_mart_other > 0.01 THEN 'other_fact_vs_mart' END,
        CASE WHEN d.marketplace = 'wb' AND d.drift_stg_fact_commission_abs > 0.01 THEN 'commission_stg_abs_vs_fact' END,
        CASE WHEN d.marketplace = 'wb' AND d.drift_stg_fact_logistics_abs > 0.01 THEN 'logistics_stg_abs_vs_fact' END,
        CASE WHEN d.marketplace = 'wb' AND d.drift_stg_fact_services_abs > 0.01 THEN 'services_stg_abs_vs_fact' END
      ),
      ''
    ) AS semantic_mismatch_reasons
  FROM drift d
)
SELECT
  period_start,
  period_end,
  marketplace,
  fact_commission,
  mart_commission,
  drift_fact_mart_commission,
  fact_logistics,
  mart_logistics,
  drift_fact_mart_logistics,
  fact_services,
  mart_services,
  drift_fact_mart_services,
  fact_storage,
  mart_storage,
  drift_fact_mart_storage,
  fact_payment_processing,
  mart_payment_processing,
  drift_fact_mart_payment_processing,
  fact_penalties,
  mart_penalties,
  drift_fact_mart_penalties,
  fact_other,
  mart_other,
  drift_fact_mart_other,
  stg_commission_abs,
  drift_stg_fact_commission_abs,
  stg_logistics_abs,
  drift_stg_fact_logistics_abs,
  stg_services_abs,
  drift_stg_fact_services_abs,
  semantic_mismatch,
  COALESCE(semantic_mismatch_reasons, '') AS semantic_mismatch_reasons
INTO {{schema}}.mart_semantic_correctness
FROM final;

CREATE INDEX mart_semantic_correctness_mkt_period_idx
  ON {{schema}}.mart_semantic_correctness (marketplace, period_start, period_end);
