from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.connectors.ozon.http import _build_ssl_config as build_ozon_ssl_config
from src.connectors.wb.http import _build_ssl_config as build_wb_ssl_config
from src.connectors.wb.products import fetch_wb_products_pages
from src.connectors.wb.stock import fetch_wb_stock_pages


class TestConnectorSafetyGates(unittest.TestCase):
    def test_marketplace_tls_verification_cannot_be_disabled(self) -> None:
        for env_name, build_config in (
            ("WB_SSL_NO_VERIFY", build_wb_ssl_config),
            ("OZON_SSL_NO_VERIFY", build_ozon_ssl_config),
        ):
            with self.subTest(env_name=env_name), patch.dict(
                os.environ, {env_name: "1"}, clear=True
            ):
                with self.assertRaisesRegex(RuntimeError, "TLS certificate verification must remain enabled"):
                    build_config()

    @patch("src.connectors.wb.products.get_wb_headers", return_value={"Authorization": "token"})
    @patch("src.connectors.wb.products.request_json_with_status")
    def test_product_page_cap_is_marked_incomplete(self, request_mock, _headers_mock) -> None:
        request_mock.return_value = (
            {
                "result": {
                    "cards": [{"nmID": 123}],
                    "cursor": {"updatedAt": "2026-07-01T00:00:00", "nmID": 123},
                }
            },
            200,
        )

        pages = fetch_wb_products_pages(limit=1, max_pages=1)

        self.assertTrue(pages[0]["page_cap_reached"])

    @patch("src.connectors.wb.stock.get_wb_headers", return_value={"Authorization": "token"})
    @patch("src.connectors.wb.stock.request_json_with_status")
    def test_stock_page_cap_is_marked_incomplete(self, request_mock, _headers_mock) -> None:
        request_mock.return_value = (
            {"data": {"items": [{"nmId": 123}]}},
            200,
        )

        pages = fetch_wb_stock_pages("2026-07-01", limit=1, max_pages=1)

        self.assertTrue(pages[0]["page_cap_reached"])


if __name__ == "__main__":
    unittest.main()
