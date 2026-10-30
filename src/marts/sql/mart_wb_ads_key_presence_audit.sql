-- mart_wb_ads_key_presence_audit
-- WB ads key availability split for allocated/unmatched layers.

DROP TABLE IF EXISTS {{schema}}.mart_wb_ads_key_presence_audit;

WITH params AS (
  SELECT
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
stg_daily AS (
  SELECT
    a.date,
    'stg_wb_ads'::text AS scope,
    COUNT(*)::bigint AS rows_total,
    SUM(COALESCE(a.spend, 0))::numeric(18,2) AS spend_total,
    COUNT(*) FILTER (WHERE NULLIF(a.nm_id, '~') IS NOT NULL)::bigint AS rows_with_nm_id,
    SUM(COALESCE(a.spend, 0)) FILTER (WHERE NULLIF(a.nm_id, '~') IS NOT NULL)::numeric(18,2) AS spend_with_nm_id,
    COUNT(*) FILTER (WHERE NULLIF(a.offer_id, '~') IS NOT NULL)::bigint AS rows_with_offer_id,
    SUM(COALESCE(a.spend, 0)) FILTER (WHERE NULLIF(a.offer_id, '~') IS NOT NULL)::numeric(18,2) AS spend_with_offer_id,
    0::bigint AS rows_with_vendor_code,
    0::bigint AS rows_with_supplier_article,
    COUNT(*) FILTER (
      WHERE NULLIF(a.nm_id, '~') IS NOT NULL
         OR NULLIF(a.offer_id, '~') IS NOT NULL
    )::bigint AS rows_with_any_key,
    COUNT(*) FILTER (
      WHERE NULLIF(a.nm_id, '~') IS NULL
        AND NULLIF(a.offer_id, '~') IS NULL
    )::bigint AS rows_without_keys
  FROM {{schema}}.stg_wb_ads a
  JOIN params p
    ON a.date BETWEEN p.period_start AND p.period_end
  WHERE COALESCE(a.marketplace, 'wb') = 'wb'
  GROUP BY a.date
),
unmatched_daily AS (
  SELECT
    u.event_date AS date,
    'fact_unmatched_money_ads'::text AS scope,
    COUNT(*)::bigint AS rows_total,
    SUM(COALESCE(u.amount_abs, ABS(COALESCE(u.amount, 0))))::numeric(18,2) AS spend_total,
    COUNT(*) FILTER (WHERE NULLIF(u.nm_id, '') IS NOT NULL)::bigint AS rows_with_nm_id,
    SUM(COALESCE(u.amount_abs, ABS(COALESCE(u.amount, 0)))) FILTER (WHERE NULLIF(u.nm_id, '') IS NOT NULL)::numeric(18,2) AS spend_with_nm_id,
    COUNT(*) FILTER (WHERE NULLIF(u.offer_id, '') IS NOT NULL AND u.offer_id <> '~')::bigint AS rows_with_offer_id,
    SUM(COALESCE(u.amount_abs, ABS(COALESCE(u.amount, 0)))) FILTER (WHERE NULLIF(u.offer_id, '') IS NOT NULL AND u.offer_id <> '~')::numeric(18,2) AS spend_with_offer_id,
    COUNT(*) FILTER (WHERE NULLIF(u.vendor_code, '') IS NOT NULL)::bigint AS rows_with_vendor_code,
    COUNT(*) FILTER (WHERE NULLIF(u.supplier_article, '') IS NOT NULL)::bigint AS rows_with_supplier_article,
    COUNT(*) FILTER (
      WHERE NULLIF(u.nm_id, '') IS NOT NULL
         OR (NULLIF(u.offer_id, '') IS NOT NULL AND u.offer_id <> '~')
         OR NULLIF(u.vendor_code, '') IS NOT NULL
         OR NULLIF(u.supplier_article, '') IS NOT NULL
         OR NULLIF(u.barcode, '') IS NOT NULL
    )::bigint AS rows_with_any_key,
    COUNT(*) FILTER (
      WHERE NULLIF(u.nm_id, '') IS NULL
        AND (NULLIF(u.offer_id, '') IS NULL OR u.offer_id = '~')
        AND NULLIF(u.vendor_code, '') IS NULL
        AND NULLIF(u.supplier_article, '') IS NULL
        AND NULLIF(u.barcode, '') IS NULL
    )::bigint AS rows_without_keys
  FROM {{schema}}.fact_unmatched_money u
  JOIN params p
    ON u.event_date BETWEEN p.period_start AND p.period_end
  WHERE u.marketplace = 'wb'
    AND u.money_type = 'ads'
  GROUP BY u.event_date
),
combined AS (
  SELECT * FROM stg_daily
  UNION ALL
  SELECT * FROM unmatched_daily
),
with_totals AS (
  SELECT
    date,
    scope,
    FALSE AS is_total,
    rows_total,
    spend_total,
    rows_with_nm_id,
    COALESCE(spend_with_nm_id, 0)::numeric(18,2) AS spend_with_nm_id,
    rows_with_offer_id,
    COALESCE(spend_with_offer_id, 0)::numeric(18,2) AS spend_with_offer_id,
    rows_with_vendor_code,
    rows_with_supplier_article,
    rows_with_any_key,
    rows_without_keys
  FROM combined

  UNION ALL

  SELECT
    NULL::date AS date,
    scope,
    TRUE AS is_total,
    SUM(rows_total)::bigint,
    SUM(spend_total)::numeric(18,2),
    SUM(rows_with_nm_id)::bigint,
    SUM(COALESCE(spend_with_nm_id, 0))::numeric(18,2),
    SUM(rows_with_offer_id)::bigint,
    SUM(COALESCE(spend_with_offer_id, 0))::numeric(18,2),
    SUM(rows_with_vendor_code)::bigint,
    SUM(rows_with_supplier_article)::bigint,
    SUM(rows_with_any_key)::bigint,
    SUM(rows_without_keys)::bigint
  FROM combined
  GROUP BY scope
)
SELECT
  date,
  scope,
  is_total,
  rows_total,
  spend_total,
  rows_with_nm_id,
  spend_with_nm_id,
  rows_with_offer_id,
  spend_with_offer_id,
  rows_with_vendor_code,
  rows_with_supplier_article,
  rows_with_any_key,
  rows_without_keys,
  CASE
    WHEN rows_total = 0 THEN 0::numeric(9,6)
    ELSE (rows_with_any_key::numeric / rows_total::numeric)
  END::numeric(9,6) AS key_presence_ratio,
  CASE
    WHEN rows_total = 0 THEN 'no_data'
    WHEN rows_without_keys > 0 THEN 'warning'
    ELSE 'ok'
  END AS status,
  now() AS created_at
INTO {{schema}}.mart_wb_ads_key_presence_audit
FROM with_totals;

CREATE INDEX mart_wb_ads_key_presence_audit_idx
  ON {{schema}}.mart_wb_ads_key_presence_audit (scope, is_total, date);
