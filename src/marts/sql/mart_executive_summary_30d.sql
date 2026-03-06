-- mart_executive_summary_30d
-- 1 строка = сводка по магазину за период + top-5 прибыльных/убыточных

DROP TABLE IF EXISTS {{schema}}.mart_executive_summary_30d;

WITH leak AS (
  SELECT *
  FROM {{schema}}.mart_leakage_summary_30d
  WHERE period_start = '{{period_start}}'::date
    AND period_end   = '{{period_end}}'::date
),
top_profit AS (
  SELECT offer_id, profit_after_ads
  FROM {{schema}}.mart_pnl_30d_by_product
  WHERE period_start = '{{period_start}}'::date
    AND period_end   = '{{period_end}}'::date
  ORDER BY profit_after_ads DESC NULLS LAST
  LIMIT 5
),
top_loss AS (
  SELECT offer_id, profit_after_ads
  FROM {{schema}}.mart_pnl_30d_by_product
  WHERE period_start = '{{period_start}}'::date
    AND period_end   = '{{period_end}}'::date
  ORDER BY profit_after_ads ASC NULLS LAST
  LIMIT 5
)
SELECT
  '{{period_start}}'::date AS period_start,
  '{{period_end}}'::date AS period_end,
  COALESCE(leak.revenue_gross_total, 0) AS revenue_gross_total,
  COALESCE(leak.payout_total, 0) AS payout_total,
  COALESCE(leak.profit_before_ads_total, 0) AS profit_before_ads_total,
  COALESCE(leak.ads_total, 0) AS ads_total,
  COALESCE(leak.profit_after_ads_total, 0) AS profit_after_ads_total,
  leak.margin_after_ads_total,
  leak.commission_share,
  leak.logistics_share,
  leak.services_share,
  leak.ads_share,
  (SELECT jsonb_agg(jsonb_build_object('offer_id', offer_id, 'profit_after_ads', profit_after_ads)) FROM top_profit) AS top_profit_products,
  (SELECT jsonb_agg(jsonb_build_object('offer_id', offer_id, 'profit_after_ads', profit_after_ads)) FROM top_loss) AS top_loss_products
INTO {{schema}}.mart_executive_summary_30d
FROM leak
RIGHT JOIN (SELECT 1 AS one) dummy ON true;