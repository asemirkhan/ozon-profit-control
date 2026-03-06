from __future__ import annotations

from unittest import TestCase

from src.normalizers.ozon.postings_fbs_normalizer import normalize_postings_fbs


class TestPostingsFbsNormalizer(TestCase):
    def test_normal_posting_with_two_items(self) -> None:
        pages = [
            {
                "result": {
                    "postings": [
                        {
                            "posting_number": "P-1",
                            "status": "delivered",
                            "created_at": "2026-02-01T10:00:00Z",
                            "products": [
                                {
                                    "offer_id": "offer-1",
                                    "sku": "sku-1",
                                    "product_id": "prod-1",
                                    "quantity": 1,
                                    "price": "100.00",
                                    "currency": "RUB",
                                },
                                {
                                    "offer_id": "offer-2",
                                    "sku": "sku-2",
                                    "product_id": "prod-2",
                                    "quantity": 2,
                                    "price": "200.00",
                                    "currency": "RUB",
                                },
                            ],
                        }
                    ]
                }
            }
        ]

        rows_posting, rows_items, rows_financial = normalize_postings_fbs(pages)

        self.assertEqual(len(rows_posting), 1)
        self.assertEqual(len(rows_items), 2)
        self.assertEqual(rows_posting[0]["posting_number"], "P-1")
        self.assertEqual(rows_items[0]["offer_id"], "offer-1")
        self.assertEqual(rows_items[1]["sku"], "sku-2")
        self.assertEqual(rows_financial, [])

    def test_missing_offer_and_sku_fallback_to_tilde(self) -> None:
        pages = [
            {
                "result": {
                    "postings": [
                        {
                            "posting_number": "P-2",
                            "created_at": "2026-02-02T10:00:00Z",
                            "products": [
                                {
                                    "product_id": "prod-x",
                                    "quantity": 1,
                                }
                            ],
                        }
                    ]
                }
            }
        ]

        rows_posting, rows_items, _rows_financial = normalize_postings_fbs(pages)

        self.assertEqual(len(rows_posting), 1)
        self.assertEqual(len(rows_items), 1)
        self.assertEqual(rows_items[0]["offer_id"], "~")
        self.assertEqual(rows_items[0]["sku"], "~")
        self.assertEqual(rows_items[0]["product_id"], "prod-x")
