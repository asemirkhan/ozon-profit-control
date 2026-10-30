from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]

CanonicalModeName = Literal["closed_period", "live_tail"]
DefaultRunnerModeName = Literal["bulk", "closed_period", "live_tail"]


@dataclass(frozen=True)
class CanonicalMode:
    name: CanonicalModeName
    lane: str
    source_of_truth: tuple[str, ...]
    phase_order: tuple[str, ...]
    close_conditions: tuple[str, ...]
    env: dict[str, str]


CLOSED_PERIOD = CanonicalMode(
    name="closed_period",
    lane="quiet_lane",
    source_of_truth=(
        "scripts/wb_finance_canonical_closed_period_runner_v1.py",
        "scripts/wb_finance_january_saturation_check_v1.py",
        "scripts/wb_finance_january_full_pass_with_saturation_closing_v1.py",
        "scripts/build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1.py",
    ),
    phase_order=(
        "tail_continuation_from_current_max_rrd_id_if_day_already_materialized",
        "restart_from_day_start_replay",
        "control_pass_after_first_useful_or_tail_growth",
        "saturation_close",
    ),
    close_conditions=(
        "terminal empty or no-content after data",
        "no new rrd_id after useful pass",
        "no amount growth after useful pass",
        "no rrd_id range expansion after useful pass",
        "duplicate-only replay tail is factual saturation for closed period",
    ),
    env={
        "WB_HTTP_GLOBAL_RPS": "0.1",
        "WB_FINANCE_PAGE_LIMIT": "1000",
        "WB_FINANCE_HTTP_MAX_ATTEMPTS": "1",
        "WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS": "1",
        "WB_FINANCE_HTTP_RETRY_DELAYS_SEC": "0",
        "WB_MAX_REQUESTS_PER_SECOND": "1",
        "WB_MAX_PARALLEL_REQUESTS_PER_SOURCE": "1",
        "WB_FINANCE_STABLE_MODE": "0",
        "WB_FINANCE_INTER_PAGE_PAUSE_SEC": "50",
        "WB_FINANCE_PASS_PAUSE_SEC": "120",
        "WB_FINANCE_RATE_LIMIT_PASS_PAUSE_SEC": "150",
        "WB_FINANCE_STABLE_MAX_PASSES": "3",
    },
)


LIVE_TAIL = CanonicalMode(
    name="live_tail",
    lane="anti_429_quiet_lane",
    source_of_truth=(
        "scripts/wb_finance_canonical_live_tail_runner_v1.py",
        "scripts/build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1.py",
        "scripts/wb_data_completion_cleanup_and_granular_prep_v1.py",
    ),
    phase_order=(
        "quiet_presence_first_probe",
        "source_open_detection_on_first_page",
        "controlled_follow_up_only_after_source_wake_up",
        "provisional_materialization_before_strict_usable_close",
    ),
    close_conditions=(
        "do not mark empty after rate-limit or deferred contour",
        "first page proves source-open, not final economics truth",
        "strict usable only after controlled follow-up closes the day",
        "fresh days stay provisional until stable close is proven",
    ),
    env={
        "WB_HTTP_GLOBAL_RPS": "0.2",
        "WB_FINANCE_PAGE_LIMIT": "1000",
        "WB_FINANCE_HTTP_MAX_ATTEMPTS": "1",
        "WB_FINANCE_STABLE_HTTP_MAX_ATTEMPTS": "1",
        "WB_FINANCE_HTTP_RETRY_DELAYS_SEC": "0",
        "WB_MAX_REQUESTS_PER_SECOND": "1",
        "WB_MAX_PARALLEL_REQUESTS_PER_SOURCE": "1",
        "WB_FINANCE_STABLE_MODE": "0",
    },
)


def canonical_mode_by_name(name: CanonicalModeName) -> CanonicalMode:
    if name == "closed_period":
        return CLOSED_PERIOD
    return LIVE_TAIL


def apply_canonical_env(name: CanonicalModeName) -> CanonicalMode:
    load_dotenv(ROOT / ".env")
    mode = canonical_mode_by_name(name)
    for key, value in mode.env.items():
        os.environ[key] = value
    return mode


def default_mode_for_day(
    day_value: date,
    *,
    current_day: date,
    latest_strict_usable_day: date | None = None,
    live_tail_days: int = 3,
) -> DefaultRunnerModeName:
    """Choose the loading contour from the age of the requested day.

    ``latest_strict_usable_day`` is deliberately not used as the boundary for
    newer days.  The old implementation did that and permanently classified
    every day after the first gap as a live, unfinished day.  A calendar day
    becomes an historical bulk-load candidate when it leaves the live tail,
    regardless of gaps that occurred before it.
    """
    del latest_strict_usable_day  # retained for call-site compatibility
    cutoff = current_day - timedelta(days=max(live_tail_days, 1))
    return "bulk" if day_value <= cutoff else "live_tail"


def canonical_modes_table() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for mode in (CLOSED_PERIOD, LIVE_TAIL):
        rows.append(
            {
                "mode_name": mode.name,
                "lane": mode.lane,
                "source_of_truth": "; ".join(mode.source_of_truth),
                "phase_order": " -> ".join(mode.phase_order),
                "close_conditions": "; ".join(mode.close_conditions),
                "env": "; ".join(f"{key}={value}" for key, value in mode.env.items()),
            }
        )
    return rows
