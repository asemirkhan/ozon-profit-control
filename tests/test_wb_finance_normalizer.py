from __future__ import annotations

from unittest import TestCase

from src.normalizers.wb.finance_normalizer import normalize_wb_finance


class TestWbFinanceNormalizer(TestCase):
    def test_maps_operation_type_and_financial_amounts(self) -> None:
        rows = normalize_wb_finance(
            [
                {
                    "response": {
                        "finance": [
                            {
                                "rrd_id": "fin-1",
                                "date": "2026-03-01T00:00:00Z",
                                "supplier_oper_name": "Логистика",
                                "nm_id": 1001,
                                "supplierArticle": "offer-1",
                                "ppvz_sales_commission": "-10.50",
                                "delivery_rub": "7.25",
                                "acquiring_fee": "1.10",
                                "ppvz_reward": "-2.30",
                            }
                        ]
                    }
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["operation_type"], "Логистика")
        self.assertEqual(str(row["commission_amount"]), "-10.50")
        self.assertEqual(str(row["logistics_amount"]), "7.25")
        self.assertEqual(str(row["service_amount"]), "1.10")
        self.assertEqual(str(row["amount"]), "-2.30")
        self.assertEqual(row["operation_date_source"], "date")

    def test_prefers_rr_dt_for_operation_date(self) -> None:
        rows = normalize_wb_finance(
            [
                {
                    "response": {
                        "finance": [
                            {
                                "rrd_id": "fin-rrdt",
                                "create_dt": "2026-03-12T23:59:59Z",
                                "rr_dt": "2026-03-05T10:00:00Z",
                                "sale_dt": "2026-03-06T11:00:00Z",
                                "supplier_oper_name": "Комиссия",
                                "nm_id": 1002,
                                "supplierArticle": "offer-2",
                                "ppvz_sales_commission": "-5.00",
                            }
                        ]
                    }
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        # rr_dt must drive operation_date to keep day-level backfill closure correct.
        self.assertEqual(rows[0]["operation_date"].date().isoformat(), "2026-03-05")
        self.assertEqual(rows[0]["operation_date_source"], "rr_dt")
