#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from psycopg.rows import dict_row

from src.ingestion.runner import run_ingestion
from src.utils.db import get_conn

MOSCOW = ZoneInfo("Europe/Moscow")
load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2"
LOOKBACK_DAYS = int(os.getenv("WB_PAID_STORAGE_CANONICAL_LOOKBACK_DAYS", "90"))
CHUNK_DAYS = 8
INTER_CHUNK_SLEEP_SEC = int(os.getenv("WB_PAID_STORAGE_INTER_CHUNK_SLEEP_SEC", "0"))
COOLDOWN_SEC = int(os.getenv("WB_PAID_STORAGE_429_COOLDOWN_SEC", "600"))
MAX_RATE_LIMIT_RETRIES = int(os.getenv("WB_PAID_STORAGE_MAX_RATE_LIMIT_RETRIES", "6"))
LOCK_PATH = ROOT / ".runtime_locks" / "wb_paid_storage_canonical_runner.lock"
COOLDOWN_STATE_PATH = ROOT / ".runtime_locks" / "wb_paid_storage_cooldown_state.json"


def _target_end_day() -> datetime.date:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            WITH finance_boundary AS (
              SELECT MAX(day)::date AS confirmed_through
              FROM {SCHEMA}.finance_day_state
              WHERE final_analytic_usable_flag = TRUE
            ),
            finance_raw AS (
              SELECT MAX(operation_date)::date AS max_finance_day
              FROM {SCHEMA}.stg_wb_finance
              WHERE COALESCE(marketplace, 'wb') = 'wb'
            )
            SELECT
              COALESCE(
                (SELECT confirmed_through FROM finance_boundary),
                (SELECT max_finance_day FROM finance_raw),
                CURRENT_DATE
              ) AS target_end_day
            """
        )
        row = cur.fetchone()
    return row["target_end_day"] if row and row["target_end_day"] else datetime.now(MOSCOW).date()


def _existing_report_days(day_from: datetime.date, day_to: datetime.date) -> set[datetime.date]:
    with get_conn() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT DISTINCT report_date::date AS day
            FROM {SCHEMA}.stg_wb_paid_storage
            WHERE COALESCE(marketplace, 'wb') = 'wb'
              AND report_date::date BETWEEN %s AND %s
            """,
            (day_from, day_to),
        )
        rows = cur.fetchall()
    return {row["day"] for row in rows if row.get("day")}


def _next_missing_day(day_from: datetime.date, day_to: datetime.date) -> datetime.date | None:
    present_days = _existing_report_days(day_from, day_to)
    current = day_from
    while current <= day_to:
        if current not in present_days:
            return current
        current += timedelta(days=1)
    return None


@contextmanager
def _cooldown_state_file():
    COOLDOWN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with COOLDOWN_STATE_PATH.open("a+", encoding="utf-8") as state_file:
        fcntl.flock(state_file.fileno(), fcntl.LOCK_EX)
        state_file.seek(0)
        raw = state_file.read().strip()
        if raw:
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                state = {}
        else:
            state = {}
        if not isinstance(state, dict):
            state = {}
        try:
            yield state
        finally:
            state_file.seek(0)
            state_file.truncate()
            state_file.write(json.dumps(state))
            state_file.flush()
            os.fsync(state_file.fileno())
            fcntl.flock(state_file.fileno(), fcntl.LOCK_UN)


def _load_cooldown_state() -> dict[str, str]:
    with _cooldown_state_file() as state:
        return {str(key): str(value) for key, value in state.items() if value is not None}


def _save_cooldown_state(*, current: datetime.date, chunk_end: datetime.date, next_allowed_at: datetime) -> None:
    with _cooldown_state_file() as state:
        state.clear()
        state.update(
            {
                "source": "wb_paid_storage",
                "chunk_from": current.isoformat(),
                "chunk_to": chunk_end.isoformat(),
                "next_allowed_at": next_allowed_at.isoformat(),
                "updated_at": datetime.now(MOSCOW).isoformat(),
                "reason": "rate_limit",
            }
        )


def _clear_cooldown_state() -> None:
    with _cooldown_state_file() as state:
        state.clear()


def _cooldown_not_elapsed(state: dict[str, str]) -> tuple[bool, datetime | None]:
    next_allowed_raw = (state.get("next_allowed_at") or "").strip()
    if not next_allowed_raw:
        return False, None
    try:
        next_allowed_at = datetime.fromisoformat(next_allowed_raw)
    except ValueError:
        return False, None
    now = datetime.now(MOSCOW)
    return now < next_allowed_at, next_allowed_at


@contextmanager
def _single_instance_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"paid_storage runner already active: {LOCK_PATH}") from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        try:
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def main() -> int:
    os.environ.setdefault("WB_HTTP_GLOBAL_RPS", os.getenv("WB_PAID_STORAGE_RPS", "0.05"))
    os.environ.setdefault("WB_MAX_REQUESTS_PER_SECOND", os.getenv("WB_PAID_STORAGE_RPS", "0.05"))
    os.environ.setdefault("WB_MAX_PARALLEL_REQUESTS_PER_SOURCE", "1")
    os.environ.setdefault("WB_HTTP_MAX_ATTEMPTS", "1")
    os.environ.setdefault("INGEST_CHUNK_MAX_RETRIES", "0")
    os.environ.setdefault("WB_RATE_LIMIT_BUFFER_SEC", "30")

    with _single_instance_lock():
        end = min(datetime.now(MOSCOW).date(), _target_end_day())
        start = end - timedelta(days=max(LOOKBACK_DAYS - 1, 0))
        cooldown_state = _load_cooldown_state()
        cooldown_active, next_allowed_at = _cooldown_not_elapsed(cooldown_state)
        if cooldown_active:
            print(
                "paid_storage_run cooldown_active "
                f"chunk_from={cooldown_state.get('chunk_from', '~')} "
                f"chunk_to={cooldown_state.get('chunk_to', '~')} "
                f"next_allowed_at={next_allowed_at.isoformat() if next_allowed_at else '~'}"
            )
            return 0
        if cooldown_state:
            _clear_cooldown_state()

        next_missing = _next_missing_day(start, end)
        if next_missing is None:
            print("paid_storage_run skipped: already up to date")
            return 0

        current = next_missing
        any_error = False
        while current <= end:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS - 1), end)
            result = run_ingestion(
                marketplace="wb",
                source="paid_storage",
                schema=SCHEMA,
                date_from=current,
                date_to=chunk_end,
                mode="incremental",
                dry_run=False,
            )
            print(
                f"paid_storage_chunk status={result.status} from={current.isoformat()} to={chunk_end.isoformat()} "
                f"attempt=1 raw={result.raw_rows_count} loaded={result.loaded_rows_count}"
            )
            if result.error_type == "rate_limit":
                next_allowed_at = datetime.now(MOSCOW) + timedelta(seconds=COOLDOWN_SEC)
                _save_cooldown_state(
                    current=current,
                    chunk_end=chunk_end,
                    next_allowed_at=next_allowed_at,
                )
                print(
                    f"paid_storage_chunk cooldown_saved from={current.isoformat()} to={chunk_end.isoformat()} "
                    f"next_allowed_at={next_allowed_at.isoformat()}"
                )
                return 0
            if result.status != "error":
                _clear_cooldown_state()
            if result.status == "error":
                any_error = True
                break
            next_missing = _next_missing_day(chunk_end + timedelta(days=1), end)
            if next_missing is None:
                break
            current = next_missing
            if current <= end and INTER_CHUNK_SLEEP_SEC > 0:
                time.sleep(INTER_CHUNK_SLEEP_SEC)
        return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
