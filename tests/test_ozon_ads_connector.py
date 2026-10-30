from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

from src.connectors.ozon import ads as ozon_ads


class TestOzonAdsConnector(TestCase):
    def test_endpoint_candidates_prefer_env_override(self) -> None:
        with patch.dict(os.environ, {"OZON_ADS_STATS_URL": "https://example.test/custom-ads"}, clear=False):
            candidates = ozon_ads._build_ads_endpoint_candidates(
                endpoint=None,
                headers={"Client-Id": "x", "Api-Key": "y"},
            )
        self.assertEqual(candidates, ["https://example.test/custom-ads"])

    def test_build_payload_for_performance_uses_camel_case_dates(self) -> None:
        with patch.dict(
            os.environ,
            {
                "OZON_ADS_CAMPAIGN_IDS": "1001, 1002, 1002, bad",
                "OZON_ADS_GROUP_BY": "SKU",
            },
            clear=False,
        ):
            payload = ozon_ads._build_payload(
                endpoint_url=ozon_ads.DEFAULT_ADS_STATS_URL_PERFORMANCE,
                date_from="2026-02-01",
                date_to="2026-03-11",
                limit=100,
                pagination_mode="offset",
                current_offset=0,
                current_page=1,
                extra_payload=None,
            )
        self.assertEqual(payload["dateFrom"], "2026-02-01")
        self.assertEqual(payload["dateTo"], "2026-03-11")
        self.assertEqual(payload["groupBy"], "SKU")
        self.assertEqual(payload["campaigns"], [1001, 1002])
        self.assertNotIn("offset", payload)
        self.assertNotIn("limit", payload)

    def test_fetch_ads_pages_falls_back_to_next_endpoint_on_404(self) -> None:
        with (
            patch("src.connectors.ozon.ads._get_ads_headers", return_value={"Client-Id": "x", "Api-Key": "y"}),
            patch("src.connectors.ozon.ads.post_json_with_status") as post_mock,
        ):
            post_mock.side_effect = [
                RuntimeError("Ozon API HTTP error 404: page not found"),
                (
                    {
                        "result": {
                            "campaigns": [{"id": "cmp-1"}],
                        }
                    },
                    200,
                ),
            ]

            pages = ozon_ads.fetch_ads_pages(
                date_from="2026-02-01",
                date_to="2026-02-02",
                limit=100,
                max_pages=1,
            )

        self.assertEqual(len(pages), 1)
        self.assertEqual(post_mock.call_count, 2)
        first_url = post_mock.call_args_list[0].args[0]
        second_url = post_mock.call_args_list[1].args[0]
        self.assertEqual(first_url, ozon_ads.DEFAULT_ADS_STATS_URL_LEGACY)
        self.assertEqual(second_url, ozon_ads.DEFAULT_ADS_STATS_URL_PERFORMANCE)
        second_payload = post_mock.call_args_list[1].args[1]
        self.assertIn("dateFrom", second_payload)
        self.assertIn("dateTo", second_payload)
        self.assertEqual(pages[0]["endpoint"], ozon_ads.DEFAULT_ADS_STATS_URL_PERFORMANCE)
