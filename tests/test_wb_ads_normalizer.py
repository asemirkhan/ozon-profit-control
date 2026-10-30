from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import TestCase

from src.normalizers.wb.ads_normalizer import normalize_wb_ads


class TestWbAdsNormalizer(TestCase):
    def test_normalize_v3_fullstats_shape_with_nms(self) -> None:
        payload = {
            "ads": [
                {
                    "advertId": 777,
                    "name": "Campaign A",
                    "type": 8,
                    "days": [
                        {
                            "date": "2026-03-01T00:00:00+03:00",
                            "apps": [
                                {
                                    "sum": 120,
                                    "clicks": 11,
                                    "views": 210,
                                    "nms": [
                                        {
                                            "nmId": 1001,
                                            "sum": 70,
                                            "clicks": 7,
                                            "views": 150,
                                        },
                                        {
                                            "nmId": 1002,
                                            "sum": 50,
                                            "clicks": 4,
                                            "views": 60,
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }

        rows = normalize_wb_ads([payload])

        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["date"], date(2026, 3, 1))
        self.assertEqual(first["campaign_id"], "777")
        self.assertEqual(first["nm_id"], "1001")
        self.assertEqual(first["offer_id"], "wb_nm_1001")
        self.assertEqual(first["spend"], Decimal("70"))
        self.assertEqual(first["impressions"], Decimal("150"))
        self.assertEqual(first["clicks"], Decimal("7"))

    def test_skips_rows_without_date_or_spend(self) -> None:
        payload = {
            "ads": [
                {"advertId": 1, "days": [{"apps": [{"nms": [{"nmId": 1}]}]}]},
            ]
        }

        rows = normalize_wb_ads([payload])
        self.assertEqual(rows, [])
