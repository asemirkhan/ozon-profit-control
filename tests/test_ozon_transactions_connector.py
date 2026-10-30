from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.connectors.ozon import transactions as ozon_tx


class TestOzonTransactionsConnector(TestCase):
    def test_chunk_date_ranges_splits_month_limit(self) -> None:
        ranges = ozon_tx._chunk_date_ranges("2026-02-01", "2026-03-11", 31)
        self.assertEqual(
            ranges,
            [
                ("2026-02-01", "2026-02-28"),
                ("2026-03-01", "2026-03-11"),
            ],
        )

    def test_fetch_transactions_pages_auto_chunks_period(self) -> None:
        with (
            patch("src.connectors.ozon.transactions.get_ozon_headers", return_value={"Client-Id": "x", "Api-Key": "y"}),
            patch("src.connectors.ozon.transactions.post_json_with_status") as post_mock,
        ):
            post_mock.side_effect = [
                (
                    {
                        "result": {
                            "operations": [
                                {
                                    "operation_id": "tx-1",
                                    "operation_date": "2026-02-01T00:00:00Z",
                                }
                            ]
                        }
                    },
                    200,
                ),
                (
                    {
                        "result": {
                            "operations": [],
                        }
                    },
                    200,
                ),
                (
                    {
                        "result": {
                            "operations": [
                                {
                                    "operation_id": "tx-2",
                                    "operation_date": "2026-03-01T00:00:00Z",
                                }
                            ]
                        }
                    },
                    200,
                ),
                (
                    {
                        "result": {
                            "operations": [],
                        }
                    },
                    200,
                ),
            ]

            pages = ozon_tx.fetch_transactions_pages(
                date_from="2026-02-01",
                date_to="2026-03-11",
                limit=1,
                max_pages=10,
                pagination_mode="page",
            )

        self.assertEqual(len(pages), 4)
        self.assertEqual(post_mock.call_count, 4)
        first_payload = post_mock.call_args_list[0].args[1]
        second_chunk_first_payload = post_mock.call_args_list[2].args[1]
        self.assertEqual(first_payload["filter"]["date"]["from"], "2026-02-01T00:00:00.000Z")
        self.assertEqual(first_payload["filter"]["date"]["to"], "2026-02-28T23:59:59.999Z")
        self.assertEqual(second_chunk_first_payload["filter"]["date"]["from"], "2026-03-01T00:00:00.000Z")
        self.assertEqual(second_chunk_first_payload["filter"]["date"]["to"], "2026-03-11T23:59:59.999Z")
