from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartDataQuality30d(TestCase):
    schema = "test_data_quality_v1"
    period_start = "2026-02-01"
    period_end = "2026-03-01"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._prepare_schema()
        cls._seed_inputs()
        cls._build_mart()

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
                        CREATE TABLE {cls.schema}.fact_unmatched_money (
                          marketplace TEXT NOT NULL,
                          event_date DATE NOT NULL,
                          amount NUMERIC NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_pnl_30d_by_product (
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          revenue_gross NUMERIC NULL,
                          units_sold NUMERIC NULL,
                          logistics_amount NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_inventory_snapshot (
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          stock_available NUMERIC NULL,
                          avg_daily_units_sold_30d NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_posting (
                          posting_number TEXT NOT NULL,
                          marketplace TEXT NULL,
                          created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction (
                          operation_id TEXT NOT NULL,
                          marketplace TEXT NULL,
                          operation_date TIMESTAMPTZ NOT NULL,
                          posting_number TEXT NULL,
                          delivery_charge NUMERIC NULL,
                          return_delivery_charge NUMERIC NULL,
                          sale_commission NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_transaction_service (
                          operation_id TEXT NOT NULL,
                          marketplace TEXT NULL,
                          service_price NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_order (
                          order_id TEXT NOT NULL,
                          marketplace TEXT NULL,
                          order_date TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.stg_wb_finance (
                          finance_id TEXT NOT NULL,
                          marketplace TEXT NULL,
                          operation_date TIMESTAMPTZ NOT NULL,
                          order_id TEXT NULL,
                          barcode TEXT NULL,
                          commission_amount NUMERIC NULL,
                          logistics_amount NUMERIC NULL,
                          service_amount NUMERIC NULL
                        )
                        """
                    )

    @classmethod
    def _seed_inputs(cls) -> None:
        pnl_rows = [
            (cls.period_start, cls.period_end, "ozon", "offer_neg_rev", -10, 1, 5),
            (cls.period_start, cls.period_end, "ozon", "offer_zero_rev", 0, 2, 7),
            (cls.period_start, cls.period_end, "ozon", "offer_neg_log", 100, 1, -3),
            (cls.period_start, cls.period_end, "ozon", "offer_ok", 50, 1, 2),
        ]
        inv_rows = [
            ("ozon", "offer_stock_no_sales", 5, 0),
            ("ozon", "offer_stock_ok", 3, 1),
        ]
        posting_rows = [
            ("posting_1", "ozon", "2026-02-10T10:00:00+00:00"),
            ("posting_1", "ozon", "2026-02-10T10:05:00+00:00"),
            ("posting_2", "ozon", "2026-02-12T11:00:00+00:00"),
        ]
        transaction_rows = [
            ("tx_1", "ozon", "2026-02-10T10:00:00+00:00", "", 10, 5, 7),
            ("tx_2", "ozon", "2026-02-11T10:00:00+00:00", "posting_x", 8, 0, 3),
            ("tx_3", "ozon", "2026-02-12T10:00:00+00:00", "posting_2", 2, 1, 1),
        ]

        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_pnl_30d_by_product (
                          period_start,
                          period_end,
                          marketplace,
                          offer_id,
                          revenue_gross,
                          units_sold,
                          logistics_amount
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        pnl_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_inventory_snapshot (
                          marketplace,
                          offer_id,
                          stock_available,
                          avg_daily_units_sold_30d
                        ) VALUES (%s,%s,%s,%s)
                        """,
                        inv_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.stg_posting (
                          posting_number,
                          marketplace,
                          created_at
                        ) VALUES (%s,%s,%s)
                        """,
                        posting_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.stg_transaction (
                          operation_id,
                          marketplace,
                          operation_date,
                          posting_number,
                          delivery_charge,
                          return_delivery_charge,
                          sale_commission
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                        """,
                        transaction_rows,
                    )

    @classmethod
    def _build_mart(cls) -> None:
        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_data_quality_30d.sql", params)

    def test_data_quality_metrics(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      negative_revenue_sku_count,
                      negative_revenue_note,
                      zero_revenue_with_sales_sku_count,
                      zero_revenue_note,
                      negative_logistics_sku_count,
                      negative_logistics_note,
                      stock_without_sales_sku_count,
                      stock_without_sales_note,
                      duplicate_posting_count,
                      duplicate_posting_note,
                      notes
                    FROM {self.schema}.mart_data_quality_30d
                    WHERE marketplace = 'ozon'
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], 1)
        self.assertIn("отрицательной выручкой", row[1].lower())
        self.assertEqual(row[2], 1)
        self.assertIn("продажи при нулевой выручке", row[3].lower())
        self.assertEqual(row[4], 1)
        self.assertIn("отрицательной логистикой", row[5].lower())
        self.assertEqual(row[6], 1)
        self.assertIn("остатки без продаж", row[7].lower())
        self.assertEqual(row[8], 1)
        self.assertIn("дубли", row[9].lower())
        self.assertIn("Отрицательная выручка", row[10])
        self.assertIn("Неразнесено", row[10])
