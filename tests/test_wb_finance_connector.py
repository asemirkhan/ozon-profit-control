from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from src.connectors.wb.finance import WbFinanceFetchError, fetch_wb_finance_pages


class TestWbFinanceConnector(TestCase):
    def test_collects_attempt_level_pages_until_terminal_no_content(self) -> None:
        with (
            patch("src.connectors.wb.finance.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.finance.request_json_with_status") as request_mock,
        ):
            def _side_effect(**kwargs):
                collector = kwargs["diagnostics_collector"]
                call_no = _side_effect.calls
                _side_effect.calls += 1
                if call_no == 0:
                    collector(
                        {
                            "attempt_no": 1,
                            "request_started_at": "2026-03-12T00:00:00+00:00",
                            "response_finished_at": "2026-03-12T00:00:01+00:00",
                            "http_status": 200,
                            "response_body_size": 0,
                            "response_content_type": "application/json",
                            "raw_body": "",
                            "empty_body": True,
                            "parse_error": False,
                            "unexpected_shape": False,
                            "error_type": "empty_response",
                            "error_message": "WB API empty response body",
                            "success": False,
                            "parsed_response": None,
                        }
                    )
                    collector(
                        {
                            "attempt_no": 2,
                            "request_started_at": "2026-03-12T00:00:02+00:00",
                            "response_finished_at": "2026-03-12T00:00:03+00:00",
                            "http_status": 200,
                            "response_body_size": 123,
                            "response_content_type": "application/json",
                            "raw_body": "[{\"rrd_id\": 1}]",
                            "empty_body": False,
                            "parse_error": False,
                            "unexpected_shape": False,
                            "error_type": None,
                            "error_message": None,
                            "success": True,
                            "parsed_response": [{"rrd_id": 1}],
                        }
                    )
                    return [{"rrd_id": 1}], 200
                collector(
                    {
                        "attempt_no": 1,
                        "request_started_at": "2026-03-12T00:00:04+00:00",
                        "response_finished_at": "2026-03-12T00:00:05+00:00",
                        "http_status": 204,
                        "response_body_size": 0,
                        "response_content_type": "application/json",
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "http_no_content",
                        "error_message": "WB API http_no_content",
                        "success": False,
                        "parsed_response": None,
                    }
                )
                raise RuntimeError("WB API http_no_content")

            _side_effect.calls = 0

            request_mock.side_effect = _side_effect
            pages = fetch_wb_finance_pages(
                date_from="2026-02-10",
                date_to="2026-03-11",
                limit=100,
                max_pages=2,
            )

        self.assertEqual(len(pages), 3)
        self.assertFalse(pages[0]["request_success"])
        self.assertEqual(pages[0]["error_type"], "empty_response")
        self.assertTrue(pages[1]["request_success"])
        self.assertEqual(pages[1]["response"]["finance"], [{"rrd_id": 1}])
        self.assertTrue(pages[2]["finance_is_terminal_page"])
        self.assertEqual(pages[2]["finance_terminal_reason"], "http_no_content_after_data")

    def test_raises_with_diagnostic_pages_on_failure(self) -> None:
        with (
            patch("src.connectors.wb.finance.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.finance.request_json_with_status") as request_mock,
        ):
            def _side_effect(**kwargs):
                collector = kwargs["diagnostics_collector"]
                collector(
                    {
                        "attempt_no": 1,
                        "request_started_at": "2026-03-12T00:00:00+00:00",
                        "response_finished_at": "2026-03-12T00:00:01+00:00",
                        "http_status": 200,
                        "response_body_size": 0,
                        "response_content_type": "application/json",
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "empty_response",
                        "error_message": "WB API empty response body",
                        "success": False,
                        "parsed_response": None,
                    }
                )
                raise RuntimeError("WB API empty response body")

            request_mock.side_effect = _side_effect
            with self.assertRaises(WbFinanceFetchError) as ctx:
                fetch_wb_finance_pages(
                    date_from="2026-02-10",
                    date_to="2026-03-11",
                    limit=100,
                    max_pages=1,
                )

        self.assertEqual(ctx.exception.error_type, "empty_response")
        self.assertEqual(len(ctx.exception.diagnostic_pages), 1)
        self.assertTrue(ctx.exception.diagnostic_pages[0]["empty_body"])

    def test_prefers_attempt_subtype_for_no_content_after_429(self) -> None:
        with (
            patch("src.connectors.wb.finance.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.finance.request_json_with_status") as request_mock,
        ):
            def _side_effect(**kwargs):
                collector = kwargs["diagnostics_collector"]
                collector(
                    {
                        "attempt_no": 1,
                        "request_started_at": "2026-03-12T00:00:00+00:00",
                        "response_finished_at": "2026-03-12T00:00:01+00:00",
                        "http_status": 429,
                        "response_body_size": 300,
                        "response_content_type": "application/problem+json",
                        "response_headers": {"X-Ratelimit-Retry": "45"},
                        "raw_body": "{\"status\":429}",
                        "empty_body": False,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "rate_limit",
                        "error_message": "WB API HTTP error 429",
                        "success": False,
                        "parsed_response": None,
                    }
                )
                collector(
                    {
                        "attempt_no": 2,
                        "request_started_at": "2026-03-12T00:00:46+00:00",
                        "response_finished_at": "2026-03-12T00:00:47+00:00",
                        "http_status": 204,
                        "response_body_size": 0,
                        "response_content_type": "application/json; charset=utf-8",
                        "response_headers": {},
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "http_no_content_after_429",
                        "error_message": "WB API http_no_content_after_429",
                        "success": False,
                        "parsed_response": None,
                    }
                )
                raise RuntimeError("WB API http_no_content_after_429")

            request_mock.side_effect = _side_effect
            with self.assertRaises(WbFinanceFetchError) as ctx:
                fetch_wb_finance_pages(
                    date_from="2026-03-09",
                    date_to="2026-03-09",
                    limit=100,
                    max_pages=1,
                )

        self.assertEqual(ctx.exception.error_type, "http_no_content_after_429")
        self.assertEqual(len(ctx.exception.diagnostic_pages), 2)

    def test_returns_resume_rrdid_on_incomplete_pagination(self) -> None:
        with (
            patch("src.connectors.wb.finance.get_wb_headers", return_value={"Authorization": "token"}),
            patch("src.connectors.wb.finance.request_json_with_status") as request_mock,
        ):
            def _side_effect(**kwargs):
                collector = kwargs["diagnostics_collector"]
                call_no = _side_effect.calls
                _side_effect.calls += 1
                if call_no == 0:
                    collector(
                        {
                            "attempt_no": 1,
                            "request_started_at": "2026-03-12T00:00:00+00:00",
                            "response_finished_at": "2026-03-12T00:00:01+00:00",
                            "http_status": 200,
                            "response_body_size": 123,
                            "response_content_type": "application/json",
                            "raw_body": "[{\"rrd_id\": 11},{\"rrd_id\": 12}]",
                            "empty_body": False,
                            "parse_error": False,
                            "unexpected_shape": False,
                            "error_type": None,
                            "error_message": None,
                            "success": True,
                            "parsed_response": [{"rrd_id": 11}, {"rrd_id": 12}],
                        }
                    )
                    return [{"rrd_id": 11}, {"rrd_id": 12}], 200
                collector(
                    {
                        "attempt_no": 1,
                        "request_started_at": "2026-03-12T00:00:02+00:00",
                        "response_finished_at": "2026-03-12T00:00:03+00:00",
                        "http_status": 429,
                        "response_body_size": 300,
                        "response_content_type": "application/problem+json",
                        "response_headers": {"X-Ratelimit-Retry": "45"},
                        "raw_body": "{\"status\":429}",
                        "empty_body": False,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": "rate_limit",
                        "error_message": "WB API HTTP error 429",
                        "success": False,
                        "parsed_response": None,
                    }
                )
                raise RuntimeError("WB API HTTP error 429")

            _side_effect.calls = 0
            request_mock.side_effect = _side_effect
            with self.assertRaises(WbFinanceFetchError) as ctx:
                fetch_wb_finance_pages(
                    date_from="2026-02-11",
                    date_to="2026-02-11",
                    limit=2,
                    max_pages=3,
                )

        self.assertEqual(ctx.exception.error_type, "finance_incomplete_pagination")
        self.assertEqual(ctx.exception.resume_rrdid, 12)
