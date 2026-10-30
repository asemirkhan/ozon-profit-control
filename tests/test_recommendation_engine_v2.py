from __future__ import annotations

import unittest
from dataclasses import replace

from src.services.recommendation_engine_v2 import (
    DataQuality,
    DaySignal,
    Decision,
    PeriodMetrics,
    ProductContext,
    evaluate_product,
)


def period(
    *, result=10000, units=20, price=1000, logistics_per_unit=100,
    result_per_unit=500, start="2026-05-01", end="2026-05-31", days=31,
):
    return PeriodMetrics(
        period_start=start, period_end=end, days=days,
        revenue=units * price, total_expenses=units * price - result,
        result=result, sales_units=units, orders=units,
        average_selling_price=price, result_per_unit=result_per_unit,
        expense_per_unit=(units * price - result) / units if units else None,
        logistics=units * logistics_per_unit,
        logistics_per_unit=logistics_per_unit,
    )


def context(current=None, previous=None, quality=None, **kwargs):
    return ProductContext(
        product_key="wb:norm:sku:123", product_name="Тестовый товар",
        current_month=current or period(start="2026-06-01", end="2026-06-30", days=30),
        previous_month=previous or period(), quality=quality or DataQuality(
            snapshot_id="snapshot-1", confirmed_through="2026-06-30",
            last_full_operational_day="2026-07-10",
        ), **kwargs,
    )


class RecommendationEngineV2Tests(unittest.TestCase):
    def test_incomplete_period_blocks_business_recommendation(self):
        q = DataQuality(
            snapshot_id="s", confirmed_through=None, last_full_operational_day=None,
            current_period_complete=False,
        )
        result = evaluate_product(context(quality=q))
        self.assertEqual(result.decision, Decision.BLOCKED)
        self.assertEqual(result.scenario_code, "DQ_BLOCKED")

    def test_low_volume_keeps_problem_but_lowers_cause_confidence(self):
        q = DataQuality(
            snapshot_id="s", confirmed_through="2026-06-30",
            last_full_operational_day="2026-07-10", minimum_sample_passed=False,
        )
        result = evaluate_product(context(
            quality=q,
            previous=period(result=9000, units=9),
            current=period(result=800, units=1, start="2026-06-01", end="2026-06-30", days=30),
        ))
        self.assertEqual(result.decision, Decision.DIAGNOSTIC_CHECK)
        self.assertEqual(result.scenario_code, "DEMAND_VOLUME_DOWN")
        self.assertEqual(result.confidence, "low")
        self.assertGreater(result.impact_amount, 0)

    def test_month_down_week_up_becomes_signal_not_action(self):
        result = evaluate_product(context(
            previous=period(result=20000, units=30),
            current=period(result=12000, units=22, start="2026-06-01", end="2026-06-30", days=30),
            previous_7d=period(result=2000, units=5, start="2026-06-17", end="2026-06-23", days=7),
            current_7d=period(result=4000, units=7, start="2026-06-24", end="2026-06-30", days=7),
        ))
        self.assertEqual(result.scenario_code, "MONTH_DOWN_WEEK_RECOVERY")
        self.assertEqual(result.decision, Decision.SIGNAL_ONLY)
        self.assertIn("следующие 7 полных дней", result.action or "")

    def test_logistics_growth_produces_specific_action(self):
        result = evaluate_product(context(
            previous=period(result=20000, units=40, logistics_per_unit=200, result_per_unit=500),
            current=period(result=12000, units=40, logistics_per_unit=320, result_per_unit=300, start="2026-06-01", end="2026-06-30", days=30),
        ))
        self.assertEqual(result.scenario_code, "LOGISTICS_UNIT_UP")
        self.assertEqual(result.decision, Decision.DIAGNOSTIC_CHECK)
        self.assertIn("200 ₽", result.full_problem)
        self.assertIn("320 ₽", result.full_problem)
        self.assertIsNotNone(result.success_criterion)
        self.assertIsNotNone(result.next_step)

    def test_price_down_without_volume_does_not_claim_causality(self):
        result = evaluate_product(context(
            previous=period(result=15000, units=20, price=1200),
            current=period(result=12000, units=18, price=1000, start="2026-06-01", end="2026-06-30", days=30),
        ))
        self.assertEqual(result.scenario_code, "PRICE_DOWN_WITHOUT_VOLUME")
        self.assertEqual(result.decision, Decision.DIAGNOSTIC_CHECK)
        self.assertNotIn("цена вызвала", result.full_problem.lower())
        self.assertEqual(result.cause_status, "partially_confirmed")
        self.assertIn("Сравните цену продавца", result.action or "")

    def test_stock_risk_has_priority_over_financial_symptoms(self):
        result = evaluate_product(context(
            previous=period(result=20000, units=40),
            current=period(result=8000, units=20, start="2026-06-01", end="2026-06-30", days=30),
            current_stock=3, days_of_stock=2.2,
        ))
        self.assertEqual(result.scenario_code, "STOCK_LT_3D")
        self.assertEqual(result.decision, Decision.URGENT_ACTION)
        self.assertIn("DEMAND_VOLUME_DOWN", result.suppressed_scenarios)

    def test_historical_availability_limit_requires_warehouse_check(self):
        result = evaluate_product(context(
            previous=period(result=20000, units=40),
            current=period(result=9000, units=20, start="2026-06-01", end="2026-06-30", days=30),
            stock_limited_days_current=6,
        ))
        self.assertEqual(result.scenario_code, "AVAILABILITY_LIMITED")
        self.assertEqual(result.action_type, "diagnostic_check")
        self.assertIn("склад", (result.action or "").lower())

    def test_ads_growth_without_attribution_stays_diagnostic(self):
        previous = period(result=20000, units=40)
        current = period(result=10000, units=40, start="2026-06-01", end="2026-06-30", days=30)
        previous = PeriodMetrics(**{**previous.__dict__, "ads_spend": 1000})
        current = PeriodMetrics(**{**current.__dict__, "ads_spend": 7000})
        result = evaluate_product(context(previous=previous, current=current))
        self.assertEqual(result.scenario_code, "ADS_COST_UP")
        self.assertEqual(result.decision, Decision.DIAGNOSTIC_CHECK)
        self.assertNotIn("останов", (result.action or "").lower())

    def test_one_day_signal_requires_same_weekday_history(self):
        bad = evaluate_product(context(day_signal=DaySignal(
            day="2026-07-10", result=100, baseline_median=1000,
            comparable_days=3, is_full_day=True,
        )))
        self.assertNotEqual(bad.scenario_code, "ONE_DAY_ALERT")
        good = evaluate_product(context(day_signal=DaySignal(
            day="2026-07-10", result=100, baseline_median=1000,
            comparable_days=6, is_full_day=True,
        )))
        self.assertEqual(good.scenario_code, "ONE_DAY_ALERT")
        self.assertEqual(good.decision, Decision.SIGNAL_ONLY)

    def test_unreconciled_ads_only_limits_confidence(self):
        q = DataQuality(
            snapshot_id="s", confirmed_through="2026-06-30",
            last_full_operational_day="2026-07-10",
            ads_reconciliation_status="gap",
        )
        result = evaluate_product(context(quality=q))
        self.assertTrue(any("Рекламный слой не сверен" in x for x in result.blocked_reasons))
        self.assertNotIn("останов", (result.action or "").lower())

    def test_positive_growth_has_no_forced_action(self):
        result = evaluate_product(context(
            previous=period(result=10000, units=20),
            current=period(result=18000, units=30, result_per_unit=600, start="2026-06-01", end="2026-06-30", days=30),
        ))
        self.assertEqual(result.scenario_code, "STABLE_GROWTH")
        self.assertEqual(result.decision, Decision.POSITIVE_STATUS)
        self.assertIsNone(result.action)

    def test_result_is_machine_readable(self):
        payload = evaluate_product(context()).to_dict()
        self.assertEqual(payload["decision"], "no_action")
        self.assertIsInstance(payload["confirmed_facts"], tuple)
        self.assertTrue(all(check["passed"] for check in payload["audit_checks"]))

    def test_every_scenario_family_passes_all_client_criteria(self):
        base_previous = period(result=20000, units=40)
        base_current = period(result=10000, units=20, start="2026-06-01", end="2026-06-30", days=30)
        ads_previous = replace(base_previous, ads_spend=1000)
        ads_current = replace(base_current, sales_units=40, ads_spend=7000)
        cases = [
            context(quality=DataQuality(snapshot_id="s", confirmed_through=None, last_full_operational_day=None, current_period_complete=False)),
            context(previous=base_previous, current=base_current, current_stock=0, days_of_stock=0),
            context(previous=base_previous, current=base_current, current_stock=2, days_of_stock=2),
            context(previous=base_previous, current=base_current, current_stock=8, days_of_stock=5),
            context(current_stock=20, days_of_stock=10),
            context(previous=base_previous, current=base_current, stock_limited_days_current=5),
            context(
                previous=base_previous, current=base_current,
                previous_7d=period(result=2000, units=5, start="2026-06-17", end="2026-06-23", days=7),
                current_7d=period(result=4000, units=7, start="2026-06-24", end="2026-06-30", days=7),
            ),
            context(
                previous=period(result=20000, units=40, logistics_per_unit=200, result_per_unit=500),
                current=period(result=12000, units=40, logistics_per_unit=320, result_per_unit=300, start="2026-06-01", end="2026-06-30", days=30),
            ),
            context(
                previous=period(result=15000, units=20, price=1200),
                current=period(result=12000, units=18, price=1000, start="2026-06-01", end="2026-06-30", days=30),
            ),
            context(previous=ads_previous, current=ads_current),
            context(previous=period(result=9000, units=9), current=period(result=800, units=1, start="2026-06-01", end="2026-06-30", days=30)),
            context(
                previous=period(result=20000, units=40, result_per_unit=500),
                current=period(result=12000, units=40, result_per_unit=300, start="2026-06-01", end="2026-06-30", days=30),
            ),
            context(day_signal=DaySignal(day="2026-07-10", result=100, baseline_median=1000, comparable_days=6, is_full_day=True)),
            context(previous=period(result=10000, units=20), current=period(result=18000, units=30, result_per_unit=600, start="2026-06-01", end="2026-06-30", days=30)),
            context(),
        ]
        results = [evaluate_product(item) for item in cases]
        expected = {
            "DQ_BLOCKED", "STOCK_ZERO", "STOCK_LT_3D", "STOCK_3_7D", "STOCK_7_14D",
            "AVAILABILITY_LIMITED", "MONTH_DOWN_WEEK_RECOVERY", "LOGISTICS_UNIT_UP",
            "PRICE_DOWN_WITHOUT_VOLUME", "ADS_COST_UP", "DEMAND_VOLUME_DOWN",
            "UNIT_ECONOMICS_DOWN", "ONE_DAY_ALERT", "STABLE_GROWTH", "NORMAL_VARIATION",
        }
        self.assertEqual({result.scenario_code for result in results}, expected)
        for result in results:
            with self.subTest(result.scenario_code):
                self.assertTrue(all(check["passed"] for check in result.audit_checks), result.audit_checks)


if __name__ == "__main__":
    unittest.main()
