from __future__ import annotations

from unittest import TestCase

from src.ingestion.publish_gate import get_publish_rules, source_criticality


class TestPublishGateRules(TestCase):
    def test_wb_critical_sources(self) -> None:
        rules = {r.source: r for r in get_publish_rules("wb")}
        self.assertTrue(rules["products"].critical_for_publish)
        self.assertTrue(rules["sales"].critical_for_publish)
        self.assertTrue(rules["finance"].critical_for_publish)
        self.assertFalse(rules["ads"].critical_for_publish)
        self.assertFalse(rules["stock"].critical_for_publish)
        self.assertFalse(rules["orders"].critical_for_publish)

    def test_ozon_critical_sources(self) -> None:
        rules = {r.source: r for r in get_publish_rules("ozon")}
        self.assertTrue(rules["products"].critical_for_publish)
        self.assertTrue(rules["postings"].critical_for_publish)
        self.assertTrue(rules["transactions"].critical_for_publish)
        self.assertFalse(rules["inventory"].critical_for_publish)
        self.assertFalse(rules["ads"].critical_for_publish)

    def test_source_criticality(self) -> None:
        self.assertEqual(source_criticality("wb", "sales"), "critical")
        self.assertEqual(source_criticality("wb", "ads"), "noncritical")
        self.assertEqual(source_criticality("ozon", "transactions"), "critical")
        self.assertEqual(source_criticality("ozon", "inventory"), "noncritical")
