from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestWbPromotionAdsOverlapFix(TestCase):
    schema = "test_wb_promotion_ads_overlap_fix"
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
                        (finance_id, marketplace, operation_date, operation_type, order_id, offer_id, nm_id, barcode, commission_amount, logistics_amount, service_amount, amount, raw_payload)
                        VALUES
                        (
                          'fin-logistics',
                          'wb',
                          '2026-03-01T10:30:00+00:00',
                          'Логистика',
                          'order-1',
                          '~',
                          '1001',
                          'BAR-1',
                          0,
                          50,
                          0,
                          0,
                          '{{}}'::jsonb
                        ),
                        (
                          'fin-promo',
                          'wb',
                          '2026-03-01T00:00:00+00:00',
                          'Удержание',
                          '',
                          '~',
                          '',
                          '',
                          0,
                          0,
                          0,
                          20,
                          '{{"bonus_type_name":"Оказание услуг «WB Продвижение», документ №1","deduction":20}}'::jsonb
                        ),
                        (
                          'fin-jam',
                          'wb',
                          '2026-03-01T00:00:00+00:00',
                          'Удержание',
                          '',
                          '~',
                          '',
                          '',
                          0,
                          0,
                          0,
                          30,
                          '{{"bonus_type_name":"Предоставление услуг по подписке «Джем», документ №2","deduction":30}}'::jsonb
                        ),
                        (
                          'fin-review',
                          'wb',
                          '2026-03-01T00:00:00+00:00',
                          'Удержание',
                          '',
                          '~',
                          '',
                          '',
                          0,
                          0,
                          0,
                          5,
                          '{{"bonus_type_name":"Списание за отзыв token: акция №1, товар 1001","deduction":5}}'::jsonb
                        )
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

    def test_wb_promotion_finance_subset_is_excluded_while_other_services_remain(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {self.schema}.fact_product_money_attribution
                    WHERE marketplace = 'wb'
                      AND source_table = 'stg_wb_finance'
                      AND source_record_id LIKE 'fin-promo:%'
                    """
                )
                promo_count = cur.fetchone()[0]

                cur.execute(
                    f"""
                    SELECT canonical_product_key, amount
                    FROM {self.schema}.fact_product_money_attribution
                    WHERE marketplace = 'wb'
                      AND source_table = 'stg_wb_finance'
                      AND source_record_id LIKE 'fin-jam:%'
                    """
                )
                jam_row = cur.fetchone()

                cur.execute(
                    f"""
                    SELECT canonical_product_key, amount, attribution_method
                    FROM {self.schema}.fact_product_money_attribution
                    WHERE marketplace = 'wb'
                      AND source_table = 'stg_wb_finance'
                      AND source_record_id LIKE 'fin-review:%'
                    """
                )
                review_row = cur.fetchone()

        self.assertEqual(promo_count, 0)
        self.assertEqual(jam_row[0], "~")
        self.assertEqual(jam_row[1], Decimal("30.00"))
        self.assertNotEqual(review_row[0], "~")
        self.assertEqual(review_row[1], Decimal("5.00"))
        self.assertEqual(review_row[2], "nm_id_match")

    def test_product_and_store_pnl_keep_ads_but_drop_promotion_duplicate(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      offer_id,
                      services_amount,
                      ads_spend,
                      logistics_amount,
                      profit_after_ads
                    FROM {self.schema}.mart_pnl_30d_by_product
                    WHERE marketplace = 'wb'
                    ORDER BY offer_id
                    """
                )
                rows = cur.fetchall()

        by_offer = {row[0]: row[1:] for row in rows}
        self.assertEqual(by_offer["SKU-A"][0], Decimal("5.00"))
        self.assertEqual(by_offer["SKU-A"][1], Decimal("100.00"))
        self.assertEqual(by_offer["SKU-A"][2], Decimal("50.00"))
        self.assertEqual(by_offer["SKU-A"][3], Decimal("845.00"))
        self.assertEqual(by_offer["~"][0], Decimal("30.00"))
        self.assertEqual(by_offer["~"][1], Decimal("0.00"))
