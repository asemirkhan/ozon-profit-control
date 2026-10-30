-- mart_data_quality_30d
-- 1 строка = сводка ключевых аномалий качества данных за период и marketplace

DROP TABLE IF EXISTS {{schema}}.mart_data_quality_30d;

WITH params AS (
  SELECT
    CURRENT_DATE AS run_date,
    '{{period_start}}'::date AS period_start,
    '{{period_end}}'::date AS period_end
),
marketplaces AS (
  SELECT DISTINCT marketplace FROM {{schema}}.mart_pnl_30d_by_product
  UNION
  SELECT DISTINCT marketplace FROM {{schema}}.mart_inventory_snapshot
  UNION
  SELECT 'ozon'::text
  UNION
  SELECT 'wb'::text
),
pnl_agg AS (
  SELECT
    p.marketplace,
    COUNT(DISTINCT CASE WHEN COALESCE(p.revenue_gross, 0) < 0 THEN p.offer_id END) AS negative_revenue_sku_count,
    COUNT(DISTINCT CASE WHEN COALESCE(p.units_sold, 0) > 0 AND COALESCE(p.revenue_gross, 0) = 0 THEN p.offer_id END) AS zero_revenue_with_sales_sku_count,
    COUNT(DISTINCT CASE WHEN COALESCE(p.logistics_amount, 0) < 0 THEN p.offer_id END) AS negative_logistics_sku_count
  FROM {{schema}}.mart_pnl_30d_by_product p
  JOIN params prm
    ON p.period_start = prm.period_start
   AND p.period_end = prm.period_end
  GROUP BY p.marketplace
),
inventory_agg AS (
  SELECT
    i.marketplace,
    COUNT(DISTINCT i.offer_id) AS stock_without_sales_sku_count
  FROM {{schema}}.mart_inventory_snapshot i
  WHERE COALESCE(i.stock_available, 0) > 0
    AND COALESCE(i.avg_daily_units_sold_30d, 0) <= 0
  GROUP BY i.marketplace
),
wb_orders_present AS (
  SELECT
    CASE
      WHEN EXISTS (
        SELECT 1
        FROM {{schema}}.stg_wb_order o
        JOIN params prm
          ON o.order_date >= prm.period_start::timestamp
         AND o.order_date < prm.period_end::timestamp
      ) THEN 1 ELSE 0
    END AS present
),
posting_dupes AS (
  SELECT
    COALESCE(SUM(x.dup_cnt), 0)::int AS duplicate_posting_count,
    'ozon'::text AS marketplace
  FROM (
    SELECT GREATEST(COUNT(*) - 1, 0) AS dup_cnt
    FROM {{schema}}.stg_posting p
    JOIN params prm
      ON p.created_at >= prm.period_start::timestamp
     AND p.created_at < prm.period_end::timestamp
    GROUP BY p.posting_number
    HAVING COUNT(*) > 1
  ) x
),
wb_order_dupes AS (
  SELECT
    CASE WHEN (SELECT present FROM wb_orders_present) = 1
      THEN COALESCE(SUM(x.dup_cnt), 0)::int
      ELSE 0::int
    END AS duplicate_posting_count,
    'wb'::text AS marketplace
  FROM (
    SELECT GREATEST(COUNT(*) - 1, 0) AS dup_cnt
    FROM {{schema}}.stg_wb_order o
    JOIN params prm
      ON o.order_date >= prm.period_start::timestamp
     AND o.order_date < prm.period_end::timestamp
    GROUP BY o.order_id
    HAVING COUNT(*) > 1
  ) x
),
duplicate_agg AS (
  SELECT marketplace, SUM(duplicate_posting_count)::int AS duplicate_posting_count
  FROM (
    SELECT * FROM posting_dupes
    UNION ALL
    SELECT * FROM wb_order_dupes
  ) z
  GROUP BY marketplace
),
txn_agg_ozon AS (
  SELECT
    'ozon'::text AS marketplace,
    COUNT(*)::int AS transaction_without_posting_count
  FROM {{schema}}.stg_transaction t
  JOIN params prm
    ON t.operation_date >= prm.period_start::timestamp
   AND t.operation_date < prm.period_end::timestamp
  WHERE COALESCE(t.posting_number, '') = ''
     OR NOT EXISTS (
       SELECT 1
       FROM {{schema}}.stg_posting p
       WHERE p.posting_number = t.posting_number
         AND COALESCE(p.marketplace, 'ozon') = 'ozon'
     )
),
txn_agg_wb AS (
  SELECT
    'wb'::text AS marketplace,
    CASE WHEN (SELECT present FROM wb_orders_present) = 1
      THEN COUNT(*)::int
      ELSE 0::int
    END AS transaction_without_posting_count
  FROM {{schema}}.stg_wb_finance f
  JOIN params prm
    ON f.operation_date >= prm.period_start::timestamp
   AND f.operation_date < prm.period_end::timestamp
  WHERE (SELECT present FROM wb_orders_present) = 1
    AND (
      COALESCE(f.order_id, '') = ''
      OR NOT EXISTS (
        SELECT 1
        FROM {{schema}}.stg_wb_order o
        WHERE o.order_id = f.order_id
          AND COALESCE(o.marketplace, 'wb') = 'wb'
      )
    )
),
txn_agg AS (
  SELECT marketplace, SUM(transaction_without_posting_count)::int AS transaction_without_posting_count
  FROM (
    SELECT * FROM txn_agg_ozon
    UNION ALL
    SELECT * FROM txn_agg_wb
  ) z
  GROUP BY marketplace
),
unattributed_ozon AS (
  SELECT
    'ozon'::text AS marketplace,
    COUNT(*)::int AS unattributed_row_count,
    SUM(
      ABS(COALESCE(t.delivery_charge, 0))
      + ABS(COALESCE(t.return_delivery_charge, 0))
      + ABS(COALESCE(t.sale_commission, 0))
    )::numeric(18,2) AS unattributed_cost_total
  FROM {{schema}}.stg_transaction t
  JOIN params prm
    ON t.operation_date >= prm.period_start::timestamp
   AND t.operation_date < prm.period_end::timestamp
  WHERE COALESCE(t.posting_number, '') = ''
     OR NOT EXISTS (
       SELECT 1
       FROM {{schema}}.stg_posting p
       WHERE p.posting_number = t.posting_number
         AND COALESCE(p.marketplace, 'ozon') = 'ozon'
     )
),
unattributed_wb AS (
  SELECT
    'wb'::text AS marketplace,
    COUNT(*)::int AS unattributed_row_count,
    SUM(
      ABS(COALESCE(f.commission_amount, 0))
      + ABS(COALESCE(f.logistics_amount, 0))
      + ABS(COALESCE(f.service_amount, 0))
    )::numeric(18,2) AS unattributed_cost_total
  FROM {{schema}}.stg_wb_finance f
  JOIN params prm
    ON f.operation_date >= prm.period_start::timestamp
   AND f.operation_date < prm.period_end::timestamp
  WHERE COALESCE(f.order_id, '') = ''
     OR NOT EXISTS (
       SELECT 1
       FROM {{schema}}.stg_wb_order o
       WHERE o.order_id = f.order_id
         AND COALESCE(o.marketplace, 'wb') = 'wb'
     )
),
unattributed_agg AS (
  SELECT
    marketplace,
    SUM(unattributed_row_count)::int AS unattributed_row_count,
    SUM(unattributed_cost_total)::numeric(18,2) AS unattributed_cost_total
  FROM (
    SELECT * FROM unattributed_ozon
    UNION ALL
    SELECT * FROM unattributed_wb
  ) z
  GROUP BY marketplace
),
explicit_store_level_unmatched AS (
  SELECT
    u.marketplace,
    COUNT(*)::int AS unmatched_row_count,
    SUM(COALESCE(u.amount, 0))::numeric(18,2) AS unmatched_cost_total
  FROM {{schema}}.fact_unmatched_money u
  JOIN params prm
    ON u.event_date >= prm.period_start
   AND u.event_date <= prm.period_end
  GROUP BY u.marketplace
),
fallback_ozon_logistics AS (
  SELECT
    'ozon'::text AS marketplace,
    COUNT(*)::int AS fallback_row_count,
    SUM(
      ABS(COALESCE(t.delivery_charge, 0))
      + ABS(COALESCE(t.return_delivery_charge, 0))
    )::numeric(18,2) AS fallback_cost_total
  FROM {{schema}}.stg_transaction t
  JOIN params prm
    ON t.operation_date >= prm.period_start::timestamp
   AND t.operation_date < prm.period_end::timestamp
  WHERE COALESCE(t.posting_number, '') <> ''
    AND EXISTS (
      SELECT 1
      FROM {{schema}}.stg_posting p
      WHERE p.posting_number = t.posting_number
        AND COALESCE(p.marketplace, 'ozon') = 'ozon'
    )
),
fallback_ozon_services AS (
  SELECT
    'ozon'::text AS marketplace,
    COUNT(*)::int AS fallback_row_count,
    SUM(ABS(COALESCE(s.service_price, 0)))::numeric(18,2) AS fallback_cost_total
  FROM {{schema}}.stg_transaction t
  JOIN {{schema}}.stg_transaction_service s
    ON s.operation_id = t.operation_id
   AND COALESCE(s.marketplace, 'ozon') = COALESCE(t.marketplace, 'ozon')
  JOIN params prm
    ON t.operation_date >= prm.period_start::timestamp
   AND t.operation_date < prm.period_end::timestamp
  WHERE COALESCE(t.posting_number, '') <> ''
    AND EXISTS (
      SELECT 1
      FROM {{schema}}.stg_posting p
      WHERE p.posting_number = t.posting_number
        AND COALESCE(p.marketplace, 'ozon') = 'ozon'
    )
),
fallback_wb AS (
  SELECT
    'wb'::text AS marketplace,
    0::int AS fallback_row_count,
    0::numeric(18,2) AS fallback_cost_total
),
fallback_agg AS (
  SELECT
    marketplace,
    SUM(fallback_row_count)::int AS fallback_attributed_row_count,
    SUM(fallback_cost_total)::numeric(18,2) AS fallback_attributed_cost_total
  FROM (
    SELECT * FROM fallback_ozon_logistics
    UNION ALL
    SELECT * FROM fallback_ozon_services
    UNION ALL
    SELECT * FROM fallback_wb
  ) z
  GROUP BY marketplace
),
metrics AS (
  SELECT
    prm.run_date,
    prm.period_start,
    prm.period_end,
    m.marketplace,
    COALESCE(pa.negative_revenue_sku_count, 0)::int AS negative_revenue_sku_count,
    COALESCE(pa.zero_revenue_with_sales_sku_count, 0)::int AS zero_revenue_with_sales_sku_count,
    COALESCE(pa.negative_logistics_sku_count, 0)::int AS negative_logistics_sku_count,
    COALESCE(ia.stock_without_sales_sku_count, 0)::int AS stock_without_sales_sku_count,
    COALESCE(da.duplicate_posting_count, 0)::int AS duplicate_posting_count,
    COALESCE(ta.transaction_without_posting_count, 0)::int AS transaction_without_posting_count,
    (COALESCE(ua.unattributed_row_count, 0) + COALESCE(eu.unmatched_row_count, 0))::int AS unattributed_row_count,
    (COALESCE(ua.unattributed_cost_total, 0) + COALESCE(eu.unmatched_cost_total, 0))::numeric(18,2) AS unattributed_cost_total,
    COALESCE(fa.fallback_attributed_row_count, 0)::int AS fallback_attributed_row_count,
    COALESCE(fa.fallback_attributed_cost_total, 0)::numeric(18,2) AS fallback_attributed_cost_total
  FROM params prm
  JOIN marketplaces m
    ON TRUE
  LEFT JOIN pnl_agg pa
    ON pa.marketplace = m.marketplace
  LEFT JOIN inventory_agg ia
    ON ia.marketplace = m.marketplace
  LEFT JOIN duplicate_agg da
    ON da.marketplace = m.marketplace
  LEFT JOIN txn_agg ta
    ON ta.marketplace = m.marketplace
  LEFT JOIN unattributed_agg ua
    ON ua.marketplace = m.marketplace
  LEFT JOIN explicit_store_level_unmatched eu
    ON eu.marketplace = m.marketplace
  LEFT JOIN fallback_agg fa
    ON fa.marketplace = m.marketplace
)
SELECT
  m.run_date,
  m.period_start,
  m.period_end,
  m.marketplace,
  m.negative_revenue_sku_count,
  CASE
    WHEN m.negative_revenue_sku_count > 0
      THEN 'Есть товары с отрицательной выручкой. Обычно это сигнал ошибки маппинга операций или некорректного учета возвратов, что влияет на итоговую прибыль.'
    ELSE 'Отрицательная выручка не выявлена.'
  END AS negative_revenue_note,
  m.zero_revenue_with_sales_sku_count,
  CASE
    WHEN m.zero_revenue_with_sales_sku_count > 0
      THEN 'Есть продажи при нулевой выручке. Это может означать, что часть финансовых сумм не связалась с продажами и расчеты искажены.'
    ELSE 'Продаж с нулевой выручкой не найдено.'
  END AS zero_revenue_note,
  m.negative_logistics_sku_count,
  CASE
    WHEN m.negative_logistics_sku_count > 0
      THEN 'Есть товары с отрицательной логистикой. Нужна проверка знака и источника логистических проводок.'
    ELSE 'Отрицательная логистика не выявлена.'
  END AS negative_logistics_note,
  m.stock_without_sales_sku_count,
  CASE
    WHEN m.stock_without_sales_sku_count > 0
      THEN 'Есть остатки без продаж. Возможны проблемы с карточкой, ценой или связкой SKU между витринами.'
    ELSE 'Критичных остатков без продаж не выявлено.'
  END AS stock_without_sales_note,
  m.duplicate_posting_count,
  CASE
    WHEN m.duplicate_posting_count > 0
      THEN 'Есть дубли ключевых документов. Это может приводить к двойному счету и искажению метрик.'
    ELSE 'Дубли ключевых документов не найдены.'
  END AS duplicate_posting_note,
  m.unattributed_row_count,
  m.unattributed_cost_total,
  CASE
    WHEN m.unattributed_row_count > 0
      THEN 'Есть операции, которые не удалось надежно разнести по SKU. Эти суммы учитываются отдельно и снижают уверенность в точности действий.'
    ELSE 'Неразнесенных операций не обнаружено.'
  END AS unattributed_note,
  m.fallback_attributed_row_count,
  m.fallback_attributed_cost_total,
  CASE
    WHEN m.fallback_attributed_row_count > 0
      THEN 'Часть расходов разнесена fallback-логикой (например, пропорционально выручке). Это прозрачная, но менее точная атрибуция.'
    ELSE 'Fallback-атрибуция не применялась.'
  END AS fallback_attributed_note,
  COALESCE(
    NULLIF(
      CONCAT_WS(
        '; ',
        CASE
          WHEN m.negative_revenue_sku_count > 0
            THEN 'Отрицательная выручка: ' || m.negative_revenue_sku_count::text || ' SKU.'
          ELSE NULL
        END,
        CASE
          WHEN m.zero_revenue_with_sales_sku_count > 0
            THEN 'Есть продажи при нулевой выручке: ' || m.zero_revenue_with_sales_sku_count::text || ' SKU.'
          ELSE NULL
        END,
        CASE
          WHEN m.negative_logistics_sku_count > 0
            THEN 'Отрицательная логистика: ' || m.negative_logistics_sku_count::text || ' SKU.'
          ELSE NULL
        END,
        CASE
          WHEN m.stock_without_sales_sku_count > 0
            THEN 'Остаток без продаж: ' || m.stock_without_sales_sku_count::text || ' SKU.'
          ELSE NULL
        END,
        CASE
          WHEN m.duplicate_posting_count > 0
            THEN 'Дубли документов: ' || m.duplicate_posting_count::text || '.'
          ELSE NULL
        END,
        CASE
          WHEN m.unattributed_row_count > 0
            THEN 'Неразнесено: ' || m.unattributed_row_count::text || ' строк на '
                 || ROUND(m.unattributed_cost_total, 2)::text || ' ₽.'
          ELSE NULL
        END,
        CASE
          WHEN m.fallback_attributed_row_count > 0
            THEN 'Fallback-атрибуция: ' || m.fallback_attributed_row_count::text || ' строк на '
                 || ROUND(m.fallback_attributed_cost_total, 2)::text || ' ₽.'
          ELSE NULL
        END
      ),
      ''
    ),
    'OK: критичных аномалий качества данных не обнаружено.'
  ) AS notes
INTO {{schema}}.mart_data_quality_30d
FROM metrics m;

CREATE INDEX mart_data_quality_30d_marketplace_idx
  ON {{schema}}.mart_data_quality_30d (marketplace, period_end DESC);
