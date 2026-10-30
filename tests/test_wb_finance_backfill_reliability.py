from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import patch

from src.ingestion.runner import (
    IngestionChunkResult,
    _finance_provider_instability_retry_delay_sec,
    _finance_retry_delay_sec,
    _completeness_status,
    _coverage_ratio,
    _day_close_error_type,
    _ensure_wb_day_closed,
    _missing_dates_in_period,
    _orders_background_mode_enabled,
    _orders_required_for_analysis,
    _rate_limit_cooldown_seconds,
    _wb_orders_pages_per_pass,
    _wb_orders_stalled_no_progress_passes,
    _wb_worker_source_order,
)
from src.ingestion.source_registry import get_source_config


class TestWbFinanceBackfillReliability(TestCase):
    def test_wb_finance_is_chunked_by_day(self) -> None:
        cfg = get_source_config("wb", "finance")
        self.assertEqual(cfg.chunk_days, 1)

    def test_wb_heavy_sources_are_chunked_by_day(self) -> None:
        for source in ("orders", "sales", "ads"):
            cfg = get_source_config("wb", source)
            self.assertEqual(cfg.chunk_days, 1, msg=f"{source} must be day-chunked")

    def test_missing_dates_in_period(self) -> None:
        missing = _missing_dates_in_period(
            date_from=date(2026, 2, 10),
            date_to=date(2026, 2, 13),
            seen_days={date(2026, 2, 10), date(2026, 2, 12)},
        )
        self.assertEqual(
            missing,
            [date(2026, 2, 11), date(2026, 2, 13)],
        )

    def test_coverage_ratio_and_threshold_statuses(self) -> None:
        self.assertAlmostEqual(_coverage_ratio(expected_days=30, actual_days=29), 29 / 30)
        self.assertEqual(
            _completeness_status(
                dry_run=False,
                run_status="ok",
                expected_days=30,
                actual_days=29,
                coverage_ratio=29 / 30,
                row_loss_ratio=0.0,
            ),
            "ok",
        )
        self.assertEqual(
            _completeness_status(
                dry_run=False,
                run_status="ok",
                expected_days=30,
                actual_days=24,
                coverage_ratio=24 / 30,
                row_loss_ratio=0.0,
            ),
            "warning",
        )
        self.assertEqual(
            _completeness_status(
                dry_run=False,
                run_status="ok",
                expected_days=30,
                actual_days=10,
                coverage_ratio=10 / 30,
                row_loss_ratio=0.0,
            ),
            "error",
        )

    def test_worker_source_order_keeps_core_before_orders(self) -> None:
        self.assertEqual(
            _wb_worker_source_order(("orders", "ads", "finance", "sales")),
            ("sales", "finance", "ads", "orders"),
        )

    def test_rate_limit_cooldown_adds_default_buffer(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertEqual(_rate_limit_cooldown_seconds(45), 50)

    def test_rate_limit_cooldown_respects_env_override(self) -> None:
        with patch.dict("os.environ", {"WB_RATE_LIMIT_BUFFER_SEC": "7"}, clear=False):
            self.assertEqual(_rate_limit_cooldown_seconds(45), 52)

    def test_finance_retry_schedule(self) -> None:
        self.assertEqual(_finance_retry_delay_sec(1), 120)
        self.assertEqual(_finance_retry_delay_sec(2), 300)
        self.assertEqual(_finance_retry_delay_sec(3), 600)
        self.assertEqual(_finance_retry_delay_sec(4), 1800)
        self.assertEqual(_finance_retry_delay_sec(5), 3600)
        self.assertEqual(_finance_retry_delay_sec(6), 3600)

    def test_finance_provider_instability_retry_has_15m_floor(self) -> None:
        self.assertEqual(_finance_provider_instability_retry_delay_sec(1), 21600)
        self.assertEqual(_finance_provider_instability_retry_delay_sec(2), 86400)
        self.assertEqual(_finance_provider_instability_retry_delay_sec(3), 172800)
        self.assertEqual(_finance_provider_instability_retry_delay_sec(4), 259200)
        self.assertEqual(_finance_provider_instability_retry_delay_sec(5), 259200)
        self.assertEqual(_finance_provider_instability_retry_delay_sec(6), 259200)

    def test_orders_background_mode_defaults(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(_orders_background_mode_enabled())
            self.assertFalse(_orders_required_for_analysis())
            self.assertEqual(_wb_orders_pages_per_pass(), 10)
            self.assertEqual(_wb_orders_stalled_no_progress_passes(), 20)

    def test_finance_zero_raw_on_active_day_is_provider_instability(self) -> None:
        with patch("src.ingestion.runner._has_wb_core_business_activity", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "provider_instability"):
                _ensure_wb_day_closed(
                    schema="client_wb_probe_2",
                    source="finance",
                    chunk_day=date(2026, 3, 10),
                    dry_run=False,
                    raw_rows_count=0,
                    normalized_rows_count=0,
                )

    def test_day_close_classifies_zero_raw_active_day_as_provider_instability(self) -> None:
        chunk = IngestionChunkResult(
            chunk_id="c1",
            chunk_date_from=date(2026, 3, 10),
            chunk_date_to=date(2026, 3, 10),
            retry_attempts=1,
            page_count=0,
            raw_files_count=0,
            raw_rows_count=0,
            normalized_rows_count=0,
            loaded_rows_count=0,
            status="error",
            error_message="provider instability",
            error_type="provider_instability",
        )
        with patch("src.ingestion.runner._has_wb_core_business_activity", return_value=True):
            self.assertEqual(
                _day_close_error_type(
                    schema="client_wb_probe_2",
                    source="finance",
                    chunk_result=chunk,
                    target_date=date(2026, 3, 10),
                ),
                "provider_instability",
            )
