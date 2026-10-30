-- Rich synthetic WB demo seed for full pipeline checks.
-- Expected placeholders:
--   {{schema}}
--   {{period_start}} (inclusive)
--   {{period_end}}   (exclusive)

TRUNCATE TABLE
  {{schema}}.stg_wb_stock,
  {{schema}}.stg_wb_ads,
  {{schema}}.stg_wb_finance,
  {{schema}}.stg_wb_sale,
  {{schema}}.stg_wb_order,
  {{schema}}.stg_wb_product,
  {{schema}}.fact_inventory_snapshot,
  {{schema}}.fact_ads_spend,
  {{schema}}.fact_cogs,
  {{schema}}.dim_product,
  {{schema}}.stg_transaction_service,
  {{schema}}.stg_transaction_item,
  {{schema}}.stg_transaction,
  {{schema}}.stg_posting_item_financial,
  {{schema}}.stg_posting_item,
  {{schema}}.stg_posting;

DROP TABLE IF EXISTS tmp_wb_product_params;
CREATE TEMP TABLE tmp_wb_product_params (
  offer_id TEXT PRIMARY KEY,
  nm_id TEXT NOT NULL,
  barcode TEXT NOT NULL,
  product_name TEXT NOT NULL,
  daily_qty INTEGER NOT NULL,
  revenue_per_unit NUMERIC(18,2) NOT NULL,
  payout_per_unit NUMERIC(18,2) NOT NULL,
  cogs_per_unit NUMERIC(18,2) NOT NULL,
  commission_per_order NUMERIC(18,2) NOT NULL,
  logistics_per_order NUMERIC(18,2) NOT NULL,
  service_per_order NUMERIC(18,2) NOT NULL,
  ads_daily_spend NUMERIC(18,2) NOT NULL,
  stock_available NUMERIC(18,2) NOT NULL,
  stock_total NUMERIC(18,2) NOT NULL,
  scenario TEXT NOT NULL,
  has_sales BOOLEAN NOT NULL
);

INSERT INTO tmp_wb_product_params VALUES
  ('wb_offer_001', '100001', 'WBBC001', 'WB Базовая бутылка',                 1, 1000, 850, 430, 80, 90, 20, 70, 120, 125, 'normal', TRUE),
  ('wb_offer_002', '100002', 'WBBC002', 'WB Базовый ланч-бокс',               1,  900, 770, 390, 75, 80, 18, 60, 115, 120, 'normal', TRUE),
  ('wb_offer_003', '100003', 'WBBC003', 'WB Базовая футболка',                1, 1100, 930, 470, 85, 95, 22, 75, 130, 138, 'normal', TRUE),
  ('wb_offer_004', '100004', 'WBBC004', 'WB Коврик с убыточной рекламой',     1,  700, 580, 300, 65, 90, 25, 320, 80,  84, 'negative_profit_after_ads', TRUE),
  ('wb_offer_005', '100005', 'WBBC005', 'WB Лампа с возвратами',              1, -220,-180,  70, 15, 40, 10,  35, 75,  78, 'negative_revenue_anomaly', TRUE),
  ('wb_offer_006', '100006', 'WBBC006', 'WB Кружка с перегретой кампанией',   1, 1000, 840, 320, 90, 85, 20, 240, 88,  90, 'ads_eating_profit', TRUE),
  ('wb_offer_007', '100007', 'WBBC007', 'WB Ручка с перегретой кампанией',    1,  900, 760, 290, 80, 78, 18, 210, 82,  85, 'ads_eating_profit', TRUE),
  ('wb_offer_008', '100008', 'WBBC008', 'WB Габаритный набор коробок',        1, 1000, 830, 410, 95,320, 25,  20, 65,  70, 'high_logistics_share', TRUE),
  ('wb_offer_009', '100009', 'WBBC009', 'WB Тяжёлая подставка',               1,  850, 710, 330, 85,-45, 18,  40, 62,  66, 'negative_logistics_anomaly', TRUE),
  ('wb_offer_010', '100010', 'WBBC010', 'WB Носки с тонкой маржой',           1, 1000, 860, 560, 90,170, 30,  30, 58,  62, 'low_margin_after_ads', TRUE),
  ('wb_offer_011', '100011', 'WBBC011', 'WB Чехол с тонкой маржой',           1, 1100, 940, 610, 95,180, 35,  30, 56,  60, 'low_margin_after_ads', TRUE),
  ('wb_offer_012', '100012', 'WBBC012', 'WB Быстрый хит Альфа',               1,  700, 590, 300, 70, 85, 18,  52,  1,   2, 'stockout_risk', TRUE),
  ('wb_offer_013', '100013', 'WBBC013', 'WB Быстрый хит Бета',                1,  650, 550, 280, 65, 82, 18,  48,  2,   3, 'stockout_risk', TRUE),
  ('wb_offer_014', '100014', 'WBBC014', 'WB Расход без выручки',              1,    0,   0,  20,  0, 20, 10,  66, 36,  39, 'zero_revenue_with_sales', TRUE),
  ('wb_offer_015', '100015', 'WBBC015', 'WB Товар с рекламой без продаж',     1,    0,   0,   0,  0,  0,  0,  95, 44,  48, 'ads_without_sales', FALSE);

DROP TABLE IF EXISTS tmp_wb_dates;
CREATE TEMP TABLE tmp_wb_dates AS
SELECT d::date AS sale_date
FROM generate_series(
  '{{period_start}}'::date,
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  INTERVAL '1 day'
) AS gs(d);

DROP TABLE IF EXISTS tmp_wb_day_profile;
CREATE TEMP TABLE tmp_wb_day_profile AS
SELECT
  d.sale_date,
  (
    CASE EXTRACT(ISODOW FROM d.sale_date)
      WHEN 1 THEN 0.86
      WHEN 2 THEN 0.93
      WHEN 3 THEN 1.00
      WHEN 4 THEN 1.08
      WHEN 5 THEN 1.17
      WHEN 6 THEN 1.24
      ELSE 0.91
    END
    + CASE WHEN EXTRACT(DAY FROM d.sale_date)::int BETWEEN 10 AND 15 THEN 0.09 ELSE 0 END
    - CASE WHEN EXTRACT(DAY FROM d.sale_date)::int IN (7, 21, 28) THEN 0.11 ELSE 0 END
  )::numeric(10,4) AS sales_factor,
  (
    CASE EXTRACT(ISODOW FROM d.sale_date)
      WHEN 1 THEN 0.90
      WHEN 2 THEN 0.96
      WHEN 3 THEN 1.00
      WHEN 4 THEN 1.06
      WHEN 5 THEN 1.14
      WHEN 6 THEN 1.22
      ELSE 0.92
    END
    + CASE WHEN EXTRACT(DAY FROM d.sale_date)::int BETWEEN 18 AND 22 THEN 0.08 ELSE 0 END
  )::numeric(10,4) AS ads_factor
FROM tmp_wb_dates d;

DROP TABLE IF EXISTS tmp_wb_sales;
CREATE TEMP TABLE tmp_wb_sales AS
SELECT
  p.*,
  d.sale_date,
  GREATEST(0, ROUND(p.daily_qty * dp.sales_factor, 0))::int AS qty_for_day,
  ROUND(p.revenue_per_unit * p.daily_qty * dp.sales_factor, 2) AS revenue_for_day,
  ROUND(p.payout_per_unit * p.daily_qty * dp.sales_factor, 2) AS payout_for_day,
  ROUND(p.ads_daily_spend * dp.ads_factor, 2) AS ads_spend_for_day,
  ('WB-ORDER-' || p.offer_id || '-' || TO_CHAR(d.sale_date, 'YYYYMMDD')) AS order_id,
  ('WB-SALE-' || p.offer_id || '-' || TO_CHAR(d.sale_date, 'YYYYMMDD')) AS sale_id,
  ('WB-FIN-' || p.offer_id || '-' || TO_CHAR(d.sale_date, 'YYYYMMDD')) AS finance_id
FROM tmp_wb_product_params p
JOIN tmp_wb_dates d
  ON TRUE
JOIN tmp_wb_day_profile dp
  ON dp.sale_date = d.sale_date
WHERE p.has_sales;

INSERT INTO {{schema}}.dim_product (
  offer_id,
  marketplace,
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
  'wb',
  nm_id,
  barcode,
  product_name,
  'WBDemoBrand',
  'wb_demo_category',
  scenario,
  FALSE,
  now(),
  now()
FROM tmp_wb_product_params;

INSERT INTO {{schema}}.fact_cogs (
  offer_id,
  marketplace,
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
  'wb',
  cogs_per_unit,
  0,
  0,
  0,
  '{{period_start}}'::date,
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  now(),
  now()
FROM tmp_wb_product_params;

INSERT INTO {{schema}}.stg_wb_product (
  nm_id,
  marketplace,
  offer_id,
  vendor_code,
  barcode,
  product_name,
  brand,
  category_name,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  nm_id,
  'wb',
  offer_id,
  offer_id,
  barcode,
  product_name,
  'WBDemoBrand',
  scenario,
  jsonb_build_object('scenario', scenario),
  now(),
  now()
FROM tmp_wb_product_params;

INSERT INTO {{schema}}.stg_wb_order (
  order_id,
  marketplace,
  order_date,
  order_status,
  cancel_dt,
  nm_id,
  offer_id,
  barcode,
  quantity,
  sale_price,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  order_id,
  'wb',
  sale_date::timestamp + INTERVAL '10 hour',
  'done',
  NULL,
  nm_id,
  offer_id,
  barcode,
  qty_for_day,
  revenue_for_day,
  jsonb_build_object('scenario', scenario),
  now(),
  now()
FROM tmp_wb_sales;

INSERT INTO {{schema}}.stg_wb_sale (
  sale_id,
  marketplace,
  sale_date,
  order_id,
  sale_status,
  is_return,
  nm_id,
  offer_id,
  barcode,
  quantity,
  revenue_gross,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  sale_id,
  'wb',
  sale_date::timestamp + INTERVAL '20 hour',
  order_id,
  'sale',
  FALSE,
  nm_id,
  offer_id,
  barcode,
  qty_for_day,
  revenue_for_day,
  jsonb_build_object('scenario', scenario),
  now(),
  now()
FROM tmp_wb_sales;

INSERT INTO {{schema}}.stg_wb_finance (
  finance_id,
  marketplace,
  operation_date,
  operation_type,
  order_id,
  sale_id,
  nm_id,
  offer_id,
  payout_amount,
  commission_amount,
  logistics_amount,
  service_amount,
  amount,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  finance_id,
  'wb',
  sale_date::timestamp + INTERVAL '22 hour',
  'sale_operation',
  order_id,
  sale_id,
  nm_id,
  offer_id,
  payout_for_day,
  commission_per_order,
  logistics_per_order,
  service_per_order,
  payout_for_day,
  jsonb_build_object('scenario', scenario),
  now(),
  now()
FROM tmp_wb_sales;

INSERT INTO {{schema}}.stg_wb_finance (
  finance_id,
  marketplace,
  operation_date,
  operation_type,
  order_id,
  sale_id,
  nm_id,
  offer_id,
  payout_amount,
  commission_amount,
  logistics_amount,
  service_amount,
  amount,
  raw_payload,
  raw_updated_at,
  loaded_at
)
VALUES
  (
    'WB-FIN-ORPHAN-001',
    'wb',
    ('{{period_start}}'::date + INTERVAL '5 day')::timestamp + INTERVAL '12 hour',
    'orphan_finance',
    NULL,
    NULL,
    '100999',
    'wb_offer_999',
    0,
    0,
    120,
    0,
    120,
    jsonb_build_object('anomaly', 'orphan_finance'),
    now(),
    now()
  ),
  (
    'WB-FIN-ORPHAN-002',
    'wb',
    ('{{period_start}}'::date + INTERVAL '12 day')::timestamp + INTERVAL '12 hour',
    'orphan_finance',
    'WB-ORDER-MISSING-001',
    NULL,
    '100998',
    'wb_offer_998',
    0,
    0,
    95,
    0,
    95,
    jsonb_build_object('anomaly', 'orphan_finance'),
    now(),
    now()
  );

INSERT INTO {{schema}}.stg_wb_ads (
  date,
  campaign_id,
  marketplace,
  campaign_name,
  campaign_type,
  offer_id,
  nm_id,
  impressions,
  clicks,
  orders_count,
  revenue,
  spend,
  currency,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  d.sale_date,
  'WB-CMP-' || p.offer_id,
  'wb',
  'WB Campaign ' || p.offer_id,
  CASE
    WHEN p.scenario IN ('stockout_risk', 'high_logistics_share') THEN 'search'
    ELSE 'auto'
  END,
  p.offer_id,
  p.nm_id,
  ROUND(p.ads_daily_spend * dp.ads_factor * 85, 0),
  GREATEST(ROUND(p.ads_daily_spend * dp.ads_factor / 7.0, 0), 0),
  CASE WHEN p.has_sales THEN GREATEST(0, ROUND(p.daily_qty * dp.sales_factor, 0))::int ELSE 0 END,
  CASE WHEN p.has_sales THEN ROUND(p.revenue_per_unit * p.daily_qty * dp.sales_factor, 2) ELSE 0 END,
  ROUND(p.ads_daily_spend * dp.ads_factor, 2),
  'RUB',
  jsonb_build_object('scenario', p.scenario),
  now(),
  now()
FROM tmp_wb_product_params p
JOIN tmp_wb_dates d
  ON TRUE
JOIN tmp_wb_day_profile dp
  ON dp.sale_date = d.sale_date;

INSERT INTO {{schema}}.fact_ads_spend (
  date,
  campaign_id,
  marketplace,
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
  a.date,
  a.campaign_id,
  a.marketplace,
  a.campaign_name,
  a.campaign_type,
  a.offer_id,
  p.barcode,
  p.nm_id,
  a.impressions,
  a.clicks,
  a.orders_count,
  a.revenue,
  a.spend,
  a.currency,
  now(),
  now()
FROM {{schema}}.stg_wb_ads a
LEFT JOIN tmp_wb_product_params p
  ON p.offer_id = a.offer_id;

INSERT INTO {{schema}}.stg_wb_stock (
  snapshot_date,
  marketplace,
  offer_id,
  nm_id,
  barcode,
  warehouse_name,
  warehouse_type,
  stock_total,
  stock_available,
  stock_reserved,
  stock_defect,
  raw_payload,
  raw_updated_at,
  loaded_at
)
SELECT
  ('{{period_end}}'::date - INTERVAL '1 day')::date,
  'wb',
  offer_id,
  nm_id,
  barcode,
  'WB Main Warehouse',
  'fulfillment',
  stock_total,
  stock_available,
  0,
  0,
  jsonb_build_object('scenario', scenario),
  now(),
  now()
FROM tmp_wb_product_params;

INSERT INTO {{schema}}.fact_inventory_snapshot (
  snapshot_date,
  marketplace,
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
  s.snapshot_date,
  s.marketplace,
  s.offer_id,
  COALESCE(s.barcode, '~'),
  COALESCE(s.nm_id, '~'),
  s.warehouse_name,
  s.warehouse_type,
  s.stock_total,
  s.stock_available,
  s.stock_reserved,
  s.stock_defect,
  now(),
  now()
FROM {{schema}}.stg_wb_stock s;
