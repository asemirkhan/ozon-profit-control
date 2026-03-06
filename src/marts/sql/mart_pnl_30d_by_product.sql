-- mart_pnl_30d_by_product
-- 1 строка = 1 товар за период
-- Период задаётся внешне (period_start, period_end)

DROP TABLE IF EXISTS {{schema}}.mart_pnl_30d_by_product;

WITH period AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
)
SELECT
  p.period_start,
  p.period_end,
  d.offer_id,
  MAX(d.product_name) AS product_name,
  SUM(d.units_sold) AS units_sold,
  SUM(d.revenue_gross) AS revenue_gross,
  SUM(d.payout) AS payout,
  SUM(d.commission_amount) AS commission_amount,
  SUM(d.logistics_amount) AS logistics_amount,
  SUM(d.services_amount) AS services_amount,
  SUM(d.returns_amount) AS returns_amount,
  SUM(d.cogs_amount) AS cogs_amount,
  SUM(d.ads_spend) AS ads_spend,
  SUM(d.profit_before_ads) AS profit_before_ads,
  SUM(d.profit_after_ads) AS profit_after_ads,
  CASE
    WHEN SUM(d.revenue_gross) > 0
      THEN SUM(d.profit_after_ads) / SUM(d.revenue_gross)
    ELSE NULL
  END AS margin_after_ads,
  CASE
    WHEN SUM(d.units_sold) > 0
      THEN SUM(d.profit_after_ads) / SUM(d.units_sold)
    ELSE NULL
  END AS profit_per_unit_after_ads,
  SUM(d.discount_total) AS discount_total
INTO {{schema}}.mart_pnl_30d_by_product
FROM {{schema}}.mart_pnl_daily_by_product d
CROSS JOIN period p
WHERE d.date BETWEEN p.period_start AND p.period_end
GROUP BY p.period_start, p.period_end, d.offer_id;

CREATE INDEX mart_pnl_30d_by_product_profit_idx
  ON {{schema}}.mart_pnl_30d_by_product (profit_after_ads DESC);