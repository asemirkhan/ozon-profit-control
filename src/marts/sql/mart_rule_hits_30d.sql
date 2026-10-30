-- mart_rule_hits_30d
-- 1 строка = 1 найденная проблема × 1 товар × marketplace × 1 дата расчета

DROP TABLE IF EXISTS {{schema}}.mart_rule_hits_30d;

WITH params AS (
  SELECT
    CURRENT_DATE AS run_date,
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
pnl_base AS (
  SELECT
    p.marketplace,
    p.offer_id,
    NULLIF(TRIM(p.product_name), '') AS product_name,
    p.units_sold,
    p.revenue_gross,
    p.logistics_amount,
    p.ads_spend,
    p.profit_before_ads,
    p.profit_after_ads,
    p.margin_after_ads
  FROM {{schema}}.mart_pnl_30d_by_product p
),
pnl_clean AS (
  SELECT *
  FROM pnl_base
  WHERE COALESCE(offer_id, '~') <> '~'
    AND COALESCE(product_name, '~') <> '~'
),
inv_base AS (
  SELECT
    i.marketplace,
    i.offer_id,
    NULLIF(TRIM(i.product_name), '') AS product_name,
    i.days_of_stock_30d,
    i.out_of_stock_risk,
    i.potential_revenue_loss_30d
  FROM {{schema}}.mart_inventory_snapshot i
),
inv_clean AS (
  SELECT *
  FROM inv_base
  WHERE COALESCE(offer_id, '~') <> '~'
    AND COALESCE(product_name, '~') <> '~'
),
rule_negative_profit_after_ads AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    p.marketplace,
    'negative_profit_after_ads'::text AS rule_code,
    'high'::text AS severity,
    p.offer_id,
    p.product_name,
    p.profit_after_ads AS metric_value,
    CAST(0 AS NUMERIC) AS threshold_value,
    'Товар убыточен после рекламы'::text AS problem_title,
    (
      'Товар "' || p.product_name
      || '" имеет profit_after_ads=' || ROUND(COALESCE(p.profit_after_ads, 0), 2)::text
      || ' при ads_spend=' || ROUND(COALESCE(p.ads_spend, 0), 2)::text || '.'
    ) AS problem_description,
    'Разобрать расходы по рекламе и проверить, можно ли отключить убыточные кампании или поднять маржу по товару.'::text AS recommended_action,
    'Если сократить убыточные расходы или поднять маржу, товар может выйти из минуса.'::text AS expected_effect_note,
    ABS(p.profit_after_ads) AS money_impact_30d,
    CAST(NULL AS NUMERIC) AS revenue_at_risk_30d,
    'mart_pnl_30d_by_product'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN pnl_clean p ON TRUE
  WHERE p.profit_after_ads < 0
),
rule_ads_eating_profit AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    p.marketplace,
    'ads_eating_profit'::text AS rule_code,
    'high'::text AS severity,
    p.offer_id,
    p.product_name,
    CASE
      WHEN p.profit_before_ads > 0
        THEN p.profit_after_ads / p.profit_before_ads
      ELSE NULL
    END AS metric_value,
    CAST(0.5 AS NUMERIC) AS threshold_value,
    'Реклама съедает прибыль'::text AS problem_title,
    (
      'Товар "' || p.product_name
      || '": profit_before_ads=' || ROUND(COALESCE(p.profit_before_ads, 0), 2)::text
      || ', profit_after_ads=' || ROUND(COALESCE(p.profit_after_ads, 0), 2)::text
      || ', ads_spend=' || ROUND(COALESCE(p.ads_spend, 0), 2)::text || '.'
    ) AS problem_description,
    'Проверить кампании по этому товару и сократить те, где расход не окупается прибылью.'::text AS recommended_action,
    'Снижение неэффективной рекламы должно вернуть товару большую долю прибыли.'::text AS expected_effect_note,
    p.ads_spend AS money_impact_30d,
    CAST(NULL AS NUMERIC) AS revenue_at_risk_30d,
    'mart_pnl_30d_by_product'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN pnl_clean p ON TRUE
  WHERE p.ads_spend > 0
    AND p.profit_before_ads > 0
    AND p.profit_after_ads < p.profit_before_ads * 0.5
),
rule_high_logistics_share AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    p.marketplace,
    'high_logistics_share'::text AS rule_code,
    'medium'::text AS severity,
    p.offer_id,
    p.product_name,
    (p.logistics_amount / p.revenue_gross) AS metric_value,
    CAST(0.20 AS NUMERIC) AS threshold_value,
    'Слишком высокая доля доставки'::text AS problem_title,
    (
      'Товар "' || p.product_name
      || '": доля логистики=' || ROUND((p.logistics_amount / p.revenue_gross) * 100, 2)::text
      || '% от выручки.'
    ) AS problem_description,
    'Проверить габариты, упаковку и экономику товара: доставка занимает слишком большую долю выручки.'::text AS recommended_action,
    'Снижение доли логистики обычно улучшает итоговую прибыль на единицу товара.'::text AS expected_effect_note,
    CASE
      WHEN (p.logistics_amount - p.revenue_gross * 0.20) > 0
        THEN p.logistics_amount - p.revenue_gross * 0.20
      ELSE NULL
    END AS money_impact_30d,
    CAST(NULL AS NUMERIC) AS revenue_at_risk_30d,
    'mart_pnl_30d_by_product'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN pnl_clean p ON TRUE
  WHERE p.revenue_gross > 0
    AND (p.logistics_amount / p.revenue_gross) > 0.20
),
rule_low_margin_after_ads AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    p.marketplace,
    'low_margin_after_ads'::text AS rule_code,
    'medium'::text AS severity,
    p.offer_id,
    p.product_name,
    p.margin_after_ads AS metric_value,
    CAST(0.10 AS NUMERIC) AS threshold_value,
    'Маржа после рекламы слишком низкая'::text AS problem_title,
    (
      'Товар "' || p.product_name
      || '" имеет margin_after_ads=' || ROUND(COALESCE(p.margin_after_ads, 0) * 100, 2)::text || '%.'
    ) AS problem_description,
    'Проверить фактическую цену, скидку и расходы площадки: товар почти не оставляет результата.'::text AS recommended_action,
    'Даже небольшое улучшение маржи по такому товару заметно влияет на итоговый результат.'::text AS expected_effect_note,
    CASE
      WHEN (p.revenue_gross * 0.10 - p.profit_after_ads) > 0
        THEN (p.revenue_gross * 0.10 - p.profit_after_ads)
      ELSE NULL
    END AS money_impact_30d,
    CAST(NULL AS NUMERIC) AS revenue_at_risk_30d,
    'mart_pnl_30d_by_product'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN pnl_clean p ON TRUE
  WHERE p.revenue_gross > 0
    AND p.profit_after_ads > 0
    AND p.margin_after_ads < 0.10
),
rule_stockout_risk AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    i.marketplace,
    'stockout_risk'::text AS rule_code,
    CASE
      WHEN i.out_of_stock_risk IN ('out_of_stock', 'critical', 'high') THEN 'high'
      WHEN i.out_of_stock_risk IN ('warning', 'medium') THEN 'medium'
      ELSE 'low'
    END AS severity,
    i.offer_id,
    i.product_name,
    i.days_of_stock_30d AS metric_value,
    CAST(14 AS NUMERIC) AS threshold_value,
    'Товар может закончиться'::text AS problem_title,
    (
      CASE
        WHEN i.days_of_stock_30d IS NULL
          THEN 'Статус остатка=' || COALESCE(i.out_of_stock_risk, 'unknown')
               || ': товар в зоне риска, но days_of_stock_30d не рассчитан.'
        WHEN i.days_of_stock_30d <= 0
          THEN 'Статус остатка=' || COALESCE(i.out_of_stock_risk, 'unknown')
               || ': товар уже находится в зоне дефицита.'
        WHEN i.days_of_stock_30d < 3
          THEN 'Статус остатка=' || COALESCE(i.out_of_stock_risk, 'unknown')
               || ': остаток практически закончился (около '
               || ROUND(i.days_of_stock_30d, 1)::text || ' дн.).'
        ELSE 'Статус остатка=' || COALESCE(i.out_of_stock_risk, 'unknown')
             || ', прогноз days_of_stock_30d=' || ROUND(i.days_of_stock_30d, 1)::text || ' дн.'
      END
    ) AS problem_description,
    'Проверить остаток и вовремя пополнить товар, чтобы не потерять продажи.'::text AS recommended_action,
    CASE
      WHEN COALESCE(i.potential_revenue_loss_30d, 0) > 0
        THEN 'Потенциальная потеря выручки за 30 дней: '
             || ROUND(i.potential_revenue_loss_30d, 2)::text || '.'
      ELSE 'Своевременное пополнение снижает риск потери продаж.'
    END AS expected_effect_note,
    CAST(NULL AS NUMERIC) AS money_impact_30d,
    i.potential_revenue_loss_30d AS revenue_at_risk_30d,
    'mart_inventory_snapshot'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN inv_clean i ON TRUE
  WHERE COALESCE(i.out_of_stock_risk, 'ok') NOT IN ('ok', 'no_sales')
),
rule_ads_without_sales AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    p.marketplace,
    'ads_without_sales'::text AS rule_code,
    'high'::text AS severity,
    p.offer_id,
    p.product_name,
    p.ads_spend AS metric_value,
    CAST(0 AS NUMERIC) AS threshold_value,
    'Есть рекламные расходы без продаж'::text AS problem_title,
    (
      'По товару "' || p.product_name
      || '" есть ads_spend=' || ROUND(COALESCE(p.ads_spend, 0), 2)::text
      || ' при units_sold=' || COALESCE(p.units_sold, 0)::text
      || ' и revenue_gross=' || ROUND(COALESCE(p.revenue_gross, 0), 2)::text || '.'
    ) AS problem_description,
    'Проверить, зачем на товар тратится реклама, если продаж за период нет.'::text AS recommended_action,
    'Отключение неэффективного трафика сокращает прямые потери бюджета.'::text AS expected_effect_note,
    p.ads_spend AS money_impact_30d,
    CAST(NULL AS NUMERIC) AS revenue_at_risk_30d,
    'mart_pnl_30d_by_product'::text AS source_mart,
    'v1'::text AS rule_version
  FROM params prm
  JOIN pnl_clean p ON TRUE
  WHERE p.ads_spend > 0
    AND (COALESCE(p.units_sold, 0) = 0 OR COALESCE(p.revenue_gross, 0) = 0)
),
hits_raw AS (
  SELECT * FROM rule_negative_profit_after_ads
  UNION ALL
  SELECT * FROM rule_ads_eating_profit
  UNION ALL
  SELECT * FROM rule_high_logistics_share
  UNION ALL
  SELECT * FROM rule_low_margin_after_ads
  UNION ALL
  SELECT * FROM rule_stockout_risk
  UNION ALL
  SELECT * FROM rule_ads_without_sales
),
hits_scored AS (
  SELECT
    h.*,
    (
      CASE h.severity
        WHEN 'high' THEN 70
        WHEN 'medium' THEN 40
        WHEN 'low' THEN 20
        ELSE 0
      END
      + LEAST(30::numeric, ABS(COALESCE(h.money_impact_30d, h.revenue_at_risk_30d, 0)) / 1000.0 * 5)
    ) AS priority_score
  FROM hits_raw h
)
SELECT
  run_date,
  period_start,
  period_end,
  marketplace,
  rule_code,
  severity,
  offer_id,
  product_name,
  metric_value,
  threshold_value,
  problem_title,
  problem_description,
  recommended_action,
  expected_effect_note,
  money_impact_30d,
  revenue_at_risk_30d,
  priority_score,
  rule_version,
  source_mart,
  now() AS loaded_at
INTO {{schema}}.mart_rule_hits_30d
FROM hits_scored;

CREATE INDEX mart_rule_hits_30d_rule_idx
  ON {{schema}}.mart_rule_hits_30d (marketplace, rule_code);

CREATE INDEX mart_rule_hits_30d_offer_idx
  ON {{schema}}.mart_rule_hits_30d (marketplace, offer_id);

CREATE INDEX mart_rule_hits_30d_severity_idx
  ON {{schema}}.mart_rule_hits_30d (marketplace, severity);

CREATE INDEX mart_rule_hits_30d_priority_idx
  ON {{schema}}.mart_rule_hits_30d (marketplace, priority_score DESC);
