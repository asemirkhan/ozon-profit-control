from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.wb.ads import _resolve_campaign_ids, WB_ADS_STATS_URL
from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status
from src.loaders.upsert_ads import upsert_fact_ads_spend
from src.loaders.upsert_wb import upsert_stg_wb_ads
from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.raw_store.writer import write_raw_json
from src.utils.db import get_conn

MOSCOW = ZoneInfo("Europe/Moscow")
load_dotenv(ROOT / ".env")

SCHEMA = os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo"
STATE_TABLE = "ads_campaign_slice_state"

OUTPUT_DIR = ROOT / "exports" / "ads_campaign_slice_runner_v1"
REQUESTS_DIR = OUTPUT_DIR / "requests"
PROGRESS_CSV = OUTPUT_DIR / "ads_campaign_slice_progress.csv"
COMPLETION_CSV = OUTPUT_DIR / "ads_full_period_completion_check.csv"
REPORT_MD = OUTPUT_DIR / "ads_campaign_slice_runner_report.md"
QUEUE_CSV = OUTPUT_DIR / "ads_campaign_queue_priority.csv"

WINDOW_DAYS = int(os.getenv("WB_ADS_WINDOW_DAYS", "31"))
PARALLEL_CAMPAIGNS = int(os.getenv("WB_ADS_PARALLEL_CAMPAIGNS", "1"))
WB_ADS_MAX_CAMPAIGNS = int(os.getenv("WB_ADS_MAX_CAMPAIGNS", "50"))
BATCH_IDS = 1
SUCCESS_COOLDOWN_SEC = int(os.getenv("WB_ADS_SUCCESS_COOLDOWN_SEC", "0"))
LIMIT_EXHAUSTED_COOLDOWN_SEC = int(os.getenv("WB_ADS_LIMIT_EXHAUSTED_COOLDOWN_SEC", "21"))
STOP_AFTER_FIRST_429 = (os.getenv("WB_ADS_STOP_AFTER_FIRST_429", "1").strip() or "1") not in {"0", "false", "False"}
WINDOW_ORDER = (os.getenv("WB_ADS_WINDOW_ORDER", "recent_first").strip() or "recent_first").lower()
ARCHIVE_RUN_ARTIFACTS = (os.getenv("WB_ADS_ARCHIVE_RUN_ARTIFACTS", "1").strip() or "1") not in {"0", "false", "False"}
QUEUE_STRATEGY = (os.getenv("WB_ADS_QUEUE_STRATEGY", "priority_v2").strip() or "priority_v2").lower()
ZERO_ONLY_MIN_COMPLETED_WINDOWS = max(int(os.getenv("WB_ADS_ZERO_ONLY_MIN_COMPLETED_WINDOWS", "2")), 2)
ENRICH_ADVERTS_IF_NEEDED = (os.getenv("WB_ADS_ENRICH_ADVERTS_IF_NEEDED", "1").strip() or "1") not in {"0", "false", "False"}
ENABLE_SPARSE_BATCHING = (os.getenv("WB_ADS_ENABLE_SPARSE_BATCHING", "0").strip() or "0") not in {"0", "false", "False"}
SPARSE_BATCH_MAX_IDS = max(int(os.getenv("WB_ADS_SPARSE_BATCH_MAX_IDS", "3")), 2)
SPARSE_TOTAL_ROWS_THRESHOLD = max(int(os.getenv("WB_ADS_SPARSE_TOTAL_ROWS_THRESHOLD", "10")), 0)
GLOBAL_BATCH_MAX_IDS = max(int(os.getenv("WB_ADS_GLOBAL_BATCH_MAX_IDS", "50")), 1)
WB_ADS_CAMPAIGNS_URL = os.getenv("WB_ADS_CAMPAIGNS_URL", "https://advert-api.wildberries.ru/adv/v1/promotion/count")
WB_ADS_ADVERTS_URL = os.getenv("WB_ADS_ADVERTS_URL", "https://advert-api.wildberries.ru/api/advert/v2/adverts")
STATUS_LABELS = {
    -1: "deleted",
    4: "ready",
    7: "completed",
    8: "declined",
    9: "active",
    11: "paused",
}


@dataclass
class SliceState:
    campaign_id: int
    slice_start: date
    slice_end: date
    status: str
    last_attempt_at: str | None
    attempts: int
    loaded_rows: int


@dataclass
class CampaignMeta:
    campaign_id: int
    status: int | None
    last_change: str | None
    campaign_type: int | None = None
    payment_type: str | None = None
    name: str | None = None


@dataclass
class CampaignQueueRow:
    campaign_id: int
    status: str
    last_change: str | None
    had_nonzero_rows_yes_no: str
    zero_only_yes_no: str
    queue_priority: int
    is_sparse_candidate: bool


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _status_label(status: int | None) -> str:
    if status is None:
        return "unknown"
    return STATUS_LABELS.get(status, str(status))


def _status_rank(status: int | None) -> int:
    if status == 9:
        return 0
    if status == 11:
        return 1
    if status == 7:
        return 2
    return 3


def _campaign_state_rows(state: dict[tuple[int, date, date], SliceState], campaign_id: int) -> list[SliceState]:
    return [row for key, row in state.items() if key[0] == campaign_id and row.status == "completed"]


def _campaign_had_nonzero_rows(state: dict[tuple[int, date, date], SliceState], campaign_id: int) -> bool:
    return any(row.loaded_rows > 0 for row in _campaign_state_rows(state, campaign_id))


def _campaign_zero_only(state: dict[tuple[int, date, date], SliceState], campaign_id: int) -> bool:
    completed = _campaign_state_rows(state, campaign_id)
    return len(completed) >= ZERO_ONLY_MIN_COMPLETED_WINDOWS and all(row.loaded_rows == 0 for row in completed)


def _campaign_total_rows(state: dict[tuple[int, date, date], SliceState], campaign_id: int) -> int:
    return sum(row.loaded_rows for row in _campaign_state_rows(state, campaign_id))


def _campaign_batch_tag(campaign_ids: list[int]) -> str:
    serialized = ",".join(str(campaign_id) for campaign_id in campaign_ids)
    digest = hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:12]
    return f"ids{len(campaign_ids)}_{digest}"


def _batch_fallback_ladder() -> list[int]:
    raw = (os.getenv("WB_ADS_BATCH_FALLBACK_IDS") or "").strip()
    values: list[int] = []
    if raw:
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                parsed = int(token)
            except ValueError:
                continue
            if parsed > 0:
                values.append(parsed)
    if not values:
        values = [GLOBAL_BATCH_MAX_IDS, 30, 20, 1]
    ladder = sorted({max(int(value), 1) for value in values}, reverse=True)
    if 1 not in ladder:
        ladder.append(1)
    return ladder


def _next_batch_size(current_size: int) -> int | None:
    for size in _batch_fallback_ladder():
        if size < current_size:
            return size
    return None


def _split_request_unit_for_fallback(campaign_ids: list[int]) -> list[list[int]]:
    next_size = _next_batch_size(len(campaign_ids))
    if next_size is None:
        return []
    return _chunk_campaign_ids(campaign_ids, next_size)


def _header_int(response_headers: dict[str, Any], name: str) -> int | None:
    return _as_int(response_headers.get(name) or response_headers.get(name.lower()))


def _success_cooldown(response_headers: dict[str, Any]) -> int:
    retry_after = _header_int(response_headers, "X-Ratelimit-Retry")
    reset_after = _header_int(response_headers, "X-Ratelimit-Reset")
    remaining = _header_int(response_headers, "X-Ratelimit-Remaining")

    header_wait = max(retry_after or 0, reset_after or 0)
    if remaining == 0:
        return max(LIMIT_EXHAUSTED_COOLDOWN_SEC, header_wait)
    if header_wait > 0:
        return header_wait
    return max(SUCCESS_COOLDOWN_SEC, 0)


def _lock_path() -> Path:
    token = os.getenv("WB_API_KEY") or os.getenv("WB_API_TOKEN") or "no_token"
    token_hash = hashlib.sha1(token.encode("utf-8")).hexdigest()[:16]
    return Path("/tmp") / f"profit_control_ads_runner_lock_{token_hash}"


def acquire_lock() -> bool:
    lock_path = _lock_path()
    for attempt in range(2):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n{now_iso()}\n")
            return True
        except FileExistsError:
            if attempt > 0 or not _remove_stale_lock(lock_path):
                return False
    return False


def _remove_stale_lock(lock_path: Path) -> bool:
    """Remove only an orphaned or unreasonably old process lock."""
    try:
        age_sec = max(time.time() - lock_path.stat().st_mtime, 0)
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return True

    pid = _as_int(lines[0]) if lines else None
    process_alive = False
    if pid:
        try:
            os.kill(pid, 0)
            process_alive = True
        except ProcessLookupError:
            process_alive = False
        except PermissionError:
            process_alive = True

    stale_after_sec = max(int(os.getenv("WB_ADS_LOCK_STALE_SEC", "14400")), 300)
    if process_alive and age_sec < stale_after_sec:
        return False
    try:
        lock_path.unlink()
        print(f"ads_run removed_stale_lock={lock_path} age_sec={int(age_sec)}")
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def release_lock() -> None:
    try:
        _lock_path().unlink()
    except FileNotFoundError:
        return


def _chunk_windows(date_from: date, date_to: date, window_days: int) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        end = min(current + timedelta(days=window_days - 1), date_to)
        windows.append((current, end))
        current = end + timedelta(days=1)
    return windows


def _order_windows(windows: list[tuple[date, date]], window_order: str) -> list[tuple[date, date]]:
    if window_order == "oldest_first":
        return list(windows)
    if window_order == "recent_first":
        return list(reversed(windows))
    raise ValueError(f"Unsupported WB ads window order: {window_order}")


def _extract_campaign_meta_from_count(payload: dict[str, Any]) -> list[CampaignMeta]:
    adverts = payload.get("adverts")
    if not isinstance(adverts, list):
        raise RuntimeError("WB ads campaign list response must include adverts[]")

    seen: set[int] = set()
    result: list[CampaignMeta] = []
    for group in adverts:
        if not isinstance(group, dict):
            continue
        group_status = _as_int(group.get("status"))
        group_type = _as_int(group.get("type"))
        advert_list = group.get("advert_list")
        if not isinstance(advert_list, list):
            continue
        for advert in advert_list:
            if not isinstance(advert, dict):
                continue
            advert_id = _as_int(advert.get("advertId") or advert.get("id"))
            if advert_id is None or advert_id in seen:
                continue
            seen.add(advert_id)
            change_time = advert.get("changeTime")
            result.append(
                CampaignMeta(
                    campaign_id=advert_id,
                    status=group_status,
                    last_change=str(change_time) if change_time else None,
                    campaign_type=group_type,
                )
            )
    return result


def _campaign_catalog_sort_key(meta: CampaignMeta) -> tuple[int, float, int]:
    change_ts = (_as_datetime(meta.last_change) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()
    return (_status_rank(meta.status), -change_ts, meta.campaign_id)


def _fetch_campaign_catalog_from_count(headers: dict[str, str]) -> list[CampaignMeta]:
    raw, _status = request_json_with_status(
        method="GET",
        url=WB_ADS_CAMPAIGNS_URL,
        headers=headers,
        retry_after_buffer_sec=5,
        rate_api_category="ads",
        rate_endpoint="/adv/v1/promotion/count",
    )
    if not isinstance(raw, dict):
        raise RuntimeError("WB ads campaign list response must be JSON object")

    catalog = _extract_campaign_meta_from_count(raw)
    catalog.sort(key=_campaign_catalog_sort_key)
    if WB_ADS_MAX_CAMPAIGNS > 0:
        catalog = catalog[:WB_ADS_MAX_CAMPAIGNS]
    return catalog


def _enrich_campaign_catalog_with_adverts(headers: dict[str, str], catalog: list[CampaignMeta]) -> list[CampaignMeta]:
    if not catalog:
        return []

    by_id = {item.campaign_id: item for item in catalog}
    ids = [item.campaign_id for item in catalog]
    for chunk_start in range(0, len(ids), 50):
        batch = ids[chunk_start : chunk_start + 50]
        raw, _status = request_json_with_status(
            method="GET",
            url=WB_ADS_ADVERTS_URL,
            headers=headers,
            params={"ids": ",".join(str(campaign_id) for campaign_id in batch)},
            retry_after_buffer_sec=1,
            rate_api_category="ads",
            rate_endpoint="/api/advert/v2/adverts",
        )
        if not isinstance(raw, dict):
            continue
        adverts = raw.get("adverts")
        if not isinstance(adverts, list):
            continue
        for advert in adverts:
            if not isinstance(advert, dict):
                continue
            campaign_id = _as_int(advert.get("id"))
            if campaign_id is None or campaign_id not in by_id:
                continue
            current = by_id[campaign_id]
            settings = advert.get("settings") if isinstance(advert.get("settings"), dict) else {}
            timestamps = advert.get("timestamps") if isinstance(advert.get("timestamps"), dict) else {}
            updated = timestamps.get("updated")
            by_id[campaign_id] = CampaignMeta(
                campaign_id=campaign_id,
                status=_as_int(advert.get("status")) or current.status,
                last_change=str(updated) if updated else current.last_change,
                campaign_type=current.campaign_type,
                payment_type=str(settings.get("payment_type")) if settings.get("payment_type") else current.payment_type,
                name=str(settings.get("name")) if settings.get("name") else current.name,
            )
    return [by_id[item.campaign_id] for item in catalog]


def _load_campaign_catalog(headers: dict[str, str], explicit_campaign_ids: list[int] | None) -> list[CampaignMeta]:
    if explicit_campaign_ids:
        catalog = [
            CampaignMeta(campaign_id=campaign_id, status=None, last_change=None)
            for campaign_id in list(dict.fromkeys(explicit_campaign_ids))
        ]
    else:
        catalog = _fetch_campaign_catalog_from_count(headers)

    if ENRICH_ADVERTS_IF_NEEDED and any(item.status is None or item.last_change is None for item in catalog):
        catalog = _enrich_campaign_catalog_with_adverts(headers, catalog)
    return catalog


def _ensure_state_table(conn: Any) -> None:
    query = sql.SQL(
        """
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            campaign_id BIGINT NOT NULL,
            slice_start DATE NOT NULL,
            slice_end DATE NOT NULL,
            status TEXT NOT NULL,
            last_attempt_at TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            loaded_rows INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (campaign_id, slice_start, slice_end)
        )
        """
    ).format(schema=sql.Identifier(SCHEMA), table=sql.Identifier(STATE_TABLE))
    with conn.cursor() as cur:
        cur.execute(query)


def _seed_state_rows(conn: Any, campaign_ids: list[int], windows: list[tuple[date, date]]) -> None:
    rows = [
        {
            "campaign_id": cid,
            "slice_start": start,
            "slice_end": end,
            "status": "pending",
        }
        for cid in campaign_ids
        for start, end in windows
    ]
    if not rows:
        return
    query = sql.SQL(
        """
        INSERT INTO {schema}.{table} (
            campaign_id, slice_start, slice_end, status
        ) VALUES (
            %(campaign_id)s, %(slice_start)s, %(slice_end)s, %(status)s
        )
        ON CONFLICT (campaign_id, slice_start, slice_end) DO NOTHING
        """
    ).format(schema=sql.Identifier(SCHEMA), table=sql.Identifier(STATE_TABLE))
    with conn.cursor() as cur:
        cur.executemany(query, rows)


def _delete_state_rows(conn: Any, stale_keys: list[tuple[int, date, date]]) -> None:
    if not stale_keys:
        return
    query = sql.SQL(
        """
        DELETE FROM {schema}.{table}
        WHERE campaign_id = %s
          AND slice_start = %s
          AND slice_end = %s
        """
    ).format(schema=sql.Identifier(SCHEMA), table=sql.Identifier(STATE_TABLE))
    with conn.cursor() as cur:
        cur.executemany(query, stale_keys)


def _load_state(conn: Any, campaign_ids: list[int], date_from: date, date_to: date) -> dict[tuple[int, date, date], SliceState]:
    placeholders = sql.SQL(",").join(sql.Placeholder() for _ in campaign_ids)
    query = sql.SQL(
        """
        SELECT campaign_id, slice_start, slice_end, status, last_attempt_at, attempts, loaded_rows
        FROM {schema}.{table}
        WHERE campaign_id IN ({campaign_ids})
          AND slice_start >= %s
          AND slice_end <= %s
        ORDER BY campaign_id, slice_start
        """
    ).format(
        schema=sql.Identifier(SCHEMA),
        table=sql.Identifier(STATE_TABLE),
        campaign_ids=placeholders,
    )
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, [*campaign_ids, date_from, date_to])
        rows = cur.fetchall()

    state: dict[tuple[int, date, date], SliceState] = {}
    for row in rows:
        key = (int(row["campaign_id"]), row["slice_start"], row["slice_end"])
        state[key] = SliceState(
            campaign_id=int(row["campaign_id"]),
            slice_start=row["slice_start"],
            slice_end=row["slice_end"],
            status=row["status"],
            last_attempt_at=row["last_attempt_at"].isoformat() if row["last_attempt_at"] else None,
            attempts=int(row["attempts"] or 0),
            loaded_rows=int(row["loaded_rows"] or 0),
        )
    return state


def _update_state(
    conn: Any,
    *,
    campaign_id: int,
    slice_start: date,
    slice_end: date,
    status: str,
    attempts: int,
    loaded_rows: int,
    last_attempt_at: str,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.{table}
        SET status = %s,
            last_attempt_at = %s::timestamptz,
            attempts = %s,
            loaded_rows = %s
        WHERE campaign_id = %s
          AND slice_start = %s
          AND slice_end = %s
        """
    ).format(schema=sql.Identifier(SCHEMA), table=sql.Identifier(STATE_TABLE))
    with conn.cursor() as cur:
        cur.execute(
            query,
            (
                status,
                last_attempt_at,
                attempts,
                loaded_rows,
                campaign_id,
                slice_start,
                slice_end,
            ),
        )


def _write_progress_csv(state: dict[tuple[int, date, date], SliceState]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with PROGRESS_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "campaign_id",
                "slice_start",
                "slice_end",
                "status",
                "last_attempt_at",
                "attempts",
                "loaded_rows",
            ],
        )
        writer.writeheader()
        for key in sorted(state.keys()):
            row = state[key]
            writer.writerow(
                {
                    "campaign_id": row.campaign_id,
                    "slice_start": row.slice_start.isoformat(),
                    "slice_end": row.slice_end.isoformat(),
                    "status": row.status,
                    "last_attempt_at": row.last_attempt_at or "",
                    "attempts": row.attempts,
                    "loaded_rows": row.loaded_rows,
                }
            )


def _write_completion_csv(state: dict[tuple[int, date, date], SliceState]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[int, dict[str, int]] = {}
    for row in state.values():
        entry = summary.setdefault(
            row.campaign_id,
            {"total": 0, "completed": 0, "pending": 0, "retry": 0, "error": 0, "rows": 0},
        )
        entry["total"] += 1
        entry["rows"] += row.loaded_rows
        if row.status == "completed":
            entry["completed"] += 1
        elif row.status == "retry":
            entry["retry"] += 1
        elif row.status == "error":
            entry["error"] += 1
        else:
            entry["pending"] += 1

    with COMPLETION_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["campaign_id", "total_slices", "completed", "pending", "retry", "error", "loaded_rows"],
        )
        writer.writeheader()
        for campaign_id in sorted(summary.keys()):
            entry = summary[campaign_id]
            writer.writerow(
                {
                    "campaign_id": campaign_id,
                    "total_slices": entry["total"],
                    "completed": entry["completed"],
                    "pending": entry["pending"],
                    "retry": entry["retry"],
                    "error": entry["error"],
                    "loaded_rows": entry["rows"],
                }
            )


def _write_snapshot_copy(path: Path, *, run_stamp: str) -> None:
    if not ARCHIVE_RUN_ARTIFACTS or not path.exists():
        return
    archive_dir = OUTPUT_DIR / "runs"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / f"{path.stem}_{run_stamp}{path.suffix}"
    archived.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def _campaign_queue_sort_key(
    meta: CampaignMeta,
    *,
    state: dict[tuple[int, date, date], SliceState],
    campaign_positions: dict[int, int],
) -> tuple[int, int, float, int, int]:
    had_nonzero = _campaign_had_nonzero_rows(state, meta.campaign_id)
    zero_only = _campaign_zero_only(state, meta.campaign_id)
    last_change_ts = (_as_datetime(meta.last_change) or datetime.min.replace(tzinfo=timezone.utc)).timestamp()
    return (
        1 if zero_only else 0,
        _status_rank(meta.status),
        -last_change_ts,
        0 if had_nonzero else 1,
        campaign_positions[meta.campaign_id],
    )


def _is_sparse_candidate(meta: CampaignMeta, state: dict[tuple[int, date, date], SliceState]) -> bool:
    if meta.status == 9:
        return False
    if _campaign_zero_only(state, meta.campaign_id):
        return True
    total_rows = _campaign_total_rows(state, meta.campaign_id)
    return (not _campaign_had_nonzero_rows(state, meta.campaign_id)) or total_rows <= SPARSE_TOTAL_ROWS_THRESHOLD


def _build_queue_rows(
    campaign_catalog: list[CampaignMeta],
    *,
    state: dict[tuple[int, date, date], SliceState],
    campaign_positions: dict[int, int],
) -> list[CampaignQueueRow]:
    ordered_catalog = sorted(
        campaign_catalog,
        key=lambda meta: _campaign_queue_sort_key(
            meta,
            state=state,
            campaign_positions=campaign_positions,
        ),
    )
    rows: list[CampaignQueueRow] = []
    for queue_priority, meta in enumerate(ordered_catalog, start=1):
        rows.append(
            CampaignQueueRow(
                campaign_id=meta.campaign_id,
                status=_status_label(meta.status),
                last_change=meta.last_change,
                had_nonzero_rows_yes_no="yes" if _campaign_had_nonzero_rows(state, meta.campaign_id) else "no",
                zero_only_yes_no="yes" if _campaign_zero_only(state, meta.campaign_id) else "no",
                queue_priority=queue_priority,
                is_sparse_candidate=_is_sparse_candidate(meta, state),
            )
        )
    return rows


def _write_queue_csv(
    campaign_catalog: list[CampaignMeta],
    *,
    state: dict[tuple[int, date, date], SliceState],
    campaign_positions: dict[int, int],
) -> list[CampaignQueueRow]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = _build_queue_rows(
        campaign_catalog,
        state=state,
        campaign_positions=campaign_positions,
    )
    with QUEUE_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "campaign_id",
                "status",
                "last_change",
                "had_nonzero_rows_yes_no",
                "zero_only_yes_no",
                "queue_priority",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "campaign_id": row.campaign_id,
                    "status": row.status,
                    "last_change": row.last_change or "",
                    "had_nonzero_rows_yes_no": row.had_nonzero_rows_yes_no,
                    "zero_only_yes_no": row.zero_only_yes_no,
                    "queue_priority": row.queue_priority,
                }
            )
    return rows


def _order_campaigns_for_window(
    campaign_catalog: list[CampaignMeta],
    *,
    state: dict[tuple[int, date, date], SliceState],
    window_start: date,
    window_end: date,
    campaign_positions: dict[int, int],
) -> list[int]:
    pending: list[CampaignMeta] = []
    retry: list[CampaignMeta] = []
    by_id = {item.campaign_id: item for item in campaign_catalog}
    for campaign_id in [item.campaign_id for item in campaign_catalog]:
        current = state.get((campaign_id, window_start, window_end))
        if current and current.status == "completed":
            continue
        if current and current.status == "retry":
            retry.append(by_id[campaign_id])
        else:
            pending.append(by_id[campaign_id])

    ordered_pending = sorted(
        pending,
        key=lambda meta: _campaign_queue_sort_key(
            meta,
            state=state,
            campaign_positions=campaign_positions,
        ),
    )
    ordered_retry = sorted(
        retry,
        key=lambda meta: _campaign_queue_sort_key(
            meta,
            state=state,
            campaign_positions=campaign_positions,
        ),
    )
    ordered = ordered_pending + ordered_retry
    if QUEUE_STRATEGY == "legacy":
        return [item.campaign_id for item in ordered]

    solo_ids: list[int] = []
    sparse_ids: list[int] = []
    for meta in ordered:
        if ENABLE_SPARSE_BATCHING and _is_sparse_candidate(meta, state):
            sparse_ids.append(meta.campaign_id)
        else:
            solo_ids.append(meta.campaign_id)
    return solo_ids + sparse_ids


def _state_summary(state: dict[tuple[int, date, date], SliceState]) -> dict[str, Any]:
    completed_rows = [row for row in state.values() if row.status == "completed"]
    completed_zero_rows = [row for row in completed_rows if row.loaded_rows == 0]

    by_campaign: dict[int, list[SliceState]] = {}
    for row in completed_rows:
        by_campaign.setdefault(row.campaign_id, []).append(row)

    zero_only_campaigns = sorted(
        campaign_id
        for campaign_id, rows in by_campaign.items()
        if len(rows) >= ZERO_ONLY_MIN_COMPLETED_WINDOWS and all(item.loaded_rows == 0 for item in rows)
    )
    top_campaigns = sorted(
        (
            {
                "campaign_id": campaign_id,
                "rows_loaded": sum(item.loaded_rows for item in rows),
                "completed_slices": len(rows),
            }
            for campaign_id, rows in by_campaign.items()
        ),
        key=lambda item: (item["rows_loaded"], item["completed_slices"], -item["campaign_id"]),
        reverse=True,
    )[:8]

    return {
        "completed_windows": len(completed_rows),
        "zero_completed_windows": len(completed_zero_rows),
        "zero_only_campaigns": zero_only_campaigns,
        "top_campaigns": top_campaigns,
    }


def _chunk_campaign_ids(campaign_ids: list[int], chunk_size: int) -> list[list[int]]:
    if chunk_size <= 1:
        return [[campaign_id] for campaign_id in campaign_ids]
    return [campaign_ids[index : index + chunk_size] for index in range(0, len(campaign_ids), chunk_size)]


def _build_request_units(
    ordered_campaign_ids: list[int],
    *,
    campaign_catalog: list[CampaignMeta],
    state: dict[tuple[int, date, date], SliceState],
    batch_max_ids: int | None = None,
) -> list[list[int]]:
    effective_batch_max_ids = max(int(batch_max_ids or GLOBAL_BATCH_MAX_IDS), 1)
    if effective_batch_max_ids > 1 and QUEUE_STRATEGY != "legacy":
        return _chunk_campaign_ids(ordered_campaign_ids, effective_batch_max_ids)
    if not ENABLE_SPARSE_BATCHING or QUEUE_STRATEGY == "legacy":
        return [[campaign_id] for campaign_id in ordered_campaign_ids]

    meta_by_id = {item.campaign_id: item for item in campaign_catalog}
    solo_units: list[list[int]] = []
    sparse_bucket: list[int] = []
    for campaign_id in ordered_campaign_ids:
        meta = meta_by_id[campaign_id]
        if _is_sparse_candidate(meta, state):
            sparse_bucket.append(campaign_id)
        else:
            solo_units.append([campaign_id])

    sparse_units: list[list[int]] = []
    chunk_size = min(max(SPARSE_BATCH_MAX_IDS, 2), 3)
    for index in range(0, len(sparse_bucket), chunk_size):
        chunk = sparse_bucket[index : index + chunk_size]
        if len(chunk) == 1:
            sparse_units.append(chunk)
        elif len(chunk) >= 2:
            sparse_units.append(chunk)
    return solo_units + sparse_units


def _write_report(
    *,
    campaign_ids: list[int],
    date_from: date,
    date_to: date,
    window_days: int,
    parallel_campaigns: int,
    total_requests: int,
    total_429: int,
    total_rows: int,
    windows_total: int,
    windows_completed: int,
    windows_retry: int,
    run_started_at: str,
    run_finished_at: str,
    window_order: str,
    state: dict[tuple[int, date, date], SliceState],
) -> None:
    summary = _state_summary(state)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ads campaign slice runner report",
        "",
        f"- schema: `{SCHEMA}`",
        f"- date range: `{date_from.isoformat()}` .. `{date_to.isoformat()}`",
        f"- run_started_at: `{run_started_at}`",
        f"- run_finished_at: `{run_finished_at}`",
        f"- campaign_ids: `{', '.join(str(cid) for cid in campaign_ids)}`",
        f"- window_days: `{window_days}`",
        f"- window_order: `{window_order}`",
        f"- queue_strategy: `{QUEUE_STRATEGY}`",
        f"- zero_only_min_completed_windows: `{ZERO_ONLY_MIN_COMPLETED_WINDOWS}`",
        f"- global_batch_max_ids: `{GLOBAL_BATCH_MAX_IDS}`",
        f"- batch_fallback_ids: `{', '.join(str(item) for item in _batch_fallback_ladder())}`",
        f"- sparse_batching_enabled: `{1 if ENABLE_SPARSE_BATCHING else 0}`",
        f"- sparse_batch_max_ids: `{SPARSE_BATCH_MAX_IDS}`",
        f"- parallel_campaigns: `{parallel_campaigns}`",
        f"- batch_ids: `dynamic` (current default up to `{GLOBAL_BATCH_MAX_IDS}` ids per request with fallback ladder)",
        f"- success_cooldown_sec: `{SUCCESS_COOLDOWN_SEC}`",
        f"- limit_exhausted_cooldown_sec: `{LIMIT_EXHAUSTED_COOLDOWN_SEC}`",
        "",
        "## Execution summary",
        f"- windows_total: `{windows_total}`",
        f"- windows_completed: `{windows_completed}`",
        f"- windows_retry: `{windows_retry}`",
        f"- requests_total: `{total_requests}`",
        f"- count_429: `{total_429}`",
        f"- rows_loaded: `{total_rows}`",
        "",
        "## State patterns",
        f"- completed_windows_in_state: `{summary['completed_windows']}`",
        f"- zero_completed_windows: `{summary['zero_completed_windows']}`",
        f"- zero_only_campaigns: `{', '.join(str(cid) for cid in summary['zero_only_campaigns']) if summary['zero_only_campaigns'] else '-'}`",
        "- top_campaigns_by_loaded_rows:",
    ]
    for item in summary["top_campaigns"]:
        lines.append(
            f"  - `{item['campaign_id']}` rows=`{item['rows_loaded']}` completed_slices=`{item['completed_slices']}`"
        )
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")


def _build_pages(
    *,
    params: dict[str, str],
    parsed: list[dict[str, Any]] | dict[str, Any] | None,
    http_status: int | None,
    error_type: str | None,
    response_body: str | None,
) -> list[dict[str, Any]]:
    response: dict[str, Any] | list[Any]
    if parsed is None:
        response = {"error": response_body or ""}
    else:
        response = parsed
    return [
        {
            "response": response,
            "request_params": dict(params),
            "http_status": http_status,
            "request_success": http_status == 200,
            "error_type": error_type,
        }
    ]


def _run_slice_request(
    *,
    campaign_ids: list[int],
    slice_start: date,
    slice_end: date,
) -> tuple[int | None, dict[int, int], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    headers = get_wb_headers()
    params = {
        "ids": ",".join(str(campaign_id) for campaign_id in campaign_ids),
        "beginDate": slice_start.isoformat(),
        "endDate": slice_end.isoformat(),
    }
    diagnostics: list[dict[str, Any]] = []
    parsed: list[dict[str, Any]] | dict[str, Any] | None = None
    status: int | None = None
    error: str | None = None
    try:
        parsed, status = request_json_with_status(
            method="GET",
            url=WB_ADS_STATS_URL,
            headers=headers,
            params=params,
            diagnostics_collector=diagnostics.append,
            max_attempts_override=1,
            retry_delays_override=[],
            retry_after_buffer_sec=0,
            rate_api_category="ads",
            rate_endpoint="/adv/v3/fullstats",
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)

    diag = diagnostics[-1] if diagnostics else {}
    status = status or diag.get("http_status")
    response_headers = diag.get("response_headers") or {}
    response_body = diag.get("response_body")
    error_type = diag.get("error_type")

    pages = _build_pages(
        params=params,
        parsed=parsed,
        http_status=status,
        error_type=error_type,
        response_body=response_body,
    )

    payload = parsed if isinstance(parsed, dict) else {"rows": parsed or []}
    batch_tag = _campaign_batch_tag(campaign_ids)
    run_id = f"wb_ads_slice_{batch_tag}_{slice_start.isoformat()}_{slice_end.isoformat()}_{_utc_stamp()}"
    write_raw_json(
        schema=SCHEMA,
        source="wb_ads",
        run_id=run_id,
        payload=payload,
        page=1,
        request_params=params,
        http_status=status,
        meta_extra={
            "campaign_ids": campaign_ids,
            "slice_start": slice_start.isoformat(),
            "slice_end": slice_end.isoformat(),
            "error": error,
        },
        file_stem=run_id,
    )

    REQUESTS_DIR.mkdir(parents=True, exist_ok=True)
    artifact = {
        "campaign_ids": campaign_ids,
        "slice_start": slice_start.isoformat(),
        "slice_end": slice_end.isoformat(),
        "request_params": params,
        "request_url": diag.get("request_url"),
        "http_status": status,
        "error": error,
        "response_headers": response_headers,
        "response_body": response_body,
        "diagnostics": diagnostics,
    }
    artifact_path = REQUESTS_DIR / f"{batch_tag}_{slice_start.isoformat()}_{slice_end.isoformat()}_{_utc_stamp()}.json"
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = normalize_wb_ads(pages) if status == 200 else []
    if rows:
        upsert_stg_wb_ads(SCHEMA, rows)
        upsert_fact_ads_spend(SCHEMA, _map_wb_ads_to_fact(rows))
    rows_by_campaign = {campaign_id: 0 for campaign_id in campaign_ids}
    for row in rows:
        campaign_id = _as_int(row.get("campaign_id"))
        if campaign_id is None:
            continue
        rows_by_campaign[campaign_id] = rows_by_campaign.get(campaign_id, 0) + 1
    return status, rows_by_campaign, pages, artifact, response_headers


def _map_wb_ads_to_fact(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "date": row.get("date"),
            "campaign_id": row.get("campaign_id"),
            "marketplace": "wb",
            "campaign_name": row.get("campaign_name"),
            "campaign_type": row.get("campaign_type"),
            "offer_id": row.get("offer_id") or "~",
            "sku": row.get("nm_id") or "~",
            "product_id": row.get("nm_id") or "~",
            "app_type": row.get("app_type") or "~",
            "impressions": row.get("impressions"),
            "clicks": row.get("clicks"),
            "orders_count": row.get("orders_count"),
            "revenue": row.get("revenue"),
            "spend": row.get("spend"),
            "currency": row.get("currency"),
            "raw_updated_at": row.get("raw_updated_at"),
        }
        for row in rows
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical WB ads runner (campaign slice mode).")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--limit-campaigns", type=int, default=0)
    parser.add_argument("--campaign-ids", nargs="*", type=int)
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--parallel-campaigns", type=int, default=PARALLEL_CAMPAIGNS)
    return parser.parse_args()


def main() -> int:
    os.environ.setdefault("WB_MAX_PARALLEL_REQUESTS_PER_SOURCE", "1")
    os.environ.setdefault("WB_HTTP_MAX_ATTEMPTS", "1")
    os.environ.setdefault("WB_HTTP_RETRY_DELAYS_SEC", "0")

    if not acquire_lock():
        print("ads_run skipped: lock busy")
        return 0

    try:
        run_started_at = now_iso()
        run_stamp = _utc_stamp()
        args = parse_args()
        date_from = date.fromisoformat(args.date_from)
        date_to = date.fromisoformat(args.date_to)
        if date_from > date_to:
            raise ValueError(f"Invalid period: {date_from.isoformat()} > {date_to.isoformat()}")

        headers = get_wb_headers()
        campaign_catalog = _load_campaign_catalog(headers, args.campaign_ids)

        if args.limit_campaigns and args.limit_campaigns > 0:
            campaign_catalog = campaign_catalog[: args.limit_campaigns]

        campaign_ids = [item.campaign_id for item in campaign_catalog]

        if not campaign_ids:
            raise RuntimeError("No WB ads campaign IDs found")

        window_days = max(int(args.window_days or WINDOW_DAYS), 1)
        parallel_campaigns = max(int(args.parallel_campaigns or PARALLEL_CAMPAIGNS), 1)
        windows = _chunk_windows(date_from, date_to, window_days)
        effective_window_order = WINDOW_ORDER if QUEUE_STRATEGY != "legacy" else "oldest_first"
        ordered_windows = _order_windows(windows, effective_window_order)
        campaign_positions = {campaign_id: index for index, campaign_id in enumerate(campaign_ids)}

        total_requests = 0
        total_429 = 0
        total_rows = 0
        windows_total = 0
        windows_completed = 0
        windows_retry = 0
        stop_requested = False

        with get_conn() as conn:
            _ensure_state_table(conn)
            _seed_state_rows(conn, campaign_ids, windows)
            conn.commit()

            state = _load_state(conn, campaign_ids, date_from, date_to)
            expected_keys = {
                (campaign_id, window_start, window_end)
                for window_start, window_end in windows
                for campaign_id in campaign_ids
            }
            windows_total = len(expected_keys)
            stale_keys = [key for key in state if key not in expected_keys]
            if stale_keys:
                _delete_state_rows(conn, stale_keys)
                conn.commit()
                state = _load_state(conn, campaign_ids, date_from, date_to)
            _write_queue_csv(
                campaign_catalog,
                state=state,
                campaign_positions=campaign_positions,
            )
            for window_start, window_end in ordered_windows:
                if stop_requested:
                    break

                for campaign_id in campaign_ids:
                    current = state.get((campaign_id, window_start, window_end))
                    if current and current.status == "completed":
                        windows_completed += 1

                if QUEUE_STRATEGY == "legacy":
                    pending_ids: list[int] = []
                    retry_ids: list[int] = []
                    for campaign_id in campaign_ids:
                        current = state.get((campaign_id, window_start, window_end))
                        if current and current.status == "completed":
                            continue
                        if current and current.status == "retry":
                            retry_ids.append(campaign_id)
                        else:
                            pending_ids.append(campaign_id)
                    request_units = [[campaign_id] for campaign_id in (pending_ids + retry_ids)]
                else:
                    ordered_campaigns = _order_campaigns_for_window(
                        campaign_catalog,
                        state=state,
                        window_start=window_start,
                        window_end=window_end,
                        campaign_positions=campaign_positions,
                    )
                    request_units = _build_request_units(
                        ordered_campaigns,
                        campaign_catalog=campaign_catalog,
                        state=state,
                    )

                index = 0
                while index < len(request_units) and not stop_requested:
                    batch: list[tuple[list[int], date, date]] = []
                    while index < len(request_units) and len(batch) < parallel_campaigns:
                        unit_campaign_ids = request_units[index]
                        index += 1
                        batch.append((unit_campaign_ids, window_start, window_end))

                    if not batch:
                        continue

                    if len(batch) == 1:
                        unit_campaign_ids, slice_start, slice_end = batch[0]
                        results = [
                            (
                                unit_campaign_ids,
                                slice_start,
                                slice_end,
                                _run_slice_request(
                                    campaign_ids=unit_campaign_ids,
                                    slice_start=slice_start,
                                    slice_end=slice_end,
                                ),
                            )
                        ]
                    else:
                        results = []
                        with concurrent.futures.ThreadPoolExecutor(max_workers=len(batch)) as pool:
                            future_map = {
                                pool.submit(
                                    _run_slice_request,
                                    campaign_ids=unit_campaign_ids,
                                    slice_start=slice_start,
                                    slice_end=slice_end,
                                ): (unit_campaign_ids, slice_start, slice_end)
                                for unit_campaign_ids, slice_start, slice_end in batch
                            }
                            for future in concurrent.futures.as_completed(future_map):
                                unit_campaign_ids, slice_start, slice_end = future_map[future]
                                results.append((unit_campaign_ids, slice_start, slice_end, future.result()))

                    max_cooldown_sec = 0
                    for unit_campaign_ids, batch_window_start, batch_window_end, result in results:
                        status, rows_by_campaign, _pages, _artifact, response_headers = result
                        total_rows += sum(rows_by_campaign.values())
                        total_requests += 1
                        if status == 200:
                            max_cooldown_sec = max(max_cooldown_sec, _success_cooldown(response_headers))
                        elif status == 429:
                            total_429 += 1

                        stop_on_rate_limit = status == 429 and STOP_AFTER_FIRST_429
                        fallback_units: list[list[int]] = []
                        if status != 200 and QUEUE_STRATEGY != "legacy" and not stop_on_rate_limit:
                            fallback_units = _split_request_unit_for_fallback(unit_campaign_ids)
                        if fallback_units:
                            request_units[index:index] = fallback_units
                            if status == 429:
                                max_cooldown_sec = max(max_cooldown_sec, LIMIT_EXHAUSTED_COOLDOWN_SEC)
                            continue

                        for campaign_id in unit_campaign_ids:
                            key = (campaign_id, batch_window_start, batch_window_end)
                            current = state.get(key)
                            attempts = (current.attempts if current else 0) + 1
                            last_attempt = _now_utc_iso()
                            rows_count = rows_by_campaign.get(campaign_id, 0)
                            if status == 200:
                                new_status = "completed"
                                windows_completed += 1
                            elif status == 429:
                                new_status = "retry"
                                windows_retry += 1
                            else:
                                new_status = "error"
                                windows_retry += 1

                            _update_state(
                                conn,
                                campaign_id=campaign_id,
                                slice_start=batch_window_start,
                                slice_end=batch_window_end,
                                status=new_status,
                                attempts=attempts,
                                loaded_rows=rows_count if status == 200 else (current.loaded_rows if current else 0),
                                last_attempt_at=last_attempt,
                            )
                            conn.commit()

                            state[key] = SliceState(
                                campaign_id=campaign_id,
                                slice_start=batch_window_start,
                                slice_end=batch_window_end,
                                status=new_status,
                                last_attempt_at=last_attempt,
                                attempts=attempts,
                                loaded_rows=rows_count if status == 200 else (current.loaded_rows if current else 0),
                            )

                        _write_progress_csv(state)
                        _write_queue_csv(
                            campaign_catalog,
                            state=state,
                            campaign_positions=campaign_positions,
                        )

                        if stop_on_rate_limit:
                            stop_requested = True

                    if max_cooldown_sec > 0 and not stop_requested:
                        time.sleep(max_cooldown_sec)

        _write_progress_csv(state)
        _write_completion_csv(state)
        _write_queue_csv(
            campaign_catalog,
            state=state,
            campaign_positions=campaign_positions,
        )
        _write_snapshot_copy(PROGRESS_CSV, run_stamp=run_stamp)
        _write_snapshot_copy(COMPLETION_CSV, run_stamp=run_stamp)
        _write_snapshot_copy(QUEUE_CSV, run_stamp=run_stamp)
        _write_report(
            campaign_ids=campaign_ids,
            date_from=date_from,
            date_to=date_to,
            window_days=window_days,
            parallel_campaigns=parallel_campaigns,
            total_requests=total_requests,
            total_429=total_429,
            total_rows=total_rows,
            windows_total=windows_total,
            windows_completed=windows_completed,
            windows_retry=windows_retry,
            run_started_at=run_started_at,
            run_finished_at=now_iso(),
            window_order=effective_window_order,
            state=state,
        )
        _write_snapshot_copy(REPORT_MD, run_stamp=run_stamp)
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
