from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartActionQueue14d(TestCase):
    schema = "test_action_queue_v1"
    period_start = "2026-02-01"
    period_end = "2026-03-01"
    run_date = "2026-03-09"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._prepare_schema()
        cls._seed_rule_hits()
        cls._build_action_queue()

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
                        CREATE TABLE {cls.schema}.mart_rule_hits_30d (
                          run_date DATE NOT NULL,
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          rule_code TEXT NOT NULL,
                          severity TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          product_name TEXT NULL,
                          metric_value NUMERIC NULL,
                          threshold_value NUMERIC NULL,
                          problem_title TEXT NOT NULL,
                          problem_description TEXT NOT NULL,
                          recommended_action TEXT NOT NULL,
                          expected_effect_note TEXT NULL,
                          money_impact_30d NUMERIC NULL,
                          revenue_at_risk_30d NUMERIC NULL,
                          priority_score NUMERIC NULL,
                          rule_version TEXT NOT NULL,
                          source_mart TEXT NOT NULL,
                          loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_store_summary_30d (
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          attribution_quality TEXT NULL,
                          confidence TEXT NULL
                        )
                        """
                    )
                    cur.execute(
                        f"""
                        CREATE TABLE {cls.schema}.mart_inventory_snapshot (
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          days_of_stock_30d NUMERIC NULL,
                          daily_revenue_run_rate NUMERIC NULL,
                          daily_profit_run_rate NUMERIC NULL,
                          revenue_at_risk_per_day NUMERIC NULL,
                          revenue_at_risk_3d NUMERIC NULL,
                          revenue_at_risk_7d NUMERIC NULL,
                          revenue_at_risk_14d NUMERIC NULL,
                          profit_at_risk_per_day NUMERIC NULL,
                          expected_replenishment_date DATE NULL,
                          planned_replenishment_qty NUMERIC NULL,
                          replenishment_status TEXT NULL,
                          stockout_effect_confidence TEXT NULL
                        )
                        """
                    )

    @classmethod
    def _seed_rule_hits(cls) -> None:
        rows = [
            # scenario 1: single issue
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "negative_profit_after_ads",
                "high",
                "offer_single",
                "Scenario Single",
                -500,
                0,
                "single issue",
                "desc",
                "do action",
                None,
                500,
                None,
                78,
                "v1",
                "mart_pnl_30d_by_product",
            ),
            # scenario 2: multi issue same product
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "negative_profit_after_ads",
                "high",
                "offer_multi",
                "Scenario Multi",
                -800,
                0,
                "multi primary",
                "desc",
                "do action",
                None,
                800,
                None,
                80,
                "v1",
                "mart_pnl_30d_by_product",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "high_logistics_share",
                "medium",
                "offer_multi",
                "Scenario Multi",
                0.35,
                0.2,
                "multi support",
                "desc",
                "do action",
                None,
                600,
                None,
                55,
                "v1",
                "mart_pnl_30d_by_product",
            ),
            # scenario 3: tie break (same score -> stockout wins)
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "stockout_risk",
                "high",
                "offer_tie",
                "Scenario Tie",
                0,
                14,
                "tie stockout",
                "desc",
                "do action",
                None,
                None,
                5000,
                74,
                "v1",
                "mart_inventory_snapshot",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "ads_eating_profit",
                "high",
                "offer_tie",
                "Scenario Tie",
                0.40,
                0.5,
                "tie ads",
                "desc",
                "do action",
                None,
                5000,
                None,
                74,
                "v1",
                "mart_pnl_30d_by_product",
            ),
            # scenario 4: stockout outranks less urgent at equal score
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "stockout_risk",
                "high",
                "offer_stockout_rank",
                "Scenario Stockout Rank",
                0,
                14,
                "stockout rank",
                "desc",
                "do action",
                None,
                None,
                2500,
                70,
                "v1",
                "mart_inventory_snapshot",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "low_margin_after_ads",
                "medium",
                "offer_low_rank",
                "Scenario Low Rank",
                0.08,
                0.10,
                "low rank",
                "desc",
                "do action",
                None,
                1200,
                None,
                70,
                "v1",
                "mart_pnl_30d_by_product",
            ),
            # scenario 6: impact fields with money null, revenue present
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "stockout_risk",
                "high",
                "offer_revenue_only",
                "Scenario Revenue Only",
                0,
                14,
                "revenue only",
                "desc",
                "do action",
                None,
                None,
                9000,
                68,
                "v1",
                "mart_inventory_snapshot",
            ),
        ]

        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
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
                          product_name,
                          metric_value,
                          threshold_value,
                          problem_title,
                          problem_description,
                          recommended_action,
                          expected_effect_note,
                          money_impact_30d,
                          revenue_at_risk_30d,
                          priority_score,
                          rule_version,
                          source_mart
                        )
                        VALUES (
                          %s,%s,%s,'ozon',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        rows,
                    )

    @classmethod
    def _build_action_queue(cls) -> None:
        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_action_queue_14d.sql", params)

    def test_action_queue_scenarios(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      offer_id,
                      primary_rule_code,
                      action_code,
                      action_type,
                      action_priority,
                      action_priority_score,
                      supporting_rule_codes,
                      supporting_problem_count,
                      money_impact_30d,
                      revenue_at_risk_30d,
                      estimated_gain_if_fixed,
                      action_window_days,
                      total_action_impact_30d,
                      action_group_size,
                      action_group_rank,
                      batch_candidate,
                      duplicate_penalty_score,
                      execution_lane
                    FROM {self.schema}.mart_action_queue_14d
                    ORDER BY offer_id
                    """
                )
                rows = cur.fetchall()

        by_offer = {r[0]: r for r in rows}

        # scenario 1: one product = one issue
        s1 = by_offer["offer_single"]
        self.assertEqual(s1[1], "negative_profit_after_ads")
        self.assertEqual(s1[2], "fix_unprofitable_ads")
        self.assertEqual(s1[3], "ads")
        self.assertEqual(s1[7], 1)
        self.assertEqual(s1[11], 7)

        # scenario 2: one product = multiple issues, one action row
        s2 = by_offer["offer_multi"]
        self.assertEqual(s2[1], "negative_profit_after_ads")
        self.assertIn("high_logistics_share", s2[6])
        self.assertEqual(s2[7], 2)

        # scenario 3: tie-break by rule order
        s3 = by_offer["offer_tie"]
        self.assertEqual(s3[1], "stockout_risk")
        self.assertEqual(s3[2], "replenish_stock")
        self.assertEqual(s3[3], "inventory")
        self.assertIn("ads_eating_profit", s3[6])

        # scenario 4: stockout should rank above less urgent on equal score
        stockout_rank = by_offer["offer_stockout_rank"][4]
        low_rank = by_offer["offer_low_rank"][4]
        self.assertLess(stockout_rank, low_rank)

        # scenario 5: no false positive passthrough (no source hit -> no action row)
        self.assertNotIn("offer_clean", by_offer)

        # scenario 6: impact fields
        s_rev = by_offer["offer_revenue_only"]
        self.assertIsNone(s_rev[8])
        self.assertEqual(s_rev[9], Decimal("9000"))
        self.assertEqual(s_rev[10], Decimal("9000"))
        self.assertEqual(s_rev[11], 7)
        self.assertEqual(s_rev[12], Decimal("9000"))

        # scenario 7: one row per product
        self.assertEqual(len(rows), len(set(by_offer.keys())))

        # scenario 8: governance metadata is present and stable
        self.assertGreaterEqual(by_offer["offer_multi"][13], 1)
        self.assertGreaterEqual(by_offer["offer_multi"][14], 1)
        self.assertFalse(by_offer["offer_multi"][15])
        self.assertGreaterEqual(by_offer["offer_multi"][16], Decimal("0"))

        # scenario 9: compact backlog lane mapping
        for row in rows:
            action_priority = row[4]
            execution_lane = row[17]
            if action_priority <= 3:
                self.assertEqual(execution_lane, "do_now")
            elif action_priority <= 10:
                self.assertEqual(execution_lane, "next")
            else:
                self.assertEqual(execution_lane, "later")
