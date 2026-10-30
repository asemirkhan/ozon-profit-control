from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.connectors.wb import ads as wb_ads


class TestWbAdsConnector(TestCase):
    def test_uses_v3_stats_endpoint_by_default(self) -> None:
        self.assertIn("/adv/v3/fullstats", wb_ads.WB_ADS_STATS_URL)
        self.assertNotIn("/adv/v2/fullstats", wb_ads.WB_ADS_STATS_URL)

    def test_fetch_ads_pages_resolves_campaigns_then_requests_stats(self) -> None:
        with (
            patch("src.connectors.wb.ads.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.ads.request_json_with_status") as request_mock,
        ):
            request_mock.side_effect = [
                (
                    {
                        "adverts": [
                            {"advert_list": [{"advertId": 101}, {"advertId": "102"}]},
                        ]
                    },
                    200,
                ),
                (
                    [
                        {"advertId": 101, "days": []},
                        {"advertId": 102, "days": []},
                    ],
                    200,
                ),
            ]

            pages = wb_ads.fetch_wb_ads_pages(
                date_from="2026-02-01",
                date_to="2026-03-01",
            )

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["http_status"], 200)
        self.assertEqual(request_mock.call_count, 2)

        first_call = request_mock.call_args_list[0].kwargs
        second_call = request_mock.call_args_list[1].kwargs
        self.assertEqual(first_call["method"], "GET")
        self.assertIn("/adv/v1/promotion/count", first_call["url"])
        self.assertEqual(second_call["method"], "GET")
        self.assertIn("/adv/v3/fullstats", second_call["url"])
        self.assertEqual(second_call["params"]["ids"], "101,102")

    def test_fetch_ads_pages_uses_explicit_advert_ids_without_campaign_lookup(self) -> None:
        with (
            patch("src.connectors.wb.ads.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.ads.request_json_with_status") as request_mock,
        ):
            request_mock.return_value = ([{"advertId": 201, "days": []}], 200)

            pages = wb_ads.fetch_wb_ads_pages(
                date_from="2026-02-01",
                date_to="2026-03-01",
                advert_ids=[201, 201, 202],
            )

        self.assertEqual(len(pages), 1)
        self.assertEqual(request_mock.call_count, 1)
        call = request_mock.call_args.kwargs
        self.assertIn("/adv/v3/fullstats", call["url"])
        self.assertEqual(call["params"]["ids"], "201,202")

    def test_chunk_dates_respects_max_range(self) -> None:
        chunks = wb_ads._chunk_dates("2026-02-01", "2026-03-11", 31)
        self.assertEqual(
            chunks,
            [
                ("2026-02-01", "2026-03-03"),
                ("2026-03-04", "2026-03-11"),
            ],
        )

    def test_returns_partial_pages_on_rate_limit_after_first_success(self) -> None:
        with (
            patch("src.connectors.wb.ads.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.ads.request_json_with_status") as request_mock,
            patch("src.connectors.wb.ads.WB_ADS_STATS_MAX_IDS", 1),
            patch("src.connectors.wb.ads.WB_ADS_STATS_MAX_RANGE_DAYS", 31),
        ):
            request_mock.side_effect = [
                (
                    {
                        "adverts": [
                            {"advert_list": [{"advertId": 101}, {"advertId": 102}]},
                        ]
                    },
                    200,
                ),
                ([{"advertId": 101, "days": []}], 200),
                RuntimeError("WB API HTTP error 429: too many requests"),
            ]

            pages = wb_ads.fetch_wb_ads_pages(
                date_from="2026-02-01",
                date_to="2026-03-01",
            )

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["http_status"], 200)

    def test_extract_campaign_ids_prefers_active_statuses_and_limits(self) -> None:
        with (
            patch("src.connectors.wb.ads.WB_ADS_ACTIVE_STATUSES", "9,11"),
            patch("src.connectors.wb.ads.WB_ADS_MAX_CAMPAIGNS", 2),
        ):
            ids = wb_ads._extract_campaign_ids(
                {
                    "adverts": [
                        {
                            "status": 7,
                            "advert_list": [
                                {"advertId": 7001, "changeTime": "2026-03-01T10:00:00+03:00"},
                            ],
                        },
                        {
                            "status": 9,
                            "advert_list": [
                                {"advertId": 9002, "changeTime": "2026-03-11T10:00:00+03:00"},
                                {"advertId": 9001, "changeTime": "2026-03-10T10:00:00+03:00"},
                            ],
                        },
                    ]
                }
            )
        self.assertEqual(ids, [9002, 9001])
