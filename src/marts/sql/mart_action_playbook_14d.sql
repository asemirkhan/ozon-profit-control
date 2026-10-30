-- mart_action_playbook_14d
-- 1 строка = 1 playbook-карточка по товару на текущий расчет

DROP TABLE IF EXISTS {{schema}}.mart_action_playbook_14d;

WITH action_base AS (
  SELECT
    run_date,
    period_start,
    period_end,
    marketplace,
    offer_id,
    product_name,
    primary_rule_code,
    action_code,
    action_priority,
    action_priority_score,
    severity,
    attribution_quality,
    confidence,
    money_impact_30d,
    revenue_at_risk_30d,
    estimated_profit_uplift_30d,
    estimated_revenue_protection_30d,
    estimated_gain_if_fixed,
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
    total_action_impact_30d,
    supporting_rule_codes,
    supporting_problem_count,
    expected_effect_note
  FROM {{schema}}.mart_action_queue_14d
),
primary_rule_metrics AS (
  SELECT
    h.run_date,
    h.period_start,
    h.period_end,
    h.marketplace,
    h.offer_id,
    h.rule_code,
    MAX(h.metric_value) AS metric_value,
    MAX(h.threshold_value) AS threshold_value
  FROM {{schema}}.mart_rule_hits_30d h
  GROUP BY 1,2,3,4,5,6
)
SELECT
  a.run_date,
  a.period_start,
  a.period_end,
  a.marketplace,
  a.offer_id,
  a.product_name,
  a.primary_rule_code,
  a.action_code,
  a.action_priority,
  a.action_priority_score,
  a.severity,
  a.attribution_quality,
  a.confidence,
  a.estimated_profit_uplift_30d,
  a.estimated_revenue_protection_30d,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads' THEN 'Товар уже убыточен после рекламы'
    WHEN 'optimize_ads_profitability' THEN 'Реклама съедает большую часть прибыли'
    WHEN 'reduce_logistics_burden' THEN 'Логистика забирает слишком большую долю выручки'
    WHEN 'improve_margin' THEN 'Маржа после рекламы слишком тонкая'
    WHEN 'replenish_stock' THEN 'Товар в зоне риска по остаткам'
    WHEN 'stop_wasteful_ads' THEN 'Реклама расходуется без продаж'
    ELSE 'Товар требует приоритетного действия'
  END AS playbook_title,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN CASE
             WHEN COALESCE(a.money_impact_30d, 0) > 0
               THEN 'За 30 дней товар потерял '
                    || ROUND(a.money_impact_30d, 2)::text
                    || ' ₽ после рекламы. '
             ELSE 'Товар убыточен после рекламы за последние 30 дней. '
           END
           || CASE
                WHEN a.supporting_problem_count > 1
                  THEN 'Параллельно есть дополнительные риски: ' || a.supporting_rule_codes || '.'
                ELSE 'Нужно остановить прямые потери по SKU до пересмотра его экономики.'
              END
    WHEN 'optimize_ads_profitability'
      THEN CASE
             WHEN COALESCE(a.money_impact_30d, 0) > 0
               THEN 'За 30 дней на рекламу ушло до '
                    || ROUND(a.money_impact_30d, 2)::text
                    || ' ₽, и после рекламы по товару остается слишком мало прибыли.'
             ELSE 'Реклама съедает значимую долю прибыли: товар пока не в минусе, но прибыль после рекламы слишком низкая.'
           END
    WHEN 'reduce_logistics_burden'
      THEN 'Логистика занимает '
           || COALESCE(ROUND(pm.metric_value * 100, 1)::text || '%', 'повышенную долю')
           || ' выручки и напрямую снижает прибыль по SKU.'
    WHEN 'improve_margin'
      THEN 'Маржа после рекламы около '
           || COALESCE(ROUND(pm.metric_value * 100, 1)::text || '%', 'низкого уровня')
           || ', это ниже безопасного уровня для роста продаж.'
    WHEN 'replenish_stock'
      THEN CASE
             WHEN COALESCE(a.revenue_at_risk_30d, 0) > 0
               THEN 'Из-за риска дефицита под угрозой до '
                    || ROUND(a.revenue_at_risk_30d, 2)::text
                    || ' ₽ выручки за 30 дней.'
             ELSE 'Остаток по товару в зоне риска и может ограничить продажи в ближайшее время.'
           END
           || CASE
                WHEN a.supporting_problem_count > 1
                  THEN ' Дополнительно по товару выявлены: ' || a.supporting_rule_codes || '.'
                ELSE ''
              END
    WHEN 'stop_wasteful_ads'
      THEN CASE
             WHEN COALESCE(a.money_impact_30d, 0) > 0
               THEN 'На товар тратится реклама без продаж: прямые потери бюджета до '
                    || ROUND(a.money_impact_30d, 2)::text
                    || ' ₽ за 30 дней.'
             ELSE 'На товар расходуется реклама без продаж, и бюджет не конвертируется в выручку.'
           END
    ELSE 'По товару есть приоритетная проблема, требующая действия.'
  END AS problem_summary,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Товар уже в минусе после рекламы, поэтому каждый следующий день усиливает потери.'
    WHEN 'optimize_ads_profitability'
      THEN 'Реклама забирает большую часть результата, и товар может перейти в убыточность уже в ближайшем цикле.'
    WHEN 'reduce_logistics_burden'
      THEN 'Пока доля логистики завышена, рост продаж увеличивает оборот без сопоставимого роста прибыли.'
    WHEN 'improve_margin'
      THEN 'Текущая маржа слишком низкая: небольшое ухудшение затрат быстро переводит товар в зону риска.'
    WHEN 'replenish_stock'
      THEN 'Риск дефицита уже влияет на будущую выручку, поэтому откладывать пополнение опасно.'
    WHEN 'stop_wasteful_ads'
      THEN 'Реклама уже расходует бюджет без результата, поэтому откладывание решения наращивает убыток.'
    ELSE 'Проблема влияет на деньги в текущем периоде и требует внимания сейчас.'
  END
  || CASE
       WHEN a.supporting_problem_count > 1
         THEN ' Есть дополнительные сигналы риска: ' || a.supporting_rule_codes || '.'
       ELSE ''
     END AS why_now,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Сократите продвижение этого SKU на 20-30% на ближайшие 7 дней. Если не можете управлять снижением отдельно по товару, временно приостановите его платное продвижение.'
    WHEN 'optimize_ads_profitability'
      THEN 'Сократите продвижение этого SKU на 20-30% на 7 дней и не увеличивайте бюджет, пока прибыль после рекламы остается слабой.'
    WHEN 'reduce_logistics_burden'
      THEN 'Не увеличивайте продвижение этого SKU на ближайшие 7 дней, пока не подтвердите, что товар остается прибыльным при текущей доле логистики.'
    WHEN 'improve_margin'
      THEN 'Не увеличивайте продвижение этого SKU на 7 дней, пока маржа после рекламы остается опасно низкой.'
    WHEN 'replenish_stock'
      THEN 'Поставьте этот SKU в приоритет ближайшего пополнения. Если план поставки еще не зафиксирован, сделайте это в первую очередь.'
    WHEN 'stop_wasteful_ads'
      THEN 'Временно приостановите платное продвижение этого SKU на 7 дней. Если полная остановка невозможна, сократите продвижение максимально резко.'
    ELSE 'Сначала остановите действия, которые усиливают текущую проблему.'
  END AS first_step,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads' THEN 7
    WHEN 'optimize_ads_profitability' THEN 7
    WHEN 'reduce_logistics_burden' THEN 7
    WHEN 'improve_margin' THEN 7
    WHEN 'replenish_stock' THEN 7
    WHEN 'stop_wasteful_ads' THEN 7
    ELSE 7
  END::int AS action_window_days,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads' THEN 'сократить на 20-30% или временно приостановить'
    WHEN 'optimize_ads_profitability' THEN 'сократить на 20-30%'
    WHEN 'reduce_logistics_burden' THEN 'не усиливать 7 дней'
    WHEN 'improve_margin' THEN 'не усиливать 7 дней'
    WHEN 'replenish_stock' THEN 'приоритет пополнения'
    WHEN 'stop_wasteful_ads' THEN 'временно приостановить'
    ELSE 'выполнить приоритетное действие 7 дней'
  END AS action_intensity_hint,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Через 7 дней сравните прибыль после рекламы до и после снижения продвижения.'
    WHEN 'optimize_ads_profitability'
      THEN 'Через 7 дней сравните прибыль после рекламы и долю рекламных расходов до и после снижения.'
    WHEN 'reduce_logistics_burden'
      THEN 'Проверьте, остается ли товар прибыльным при текущей цене и текущей доле логистики. Если без активного продвижения товар слабый, не относите его к SKU для роста.'
    WHEN 'improve_margin'
      THEN 'Через 7 дней проверьте, остается ли у товара нормальная прибыль без дополнительной рекламной нагрузки. Если нет, не относите SKU к товарам для роста.'
    WHEN 'replenish_stock'
      THEN 'Если быстро пополнить товар нельзя, на ближайшие 7 дней не усиливайте рекламу и не разгоняйте спрос искусственно.'
    WHEN 'stop_wasteful_ads'
      THEN 'После остановки проверьте, есть ли у товара шанс продаваться при текущей карточке, цене и остатке.'
    ELSE 'Далее проверьте, стабилизировалась ли метрика после первого шага.'
  END AS next_step,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Сравните прибыль после рекламы до и после ограничения продвижения: товар должен выйти из минуса или заметно сократить убыток.'
    WHEN 'optimize_ads_profitability'
      THEN 'Сравните прибыль после рекламы и долю рекламных расходов до и после ограничения SKU.'
    WHEN 'reduce_logistics_burden'
      THEN 'Проверьте, сохраняет ли товар прибыль без дополнительного разгона продаж при текущей доле логистики.'
    WHEN 'improve_margin'
      THEN 'Проверьте, вышла ли маржа после рекламы из опасно низкой зоны и сохраняется ли прибыль.'
    WHEN 'replenish_stock'
      THEN 'Проверьте, вышел ли товар из зоны риска по остаткам и снизился ли риск потери выручки.'
    WHEN 'stop_wasteful_ads'
      THEN 'Проверьте, прекратились ли рекламные расходы без продаж и появились ли продажи после ручной проверки товара.'
    ELSE 'Проверьте целевую метрику до и после первого шага.'
  END AS check_after_window,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Если результат все еще отрицательный, не возвращайте прежний объем продвижения: отдельно проверьте фактическую цену и логистику.'
    WHEN 'optimize_ads_profitability'
      THEN 'Если после снижения продвижения прибыль остается слишком слабой, не масштабируйте SKU и переходите к проверке цены и общей экономики товара.'
    WHEN 'reduce_logistics_burden'
      THEN 'Если экономика остается слабой, не наращивайте продажи этого товара как приоритетного, пока не решен вопрос цены или издержек.'
    WHEN 'improve_margin'
      THEN 'Если результат все еще слишком слабый, не масштабируйте товар: сначала проверьте цену, скидку и рекламную нагрузку.'
    WHEN 'replenish_stock'
      THEN 'Если риск дефицита сохраняется, не увеличивайте спрос на SKU до появления подтвержденного пополнения.'
    WHEN 'stop_wasteful_ads'
      THEN 'Если продаж все еще нет, не возвращайте платное продвижение до исправления карточки, цены или наличия.'
    ELSE 'Если улучшения нет, не усиливайте текущую активность и переходите к ручной проверке экономики товара.'
  END AS if_not_improved,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Когда товар уже убыточен после рекламы, безопаснее сначала уменьшить платное давление по SKU, чем продолжать покупать трафик в минус.'
    WHEN 'optimize_ads_profitability'
      THEN 'Когда реклама съедает большую часть прибыли, безопаснее сначала снизить промо-нагрузку по SKU, чем масштабировать слабую экономику.'
    WHEN 'reduce_logistics_burden'
      THEN 'Если логистика съедает слишком большую долю выручки, безопаснее сначала не усиливать продажи SKU, пока не подтверждена рабочая прибыльность.'
    WHEN 'improve_margin'
      THEN 'Если маржа после рекламы слишком тонкая, безопаснее сначала не наращивать продвижение, чем усиливать товар без запаса по прибыли.'
    WHEN 'replenish_stock'
      THEN 'Если товар близок к дефициту, безопаснее сначала зафиксировать пополнение, чем усиливать спрос без подтвержденного остатка.'
    WHEN 'stop_wasteful_ads'
      THEN 'Если реклама идет без продаж, безопаснее сначала остановить прямые потери бюджета, чем продолжать покупать трафик без результата.'
    ELSE 'Сначала выполняется безопасный шаг, который ограничивает потери и не усиливает риск по товару.'
  END AS action_rationale,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN '7 дней достаточно, чтобы увидеть, сокращается ли убыток после ограничения продвижения, и при этом не затягивать решение.'
    WHEN 'optimize_ads_profitability'
      THEN '7 дней достаточно, чтобы увидеть, меняется ли прибыль после рекламы при более умеренном продвижении.'
    WHEN 'reduce_logistics_burden'
      THEN '7 дней позволяют не разгонять экономически слабый товар и проверить, сохраняет ли он прибыль без дополнительного давления.'
    WHEN 'improve_margin'
      THEN '7 дней дают рабочий интервал, чтобы проверить, остается ли товар прибыльным без дополнительной рекламной нагрузки.'
    WHEN 'replenish_stock'
      THEN '7 дней дают стартовый горизонт, чтобы понять, выходит ли товар из зоны риска по остаткам или спрос нужно ограничивать дальше.'
    WHEN 'stop_wasteful_ads'
      THEN '7 дней дают стартовое окно, чтобы остановить слив бюджета и отдельно проверить готовность товара к продажам.'
    ELSE '7 дней используются как стартовое контрольное окно: достаточно длинное для наблюдения и достаточно короткое для быстрого решения.'
  END AS why_this_window,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Если через 7 дней товар все еще убыточен после рекламы или убыток сократился незначительно, первый шаг проблему не решил.'
    WHEN 'optimize_ads_profitability'
      THEN 'Если через 7 дней реклама все еще забирает большую часть прибыли, товар пока не готов к масштабированию.'
    WHEN 'reduce_logistics_burden'
      THEN 'Если товар остается слабым по прибыли даже без усиления продаж, проблема в экономике SKU не решена.'
    WHEN 'improve_margin'
      THEN 'Если через 7 дней маржа после рекламы остается в опасно низкой зоне, товар пока не готов к масштабированию.'
    WHEN 'replenish_stock'
      THEN 'Если через 7 дней товар остается в зоне риска по остаткам, пополнение или ограничение спроса нужно продолжать.'
    WHEN 'stop_wasteful_ads'
      THEN 'Если после паузы и ручной проверки продаж по-прежнему нет, возвращать платное продвижение рано.'
    ELSE 'Если после контрольного окна ключевая метрика почти не изменилась, первый шаг не дал достаточного эффекта.'
  END AS not_improved_criteria,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Проверьте, остается ли товар убыточным после ограничения продвижения.'
    WHEN 'optimize_ads_profitability'
      THEN 'Проверьте, улучшилась ли прибыль после рекламы после снижения продвижения.'
    WHEN 'reduce_logistics_burden'
      THEN 'Проверьте, остается ли товар прибыльным при текущей цене и текущей доле логистики.'
    WHEN 'improve_margin'
      THEN 'Проверьте, сохраняет ли товар нормальную прибыль без дополнительного продвижения.'
    WHEN 'replenish_stock'
      THEN 'Проверьте, зафиксирован ли план пополнения и выходит ли товар из зоны риска по остаткам.'
    WHEN 'stop_wasteful_ads'
      THEN 'Сначала убедитесь, что рекламные потери по SKU остановлены.'
    ELSE 'Проверьте, изменилась ли ключевая метрика после первого шага.'
  END AS diagnostic_priority_1,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Если убыток сохраняется, следующим кандидатом на проверку является цена и маржа товара.'
    WHEN 'optimize_ads_profitability'
      THEN 'Если улучшения нет, следующим кандидатом на проверку является цена и маржа товара.'
    WHEN 'reduce_logistics_burden'
      THEN 'Если прибыль не сохраняется, не относите SKU к приоритетам роста.'
    WHEN 'improve_margin'
      THEN 'Если результат слабый, следующим кандидатом на проверку является фактическая цена, скидка или крупнейший расход площадки.'
    WHEN 'replenish_stock'
      THEN 'Если риска меньше не стало, не усиливайте рекламу и не разгоняйте спрос.'
    WHEN 'stop_wasteful_ads'
      THEN 'Затем проверьте карточку товара, цену и наличие.'
    ELSE 'Если явного улучшения нет, переходите к проверке базовой экономики товара.'
  END AS diagnostic_priority_2,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Если маржа не объясняет проблему, проверьте логистику и прочие издержки.'
    WHEN 'optimize_ads_profitability'
      THEN 'Если экономика остается слабой, не относите SKU к товарам для роста.'
    WHEN 'reduce_logistics_burden'
      THEN 'Следующим кандидатом на пересмотр является цена или структура издержек по товару.'
    WHEN 'improve_margin'
      THEN 'Пока маржа не укреплена, не относите SKU к товарам для роста.'
    WHEN 'replenish_stock'
      THEN 'До подтвержденного пополнения не увеличивайте спрос по SKU.'
    WHEN 'stop_wasteful_ads'
      THEN 'Если продаж все еще нет, не возвращайте платное продвижение до исправления базовых проблем.'
    ELSE 'До подтверждения устойчивого результата не усиливайте спрос по SKU.'
  END AS diagnostic_priority_3,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Сначала уменьшите рекламное давление, затем проверьте, ушел ли товар из минуса. Если нет, приоритет проверки: маржа и издержки.'
    WHEN 'optimize_ads_profitability'
      THEN 'Сначала снизьте рекламную нагрузку и сравните результат. Если реклама по-прежнему съедает прибыль, не масштабируйте товар и проверьте базовую экономику.'
    WHEN 'reduce_logistics_burden'
      THEN 'Сначала не усиливайте продажи, затем проверьте, выдерживает ли товар текущую логистическую нагрузку. Если нет, не масштабируйте SKU как growth-товар.'
    WHEN 'improve_margin'
      THEN 'Сначала не усиливайте товар, затем проверьте, остается ли нормальная прибыль без дополнительного продвижения. Если нет, проблема в базовой экономике товара.'
    WHEN 'replenish_stock'
      THEN 'Сначала поставьте товар в приоритет пополнения. Если быстрый выход из риска не подтверждается, удерживайте спрос под контролем до подтвержденного пополнения.'
    WHEN 'stop_wasteful_ads'
      THEN 'Сначала остановите прямые рекламные потери, затем проверьте базовую готовность товара к продажам. Если продаж нет, не возвращайте платное продвижение.'
    ELSE 'Сначала выполните безопасный шаг, затем проверьте метрику. Если улучшения нет, переходите к базовой диагностике экономики товара.'
  END AS decision_path_summary,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Не увеличивайте платный трафик, пока товар уже уходит в минус.'
    WHEN 'optimize_ads_profitability'
      THEN 'Не масштабируйте продвижение, пока реклама забирает большую часть прибыли.'
    WHEN 'reduce_logistics_burden'
      THEN 'Не разгоняйте продажи товара с плохой логистической экономикой без проверки маржи.'
    WHEN 'improve_margin'
      THEN 'Не считайте товар здоровым, если он держится на слишком тонкой марже.'
    WHEN 'replenish_stock'
      THEN 'Не увеличивайте спрос на товар, который может закончиться раньше пополнения.'
    WHEN 'stop_wasteful_ads'
      THEN 'Не продолжайте тратить рекламу на SKU без продаж.'
    ELSE NULL
  END AS do_not_do,
  CASE a.action_code
    WHEN 'fix_unprofitable_ads'
      THEN 'Товар перестал быть убыточным после рекламы или убыток после рекламы заметно ниже базового уровня.'
    WHEN 'optimize_ads_profitability'
      THEN 'Реклама перестала съедать большую часть прибыли, а прибыль после рекламы сохраняется после ограничения SKU.'
    WHEN 'reduce_logistics_burden'
      THEN 'Товар сохраняет прибыль без дополнительного разгона продаж, а логистика не делает SKU убыточным.'
    WHEN 'improve_margin'
      THEN 'Маржа после рекламы вышла из опасной зоны, и товар сохраняет прибыль без роста рекламной нагрузки.'
    WHEN 'replenish_stock'
      THEN 'SKU вышел из зоны риска по остаткам или риск потери выручки заметно снизился.'
    WHEN 'stop_wasteful_ads'
      THEN 'Реклама по SKU больше не идет без продаж: расходы остановлены или появились продажи.'
    ELSE NULL
  END AS success_signal,
  a.money_impact_30d,
  a.revenue_at_risk_30d,
  a.estimated_gain_if_fixed,
  a.days_of_stock_30d,
  a.daily_revenue_run_rate,
  a.daily_profit_run_rate,
  a.revenue_at_risk_per_day,
  a.revenue_at_risk_3d,
  a.revenue_at_risk_7d,
  a.revenue_at_risk_14d,
  a.profit_at_risk_per_day,
  a.expected_replenishment_date,
  a.planned_replenishment_qty,
  a.replenishment_status,
  a.stockout_effect_confidence,
  a.total_action_impact_30d,
  CASE
    WHEN COALESCE(a.money_impact_30d, 0) > 0
      THEN 'При реализации шагов можно сократить потери до '
           || ROUND(a.money_impact_30d, 2)::text || ' ₽ за 30 дней.'
    WHEN COALESCE(a.revenue_at_risk_30d, 0) > 0
      THEN 'При реализации шагов можно защитить выручку до '
           || ROUND(a.revenue_at_risk_30d, 2)::text || ' ₽ за 30 дней.'
    ELSE a.expected_effect_note
  END AS expected_effect_note,
  a.supporting_rule_codes,
  a.supporting_problem_count,
  'v1.2'::text AS playbook_version,
  'mart_action_queue_14d'::text AS source_layer,
  now() AS loaded_at
INTO {{schema}}.mart_action_playbook_14d
FROM action_base a
LEFT JOIN primary_rule_metrics pm
  ON pm.run_date = a.run_date
 AND pm.period_start = a.period_start
 AND pm.period_end = a.period_end
 AND pm.marketplace = a.marketplace
 AND pm.offer_id = a.offer_id
 AND pm.rule_code = a.primary_rule_code;

CREATE INDEX mart_action_playbook_14d_offer_idx
  ON {{schema}}.mart_action_playbook_14d (marketplace, offer_id);

CREATE INDEX mart_action_playbook_14d_priority_idx
  ON {{schema}}.mart_action_playbook_14d (marketplace, action_priority, action_priority_score DESC);
