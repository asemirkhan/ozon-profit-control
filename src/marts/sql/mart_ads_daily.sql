-- mart_ads_daily
-- 1 строка = 1 день × 1 кампания × (товар если доступен)

DROP TABLE IF EXISTS {{schema}}.mart_ads_daily;

SELECT
  a.date,
  a.campaign_id,
  a.campaign_name,
  COALESCE(a.offer_id, dp.offer_id) AS offer_id,
  COALESCE(a.sku, dp.sku) AS sku,
  SUM(a.impressions) AS impressions,
  SUM(a.clicks) AS clicks,
  SUM(a.spend) AS spend,
  CASE WHEN SUM(a.clicks) > 0 THEN SUM(a.spend) / SUM(a.clicks) ELSE NULL END AS cpc,
  SUM(a.attributed_orders) AS attributed_orders,
  SUM(a.attributed_revenue) AS attributed_revenue,
  CASE WHEN SUM(a.attributed_revenue) > 0 THEN SUM(a.spend) / SUM(a.attributed_revenue) ELSE NULL END AS acos
INTO {{schema}}.mart_ads_daily
FROM {{schema}}.fact_ads_spend a
LEFT JOIN {{schema}}.dim_product dp
  ON (dp.sku IS NOT NULL AND dp.sku = a.sku)
GROUP BY 1,2,3,4,5;

CREATE INDEX mart_ads_daily_date_idx
  ON {{schema}}.mart_ads_daily (date);