#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.ingestion.runner import run_ingestion

MOSCOW = ZoneInfo("Europe/Moscow")
load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
LOOKBACK_DAYS = int(os.getenv("WB_ORDERS_CANONICAL_LOOKBACK_DAYS", "90"))
COOLDOWN_SEC = int(os.getenv("WB_ORDERS_429_COOLDOWN_SEC", "300"))


def main() -> int:
    os.environ.setdefault("WB_HTTP_GLOBAL_RPS", os.getenv("WB_ORDERS_RPS", "0.08"))
    os.environ.setdefault("WB_MAX_REQUESTS_PER_SECOND", os.getenv("WB_ORDERS_RPS", "0.08"))
    os.environ.setdefault("WB_MAX_PARALLEL_REQUESTS_PER_SOURCE", "1")
    os.environ.setdefault("WB_HTTP_MAX_ATTEMPTS", "1")

    end = datetime.now(MOSCOW).date()
    start = end - timedelta(days=max(LOOKBACK_DAYS - 1, 0))
    result = run_ingestion(
        marketplace="wb",
        source="orders",
        schema=SCHEMA,
        date_from=start,
        date_to=end,
        mode="incremental",
        dry_run=False,
        pagination_mode="cursor",
        limit=100,
        max_pages=2,
    )
    if result.error_type == "rate_limit":
        time.sleep(COOLDOWN_SEC)
    print(
        f"orders_run status={result.status} raw={result.raw_rows_count} "
        f"loaded={result.loaded_rows_count} actual_days={result.actual_days} expected_days={result.expected_days}"
    )
    return 0 if result.status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
