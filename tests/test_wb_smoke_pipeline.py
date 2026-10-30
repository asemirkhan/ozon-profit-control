from __future__ import annotations

from datetime import date
from unittest import TestCase

from psycopg.types.json import Jsonb

from src.cli import _map_wb_ads_to_fact, _map_wb_stock_to_fact
from src.loaders.upsert_wb import _prepare_rows_for_db
from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.normalizers.wb.finance_normalizer import normalize_wb_finance
from src.normalizers.wb.orders_normalizer import normalize_wb_orders
from src.normalizers.wb.products_normalizer import normalize_wb_products
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.normalizers.wb.stock_normalizer import normalize_wb_stock


class TestWbSmokePipeline(TestCase):
    def test_smoke_normalize_and_prepare_rows(self) -> None:
        products_pages = [
            {
                "response": {
                    "result": {
                        "cards": [
                            {
                                "nmID": 1001,
                                "vendorCode": "offer-1",
                                "title": "Product 1",
                                "brand": "Brand",
                                "subjectName": "Cat",
                                "sizes": [{"skus": ["sku-1"]}],
                            }
                        ]
                    }
                }
            }
        ]
        orders_pages = [
            {
                "response": {
                    "orders": [
                        {
                            "srid": "order-1",
                            "date": "2026-03-01T00:00:00Z",
                            "nmId": 1001,
                            "supplierArticle": "offer-1",
                            "barcode": "sku-1",
                            "quantity": 1,
                            "totalPrice": "1000.00",
                        }
                    ]
                }
            }
        ]
        sales_pages = [
            {
                "response": {
                    "sales": [
                        {
                            "saleID": "sale-1",
                            "date": "2026-03-01T00:00:00Z",
                            "srid": "order-1",
                            "nmId": 1001,
                            "supplierArticle": "offer-1",
                            "barcode": "sku-1",
                            "quantity": 1,
                            "forPay": "950.00",
                        }
                    ]
                }
            }
        ]
        finance_pages = [
            {
                "response": {
                    "finance": [
                        {
                            "rrd_id": "fin-1",
                            "date": "2026-03-01T00:00:00Z",
                            "nmId": 1001,
                            "supplierArticle": "offer-1",
                            "ppvz_for_pay": "900.00",
                        }
                    ]
                }
            }
        ]
        stock_pages = [
            {
                "response": {
                    "stocks": [
                        {
                            "date": "2026-03-01",
                            "nmId": 1001,
                            "supplierArticle": "offer-1",
                            "barcode": "sku-1",
                            "warehouseName": "WH1",
                            "quantity": 10,
                        }
                    ]
                }
            }
        ]
        ads_pages = [
            {
                "response": {
                    "ads": [
                        {
                            "advertId": 777,
                            "days": [
                                {
                                    "date": "2026-03-01",
                                    "apps": [
                                        {
                                            "nms": [
                                                {
                                                    "nmId": 1001,
                                                    "sum": 120,
                                                    "clicks": 10,
                                                    "views": 200,
                                                }
                                            ]
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                }
            }
        ]

        products_rows = normalize_wb_products(products_pages)
        orders_rows = normalize_wb_orders(orders_pages)
        sales_rows = normalize_wb_sales(sales_pages)
        finance_rows = normalize_wb_finance(finance_pages)
        stock_rows = normalize_wb_stock(stock_pages, snapshot_date=date(2026, 3, 1))
        ads_rows = normalize_wb_ads(ads_pages)

        self.assertEqual(len(products_rows), 1)
        self.assertEqual(len(orders_rows), 1)
        self.assertEqual(len(sales_rows), 1)
        self.assertEqual(len(finance_rows), 1)
        self.assertEqual(len(stock_rows), 1)
        self.assertEqual(len(ads_rows), 1)

        for rows in [products_rows, orders_rows, sales_rows, finance_rows, stock_rows, ads_rows]:
            prepared = _prepare_rows_for_db(rows)
            self.assertIsInstance(prepared[0]["raw_payload"], Jsonb)

        stock_fact_rows = _map_wb_stock_to_fact(stock_rows)
        ads_fact_rows = _map_wb_ads_to_fact(ads_rows)
        self.assertEqual(len(stock_fact_rows), 1)
        self.assertEqual(len(ads_fact_rows), 1)
