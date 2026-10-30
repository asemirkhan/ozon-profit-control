from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from src.normalizers.ozon.inventory_normalizer import normalize_inventory


class TestInventoryNormalizer(TestCase):
    def test_normalize_inventory_from_sample(self) -> None:
        sample_path = Path(__file__).resolve().parents[1] / "examples" / "inventory_sample.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))

        rows = normalize_inventory([payload], snapshot_date=date(2026, 3, 8))

        self.assertEqual(len(rows), 3)

        first = rows[0]
        self.assertEqual(first["snapshot_date"], date(2026, 3, 8))
        self.assertEqual(first["offer_id"], "offer-1")
        self.assertEqual(first["sku"], "sku-1")
        self.assertEqual(first["product_id"], "prod-1")
        self.assertEqual(first["warehouse_name"], "Main Warehouse")
        self.assertEqual(first["warehouse_type"], "fbo")
        self.assertEqual(first["stock_total"], Decimal("25"))
        self.assertEqual(first["stock_available"], Decimal("20"))
        self.assertEqual(first["stock_reserved"], Decimal("4"))
        self.assertEqual(first["stock_defect"], Decimal("1"))

        missing_ids = [row for row in rows if row["sku"] == "sku-2"][0]
        self.assertEqual(missing_ids["offer_id"], "~")
        self.assertEqual(missing_ids["product_id"], "~")
        self.assertEqual(missing_ids["warehouse_name"], "Reserve Warehouse")
        self.assertEqual(missing_ids["stock_total"], Decimal("-2"))
        self.assertEqual(missing_ids["stock_available"], Decimal("-2"))

    def test_normalize_flat_inventory_rows(self) -> None:
        payload = {
            "result": {
                "rows": [
                    {
                        "warehouse_name": "WH A",
                        "warehouse_type": "fbs",
                        "free_to_sell_amount": "5",
                        "promised_amount": "1",
                    }
                ]
            }
        }

        rows = normalize_inventory([payload], snapshot_date="2026-03-09")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["snapshot_date"], date(2026, 3, 9))
        self.assertEqual(row["offer_id"], "~")
        self.assertEqual(row["sku"], "~")
        self.assertEqual(row["product_id"], "~")
        self.assertEqual(row["warehouse_name"], "WH A")
        self.assertEqual(row["warehouse_type"], "fbs")
        self.assertEqual(row["stock_total"], Decimal("6"))
        self.assertEqual(row["stock_available"], Decimal("5"))
        self.assertEqual(row["stock_reserved"], Decimal("1"))
