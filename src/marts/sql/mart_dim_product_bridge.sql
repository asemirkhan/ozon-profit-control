-- mart_dim_product_bridge
-- 1 строка = канонический ключ товара + мост всех известных идентификаторов

DROP TABLE IF EXISTS {{schema}}.dim_product_bridge;

WITH wb_nm_offer_map AS (
  SELECT
    nm_id,
    MAX(canonical_offer_id) AS canonical_offer_id
  FROM (
    SELECT
      NULLIF(TRIM(p.nm_id), '') AS nm_id,
      CASE
        WHEN NULLIF(TRIM(p.offer_id), '') IS NULL THEN NULL
        WHEN TRIM(p.offer_id) = '~' THEN NULL
        WHEN TRIM(p.offer_id) ~ '^wb_nm_' THEN NULL
        ELSE TRIM(p.offer_id)
      END AS canonical_offer_id
    FROM {{schema}}.stg_wb_product p

    UNION ALL

    SELECT
      NULLIF(TRIM(d.product_id), '') AS nm_id,
      CASE
        WHEN NULLIF(TRIM(d.offer_id), '') IS NULL THEN NULL
        WHEN TRIM(d.offer_id) = '~' THEN NULL
        WHEN TRIM(d.offer_id) ~ '^wb_nm_' THEN NULL
        ELSE TRIM(d.offer_id)
      END AS canonical_offer_id
    FROM {{schema}}.dim_product d
    WHERE COALESCE(d.marketplace, '') = 'wb'
  ) src
  WHERE nm_id IS NOT NULL
  GROUP BY nm_id
),
source_rows AS (
  SELECT
    COALESCE(d.marketplace, 'ozon') AS marketplace,
    NULLIF(TRIM(d.offer_id), '') AS offer_id,
    NULLIF(TRIM(d.sku), '') AS sku,
    NULLIF(TRIM(d.product_id), '') AS product_id,
    CASE WHEN COALESCE(d.marketplace, 'ozon') = 'wb' THEN NULLIF(TRIM(d.product_id), '') ELSE NULL END AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULL::text AS item_code,
    NULLIF(TRIM(d.product_name), '') AS product_name,
    NULLIF(TRIM(d.category_name), '') AS category_name,
    NULLIF(TRIM(d.brand), '') AS brand,
    10::int AS source_priority
  FROM {{schema}}.dim_product d

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(p.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(p.nm_id), '') AS nm_id,
    NULLIF(TRIM(p.vendor_code), '') AS vendor_code,
    NULLIF(TRIM(p.barcode), '') AS barcode,
    NULL::text AS item_code,
    NULLIF(TRIM(p.product_name), '') AS product_name,
    NULLIF(TRIM(p.category_name), '') AS category_name,
    NULLIF(TRIM(p.brand), '') AS brand,
    1::int AS source_priority
  FROM {{schema}}.stg_wb_product p

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(s.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(s.nm_id), '') AS nm_id,
    NULL::text AS vendor_code,
    NULLIF(TRIM(s.barcode), '') AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    20::int AS source_priority
  FROM {{schema}}.stg_wb_sale s

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(o.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(o.nm_id), '') AS nm_id,
    NULL::text AS vendor_code,
    NULLIF(TRIM(o.barcode), '') AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    25::int AS source_priority
  FROM {{schema}}.stg_wb_order o

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(f.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(f.nm_id), '') AS nm_id,
    NULL::text AS vendor_code,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    30::int AS source_priority
  FROM {{schema}}.stg_wb_finance f

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(a.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(a.nm_id), '') AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    35::int AS source_priority
  FROM {{schema}}.stg_wb_ads a

  UNION ALL

  SELECT
    'wb' AS marketplace,
    NULLIF(TRIM(st.offer_id), '') AS offer_id,
    NULL::text AS sku,
    NULL::text AS product_id,
    NULLIF(TRIM(st.nm_id), '') AS nm_id,
    NULL::text AS vendor_code,
    NULLIF(TRIM(st.barcode), '') AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    40::int AS source_priority
  FROM {{schema}}.stg_wb_stock st

  UNION ALL

  SELECT
    'ozon' AS marketplace,
    NULLIF(TRIM(i.offer_id), '') AS offer_id,
    NULLIF(TRIM(i.sku), '') AS sku,
    NULLIF(TRIM(i.product_id), '') AS product_id,
    NULL::text AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    20::int AS source_priority
  FROM {{schema}}.stg_posting_item i

  UNION ALL

  SELECT
    'ozon' AS marketplace,
    NULL::text AS offer_id,
    NULLIF(TRIM(f.sku), '') AS sku,
    NULLIF(TRIM(f.product_id), '') AS product_id,
    NULL::text AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    25::int AS source_priority
  FROM {{schema}}.stg_posting_item_financial f

  UNION ALL

  SELECT
    'ozon' AS marketplace,
    NULL::text AS offer_id,
    NULLIF(TRIM(ti.sku), '') AS sku,
    NULL::text AS product_id,
    NULL::text AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULLIF(TRIM(ti.item_name), '') AS item_code,
    NULLIF(TRIM(ti.item_name), '') AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    30::int AS source_priority
  FROM {{schema}}.stg_transaction_item ti

  UNION ALL

  SELECT
    COALESCE(inv.marketplace, 'ozon') AS marketplace,
    NULLIF(TRIM(inv.offer_id), '') AS offer_id,
    NULLIF(TRIM(inv.sku), '') AS sku,
    NULLIF(TRIM(inv.product_id), '') AS product_id,
    CASE WHEN COALESCE(inv.marketplace, 'ozon') = 'wb' THEN NULLIF(TRIM(inv.product_id), '') ELSE NULL END AS nm_id,
    NULL::text AS vendor_code,
    NULL::text AS barcode,
    NULL::text AS item_code,
    NULL::text AS product_name,
    NULL::text AS category_name,
    NULL::text AS brand,
    40::int AS source_priority
  FROM {{schema}}.fact_inventory_snapshot inv
),
normalized AS (
  SELECT
    s.marketplace,
    CASE
      WHEN s.marketplace = 'wb' THEN
        CASE
          WHEN COALESCE(
            CASE
              WHEN s.offer_id IS NULL OR s.offer_id = '~' OR s.offer_id ~ '^wb_nm_' THEN NULL
              ELSE s.offer_id
            END,
            m.canonical_offer_id
          ) IS NOT NULL
            THEN 'wb:offer:' || COALESCE(
              CASE
                WHEN s.offer_id IS NULL OR s.offer_id = '~' OR s.offer_id ~ '^wb_nm_' THEN NULL
                ELSE s.offer_id
              END,
              m.canonical_offer_id
            )
          WHEN s.nm_id IS NOT NULL THEN 'wb:nm:' || s.nm_id
          WHEN COALESCE(s.barcode, s.sku) IS NOT NULL THEN 'wb:sku:' || COALESCE(s.barcode, s.sku)
          ELSE NULL
        END
      ELSE
        CASE
          WHEN s.offer_id IS NOT NULL AND s.offer_id <> '~' THEN 'ozon:offer:' || s.offer_id
          WHEN s.product_id IS NOT NULL AND s.product_id <> '~' THEN 'ozon:product:' || s.product_id
          WHEN s.sku IS NOT NULL AND s.sku <> '~' THEN 'ozon:sku:' || s.sku
          ELSE NULL
        END
    END AS canonical_product_key,
    CASE
      WHEN s.marketplace = 'wb'
        THEN COALESCE(
          CASE
            WHEN s.offer_id IS NULL OR s.offer_id = '~' OR s.offer_id ~ '^wb_nm_' THEN NULL
            ELSE s.offer_id
          END,
          m.canonical_offer_id,
          CASE WHEN s.offer_id = '~' THEN NULL ELSE s.offer_id END
        )
      ELSE s.offer_id
    END AS offer_id,
    s.sku,
    s.product_id,
    s.nm_id,
    s.vendor_code,
    s.barcode,
    s.item_code,
    s.product_name,
    s.category_name,
    s.brand,
    s.source_priority,
    CASE
      WHEN s.marketplace = 'wb' AND COALESCE(
        CASE
          WHEN s.offer_id IS NULL OR s.offer_id = '~' OR s.offer_id ~ '^wb_nm_' THEN NULL
          ELSE s.offer_id
        END,
        m.canonical_offer_id
      ) IS NOT NULL THEN 3
      WHEN s.marketplace = 'ozon' AND s.offer_id IS NOT NULL AND s.offer_id <> '~' THEN 3
      WHEN COALESCE(s.product_id, s.nm_id, s.sku, s.barcode) IS NOT NULL THEN 2
      ELSE 1
    END AS confidence_rank
  FROM source_rows s
  LEFT JOIN wb_nm_offer_map m
    ON s.marketplace = 'wb'
   AND s.nm_id = m.nm_id
),
filtered AS (
  SELECT *
  FROM normalized
  WHERE canonical_product_key IS NOT NULL
),
aggregated AS (
  SELECT
    marketplace,
    canonical_product_key,
    MAX(NULLIF(offer_id, '~')) AS offer_id,
    MAX(NULLIF(sku, '~')) AS sku,
    MAX(NULLIF(product_id, '~')) AS product_id,
    MAX(NULLIF(nm_id, '~')) AS nm_id,
    MAX(NULLIF(vendor_code, '~')) AS vendor_code,
    MAX(NULLIF(barcode, '~')) AS barcode,
    MAX(NULLIF(item_code, '~')) AS item_code,
    MAX(product_name) AS product_name,
    MAX(category_name) AS category_name,
    MAX(brand) AS brand,
    MAX(confidence_rank) AS confidence_rank,
    MIN(source_priority) AS source_priority
  FROM filtered
  GROUP BY marketplace, canonical_product_key
)
SELECT
  ROW_NUMBER() OVER (ORDER BY marketplace, canonical_product_key) AS bridge_id,
  marketplace,
  canonical_product_key,
  COALESCE(offer_id, '~') AS offer_id,
  sku,
  product_id,
  nm_id,
  vendor_code,
  barcode,
  item_code,
  product_name,
  category_name,
  brand,
  CASE confidence_rank
    WHEN 3 THEN 'high'
    WHEN 2 THEN 'medium'
    ELSE 'low'
  END AS match_confidence,
  now() AS created_at,
  now() AS updated_at
INTO {{schema}}.dim_product_bridge
FROM aggregated;

CREATE INDEX dim_product_bridge_key_idx
  ON {{schema}}.dim_product_bridge (marketplace, canonical_product_key);

CREATE INDEX dim_product_bridge_offer_idx
  ON {{schema}}.dim_product_bridge (marketplace, offer_id);

CREATE INDEX dim_product_bridge_nm_idx
  ON {{schema}}.dim_product_bridge (marketplace, nm_id);

CREATE INDEX dim_product_bridge_sku_idx
  ON {{schema}}.dim_product_bridge (marketplace, sku);
