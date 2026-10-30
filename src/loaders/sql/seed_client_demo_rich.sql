-- Rich synthetic demo seed for full pipeline checks.
-- Expected placeholders:
--   {{schema}}
--   {{period_start}}  (inclusive)
--   {{period_end}}    (exclusive)

TRUNCATE TABLE
  {{schema}}.stg_transaction_service,
  {{schema}}.stg_transaction_item,
  {{schema}}.stg_transaction,
  {{schema}}.stg_posting_item_financial,
  {{schema}}.stg_posting_item,
  {{schema}}.stg_posting,
  {{schema}}.fact_ads_spend,
  {{schema}}.fact_inventory_snapshot,
  {{schema}}.fact_cogs,
  {{schema}}.dim_product;

DROP TABLE IF EXISTS tmp_rich_product_params;
CREATE TEMP TABLE tmp_rich_product_params (
  offer_id TEXT PRIMARY KEY,
  product_name TEXT NOT NULL,
  sku TEXT NOT NULL,
  product_id TEXT NOT NULL,
  daily_qty INTEGER NOT NULL,
  item_price NUMERIC(18,2) NOT NULL,
  payout_per_unit NUMERIC(18,2) NOT NULL,
  cogs_per_unit NUMERIC(18,2) NOT NULL,
  logistics_per_order NUMERIC(18,2) NOT NULL,
  ads_daily_spend NUMERIC(18,2) NOT NULL,
  stock_available NUMERIC(18,2) NOT NULL,
  stock_total NUMERIC(18,2) NOT NULL,
  scenario TEXT NOT NULL,
  has_sales BOOLEAN NOT NULL
);

INSERT INTO tmp_rich_product_params (
  offer_id,
  product_name,
  sku,
  product_id,
  daily_qty,
  item_price,
  payout_per_unit,
  cogs_per_unit,
  logistics_per_order,
  ads_daily_spend,
  stock_available,
  stock_total,
  scenario,
  has_sales
)
VALUES
  ('offer_001', 'Базовая бутылка 500 мл',      'SKU001', 'PRD001', 1, 1000, 900, 400, 120, 80,  90,  95, 'normal', TRUE),
  ('offer_002', 'Базовый ланч-бокс',           'SKU002', 'PRD002', 1,  800, 700, 320, 100, 60,  70,  72, 'normal', TRUE),
  ('offer_003', 'Базовая хлопковая футболка',  'SKU003', 'PRD003', 1, 1200,1050, 500, 130,120, 110, 115, 'normal', TRUE),
  ('offer_004', 'Коврик с убыточной рекламой', 'SKU004', 'PRD004', 1,  700, 620, 300, -20,400,  55,  58, 'negative_profit_after_ads', TRUE),
  ('offer_005', 'Лампа с возвратами',          'SKU005', 'PRD005', 1, -200,-180,  50,  20, 20,  40,  42, 'negative_revenue_anomaly', TRUE),
  ('offer_006', 'Кружка с перегретой кампанией','SKU006', 'PRD006', 1, 1000, 900, 300, 120,350,  65,  67, 'ads_eating_profit', TRUE),
  ('offer_007', 'Ручка с перегретой кампанией','SKU007', 'PRD007', 1,  900, 800, 280, 110,250,  60,  62, 'ads_eating_profit', TRUE),
  ('offer_008', 'Габаритный набор коробок',    'SKU008', 'PRD008', 1, 1000, 900, 400, 300, 50,  50,  54, 'high_logistics_share', TRUE),
  ('offer_009', 'Тяжёлая подставка',           'SKU009', 'PRD009', 1,  850, 760, 320, 230, 40,  45,  47, 'high_logistics_share', TRUE),
  ('offer_010', 'Носки с тонкой маржой',       'SKU010', 'PRD010', 1, 1000, 900, 540, 180, 98,  35,  37, 'low_margin_after_ads', TRUE),
  ('offer_011', 'Чехол с тонкой маржой',       'SKU011', 'PRD011', 1, 1100, 980, 570, 210,109,  32,  34, 'low_margin_after_ads', TRUE),
  ('offer_012', 'Быстрый хит Альфа',           'SKU012', 'PRD012', 1,  700, 620, 300, 100, 60,   1,   2, 'stockout_risk', TRUE),
  ('offer_013', 'Быстрый хит Бета',            'SKU013', 'PRD013', 1,  650, 580, 280,  95, 55,   3,   4, 'stockout_risk', TRUE),
  ('offer_014', 'Расход без выручки',          'SKU014', 'PRD014', 1,    0,   0,  20,  15, 70,  20,  22, 'zero_revenue_with_sales', TRUE),
  ('offer_015', 'Товар с рекламой без продаж', 'SKU015', 'PRD015', 1,    0,   0,   0,   0, 80,  25,  27, 'ads_without_sales', FALSE);

DROP TABLE IF EXISTS tmp_rich_dates;
CREATE TEMP TABLE tmp_rich_dates AS
SELECT d::date AS sale_date
FROM generate_series(
  '{{period_start}}'::date,
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  INTERVAL '1 day'
) AS gs(d);

DROP TABLE IF EXISTS tmp_rich_day_profile;
CREATE TEMP TABLE tmp_rich_day_profile AS
SELECT
  d.sale_date,
  (
    CASE EXTRACT(ISODOW FROM d.sale_date)
      WHEN 1 THEN 0.86
      WHEN 2 THEN 0.94
      WHEN 3 THEN 1.00
      WHEN 4 THEN 1.08
      WHEN 5 THEN 1.18
      WHEN 6 THEN 1.24
      ELSE 0.90
    END
    + CASE WHEN EXTRACT(DAY FROM d.sale_date)::int BETWEEN 12 AND 16 THEN 0.10 ELSE 0 END
    - CASE WHEN EXTRACT(DAY FROM d.sale_date)::int IN (7, 21, 28) THEN 0.12 ELSE 0 END
  )::numeric(10,4) AS sales_factor,
  (
    CASE EXTRACT(ISODOW FROM d.sale_date)
      WHEN 1 THEN 0.90
      WHEN 2 THEN 0.97
      WHEN 3 THEN 1.03
      WHEN 4 THEN 1.06
      WHEN 5 THEN 1.12
      WHEN 6 THEN 1.16
      ELSE 0.92
    END
    + CASE WHEN EXTRACT(DAY FROM d.sale_date)::int IN (5, 13, 24) THEN 0.20 ELSE 0 END
    - CASE WHEN EXTRACT(DAY FROM d.sale_date)::int IN (9, 20) THEN 0.08 ELSE 0 END
  )::numeric(10,4) AS ads_factor
FROM tmp_rich_dates d;

DROP TABLE IF EXISTS tmp_rich_sales;
CREATE TEMP TABLE tmp_rich_sales AS
SELECT
  p.*,
  d.sale_date,
  dp.sales_factor,
  dp.ads_factor,
  ('RICH-' || p.offer_id || '-' || TO_CHAR(d.sale_date, 'YYYYMMDD')) AS posting_number,
  ('TX-' || p.offer_id || '-' || TO_CHAR(d.sale_date, 'YYYYMMDD')) AS operation_id
FROM tmp_rich_product_params p
JOIN tmp_rich_dates d
  ON TRUE
JOIN tmp_rich_day_profile dp
  ON dp.sale_date = d.sale_date
WHERE p.has_sales;

INSERT INTO {{schema}}.dim_product (
  offer_id,
  product_id,
  sku,
  product_name,
  brand,
  category_id,
  category_name,
  archived,
  updated_at,
  loaded_at
)
SELECT
  offer_id,
  product_id,
  sku,
  product_name,
  'DemoBrand',
  'demo_category',
  scenario,
  FALSE,
  now(),
  now()
FROM tmp_rich_product_params;

INSERT INTO {{schema}}.fact_cogs (
  offer_id,
  unit_cogs,
  packaging_cost,
  inbound_shipping_cost,
  other_variable_cost,
  effective_from,
  effective_to,
  updated_at,
  loaded_at
)
SELECT
  offer_id,
  cogs_per_unit,
  0,
  0,
  0,
  '{{period_start}}'::date,
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  now(),
  now()
FROM tmp_rich_product_params;

INSERT INTO {{schema}}.stg_posting (
  posting_number,
  status,
  created_at,
  in_process_at,
  shipped_at,
  delivered_at,
  cancelled_at,
  delivery_schema,
  warehouse_id,
  customer_region,
  is_multibox,
  raw_updated_at,
  loaded_at
)
SELECT
  posting_number,
  'delivered',
  sale_date::timestamp + INTERVAL '10 hour',
  sale_date::timestamp + INTERVAL '11 hour',
  sale_date::timestamp + INTERVAL '14 hour',
  sale_date::timestamp + INTERVAL '20 hour',
  NULL,
  'FBS',
  'WH_MAIN',
  'RU-MOW',
  FALSE,
  now(),
  now()
FROM tmp_rich_sales;

INSERT INTO {{schema}}.stg_posting_item (
  posting_number,
  offer_id,
  sku,
  product_id,
  quantity,
  item_price,
  currency,
  raw_updated_at,
  loaded_at
)
SELECT
  posting_number,
  offer_id,
  sku,
  product_id,
  daily_qty,
  ROUND(item_price * sales_factor, 2),
  'RUB',
  now(),
  now()
FROM tmp_rich_sales;

INSERT INTO {{schema}}.stg_posting_item_financial (
  posting_number,
  product_id,
  sku,
  quantity,
  price,
  old_price,
  total_discount_value,
  total_discount_percent,
  commission_amount,
  commission_percent,
  payout,
  item_services_total,
  raw_updated_at,
  loaded_at
)
SELECT
  posting_number,
  product_id,
  sku,
  daily_qty,
  ROUND(item_price * sales_factor, 2),
  ROUND(item_price * sales_factor, 2),
  0,
  0,
  GREATEST((item_price - payout_per_unit) * daily_qty * sales_factor, 0),
  0,
  payout_per_unit * daily_qty * sales_factor,
  0,
  now(),
  now()
FROM tmp_rich_sales;

INSERT INTO {{schema}}.stg_transaction (
  operation_id,
  operation_date,
  operation_type,
  operation_type_name,
  posting_number,
  accruals_for_sale,
  sale_commission,
  delivery_charge,
  return_delivery_charge,
  amount,
  raw_updated_at,
  loaded_at
)
SELECT
  operation_id,
  sale_date::timestamp + INTERVAL '21 hour',
  'orders',
  'demo_order_transaction',
  posting_number,
  payout_per_unit * daily_qty * sales_factor,
  GREATEST((item_price - payout_per_unit) * daily_qty * sales_factor, 0),
  logistics_per_order * sales_factor,
  0,
  payout_per_unit * daily_qty * sales_factor,
  now(),
  now()
FROM tmp_rich_sales;

INSERT INTO {{schema}}.stg_transaction_item (
  operation_id,
  sku,
  item_name,
  raw_updated_at,
  loaded_at
)
SELECT
  operation_id,
  sku,
  product_name,
  now(),
  now()
FROM tmp_rich_sales;

INSERT INTO {{schema}}.stg_transaction (
  operation_id,
  operation_date,
  operation_type,
  operation_type_name,
  posting_number,
  accruals_for_sale,
  sale_commission,
  delivery_charge,
  return_delivery_charge,
  amount,
  raw_updated_at,
  loaded_at
)
VALUES
  (
    'TX-MISSING-001',
    ('{{period_start}}'::date + INTERVAL '5 day')::timestamp + INTERVAL '12 hour',
    'service',
    'orphan_transaction',
    'UNKNOWN-POSTING-001',
    0,
    0,
    120,
    0,
    120,
    now(),
    now()
  ),
  (
    'TX-MISSING-002',
    ('{{period_start}}'::date + INTERVAL '12 day')::timestamp + INTERVAL '12 hour',
    'service',
    'empty_posting_transaction',
    NULL,
    0,
    0,
    75,
    0,
    75,
    now(),
    now()
  );

INSERT INTO {{schema}}.fact_ads_spend (
  date,
  campaign_id,
  campaign_name,
  campaign_type,
  offer_id,
  sku,
  product_id,
  impressions,
  clicks,
  orders_count,
  revenue,
  spend,
  currency,
  raw_updated_at,
  loaded_at
)
SELECT
  d.sale_date,
  'CMP-' || p.offer_id,
  'Campaign ' || p.offer_id,
  CASE
    WHEN p.scenario IN ('stockout_risk', 'high_logistics_share') THEN 'search'
    ELSE 'performance'
  END,
  p.offer_id,
  p.sku,
  p.product_id,
  ROUND(p.ads_daily_spend * d.ads_factor * 90, 0),
  GREATEST(ROUND((p.ads_daily_spend * d.ads_factor) / 8.0, 0), 0),
  CASE WHEN p.has_sales THEN p.daily_qty ELSE 0 END,
  CASE WHEN p.has_sales THEN p.item_price * p.daily_qty * d.sales_factor ELSE 0 END,
  p.ads_daily_spend * d.ads_factor,
  'RUB',
  now(),
  now()
FROM tmp_rich_product_params p
JOIN tmp_rich_day_profile d
  ON TRUE;

INSERT INTO {{schema}}.fact_inventory_snapshot (
  snapshot_date,
  offer_id,
  sku,
  product_id,
  warehouse_name,
  warehouse_type,
  stock_total,
  stock_available,
  stock_reserved,
  stock_defect,
  raw_updated_at,
  loaded_at
)
SELECT
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  offer_id,
  sku,
  product_id,
  'Main Warehouse',
  'fulfillment',
  stock_total,
  stock_available,
  0,
  0,
  now(),
  now()
FROM tmp_rich_product_params;
