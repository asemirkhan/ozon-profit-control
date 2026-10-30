from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from src.normalizers.ozon.ads_normalizer import normalize_ads


class TestAdsNormalizer(TestCase):
    def test_normalize_ads_from_sample(self) -> None:
        sample_path = Path(__file__).resolve().parents[1] / "examples" / "ads_sample.json"
        payload = json.loads(sample_path.read_text(encoding="utf-8"))

        rows = normalize_ads([payload])

        self.assertEqual(len(rows), 7)
        first = rows[0]
        self.assertEqual(first["date"], date(2026, 2, 1))
        self.assertEqual(first["campaign_id"], "cmp-search-1001")
        self.assertEqual(first["offer_id"], "offer-1")
        self.assertEqual(first["sku"], "sku-1")
        self.assertEqual(first["product_id"], "prod-1")
        self.assertEqual(first["spend"], Decimal("12650.40"))
        self.assertEqual(first["impressions"], Decimal("21500"))
        self.assertEqual(first["clicks"], Decimal("640"))
        self.assertEqual(first["orders_count"], Decimal("52"))
        self.assertEqual(first["revenue"], Decimal("183400.00"))

    def test_fill_tilde_for_missing_offer_sku_product(self) -> None:
        payload = {
            "result": {
                "campaigns": [
                    {
                        "campaign_id": "cmp-1",
                        "campaign_name": "Campaign 1",
                        "products": [
                            {
                                "stats": [
                                    {
                                        "date": "2026-03-01T12:00:00Z",
                                        "spend": "100.50",
                                    }
                                ]
                            }
                        ],
                    }
                ]
            }
        }

        rows = normalize_ads([payload])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], date(2026, 3, 1))
        self.assertEqual(rows[0]["offer_id"], "~")
        self.assertEqual(rows[0]["sku"], "~")
        self.assertEqual(rows[0]["product_id"], "~")
        self.assertEqual(rows[0]["spend"], Decimal("100.50"))
