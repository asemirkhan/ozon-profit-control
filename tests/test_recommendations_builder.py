from __future__ import annotations

from unittest import TestCase

from src.services.recommendations import build_recommendation_payload, enrich_action_row


class TestRecommendationsBuilder(TestCase):
    def test_build_payload_contract(self) -> None:
        payload = build_recommendation_payload(
            {
                "primary_rule_code": "negative_profit_after_ads",
                "action_code": "fix_unprofitable_ads",
                "estimated_gain_if_fixed": 12500,
                "action_window_days": 7,
                "confidence": "medium",
                "attribution_quality": "fallback_match",
                "profit_after_ads": -3200,
                "ads_spend": 6100,
            }
        )

        required_keys = {
            "rule_code",
            "rule_label",
            "action_code",
            "action_title",
            "problem_title",
            "problem_summary",
            "what_to_do",
            "where_to_do_it",
            "action_window_days",
            "check_after_window",
            "success_signal",
            "if_not_improved",
            "do_not_do",
            "expected_effect_note",
            "estimated_gain_if_fixed",
            "confidence",
            "attribution_quality",
        }
        self.assertTrue(required_keys.issubset(payload.keys()))
        self.assertEqual(payload["rule_label"], "Убыток после рекламы")
        self.assertIn("товар", payload["problem_summary"].lower())

    def test_enrich_action_row_uses_builder_text(self) -> None:
        row = {
            "primary_rule_code": "stockout_risk",
            "action_code": "replenish_stock",
            "action_window_days": 7,
            "estimated_gain_if_fixed": 70000,
            "confidence": "high",
            "attribution_quality": "exact_match",
        }

        enriched = enrich_action_row(row)
        self.assertEqual(enriched["first_step"], enriched["what_to_do"])
        self.assertIn("пополн", enriched["what_to_do"].lower())
        self.assertNotIn("sku", enriched["what_to_do"].lower())
        self.assertEqual(enriched["recommendation"]["action_code"], "replenish_stock")
