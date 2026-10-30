from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
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
OUTPUT_DIR = ROOT / "exports" / "wb_stock_canonical_runner_v1"
LOG_CSV = OUTPUT_DIR / "stock_run_log.csv"


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def append_row(row: dict[str, str]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not LOG_CSV.exists():
        LOG_CSV.write_text(",".join(row.keys()) + "\n", encoding="utf-8")
    LOG_CSV.write_text(LOG_CSV.read_text(encoding="utf-8") + ",".join(row.values()) + "\n", encoding="utf-8")


def last_finished_at(*, schema: str) -> datetime | None:
    if not LOG_CSV.exists():
        return None
    lines = [line for line in LOG_CSV.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    header = lines[0].split(",")
    if "finished_at" not in header or "schema" not in header or "status" not in header:
        return None
    finished_idx = header.index("finished_at")
    schema_idx = header.index("schema")
    status_idx = header.index("status")
    for line in reversed(lines[1:]):
        row = line.split(",")
        if len(row) <= max(finished_idx, schema_idx, status_idx):
            continue
        if row[schema_idx] != schema:
            continue
        if row[status_idx] not in {"ok", "warning"}:
            continue
        try:
            return datetime.fromisoformat(row[finished_idx])
        except Exception:
            return None
    return None

def main() -> int:
    os.environ.setdefault("WB_HTTP_GLOBAL_RPS", os.getenv("WB_STOCK_RPS", "0.08"))
    os.environ.setdefault("WB_MAX_REQUESTS_PER_SECOND", os.getenv("WB_STOCK_RPS", "0.08"))
    os.environ.setdefault("WB_MAX_PARALLEL_REQUESTS_PER_SOURCE", "1")
    cooldown_sec = int(os.getenv("WB_STOCK_429_COOLDOWN_SEC", "300"))
    snapshot_days = int(os.getenv("WB_STOCK_SNAPSHOT_DAYS", "1"))
    min_interval_hours = int(os.getenv("WB_STOCK_MIN_INTERVAL_HOURS", "6"))

    prev_finish = last_finished_at(schema=SCHEMA)
    if prev_finish:
        delta = datetime.now(MOSCOW) - prev_finish
        if delta.total_seconds() < min_interval_hours * 3600:
            append_row(
                {
                    "started_at": now_iso(),
                    "finished_at": now_iso(),
                    "schema": SCHEMA,
                    "snapshot_from": "",
                    "snapshot_to": "",
                    "status": "skipped_interval_guard",
                    "raw_rows": "0",
                    "loaded_rows": "0",
                    "error_type": "",
                    "cooldown_applied": "no",
                }
            )
            print("stock_run skipped: interval guard")
            return 0

    end = datetime.now(MOSCOW).date()
    start = end - timedelta(days=max(snapshot_days - 1, 0))
    started_at = now_iso()
    result = run_ingestion(
        marketplace="wb",
        source="stock",
        schema=SCHEMA,
        date_from=start,
        date_to=end,
        mode="incremental",
        dry_run=False,
    )
    cooldown_applied = "no"
    if result.error_type == "rate_limit":
        cooldown_applied = "yes"
        time.sleep(cooldown_sec)
    row = {
        "started_at": started_at,
        "finished_at": now_iso(),
        "schema": SCHEMA,
        "snapshot_from": start.isoformat(),
        "snapshot_to": end.isoformat(),
        "status": result.status,
        "raw_rows": str(result.raw_rows_count),
        "loaded_rows": str(result.loaded_rows_count),
        "error_type": result.error_type or "",
        "cooldown_applied": cooldown_applied,
    }
    append_row(row)
    print(f"stock_run status={result.status} raw={result.raw_rows_count} loaded={result.loaded_rows_count}")
    return 0 if result.status != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
