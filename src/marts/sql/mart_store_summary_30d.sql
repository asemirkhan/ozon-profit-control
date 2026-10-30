-- mart_store_summary_30d
-- 1 строка = агрегированное состояние магазина за период и marketplace

DROP TABLE IF EXISTS {{schema}}.mart_store_summary_30d;

WITH params AS (
  SELECT
    CURRENT_DATE AS run_date,
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
finance_boundary AS (
  SELECT MAX(day) AS confirmed_through
  FROM {{schema}}.finance_day_state
  WHERE final_analytic_usable_flag = TRUE
),
marketplaces AS (
  SELECT DISTINCT marketplace FROM {{schema}}.mart_pnl_30d_by_product
  UNION
  SELECT DISTINCT marketplace FROM {{schema}}.mart_rule_hits_30d
  UNION
  SELECT DISTINCT marketplace FROM {{schema}}.mart_inventory_snapshot
),
pnl_daily AS (
  SELECT
    d.marketplace,
    d.offer_id,
    d.units_sold,
    d.revenue_gross,
    d.ads_spend,
    CASE
      WHEN fb.confirmed_through IS NULL OR d.date <= fb.confirmed_through
        THEN d.logistics_amount
      ELSE 0
    END AS logistics_amount,
    CASE
      WHEN fb.confirmed_through IS NULL OR d.date <= fb.confirmed_through
        THEN d.profit_before_ads
      ELSE 0
    END AS profit_before_ads,
    CASE
      WHEN fb.confirmed_through IS NULL OR d.date <= fb.confirmed_through
        THEN d.profit_after_ads
      ELSE 0
    END AS profit_after_ads
  FROM {{schema}}.mart_pnl_daily_by_product d
  JOIN params prm
    ON d.date BETWEEN prm.period_start AND prm.period_end
  CROSS JOIN finance_boundary fb
),
pnl AS (
  SELECT
    marketplace,
    offer_id,
    SUM(COALESCE(units_sold, 0)) AS units_sold,
    SUM(COALESCE(revenue_gross, 0)) AS revenue_gross,
    SUM(COALESCE(ads_spend, 0)) AS ads_spend,
    SUM(COALESCE(logistics_amount, 0)) AS logistics_amount,
    SUM(COALESCE(profit_before_ads, 0)) AS profit_before_ads,
    SUM(COALESCE(profit_after_ads, 0)) AS profit_after_ads
  FROM pnl_daily
  GROUP BY marketplace, offer_id
),
pnl_agg AS (
  SELECT
    p.marketplace,
    COUNT(DISTINCT CASE WHEN p.offer_id <> '~' THEN p.offer_id END) AS sku_count,
    SUM(COALESCE(p.units_sold, 0)) AS units_sold_total,
    SUM(COALESCE(p.revenue_gross, 0)) AS revenue_total,
    SUM(COALESCE(p.ads_spend, 0)) AS ads_spend_total,
    SUM(COALESCE(p.logistics_amount, 0)) AS logistics_total,
    SUM(COALESCE(p.profit_before_ads, 0)) AS profit_before_ads_total,
    SUM(COALESCE(p.profit_after_ads, 0)) AS profit_after_ads_total,
    COUNT(DISTINCT CASE WHEN p.offer_id <> '~' AND COALESCE(p.profit_after_ads, 0) < 0 THEN p.offer_id END) AS loss_sku_count,
    COUNT(DISTINCT CASE WHEN p.offer_id <> '~' AND COALESCE(p.ads_spend, 0) > 0 THEN p.offer_id END) AS sku_with_ads_count
  FROM pnl p
  GROUP BY p.marketplace
),
stockout_agg AS (
  SELECT
    i.marketplace,
    COUNT(DISTINCT i.offer_id) AS sku_with_stockout_risk_count
  FROM {{schema}}.mart_inventory_snapshot i
  WHERE COALESCE(i.out_of_stock_risk, 'ok') NOT IN ('ok', 'no_sales')
  GROUP BY i.marketplace
),
rule_hits AS (
  SELECT
    h.marketplace,
    h.rule_code,
    COUNT(*) AS hit_count,
    COUNT(*) FILTER (WHERE h.severity = 'high') AS high_severity_hit_count
  FROM {{schema}}.mart_rule_hits_30d h
  JOIN params prm
    ON h.period_start = prm.period_start
   AND h.period_end = prm.period_end
  GROUP BY h.marketplace, h.rule_code
),
rule_hits_agg AS (
  SELECT
    r.marketplace,
    COALESCE(SUM(r.high_severity_hit_count), 0) AS high_severity_hit_count_total
  FROM rule_hits r
  GROUP BY r.marketplace
),
rule_impact_agg AS (
  SELECT
    h.marketplace,
    SUM(
      CASE
        WHEN COALESCE(h.money_impact_30d, 0) > 0 THEN h.money_impact_30d
        ELSE 0
      END
    )::numeric(18,2) AS profit_growth_potential_30d,
    SUM(
      CASE
        WHEN COALESCE(h.revenue_at_risk_30d, 0) > 0 THEN h.revenue_at_risk_30d
        ELSE 0
      END
    )::numeric(18,2) AS revenue_at_risk_30d
  FROM {{schema}}.mart_rule_hits_30d h
  JOIN params prm
    ON h.period_start = prm.period_start
   AND h.period_end = prm.period_end
  GROUP BY h.marketplace
),
top_problem AS (
  SELECT
    ranked.marketplace,
    ranked.rule_code AS top_problem_rule_code
  FROM (
    SELECT
      r.marketplace,
      r.rule_code,
      r.hit_count,
      ROW_NUMBER() OVER (
        PARTITION BY r.marketplace
        ORDER BY r.hit_count DESC, r.rule_code ASC
      ) AS rn
    FROM rule_hits r
  ) ranked
  WHERE ranked.rn = 1
),
dq AS (
  SELECT
    d.marketplace,
    COALESCE(d.unattributed_row_count, 0)::int AS unattributed_row_count,
    COALESCE(d.unattributed_cost_total, 0)::numeric(18,2) AS unattributed_cost_total,
    COALESCE(d.fallback_attributed_cost_total, 0)::numeric(18,2) AS fallback_attributed_cost_total
  FROM {{schema}}.mart_data_quality_30d d
  JOIN params prm
    ON d.period_start = prm.period_start
   AND d.period_end = prm.period_end
)
SELECT
  prm.run_date,
  prm.period_start,
  prm.period_end,
  m.marketplace,
  COALESCE(pa.sku_count, 0) AS sku_count,
  COALESCE(pa.units_sold_total, 0) AS units_sold_total,
  COALESCE(pa.revenue_total, 0) AS revenue_total,
  COALESCE(pa.ads_spend_total, 0) AS ads_spend_total,
  COALESCE(pa.logistics_total, 0) AS logistics_total,
  COALESCE(pa.profit_before_ads_total, 0) AS profit_before_ads_total,
  COALESCE(pa.profit_after_ads_total, 0) AS profit_after_ads_total,
  CASE
    WHEN COALESCE(pa.revenue_total, 0) > 0
      THEN COALESCE(pa.profit_after_ads_total, 0) / pa.revenue_total
    ELSE NULL
  END AS margin_after_ads_total,
  COALESCE(pa.loss_sku_count, 0) AS loss_sku_count,
  COALESCE(pa.sku_with_ads_count, 0) AS sku_with_ads_count,
  COALESCE(sa.sku_with_stockout_risk_count, 0) AS sku_with_stockout_risk_count,
  tp.top_problem_rule_code,
  COALESCE(ri.profit_growth_potential_30d, 0) AS profit_growth_potential_30d,
  COALESCE(ri.revenue_at_risk_30d, 0) AS revenue_at_risk_30d,
  COALESCE(dq.unattributed_cost_total, 0) AS unattributed_cost_total,
  COALESCE(dq.fallback_attributed_cost_total, 0) AS fallback_attributed_cost_total,
  CASE
    WHEN COALESCE(dq.unattributed_row_count, 0) > 0
      OR COALESCE(dq.unattributed_cost_total, 0) > 0
      THEN 'unattributed'
    WHEN COALESCE(dq.fallback_attributed_cost_total, 0) > 0
      THEN 'fallback_match'
    ELSE 'exact_match'
  END AS attribution_quality,
  CASE
    WHEN COALESCE(dq.unattributed_cost_total, 0) >= GREATEST(1000::numeric, COALESCE(pa.revenue_total, 0) * 0.03)
      OR COALESCE(dq.unattributed_row_count, 0) >= 10
      THEN 'low'
    WHEN COALESCE(dq.unattributed_row_count, 0) > 0
      OR COALESCE(dq.unattributed_cost_total, 0) > 0
      OR COALESCE(dq.fallback_attributed_cost_total, 0) > 0
      THEN 'medium'
    ELSE 'high'
  END AS confidence,
  CASE
    WHEN COALESCE(dq.unattributed_cost_total, 0) > 0
      THEN 'Часть расходов не удалось точно привязать к товарам. Оценки эффекта используйте как ориентир.'
    WHEN COALESCE(dq.fallback_attributed_cost_total, 0) > 0
      THEN 'Часть расходов разнесена fallback-логикой. Оценки эффекта менее точные, чем при полном SKU-match.'
    ELSE 'Ключевые метрики рассчитаны с высокой точностью.'
  END AS quality_disclaimer,
  CASE
    WHEN COALESCE(pa.profit_after_ads_total, 0) < 0 THEN 'losing_money'
    WHEN COALESCE(pa.loss_sku_count, 0) > 0
      OR COALESCE(sa.sku_with_stockout_risk_count, 0) > 0
      OR COALESCE(rha.high_severity_hit_count_total, 0) >= 2
      THEN 'at_risk'
    ELSE 'healthy'
  END AS store_health_status,
  CASE
    WHEN COALESCE(pa.profit_after_ads_total, 0) < 0
      THEN 'Магазин теряет деньги, нужно разобрать проблемные товары в первую очередь.'
    WHEN COALESCE(pa.loss_sku_count, 0) > 0
      OR COALESCE(sa.sku_with_stockout_risk_count, 0) > 0
      OR COALESCE(rha.high_severity_hit_count_total, 0) >= 2
      THEN 'Прибыль магазина под давлением: есть проблемы, требующие внимания.'
    ELSE 'Магазин в целом выглядит стабильно.'
  END AS store_health_message
INTO {{schema}}.mart_store_summary_30d
FROM params prm
JOIN marketplaces m
  ON TRUE
LEFT JOIN pnl_agg pa
  ON pa.marketplace = m.marketplace
LEFT JOIN stockout_agg sa
  ON sa.marketplace = m.marketplace
LEFT JOIN rule_hits_agg rha
  ON rha.marketplace = m.marketplace
LEFT JOIN top_problem tp
  ON tp.marketplace = m.marketplace
LEFT JOIN rule_impact_agg ri
  ON ri.marketplace = m.marketplace
LEFT JOIN dq
  ON dq.marketplace = m.marketplace;

CREATE INDEX mart_store_summary_30d_marketplace_idx
  ON {{schema}}.mart_store_summary_30d (marketplace, period_end DESC);
