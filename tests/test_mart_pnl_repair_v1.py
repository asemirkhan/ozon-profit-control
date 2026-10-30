from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartPnlRepairV1(TestCase):
    schema = "test_mart_pnl_repair_v1"
    period_start = "2026-03-01"
    period_end = "2026-03-01"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._prepare_schema()
        cls._seed_data()
        cls._build_pipeline()

    @classmethod
    def tearDownClass(cls) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
        super().tearDownClass()

    @classmethod
    def _prepare_schema(cls) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(f"DROP SCHEMA IF EXISTS {cls.schema} CASCADE")
                    cur.execute(f"CREATE SCHEMA {cls.schema}")

                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.dim_product (
                          offer_id TEXT,
                          marketplace TEXT,
                          product_id TEXT,
                          sku TEXT,
                          product_name TEXT,
                          brand TEXT,
                          category_id TEXT,
                          category_name TEXT,
                          archived BOOLEAN,
                          updated_at TIMESTAMPTZ
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_product (
                          nm_id TEXT,
                          offer_id TEXT,
                          vendor_code TEXT,
                          barcode TEXT,
                          product_name TEXT,
                          brand TEXT,
                          category_name TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_sale (
                          sale_id TEXT,
                          marketplace TEXT,
                          sale_date TIMESTAMPTZ,
                          order_id TEXT,
                          is_return BOOLEAN,
                          nm_id TEXT,
                          offer_id TEXT,
                          barcode TEXT,
                          quantity NUMERIC,
                          revenue_gross NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_order (
                          order_id TEXT,
                          marketplace TEXT,
                          order_date TIMESTAMPTZ,
                          nm_id TEXT,
                          offer_id TEXT,
                          barcode TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_finance (
                          finance_id TEXT,
                          marketplace TEXT,
                          operation_date TIMESTAMPTZ,
                          operation_type TEXT,
                          order_id TEXT,
                          offer_id TEXT,
                          nm_id TEXT,
                          barcode TEXT,
                          commission_amount NUMERIC,
                          logistics_amount NUMERIC,
                          service_amount NUMERIC,
                          amount NUMERIC,
                          raw_payload JSONB
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_ads (
                          date DATE,
                          campaign_id TEXT,
                          marketplace TEXT,
                          offer_id TEXT,
                          nm_id TEXT,
                          app_type TEXT,
                          spend NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_paid_storage (
                          source_record_id TEXT,
                          marketplace TEXT,
                          report_date DATE,
                          offer_id TEXT,
                          nm_id TEXT,
                          barcode TEXT,
                          warehouse_price NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_stock (
                          snapshot_date DATE,
                          marketplace TEXT,
                          offer_id TEXT,
                          nm_id TEXT,
                          barcode TEXT,
                          warehouse_name TEXT,
                          stock_total NUMERIC
                        )
                        """
                    )

                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting (
                          posting_number TEXT,
                          marketplace TEXT,
                          created_at TIMESTAMPTZ
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting_item (
                          posting_number TEXT,
                          marketplace TEXT,
                          offer_id TEXT,
                          sku TEXT,
                          product_id TEXT,
                          quantity NUMERIC,
                          item_price NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting_item_financial (
                          posting_number TEXT,
                          marketplace TEXT,
                          product_id TEXT,
                          sku TEXT,
                          commission_amount NUMERIC,
                          item_services_total NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction (
                          operation_id TEXT,
                          marketplace TEXT,
                          operation_date TIMESTAMPTZ,
                          posting_number TEXT,
                          sale_commission NUMERIC,
                          delivery_charge NUMERIC,
                          return_delivery_charge NUMERIC
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction_item (
                          operation_id TEXT,
                          marketplace TEXT,
                          sku TEXT,
                          item_name TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction_service (
                          operation_id TEXT,
                          marketplace TEXT,
                          service_name TEXT,
                          service_price NUMERIC
                        )
                        """
                    )

                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.fact_inventory_snapshot (
                          snapshot_date DATE,
                          marketplace TEXT,
                          offer_id TEXT,
                          sku TEXT,
                          product_id TEXT
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.product_cost_input (
                          id BIGSERIAL,
                          marketplace TEXT,
                          offer_id TEXT,
                          product_id TEXT,
                          sku TEXT,
                          cogs_purchase_amount NUMERIC,
                          packaging_amount NUMERIC,
                          labeling_amount NUMERIC,
                          inbound_logistics_amount NUMERIC,
                          assembly_amount NUMERIC,
                          other_unit_cost_amount NUMERIC,
                          valid_from DATE,
                          valid_to DATE,
                          is_active BOOLEAN,
                          updated_at TIMESTAMPTZ
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.fact_cogs (
                          offer_id TEXT,
                          marketplace TEXT,
                          unit_cogs NUMERIC,
                          packaging_cost NUMERIC,
                          inbound_shipping_cost NUMERIC,
                          other_variable_cost NUMERIC,
                          effective_from DATE,
                          effective_to DATE,
                          updated_at TIMESTAMPTZ
                        )
                        """
                    )

    @classmethod
    def _seed_data(cls) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_product (nm_id, offer_id, vendor_code, barcode, product_name, category_name)
                        VALUES ('1001', 'SKU-A', 'SKU-A', 'BAR-1', 'Toy A', 'Игрушки')
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_sale
                        (sale_id, marketplace, sale_date, order_id, is_return, nm_id, offer_id, barcode, quantity, revenue_gross)
                        VALUES
                        ('sale-1', 'wb', '2026-03-01T10:00:00+00:00', 'order-1', FALSE, '1001', 'SKU-A', 'BAR-1', 1, 1000)
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_finance
                        (finance_id, marketplace, operation_date, operation_type, order_id, offer_id, nm_id, commission_amount, logistics_amount, service_amount, amount)
                        VALUES
                        ('fin-1', 'wb', '2026-03-01T10:30:00+00:00', 'Логистика', 'order-1', '~', '1001', 0, 50, 0, 0),
                        ('fin-2', 'wb', '2026-03-01T10:35:00+00:00', 'misc charge', 'order-1', '~', '1001', 0, 0, 10, 5),
                        ('fin-3', 'wb', '2026-03-01T00:00:00+00:00', 'Хранение', '', '~', '', 0, 0, 0, 10)
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_paid_storage
                        (source_record_id, marketplace, report_date, offer_id, nm_id, barcode, warehouse_price)
                        VALUES
                        ('ps-1', 'wb', '2026-03-01', 'SKU-A', '1001', 'BAR-1', 10),
                        ('ps-2', 'wb', '2026-03-01', 'SKU-A', '1001', 'BAR-1', -90)
                        """
                    )
                    cur.execute(
                        f"""
                        INSERT INTO {cls.schema}.stg_wb_ads
                        (date, campaign_id, marketplace, offer_id, nm_id, app_type, spend)
                        VALUES ('2026-03-01', 'adv-1', 'wb', 'wb_nm_1001', '1001', '1', 100)
                        """
                    )

    @classmethod
    def _build_pipeline(cls) -> None:
        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_dim_product_bridge.sql", params)
                execute_sql_file(conn, "src/marts/sql/mart_product_money_attribution.sql", params)
                execute_sql_file(conn, "src/marts/sql/mart_pnl_daily_by_product.sql", params)
                execute_sql_file(conn, "src/marts/sql/mart_pnl_30d_by_product.sql", params)
                execute_sql_file(conn, "src/marts/sql/mart_semantic_correctness.sql", params)

    def test_wb_cogs_fallback_is_reported_separately_from_operational_result(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      revenue_gross,
                      logistics_amount,
                      services_amount,
                      other_amount,
                      marketplace_other_fees_amount,
                      ads_spend,
                      cogs_amount,
                      profit_after_ads,
                      margin_after_ads
                    FROM {self.schema}.mart_pnl_30d_by_product
                    WHERE marketplace='wb' AND offer_id='SKU-A'
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], Decimal("1000"))
        self.assertEqual(row[1], Decimal("50"))
        self.assertEqual(row[2], Decimal("10"))
        self.assertEqual(row[3], Decimal("5"))
        self.assertEqual(row[4], Decimal("25"))
        self.assertEqual(row[5], Decimal("100"))
        self.assertEqual(row[6], Decimal("250"))
        # profit_after_ads is the current operational result; estimated COGS is
        # exposed separately until the owner approves a post-COGS metric.
        self.assertEqual(row[7], Decimal("825"))
        self.assertEqual(row[8].quantize(Decimal("0.0001")), Decimal("0.8250"))

    def test_period_end_is_inclusive_in_30d_mart(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {self.schema}.mart_pnl_30d_by_product
                    WHERE marketplace='wb' AND offer_id='SKU-A'
                    """
                )
                count_row = cur.fetchone()

        self.assertEqual(count_row[0], 1)

    def test_ads_nm_id_bridge_match_exists(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT attribution_method, attribution_confidence
                    FROM {self.schema}.fact_product_money_attribution
                    WHERE marketplace='wb'
                      AND money_bucket='ads'
                      AND source_table='stg_wb_ads'
                    ORDER BY source_record_id
                    LIMIT 1
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], "nm_id_match")
        self.assertEqual(row[1], "high")

    def test_semantic_correctness_has_no_bucket_drift_for_seed(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      semantic_mismatch,
                      drift_fact_mart_services,
                      drift_fact_mart_other
                    FROM {self.schema}.mart_semantic_correctness
                    WHERE marketplace='wb'
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertFalse(bool(row[0]))
        self.assertEqual(row[1], Decimal("0.00"))
        self.assertEqual(row[2], Decimal("0.00"))

    def test_paid_storage_uses_positive_rows_only_without_abs_inflation(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      amount,
                      attribution_method,
                      attribution_confidence
                    FROM {self.schema}.fact_product_money_attribution
                    WHERE marketplace='wb'
                      AND money_bucket='storage'
                    ORDER BY source_record_id
                    """
                )
                rows = cur.fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], Decimal("10.00"))
        self.assertEqual(rows[0][1], "paid_storage_share_day")
        self.assertEqual(rows[0][2], "high")
