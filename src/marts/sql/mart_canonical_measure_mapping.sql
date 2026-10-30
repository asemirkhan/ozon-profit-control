-- mart_canonical_measure_mapping
-- Canonical catalog of additive money measures across raw/stg entities.

DROP TABLE IF EXISTS {{schema}}.mart_canonical_measure_mapping;

SELECT
  v.marketplace,
  v.source,
  v.entity,
  v.raw_field,
  v.stg_field,
  v.money_type,
  v.field_role,
  v.granularity,
  v.sign_rule,
  v.notes,
  now() AS created_at
INTO {{schema}}.mart_canonical_measure_mapping
FROM (
  VALUES
    -- WB ads
    ('wb', 'ads', 'wb_ads', 'sum', 'spend', 'ads', 'canonical_total', 'day_campaign_nm', 'abs_cost', 'Core ad spend used for PnL'),
    ('wb', 'ads', 'wb_ads', 'sum_price', 'revenue', 'ads_revenue', 'derived_metric', 'day_campaign_nm', 'signed', 'Ad-attributed revenue metric'),
    ('wb', 'ads', 'wb_ads', 'cpc', NULL, 'ads_cpc', 'unit_price', 'campaign_or_phrase', 'non_additive', 'Reference CPC, not additive'),

    -- WB finance
    ('wb', 'finance', 'wb_finance', 'ppvz_sales_commission', 'commission_amount', 'commission', 'canonical_total', 'operation_document', 'abs_cost', 'Commission bucket'),
    ('wb', 'finance', 'wb_finance', 'rebill_logistic_cost', 'logistics_amount', 'logistics', 'canonical_total', 'operation_document', 'abs_cost', 'Logistics bucket'),
    ('wb', 'finance', 'wb_finance', 'service_fee_like', 'service_amount', 'services', 'canonical_total', 'operation_document', 'abs_cost', 'Services bucket'),
    ('wb', 'finance', 'wb_finance', 'amount', 'amount', 'other', 'component_amount', 'operation_document', 'abs_cost', 'Fallback amount split by operation_type'),

    -- WB sales/orders
    ('wb', 'sales', 'wb_sales', 'forPay', 'revenue_gross', 'revenue', 'canonical_total', 'sale_document', 'signed', 'Gross sale revenue'),
    ('wb', 'orders', 'wb_orders', 'salePrice', 'sale_price', 'order_value', 'reference_price', 'order_document', 'signed', 'Order-level value reference'),

    -- Ozon transactions
    ('ozon', 'transactions', 'ozon_transactions', 'sale_commission', 'sale_commission', 'commission', 'canonical_total', 'operation_document', 'signed', 'Commission from transactions'),
    ('ozon', 'transactions', 'ozon_transactions', 'delivery_charge', 'delivery_charge', 'logistics', 'canonical_total', 'operation_document', 'signed', 'Delivery charge'),
    ('ozon', 'transactions', 'ozon_transactions', 'return_delivery_charge', 'return_delivery_charge', 'logistics', 'component_amount', 'operation_document', 'signed', 'Return delivery charge'),
    ('ozon', 'transactions', 'ozon_transactions', 'service_price', 'service_price', 'services', 'canonical_total', 'operation_service', 'signed', 'Service rows from transaction service details'),
    ('ozon', 'transactions', 'ozon_transactions', 'amount', 'amount', 'other', 'component_amount', 'operation_document', 'signed', 'Fallback transaction amount'),

    -- Ozon postings
    ('ozon', 'postings', 'ozon_postings', 'commission_amount', 'commission_amount', 'commission', 'canonical_total', 'posting_item', 'signed', 'Posting-level commission'),
    ('ozon', 'postings', 'ozon_postings', 'item_services_total', 'item_services_total', 'services', 'canonical_total', 'posting_item', 'signed', 'Posting-level services total'),
    ('ozon', 'postings', 'ozon_postings', 'price', 'price', 'revenue', 'reference_price', 'posting_item', 'signed', 'Posting item price reference'),

    -- Ozon ads
    ('ozon', 'ads', 'ozon_ads', 'sum', 'spend', 'ads', 'canonical_total', 'day_campaign_item', 'abs_cost', 'Core ad spend used for PnL'),
    ('ozon', 'ads', 'ozon_ads', 'orders_money', 'revenue', 'ads_revenue', 'derived_metric', 'day_campaign_item', 'signed', 'Ad-attributed revenue metric')
) AS v(
  marketplace,
  source,
  entity,
  raw_field,
  stg_field,
  money_type,
  field_role,
  granularity,
  sign_rule,
  notes
);

CREATE INDEX mart_canonical_measure_mapping_idx
  ON {{schema}}.mart_canonical_measure_mapping (marketplace, source, entity, money_type);
