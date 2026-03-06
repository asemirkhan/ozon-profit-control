-- mart_leakage_summary_30d
-- 1 строка = итог по магазину за период
-- + показывает неразнесённые транзакции (без posting_number)

DROP TABLE IF EXISTS {{schema}}.mart_leakage_summary_30d;

WITH unallocated AS (
  SELECT
    COALESCE(SUM(COALESCE(amount,0)), 0) AS unallocated_transactions_amount
  FROM {{schema}}.stg_transaction
  WHERE (posting_number IS NULL OR TRIM(posting_number) = '')
    AND (operation_date)::date BETWEEN '{{period_start}}'::date AND '{{period_end}}'::date
)
SELECT
  '{{period_start}}'::date AS period_start,
  '{{period_end}}'::date AS period_end,
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
  CASE WHEN COALESCE(SUM(t.revenue_gross),0) > 0 THEN COALESCE(SUM(t.ads_spend),0) / SUM(t.revenue_gross) ELSE NULL END AS ads_share,
  (SELECT unallocated_transactions_amount FROM unallocated) AS unallocated_transactions_amount
INTO {{schema}}.mart_leakage_summary_30d
FROM {{schema}}.mart_pnl_30d_by_product t
WHERE t.period_start = '{{period_start}}'::date
  AND t.period_end   = '{{period_end}}'::date;