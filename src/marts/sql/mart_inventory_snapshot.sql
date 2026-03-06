-- mart_inventory_snapshot
-- Требует {{schema}}.stg_inventory (если остатки включены)

DROP TABLE IF EXISTS {{schema}}.mart_inventory_snapshot;

WITH sales_7d AS (
  SELECT
    offer_id,
    AVG(units_sold) AS avg_daily_sales_7d
  FROM {{schema}}.mart_pnl_daily_by_product
  WHERE date >= (CURRENT_DATE - INTERVAL '7 day')
  GROUP BY 1
),
sales_30d AS (
  SELECT
    offer_id,
    AVG(units_sold) AS avg_daily_sales_30d
  FROM {{schema}}.mart_pnl_daily_by_product
  WHERE date >= (CURRENT_DATE - INTERVAL '30 day')
  GROUP BY 1
),
inv AS (
  SELECT
    snapshot_date,
    COALESCE(offer_id, dp.offer_id) AS offer_id,
    COALESCE(sku, dp.sku) AS sku,
    stock_on_hand,
    COALESCE(stock_reserved,0) AS stock_reserved,
    (stock_on_hand - COALESCE(stock_reserved,0)) AS stock_available
  FROM {{schema}}.stg_inventory i
  LEFT JOIN {{schema}}.dim_product dp
    ON (dp.sku IS NOT NULL AND dp.sku = i.sku)
)
SELECT
  inv.snapshot_date,
  inv.offer_id,
  inv.sku,
  dp.product_name,
  inv.stock_on_hand,
  inv.stock_reserved,
  inv.stock_available,
  s7.avg_daily_sales_7d,
  s30.avg_daily_sales_30d,
  CASE WHEN s7.avg_daily_sales_7d > 0 THEN inv.stock_available / s7.avg_daily_sales_7d ELSE NULL END AS days_of_stock_7d,
  CASE WHEN s30.avg_daily_sales_30d > 0 THEN inv.stock_available / s30.avg_daily_sales_30d ELSE NULL END AS days_of_stock_30d,
  CASE WHEN s7.avg_daily_sales_7d > 0 AND (inv.stock_available / s7.avg_daily_sales_7d) < 10 THEN TRUE ELSE FALSE END AS reorder_flag
INTO {{schema}}.mart_inventory_snapshot
FROM inv
LEFT JOIN {{schema}}.dim_product dp
  ON dp.offer_id = inv.offer_id
LEFT JOIN sales_7d s7
  ON s7.offer_id = inv.offer_id
LEFT JOIN sales_30d s30
  ON s30.offer_id = inv.offer_id;