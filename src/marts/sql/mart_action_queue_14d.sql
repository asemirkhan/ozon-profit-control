-- mart_action_queue_14d
-- 1 строка = 1 приоритетное действие по товару на текущий расчет и marketplace

DROP TABLE IF EXISTS {{schema}}.mart_action_queue_14d;

WITH hits AS (
  SELECT
    run_date,
    period_start,
    period_end,
    marketplace,
    rule_code,
    severity,
    offer_id,
    product_name,
    recommended_action,
    expected_effect_note,
    money_impact_30d,
    revenue_at_risk_30d,
    priority_score
  FROM {{schema}}.mart_rule_hits_30d
),
ranked_hits AS (
  SELECT
    h.*,
    CASE h.rule_code
      WHEN 'stockout_risk' THEN 1
      WHEN 'negative_profit_after_ads' THEN 2
      WHEN 'ads_without_sales' THEN 3
      WHEN 'ads_eating_profit' THEN 4
      WHEN 'high_logistics_share' THEN 5
      WHEN 'low_margin_after_ads' THEN 6
      ELSE 99
    END AS rule_rank,
    ROW_NUMBER() OVER (
      PARTITION BY h.run_date, h.period_start, h.period_end, h.marketplace, h.offer_id
      ORDER BY h.priority_score DESC NULLS LAST,
               CASE h.rule_code
                 WHEN 'stockout_risk' THEN 1
                 WHEN 'negative_profit_after_ads' THEN 2
                 WHEN 'ads_without_sales' THEN 3
                 WHEN 'ads_eating_profit' THEN 4
                 WHEN 'high_logistics_share' THEN 5
                 WHEN 'low_margin_after_ads' THEN 6
                 ELSE 99
               END,
               h.rule_code
    ) AS rn
  FROM hits h
),
primary_hit AS (
  SELECT *
  FROM ranked_hits
  WHERE rn = 1
),
supporting_agg AS (
  SELECT
    h.run_date,
    h.period_start,
    h.period_end,
    h.marketplace,
    h.offer_id,
    MAX(h.product_name) AS product_name,
    COUNT(*)::int AS supporting_problem_count,
    MAX(h.money_impact_30d) AS money_impact_30d,
    MAX(h.revenue_at_risk_30d) AS revenue_at_risk_30d
  FROM hits h
  GROUP BY 1,2,3,4,5
),
supporting_codes AS (
  SELECT
    x.run_date,
    x.period_start,
    x.period_end,
    x.marketplace,
    x.offer_id,
    STRING_AGG(x.rule_code, ', ' ORDER BY x.rule_rank, x.rule_code) AS supporting_rule_codes
  FROM (
    SELECT DISTINCT
      h.run_date,
      h.period_start,
      h.period_end,
      h.marketplace,
      h.offer_id,
      h.rule_code,
      CASE h.rule_code
        WHEN 'stockout_risk' THEN 1
        WHEN 'negative_profit_after_ads' THEN 2
        WHEN 'ads_without_sales' THEN 3
        WHEN 'ads_eating_profit' THEN 4
        WHEN 'high_logistics_share' THEN 5
        WHEN 'low_margin_after_ads' THEN 6
        ELSE 99
      END AS rule_rank
    FROM hits h
  ) x
  GROUP BY 1,2,3,4,5
),
store_quality AS (
  SELECT
    s.period_start,
    s.period_end,
    s.marketplace,
    COALESCE(s.attribution_quality, 'exact_match') AS attribution_quality,
    COALESCE(s.confidence, 'high') AS confidence
  FROM {{schema}}.mart_store_summary_30d s
),
inventory_context AS (
  SELECT
    i.marketplace,
    i.offer_id,
    i.days_of_stock_30d,
    i.daily_revenue_run_rate,
    i.daily_profit_run_rate,
    i.revenue_at_risk_per_day,
    i.revenue_at_risk_3d,
    i.revenue_at_risk_7d,
    i.revenue_at_risk_14d,
    i.profit_at_risk_per_day,
    i.expected_replenishment_date,
    i.planned_replenishment_qty,
    i.replenishment_status,
    i.stockout_effect_confidence
  FROM {{schema}}.mart_inventory_snapshot i
),
action_base AS (
  SELECT
    p.run_date,
    p.period_start,
    p.period_end,
    p.marketplace,
    p.offer_id,
    COALESCE(s.product_name, p.product_name) AS product_name,
    p.rule_code AS primary_rule_code,
    p.rule_rank,
    p.severity,
    p.priority_score,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads' THEN 'fix_unprofitable_ads'
      WHEN 'ads_eating_profit' THEN 'optimize_ads_profitability'
      WHEN 'high_logistics_share' THEN 'reduce_logistics_burden'
      WHEN 'low_margin_after_ads' THEN 'improve_margin'
      WHEN 'stockout_risk' THEN 'replenish_stock'
      WHEN 'ads_without_sales' THEN 'stop_wasteful_ads'
      ELSE 'fix_unprofitable_ads'
    END AS action_code,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads' THEN 'ads'
      WHEN 'ads_eating_profit' THEN 'ads'
      WHEN 'ads_without_sales' THEN 'ads'
      WHEN 'stockout_risk' THEN 'inventory'
      WHEN 'high_logistics_share' THEN 'logistics'
      WHEN 'low_margin_after_ads' THEN 'margin'
      ELSE 'other'
    END AS action_type,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads' THEN 'Остановить убыточную рекламу и проверить маржу'
      WHEN 'ads_eating_profit' THEN 'Снизить рекламу, которая съедает прибыль'
      WHEN 'high_logistics_share' THEN 'Проверить товар с слишком дорогой логистикой'
      WHEN 'low_margin_after_ads' THEN 'Поднять запас маржи по товару'
      WHEN 'stockout_risk' THEN 'Срочно пополнить остаток'
      WHEN 'ads_without_sales' THEN 'Остановить рекламу без продаж'
      ELSE 'Проверить приоритетную проблему'
    END AS action_title,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads'
        THEN 'Товар убыточен после рекламы за последние 30 дней. В первую очередь проверьте рекламные кампании и маржу по SKU.'
      WHEN 'ads_eating_profit'
        THEN 'Реклама съедает большую часть прибыли. Сфокусируйтесь на кампаниях с низкой окупаемостью.'
      WHEN 'high_logistics_share'
        THEN 'Логистика занимает слишком большую долю выручки. Проверьте упаковку, габариты и модель поставки.'
      WHEN 'low_margin_after_ads'
        THEN 'Товар остается прибыльным, но маржа после рекламы слишком низкая. Нужно поднять запас маржи.'
      WHEN 'stockout_risk'
        THEN 'Остаток по товару в зоне риска. Важно пополнить запас до потери продаж.'
      WHEN 'ads_without_sales'
        THEN 'По товару есть рекламные расходы без продаж. Остановите или пересоберите неэффективную рекламу.'
      ELSE 'По товару найден приоритетный риск, который требует действия.'
    END AS action_summary,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads'
        THEN 'Товар уже уходит в минус и продолжает терять деньги.'
      WHEN 'ads_eating_profit'
        THEN 'Прибыль до рекламы есть, но реклама быстро съедает результат.'
      WHEN 'high_logistics_share'
        THEN 'Высокая доля логистики давит на прибыль уже в текущем периоде.'
      WHEN 'low_margin_after_ads'
        THEN 'Маржа слишком тонкая: любое ухудшение затрат быстро приведет к убыточности.'
      WHEN 'stockout_risk'
        THEN 'Есть риск потерять будущую выручку из-за нехватки остатка.'
      WHEN 'ads_without_sales'
        THEN 'Бюджет уже расходуется без продаж и не создает выручку.'
      ELSE 'Проблема влияет на деньги и требует внимания сейчас.'
    END AS why_now,
    p.recommended_action,
    CASE p.rule_code
      WHEN 'negative_profit_after_ads'
        THEN 'Сократите продвижение SKU на 20-30% на 7 дней и проверьте, вышел ли товар из минуса.'
      WHEN 'ads_eating_profit'
        THEN 'Сократите рекламную нагрузку на SKU на 20-30% и не увеличивайте бюджет до повторной проверки.'
      WHEN 'high_logistics_share'
        THEN 'Не усиливайте продвижение SKU, пока не проверите экономику товара при текущей доле логистики.'
      WHEN 'low_margin_after_ads'
        THEN 'Заморозьте масштабирование SKU на 7 дней и перепроверьте маржу после рекламы.'
      WHEN 'stockout_risk'
        THEN 'Поставьте SKU в приоритет пополнения и не разгоняйте спрос до подтверждения поставки.'
      WHEN 'ads_without_sales'
        THEN 'Поставьте рекламу SKU на паузу на 7 дней и проверьте карточку, цену и наличие.'
      ELSE p.recommended_action
    END AS first_step,
    7::int AS action_window_days,
    p.expected_effect_note,
    COALESCE(sq.attribution_quality, 'exact_match') AS attribution_quality,
    COALESCE(sq.confidence, 'high') AS confidence,
    s.money_impact_30d,
    s.revenue_at_risk_30d,
    CASE
      WHEN p.rule_code = 'stockout_risk' THEN CAST(NULL AS NUMERIC(18,2))
      ELSE s.money_impact_30d
    END AS estimated_profit_uplift_30d,
    CASE
      WHEN p.rule_code = 'stockout_risk' THEN s.revenue_at_risk_30d
      ELSE CAST(NULL AS NUMERIC(18,2))
    END AS estimated_revenue_protection_30d,
    COALESCE(s.money_impact_30d, s.revenue_at_risk_30d) AS estimated_gain_if_fixed,
    ic.days_of_stock_30d,
    ic.daily_revenue_run_rate,
    ic.daily_profit_run_rate,
    ic.revenue_at_risk_per_day,
    ic.revenue_at_risk_3d,
    ic.revenue_at_risk_7d,
    ic.revenue_at_risk_14d,
    ic.profit_at_risk_per_day,
    ic.expected_replenishment_date,
    ic.planned_replenishment_qty,
    ic.replenishment_status,
    ic.stockout_effect_confidence,
    c.supporting_rule_codes,
    s.supporting_problem_count,
    (
      p.priority_score
      + LEAST(15::numeric, GREATEST((s.supporting_problem_count - 1), 0) * 5::numeric)
    ) AS action_priority_score
  FROM primary_hit p
  JOIN supporting_agg s
    ON s.run_date = p.run_date
   AND s.period_start = p.period_start
   AND s.period_end = p.period_end
   AND s.marketplace = p.marketplace
   AND s.offer_id = p.offer_id
  JOIN supporting_codes c
    ON c.run_date = p.run_date
   AND c.period_start = p.period_start
   AND c.period_end = p.period_end
   AND c.marketplace = p.marketplace
   AND c.offer_id = p.offer_id
  LEFT JOIN store_quality sq
    ON sq.period_start = p.period_start
   AND sq.period_end = p.period_end
   AND sq.marketplace = p.marketplace
  LEFT JOIN inventory_context ic
    ON ic.marketplace = p.marketplace
   AND ic.offer_id = p.offer_id
),
action_governance AS (
  SELECT
    a.*,
    COUNT(*) OVER (
      PARTITION BY a.run_date, a.period_start, a.period_end, a.marketplace, a.action_code
    )::int AS action_group_size,
    ROW_NUMBER() OVER (
      PARTITION BY a.run_date, a.period_start, a.period_end, a.marketplace, a.action_code
      ORDER BY a.action_priority_score DESC NULLS LAST, a.rule_rank, a.offer_id
    )::int AS action_group_rank,
    COUNT(*) OVER (
      PARTITION BY a.run_date, a.period_start, a.period_end, a.marketplace, a.action_type
    )::int AS action_type_group_size,
    ROW_NUMBER() OVER (
      PARTITION BY a.run_date, a.period_start, a.period_end, a.marketplace, a.action_type
      ORDER BY a.action_priority_score DESC NULLS LAST, a.rule_rank, a.offer_id
    )::int AS action_type_group_rank
  FROM action_base a
),
action_scored AS (
  SELECT
    g.*,
    CASE
      WHEN g.action_group_size >= 3 THEN TRUE
      ELSE FALSE
    END AS batch_candidate,
    LEAST(20::numeric, GREATEST(g.action_group_rank - 1, 0) * 4::numeric) AS duplicate_penalty_score,
    (
      g.action_priority_score
      - LEAST(20::numeric, GREATEST(g.action_group_rank - 1, 0) * 4::numeric)
      + CASE g.primary_rule_code
          WHEN 'stockout_risk' THEN 6::numeric
          WHEN 'negative_profit_after_ads' THEN 4::numeric
          WHEN 'ads_without_sales' THEN 3::numeric
          WHEN 'ads_eating_profit' THEN 2::numeric
          ELSE 0::numeric
        END
      + CASE
          WHEN g.action_group_rank = 1 AND g.action_group_size >= 3
            THEN LEAST(12::numeric, (g.action_group_size - 1) * 2::numeric)
          ELSE 0::numeric
        END
    ) AS governance_priority_score
  FROM action_governance g
),
action_ranked AS (
  SELECT
    a.*,
    ROW_NUMBER() OVER (
      PARTITION BY a.run_date, a.period_start, a.period_end, a.marketplace
      ORDER BY a.governance_priority_score DESC NULLS LAST, a.rule_rank, a.action_group_rank, a.offer_id
    )::int AS action_priority
  FROM action_scored a
)
SELECT
  run_date,
  period_start,
  period_end,
  marketplace,
  offer_id,
  product_name,
  primary_rule_code,
  action_code,
  action_type,
  action_priority,
  governance_priority_score AS action_priority_score,
  severity,
  action_title,
  action_summary,
  why_now,
  recommended_action,
  first_step,
  action_window_days,
  attribution_quality,
  confidence,
  CASE
    WHEN primary_rule_code = 'stockout_risk' AND COALESCE(revenue_at_risk_per_day, 0) > 0
      THEN 'Если пополнение задержится, под риском около '
           || ROUND(revenue_at_risk_per_day, 2)::text
           || ' выручки в день; за 7 дней — до '
           || ROUND(COALESCE(revenue_at_risk_7d, 0), 2)::text || '.'
    WHEN COALESCE(money_impact_30d, 0) > 0
      THEN 'Снижение проблемы может сократить потери до '
           || ROUND(money_impact_30d, 2)::text || ' за 30 дней.'
    WHEN COALESCE(revenue_at_risk_30d, 0) > 0
      THEN 'Снижение риска может защитить выручку до '
           || ROUND(revenue_at_risk_30d, 2)::text || ' за 30 дней.'
    ELSE expected_effect_note
  END AS expected_effect_note,
  money_impact_30d,
  revenue_at_risk_30d,
  estimated_profit_uplift_30d,
  estimated_revenue_protection_30d,
  estimated_gain_if_fixed,
  COALESCE(money_impact_30d, revenue_at_risk_30d) AS total_action_impact_30d,
  days_of_stock_30d,
  daily_revenue_run_rate,
  daily_profit_run_rate,
  revenue_at_risk_per_day,
  revenue_at_risk_3d,
  revenue_at_risk_7d,
  revenue_at_risk_14d,
  profit_at_risk_per_day,
  expected_replenishment_date,
  planned_replenishment_qty,
  replenishment_status,
  stockout_effect_confidence,
  supporting_rule_codes,
  supporting_problem_count,
  action_group_size,
  action_group_rank,
  action_type_group_size,
  action_type_group_rank,
  batch_candidate,
  duplicate_penalty_score,
  CASE
    WHEN batch_candidate AND action_group_rank = 1
      THEN 'В группе '
           || action_code
           || ' найдено '
           || action_group_size::text
           || ' похожих SKU. Начните с этого товара как шаблона для batch-действия.'
    WHEN batch_candidate AND action_group_rank > 1
      THEN 'Похожая проблема уже представлена более приоритетной карточкой в этой группе.'
    ELSE NULL
  END AS batch_hint,
  CASE
    WHEN action_priority <= 3 THEN 'do_now'
    WHEN action_priority <= 10 THEN 'next'
    ELSE 'later'
  END AS execution_lane,
  'mart_rule_hits_30d'::text AS source_layer,
  'v1.1'::text AS action_version,
  now() AS loaded_at
INTO {{schema}}.mart_action_queue_14d
FROM action_ranked;

CREATE INDEX mart_action_queue_14d_offer_idx
  ON {{schema}}.mart_action_queue_14d (marketplace, offer_id);

CREATE INDEX mart_action_queue_14d_priority_idx
  ON {{schema}}.mart_action_queue_14d (marketplace, action_priority, action_priority_score DESC);

CREATE INDEX mart_action_queue_14d_lane_idx
  ON {{schema}}.mart_action_queue_14d (marketplace, execution_lane, action_priority);
