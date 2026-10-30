-- mart_pnl_daily_by_product
-- 1 строка = 1 день × 1 товар × marketplace
-- Собирается из unified attribution слоя fact_product_money_attribution

DROP TABLE IF EXISTS {{schema}}.mart_pnl_daily_by_product;

WITH bridge_best AS (
  SELECT
    b.marketplace,
    b.canonical_product_key,
    b.offer_id,
    b.sku,
    b.barcode,
    b.product_name,
    ROW_NUMBER() OVER (
      PARTITION BY b.marketplace, b.canonical_product_key
      ORDER BY
        CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
        b.updated_at DESC,
        b.bridge_id DESC
    ) AS rn
  FROM {{schema}}.dim_product_bridge b
),
bridge_info AS (
  SELECT
    marketplace,
    canonical_product_key,
    COALESCE(NULLIF(offer_id, ''), '~') AS offer_id,
    COALESCE(NULLIF(sku, ''), NULLIF(barcode, ''), '~') AS sku,
    COALESCE(NULLIF(product_name, ''), '~') AS product_name
  FROM bridge_best
  WHERE rn = 1
),
units_wb AS (
  SELECT
    (s.sale_date)::date AS date,
    'wb'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    SUM(
      CASE
        WHEN COALESCE(s.is_return, FALSE) THEN -ABS(COALESCE(s.quantity, 0))
        ELSE COALESCE(s.quantity, 0)
      END
    )::numeric(18,2) AS units_sold
  FROM {{schema}}.stg_wb_sale s
  LEFT JOIN LATERAL (
    SELECT b.canonical_product_key
    FROM {{schema}}.dim_product_bridge b
    WHERE b.marketplace = 'wb'
      AND (
        (NULLIF(s.offer_id, '~') IS NOT NULL AND b.offer_id = s.offer_id)
        OR (NULLIF(s.nm_id, '') IS NOT NULL AND b.nm_id = s.nm_id)
        OR (NULLIF(s.barcode, '') IS NOT NULL AND b.barcode = s.barcode)
      )
    ORDER BY
      CASE
        WHEN NULLIF(s.offer_id, '~') IS NOT NULL AND b.offer_id = s.offer_id THEN 1
        WHEN NULLIF(s.nm_id, '') IS NOT NULL AND b.nm_id = s.nm_id THEN 2
        WHEN NULLIF(s.barcode, '') IS NOT NULL AND b.barcode = s.barcode THEN 3
        ELSE 4
      END,
      CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
      b.updated_at DESC,
      b.bridge_id DESC
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(s.marketplace, 'wb') = 'wb'
  GROUP BY 1,2,3
),
units_ozon AS (
  SELECT
    (p.created_at)::date AS date,
    'ozon'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    SUM(COALESCE(i.quantity, 0))::numeric(18,2) AS units_sold
  FROM {{schema}}.stg_posting_item i
  JOIN {{schema}}.stg_posting p
    ON TRIM(p.posting_number) = TRIM(i.posting_number)
   AND COALESCE(p.marketplace, 'ozon') = COALESCE(i.marketplace, 'ozon')
  LEFT JOIN LATERAL (
    SELECT b.canonical_product_key
    FROM {{schema}}.dim_product_bridge b
    WHERE b.marketplace = 'ozon'
      AND (
        (NULLIF(i.offer_id, '~') IS NOT NULL AND b.offer_id = i.offer_id)
        OR (NULLIF(i.product_id, '~') IS NOT NULL AND b.product_id = i.product_id)
        OR (NULLIF(i.sku, '~') IS NOT NULL AND b.sku = i.sku)
      )
    ORDER BY
      CASE
        WHEN NULLIF(i.offer_id, '~') IS NOT NULL AND b.offer_id = i.offer_id THEN 1
        WHEN NULLIF(i.product_id, '~') IS NOT NULL AND b.product_id = i.product_id THEN 2
        WHEN NULLIF(i.sku, '~') IS NOT NULL AND b.sku = i.sku THEN 3
        ELSE 4
      END,
      CASE b.match_confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
      b.updated_at DESC,
      b.bridge_id DESC
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(i.marketplace, 'ozon') = 'ozon'
  GROUP BY 1,2,3
),
units_daily AS (
  SELECT
    date,
    marketplace,
    canonical_product_key,
    SUM(units_sold)::numeric(18,2) AS units_sold
  FROM (
    SELECT * FROM units_wb
    UNION ALL
    SELECT * FROM units_ozon
  ) x
  GROUP BY 1,2,3
),
money_daily AS (
  SELECT
    m.event_date AS date,
    m.marketplace,
    m.canonical_product_key,
    SUM(CASE WHEN m.money_bucket = 'revenue' THEN m.amount ELSE 0 END)::numeric(18,2) AS revenue_gross,
    SUM(CASE WHEN m.money_bucket = 'returns' THEN m.amount ELSE 0 END)::numeric(18,2) AS returns_amount,
    SUM(CASE WHEN m.money_bucket = 'commission' THEN m.amount ELSE 0 END)::numeric(18,2) AS commission_amount,
    SUM(CASE WHEN m.money_bucket = 'logistics' THEN m.amount ELSE 0 END)::numeric(18,2) AS logistics_amount,
    SUM(CASE WHEN m.money_bucket = 'storage' THEN m.amount ELSE 0 END)::numeric(18,2) AS storage_amount,
    SUM(CASE WHEN m.money_bucket = 'payment_processing' THEN m.amount ELSE 0 END)::numeric(18,2) AS payment_processing_amount,
    SUM(CASE WHEN m.money_bucket = 'penalties' THEN m.amount ELSE 0 END)::numeric(18,2) AS penalties_amount,
    SUM(CASE WHEN m.money_bucket = 'services' THEN m.amount ELSE 0 END)::numeric(18,2) AS services_amount,
    SUM(CASE WHEN m.money_bucket = 'other' THEN m.amount ELSE 0 END)::numeric(18,2) AS other_amount,
    SUM(CASE WHEN m.money_bucket = 'ads' THEN m.amount ELSE 0 END)::numeric(18,2) AS ads_spend,
    SUM(CASE WHEN m.money_bucket = 'cogs' THEN m.amount ELSE 0 END)::numeric(18,2) AS cogs_amount
  FROM {{schema}}.fact_product_money_attribution m
  GROUP BY 1,2,3
),
keys AS (
  SELECT date, marketplace, canonical_product_key FROM units_daily
  UNION
  SELECT date, marketplace, canonical_product_key FROM money_daily
)
SELECT
  k.date,
  k.marketplace,
  COALESCE(b.offer_id, '~') AS offer_id,
  COALESCE(b.sku, '~') AS sku,
  COALESCE(b.product_name, '~') AS product_name,
  COALESCE(u.units_sold, 0)::numeric(18,2) AS units_sold,
  COALESCE(m.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
  (
    COALESCE(m.revenue_gross, 0)
    - COALESCE(m.returns_amount, 0)
    - COALESCE(m.commission_amount, 0)
    - COALESCE(m.logistics_amount, 0)
    - COALESCE(m.storage_amount, 0)
    - COALESCE(m.payment_processing_amount, 0)
    - COALESCE(m.penalties_amount, 0)
    - COALESCE(m.services_amount, 0)
    - COALESCE(m.other_amount, 0)
  )::numeric(18,2) AS payout,
  COALESCE(m.commission_amount, 0)::numeric(18,2) AS commission_amount,
  COALESCE(m.logistics_amount, 0)::numeric(18,2) AS logistics_amount,
  COALESCE(m.services_amount, 0)::numeric(18,2) AS services_amount,
  COALESCE(m.storage_amount, 0)::numeric(18,2) AS storage_amount,
  COALESCE(m.payment_processing_amount, 0)::numeric(18,2) AS payment_processing_amount,
  COALESCE(m.penalties_amount, 0)::numeric(18,2) AS penalties_amount,
  COALESCE(m.other_amount, 0)::numeric(18,2) AS other_amount,
  (
    COALESCE(m.services_amount, 0)
    + COALESCE(m.storage_amount, 0)
    + COALESCE(m.payment_processing_amount, 0)
    + COALESCE(m.penalties_amount, 0)
    + COALESCE(m.other_amount, 0)
  )::numeric(18,2) AS marketplace_other_fees_amount,
  COALESCE(m.returns_amount, 0)::numeric(18,2) AS returns_amount,
  COALESCE(m.cogs_amount, 0)::numeric(18,2) AS cogs_amount,
  COALESCE(m.ads_spend, 0)::numeric(18,2) AS ads_spend,
  (
    COALESCE(m.revenue_gross, 0)
    - COALESCE(m.returns_amount, 0)
    - COALESCE(m.commission_amount, 0)
    - COALESCE(m.logistics_amount, 0)
    - COALESCE(m.storage_amount, 0)
    - COALESCE(m.payment_processing_amount, 0)
    - COALESCE(m.penalties_amount, 0)
    - COALESCE(m.services_amount, 0)
    - COALESCE(m.other_amount, 0)
  )::numeric(18,2) AS profit_before_ads,
  (
    COALESCE(m.revenue_gross, 0)
    - COALESCE(m.returns_amount, 0)
    - COALESCE(m.commission_amount, 0)
    - COALESCE(m.logistics_amount, 0)
    - COALESCE(m.storage_amount, 0)
    - COALESCE(m.payment_processing_amount, 0)
    - COALESCE(m.penalties_amount, 0)
    - COALESCE(m.services_amount, 0)
    - COALESCE(m.other_amount, 0)
    - COALESCE(m.ads_spend, 0)
  )::numeric(18,2) AS profit_after_ads,
  CASE
    WHEN COALESCE(m.revenue_gross, 0) > 0 THEN (
      COALESCE(m.revenue_gross, 0)
      - COALESCE(m.returns_amount, 0)
      - COALESCE(m.commission_amount, 0)
      - COALESCE(m.logistics_amount, 0)
      - COALESCE(m.storage_amount, 0)
      - COALESCE(m.payment_processing_amount, 0)
      - COALESCE(m.penalties_amount, 0)
      - COALESCE(m.services_amount, 0)
      - COALESCE(m.other_amount, 0)
      - COALESCE(m.ads_spend, 0)
    ) / m.revenue_gross
    ELSE NULL
  END AS margin_after_ads,
  CASE
    WHEN COALESCE(u.units_sold, 0) <> 0 THEN (
      COALESCE(m.revenue_gross, 0)
      - COALESCE(m.returns_amount, 0)
      - COALESCE(m.commission_amount, 0)
      - COALESCE(m.logistics_amount, 0)
      - COALESCE(m.storage_amount, 0)
      - COALESCE(m.payment_processing_amount, 0)
      - COALESCE(m.penalties_amount, 0)
      - COALESCE(m.services_amount, 0)
      - COALESCE(m.other_amount, 0)
      - COALESCE(m.ads_spend, 0)
    ) / u.units_sold
    ELSE NULL
  END AS profit_per_unit_after_ads,
  0::numeric(18,2) AS discount_total
INTO {{schema}}.mart_pnl_daily_by_product
FROM keys k
LEFT JOIN units_daily u
  ON u.date = k.date
 AND u.marketplace = k.marketplace
 AND u.canonical_product_key = k.canonical_product_key
LEFT JOIN money_daily m
  ON m.date = k.date
 AND m.marketplace = k.marketplace
 AND m.canonical_product_key = k.canonical_product_key
LEFT JOIN bridge_info b
  ON b.marketplace = k.marketplace
 AND b.canonical_product_key = k.canonical_product_key;

CREATE INDEX mart_pnl_daily_by_product_date_idx
  ON {{schema}}.mart_pnl_daily_by_product (date);

CREATE INDEX mart_pnl_daily_by_product_offer_idx
  ON {{schema}}.mart_pnl_daily_by_product (marketplace, offer_id);

CREATE INDEX mart_pnl_daily_by_product_marketplace_idx
  ON {{schema}}.mart_pnl_daily_by_product (marketplace);
