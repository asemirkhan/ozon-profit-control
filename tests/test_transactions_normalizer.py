from __future__ import annotations

from unittest import TestCase

from src.normalizers.ozon.transactions_normalizer import normalize_transactions


class TestTransactionsNormalizer(TestCase):
    def test_normal_operation_with_services_and_items(self) -> None:
        pages = [
            {
                "result": {
                    "operations": [
                        {
                            "operation_id": "op-1",
                            "operation_date": "2026-02-01T10:00:00Z",
                            "operation_type": "sale",
                            "amount": "100.00",
                            "services": [
                                {"name": "delivery", "price": "10.00"},
                                {"name": "acquiring", "price": "5.00"},
                            ],
                            "items": [
                                {"sku": "sku-1", "name": "Item 1"},
                                {"sku": "sku-2", "name": "Item 2"},
                            ],
                        }
                    ]
                }
            }
        ]

        rows_tx, rows_services, rows_items = normalize_transactions(pages)

        self.assertEqual(len(rows_tx), 1)
        self.assertEqual(len(rows_services), 2)
        self.assertEqual(len(rows_items), 2)
        self.assertEqual(rows_tx[0]["operation_id"], "op-1")
        self.assertEqual(rows_services[0]["operation_id"], "op-1")
        self.assertEqual(rows_items[0]["operation_id"], "op-1")

    def test_skip_operation_without_required_fields(self) -> None:
        pages = [
            {
                "result": {
                    "operations": [
                        {"operation_date": "2026-02-01T10:00:00Z", "operation_type": "sale"},
                        {"operation_id": "op-2", "operation_type": "sale"},
                    ]
                }
            }
        ]

        rows_tx, rows_services, rows_items = normalize_transactions(pages)

        self.assertEqual(rows_tx, [])
        self.assertEqual(rows_services, [])
        self.assertEqual(rows_items, [])
