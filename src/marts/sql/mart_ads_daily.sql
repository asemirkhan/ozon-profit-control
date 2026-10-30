-- mart_ads_daily
-- 1 строка = 1 день × 1 кампания × marketplace × (товар если доступен)

DROP TABLE IF EXISTS {{schema}}.mart_ads_daily;

SELECT
  a.date,
  COALESCE(a.marketplace, 'ozon') AS marketplace,
  a.campaign_id,
  a.campaign_name,
  a.campaign_type,
  COALESCE(NULLIF(a.offer_id,'~'), dp.offer_id, '~') AS offer_id,
  COALESCE(NULLIF(a.sku,'~'), dp.sku, '~') AS sku,
  COALESCE(NULLIF(a.product_id,'~'), dp.product_id, '~') AS product_id,
  SUM(a.impressions) AS impressions,
  SUM(a.clicks) AS clicks,
  SUM(a.spend) AS spend,
  CASE WHEN SUM(a.clicks) > 0 THEN SUM(a.spend) / SUM(a.clicks) ELSE NULL END AS cpc,
  SUM(a.orders_count) AS orders_count,
  SUM(a.revenue) AS revenue,
  CASE WHEN SUM(a.revenue) > 0 THEN SUM(a.spend) / SUM(a.revenue) ELSE NULL END AS acos,
  MAX(a.currency) AS currency
INTO {{schema}}.mart_ads_daily
FROM {{schema}}.fact_ads_spend a
LEFT JOIN {{schema}}.dim_product dp
  ON COALESCE(dp.marketplace, 'ozon') = COALESCE(a.marketplace, 'ozon')
 AND dp.sku IS NOT NULL
 AND dp.sku = a.sku
GROUP BY 1,2,3,4,5,6,7,8;

CREATE INDEX mart_ads_daily_date_idx
  ON {{schema}}.mart_ads_daily (date);

CREATE INDEX mart_ads_daily_marketplace_idx
  ON {{schema}}.mart_ads_daily (marketplace, date);
