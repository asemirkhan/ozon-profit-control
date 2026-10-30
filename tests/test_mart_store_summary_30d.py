from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartStoreSummary30d(TestCase):
    schema = "test_store_summary_v1"
    period_start = "2026-02-01"
    period_end = "2026-03-01"
    run_date = "2026-03-09"

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
                        CREATE TABLE {cls.schema}.finance_day_state (
                          day DATE NOT NULL,
                          final_analytic_usable_flag BOOLEAN NOT NULL
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
                          units_sold NUMERIC NULL,
                          revenue_gross NUMERIC NULL,
                          ads_spend NUMERIC NULL,
                          logistics_amount NUMERIC NULL,
                          profit_before_ads NUMERIC NULL,
                          profit_after_ads NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_pnl_daily_by_product (
                          date DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          units_sold NUMERIC NULL,
                          revenue_gross NUMERIC NULL,
                          ads_spend NUMERIC NULL,
                          logistics_amount NUMERIC NULL,
                          profit_before_ads NUMERIC NULL,
                          profit_after_ads NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_rule_hits_30d (
                          run_date DATE NOT NULL,
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          rule_code TEXT NOT NULL,
                          severity TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          money_impact_30d NUMERIC NULL,
                          revenue_at_risk_30d NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_inventory_snapshot (
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          out_of_stock_risk TEXT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_data_quality_30d (
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          unattributed_row_count INTEGER NULL,
                          unattributed_cost_total NUMERIC NULL,
                          fallback_attributed_cost_total NUMERIC NULL
                        )
                        """
                    )

    @classmethod
    def _seed_inputs(cls) -> None:
        pnl_rows = [
            (cls.period_start, cls.period_end, "ozon", "offer_a", 10, 1000, 200, 100, 300, 100),
            (cls.period_start, cls.period_end, "ozon", "offer_b", 5, 500, 100, 80, 50, -50),
            (cls.period_start, cls.period_end, "ozon", "offer_c", 0, 0, 0, 0, 0, 0),
        ]
        rule_rows = [
            (cls.run_date, cls.period_start, cls.period_end, "ozon", "stockout_risk", "high", "offer_b", None, 600),
            (cls.run_date, cls.period_start, cls.period_end, "ozon", "stockout_risk", "high", "offer_c", None, 200),
            (cls.run_date, cls.period_start, cls.period_end, "ozon", "negative_profit_after_ads", "high", "offer_b", 120, None),
            (cls.run_date, cls.period_start, cls.period_end, "ozon", "ads_eating_profit", "high", "offer_a", 80, None),
            (cls.run_date, cls.period_start, cls.period_end, "ozon", "high_logistics_share", "medium", "offer_b", 40, None),
        ]
        inv_rows = [
            ("ozon", "offer_a", "ok"),
            ("ozon", "offer_b", "high"),
            ("ozon", "offer_c", "no_sales"),
        ]
        dq_rows = [
            (cls.period_start, cls.period_end, "ozon", 0, 0, 0),
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
                          units_sold,
                          revenue_gross,
                          ads_spend,
                          logistics_amount,
                          profit_before_ads,
                          profit_after_ads
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        pnl_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_pnl_daily_by_product (
                          date, marketplace, offer_id, units_sold, revenue_gross,
                          ads_spend, logistics_amount, profit_before_ads, profit_after_ads
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (cls.period_start, row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9])
                            for row in pnl_rows
                        ],
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_rule_hits_30d (
                          run_date,
                          period_start,
                          period_end,
                          marketplace,
                          rule_code,
                          severity,
                          offer_id,
                          money_impact_30d,
                          revenue_at_risk_30d
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        rule_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_inventory_snapshot (
                          marketplace,
                          offer_id,
                          out_of_stock_risk
                        ) VALUES (%s,%s,%s)
                        """,
                        inv_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_data_quality_30d (
                          period_start,
                          period_end,
                          marketplace,
                          unattributed_row_count,
                          unattributed_cost_total,
                          fallback_attributed_cost_total
                        ) VALUES (%s,%s,%s,%s,%s,%s)
                        """,
                        dq_rows,
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
                execute_sql_file(conn, "src/marts/sql/mart_store_summary_30d.sql", params)

    def test_store_summary_metrics(self) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {self.schema}.mart_pnl_daily_by_product
                        SET profit_after_ads = CASE offer_id
                          WHEN 'offer_a' THEN 100
                          WHEN 'offer_b' THEN -50
                          ELSE 0
                        END
                        """
                    )
                execute_sql_file(
                    conn,
                    "src/marts/sql/mart_store_summary_30d.sql",
                    {
                        "schema": self.schema,
                        "period_start": self.period_start,
                        "period_end": self.period_end,
                    },
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      sku_count,
                      units_sold_total,
                      revenue_total,
                      ads_spend_total,
                      logistics_total,
                      profit_before_ads_total,
                      profit_after_ads_total,
                      margin_after_ads_total,
                      loss_sku_count,
                      sku_with_ads_count,
                      sku_with_stockout_risk_count,
                      top_problem_rule_code,
                      store_health_status,
                      store_health_message
                    FROM {self.schema}.mart_store_summary_30d
                    """
                )
                row = cur.fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], 3)
        self.assertEqual(row[1], Decimal("15"))
        self.assertEqual(row[2], Decimal("1500"))
        self.assertEqual(row[3], Decimal("300"))
        self.assertEqual(row[4], Decimal("180"))
        self.assertEqual(row[5], Decimal("350"))
        self.assertEqual(row[6], Decimal("50"))
        self.assertEqual(row[7].quantize(Decimal("0.0001")), Decimal("0.0333"))
        self.assertEqual(row[8], 1)
        self.assertEqual(row[9], 2)
        self.assertEqual(row[10], 1)
        self.assertEqual(row[11], "stockout_risk")
        self.assertEqual(row[12], "at_risk")
        self.assertIn("под давлением", row[13])

    def test_losing_money_status_overrides(self) -> None:
        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        UPDATE {self.schema}.mart_pnl_daily_by_product
                        SET profit_after_ads = -200
                        WHERE offer_id = 'offer_a'
                        """
                    )
                execute_sql_file(
                    conn,
                    "src/marts/sql/mart_store_summary_30d.sql",
                    {
                        "schema": self.schema,
                        "period_start": self.period_start,
                        "period_end": self.period_end,
                    },
                )
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT store_health_status, store_health_message FROM {self.schema}.mart_store_summary_30d"
                )
                status, message = cur.fetchone()

        self.assertEqual(status, "losing_money")
        self.assertIn("теряет деньги", message)
