from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartRuleHits30d(TestCase):
    schema = "test_rules_engine_v1"
    period_start = "2026-02-01"
    period_end = "2026-03-01"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._prepare_schema()
        cls._seed_source_marts()
        cls._build_rule_mart()

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
                        CREATE TABLE {cls.schema}.mart_pnl_30d_by_product (
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          product_name TEXT NULL,
                          units_sold NUMERIC NULL,
                          revenue_gross NUMERIC NULL,
                          logistics_amount NUMERIC NULL,
                          ads_spend NUMERIC NULL,
                          profit_before_ads NUMERIC NULL,
                          profit_after_ads NUMERIC NULL,
                          margin_after_ads NUMERIC NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_inventory_snapshot (
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          product_name TEXT NULL,
                          days_of_stock_30d NUMERIC NULL,
                          out_of_stock_risk TEXT NULL,
                          potential_revenue_loss_30d NUMERIC NULL
                        )
                        """
                    )

    @classmethod
    def _seed_source_marts(cls) -> None:
        pnl_rows = [
            # scenario 1: negative_profit_after_ads
            ("ozon", "offer_neg", "Scenario 1 Negative", 10, 10000, 1000, 0, -1000, -1000, -0.10),
            # scenario 2: ads_eating_profit
            ("ozon", "offer_ads_eating", "Scenario 2 Ads Eating", 50, 20000, 1000, 4000, 5000, 1000, 0.12),
            # scenario 3: high_logistics_share
            ("ozon", "offer_logistics", "Scenario 3 Logistics", 30, 10000, 3000, 0, 2500, 2500, 0.25),
            # scenario 4: low_margin_after_ads (positive profit, weak margin)
            ("ozon", "offer_low_margin", "Scenario 4 Low Margin", 40, 10000, 1000, 200, 700, 500, 0.05),
            # scenario 6: ads_without_sales
            ("ozon", "offer_ads_no_sales", "Scenario 6 Ads No Sales", 0, 0, 0, 1200, 0, 0, None),
            # scenario 7: no_false_positive
            ("ozon", "offer_clean", "Scenario 7 Clean", 100, 15000, 1500, 1000, 4000, 3000, 0.20),
            # technical placeholder should be filtered out in rule mart
            ("ozon", "~", "~", 0, 0, 0, 100, 0, -100, None),
        ]
        inv_rows = [
            # scenario 5: stockout_risk with negative days text handling
            ("ozon", "offer_stockout", "Scenario 5 Stockout", -2, "out_of_stock", 2500),
            # no_false_positive inventory state
            ("ozon", "offer_clean", "Scenario 7 Clean", 30, "ok", 0),
            # technical placeholder should be filtered out in rule mart
            ("ozon", "~", "~", -5, "out_of_stock", 9999),
        ]

        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_pnl_30d_by_product (
                          marketplace,
                          offer_id,
                          product_name,
                          units_sold,
                          revenue_gross,
                          logistics_amount,
                          ads_spend,
                          profit_before_ads,
                          profit_after_ads,
                          margin_after_ads
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        pnl_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_inventory_snapshot (
                          marketplace,
                          offer_id,
                          product_name,
                          days_of_stock_30d,
                          out_of_stock_risk,
                          potential_revenue_loss_30d
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        inv_rows,
                    )

    @classmethod
    def _build_rule_mart(cls) -> None:
        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_rule_hits_30d.sql", params)

    def test_all_scenarios_and_no_false_positive(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      rule_code,
                      severity,
                      offer_id,
                      product_name,
                      money_impact_30d,
                      revenue_at_risk_30d,
                      priority_score,
                      rule_version,
                      problem_description
                    FROM {self.schema}.mart_rule_hits_30d
                    ORDER BY rule_code, offer_id
                    """
                )
                rows = cur.fetchall()

        self.assertEqual(len(rows), 6)

        by_key = {(r[0], r[2]): r for r in rows}
        expected_keys = {
            ("negative_profit_after_ads", "offer_neg"),
            ("ads_eating_profit", "offer_ads_eating"),
            ("high_logistics_share", "offer_logistics"),
            ("low_margin_after_ads", "offer_low_margin"),
            ("stockout_risk", "offer_stockout"),
            ("ads_without_sales", "offer_ads_no_sales"),
        }
        self.assertEqual(set(by_key.keys()), expected_keys)

        # scenario 1
        s1 = by_key[("negative_profit_after_ads", "offer_neg")]
        self.assertEqual(s1[1], "high")
        self.assertEqual(s1[4], Decimal("1000"))

        # scenario 2
        s2 = by_key[("ads_eating_profit", "offer_ads_eating")]
        self.assertEqual(s2[1], "high")
        self.assertEqual(s2[4], Decimal("4000"))

        # scenario 3
        s3 = by_key[("high_logistics_share", "offer_logistics")]
        self.assertEqual(s3[1], "medium")
        self.assertEqual(s3[4], Decimal("1000.0"))

        # scenario 4: low_margin only on positive profit after ads
        s4 = by_key[("low_margin_after_ads", "offer_low_margin")]
        self.assertEqual(s4[1], "medium")
        self.assertEqual(s4[4], Decimal("500.0"))
        self.assertNotIn(("low_margin_after_ads", "offer_neg"), by_key)

        # scenario 5
        s5 = by_key[("stockout_risk", "offer_stockout")]
        self.assertEqual(s5[1], "high")
        self.assertIsNone(s5[4])
        self.assertEqual(s5[5], Decimal("2500"))
        self.assertIn("зоне дефицита", s5[8])
        self.assertNotIn("-2", s5[8])

        # scenario 6
        s6 = by_key[("ads_without_sales", "offer_ads_no_sales")]
        self.assertEqual(s6[1], "high")
        self.assertEqual(s6[4], Decimal("1200"))

        # scenario 7: no false positive
        self.assertNotIn(("negative_profit_after_ads", "offer_clean"), by_key)
        self.assertNotIn(("ads_eating_profit", "offer_clean"), by_key)
        self.assertNotIn(("high_logistics_share", "offer_clean"), by_key)
        self.assertNotIn(("low_margin_after_ads", "offer_clean"), by_key)
        self.assertNotIn(("stockout_risk", "offer_clean"), by_key)
        self.assertNotIn(("ads_without_sales", "offer_clean"), by_key)

        # placeholders are filtered; rule_version/priority populated
        self.assertNotIn(("negative_profit_after_ads", "~"), by_key)
        for row in rows:
            self.assertEqual(row[7], "v1")
            self.assertIsNotNone(row[6])
