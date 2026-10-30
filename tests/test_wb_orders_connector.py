from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.connectors.wb.orders import (
    WB_ORDERS_CURSOR_RESPONSE_CAP,
    fetch_wb_orders_pages,
)


class TestWbOrdersConnector(TestCase):
    def test_cursor_mode_does_not_report_page_cap_for_small_response(self) -> None:
        rows = [
            {
                "srid": f"order-{idx}",
                "lastChangeDate": "2026-04-08T19:13:38",
            }
            for idx in range(4_935)
        ]

        with (
            patch("src.connectors.wb.orders.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.orders.request_json_with_status", return_value=(rows, 200)),
            patch("src.connectors.wb.orders.time.sleep"),
        ):
            pages = fetch_wb_orders_pages(
                "2026-01-09",
                max_pages=1,
                pagination_mode="cursor",
            )

        self.assertEqual(len(pages), 1)
        self.assertFalse(pages[0]["page_cap_reached"])

    def test_cursor_mode_reports_page_cap_near_documented_80k_ceiling(self) -> None:
        rows = [
            {
                "srid": f"order-{idx}",
                "lastChangeDate": "2026-04-08T19:13:38",
            }
            for idx in range(WB_ORDERS_CURSOR_RESPONSE_CAP)
        ]

        with (
            patch("src.connectors.wb.orders.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.orders.request_json_with_status", return_value=(rows, 200)),
            patch("src.connectors.wb.orders.time.sleep"),
        ):
            pages = fetch_wb_orders_pages(
                "2026-01-09",
                max_pages=1,
                pagination_mode="cursor",
            )

        self.assertEqual(len(pages), 1)
        self.assertTrue(pages[0]["page_cap_reached"])
