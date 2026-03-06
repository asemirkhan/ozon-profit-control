-- =========================
-- DDL v1 — Ozon Profit Control
-- Postgres, schema-per-client
-- =========================

-- 0) schema
CREATE SCHEMA IF NOT EXISTS client_demo;

-- 1) dim_product
CREATE TABLE IF NOT EXISTS client_demo.dim_product (
  offer_id        TEXT PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS dim_product_sku_idx
  ON client_demo.dim_product (sku);

CREATE INDEX IF NOT EXISTS dim_product_product_id_idx
  ON client_demo.dim_product (product_id);

-- 2) stg_posting
CREATE TABLE IF NOT EXISTS client_demo.stg_posting (
  posting_number  TEXT PRIMARY KEY,
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

CREATE INDEX IF NOT EXISTS stg_posting_created_at_idx
  ON client_demo.stg_posting (created_at);

-- 3) stg_posting_item (товар в отправлении)
DROP TABLE IF EXISTS client_demo.stg_posting_item;

CREATE TABLE IF NOT EXISTS client_demo.stg_posting_item (
  posting_number  TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS stg_posting_item_posting_idx
  ON client_demo.stg_posting_item (posting_number);

CREATE INDEX IF NOT EXISTS stg_posting_item_offer_idx
  ON client_demo.stg_posting_item (offer_id);

CREATE INDEX IF NOT EXISTS stg_posting_item_sku_idx
  ON client_demo.stg_posting_item (sku);

-- 4) stg_posting_item_financial (финансы по товару в отправлении)
CREATE TABLE IF NOT EXISTS client_demo.stg_posting_item_financial (
  posting_number        TEXT NOT NULL,
  product_id            TEXT NOT NULL,
  sku                   TEXT NULL,
  quantity              INTEGER NOT NULL,
  price                 NUMERIC(18,2) NULL,
  old_price             NUMERIC(18,2) NULL,
  total_discount_value  NUMERIC(18,2) NULL,
  total_discount_percent NUMERIC(9,4) NULL,
  commission_amount     NUMERIC(18,2) NULL,
  commission_percent    NUMERIC(9,4) NULL,
  payout                NUMERIC(18,2) NULL,
  item_services_total   NUMERIC(18,2) NULL,
  raw_updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (posting_number, product_id)
);

CREATE INDEX IF NOT EXISTS stg_posting_fin_sku_idx
  ON client_demo.stg_posting_item_financial (sku);

-- 5) stg_transaction (финансовые операции)
CREATE TABLE IF NOT EXISTS client_demo.stg_transaction (
  operation_id          TEXT PRIMARY KEY,
  operation_date        TIMESTAMPTZ NOT NULL,
  operation_type        TEXT NOT NULL,
  operation_type_name   TEXT NULL,
  posting_number        TEXT NULL,
  accruals_for_sale     NUMERIC(18,2) NULL,
  sale_commission       NUMERIC(18,2) NULL,
  delivery_charge       NUMERIC(18,2) NULL,
  return_delivery_charge NUMERIC(18,2) NULL,
  amount                NUMERIC(18,2) NULL,
  raw_updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stg_transaction_date_idx
  ON client_demo.stg_transaction (operation_date);

CREATE INDEX IF NOT EXISTS stg_transaction_posting_idx
  ON client_demo.stg_transaction (posting_number);

-- 6) stg_transaction_item (товары в транзакции)
CREATE TABLE IF NOT EXISTS client_demo.stg_transaction_item (
  operation_id     TEXT NOT NULL,
  sku              TEXT NOT NULL,
  item_name        TEXT NULL,
  raw_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (operation_id, sku)
);

-- 7) stg_transaction_service (услуги в транзакции)
CREATE TABLE IF NOT EXISTS client_demo.stg_transaction_service (
  operation_id     TEXT NOT NULL,
  service_name     TEXT NOT NULL,
  service_price    NUMERIC(18,2) NOT NULL,
  raw_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (operation_id, service_name)
);

-- 8) fact_cogs (себестоимость)
CREATE TABLE IF NOT EXISTS client_demo.fact_cogs (
  offer_id              TEXT PRIMARY KEY,
  unit_cogs             NUMERIC(18,2) NOT NULL,
  packaging_cost        NUMERIC(18,2) NULL,
  inbound_shipping_cost NUMERIC(18,2) NULL,
  other_variable_cost   NUMERIC(18,2) NULL,
  effective_from        DATE NULL,
  effective_to          DATE NULL,
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 9) fact_ads_spend (реклама)
DROP TABLE IF EXISTS client_demo.fact_ads_spend;

CREATE TABLE IF NOT EXISTS client_demo.fact_ads_spend (
  date               DATE NOT NULL,
  campaign_id         TEXT NOT NULL DEFAULT '~',
  campaign_name       TEXT NULL,
  offer_id            TEXT NOT NULL DEFAULT '~',
  sku                 TEXT NOT NULL DEFAULT '~',
  spend               NUMERIC(18,2) NOT NULL,
  impressions         BIGINT NULL,
  clicks              BIGINT NULL,
  attributed_orders   BIGINT NULL,
  attributed_revenue  NUMERIC(18,2) NULL,
  raw_updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  loaded_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, offer_id, sku)
);

CREATE INDEX IF NOT EXISTS fact_ads_spend_date_idx
  ON client_demo.fact_ads_spend (date);

CREATE INDEX IF NOT EXISTS fact_ads_spend_offer_idx
  ON client_demo.fact_ads_spend (offer_id);

-- 10) dq_report (отчёт качества данных)
CREATE TABLE IF NOT EXISTS client_demo.dq_report (
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