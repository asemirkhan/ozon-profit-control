from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.connectors.wb.stock import fetch_wb_stock_pages


class TestWbStockConnector(TestCase):
    @patch("src.connectors.wb.stock.get_wb_headers", return_value={"Authorization": "token"})
    @patch("src.connectors.wb.stock.request_json_with_status")
    def test_uses_current_warehouse_stock_api(self, request_mock, _headers_mock) -> None:
        request_mock.return_value = (
            {
                "data": {
                    "items": [
                        {
                            "nmId": 123,
                            "warehouseId": 4,
                            "warehouseName": "Коледино",
                            "quantity": 9,
                        }
                    ]
                }
            },
            200,
        )

        pages = fetch_wb_stock_pages("2026-07-26", limit=100)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["response"]["stocks"][0]["nmId"], 123)
        request_mock.assert_called_once_with(
            method="POST",
            url="https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses",
            headers={"Authorization": "token"},
            payload={"limit": 100, "offset": 0},
            rate_api_category="analytics",
            rate_endpoint="/api/analytics/v1/stocks-report/wb-warehouses",
        )

    @patch("src.connectors.wb.stock.get_wb_headers", return_value={"Authorization": "token"})
    @patch("src.connectors.wb.stock.request_json_with_status")
    def test_rejects_unexpected_response_shape(self, request_mock, _headers_mock) -> None:
        request_mock.return_value = ({"data": {}}, 200)

        with self.assertRaisesRegex(RuntimeError, "data.items"):
            fetch_wb_stock_pages("2026-07-26")
