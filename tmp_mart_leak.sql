-- mart_leakage_summary_30d
-- 1 строка = итог по магазину за период

DROP TABLE IF EXISTS client_demo.mart_leakage_summary_30d;

SELECT
  '2026-02-01'::date AS period_start,
  '2026-03-01'::date AS period_end,
  COALESCE(SUM(t.revenue_gross), 0) AS revenue_gross_total,
  COALESCE(SUM(t.payout), 0) AS payout_total,
  COALESCE(SUM(t.commission_amount), 0) AS commission_total,
  COALESCE(SUM(t.logistics_amount), 0) AS logistics_total,
  COALESCE(SUM(t.services_amount), 0) AS services_total,
  COALESCE(SUM(t.returns_amount), 0) AS returns_total,
  COALESCE(SUM(t.cogs_amount), 0) AS cogs_total,
  COALESCE(SUM(t.ads_spend), 0) AS ads_total,
  COALESCE(SUM(t.profit_before_ads), 0) AS profit_before_ads_total,
  COALESCE(SUM(t.profit_after_ads), 0) AS profit_after_ads_total,
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.profit_after_ads),0) / SUM(t.revenue_gross) ELSE NULL END AS margin_after_ads_total,
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.commission_amount),0) / SUM(t.revenue_gross) ELSE NULL END AS commission_share,
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.logistics_amount),0) / SUM(t.revenue_gross) ELSE NULL END AS logistics_share,
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.services_amount),0) / SUM(t.revenue_gross) ELSE NULL END AS services_share,
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.ads_spend),0) / SUM(t.revenue_gross) ELSE NULL END AS ads_share
INTO client_demo.mart_leakage_summary_30d
FROM client_demo.mart_pnl_30d_by_product t
WHERE t.period_start = '2026-02-01'::date
  AND t.period_end   = '2026-03-01'::date;