from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib import error, request
import fcntl

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import _build_ssl_config, request_json_with_status

logger = logging.getLogger(__name__)
_LAST_CREATE_AT = 0.0
_LAST_DOWNLOAD_AT = 0.0
_RATE_STATE_PATH = Path(__file__).resolve().parents[3] / ".runtime_locks" / "wb_paid_storage_rate_state.json"

WB_PAID_STORAGE_URL = os.getenv(
    "WB_PAID_STORAGE_URL",
    "https://seller-analytics-api.wildberries.ru/api/v1/paid_storage",
)


class WbPaidStorageFetchError(RuntimeError):
    pass


def _status_poll_sec() -> float:
    raw = (os.getenv("WB_PAID_STORAGE_STATUS_POLL_SEC") or "").strip()
    if not raw:
        return 5.0
    try:
        parsed = float(raw)
    except ValueError:
        return 5.0
    return max(parsed, 1.0)


def _status_max_polls() -> int:
    raw = (os.getenv("WB_PAID_STORAGE_STATUS_MAX_POLLS") or "").strip()
    if not raw:
        return 60
    try:
        parsed = int(raw)
    except ValueError:
        return 60
    return max(parsed, 1)


def fetch_wb_paid_storage_pages(date_from: str, date_to: str) -> list[dict[str, Any]]:
    headers = get_wb_headers()
    create_params = {"dateFrom": date_from, "dateTo": date_to}
    _wait_method_interval("create", 65.0)
    try:
        created, create_status = request_json_with_status(
            method="GET",
            url=WB_PAID_STORAGE_URL,
            headers=headers,
            params=create_params,
            timeout=120,
            rate_api_category="analytics",
            rate_endpoint="/api/v1/paid_storage",
        )
    finally:
        _mark_method_called("create")
    task_id = created.get("data", {}).get("taskId") if isinstance(created, Mapping) else None
    if not task_id:
        raise WbPaidStorageFetchError(f"WB paid storage taskId missing in response: {created}")

    final_status_payload: dict[str, Any] | list[Any] | None = None
    final_state = "new"
    for poll_no in range(1, _status_max_polls() + 1):
        time.sleep(_status_poll_sec())
        payload, _ = request_json_with_status(
            method="GET",
            url=f"{WB_PAID_STORAGE_URL}/tasks/{task_id}/status",
            headers=headers,
            timeout=120,
            rate_api_category="analytics",
            rate_endpoint="/api/v1/paid_storage/tasks/{task_id}/status",
        )
        final_status_payload = payload
        final_state = payload.get("data", {}).get("status") if isinstance(payload, Mapping) else None
        logger.info(
            "WB paid storage status poll=%s task_id=%s state=%s period=%s..%s",
            poll_no,
            task_id,
            final_state,
            date_from,
            date_to,
        )
        if final_state in {"done", "failed"}:
            break

    if final_state != "done":
        raise WbPaidStorageFetchError(
            f"WB paid storage task did not complete successfully: task_id={task_id} state={final_state} payload={final_status_payload}"
        )

    report_rows, download_status = _download_paid_storage_report(
        url=f"{WB_PAID_STORAGE_URL}/tasks/{task_id}/download",
        headers=headers,
    )

    page = {
        "response": {"report": report_rows},
        "request_params": dict(create_params),
        "http_status": download_status,
        "task_id": task_id,
        "task_create_status": create_status,
        "task_final_state": final_state,
        "task_status_payload": final_status_payload,
    }
    return [page]


def _download_paid_storage_report(*, url: str, headers: Mapping[str, str]) -> tuple[list[dict[str, Any]], int]:
    _wait_method_interval("download", 65.0)
    req = request.Request(url=url, method="GET")
    for name, value in headers.items():
        req.add_header(name, value)

    ssl_config = _build_ssl_config()
    try:
        with request.urlopen(req, timeout=120, context=ssl_config.context) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8")
    except error.HTTPError as exc:
        _mark_method_called("download")
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 204:
            return [], 204
        raise WbPaidStorageFetchError(f"WB paid storage download HTTP {exc.code}: {body}") from exc
    except Exception as exc:
        _mark_method_called("download")
        raise WbPaidStorageFetchError(f"WB paid storage download failed: {exc}") from exc
    else:
        _mark_method_called("download")

    if status == 204 or not body.strip():
        return [], status

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise WbPaidStorageFetchError(f"WB paid storage download returned invalid JSON: {body[:400]}") from exc

    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)], status
    if isinstance(parsed, Mapping):
        rows = parsed.get("report")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)], status
    raise WbPaidStorageFetchError("WB paid storage download returned unexpected payload shape")


def _wait_method_interval(method_name: str, min_interval_sec: float) -> None:
    global _LAST_CREATE_AT, _LAST_DOWNLOAD_AT
    state_key = f"{method_name}_wall_time"
    while True:
        now_mono = time.monotonic()
        now_wall = time.time()
        if method_name == "create":
            local_last_called_at = _LAST_CREATE_AT
        else:
            local_last_called_at = _LAST_DOWNLOAD_AT
        local_wait_for = max(min_interval_sec - (now_mono - local_last_called_at), 0.0)

        with _rate_state_lock() as state:
            persisted_last_wall = float(state.get(state_key) or 0.0)
            persisted_wait_for = max(min_interval_sec - (now_wall - persisted_last_wall), 0.0)
            wait_for = max(local_wait_for, persisted_wait_for)
            if wait_for <= 0:
                return
        logger.info("WB paid storage throttle method=%s wait_sec=%.2f", method_name, wait_for)
        time.sleep(wait_for)

def _mark_method_called(method_name: str) -> None:
    global _LAST_CREATE_AT, _LAST_DOWNLOAD_AT
    called_at = time.monotonic()
    called_wall = time.time()
    if method_name == "create":
        _LAST_CREATE_AT = called_at
    else:
        _LAST_DOWNLOAD_AT = called_at
    state_key = f"{method_name}_wall_time"
    with _rate_state_lock() as state:
        state[state_key] = called_wall


@contextmanager
def _rate_state_lock() -> Any:
    _RATE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _RATE_STATE_PATH.open("a+", encoding="utf-8") as state_file:
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
