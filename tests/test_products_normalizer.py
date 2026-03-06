from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from src.normalizers.ozon.products_normalizer import normalize_products


class TestNormalizeProducts(TestCase):
    def test_normalize_products_from_sample(self) -> None:
        sample_path = Path(__file__).resolve().parents[1] / "examples" / "products_sample.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))

        rows = normalize_products([payload])

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["offer_id"], "offer-1")
        self.assertEqual(rows[0]["product_id"], "1001")
        self.assertEqual(rows[0]["sku"], "5001")
        self.assertEqual(rows[0]["product_name"], "Sample Product A")
        self.assertEqual(rows[0]["brand"], "Brand A")
        self.assertEqual(rows[0]["category_id"], "10")
        self.assertEqual(rows[0]["category_name"], "Category A")
        self.assertFalse(rows[0]["archived"])
        self.assertIn("updated_at", rows[0])

    def test_skip_row_without_offer_id(self) -> None:
        payload = {"result": {"items": [{"product_id": 1}]}}
        rows = normalize_products([payload])
        self.assertEqual(rows, [])
