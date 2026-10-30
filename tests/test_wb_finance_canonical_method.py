from __future__ import annotations

from datetime import date
from unittest import TestCase

from src.services.wb_finance_canonical_method_v1 import default_mode_for_day


class TestWbFinanceCanonicalMethod(TestCase):
    def test_historical_day_after_old_gap_uses_bulk_mode(self) -> None:
        self.assertEqual(
            default_mode_for_day(
                date(2026, 6, 30),
                current_day=date(2026, 7, 26),
                latest_strict_usable_day=date(2026, 6, 7),
                live_tail_days=3,
            ),
            "bulk",
        )

    def test_recent_days_remain_in_live_tail(self) -> None:
        self.assertEqual(
            default_mode_for_day(
                date(2026, 7, 25),
                current_day=date(2026, 7, 26),
                latest_strict_usable_day=date(2026, 6, 7),
                live_tail_days=3,
            ),
            "live_tail",
        )

    def test_live_tail_boundary_is_inclusive_for_bulk(self) -> None:
        self.assertEqual(
            default_mode_for_day(
                date(2026, 7, 23),
                current_day=date(2026, 7, 26),
                live_tail_days=3,
            ),
            "bulk",
        )
