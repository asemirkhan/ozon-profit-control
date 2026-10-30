from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import patch

from scripts import wb_ads_canonical_runner_v1 as runner


class TestWbAdsCanonicalRunner(TestCase):
    def test_order_windows_supports_recent_first(self) -> None:
        windows = [
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2026, 2, 1), date(2026, 3, 3)),
            (date(2026, 3, 4), date(2026, 4, 3)),
        ]

        self.assertEqual(runner._order_windows(windows, "oldest_first"), windows)
        self.assertEqual(runner._order_windows(windows, "recent_first"), list(reversed(windows)))

    def test_order_campaigns_deprioritizes_zero_only_campaigns(self) -> None:
        campaign_ids = [101, 202, 303]
        catalog = [
            runner.CampaignMeta(campaign_id=101, status=11, last_change="2026-03-01T10:00:00+03:00"),
            runner.CampaignMeta(campaign_id=202, status=9, last_change="2026-04-01T10:00:00+03:00"),
            runner.CampaignMeta(campaign_id=303, status=11, last_change="2026-03-20T10:00:00+03:00"),
        ]
        campaign_positions = {campaign_id: index for index, campaign_id in enumerate(campaign_ids)}
        state = {
            (101, date(2026, 2, 1), date(2026, 3, 3)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 2, 1),
                slice_end=date(2026, 3, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
            (101, date(2026, 3, 4), date(2026, 4, 3)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 3, 4),
                slice_end=date(2026, 4, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
            (202, date(2026, 3, 4), date(2026, 4, 3)): runner.SliceState(
                campaign_id=202,
                slice_start=date(2026, 3, 4),
                slice_end=date(2026, 4, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=17,
            ),
        }

        ordered = runner._order_campaigns_for_window(
            catalog,
            state=state,
            window_start=date(2026, 4, 4),
            window_end=date(2026, 4, 8),
            campaign_positions=campaign_positions,
        )

        self.assertEqual(ordered, [202, 303, 101])

    def test_state_summary_reports_zero_only_campaigns(self) -> None:
        state = {
            (101, date(2026, 1, 1), date(2026, 1, 31)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 1, 1),
                slice_end=date(2026, 1, 31),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
            (101, date(2026, 2, 1), date(2026, 3, 3)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 2, 1),
                slice_end=date(2026, 3, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
            (202, date(2026, 2, 1), date(2026, 3, 3)): runner.SliceState(
                campaign_id=202,
                slice_start=date(2026, 2, 1),
                slice_end=date(2026, 3, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=11,
            ),
        }

        summary = runner._state_summary(state)

        self.assertEqual(summary["zero_only_campaigns"], [101])
        self.assertEqual(summary["zero_completed_windows"], 2)

    def test_queue_rows_use_requested_columns(self) -> None:
        catalog = [
            runner.CampaignMeta(campaign_id=202, status=9, last_change="2026-04-01T10:00:00+03:00"),
            runner.CampaignMeta(campaign_id=101, status=11, last_change="2026-03-01T10:00:00+03:00"),
        ]
        state = {
            (202, date(2026, 2, 1), date(2026, 3, 3)): runner.SliceState(
                campaign_id=202,
                slice_start=date(2026, 2, 1),
                slice_end=date(2026, 3, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=11,
            ),
            (101, date(2026, 1, 1), date(2026, 1, 31)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 1, 1),
                slice_end=date(2026, 1, 31),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
            (101, date(2026, 2, 1), date(2026, 3, 3)): runner.SliceState(
                campaign_id=101,
                slice_start=date(2026, 2, 1),
                slice_end=date(2026, 3, 3),
                status="completed",
                last_attempt_at=None,
                attempts=1,
                loaded_rows=0,
            ),
        }

        rows = runner._build_queue_rows(
            catalog,
            state=state,
            campaign_positions={202: 0, 101: 1},
        )

        self.assertEqual(rows[0].campaign_id, 202)
        self.assertEqual(rows[0].status, "active")
        self.assertEqual(rows[0].had_nonzero_rows_yes_no, "yes")
        self.assertEqual(rows[1].zero_only_yes_no, "yes")
        self.assertEqual(rows[1].queue_priority, 2)

    def test_build_request_units_keeps_sparse_batching_off_by_default(self) -> None:
        catalog = [
            runner.CampaignMeta(campaign_id=101, status=11, last_change=None),
            runner.CampaignMeta(campaign_id=202, status=11, last_change=None),
        ]

        with patch.object(runner, "ENABLE_SPARSE_BATCHING", False), patch.object(runner, "GLOBAL_BATCH_MAX_IDS", 1):
            units = runner._build_request_units(
                [101, 202],
                campaign_catalog=catalog,
                state={},
            )

        self.assertEqual(units, [[101], [202]])

    def test_build_request_units_uses_global_batch_when_enabled(self) -> None:
        catalog = [
            runner.CampaignMeta(campaign_id=101, status=9, last_change=None),
            runner.CampaignMeta(campaign_id=202, status=9, last_change=None),
            runner.CampaignMeta(campaign_id=303, status=11, last_change=None),
        ]

        with patch.object(runner, "GLOBAL_BATCH_MAX_IDS", 2):
            units = runner._build_request_units(
                [101, 202, 303],
                campaign_catalog=catalog,
                state={},
            )

        self.assertEqual(units, [[101, 202], [303]])

    def test_next_batch_size_uses_default_fallback_ladder(self) -> None:
        with patch.dict("os.environ", {}, clear=False), patch.object(runner, "GLOBAL_BATCH_MAX_IDS", 50):
            self.assertEqual(runner._next_batch_size(50), 30)
            self.assertEqual(runner._next_batch_size(30), 20)
            self.assertEqual(runner._next_batch_size(20), 1)
            self.assertIsNone(runner._next_batch_size(1))

    def test_split_request_unit_for_fallback_breaks_large_batch(self) -> None:
        with patch.dict("os.environ", {"WB_ADS_BATCH_FALLBACK_IDS": "50,30,20,1"}, clear=False):
            self.assertEqual(
                runner._split_request_unit_for_fallback([101, 202, 303, 404, 505]),
                [[101], [202], [303], [404], [505]],
            )
            self.assertEqual(
                runner._split_request_unit_for_fallback(list(range(1, 51))),
                [list(range(1, 31)), list(range(31, 51))],
            )
