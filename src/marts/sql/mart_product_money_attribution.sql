-- mart_product_money_attribution
-- 1 строка = денежное событие, привязанное к canonical_product_key

DROP TABLE IF EXISTS {{schema}}.fact_product_money_attribution;

WITH bridge AS (
  SELECT *
  FROM {{schema}}.dim_product_bridge
),

-- =========================
-- WB revenue and helper maps
-- =========================
wb_sales_mapped AS (
  SELECT
    s.sale_id,
    (s.sale_date)::date AS event_date,
    'wb'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    COALESCE(bm.offer_id, NULLIF(s.offer_id, '~'), '~') AS resolved_offer_id,
    CASE
      WHEN COALESCE(s.is_return, FALSE) THEN -ABS(COALESCE(s.quantity, 0))
      ELSE COALESCE(s.quantity, 0)
    END AS units_sold,
    COALESCE(s.revenue_gross, 0)::numeric(18,2) AS revenue_gross,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN 'unmatched'
      WHEN NULLIF(s.offer_id, '~') IS NOT NULL AND bm.offer_id = s.offer_id THEN 'exact_id_match'
      WHEN s.nm_id IS NOT NULL AND bm.nm_id = s.nm_id THEN 'nm_id_match'
      WHEN s.barcode IS NOT NULL AND bm.barcode = s.barcode THEN 'sku_match'
      ELSE 'bridge_match'
    END AS attribution_method,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN 'low'
      WHEN NULLIF(s.offer_id, '~') IS NOT NULL AND bm.offer_id = s.offer_id THEN 'high'
      WHEN s.nm_id IS NOT NULL AND bm.nm_id = s.nm_id THEN 'high'
      ELSE 'medium'
    END AS attribution_confidence,
    NULLIF(TRIM(s.order_id), '') AS order_id,
    GREATEST(COALESCE(s.revenue_gross, 0), 0)::numeric(18,2) AS alloc_revenue
  FROM {{schema}}.stg_wb_sale s
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
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
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(s.marketplace, 'wb') = 'wb'
),
wb_order_map AS (
  SELECT
    x.order_id,
    x.canonical_product_key
  FROM (
    SELECT
      wm.order_id,
      wm.canonical_product_key,
      ROW_NUMBER() OVER (
        PARTITION BY wm.order_id
        ORDER BY wm.attribution_confidence DESC, wm.sale_id
      ) AS rn
    FROM wb_sales_mapped wm
    WHERE wm.order_id IS NOT NULL
      AND wm.order_id <> ''
      AND wm.canonical_product_key <> '~'
  ) x
  WHERE x.rn = 1
),
wb_sales_daily_share AS (
  SELECT
    event_date,
    canonical_product_key,
    SUM(alloc_revenue) AS revenue_for_share,
    CASE
      WHEN SUM(SUM(alloc_revenue)) OVER (PARTITION BY event_date) > 0
        THEN SUM(alloc_revenue) / SUM(SUM(alloc_revenue)) OVER (PARTITION BY event_date)
      ELSE NULL
    END AS revenue_share
  FROM wb_sales_mapped
  WHERE canonical_product_key <> '~'
  GROUP BY event_date, canonical_product_key
),
wb_paid_storage_mapped AS (
  SELECT
    s.source_record_id,
    s.report_date AS event_date,
    'wb'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    GREATEST(COALESCE(s.warehouse_price, 0), 0)::numeric(18,2) AS storage_amount
  FROM {{schema}}.stg_wb_paid_storage s
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
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
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(s.marketplace, 'wb') = 'wb'
    AND GREATEST(COALESCE(s.warehouse_price, 0), 0) > 0
),
wb_paid_storage_daily_share AS (
  SELECT
    event_date,
    canonical_product_key,
    SUM(storage_amount) AS storage_for_share,
    CASE
      WHEN SUM(SUM(storage_amount)) OVER (PARTITION BY event_date) > 0
        THEN SUM(storage_amount) / SUM(SUM(storage_amount)) OVER (PARTITION BY event_date)
      ELSE NULL
    END AS storage_share
  FROM wb_paid_storage_mapped
  WHERE canonical_product_key <> '~'
  GROUP BY event_date, canonical_product_key
),

-- =========================
-- WB finance: split into canonical buckets + multi-step attribution
-- =========================
wb_finance_components AS (
  SELECT
    f.finance_id,
    (f.operation_date)::date AS event_date,
    NULLIF(TRIM(f.order_id), '') AS order_id,
    NULLIF(TRIM(f.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.nm_id), '') AS nm_id,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    LOWER(COALESCE(f.operation_type, '')) AS operation_type,
    'commission'::text AS money_bucket,
    ABS(COALESCE(f.commission_amount, 0))::numeric(18,2) AS amount
  FROM {{schema}}.stg_wb_finance f
  WHERE COALESCE(f.marketplace, 'wb') = 'wb'

  UNION ALL

  SELECT
    f.finance_id,
    (f.operation_date)::date AS event_date,
    NULLIF(TRIM(f.order_id), '') AS order_id,
    NULLIF(TRIM(f.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.nm_id), '') AS nm_id,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    LOWER(COALESCE(f.operation_type, '')) AS operation_type,
    'logistics'::text AS money_bucket,
    ABS(COALESCE(f.logistics_amount, 0))::numeric(18,2) AS amount
  FROM {{schema}}.stg_wb_finance f
  WHERE COALESCE(f.marketplace, 'wb') = 'wb'

  UNION ALL

  SELECT
    f.finance_id,
    (f.operation_date)::date AS event_date,
    NULLIF(TRIM(f.order_id), '') AS order_id,
    NULLIF(TRIM(f.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.nm_id), '') AS nm_id,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    LOWER(COALESCE(f.operation_type, '')) AS operation_type,
    'services'::text AS money_bucket,
    ABS(COALESCE(f.service_amount, 0))::numeric(18,2) AS amount
  FROM {{schema}}.stg_wb_finance f
  WHERE COALESCE(f.marketplace, 'wb') = 'wb'

  UNION ALL

  SELECT
    f.finance_id,
    (f.operation_date)::date AS event_date,
    NULLIF(TRIM(f.order_id), '') AS order_id,
    NULLIF(NULLIF(TRIM(f.offer_id), ''), '~') AS offer_id,
    COALESCE(
      NULLIF(NULLIF(TRIM(f.nm_id), ''), '0'),
      NULLIF(substring(LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) FROM 'товар ([0-9]+)'), '')
    ) AS nm_id,
    NULLIF(TRIM(f.barcode), '') AS barcode,
    LOWER(COALESCE(f.operation_type, '')) AS operation_type,
    CASE
      WHEN LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%wb продвижени%'
        THEN 'ads_overlap_excluded'
      WHEN LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%продвижени%'
        OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%подписк%'
        OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%списание за отзыв%'
        OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%предоставление услуг%'
        OR LOWER(COALESCE(f.raw_payload->>'bonus_type_name', f.raw_payload->>'bonusTypeName', '')) LIKE '%оказание услуг%'
        THEN 'services'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%комисс%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%commission%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%вознагражд%'
        THEN 'commission'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%достав%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%логист%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%delivery%'
        THEN 'logistics'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%услуг%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%service%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%подписк%'
        THEN 'services'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%возврат%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%сторн%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%refund%'
        THEN 'returns'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%штраф%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%удерж%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%penalt%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%deduct%'
        THEN 'penalties'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%хран%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%storage%'
        THEN 'storage'
      WHEN LOWER(COALESCE(f.operation_type, '')) LIKE '%эквайр%'
        OR LOWER(COALESCE(f.operation_type, '')) LIKE '%acquir%'
        THEN 'payment_processing'
      ELSE 'other'
    END AS money_bucket,
    ABS(COALESCE(f.amount, 0))::numeric(18,2) AS amount
  FROM {{schema}}.stg_wb_finance f
  WHERE COALESCE(f.marketplace, 'wb') = 'wb'
    -- Some finance rows repeat the same charge in both `amount` and
    -- `additional_payment`/`service_amount`.  The service component above is
    -- the more specific representation; emitting the generic amount as well
    -- charged the same correction twice.
    AND NOT (
      ABS(COALESCE(f.amount, 0)) > 0
      AND ABS(COALESCE(f.amount, 0)) = ABS(COALESCE(f.service_amount, 0))
    )
),
wb_finance_components_nonzero AS (
  SELECT *
  FROM wb_finance_components
  WHERE amount > 0
    AND money_bucket <> 'ads_overlap_excluded'
),
wb_finance_direct AS (
  SELECT
    c.event_date,
    'wb'::text AS marketplace,
    COALESCE(om.canonical_product_key, bm.canonical_product_key) AS canonical_product_key,
    'expense'::text AS money_component_type,
    c.money_bucket,
    c.amount,
    CASE
      WHEN om.canonical_product_key IS NOT NULL THEN 'order_id_match'
      WHEN bm.canonical_product_key IS NOT NULL AND c.nm_id IS NOT NULL AND bm.nm_id = c.nm_id THEN 'nm_id_match'
      WHEN bm.canonical_product_key IS NOT NULL AND c.offer_id IS NOT NULL AND bm.offer_id = c.offer_id THEN 'offer_id_match'
      WHEN bm.canonical_product_key IS NOT NULL AND c.barcode IS NOT NULL AND bm.barcode = c.barcode THEN 'barcode_match'
      WHEN bm.canonical_product_key IS NOT NULL THEN 'bridge_match'
      ELSE NULL
    END AS attribution_method,
    CASE
      WHEN om.canonical_product_key IS NOT NULL THEN 'high'
      WHEN bm.canonical_product_key IS NOT NULL AND c.nm_id IS NOT NULL AND bm.nm_id = c.nm_id THEN 'high'
      WHEN bm.canonical_product_key IS NOT NULL AND c.offer_id IS NOT NULL AND bm.offer_id = c.offer_id THEN 'high'
      WHEN bm.canonical_product_key IS NOT NULL AND c.barcode IS NOT NULL AND bm.barcode = c.barcode THEN 'medium'
      WHEN bm.canonical_product_key IS NOT NULL THEN 'medium'
      ELSE NULL
    END AS attribution_confidence,
    'stg_wb_finance'::text AS source_table,
    c.finance_id || ':' || c.money_bucket AS source_record_id
  FROM wb_finance_components_nonzero c
  LEFT JOIN wb_order_map om
    ON om.order_id = c.order_id
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
    WHERE b.marketplace = 'wb'
      AND (
        (c.nm_id IS NOT NULL AND b.nm_id = c.nm_id)
        OR (c.offer_id IS NOT NULL AND c.offer_id <> '~' AND b.offer_id = c.offer_id)
        OR (c.barcode IS NOT NULL AND b.barcode = c.barcode)
      )
    ORDER BY
      CASE
        WHEN c.nm_id IS NOT NULL AND b.nm_id = c.nm_id THEN 1
        WHEN c.offer_id IS NOT NULL AND c.offer_id <> '~' AND b.offer_id = c.offer_id THEN 2
        WHEN c.barcode IS NOT NULL AND b.barcode = c.barcode THEN 3
        ELSE 4
      END,
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
),
wb_finance_direct_resolved AS (
  SELECT *
  FROM wb_finance_direct
  WHERE canonical_product_key IS NOT NULL
),
wb_finance_unmatched AS (
  SELECT
    c.*
  FROM wb_finance_components_nonzero c
  LEFT JOIN wb_finance_direct_resolved d
    ON d.source_record_id = c.finance_id || ':' || c.money_bucket
  WHERE d.source_record_id IS NULL
),
wb_finance_storage_allocated AS (
  SELECT
    x.event_date,
    x.marketplace,
    x.canonical_product_key,
    x.money_component_type,
    x.money_bucket,
    CASE
      WHEN x.share_rank = 1
        THEN (x.rounded_amount + x.rounding_residual)::numeric(18,2)
      ELSE x.rounded_amount
    END AS amount,
    x.attribution_method,
    x.attribution_confidence,
    x.source_table,
    x.source_record_id
  FROM (
    SELECT
      u.event_date,
      'wb'::text AS marketplace,
      s.canonical_product_key,
      'expense'::text AS money_component_type,
      u.money_bucket,
      (u.amount * COALESCE(s.storage_share, 0))::numeric(18,2) AS rounded_amount,
      (
        u.amount
        - SUM((u.amount * COALESCE(s.storage_share, 0))::numeric(18,2))
            OVER (PARTITION BY u.finance_id, u.money_bucket)
      )::numeric(18,2) AS rounding_residual,
      ROW_NUMBER() OVER (
        PARTITION BY u.finance_id, u.money_bucket
        ORDER BY COALESCE(s.storage_share, 0) DESC, s.canonical_product_key
      ) AS share_rank,
      'paid_storage_share_day'::text AS attribution_method,
      'high'::text AS attribution_confidence,
      'stg_wb_finance'::text AS source_table,
      u.finance_id || ':' || u.money_bucket || ':storage_alloc:' || s.canonical_product_key AS source_record_id
    FROM wb_finance_unmatched u
    JOIN wb_paid_storage_daily_share s
      ON s.event_date = u.event_date
    WHERE u.money_bucket = 'storage'
      AND COALESCE(s.storage_share, 0) > 0
  ) x
),
wb_finance_unmatched_after_storage AS (
  SELECT
    u.*
  FROM wb_finance_unmatched u
  LEFT JOIN (
    SELECT DISTINCT split_part(source_record_id, ':storage_alloc:', 1) AS storage_alloc_key
    FROM wb_finance_storage_allocated
  ) a
    ON a.storage_alloc_key = u.finance_id || ':' || u.money_bucket
  WHERE a.storage_alloc_key IS NULL
),
wb_finance_allocated AS (
  SELECT
    u.event_date,
    'wb'::text AS marketplace,
    s.canonical_product_key,
    'expense'::text AS money_component_type,
    u.money_bucket,
    (u.amount * (
      s.revenue_for_share
      / NULLIF(SUM(s.revenue_for_share) OVER (PARTITION BY u.finance_id, u.money_bucket), 0)
    ))::numeric(18,2) AS amount,
    'revenue_share_window'::text AS attribution_method,
    'low'::text AS attribution_confidence,
    'stg_wb_finance'::text AS source_table,
    u.finance_id || ':' || u.money_bucket || ':alloc:' || s.canonical_product_key AS source_record_id
  FROM wb_finance_unmatched_after_storage u
  JOIN (
    SELECT
      event_date,
      canonical_product_key,
      SUM(alloc_revenue) AS revenue_for_share
    FROM wb_sales_mapped
    WHERE canonical_product_key <> '~'
    GROUP BY 1,2
  ) s
    ON s.event_date BETWEEN (u.event_date - INTERVAL '3 day') AND (u.event_date + INTERVAL '3 day')
  WHERE u.money_bucket <> 'services'
),
wb_finance_store_level AS (
  SELECT
    u.event_date,
    'wb'::text AS marketplace,
    '~'::text AS canonical_product_key,
    'expense'::text AS money_component_type,
    u.money_bucket,
    u.amount,
    'store_level'::text AS attribution_method,
    'high'::text AS attribution_confidence,
    'stg_wb_finance'::text AS source_table,
    u.finance_id || ':' || u.money_bucket || ':store_level' AS source_record_id
  FROM wb_finance_unmatched_after_storage u
  WHERE u.money_bucket = 'services'
),
wb_finance_still_unmatched AS (
  SELECT
    u.event_date,
    'wb'::text AS marketplace,
    '~'::text AS canonical_product_key,
    'expense'::text AS money_component_type,
    u.money_bucket,
    u.amount,
    'unmatched'::text AS attribution_method,
    'low'::text AS attribution_confidence,
    'stg_wb_finance'::text AS source_table,
    u.finance_id || ':' || u.money_bucket || ':unmatched' AS source_record_id
  FROM wb_finance_unmatched_after_storage u
  LEFT JOIN (
    SELECT DISTINCT split_part(source_record_id, ':alloc:', 1) AS alloc_key
    FROM wb_finance_allocated
  ) a
    ON a.alloc_key = u.finance_id || ':' || u.money_bucket
  WHERE a.alloc_key IS NULL
    AND u.money_bucket <> 'services'
),

-- =========================
-- WB ads: nm_id/bridge match + same-day revenue share fallback
-- =========================
wb_ads_direct AS (
  SELECT
    a.date AS event_date,
    'wb'::text AS marketplace,
    COALESCE(bm.canonical_product_key, NULL) AS canonical_product_key,
    'expense'::text AS money_component_type,
    'ads'::text AS money_bucket,
    ABS(COALESCE(a.spend, 0))::numeric(18,2) AS amount,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN NULL
      WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND bm.offer_id = a.offer_id THEN 'exact_id_match'
      WHEN a.nm_id IS NOT NULL AND bm.nm_id = a.nm_id THEN 'nm_id_match'
      ELSE 'bridge_match'
    END AS attribution_method,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN NULL
      WHEN a.nm_id IS NOT NULL AND bm.nm_id = a.nm_id THEN 'high'
      WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND bm.offer_id = a.offer_id THEN 'high'
      ELSE 'medium'
    END AS attribution_confidence,
    'stg_wb_ads'::text AS source_table,
    a.campaign_id
      || ':'
      || COALESCE(NULLIF(a.offer_id, ''), '~')
      || ':'
      || COALESCE(NULLIF(a.nm_id, ''), '~')
      || ':'
      || COALESCE(NULLIF(a.app_type, ''), '~')
      || ':'
      || a.date::text AS source_record_id
  FROM {{schema}}.stg_wb_ads a
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
    WHERE b.marketplace = 'wb'
      AND (
        (NULLIF(a.offer_id, '~') IS NOT NULL AND b.offer_id = a.offer_id)
        OR (NULLIF(a.nm_id, '') IS NOT NULL AND b.nm_id = a.nm_id)
      )
    ORDER BY
      CASE
        WHEN NULLIF(a.offer_id, '~') IS NOT NULL AND b.offer_id = a.offer_id THEN 1
        WHEN NULLIF(a.nm_id, '') IS NOT NULL AND b.nm_id = a.nm_id THEN 2
        ELSE 3
      END,
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(a.marketplace, 'wb') = 'wb'
    AND ABS(COALESCE(a.spend, 0)) > 0
),
wb_ads_direct_resolved AS (
  SELECT *
  FROM wb_ads_direct
  WHERE canonical_product_key IS NOT NULL
),
wb_ads_unmatched AS (
  SELECT *
  FROM wb_ads_direct
  WHERE canonical_product_key IS NULL
),
wb_ads_allocated AS (
  SELECT
    u.event_date,
    'wb'::text AS marketplace,
    s.canonical_product_key,
    'expense'::text AS money_component_type,
    'ads'::text AS money_bucket,
    (u.amount * COALESCE(s.revenue_share, 0))::numeric(18,2) AS amount,
    'revenue_share_window'::text AS attribution_method,
    'low'::text AS attribution_confidence,
    'stg_wb_ads'::text AS source_table,
    u.source_record_id || ':alloc:' || s.canonical_product_key AS source_record_id
  FROM wb_ads_unmatched u
  JOIN wb_sales_daily_share s
    ON s.event_date = u.event_date
  WHERE COALESCE(s.revenue_share, 0) > 0
),
wb_ads_still_unmatched AS (
  SELECT
    u.event_date,
    'wb'::text AS marketplace,
    '~'::text AS canonical_product_key,
    'expense'::text AS money_component_type,
    'ads'::text AS money_bucket,
    u.amount,
    'unmatched'::text AS attribution_method,
    'low'::text AS attribution_confidence,
    'stg_wb_ads'::text AS source_table,
    u.source_record_id || ':unmatched' AS source_record_id
  FROM wb_ads_unmatched u
  LEFT JOIN (
    SELECT DISTINCT split_part(source_record_id, ':alloc:', 1) AS alloc_key
    FROM wb_ads_allocated
  ) a
    ON a.alloc_key = u.source_record_id
  WHERE a.alloc_key IS NULL
),

-- =========================
-- Ozon revenue base and posting financial attribution
-- =========================
ozon_posting_item_mapped AS (
  SELECT
    TRIM(i.posting_number) AS posting_number,
    (p.created_at)::date AS event_date,
    'ozon'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    COALESCE(NULLIF(i.offer_id, '~'), bm.offer_id, '~') AS resolved_offer_id,
    COALESCE(i.quantity, 0)::numeric(18,2) AS units_sold,
    (COALESCE(i.item_price, 0) * COALESCE(i.quantity, 0))::numeric(18,2) AS revenue_gross,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN 'unmatched'
      WHEN NULLIF(i.offer_id, '~') IS NOT NULL AND bm.offer_id = i.offer_id THEN 'exact_id_match'
      WHEN NULLIF(i.product_id, '~') IS NOT NULL AND bm.product_id = i.product_id THEN 'bridge_match'
      WHEN NULLIF(i.sku, '~') IS NOT NULL AND bm.sku = i.sku THEN 'sku_match'
      ELSE 'bridge_match'
    END AS attribution_method,
    CASE
      WHEN bm.canonical_product_key IS NULL THEN 'low'
      WHEN NULLIF(i.offer_id, '~') IS NOT NULL AND bm.offer_id = i.offer_id THEN 'high'
      WHEN NULLIF(i.product_id, '~') IS NOT NULL AND bm.product_id = i.product_id THEN 'high'
      ELSE 'medium'
    END AS attribution_confidence,
    i.posting_number || ':' || COALESCE(NULLIF(i.offer_id, ''), '~') || ':' || COALESCE(NULLIF(i.product_id, ''), '~') AS source_record_id
  FROM {{schema}}.stg_posting_item i
  JOIN {{schema}}.stg_posting p
    ON TRIM(p.posting_number) = TRIM(i.posting_number)
   AND COALESCE(p.marketplace, 'ozon') = COALESCE(i.marketplace, 'ozon')
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
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
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
  WHERE COALESCE(i.marketplace, 'ozon') = 'ozon'
),
ozon_fin_components AS (
  SELECT
    f.posting_number,
    (p.created_at)::date AS event_date,
    NULLIF(TRIM(i.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.sku), '') AS sku,
    NULLIF(TRIM(f.product_id), '') AS product_id,
    'commission'::text AS money_bucket,
    ABS(COALESCE(f.commission_amount, 0))::numeric(18,2) AS amount,
    f.posting_number || ':' || COALESCE(NULLIF(f.product_id, ''), '~') || ':commission' AS source_record_id
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p
    ON TRIM(p.posting_number) = TRIM(f.posting_number)
   AND COALESCE(p.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  LEFT JOIN {{schema}}.stg_posting_item i
    ON TRIM(i.posting_number) = TRIM(f.posting_number)
   AND (
     (NULLIF(i.product_id, '~') IS NOT NULL AND i.product_id = f.product_id)
     OR (NULLIF(i.sku, '~') IS NOT NULL AND i.sku = f.sku)
   )
   AND COALESCE(i.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  WHERE COALESCE(f.marketplace, 'ozon') = 'ozon'

  UNION ALL

  SELECT
    f.posting_number,
    (p.created_at)::date AS event_date,
    NULLIF(TRIM(i.offer_id), '') AS offer_id,
    NULLIF(TRIM(f.sku), '') AS sku,
    NULLIF(TRIM(f.product_id), '') AS product_id,
    'services'::text AS money_bucket,
    ABS(COALESCE(f.item_services_total, 0))::numeric(18,2) AS amount,
    f.posting_number || ':' || COALESCE(NULLIF(f.product_id, ''), '~') || ':services' AS source_record_id
  FROM {{schema}}.stg_posting_item_financial f
  JOIN {{schema}}.stg_posting p
    ON TRIM(p.posting_number) = TRIM(f.posting_number)
   AND COALESCE(p.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  LEFT JOIN {{schema}}.stg_posting_item i
    ON TRIM(i.posting_number) = TRIM(f.posting_number)
   AND (
     (NULLIF(i.product_id, '~') IS NOT NULL AND i.product_id = f.product_id)
     OR (NULLIF(i.sku, '~') IS NOT NULL AND i.sku = f.sku)
   )
   AND COALESCE(i.marketplace, 'ozon') = COALESCE(f.marketplace, 'ozon')
  WHERE COALESCE(f.marketplace, 'ozon') = 'ozon'
),
ozon_fin_direct AS (
  SELECT
    c.event_date,
    'ozon'::text AS marketplace,
    COALESCE(bm.canonical_product_key, '~') AS canonical_product_key,
    'expense'::text AS money_component_type,
    c.money_bucket,
    c.amount,
    CASE
      WHEN bm.canonical_product_key = '~' THEN 'unmatched'
      WHEN c.offer_id IS NOT NULL AND bm.offer_id = c.offer_id THEN 'order_match'
      WHEN c.product_id IS NOT NULL AND bm.product_id = c.product_id THEN 'exact_id_match'
      WHEN c.sku IS NOT NULL AND bm.sku = c.sku THEN 'sku_match'
      ELSE 'bridge_match'
    END AS attribution_method,
    CASE
      WHEN bm.canonical_product_key = '~' THEN 'low'
      WHEN c.product_id IS NOT NULL AND bm.product_id = c.product_id THEN 'high'
      WHEN c.offer_id IS NOT NULL AND bm.offer_id = c.offer_id THEN 'high'
      ELSE 'medium'
    END AS attribution_confidence,
    'stg_posting_item_financial'::text AS source_table,
    c.source_record_id
  FROM ozon_fin_components c
  LEFT JOIN LATERAL (
    SELECT b.*
    FROM bridge b
    WHERE b.marketplace = 'ozon'
      AND (
        (c.offer_id IS NOT NULL AND c.offer_id <> '~' AND b.offer_id = c.offer_id)
        OR (c.product_id IS NOT NULL AND c.product_id <> '~' AND b.product_id = c.product_id)
        OR (c.sku IS NOT NULL AND c.sku <> '~' AND b.sku = c.sku)
      )
    ORDER BY
      CASE
        WHEN c.offer_id IS NOT NULL AND c.offer_id <> '~' AND b.offer_id = c.offer_id THEN 1
        WHEN c.product_id IS NOT NULL AND c.product_id <> '~' AND b.product_id = c.product_id THEN 2
        WHEN c.sku IS NOT NULL AND c.sku <> '~' AND b.sku = c.sku THEN 3
        ELSE 4
      END,
      b.match_confidence DESC,
      b.bridge_id
    LIMIT 1
  ) bm ON TRUE
  WHERE c.amount > 0
),

-- =========================
-- Ozon transactions: item match -> posting share -> unmatched
-- =========================
ozon_tx_components AS (
  SELECT
    t.operation_id,
    (t.operation_date)::date AS event_date,
    NULLIF(TRIM(t.posting_number), '') AS posting_number,
    'commission'::text AS money_bucket,
    ABS(COALESCE(t.sale_commission, 0))::numeric(18,2) AS amount,
    t.operation_id || ':commission' AS source_record_id
  FROM {{schema}}.stg_transaction t
  WHERE COALESCE(t.marketplace, 'ozon') = 'ozon'

  UNION ALL

  SELECT
    t.operation_id,
    (t.operation_date)::date AS event_date,
    NULLIF(TRIM(t.posting_number), '') AS posting_number,
    'logistics'::text AS money_bucket,
    ABS(COALESCE(t.delivery_charge, 0) + COALESCE(t.return_delivery_charge, 0))::numeric(18,2) AS amount,
    t.operation_id || ':logistics' AS source_record_id
  FROM {{schema}}.stg_transaction t
  WHERE COALESCE(t.marketplace, 'ozon') = 'ozon'

  UNION ALL

  SELECT
    t.operation_id,
    (t.operation_date)::date AS event_date,
    NULLIF(TRIM(t.posting_number), '') AS posting_number,
    CASE
      WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%достав%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%логист%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%delivery%'
        THEN 'logistics'
      WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%хран%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%storage%'
        THEN 'storage'
      WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%эквайр%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%acquir%'
        THEN 'payment_processing'
      WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%штраф%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%penalt%'
        THEN 'penalties'
      WHEN LOWER(COALESCE(s.service_name, '')) LIKE '%возврат%'
        OR LOWER(COALESCE(s.service_name, '')) LIKE '%refund%'
        THEN 'returns'
      ELSE 'services'
    END AS money_bucket,
    ABS(COALESCE(s.service_price, 0))::numeric(18,2) AS amount,
    t.operation_id || ':service:' || COALESCE(s.service_name, '~') AS source_record_id
  FROM {{schema}}.stg_transaction t
  JOIN {{schema}}.stg_transaction_service s
    ON s.operation_id = t.operation_id
   AND COALESCE(s.marketplace, 'ozon') = COALESCE(t.marketplace, 'ozon')
  WHERE COALESCE(t.marketplace, 'ozon') = 'ozon'
),
ozon_tx_components_nonzero AS (
  SELECT *
  FROM ozon_tx_components
  WHERE amount > 0
),
ozon_tx_item_map AS (
  SELECT
    ti.operation_id,
    b.canonical_product_key,
    COUNT(*) OVER (PARTITION BY ti.operation_id) AS product_count
  FROM {{schema}}.stg_transaction_item ti
  JOIN bridge b
    ON b.marketplace = 'ozon'
   AND NULLIF(ti.sku, '~') IS NOT NULL
   AND b.sku = ti.sku
  WHERE COALESCE(ti.marketplace, 'ozon') = 'ozon'
),
ozon_tx_alloc_item AS (
  SELECT
    c.event_date,
    'ozon'::text AS marketplace,
    m.canonical_product_key,
    'expense'::text AS money_component_type,
    c.money_bucket,
    (c.amount / NULLIF(m.product_count, 0))::numeric(18,2) AS amount,
    'sku_match'::text AS attribution_method,
    'medium'::text AS attribution_confidence,
    'stg_transaction'::text AS source_table,
    c.source_record_id || ':item:' || m.canonical_product_key AS source_record_id
  FROM ozon_tx_components_nonzero c
  JOIN ozon_tx_item_map m
    ON m.operation_id = c.operation_id
),
ozon_tx_without_item AS (
  SELECT c.*
  FROM ozon_tx_components_nonzero c
  LEFT JOIN (
    SELECT DISTINCT split_part(source_record_id, ':item:', 1) AS source_prefix
    FROM ozon_tx_alloc_item
  ) x
    ON x.source_prefix = c.source_record_id
  WHERE x.source_prefix IS NULL
),
ozon_posting_share AS (
  SELECT
    posting_number,
    event_date,
    canonical_product_key,
    revenue_gross,
    CASE
      WHEN SUM(revenue_gross) OVER (PARTITION BY posting_number, event_date) > 0
        THEN revenue_gross / SUM(revenue_gross) OVER (PARTITION BY posting_number, event_date)
      ELSE NULL
    END AS revenue_share
  FROM ozon_posting_item_mapped
  WHERE canonical_product_key <> '~'
),
ozon_tx_alloc_posting AS (
  SELECT
    c.event_date,
    'ozon'::text AS marketplace,
    p.canonical_product_key,
    'expense'::text AS money_component_type,
    c.money_bucket,
    (c.amount * COALESCE(p.revenue_share, 0))::numeric(18,2) AS amount,
    'order_match'::text AS attribution_method,
    'medium'::text AS attribution_confidence,
    'stg_transaction'::text AS source_table,
    c.source_record_id || ':posting:' || p.canonical_product_key AS source_record_id
  FROM ozon_tx_without_item c
  JOIN ozon_posting_share p
    ON p.posting_number = c.posting_number
   AND p.event_date = c.event_date
  WHERE COALESCE(p.revenue_share, 0) > 0
),
ozon_tx_still_unmatched AS (
  SELECT
    c.event_date,
    'ozon'::text AS marketplace,
    '~'::text AS canonical_product_key,
    'expense'::text AS money_component_type,
    c.money_bucket,
    c.amount,
    'unmatched'::text AS attribution_method,
    'low'::text AS attribution_confidence,
    'stg_transaction'::text AS source_table,
    c.source_record_id || ':unmatched' AS source_record_id
  FROM ozon_tx_without_item c
  LEFT JOIN (
    SELECT DISTINCT split_part(source_record_id, ':posting:', 1) AS source_prefix
    FROM ozon_tx_alloc_posting
  ) x
    ON x.source_prefix = c.source_record_id
  WHERE x.source_prefix IS NULL
),

-- =========================
-- COGS: user input -> fact_cogs -> category/global default ratio fallback
-- =========================
cogs_base AS (
  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    SUM(units_sold)::numeric(18,2) AS units_sold,
    SUM(revenue_gross)::numeric(18,2) AS revenue_gross
  FROM (
    SELECT event_date, marketplace, canonical_product_key, units_sold, revenue_gross
    FROM wb_sales_mapped

    UNION ALL

    SELECT event_date, marketplace, canonical_product_key, units_sold, revenue_gross
    FROM ozon_posting_item_mapped
  ) x
  WHERE canonical_product_key <> '~'
  GROUP BY event_date, marketplace, canonical_product_key
),
cogs_enriched AS (
  SELECT
    c.event_date,
    c.marketplace,
    c.canonical_product_key,
    b.offer_id,
    b.sku,
    b.product_id,
    b.category_name,
    c.units_sold,
    c.revenue_gross,
    pci.unit_cost_input,
    fc.unit_cost_fact,
    CASE
      WHEN LOWER(COALESCE(b.category_name, '')) LIKE '%игруш%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%toy%'
        THEN 0.25::numeric
      WHEN LOWER(COALESCE(b.category_name, '')) LIKE '%электрон%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%electron%'
        THEN 0.70::numeric
      WHEN LOWER(COALESCE(b.category_name, '')) LIKE '%одеж%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%cloth%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%apparel%'
        THEN 0.45::numeric
      WHEN LOWER(COALESCE(b.category_name, '')) LIKE '%космет%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%beauty%'
        OR LOWER(COALESCE(b.category_name, '')) LIKE '%cosmet%'
        THEN 0.35::numeric
      ELSE 0.20::numeric
    END AS fallback_ratio,
    CASE
      WHEN pci.unit_cost_input IS NOT NULL THEN 'user_input'
      WHEN fc.unit_cost_fact IS NOT NULL THEN 'user_input'
      WHEN COALESCE(b.category_name, '') <> '' THEN 'category_default'
      ELSE 'global_default'
    END AS cogs_source,
    CASE
      WHEN pci.unit_cost_input IS NOT NULL THEN 'high'
      WHEN fc.unit_cost_fact IS NOT NULL THEN 'high'
      WHEN COALESCE(b.category_name, '') <> '' THEN 'medium'
      ELSE 'low'
    END AS cogs_confidence,
    CASE
      WHEN pci.unit_cost_input IS NOT NULL THEN 'exact_id_match'
      WHEN fc.unit_cost_fact IS NOT NULL THEN 'exact_id_match'
      WHEN COALESCE(b.category_name, '') <> '' THEN 'category_default_cogs'
      ELSE 'global_default_cogs'
    END AS attribution_method
  FROM cogs_base c
  LEFT JOIN bridge b
    ON b.marketplace = c.marketplace
   AND b.canonical_product_key = c.canonical_product_key
  LEFT JOIN LATERAL (
    SELECT
      (
        COALESCE(i.cogs_purchase_amount, 0)
        + COALESCE(i.packaging_amount, 0)
        + COALESCE(i.labeling_amount, 0)
        + COALESCE(i.inbound_logistics_amount, 0)
        + COALESCE(i.assembly_amount, 0)
        + COALESCE(i.other_unit_cost_amount, 0)
      )::numeric(18,2) AS unit_cost_input
    FROM {{schema}}.product_cost_input i
    WHERE i.is_active = TRUE
      AND i.marketplace = c.marketplace
      AND i.valid_from <= c.event_date
      AND (i.valid_to IS NULL OR i.valid_to >= c.event_date)
      AND (
        (NULLIF(i.offer_id, '~') IS NOT NULL AND i.offer_id = b.offer_id)
        OR (i.sku IS NOT NULL AND b.sku IS NOT NULL AND i.sku = b.sku)
        OR (i.product_id IS NOT NULL AND b.product_id IS NOT NULL AND i.product_id = b.product_id)
      )
    ORDER BY
      CASE
        WHEN NULLIF(i.offer_id, '~') IS NOT NULL AND i.offer_id = b.offer_id THEN 1
        WHEN i.sku IS NOT NULL AND b.sku IS NOT NULL AND i.sku = b.sku THEN 2
        WHEN i.product_id IS NOT NULL AND b.product_id IS NOT NULL AND i.product_id = b.product_id THEN 3
        ELSE 4
      END,
      i.valid_from DESC,
      i.updated_at DESC,
      i.id DESC
    LIMIT 1
  ) pci ON TRUE
  LEFT JOIN LATERAL (
    SELECT
      (
        COALESCE(f.unit_cogs, 0)
        + COALESCE(f.packaging_cost, 0)
        + COALESCE(f.inbound_shipping_cost, 0)
        + COALESCE(f.other_variable_cost, 0)
      )::numeric(18,2) AS unit_cost_fact
    FROM {{schema}}.fact_cogs f
    WHERE f.marketplace = c.marketplace
      AND NULLIF(f.offer_id, '~') IS NOT NULL
      AND f.offer_id = b.offer_id
      AND (f.effective_from IS NULL OR f.effective_from <= c.event_date)
      AND (f.effective_to IS NULL OR f.effective_to >= c.event_date)
    ORDER BY f.updated_at DESC
    LIMIT 1
  ) fc ON TRUE
),
cogs_rows AS (
  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    'expense'::text AS money_component_type,
    'cogs'::text AS money_bucket,
    CASE
      WHEN unit_cost_input IS NOT NULL THEN (ABS(units_sold) * unit_cost_input)::numeric(18,2)
      WHEN unit_cost_fact IS NOT NULL THEN (ABS(units_sold) * unit_cost_fact)::numeric(18,2)
      ELSE (ABS(revenue_gross) * fallback_ratio)::numeric(18,2)
    END AS amount,
    attribution_method,
    cogs_confidence AS attribution_confidence,
    'cogs_engine'::text AS source_table,
    canonical_product_key || ':' || event_date::text || ':cogs' AS source_record_id,
    CASE
      WHEN unit_cost_input IS NOT NULL OR unit_cost_fact IS NOT NULL
        THEN CASE WHEN ABS(revenue_gross) > 0
          THEN ((ABS(units_sold) * COALESCE(unit_cost_input, unit_cost_fact)) / ABS(revenue_gross))::numeric(9,4)
          ELSE NULL
        END
      ELSE fallback_ratio::numeric(9,4)
    END AS cogs_ratio_used,
    cogs_source,
    cogs_confidence
  FROM cogs_enriched
),

-- =========================
-- Final rowset
-- =========================
final_rows AS (
  SELECT
    w.event_date,
    w.marketplace,
    w.canonical_product_key,
    'revenue'::text AS money_component_type,
    'revenue'::text AS money_bucket,
    w.revenue_gross::numeric(18,2) AS amount,
    w.attribution_method,
    w.attribution_confidence,
    'stg_wb_sale'::text AS source_table,
    w.sale_id::text AS source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_sales_mapped w

  UNION ALL

  SELECT
    o.event_date,
    o.marketplace,
    o.canonical_product_key,
    'revenue'::text AS money_component_type,
    'revenue'::text AS money_bucket,
    o.revenue_gross::numeric(18,2) AS amount,
    o.attribution_method,
    o.attribution_confidence,
    'stg_posting_item'::text AS source_table,
    o.source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM ozon_posting_item_mapped o

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_finance_direct_resolved

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_finance_storage_allocated

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_finance_allocated

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_finance_store_level

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_finance_still_unmatched

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_ads_direct_resolved

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_ads_allocated

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM wb_ads_still_unmatched

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM ozon_fin_direct

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM ozon_tx_alloc_item

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM ozon_tx_alloc_posting

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    NULL::numeric(9,4) AS cogs_ratio_used,
    NULL::text AS cogs_source,
    NULL::text AS cogs_confidence
  FROM ozon_tx_still_unmatched

  UNION ALL

  SELECT
    event_date,
    marketplace,
    canonical_product_key,
    money_component_type,
    money_bucket,
    amount,
    attribution_method,
    attribution_confidence,
    source_table,
    source_record_id,
    cogs_ratio_used,
    cogs_source,
    cogs_confidence
  FROM cogs_rows
)
SELECT
  marketplace,
  canonical_product_key,
  event_date,
  money_component_type,
  money_bucket,
  amount,
  attribution_method,
  attribution_confidence,
  source_table,
  source_record_id,
  cogs_ratio_used,
  cogs_source,
  cogs_confidence,
  now() AS loaded_at
INTO {{schema}}.fact_product_money_attribution
FROM final_rows;

CREATE INDEX fact_product_money_attr_date_idx
  ON {{schema}}.fact_product_money_attribution (event_date, marketplace);

CREATE INDEX fact_product_money_attr_offer_idx
  ON {{schema}}.fact_product_money_attribution (marketplace, canonical_product_key, money_bucket);
