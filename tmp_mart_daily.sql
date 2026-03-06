-- mart_pnl_daily_by_product
-- 1 строка = 1 день × 1 товар (offer_id)
-- Основная витрина денег/прибыли

DROP TABLE IF EXISTS client_demo.mart_pnl_daily_by_product;

WITH base_items AS (
  SELECT
    p.posting_number,
    (p.created_at)::date AS date,                         -- политика даты v1: created_at
    COALESCE(i.offer_id, dp.offer_id) AS offer_id,
    COALESCE(i.sku, dp.sku) AS sku,
    COALESCE(dp.product_name, i.offer_id, i.sku) AS product_name,
    SUM(i.quantity) AS units_sold,
    SUM(COALESCE(i.item_price, 0) * i.quantity) AS revenue_gross
  FROM client_demo.stg_posting p
  JOIN client_demo.stg_posting_item i
    ON i.posting_number = p.posting_number
  LEFT JOIN client_demo.dim_product dp
    ON (dp.sku IS NOT NULL AND dp.sku = i.sku)
    OR (dp.offer_id IS NOT NULL AND dp.offer_id = i.offer_id)
  GROUP BY 1,2,3,4,5
),
fin_by_posting_offer AS (
  -- Финансы из posting_item_financial привязываются к offer_id через dim_product
  SELECT
    p.posting_number,
    (p.created_at)::date AS date,
    dp.offer_id AS offer_id,
    dp.sku AS sku,
    SUM(COALESCE(f.payout,0)) AS payout,
    SUM(COALESCE(f.commission_amount,0)) AS commission_amount,
    SUM(COALESCE(f.item_services_total,0)) AS services_amount,
    SUM(COALESCE(f.total_discount_value,0)) AS discount_total
  FROM client_demo.stg_posting p
  JOIN client_demo.stg_posting_item_financial f
    ON f.posting_number = p.posting_number
  LEFT JOIN client_demo.dim_product dp
    ON (dp.product_id IS NOT NULL AND dp.product_id = f.product_id)
    OR (dp.sku IS NOT NULL AND dp.sku = f.sku)
  GROUP BY 1,2,3,4
),
ads_daily AS (
  SELECT
    a.date,
    COALESCE(a.offer_id, dp.offer_id) AS offer_id,
    SUM(a.spend) AS ads_spend
  FROM client_demo.fact_ads_spend a
  LEFT JOIN client_demo.dim_product dp
    ON (dp.sku IS NOT NULL AND dp.sku = a.sku)
  GROUP BY 1,2
),
cogs_daily AS (
  SELECT
    b.date,
    b.offer_id,
    SUM(b.units_sold * (c.unit_cogs
        + COALESCE(c.packaging_cost,0)
        + COALESCE(c.inbound_shipping_cost,0)
        + COALESCE(c.other_variable_cost,0)
    )) AS cogs_amount
  FROM base_items b
  LEFT JOIN client_demo.fact_cogs c
    ON c.offer_id = b.offer_id
  GROUP BY 1,2
)
SELECT
  b.date,
  b.offer_id,
  b.sku,
  b.product_name,
  b.units_sold,
  b.revenue_gross,
  COALESCE(f.payout,0) AS payout,
  COALESCE(f.commission_amount,0) AS commission_amount,
  CAST(0 AS NUMERIC(18,2)) AS logistics_amount,       -- v1: логистика может жить в транзакциях; добавим позже
  COALESCE(f.services_amount,0) AS services_amount,
  CAST(0 AS NUMERIC(18,2)) AS returns_amount,         -- v1: возвраты/корректировки добавим из транзакций когда будет правило распределения
  COALESCE(cg.cogs_amount,0) AS cogs_amount,
  COALESCE(a.ads_spend,0) AS ads_spend,
  (COALESCE(f.payout,0) - COALESCE(cg.cogs_amount,0)) AS profit_before_ads,
  (COALESCE(f.payout,0) - COALESCE(cg.cogs_amount,0) - COALESCE(a.ads_spend,0)) AS profit_after_ads,
  CASE
    WHEN b.revenue_gross > 0
      THEN (COALESCE(f.payout,0) - COALESCE(cg.cogs_amount,0) - COALESCE(a.ads_spend,0)) / b.revenue_gross
    ELSE NULL
  END AS margin_after_ads,
  CASE
    WHEN b.units_sold > 0
      THEN (COALESCE(f.payout,0) - COALESCE(cg.cogs_amount,0) - COALESCE(a.ads_spend,0)) / b.units_sold
    ELSE NULL
  END AS profit_per_unit_after_ads,
  COALESCE(f.discount_total,0) AS discount_total
INTO client_demo.mart_pnl_daily_by_product
FROM base_items b
LEFT JOIN fin_by_posting_offer f
  ON f.posting_number = b.posting_number
 AND f.date = b.date
 AND f.offer_id = b.offer_id
LEFT JOIN cogs_daily cg
  ON cg.date = b.date
 AND cg.offer_id = b.offer_id
LEFT JOIN ads_daily a
  ON a.date = b.date
 AND a.offer_id = b.offer_id;

CREATE INDEX mart_pnl_daily_by_product_date_idx
  ON client_demo.mart_pnl_daily_by_product (date);

CREATE INDEX mart_pnl_daily_by_product_offer_idx
  ON client_demo.mart_pnl_daily_by_product (offer_id);