from __future__ import annotations

from pathlib import Path
from unittest import TestCase

from dotenv import load_dotenv

from src.utils.db import execute_sql_file, get_conn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class TestMartActionPlaybook14d(TestCase):
    schema = "test_action_playbook_v1"
    run_date = "2026-03-09"
    period_start = "2026-02-01"
    period_end = "2026-03-01"
    clean_offer = "offer_clean"
    expected_action_codes = {
        "fix_unprofitable_ads",
        "optimize_ads_profitability",
        "reduce_logistics_burden",
        "improve_margin",
        "replenish_stock",
        "stop_wasteful_ads",
    }
    expected_issue_offers = {
        "offer_unprof",
        "offer_ads_eating",
        "offer_logistics",
        "offer_margin",
        "offer_stock",
        "offer_ads_no_sales",
    }

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._prepare_schema()
        cls._seed_inputs()
        cls._build_playbook()

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
                        CREATE TABLE {cls.schema}.mart_action_queue_14d (
                          run_date DATE NOT NULL,
                          period_start DATE NOT NULL,
                          period_end DATE NOT NULL,
                          marketplace TEXT NOT NULL,
                          offer_id TEXT NOT NULL,
                          product_name TEXT NULL,
                          primary_rule_code TEXT NOT NULL,
                          action_code TEXT NOT NULL,
                          action_priority INTEGER NOT NULL,
                          action_priority_score NUMERIC NOT NULL,
                          severity TEXT NOT NULL,
                          attribution_quality TEXT NULL,
                          confidence TEXT NULL,
                          action_title TEXT NOT NULL,
                          action_summary TEXT NOT NULL,
                          why_now TEXT NOT NULL,
                          recommended_action TEXT NOT NULL,
                          expected_effect_note TEXT NULL,
                          money_impact_30d NUMERIC NULL,
                          revenue_at_risk_30d NUMERIC NULL,
                          estimated_profit_uplift_30d NUMERIC NULL,
                          estimated_revenue_protection_30d NUMERIC NULL,
                          estimated_gain_if_fixed NUMERIC NULL,
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
                          stockout_effect_confidence TEXT NULL,
                          total_action_impact_30d NUMERIC NULL,
                          supporting_rule_codes TEXT NOT NULL,
                          supporting_problem_count INTEGER NOT NULL,
                          source_layer TEXT NOT NULL,
                          action_version TEXT NOT NULL
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
                          offer_id TEXT NOT NULL,
                          metric_value NUMERIC NULL,
                          threshold_value NUMERIC NULL
                        )
                        """
                    )

    @classmethod
    def _seed_inputs(cls) -> None:
        action_rows = [
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_unprof",
                "Mixed Unprofitable",
                "negative_profit_after_ads",
                "fix_unprofitable_ads",
                1,
                92,
                "high",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                1900,
                None,
                1900,
                1900,
                "negative_profit_after_ads",
                1,
                "mart_rule_hits_30d",
                "v1",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_ads_eating",
                "Mixed Ads Eating",
                "ads_eating_profit",
                "optimize_ads_profitability",
                2,
                88,
                "high",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                4000,
                None,
                4000,
                4000,
                "ads_eating_profit",
                1,
                "mart_rule_hits_30d",
                "v1",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_logistics",
                "Mixed Logistics",
                "high_logistics_share",
                "reduce_logistics_burden",
                3,
                75,
                "medium",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                1000,
                None,
                1000,
                1000,
                "high_logistics_share",
                1,
                "mart_rule_hits_30d",
                "v1",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_margin",
                "Mixed Margin",
                "low_margin_after_ads",
                "improve_margin",
                4,
                70,
                "medium",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                800,
                None,
                800,
                800,
                "low_margin_after_ads",
                1,
                "mart_rule_hits_30d",
                "v1",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_stock",
                "Mixed Stockout Multi",
                "stockout_risk",
                "replenish_stock",
                5,
                85,
                "high",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                None,
                48000,
                48000,
                48000,
                "stockout_risk, high_logistics_share",
                2,
                "mart_rule_hits_30d",
                "v1",
            ),
            (
                cls.run_date,
                cls.period_start,
                cls.period_end,
                "offer_ads_no_sales",
                "Mixed Wasteful Ads",
                "ads_without_sales",
                "stop_wasteful_ads",
                6,
                80,
                "high",
                "exact_match",
                "high",
                "x",
                "x",
                "x",
                "x",
                None,
                1200,
                None,
                1200,
                1200,
                "ads_without_sales",
                1,
                "mart_rule_hits_30d",
                "v1",
            ),
        ]
        rule_rows = [
            (cls.run_date, cls.period_start, cls.period_end, "negative_profit_after_ads", "offer_unprof", -1900, 0),
            (cls.run_date, cls.period_start, cls.period_end, "ads_eating_profit", "offer_ads_eating", 0.20, 0.5),
            (cls.run_date, cls.period_start, cls.period_end, "high_logistics_share", "offer_logistics", 0.30, 0.2),
            (cls.run_date, cls.period_start, cls.period_end, "low_margin_after_ads", "offer_margin", 0.05, 0.1),
            (cls.run_date, cls.period_start, cls.period_end, "stockout_risk", "offer_stock", -1, 14),
            (cls.run_date, cls.period_start, cls.period_end, "ads_without_sales", "offer_ads_no_sales", 1200, 0),
        ]

        with get_conn() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_action_queue_14d (
                          run_date,
                          period_start,
                          period_end,
                          marketplace,
                          offer_id,
                          product_name,
                          primary_rule_code,
                          action_code,
                          action_priority,
                          action_priority_score,
                          severity,
                          attribution_quality,
                          confidence,
                          action_title,
                          action_summary,
                          why_now,
                          recommended_action,
                          expected_effect_note,
                          money_impact_30d,
                          revenue_at_risk_30d,
                          estimated_gain_if_fixed,
                          total_action_impact_30d,
                          supporting_rule_codes,
                          supporting_problem_count,
                          source_layer,
                          action_version
                        )
                        VALUES (
                          %s,%s,%s,'ozon',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                        )
                        """,
                        action_rows,
                    )
                    cur.executemany(
                        f"""
                        INSERT INTO {cls.schema}.mart_rule_hits_30d (
                          run_date,
                          period_start,
                          period_end,
                          marketplace,
                          rule_code,
                          offer_id,
                          metric_value,
                          threshold_value
                        )
                        VALUES (%s,%s,%s,'ozon',%s,%s,%s,%s)
                        """,
                        rule_rows,
                    )

    @classmethod
    def _build_playbook(cls) -> None:
        params = {
            "schema": cls.schema,
            "period_start": cls.period_start,
            "period_end": cls.period_end,
        }
        with get_conn() as conn:
            with conn.transaction():
                execute_sql_file(conn, "src/marts/sql/mart_action_playbook_14d.sql", params)

    def test_playbook_quality_and_passthrough(self) -> None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                      offer_id,
                      action_code,
                      playbook_title,
                      problem_summary,
                      why_now,
                      first_step,
                      action_window_days,
                      action_intensity_hint,
                      next_step,
                      check_after_window,
                      if_not_improved,
                      action_rationale,
                      why_this_window,
                      not_improved_criteria,
                      diagnostic_priority_1,
                      diagnostic_priority_2,
                      diagnostic_priority_3,
                      decision_path_summary,
                      do_not_do,
                      success_signal,
                      supporting_rule_codes,
                      supporting_problem_count,
                      money_impact_30d,
                      revenue_at_risk_30d,
                      estimated_gain_if_fixed,
                      total_action_impact_30d
                    FROM {self.schema}.mart_action_playbook_14d
                    ORDER BY offer_id
                    """
                )
                columns = [d.name for d in cur.description]
                rows = [dict(zip(columns, row)) for row in cur.fetchall()]

        by_offer = {r["offer_id"]: r for r in rows}
        action_codes = {r["action_code"] for r in rows}

        # 1) one-row-per-product
        self.assertEqual(len(rows), len(by_offer))
        self.assertSetEqual(action_codes, self.expected_action_codes)
        self.assertSetEqual(set(by_offer), self.expected_issue_offers)

        # 2) required not-null fields + 3) no weak empty text
        for row in rows:
            for key in (
                "playbook_title",
                "problem_summary",
                "why_now",
                "first_step",
                "next_step",
                "check_after_window",
                "if_not_improved",
                "action_rationale",
                "why_this_window",
                "not_improved_criteria",
                "diagnostic_priority_1",
                "diagnostic_priority_2",
                "diagnostic_priority_3",
                "decision_path_summary",
                "success_signal",
            ):
                value = row[key]
                self.assertIsNotNone(value)
                self.assertNotEqual(str(value).strip(), "")
                self.assertNotEqual(str(value).strip(), "~")

        # 4) action_window_days + intensity hint present
        for row in rows:
            self.assertEqual(row["action_window_days"], 7)
            self.assertIsNotNone(row["action_intensity_hint"])
            self.assertNotEqual(str(row["action_intensity_hint"]).strip(), "")
            self.assertNotEqual(str(row["action_intensity_hint"]).strip(), "~")

        # 5) wording should not be overly vague
        banned_patterns = [
            "короткий период",
            "ослабьте давление",
            "оцените результат",
            "общий смысл",
            "запас прочности",
        ]
        for row in rows:
            combined_text = " ".join(
                str(row[key]).lower()
                for key in (
                    "problem_summary",
                    "first_step",
                    "next_step",
                    "check_after_window",
                    "if_not_improved",
                    "action_rationale",
                    "why_this_window",
                    "not_improved_criteria",
                )
            )
            for pattern in banned_patterns:
                self.assertNotIn(pattern, combined_text)

        # 6) action-specific wording
        stock_row = by_offer["offer_stock"]
        self.assertIn("пополн", stock_row["first_step"].lower())
        self.assertNotIn("реклам", stock_row["first_step"].lower())

        fix_row = by_offer["offer_unprof"]
        self.assertIn("продвиж", fix_row["first_step"].lower())
        self.assertNotIn("логист", fix_row["first_step"].lower())

        # diagnostic logic differs by action
        self.assertIn("марж", fix_row["diagnostic_priority_2"].lower())
        self.assertIn("логист", fix_row["diagnostic_priority_3"].lower())
        self.assertIn("карточк", by_offer["offer_ads_no_sales"]["diagnostic_priority_2"].lower())
        self.assertIn("пополн", stock_row["diagnostic_priority_1"].lower())

        # 7) rationale and window explanation present
        for row in rows:
            self.assertIn("безопас", row["action_rationale"].lower())
            self.assertIn("7 дней", row["why_this_window"].lower())

        # 8) observable not-improved criteria present
        for row in rows:
            self.assertIn("если", row["not_improved_criteria"].lower())

        # 9) supporting context passthrough
        self.assertEqual(stock_row["supporting_rule_codes"], "stockout_risk, high_logistics_share")
        self.assertEqual(stock_row["supporting_problem_count"], 2)

        # 10) no_false_positive passthrough
        self.assertNotIn(self.clean_offer, by_offer)

        # 11) success signal present
        for row in rows:
            self.assertIsNotNone(row["success_signal"])
            self.assertNotEqual(str(row["success_signal"]).strip(), "")

        # impact fields are consistent for revenue-only stockout
        self.assertIsNone(stock_row["money_impact_30d"])
        self.assertEqual(stock_row["revenue_at_risk_30d"], 48000)
        self.assertEqual(stock_row["estimated_gain_if_fixed"], 48000)
        self.assertEqual(stock_row["total_action_impact_30d"], 48000)
