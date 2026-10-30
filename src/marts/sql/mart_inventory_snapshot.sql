-- mart_inventory_snapshot
-- 1 строка = 1 товар × marketplace × последний снимок остатков

DROP TABLE IF EXISTS {{schema}}.mart_inventory_snapshot;

WITH params AS (
  SELECT
    '{{period_end}}'::date AS period_end,
    ('{{period_end}}'::date - INTERVAL '1 day')::date AS anchor_date
),
replenishment_latest AS (
  SELECT
    ranked.marketplace,
    ranked.offer_id,
    ranked.expected_replenishment_date,
    ranked.planned_replenishment_qty,
    ranked.replenishment_status
  FROM (
    SELECT
      r.marketplace,
      r.offer_id,
      r.expected_replenishment_date,
      r.planned_replenishment_qty,
      r.replenishment_status,
      ROW_NUMBER() OVER (
        PARTITION BY r.marketplace, r.offer_id
        ORDER BY r.updated_at DESC, r.id DESC
      ) AS rn
    FROM {{schema}}.product_replenishment_input r
    WHERE r.is_active = TRUE
  ) ranked
  WHERE ranked.rn = 1
),
latest_snapshot AS (
  SELECT
    COALESCE(i.marketplace, 'ozon') AS marketplace,
    MAX(i.snapshot_date) AS snapshot_date
  FROM {{schema}}.fact_inventory_snapshot i
  GROUP BY 1
),
inventory_latest AS (
  SELECT i.*
  FROM {{schema}}.fact_inventory_snapshot i
  JOIN latest_snapshot ls
    ON ls.marketplace = COALESCE(i.marketplace, 'ozon')
   AND ls.snapshot_date = i.snapshot_date
),
inventory_resolved AS (
  SELECT
    i.snapshot_date,
    COALESCE(i.marketplace, 'ozon') AS marketplace,
    i.offer_id AS raw_offer_id,
    i.sku AS raw_sku,
    i.product_id AS raw_product_id,
    i.stock_total,
    i.stock_reserved,
    i.stock_available,
    i.stock_defect,
    dp_match.offer_id AS dim_offer_id,
    dp_match.sku AS dim_sku,
    dp_match.product_id AS dim_product_id,
    CASE
      WHEN dp_match.offer_id IS NOT NULL AND dp_match.offer_id = NULLIF(i.offer_id, '~') THEN 'exact_match'
      WHEN dp_match.sku IS NOT NULL AND dp_match.sku = NULLIF(i.sku, '~') THEN 'sku_match'
      WHEN dp_match.product_id IS NOT NULL AND dp_match.product_id = NULLIF(i.product_id, '~') THEN 'sku_match'
      WHEN dp_match.offer_id IS NOT NULL THEN 'name_match'
      ELSE 'unmatched'
    END AS inventory_match_quality
  FROM inventory_latest i
  LEFT JOIN LATERAL (
    SELECT
      dp.offer_id,
      dp.sku,
      dp.product_id,
      CASE
        WHEN dp.offer_id = NULLIF(i.offer_id, '~') THEN 1
        WHEN dp.sku = NULLIF(i.sku, '~') THEN 2
        WHEN dp.product_id = NULLIF(i.product_id, '~') THEN 3
        WHEN LOWER(COALESCE(dp.product_name, '')) = LOWER(COALESCE(NULLIF(i.offer_id, '~'), '')) THEN 4
        ELSE 99
      END AS match_rank
    FROM {{schema}}.dim_product dp
    WHERE COALESCE(dp.marketplace, 'ozon') = COALESCE(i.marketplace, 'ozon')
      AND (
        (dp.offer_id IS NOT NULL AND dp.offer_id = NULLIF(i.offer_id, '~'))
        OR (dp.sku IS NOT NULL AND dp.sku = NULLIF(i.sku, '~'))
        OR (dp.product_id IS NOT NULL AND dp.product_id = NULLIF(i.product_id, '~'))
        OR LOWER(COALESCE(dp.product_name, '')) = LOWER(COALESCE(NULLIF(i.offer_id, '~'), ''))
      )
    ORDER BY match_rank
    LIMIT 1
  ) dp_match
    ON TRUE
),
inventory_agg AS (
  SELECT
    i.snapshot_date,
    i.marketplace,
    COALESCE(NULLIF(i.raw_offer_id, '~'), i.dim_offer_id, '~') AS offer_id,
    COALESCE(NULLIF(i.raw_sku, '~'), i.dim_sku, '~') AS sku,
    COALESCE(NULLIF(i.raw_product_id, '~'), i.dim_product_id, '~') AS product_id,
    i.inventory_match_quality,
    SUM(COALESCE(i.stock_total, 0)) AS stock_total,
    SUM(COALESCE(i.stock_reserved, 0)) AS stock_reserved,
    SUM(
      COALESCE(
        i.stock_available,
        COALESCE(i.stock_total, 0) - COALESCE(i.stock_reserved, 0)
      )
    ) AS stock_available,
    SUM(COALESCE(i.stock_defect, 0)) AS stock_defect
  FROM inventory_resolved i
  GROUP BY 1,2,3,4,5,6
),
sales_7d AS (
  SELECT
    d.marketplace,
    d.offer_id,
    (SUM(COALESCE(d.units_sold, 0)) / 7.0) AS avg_daily_units_sold_7d
  FROM {{schema}}.mart_pnl_daily_by_product d
  CROSS JOIN params p
  WHERE d.date >= (p.period_end - INTERVAL '7 day')
    AND d.date < p.period_end
  GROUP BY 1,2
),
sales_30d AS (
  SELECT
    d.marketplace,
    d.offer_id,
    (SUM(COALESCE(d.units_sold, 0)) / 30.0) AS avg_daily_units_sold_30d,
    (SUM(COALESCE(d.revenue_gross, 0)) / 30.0) AS avg_daily_revenue_30d,
    (SUM(COALESCE(d.profit_after_ads, 0)) / 30.0) AS avg_daily_profit_after_ads_30d
  FROM {{schema}}.mart_pnl_daily_by_product d
  CROSS JOIN params p
  WHERE d.date >= (p.period_end - INTERVAL '30 day')
    AND d.date < p.period_end
  GROUP BY 1,2
),
base AS (
  SELECT
    i.snapshot_date,
    i.marketplace,
    i.offer_id,
    i.sku,
    i.product_id,
    i.inventory_match_quality,
    COALESCE(dp.product_name, '~') AS product_name,
    i.stock_total,
    i.stock_available,
    i.stock_reserved,
    i.stock_defect,
    s7.avg_daily_units_sold_7d,
    s30.avg_daily_units_sold_30d,
    s30.avg_daily_revenue_30d,
    s30.avg_daily_profit_after_ads_30d,
    CASE
      WHEN COALESCE(s7.avg_daily_units_sold_7d, 0) > 0
        THEN i.stock_available / s7.avg_daily_units_sold_7d
      ELSE NULL
    END AS days_of_stock_7d,
    CASE
      WHEN COALESCE(s30.avg_daily_units_sold_30d, 0) > 0
        THEN i.stock_available / s30.avg_daily_units_sold_30d
      ELSE NULL
    END AS days_of_stock_30d,
    rp.expected_replenishment_date,
    rp.planned_replenishment_qty,
    COALESCE(rp.replenishment_status, 'unknown') AS replenishment_status
  FROM inventory_agg i
  LEFT JOIN {{schema}}.dim_product dp
    ON COALESCE(dp.marketplace, 'ozon') = i.marketplace
   AND dp.offer_id = i.offer_id
  LEFT JOIN sales_7d s7
    ON s7.marketplace = i.marketplace
   AND s7.offer_id = i.offer_id
  LEFT JOIN sales_30d s30
    ON s30.marketplace = i.marketplace
   AND s30.offer_id = i.offer_id
  LEFT JOIN replenishment_latest rp
    ON rp.marketplace = i.marketplace
   AND rp.offer_id = i.offer_id
)
SELECT
  b.snapshot_date,
  b.marketplace,
  b.offer_id,
  b.sku,
  b.product_id,
  b.product_name,
  b.stock_total,
  b.stock_available,
  b.stock_reserved,
  b.stock_defect,
  b.inventory_match_quality,
  b.avg_daily_units_sold_7d,
  b.avg_daily_units_sold_30d,
  b.avg_daily_revenue_30d,
  b.avg_daily_profit_after_ads_30d,
  b.avg_daily_revenue_30d AS daily_revenue_run_rate,
  b.avg_daily_profit_after_ads_30d AS daily_profit_run_rate,
  b.days_of_stock_7d,
  b.days_of_stock_30d,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN 'no_sales'
    WHEN COALESCE(b.stock_available, 0) <= 0 THEN 'out_of_stock'
    WHEN b.days_of_stock_30d <= 3 THEN 'high'
    WHEN b.days_of_stock_30d <= 7 THEN 'medium'
    WHEN b.days_of_stock_30d <= 14 THEN 'low'
    ELSE 'ok'
  END AS out_of_stock_risk,
  b.expected_replenishment_date,
  b.planned_replenishment_qty,
  b.replenishment_status,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN CAST(0 AS NUMERIC(18,2))
    ELSE
      COALESCE(b.avg_daily_revenue_30d, 0)
      * CASE
          WHEN b.replenishment_status IN ('planned', 'in_transit')
               AND b.expected_replenishment_date IS NOT NULL
               AND b.days_of_stock_30d IS NOT NULL
               AND b.expected_replenishment_date <= (b.snapshot_date + (b.days_of_stock_30d || ' day')::interval)
            THEN 0.25
          ELSE 1
        END
  END AS revenue_at_risk_per_day,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN CAST(0 AS NUMERIC(18,2))
    ELSE
      GREATEST(3 - GREATEST(COALESCE(b.days_of_stock_30d, 0), 0), 0)
      * COALESCE(b.avg_daily_revenue_30d, 0)
  END AS revenue_at_risk_3d,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN CAST(0 AS NUMERIC(18,2))
    ELSE
      GREATEST(7 - GREATEST(COALESCE(b.days_of_stock_30d, 0), 0), 0)
      * COALESCE(b.avg_daily_revenue_30d, 0)
  END AS revenue_at_risk_7d,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN CAST(0 AS NUMERIC(18,2))
    ELSE
      GREATEST(14 - GREATEST(COALESCE(b.days_of_stock_30d, 0), 0), 0)
      * COALESCE(b.avg_daily_revenue_30d, 0)
  END AS revenue_at_risk_14d,
  CASE
    WHEN COALESCE(b.avg_daily_profit_after_ads_30d, 0) > 0 THEN b.avg_daily_profit_after_ads_30d
    ELSE NULL
  END AS profit_at_risk_per_day,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN CAST(0 AS NUMERIC(18,2))
    ELSE GREATEST(30 - GREATEST(COALESCE(b.days_of_stock_30d, 0), 0), 0) * COALESCE(b.avg_daily_revenue_30d, 0)
  END AS potential_revenue_loss_30d,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) <= 0 THEN 'low'
    WHEN b.inventory_match_quality = 'exact_match' THEN 'high'
    WHEN b.inventory_match_quality IN ('sku_match', 'name_match') THEN 'medium'
    ELSE 'low'
  END AS stockout_effect_confidence,
  CASE
    WHEN COALESCE(b.avg_daily_units_sold_30d, 0) > 0 AND b.days_of_stock_30d < 10 THEN TRUE
    ELSE FALSE
  END AS reorder_flag
INTO {{schema}}.mart_inventory_snapshot
FROM base b;

CREATE INDEX mart_inventory_snapshot_offer_idx
  ON {{schema}}.mart_inventory_snapshot (marketplace, offer_id);

CREATE INDEX mart_inventory_snapshot_risk_idx
  ON {{schema}}.mart_inventory_snapshot (marketplace, out_of_stock_risk);
