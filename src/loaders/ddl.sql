-- =========================
-- DDL v1 — Ozon/WB Profit Control
-- Postgres, schema-per-client
-- =========================

-- 0) schema
CREATE SCHEMA IF NOT EXISTS {{schema}};

-- 1) dim_product
CREATE TABLE IF NOT EXISTS {{schema}}.dim_product (
  offer_id        TEXT PRIMARY KEY,
  marketplace     TEXT NOT NULL DEFAULT 'ozon',
  product_id      TEXT NULL,
  sku             TEXT NULL,
  product_name    TEXT NULL,
  brand           TEXT NULL,
  category_id     TEXT NULL,
  category_name   TEXT NULL,
  archived        BOOLEAN NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.dim_product
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS dim_product_sku_idx
  ON {{schema}}.dim_product (sku);

CREATE INDEX IF NOT EXISTS dim_product_product_id_idx
  ON {{schema}}.dim_product (product_id);

CREATE INDEX IF NOT EXISTS dim_product_marketplace_idx
  ON {{schema}}.dim_product (marketplace);

-- 1a) dim_product_bridge (канонический мост идентификаторов товара)
CREATE TABLE IF NOT EXISTS {{schema}}.dim_product_bridge (
  bridge_id              BIGSERIAL PRIMARY KEY,
  marketplace            TEXT NOT NULL,
  canonical_product_key  TEXT NOT NULL,
  offer_id               TEXT NULL,
  sku                    TEXT NULL,
  product_id             TEXT NULL,
  nm_id                  TEXT NULL,
  vendor_code            TEXT NULL,
  barcode                TEXT NULL,
  item_code              TEXT NULL,
  product_name           TEXT NULL,
  category_name          TEXT NULL,
  brand                  TEXT NULL,
  match_confidence       TEXT NOT NULL DEFAULT 'low',
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dim_product_bridge_key_idx
  ON {{schema}}.dim_product_bridge (marketplace, canonical_product_key);

CREATE INDEX IF NOT EXISTS dim_product_bridge_offer_idx
  ON {{schema}}.dim_product_bridge (marketplace, offer_id);

CREATE INDEX IF NOT EXISTS dim_product_bridge_nm_idx
  ON {{schema}}.dim_product_bridge (marketplace, nm_id);

CREATE INDEX IF NOT EXISTS dim_product_bridge_sku_idx
  ON {{schema}}.dim_product_bridge (marketplace, sku);

-- 2) stg_posting
CREATE TABLE IF NOT EXISTS {{schema}}.stg_posting (
  posting_number  TEXT PRIMARY KEY,
  marketplace     TEXT NOT NULL DEFAULT 'ozon',
  status          TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL,
  in_process_at   TIMESTAMPTZ NULL,
  shipped_at      TIMESTAMPTZ NULL,
  delivered_at    TIMESTAMPTZ NULL,
  cancelled_at    TIMESTAMPTZ NULL,
  delivery_schema TEXT NULL,
  warehouse_id    TEXT NULL,
  customer_region TEXT NULL,
  is_multibox     BOOLEAN NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.stg_posting
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_posting_created_at_idx
  ON {{schema}}.stg_posting (created_at);

CREATE INDEX IF NOT EXISTS stg_posting_marketplace_idx
  ON {{schema}}.stg_posting (marketplace);

-- 3) stg_posting_item (товар в отправлении)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_posting_item (
  posting_number  TEXT NOT NULL,
  marketplace     TEXT NOT NULL DEFAULT 'ozon',
  offer_id        TEXT NOT NULL DEFAULT '~',
  sku             TEXT NOT NULL DEFAULT '~',
  product_id      TEXT NOT NULL DEFAULT '~',
  quantity        INTEGER NOT NULL,
  item_price      NUMERIC(18,2) NULL,
  currency        TEXT NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posting_number, offer_id, sku, product_id)
);

ALTER TABLE {{schema}}.stg_posting_item
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_posting_item_posting_idx
  ON {{schema}}.stg_posting_item (posting_number);

CREATE INDEX IF NOT EXISTS stg_posting_item_offer_idx
  ON {{schema}}.stg_posting_item (offer_id);

CREATE INDEX IF NOT EXISTS stg_posting_item_sku_idx
  ON {{schema}}.stg_posting_item (sku);

CREATE INDEX IF NOT EXISTS stg_posting_item_marketplace_idx
  ON {{schema}}.stg_posting_item (marketplace);

-- 4) stg_posting_item_financial (финансы по товару в отправлении)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_posting_item_financial (
  posting_number         TEXT NOT NULL,
  marketplace            TEXT NOT NULL DEFAULT 'ozon',
  product_id             TEXT NOT NULL,
  sku                    TEXT NULL,
  quantity               INTEGER NOT NULL,
  price                  NUMERIC(18,2) NULL,
  old_price              NUMERIC(18,2) NULL,
  total_discount_value   NUMERIC(18,2) NULL,
  total_discount_percent NUMERIC(9,4) NULL,
  commission_amount      NUMERIC(18,2) NULL,
  commission_percent     NUMERIC(9,4) NULL,
  payout                 NUMERIC(18,2) NULL,
  item_services_total    NUMERIC(18,2) NULL,
  raw_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posting_number, product_id)
);

ALTER TABLE {{schema}}.stg_posting_item_financial
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_posting_fin_sku_idx
  ON {{schema}}.stg_posting_item_financial (sku);

CREATE INDEX IF NOT EXISTS stg_posting_fin_marketplace_idx
  ON {{schema}}.stg_posting_item_financial (marketplace);

-- 5) stg_transaction (финансовые операции)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_transaction (
  operation_id           TEXT PRIMARY KEY,
  marketplace            TEXT NOT NULL DEFAULT 'ozon',
  operation_date         TIMESTAMPTZ NOT NULL,
  operation_type         TEXT NOT NULL,
  operation_type_name    TEXT NULL,
  posting_number         TEXT NULL,
  accruals_for_sale      NUMERIC(18,2) NULL,
  sale_commission        NUMERIC(18,2) NULL,
  delivery_charge        NUMERIC(18,2) NULL,
  return_delivery_charge NUMERIC(18,2) NULL,
  amount                 NUMERIC(18,2) NULL,
  raw_updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.stg_transaction
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_transaction_date_idx
  ON {{schema}}.stg_transaction (operation_date);

CREATE INDEX IF NOT EXISTS stg_transaction_posting_idx
  ON {{schema}}.stg_transaction (posting_number);

CREATE INDEX IF NOT EXISTS stg_transaction_marketplace_idx
  ON {{schema}}.stg_transaction (marketplace);

-- 6) stg_transaction_item (товары в транзакции)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_transaction_item (
  operation_id     TEXT NOT NULL,
  marketplace      TEXT NOT NULL DEFAULT 'ozon',
  sku              TEXT NOT NULL,
  item_name        TEXT NULL,
  raw_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (operation_id, sku)
);

ALTER TABLE {{schema}}.stg_transaction_item
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_transaction_item_marketplace_idx
  ON {{schema}}.stg_transaction_item (marketplace);

-- 7) stg_transaction_service (услуги в транзакции)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_transaction_service (
  operation_id     TEXT NOT NULL,
  marketplace      TEXT NOT NULL DEFAULT 'ozon',
  service_name     TEXT NOT NULL,
  service_price    NUMERIC(18,2) NOT NULL,
  raw_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (operation_id, service_name)
);

ALTER TABLE {{schema}}.stg_transaction_service
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS stg_transaction_service_marketplace_idx
  ON {{schema}}.stg_transaction_service (marketplace);

-- 8) fact_cogs (себестоимость)
CREATE TABLE IF NOT EXISTS {{schema}}.fact_cogs (
  offer_id              TEXT PRIMARY KEY,
  marketplace           TEXT NOT NULL DEFAULT 'ozon',
  unit_cogs             NUMERIC(18,2) NOT NULL,
  packaging_cost        NUMERIC(18,2) NULL,
  inbound_shipping_cost NUMERIC(18,2) NULL,
  other_variable_cost   NUMERIC(18,2) NULL,
  effective_from        DATE NULL,
  effective_to          DATE NULL,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.fact_cogs
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS fact_cogs_marketplace_idx
  ON {{schema}}.fact_cogs (marketplace);

-- 9) fact_ads_spend (реклама)
CREATE TABLE IF NOT EXISTS {{schema}}.fact_ads_spend (
  date            DATE NOT NULL,
  campaign_id     TEXT NOT NULL,
  marketplace     TEXT NOT NULL DEFAULT 'ozon',
  campaign_name   TEXT NULL,
  campaign_type   TEXT NULL,
  offer_id        TEXT NOT NULL DEFAULT '~',
  sku             TEXT NOT NULL DEFAULT '~',
  product_id      TEXT NOT NULL DEFAULT '~',
  app_type        TEXT NOT NULL DEFAULT '~',
  impressions     NUMERIC(18,2) NULL,
  clicks          NUMERIC(18,2) NULL,
  orders_count    NUMERIC(18,2) NULL,
  revenue         NUMERIC(18,2) NULL,
  spend           NUMERIC(18,2) NOT NULL,
  currency        TEXT NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, offer_id, sku, product_id, app_type)
);

ALTER TABLE {{schema}}.fact_ads_spend
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

ALTER TABLE {{schema}}.fact_ads_spend
  ADD COLUMN IF NOT EXISTS app_type TEXT NULL;

UPDATE {{schema}}.fact_ads_spend
SET app_type = '~'
WHERE app_type IS NULL;

ALTER TABLE {{schema}}.fact_ads_spend
  ALTER COLUMN app_type SET DEFAULT '~';

ALTER TABLE {{schema}}.fact_ads_spend
  ALTER COLUMN app_type SET NOT NULL;

ALTER TABLE {{schema}}.fact_ads_spend
  DROP CONSTRAINT IF EXISTS fact_ads_spend_pkey;

ALTER TABLE {{schema}}.fact_ads_spend
  ADD CONSTRAINT fact_ads_spend_pkey PRIMARY KEY (date, campaign_id, offer_id, sku, product_id, app_type);

CREATE INDEX IF NOT EXISTS fact_ads_spend_date_idx
  ON {{schema}}.fact_ads_spend (date);

CREATE INDEX IF NOT EXISTS fact_ads_spend_offer_idx
  ON {{schema}}.fact_ads_spend (offer_id);

CREATE INDEX IF NOT EXISTS fact_ads_spend_sku_idx
  ON {{schema}}.fact_ads_spend (sku);

CREATE INDEX IF NOT EXISTS fact_ads_spend_marketplace_idx
  ON {{schema}}.fact_ads_spend (marketplace);

-- 9aa) fact_product_money_attribution (единый слой денежных событий по SKU)
CREATE TABLE IF NOT EXISTS {{schema}}.fact_product_money_attribution (
  marketplace             TEXT NOT NULL,
  canonical_product_key   TEXT NOT NULL,
  event_date              DATE NOT NULL,
  money_component_type    TEXT NOT NULL,
  money_bucket            TEXT NOT NULL,
  amount                  NUMERIC(18,2) NOT NULL,
  attribution_method      TEXT NOT NULL,
  attribution_confidence  TEXT NOT NULL DEFAULT 'low',
  source_table            TEXT NOT NULL,
  source_record_id        TEXT NOT NULL,
  cogs_ratio_used         NUMERIC(9,4) NULL,
  cogs_source             TEXT NULL,
  cogs_confidence         TEXT NULL,
  loaded_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (marketplace, canonical_product_key, event_date, money_bucket, source_record_id)
);

CREATE INDEX IF NOT EXISTS fact_product_money_attr_date_idx
  ON {{schema}}.fact_product_money_attribution (event_date, marketplace);

CREATE INDEX IF NOT EXISTS fact_product_money_attr_offer_idx
  ON {{schema}}.fact_product_money_attribution (marketplace, canonical_product_key, money_bucket);

-- 9ab) fact_unmatched_money (явный слой неразнесенных/неполных денежных событий)
CREATE TABLE IF NOT EXISTS {{schema}}.fact_unmatched_money (
  unmatched_id                TEXT PRIMARY KEY,
  marketplace                 TEXT NOT NULL,
  source_entity               TEXT NOT NULL,
  money_type                  TEXT NOT NULL,
  amount                      NUMERIC(18,2) NOT NULL,
  amount_abs                  NUMERIC(18,2) NOT NULL,
  event_date                  DATE NOT NULL,
  raw_record_id               TEXT NOT NULL,
  campaign_id                 TEXT NULL,
  advert_id                   TEXT NULL,
  ad_id                       TEXT NULL,
  order_id                    TEXT NULL,
  posting_id                  TEXT NULL,
  document_id                 TEXT NULL,
  operation_id                TEXT NULL,
  nm_id                       TEXT NULL,
  sku                         TEXT NULL,
  offer_id                    TEXT NULL,
  barcode                     TEXT NULL,
  vendor_code                 TEXT NULL,
  supplier_article            TEXT NULL,
  product_name_raw            TEXT NULL,
  brand_raw                   TEXT NULL,
  subject_raw                 TEXT NULL,
  match_status                TEXT NOT NULL,
  unmatched_reason            TEXT NULL,
  candidate_mapping_reason    TEXT NULL,
  candidate_product_count     INTEGER NOT NULL DEFAULT 0,
  most_likely_product_id      TEXT NULL,
  most_likely_key_type        TEXT NULL,
  confidence_level            TEXT NOT NULL DEFAULT 'low',
  incompleteness_stage        TEXT NOT NULL DEFAULT 'match_gap',
  raw_json                    JSONB NULL,
  loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fact_unmatched_money_date_idx
  ON {{schema}}.fact_unmatched_money (marketplace, event_date);

CREATE INDEX IF NOT EXISTS fact_unmatched_money_type_idx
  ON {{schema}}.fact_unmatched_money (marketplace, money_type, match_status);

CREATE INDEX IF NOT EXISTS fact_unmatched_money_stage_idx
  ON {{schema}}.fact_unmatched_money (incompleteness_stage, unmatched_reason);

ALTER TABLE {{schema}}.fact_unmatched_money
  ALTER COLUMN incompleteness_stage SET DEFAULT 'match_gap';

-- 9a) product_cost_input (управляемый слой затрат по SKU/offer)
CREATE TABLE IF NOT EXISTS {{schema}}.product_cost_input (
  id                       BIGSERIAL PRIMARY KEY,
  marketplace              TEXT NOT NULL,
  offer_id                 TEXT NOT NULL DEFAULT '~',
  product_id               TEXT NULL,
  sku                      TEXT NULL,
  product_name_snapshot    TEXT NULL,
  cogs_purchase_amount     NUMERIC(18,2) NOT NULL DEFAULT 0,
  packaging_amount         NUMERIC(18,2) NOT NULL DEFAULT 0,
  labeling_amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
  inbound_logistics_amount NUMERIC(18,2) NOT NULL DEFAULT 0,
  assembly_amount          NUMERIC(18,2) NOT NULL DEFAULT 0,
  other_unit_cost_amount   NUMERIC(18,2) NOT NULL DEFAULT 0,
  currency                 TEXT NOT NULL DEFAULT 'RUB',
  valid_from               DATE NOT NULL,
  valid_to                 DATE NULL,
  source_type              TEXT NOT NULL DEFAULT 'manual',
  source_comment           TEXT NULL,
  updated_by               TEXT NULL,
  is_active                BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS product_cost_input_offer_idx
  ON {{schema}}.product_cost_input (marketplace, offer_id, is_active, valid_from DESC);

CREATE INDEX IF NOT EXISTS product_cost_input_sku_idx
  ON {{schema}}.product_cost_input (marketplace, sku, is_active, valid_from DESC);

CREATE INDEX IF NOT EXISTS product_cost_input_product_idx
  ON {{schema}}.product_cost_input (marketplace, product_id, is_active, valid_from DESC);

-- 9b) product_cost_overhead_input (периодические/накладные затраты)
CREATE TABLE IF NOT EXISTS {{schema}}.product_cost_overhead_input (
  id                  BIGSERIAL PRIMARY KEY,
  marketplace         TEXT NOT NULL,
  offer_id            TEXT NULL,
  overhead_type       TEXT NOT NULL,
  amount              NUMERIC(18,2) NOT NULL,
  allocation_method   TEXT NOT NULL DEFAULT 'per_unit',
  valid_from          DATE NOT NULL,
  valid_to            DATE NULL,
  source_comment      TEXT NULL,
  updated_by          TEXT NULL,
  is_active           BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS product_cost_overhead_offer_idx
  ON {{schema}}.product_cost_overhead_input (marketplace, offer_id, is_active, valid_from DESC);

-- 9c) product_cost_input_history (аудит изменений cost input)
CREATE TABLE IF NOT EXISTS {{schema}}.product_cost_input_history (
  history_id               BIGSERIAL PRIMARY KEY,
  cost_input_id            BIGINT NOT NULL,
  changed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  changed_by               TEXT NULL,
  change_type              TEXT NOT NULL,
  old_payload              JSONB NULL,
  new_payload              JSONB NULL
);

CREATE INDEX IF NOT EXISTS product_cost_input_history_cost_idx
  ON {{schema}}.product_cost_input_history (cost_input_id, changed_at DESC);

-- 9d) product_replenishment_input (ручной слой планируемых пополнений)
CREATE TABLE IF NOT EXISTS {{schema}}.product_replenishment_input (
  id                          BIGSERIAL PRIMARY KEY,
  marketplace                 TEXT NOT NULL,
  offer_id                    TEXT NOT NULL DEFAULT '~',
  expected_replenishment_date DATE NULL,
  planned_replenishment_qty   NUMERIC(18,2) NULL,
  replenishment_status        TEXT NOT NULL DEFAULT 'unknown',
  source_comment              TEXT NULL,
  updated_by                  TEXT NULL,
  is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS product_replenishment_offer_idx
  ON {{schema}}.product_replenishment_input (marketplace, offer_id, is_active, updated_at DESC);

-- 10) fact_inventory_snapshot (остатки по товарам и складам)
CREATE TABLE IF NOT EXISTS {{schema}}.fact_inventory_snapshot (
  snapshot_date    DATE NOT NULL,
  marketplace      TEXT NOT NULL DEFAULT 'ozon',
  offer_id         TEXT NOT NULL DEFAULT '~',
  sku              TEXT NOT NULL DEFAULT '~',
  product_id       TEXT NOT NULL DEFAULT '~',
  warehouse_name   TEXT NOT NULL DEFAULT '~',
  warehouse_type   TEXT NULL,
  stock_total      NUMERIC NOT NULL DEFAULT 0,
  stock_available  NUMERIC NULL,
  stock_reserved   NUMERIC NULL,
  stock_defect     NUMERIC NULL,
  raw_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, offer_id, sku, product_id, warehouse_name)
);

ALTER TABLE {{schema}}.fact_inventory_snapshot
  ADD COLUMN IF NOT EXISTS marketplace TEXT NOT NULL DEFAULT 'ozon';

CREATE INDEX IF NOT EXISTS fact_inventory_snapshot_date_idx
  ON {{schema}}.fact_inventory_snapshot (snapshot_date);

CREATE INDEX IF NOT EXISTS fact_inventory_snapshot_offer_idx
  ON {{schema}}.fact_inventory_snapshot (offer_id);

CREATE INDEX IF NOT EXISTS fact_inventory_snapshot_marketplace_idx
  ON {{schema}}.fact_inventory_snapshot (marketplace);

-- 11) WB staging: products
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_product (
  nm_id           TEXT PRIMARY KEY,
  marketplace     TEXT NOT NULL DEFAULT 'wb',
  offer_id        TEXT NOT NULL DEFAULT '~',
  vendor_code     TEXT NULL,
  barcode         TEXT NULL,
  product_name    TEXT NULL,
  brand           TEXT NULL,
  category_name   TEXT NULL,
  subject_id      TEXT NULL,
  subject_name    TEXT NULL,
  length_cm       NUMERIC(18,4) NULL,
  width_cm        NUMERIC(18,4) NULL,
  height_cm       NUMERIC(18,4) NULL,
  weight_brutto_kg NUMERIC(18,4) NULL,
  characteristics_json JSONB NULL,
  raw_payload     JSONB NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS subject_id TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS subject_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS length_cm NUMERIC(18,4) NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS width_cm NUMERIC(18,4) NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS height_cm NUMERIC(18,4) NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS weight_brutto_kg NUMERIC(18,4) NULL;

ALTER TABLE {{schema}}.stg_wb_product
  ADD COLUMN IF NOT EXISTS characteristics_json JSONB NULL;

CREATE INDEX IF NOT EXISTS stg_wb_product_offer_idx
  ON {{schema}}.stg_wb_product (offer_id);

-- 11.1) WB staging: competitor snapshot (public search capture)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_competitor_snapshot (
  own_nm_id           TEXT NOT NULL,
  competitor_nm_id    TEXT NOT NULL,
  marketplace         TEXT NOT NULL DEFAULT 'wb',
  search_query        TEXT NOT NULL DEFAULT '~',
  competitor_name     TEXT NULL,
  competitor_price    NUMERIC(18,2) NULL,
  competitor_rating   NUMERIC(8,3) NULL,
  competitor_reviews  NUMERIC(18,2) NULL,
  position            INTEGER NULL,
  captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (own_nm_id, competitor_nm_id, search_query)
);

CREATE INDEX IF NOT EXISTS stg_wb_competitor_snapshot_own_idx
  ON {{schema}}.stg_wb_competitor_snapshot (own_nm_id);

CREATE INDEX IF NOT EXISTS stg_wb_competitor_snapshot_query_idx
  ON {{schema}}.stg_wb_competitor_snapshot (search_query);

-- 11.2) WB staging: product card snapshot (public card meta)
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_product_card_snapshot (
  nm_id                 TEXT PRIMARY KEY,
  marketplace           TEXT NOT NULL DEFAULT 'wb',
  product_title         TEXT NULL,
  brand                 TEXT NULL,
  rating                NUMERIC(8,3) NULL,
  feedbacks             NUMERIC(18,2) NULL,
  photo_count           INTEGER NULL,
  has_video             BOOLEAN NOT NULL DEFAULT FALSE,
  characteristics_count INTEGER NULL,
  captured_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stg_wb_product_card_snapshot_marketplace_idx
  ON {{schema}}.stg_wb_product_card_snapshot (marketplace);

ALTER TABLE {{schema}}.stg_wb_product_card_snapshot
  ADD COLUMN IF NOT EXISTS search_query TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_product_card_snapshot
  ADD COLUMN IF NOT EXISTS search_position INTEGER NULL;

-- Append-only observations.  The staging tables above remain the fast current
-- snapshot; these tables preserve the daily history used for trends.
CREATE TABLE IF NOT EXISTS {{schema}}.fact_wb_competitor_observation (
  observation_date    DATE NOT NULL,
  own_nm_id           TEXT NOT NULL,
  competitor_nm_id    TEXT NOT NULL,
  marketplace         TEXT NOT NULL DEFAULT 'wb',
  search_query        TEXT NOT NULL,
  competitor_name     TEXT NULL,
  competitor_price    NUMERIC(18,2) NULL,
  competitor_rating   NUMERIC(8,3) NULL,
  competitor_reviews  NUMERIC(18,2) NULL,
  search_position     INTEGER NULL,
  captured_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (observation_date, own_nm_id, competitor_nm_id, search_query)
);

CREATE INDEX IF NOT EXISTS fact_wb_competitor_observation_lookup_idx
  ON {{schema}}.fact_wb_competitor_observation (own_nm_id, observation_date DESC);

CREATE TABLE IF NOT EXISTS {{schema}}.fact_wb_product_card_observation (
  observation_date      DATE NOT NULL,
  nm_id                 TEXT NOT NULL,
  marketplace           TEXT NOT NULL DEFAULT 'wb',
  product_title         TEXT NULL,
  brand                 TEXT NULL,
  rating                NUMERIC(8,3) NULL,
  feedbacks             NUMERIC(18,2) NULL,
  photo_count           INTEGER NULL,
  has_video             BOOLEAN NOT NULL DEFAULT FALSE,
  characteristics_count INTEGER NULL,
  search_query          TEXT NULL,
  search_position       INTEGER NULL,
  captured_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (observation_date, nm_id)
);

CREATE INDEX IF NOT EXISTS fact_wb_product_card_observation_lookup_idx
  ON {{schema}}.fact_wb_product_card_observation (nm_id, observation_date DESC);

-- 12) WB staging: orders
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_order (
  order_id        TEXT PRIMARY KEY,
  marketplace     TEXT NOT NULL DEFAULT 'wb',
  order_date      TIMESTAMPTZ NOT NULL,
  order_status    TEXT NULL,
  cancel_dt       TIMESTAMPTZ NULL,
  nm_id           TEXT NULL,
  offer_id        TEXT NOT NULL DEFAULT '~',
  barcode         TEXT NULL,
  quantity        INTEGER NOT NULL DEFAULT 1,
  sale_price      NUMERIC(18,2) NULL,
  warehouse_name  TEXT NULL,
  warehouse_type  TEXT NULL,
  region_name     TEXT NULL,
  tech_size       TEXT NULL,
  subject_name    TEXT NULL,
  supplier_article TEXT NULL,
  raw_payload     JSONB NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS warehouse_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS warehouse_type TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS region_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS tech_size TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS subject_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_order
  ADD COLUMN IF NOT EXISTS supplier_article TEXT NULL;

CREATE INDEX IF NOT EXISTS stg_wb_order_date_idx
  ON {{schema}}.stg_wb_order (order_date);

CREATE INDEX IF NOT EXISTS stg_wb_order_offer_idx
  ON {{schema}}.stg_wb_order (offer_id);

-- 12b) WB staging: paid storage report
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_paid_storage (
  source_record_id   TEXT PRIMARY KEY,
  marketplace        TEXT NOT NULL DEFAULT 'wb',
  report_date        DATE NOT NULL,
  offer_id           TEXT NOT NULL DEFAULT '~',
  nm_id              TEXT NULL,
  barcode            TEXT NULL,
  vendor_code        TEXT NULL,
  subject_name       TEXT NULL,
  brand              TEXT NULL,
  size               TEXT NULL,
  warehouse_name     TEXT NULL,
  office_id          TEXT NULL,
  gi_id              TEXT NULL,
  chrt_id            TEXT NULL,
  volume             NUMERIC(18,6) NULL,
  calc_type          TEXT NULL,
  warehouse_price    NUMERIC(18,2) NOT NULL DEFAULT 0,
  warehouse_coef     NUMERIC(18,6) NULL,
  log_warehouse_coef NUMERIC(18,6) NULL,
  barcodes_count     NUMERIC(18,2) NULL,
  pallet_place_code  NUMERIC(18,2) NULL,
  pallet_count       NUMERIC(18,2) NULL,
  loyalty_discount   NUMERIC(18,2) NULL,
  original_date      DATE NULL,
  tariff_fix_date    DATE NULL,
  tariff_lower_date  DATE NULL,
  raw_payload        JSONB NULL,
  raw_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stg_wb_paid_storage_date_idx
  ON {{schema}}.stg_wb_paid_storage (report_date);

CREATE INDEX IF NOT EXISTS stg_wb_paid_storage_offer_idx
  ON {{schema}}.stg_wb_paid_storage (offer_id);

CREATE INDEX IF NOT EXISTS stg_wb_paid_storage_nm_idx
  ON {{schema}}.stg_wb_paid_storage (nm_id);

CREATE INDEX IF NOT EXISTS stg_wb_paid_storage_barcode_idx
  ON {{schema}}.stg_wb_paid_storage (barcode);

-- 13) WB staging: sales
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_sale (
  sale_id         TEXT PRIMARY KEY,
  marketplace     TEXT NOT NULL DEFAULT 'wb',
  sale_date       TIMESTAMPTZ NOT NULL,
  order_id        TEXT NULL,
  sale_status     TEXT NULL,
  is_return       BOOLEAN NULL,
  nm_id           TEXT NULL,
  offer_id        TEXT NOT NULL DEFAULT '~',
  barcode         TEXT NULL,
  quantity        INTEGER NOT NULL DEFAULT 1,
  revenue_gross   NUMERIC(18,2) NULL,
  warehouse_name  TEXT NULL,
  region_name     TEXT NULL,
  tech_size       TEXT NULL,
  subject_name    TEXT NULL,
  supplier_article TEXT NULL,
  raw_payload     JSONB NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.stg_wb_sale
  ADD COLUMN IF NOT EXISTS warehouse_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_sale
  ADD COLUMN IF NOT EXISTS region_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_sale
  ADD COLUMN IF NOT EXISTS tech_size TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_sale
  ADD COLUMN IF NOT EXISTS subject_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_sale
  ADD COLUMN IF NOT EXISTS supplier_article TEXT NULL;

CREATE INDEX IF NOT EXISTS stg_wb_sale_date_idx
  ON {{schema}}.stg_wb_sale (sale_date);

CREATE INDEX IF NOT EXISTS stg_wb_sale_offer_idx
  ON {{schema}}.stg_wb_sale (offer_id);

-- 14) WB staging: finance operations
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_finance (
  finance_id         TEXT PRIMARY KEY,
  marketplace        TEXT NOT NULL DEFAULT 'wb',
  operation_date     TIMESTAMPTZ NOT NULL,
  operation_date_source TEXT NULL,
  source_operation_date TIMESTAMPTZ NULL,
  operation_type     TEXT NULL,
  order_id           TEXT NULL,
  sale_id            TEXT NULL,
  nm_id              TEXT NULL,
  offer_id           TEXT NOT NULL DEFAULT '~',
  barcode            TEXT NULL,
  payout_amount      NUMERIC(18,2) NULL,
  commission_amount  NUMERIC(18,2) NULL,
  logistics_amount   NUMERIC(18,2) NULL,
  service_amount     NUMERIC(18,2) NULL,
  amount             NUMERIC(18,2) NULL,
  rrd_id             BIGINT NULL,
  rr_dt              TIMESTAMPTZ NULL,
  sale_dt            TIMESTAMPTZ NULL,
  order_dt           TIMESTAMPTZ NULL,
  doc_type_name      TEXT NULL,
  supplier_oper_name TEXT NULL,
  delivery_rub       NUMERIC(18,2) NULL,
  rebill_logistic_cost NUMERIC(18,2) NULL,
  storage_fee        NUMERIC(18,2) NULL,
  acceptance_amount  NUMERIC(18,2) NULL,
  penalty_amount     NUMERIC(18,2) NULL,
  deduction_amount   NUMERIC(18,2) NULL,
  acquiring_fee      NUMERIC(18,2) NULL,
  commission_percent NUMERIC(9,4) NULL,
  office_name        TEXT NULL,
  gi_box_type_name   TEXT NULL,
  subject_name       TEXT NULL,
  raw_payload        JSONB NULL,
  raw_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stg_wb_finance_date_idx
  ON {{schema}}.stg_wb_finance (operation_date);

CREATE INDEX IF NOT EXISTS stg_wb_finance_offer_idx
  ON {{schema}}.stg_wb_finance (offer_id);

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS barcode TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS operation_date_source TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS source_operation_date TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS rrd_id BIGINT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS rr_dt TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS sale_dt TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS order_dt TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS doc_type_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS supplier_oper_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS delivery_rub NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS rebill_logistic_cost NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS storage_fee NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS acceptance_amount NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS penalty_amount NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS deduction_amount NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS acquiring_fee NUMERIC(18,2) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS commission_percent NUMERIC(9,4) NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS office_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS gi_box_type_name TEXT NULL;

ALTER TABLE {{schema}}.stg_wb_finance
  ADD COLUMN IF NOT EXISTS subject_name TEXT NULL;

CREATE INDEX IF NOT EXISTS stg_wb_finance_barcode_idx
  ON {{schema}}.stg_wb_finance (barcode);

CREATE INDEX IF NOT EXISTS stg_wb_finance_date_source_idx
  ON {{schema}}.stg_wb_finance (operation_date_source);

-- 15) WB staging: ads
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_ads (
  date            DATE NOT NULL,
  campaign_id     TEXT NOT NULL,
  marketplace     TEXT NOT NULL DEFAULT 'wb',
  campaign_name   TEXT NULL,
  campaign_type   TEXT NULL,
  offer_id        TEXT NOT NULL DEFAULT '~',
  nm_id           TEXT NOT NULL DEFAULT '~',
  app_type        TEXT NOT NULL DEFAULT '~',
  impressions     NUMERIC(18,2) NULL,
  clicks          NUMERIC(18,2) NULL,
  ctr             NUMERIC(18,6) NULL,
  cpc             NUMERIC(18,6) NULL,
  cr              NUMERIC(18,6) NULL,
  atbs            NUMERIC(18,2) NULL,
  orders_count    NUMERIC(18,2) NULL,
  revenue         NUMERIC(18,2) NULL,
  spend           NUMERIC(18,2) NOT NULL,
  currency        TEXT NULL,
  raw_payload     JSONB NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, offer_id, nm_id, app_type)
);

ALTER TABLE {{schema}}.stg_wb_ads
  ALTER COLUMN nm_id SET DEFAULT '~';

ALTER TABLE {{schema}}.stg_wb_ads
  ADD COLUMN IF NOT EXISTS app_type TEXT NULL;

UPDATE {{schema}}.stg_wb_ads
SET app_type = '~'
WHERE app_type IS NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ALTER COLUMN app_type SET DEFAULT '~';

ALTER TABLE {{schema}}.stg_wb_ads
  ALTER COLUMN app_type SET NOT NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ADD COLUMN IF NOT EXISTS ctr NUMERIC(18,6) NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ADD COLUMN IF NOT EXISTS cpc NUMERIC(18,6) NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ADD COLUMN IF NOT EXISTS cr NUMERIC(18,6) NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ADD COLUMN IF NOT EXISTS atbs NUMERIC(18,2) NULL;

UPDATE {{schema}}.stg_wb_ads
SET nm_id = '~'
WHERE nm_id IS NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  ALTER COLUMN nm_id SET NOT NULL;

ALTER TABLE {{schema}}.stg_wb_ads
  DROP CONSTRAINT IF EXISTS stg_wb_ads_pkey;

ALTER TABLE {{schema}}.stg_wb_ads
  ADD CONSTRAINT stg_wb_ads_pkey PRIMARY KEY (date, campaign_id, offer_id, nm_id, app_type);

CREATE INDEX IF NOT EXISTS stg_wb_ads_date_idx
  ON {{schema}}.stg_wb_ads (date);

CREATE INDEX IF NOT EXISTS stg_wb_ads_offer_idx
  ON {{schema}}.stg_wb_ads (offer_id);

CREATE INDEX IF NOT EXISTS stg_wb_ads_nm_idx
  ON {{schema}}.stg_wb_ads (nm_id);

-- 16) WB staging: stock
CREATE TABLE IF NOT EXISTS {{schema}}.stg_wb_stock (
  snapshot_date   DATE NOT NULL,
  marketplace     TEXT NOT NULL DEFAULT 'wb',
  offer_id        TEXT NOT NULL DEFAULT '~',
  nm_id           TEXT NULL,
  barcode         TEXT NULL,
  warehouse_name  TEXT NOT NULL DEFAULT '~',
  warehouse_type  TEXT NULL,
  stock_total     NUMERIC(18,2) NOT NULL DEFAULT 0,
  stock_available NUMERIC(18,2) NULL,
  stock_reserved  NUMERIC(18,2) NULL,
  stock_defect    NUMERIC(18,2) NULL,
  raw_payload     JSONB NULL,
  raw_updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (snapshot_date, offer_id, warehouse_name)
);

CREATE INDEX IF NOT EXISTS stg_wb_stock_date_idx
  ON {{schema}}.stg_wb_stock (snapshot_date);

CREATE INDEX IF NOT EXISTS stg_wb_stock_offer_idx
  ON {{schema}}.stg_wb_stock (offer_id);

-- 16a) ingestion_run (единый аудит запусков ingestion framework)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_run (
  run_id       TEXT PRIMARY KEY,
  marketplace  TEXT NOT NULL,
  source       TEXT NOT NULL,
  schema_name  TEXT NOT NULL,
  mode         TEXT NOT NULL,
  date_from    DATE NULL,
  date_to      DATE NULL,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ NULL,
  status       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ingestion_run_src_idx
  ON {{schema}}.ingestion_run (marketplace, source, started_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_run_status_idx
  ON {{schema}}.ingestion_run (status, started_at DESC);

-- 16b) ingestion_chunk_audit (чанки / страницы / потери по запуску)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_chunk_audit (
  run_id                 TEXT NOT NULL,
  chunk_id               TEXT NOT NULL,
  chunk_date_from        DATE NULL,
  chunk_date_to          DATE NULL,
  retry_attempts         INTEGER NOT NULL DEFAULT 1,
  page_count             INTEGER NOT NULL DEFAULT 0,
  raw_files_count        INTEGER NOT NULL DEFAULT 0,
  raw_rows_count         INTEGER NOT NULL DEFAULT 0,
  normalized_rows_count  INTEGER NOT NULL DEFAULT 0,
  loaded_rows_count      INTEGER NOT NULL DEFAULT 0,
  status                 TEXT NOT NULL,
  error_type             TEXT NULL,
  error_message          TEXT NULL,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, chunk_id)
);

ALTER TABLE {{schema}}.ingestion_chunk_audit
  ADD COLUMN IF NOT EXISTS retry_attempts INTEGER NOT NULL DEFAULT 1;

ALTER TABLE {{schema}}.ingestion_chunk_audit
  ADD COLUMN IF NOT EXISTS error_type TEXT NULL;

CREATE INDEX IF NOT EXISTS ingestion_chunk_status_idx
  ON {{schema}}.ingestion_chunk_audit (status, created_at DESC);

-- 16bb) ingestion_request_log (request-level observability for source availability/rate limits)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_request_log (
  request_id              BIGSERIAL PRIMARY KEY,
  run_id                  TEXT NOT NULL,
  chunk_id                TEXT NOT NULL,
  marketplace             TEXT NOT NULL,
  source                  TEXT NOT NULL,
  endpoint                TEXT NOT NULL DEFAULT '',
  request_url             TEXT NOT NULL DEFAULT '',
  target_date             DATE NULL,
  request_attempt_no      INTEGER NOT NULL DEFAULT 1,
  request_started_at      TIMESTAMPTZ NOT NULL,
  request_finished_at     TIMESTAMPTZ NOT NULL,
  http_status             INTEGER NULL,
  response_body_size      INTEGER NOT NULL DEFAULT 0,
  response_content_type   TEXT NULL,
  success                 BOOLEAN NOT NULL DEFAULT FALSE,
  error_type              TEXT NULL,
  error_message           TEXT NULL,
  rate_limit_retry        TEXT NULL,
  rate_limit_limit        TEXT NULL,
  rate_limit_reset        TEXT NULL,
  cooldown_wait_sec       NUMERIC(12,3) NOT NULL DEFAULT 0,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestion_request_log_run_idx
  ON {{schema}}.ingestion_request_log (run_id, chunk_id, request_attempt_no);

CREATE INDEX IF NOT EXISTS ingestion_request_log_src_idx
  ON {{schema}}.ingestion_request_log (marketplace, source, target_date, created_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_request_log_status_idx
  ON {{schema}}.ingestion_request_log (http_status, error_type, created_at DESC);

-- 16bc) rate_limit_headers (explicit storage of provider ratelimit headers)
CREATE TABLE IF NOT EXISTS {{schema}}.rate_limit_headers (
  id                    BIGSERIAL PRIMARY KEY,
  run_id                TEXT NOT NULL,
  chunk_id              TEXT NOT NULL,
  marketplace           TEXT NOT NULL,
  source                TEXT NOT NULL,
  endpoint              TEXT NOT NULL DEFAULT '',
  target_date           DATE NULL,
  request_attempt_no    INTEGER NOT NULL DEFAULT 1,
  http_status           INTEGER NULL,
  x_ratelimit_retry     TEXT NULL,
  x_ratelimit_limit     TEXT NULL,
  x_ratelimit_reset     TEXT NULL,
  observed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rate_limit_headers_src_idx
  ON {{schema}}.rate_limit_headers (marketplace, source, target_date, observed_at DESC);

CREATE INDEX IF NOT EXISTS rate_limit_headers_status_idx
  ON {{schema}}.rate_limit_headers (http_status, observed_at DESC);

-- 16c) ingestion_completeness (результат покрытия/полноты загрузки)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_completeness (
  run_id              TEXT PRIMARY KEY,
  marketplace         TEXT NOT NULL,
  source              TEXT NOT NULL,
  schema_name         TEXT NOT NULL,
  expected_days       INTEGER NOT NULL DEFAULT 0,
  actual_days         INTEGER NOT NULL DEFAULT 0,
  missing_days_count  INTEGER NOT NULL DEFAULT 0,
  coverage_ratio      NUMERIC(9,6) NOT NULL DEFAULT 0,
  expected_entities   INTEGER NOT NULL DEFAULT 0,
  actual_entities     INTEGER NOT NULL DEFAULT 0,
  row_loss_ratio      NUMERIC(9,6) NOT NULL DEFAULT 0,
  source_criticality  TEXT NOT NULL DEFAULT 'noncritical',
  usable_for_publish  BOOLEAN NOT NULL DEFAULT FALSE,
  error_type          TEXT NULL,
  retry_exhausted     BOOLEAN NOT NULL DEFAULT FALSE,
  partial_reason      TEXT NULL,
  status              TEXT NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS source_criticality TEXT NOT NULL DEFAULT 'noncritical';

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS usable_for_publish BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS error_type TEXT NULL;

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS retry_exhausted BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS partial_reason TEXT NULL;

ALTER TABLE {{schema}}.ingestion_completeness
  ADD COLUMN IF NOT EXISTS coverage_ratio NUMERIC(9,6) NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ingestion_completeness_src_idx
  ON {{schema}}.ingestion_completeness (marketplace, source, created_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_completeness_status_idx
  ON {{schema}}.ingestion_completeness (status, created_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_completeness_publish_idx
  ON {{schema}}.ingestion_completeness (source_criticality, usable_for_publish, created_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_completeness_coverage_idx
  ON {{schema}}.ingestion_completeness (coverage_ratio, created_at DESC);

-- 16d) ingestion_snapshot_state (жизненный цикл snapshot + publish gate)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_snapshot_state (
  snapshot_id          TEXT PRIMARY KEY,
  schema_name          TEXT NOT NULL,
  marketplace          TEXT NOT NULL,
  period_from          DATE NOT NULL,
  period_to            DATE NOT NULL,
  required_sources     JSONB NOT NULL DEFAULT '[]'::jsonb,
  completed_sources    JSONB NOT NULL DEFAULT '[]'::jsonb,
  failed_sources       JSONB NOT NULL DEFAULT '[]'::jsonb,
  warning_sources      JSONB NOT NULL DEFAULT '[]'::jsonb,
  completeness_status  TEXT NOT NULL,
  publish_status       TEXT NOT NULL,
  published_at         TIMESTAMPTZ NULL,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ingestion_snapshot_state_period_uq
  ON {{schema}}.ingestion_snapshot_state (schema_name, marketplace, period_from, period_to);

CREATE INDEX IF NOT EXISTS ingestion_snapshot_state_status_idx
  ON {{schema}}.ingestion_snapshot_state (publish_status, updated_at DESC);

-- 16e) finance_backfill_queue (дни wb finance для автодогруза)
CREATE TABLE IF NOT EXISTS {{schema}}.finance_backfill_queue (
  schema_name      TEXT NOT NULL,
  marketplace      TEXT NOT NULL,
  source           TEXT NOT NULL,
  target_date      DATE NOT NULL,
  retry_count      INTEGER NOT NULL DEFAULT 0,
  last_attempt_at  TIMESTAMPTZ NULL,
  status           TEXT NOT NULL DEFAULT 'pending',
  last_error_type  TEXT NULL,
  last_error       TEXT NULL,
  run_id           TEXT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, source, target_date)
);

CREATE INDEX IF NOT EXISTS finance_backfill_queue_status_idx
  ON {{schema}}.finance_backfill_queue (status, target_date, updated_at DESC);

-- 16ea) finance_unstable_days (дни finance с provider instability до raw ingestion)
CREATE TABLE IF NOT EXISTS {{schema}}.finance_unstable_days (
  schema_name        TEXT NOT NULL,
  marketplace        TEXT NOT NULL,
  source             TEXT NOT NULL,
  target_date        DATE NOT NULL,
  attempts           INTEGER NOT NULL DEFAULT 0,
  next_retry_at      TIMESTAMPTZ NULL,
  last_error_type    TEXT NULL,
  last_error         TEXT NULL,
  raw_rows           BIGINT NOT NULL DEFAULT 0,
  normalized_rows    BIGINT NOT NULL DEFAULT 0,
  loaded_rows        BIGINT NOT NULL DEFAULT 0,
  stg_rows           BIGINT NOT NULL DEFAULT 0,
  run_id             TEXT NULL,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, source, target_date)
);

CREATE INDEX IF NOT EXISTS finance_unstable_days_retry_idx
  ON {{schema}}.finance_unstable_days (next_retry_at, updated_at DESC);

-- 16f) ingestion_backfill_queue (универсальная очередь догруза по дням)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_backfill_queue (
  schema_name       TEXT NOT NULL,
  marketplace       TEXT NOT NULL,
  source            TEXT NOT NULL,
  target_date       DATE NOT NULL,
  lane              TEXT NOT NULL DEFAULT 'backfill',
  priority          INTEGER NOT NULL DEFAULT 100,
  state             TEXT NOT NULL DEFAULT 'pending',
  attempts          INTEGER NOT NULL DEFAULT 0,
  max_attempts      INTEGER NOT NULL DEFAULT 30,
  worker_id         TEXT NULL,
  leased_at         TIMESTAMPTZ NULL,
  lease_expires_at  TIMESTAMPTZ NULL,
  heartbeat_at      TIMESTAMPTZ NULL,
  order_resume_offset BIGINT NOT NULL DEFAULT 0,
  order_pages_done    INTEGER NOT NULL DEFAULT 0,
  order_rows_done     BIGINT NOT NULL DEFAULT 0,
  order_last_progress_at TIMESTAMPTZ NULL,
  order_no_progress_passes INTEGER NOT NULL DEFAULT 0,
  finance_resume_rrdid BIGINT NOT NULL DEFAULT 0,
  finance_pages_done INTEGER NOT NULL DEFAULT 0,
  finance_rows_done BIGINT NOT NULL DEFAULT 0,
  finance_last_progress_at TIMESTAMPTZ NULL,
  finance_no_progress_passes INTEGER NOT NULL DEFAULT 0,
  retry_after_ts    TIMESTAMPTZ NULL,
  last_attempt_at   TIMESTAMPTZ NULL,
  last_http_status  INTEGER NULL,
  last_error_type   TEXT NULL,
  last_error        TEXT NULL,
  day_close_reason  TEXT NULL,
  rate_limit_retry  TEXT NULL,
  rate_limit_limit  TEXT NULL,
  rate_limit_reset  TEXT NULL,
  run_id            TEXT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, source, target_date)
);

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS worker_id TEXT NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS leased_at TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS order_resume_offset BIGINT NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS order_pages_done INTEGER NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS order_rows_done BIGINT NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS order_last_progress_at TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS order_no_progress_passes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS finance_resume_rrdid BIGINT NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS finance_pages_done INTEGER NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS finance_rows_done BIGINT NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS finance_last_progress_at TIMESTAMPTZ NULL;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS finance_no_progress_passes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE {{schema}}.ingestion_backfill_queue
  ADD COLUMN IF NOT EXISTS day_close_reason TEXT NULL;

CREATE INDEX IF NOT EXISTS ingestion_backfill_queue_state_idx
  ON {{schema}}.ingestion_backfill_queue (marketplace, source, state, retry_after_ts, priority, target_date);

CREATE INDEX IF NOT EXISTS ingestion_backfill_queue_status_idx
  ON {{schema}}.ingestion_backfill_queue (state, updated_at DESC);

CREATE INDEX IF NOT EXISTS ingestion_backfill_queue_lease_idx
  ON {{schema}}.ingestion_backfill_queue (state, lease_expires_at, heartbeat_at);

CREATE TABLE IF NOT EXISTS {{schema}}.finance_page_audit (
  schema_name TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  source TEXT NOT NULL,
  target_date DATE NOT NULL,
  run_id TEXT NOT NULL,
  chunk_id TEXT NOT NULL,
  request_no INTEGER NOT NULL,
  rrdid_request BIGINT NOT NULL DEFAULT 0,
  http_status INTEGER NULL,
  rows_in_response INTEGER NOT NULL DEFAULT 0,
  max_rrdid_in_response BIGINT NULL,
  response_body_size INTEGER NOT NULL DEFAULT 0,
  request_error_type TEXT NULL,
  is_terminal_page BOOLEAN NOT NULL DEFAULT FALSE,
  terminal_reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, source, target_date, run_id, chunk_id, request_no)
);

CREATE INDEX IF NOT EXISTS finance_page_audit_day_idx
  ON {{schema}}.finance_page_audit (target_date, run_id, request_no);

CREATE TABLE IF NOT EXISTS {{schema}}.finance_raw_vs_normalized_audit (
  schema_name TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  target_date DATE NOT NULL,
  run_id TEXT NOT NULL,
  raw_rows INTEGER NOT NULL DEFAULT 0,
  normalized_rows INTEGER NOT NULL DEFAULT 0,
  dropped_rows INTEGER NOT NULL DEFAULT 0,
  drop_reason TEXT NOT NULL,
  operation_date_source TEXT NULL,
  rrd_id_min BIGINT NULL,
  rrd_id_max BIGINT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, target_date, run_id, drop_reason, operation_date_source)
);

CREATE INDEX IF NOT EXISTS finance_raw_vs_normalized_day_idx
  ON {{schema}}.finance_raw_vs_normalized_audit (target_date, run_id);

CREATE TABLE IF NOT EXISTS {{schema}}.finance_reconciliation_day (
  schema_name TEXT NOT NULL,
  marketplace TEXT NOT NULL,
  target_date DATE NOT NULL,
  run_id TEXT NOT NULL,
  raw_payload_files INTEGER NOT NULL DEFAULT 0,
  raw_rows_total INTEGER NOT NULL DEFAULT 0,
  normalized_rows_total INTEGER NOT NULL DEFAULT 0,
  stg_rows_total INTEGER NOT NULL DEFAULT 0,
  raw_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  normalized_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  stg_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  raw_commission_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  stg_commission_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  raw_logistics_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  stg_logistics_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  raw_services_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  stg_services_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
  pagination_complete BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, target_date, run_id)
);

CREATE INDEX IF NOT EXISTS finance_reconciliation_day_idx
  ON {{schema}}.finance_reconciliation_day (target_date, run_id);

CREATE TABLE IF NOT EXISTS {{schema}}.finance_day_state (
  day DATE PRIMARY KEY,
  status TEXT NOT NULL,
  raw_presence_flag BOOLEAN NOT NULL DEFAULT FALSE,
  stg_presence_flag BOOLEAN NOT NULL DEFAULT FALSE,
  finance_day_complete_flag BOOLEAN NOT NULL DEFAULT FALSE,
  final_analytic_usable_flag BOOLEAN NOT NULL DEFAULT FALSE,
  last_resume_rrdid BIGINT NOT NULL DEFAULT 0,
  previous_resume_rrdid BIGINT NOT NULL DEFAULT 0,
  pages_done_total INTEGER NOT NULL DEFAULT 0,
  rows_done_total BIGINT NOT NULL DEFAULT 0,
  attempts_total INTEGER NOT NULL DEFAULT 0,
  no_progress_attempts INTEGER NOT NULL DEFAULT 0,
  repeated_page_attempts INTEGER NOT NULL DEFAULT 0,
  http_429_count_total INTEGER NOT NULL DEFAULT 0,
  cumulative_wait_sec NUMERIC NOT NULL DEFAULT 0,
  last_progress_at TIMESTAMPTZ NULL,
  last_success_at TIMESTAMPTZ NULL,
  last_http_code INTEGER NULL,
  last_rows_delta BIGINT NOT NULL DEFAULT 0,
  last_page_checksum TEXT NULL,
  suspected_trap_type TEXT NULL,
  suspected_trap_reason TEXT NULL,
  reason_code TEXT NULL,
  source_pressure_flag BOOLEAN NOT NULL DEFAULT FALSE,
  reconciliation_exception_explained BOOLEAN NOT NULL DEFAULT FALSE,
  reconciliation_exception_reason TEXT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  terminal_reason_code TEXT NULL,
  terminal_decision_at TIMESTAMPTZ NULL,
  terminal_decision_basis TEXT NULL,
  terminal_confidence_level TEXT NULL,
  recheck_required_flag BOOLEAN NOT NULL DEFAULT FALSE,
  alternative_strategy_exhausted_flag BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS finance_day_state_status_idx
  ON {{schema}}.finance_day_state (status, updated_at DESC);

CREATE INDEX IF NOT EXISTS finance_day_state_usable_idx
  ON {{schema}}.finance_day_state (final_analytic_usable_flag, day DESC);

-- 16g) ingestion_rate_limit_state (персистентный cooldown state между запусками)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_rate_limit_state (
  schema_name         TEXT NOT NULL,
  marketplace         TEXT NOT NULL,
  source              TEXT NOT NULL,
  endpoint            TEXT NOT NULL,
  cooldown_until      TIMESTAMPTZ NULL,
  last_http_status    INTEGER NULL,
  x_ratelimit_retry   TEXT NULL,
  x_ratelimit_limit   TEXT NULL,
  x_ratelimit_reset   TEXT NULL,
  observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (schema_name, marketplace, source, endpoint)
);

CREATE INDEX IF NOT EXISTS ingestion_rate_limit_state_cooldown_idx
  ON {{schema}}.ingestion_rate_limit_state (marketplace, source, cooldown_until DESC);

-- 16h) ingestion_performance_metrics (worker throughput / pacing diagnostics)
CREATE TABLE IF NOT EXISTS {{schema}}.ingestion_performance_metrics (
  metric_id                      BIGSERIAL PRIMARY KEY,
  schema_name                    TEXT NOT NULL,
  marketplace                    TEXT NOT NULL,
  source                         TEXT NOT NULL,
  observed_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
  requests_per_minute            INTEGER NOT NULL DEFAULT 0,
  success_days_per_hour          INTEGER NOT NULL DEFAULT 0,
  rate_limit_429_per_hour        INTEGER NOT NULL DEFAULT 0,
  average_wait_time_after_429    NUMERIC(12,3) NOT NULL DEFAULT 0,
  created_at                     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestion_performance_metrics_src_idx
  ON {{schema}}.ingestion_performance_metrics (marketplace, source, observed_at DESC);

-- 17) dq_report (отчёт качества данных)
CREATE TABLE IF NOT EXISTS {{schema}}.dq_report (
  run_id        TEXT NOT NULL,
  period_start  DATE NOT NULL,
  period_end    DATE NOT NULL,
  check_id      TEXT NOT NULL,
  severity      TEXT NOT NULL,  -- OK/WARN/ERROR
  message       TEXT NOT NULL,
  metric_value  NUMERIC(18,4) NULL,
  threshold     NUMERIC(18,4) NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, check_id)
);

-- 18) Internal registry (global control table)
CREATE TABLE IF NOT EXISTS public.internal_clients_registry (
  client_code          TEXT PRIMARY KEY,
  client_name          TEXT NOT NULL,
  marketplace          TEXT NOT NULL CHECK (marketplace IN ('ozon', 'wb')),
  db_schema            TEXT NOT NULL,
  connection_status    TEXT NOT NULL DEFAULT 'new',
  last_sync_at         TIMESTAMPTZ NULL,
  last_marts_build_at  TIMESTAMPTZ NULL,
  notes                TEXT NULL,
  updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS internal_clients_registry_marketplace_idx
  ON public.internal_clients_registry (marketplace);

CREATE INDEX IF NOT EXISTS internal_clients_registry_schema_idx
  ON public.internal_clients_registry (db_schema);
