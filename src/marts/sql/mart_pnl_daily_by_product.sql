-- mart_pnl_daily_by_product
-- 1 строка = 1 день × 1 товар (offer_id)
-- v1: транзакции разносятся по posting_number (без даты) пропорционально revenue_gross
-- защита от пробелов/мусора в ключах: posting_number нормализуется через TRIM()

DROP TABLE IF EXISTS {{schema}}.mart_pnl_daily_by_product;

WITH base_items AS (
  SELECT
    TRIM(p.posting_number) AS posting_number,
    (p.created_at)::date AS date,
    COALESCE(NULLIF(i.offer_id,'~'), dp.offer_id, '~') AS offer_id,
    COALESCE(NULLIF(i.sku,'~'), dp.sku, '~') AS sku,
    COALESCE(dp.product_name, NULLIF(i.offer_id,'~'), NULLIF(i.sku,'~'), '~') AS product_name,
    SUM(i.quantity) AS units_sold,
    SUM(COALESCE(i.item_price, 0) * i.quantity) AS revenue_gross
  FROM {{schema}}.stg_posting p
  JOIN {{schema}}.stg_posting_item i
    ON TRIM(i.posting_number) = TRIM(p.posting_number)
  LEFT JOIN {{schema}}.dim_product dp
    ON (dp.sku IS NOT NULL AND dp.sku = NULLIF(i.sku,'~'))
    OR (dp.offer_id IS NOT NULL AND dp.offer_id = NULLIF(i.offer_id,'~'))
  GROUP BY 1,2,3,4,5
),
posting_totals AS (
  SELECT
    posting_number,
    date,
    SUM(revenue_gross) AS posting_revenue_gross
  FROM base_items
  GROUP BY 1,2
),
fin_by_posting_offer AS (
  SELECT
    TRIM(p.posting_number) AS posting_number,
    (p.created_at)::date AS date,
    dp.offer_id AS offer_id,
    dp.sku AS sku,
    SUM(COALESCE(f.payout,0)) AS payout,
    SUM(COALESCE(f.commission_amount,0)) AS commission_amount,
    SUM(COALESCE(f.item_services_total,0)) AS services_from_posting_fin,
    SUM(COALESCE(f.total_discount_value,0)) AS discount_total
  FROM {{schema}}.stg_posting p
  JOIN {{schema}}.stg_posting_item_financial f
    ON TRIM(f.posting_number) = TRIM(p.posting_number)
  LEFT JOIN {{schema}}.dim_product dp
    ON (dp.product_id IS NOT NULL AND dp.product_id = f.product_id)
    OR (dp.sku IS NOT NULL AND dp.sku = f.sku)
  GROUP BY 1,2,3,4
),

-- ===== TRANSACTIONS (по posting_number, без даты) =====
tx_logistics_by_posting AS (
  SELECT
    TRIM(t.posting_number) AS posting_number,
    SUM(COALESCE(t.delivery_charge,0) + COALESCE(t.return_delivery_charge,0)) AS logistics_amount
  FROM {{schema}}.stg_transaction t
  WHERE t.posting_number IS NOT NULL AND TRIM(t.posting_number) <> ''
  GROUP BY 1
),
tx_services_by_posting AS (
  SELECT
    TRIM(t.posting_number) AS posting_number,
    SUM(COALESCE(s.service_price,0)) AS services_amount
  FROM {{schema}}.stg_transaction t
  JOIN {{schema}}.stg_transaction_service s
    ON s.operation_id = t.operation_id
  WHERE t.posting_number IS NOT NULL AND TRIM(t.posting_number) <> ''
  GROUP BY 1
),
tx_by_posting AS (
  SELECT
    COALESCE(l.posting_number, sv.posting_number) AS posting_number,
    COALESCE(l.logistics_amount,0) AS logistics_amount,
    COALESCE(sv.services_amount,0) AS services_amount
  FROM tx_logistics_by_posting l
  FULL JOIN tx_services_by_posting sv
    ON sv.posting_number = l.posting_number
),

ads_daily AS (
  SELECT
    a.date,
    COALESCE(NULLIF(a.offer_id,'~'), dp.offer_id, '~') AS offer_id,
    SUM(a.spend) AS ads_spend
  FROM {{schema}}.fact_ads_spend a
  LEFT JOIN {{schema}}.dim_product dp
    ON (dp.sku IS NOT NULL AND dp.sku = NULLIF(a.sku,'~'))
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
  LEFT JOIN {{schema}}.fact_cogs c
    ON c.offer_id = b.offer_id
  GROUP BY 1,2
),
tx_allocated AS (
  SELECT
    b.date,
    b.posting_number,
    b.offer_id,
    CASE
      WHEN pt.posting_revenue_gross > 0
        THEN (b.revenue_gross / pt.posting_revenue_gross) * COALESCE(tx.logistics_amount,0)
      ELSE 0
    END AS logistics_amount_alloc,
    CASE
      WHEN pt.posting_revenue_gross > 0
        THEN (b.revenue_gross / pt.posting_revenue_gross) * COALESCE(tx.services_amount,0)
      ELSE 0
    END AS services_amount_alloc
  FROM base_items b
  JOIN posting_totals pt
    ON pt.posting_number = b.posting_number AND pt.date = b.date
  LEFT JOIN tx_by_posting tx
    ON tx.posting_number = b.posting_number
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

  COALESCE(tx.logistics_amount_alloc,0) AS logistics_amount,
  (COALESCE(tx.services_amount_alloc,0) + COALESCE(f.services_from_posting_fin,0)) AS services_amount,

  CAST(0 AS NUMERIC(18,2)) AS returns_amount,

  COALESCE(cg.cogs_amount,0) AS cogs_amount,
  COALESCE(a.ads_spend,0) AS ads_spend,

  (COALESCE(f.payout,0)
   - COALESCE(cg.cogs_amount,0)
   - COALESCE(tx.logistics_amount_alloc,0)
   - COALESCE(tx.services_amount_alloc,0)
   - COALESCE(f.services_from_posting_fin,0)
  ) AS profit_before_ads,

  (COALESCE(f.payout,0)
   - COALESCE(cg.cogs_amount,0)
   - COALESCE(tx.logistics_amount_alloc,0)
   - COALESCE(tx.services_amount_alloc,0)
   - COALESCE(f.services_from_posting_fin,0)
   - COALESCE(a.ads_spend,0)
  ) AS profit_after_ads,

  CASE
    WHEN b.revenue_gross > 0
      THEN (COALESCE(f.payout,0)
            - COALESCE(cg.cogs_amount,0)
            - COALESCE(tx.logistics_amount_alloc,0)
            - COALESCE(tx.services_amount_alloc,0)
            - COALESCE(f.services_from_posting_fin,0)
            - COALESCE(a.ads_spend,0)
           ) / b.revenue_gross
    ELSE NULL
  END AS margin_after_ads,

  CASE
    WHEN b.units_sold > 0
      THEN (COALESCE(f.payout,0)
            - COALESCE(cg.cogs_amount,0)
            - COALESCE(tx.logistics_amount_alloc,0)
            - COALESCE(tx.services_amount_alloc,0)
            - COALESCE(f.services_from_posting_fin,0)
            - COALESCE(a.ads_spend,0)
           ) / b.units_sold
    ELSE NULL
  END AS profit_per_unit_after_ads,

  COALESCE(f.discount_total,0) AS discount_total

INTO {{schema}}.mart_pnl_daily_by_product
FROM base_items b
LEFT JOIN fin_by_posting_offer f
  ON f.posting_number = b.posting_number
 AND f.date = b.date
 AND f.offer_id = b.offer_id
LEFT JOIN tx_allocated tx
  ON tx.posting_number = b.posting_number
 AND tx.date = b.date
 AND tx.offer_id = b.offer_id
LEFT JOIN cogs_daily cg
  ON cg.date = b.date
 AND cg.offer_id = b.offer_id
LEFT JOIN ads_daily a
  ON a.date = b.date
 AND a.offer_id = b.offer_id;

CREATE INDEX mart_pnl_daily_by_product_date_idx
  ON {{schema}}.mart_pnl_daily_by_product (date);

CREATE INDEX mart_pnl_daily_by_product_offer_idx
  ON {{schema}}.mart_pnl_daily_by_product (offer_id);