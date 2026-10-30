-- mart_pnl_30d_by_product
-- 1 строка = 1 товар × marketplace за период

DROP TABLE IF EXISTS {{schema}}.mart_pnl_30d_by_product;

WITH period AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
pnl_agg AS (
  SELECT
    d.marketplace,
    d.offer_id,
    MAX(d.product_name) AS product_name,
    SUM(COALESCE(d.units_sold, 0)) AS units_sold,
    SUM(COALESCE(d.revenue_gross, 0)) AS revenue_gross,
    SUM(COALESCE(d.payout, 0)) AS payout,
    SUM(COALESCE(d.commission_amount, 0)) AS commission_amount,
    SUM(COALESCE(d.logistics_amount, 0)) AS logistics_amount,
    SUM(COALESCE(d.services_amount, 0)) AS services_amount,
    SUM(COALESCE(d.storage_amount, 0)) AS storage_amount,
    SUM(COALESCE(d.payment_processing_amount, 0)) AS payment_processing_amount,
    SUM(COALESCE(d.penalties_amount, 0)) AS penalties_amount,
    SUM(COALESCE(d.other_amount, 0)) AS other_amount,
    SUM(COALESCE(d.marketplace_other_fees_amount, 0)) AS marketplace_other_fees_amount,
    SUM(COALESCE(d.returns_amount, 0)) AS returns_amount,
    SUM(COALESCE(d.cogs_amount, 0)) AS cogs_amount,
    SUM(COALESCE(d.ads_spend, 0)) AS ads_spend,
    SUM(COALESCE(d.profit_before_ads, 0)) AS profit_before_ads,
    SUM(COALESCE(d.profit_after_ads, 0)) AS profit_after_ads,
    SUM(COALESCE(d.discount_total, 0)) AS discount_total
  FROM {{schema}}.mart_pnl_daily_by_product d
  CROSS JOIN period p
  WHERE d.date >= p.period_start
    AND d.date <= p.period_end
  GROUP BY d.marketplace, d.offer_id
)
SELECT
  p.period_start,
  p.period_end,
  a.marketplace,
  a.offer_id,
  a.product_name,
  a.units_sold,
  a.revenue_gross,
  a.payout,
  a.commission_amount,
  a.logistics_amount,
  a.services_amount,
  a.storage_amount,
  a.payment_processing_amount,
  a.penalties_amount,
  a.other_amount,
  a.marketplace_other_fees_amount,
  a.returns_amount,
  a.cogs_amount,
  a.ads_spend,
  a.profit_before_ads,
  a.profit_after_ads,
  CASE
    WHEN COALESCE(a.revenue_gross, 0) > 0 THEN a.profit_after_ads / a.revenue_gross
    ELSE NULL
  END AS margin_after_ads,
  CASE
    WHEN COALESCE(a.units_sold, 0) > 0 THEN a.profit_after_ads / a.units_sold
    ELSE NULL
  END AS profit_per_unit_after_ads,
  a.discount_total
INTO {{schema}}.mart_pnl_30d_by_product
FROM period p
JOIN pnl_agg a
  ON TRUE;

CREATE INDEX mart_pnl_30d_by_product_profit_idx
  ON {{schema}}.mart_pnl_30d_by_product (marketplace, profit_after_ads DESC);

CREATE INDEX mart_pnl_30d_by_product_offer_idx
  ON {{schema}}.mart_pnl_30d_by_product (marketplace, offer_id);
