from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from scripts.build_profit_control_force_convert_all_provisional_days_to_strict_usable_v1 import connect
from src.services.wb_finance_canonical_method_v1 import default_mode_for_day

load_dotenv(ROOT / ".env")
SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Default canonical WB finance runner.")
    parser.add_argument("--days", nargs="+", required=True, help="Day list in YYYY-MM-DD format.")
    return parser.parse_args()


def latest_strict_usable_day() -> date | None:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT MAX(day)
                FROM {SCHEMA}.finance_day_state
                WHERE COALESCE(final_analytic_usable_flag, FALSE)
                """
            )
            value = cur.fetchone()[0]
    return value


def resolve_live_tail_days() -> int:
    raw = (os.getenv("PROFIT_CONTROL_FINANCE_LIVE_TAIL_DAYS") or "").strip()
    if not raw:
        return 3
    try:
        return max(int(raw), 1)
    except ValueError:
        return 3


def resolve_forced_mode() -> str | None:
    raw = (os.getenv("PROFIT_CONTROL_FINANCE_FORCE_MODE") or "").strip().lower()
    if raw in {"bulk", "bulk_period", "period_bulk"}:
        return "bulk"
    if raw in {"closed", "closed_period"}:
        return "closed_period"
    if raw in {"live", "live_tail"}:
        return "live_tail"
    return None


def resolve_mode_for_day(day_value: date, *, today: date, latest_strict: date | None) -> str:
    forced = resolve_forced_mode()
    if forced:
        return forced
    return default_mode_for_day(
        day_value,
        current_day=today,
        latest_strict_usable_day=latest_strict,
        live_tail_days=resolve_live_tail_days(),
    )


def main() -> None:
    args = parse_args()
    today = datetime.now().date()
    cutoff = latest_strict_usable_day()
    planned: list[tuple[str, str]] = []
    for item in args.days:
        day_value = date.fromisoformat(item)
        mode = resolve_mode_for_day(day_value, today=today, latest_strict=cutoff)
        planned.append((item, mode))

    bulk_days = [day for day, mode in planned if mode == "bulk"]
    if bulk_days:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wb_finance_canonical_bulk_runner_v1.py"), "--days", *bulk_days],
            cwd=ROOT,
            check=True,
        )

    closed_period_days = [day for day, mode in planned if mode == "closed_period"]
    live_tail_days = [day for day, mode in planned if mode == "live_tail"]

    for day_text in closed_period_days:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wb_finance_canonical_closed_period_runner_v1.py"), "--days", day_text],
            cwd=ROOT,
            check=True,
        )

    if live_tail_days:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "wb_finance_canonical_live_tail_runner_v1.py"), "--days", *live_tail_days],
            cwd=ROOT,
            check=True,
        )

    print(f"bulk_days={','.join(day for day, mode in planned if mode == 'bulk') or 'none'}")
    print(f"closed_period_days={','.join(day for day, mode in planned if mode == 'closed_period') or 'none'}")
    print(f"live_tail_days={','.join(day for day, mode in planned if mode == 'live_tail') or 'none'}")
    print(f"finance_live_tail_days={resolve_live_tail_days()}")
    if resolve_forced_mode():
        print(f"finance_forced_mode={resolve_forced_mode()}")


if __name__ == "__main__":
    main()
