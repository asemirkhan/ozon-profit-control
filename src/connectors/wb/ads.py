from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from src.connectors.wb.auth import get_wb_headers
from src.connectors.wb.http import request_json_with_status

logger = logging.getLogger(__name__)

WB_ADS_CAMPAIGNS_URL = os.getenv(
    "WB_ADS_CAMPAIGNS_URL",
    "https://advert-api.wildberries.ru/adv/v1/promotion/count",
)
WB_ADS_STATS_URL = os.getenv(
    "WB_ADS_STATS_URL",
    os.getenv("WB_ADS_URL", "https://advert-api.wildberries.ru/adv/v3/fullstats"),
)
WB_ADS_STATS_MAX_IDS = int(os.getenv("WB_ADS_STATS_MAX_IDS", "50"))
WB_ADS_STATS_MAX_RANGE_DAYS = int(os.getenv("WB_ADS_STATS_MAX_RANGE_DAYS", "31"))
WB_ADS_MAX_CAMPAIGNS = int(os.getenv("WB_ADS_MAX_CAMPAIGNS", "50"))
WB_ADS_ACTIVE_STATUSES = os.getenv("WB_ADS_ACTIVE_STATUSES", "9,11")


class WbAdsFetchError(RuntimeError):
    def __init__(self, message: str, *, diagnostic_pages: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.diagnostic_pages = diagnostic_pages or []


def _ads_lock_path(headers: Mapping[str, str]) -> Path:
    token = (
        headers.get("Authorization")
        or headers.get("authorization")
        or os.getenv("WB_API_KEY")
        or os.getenv("WB_API_TOKEN")
        or "no_token"
    )
    token_hash = hashlib.sha1(str(token).encode("utf-8")).hexdigest()[:16]
    return Path("/tmp") / f"profit_control_ads_lock_{token_hash}"


def _acquire_ads_lock(headers: Mapping[str, str]) -> Path | None:
    lock_path = _ads_lock_path(headers)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(datetime.utcnow().isoformat())
        return lock_path
    except FileExistsError:
        return None


def _release_ads_lock(lock_path: Path | None) -> None:
    if lock_path is None:
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        return


def _build_attempt_pages(
    attempts: list[dict[str, Any]],
    params: Mapping[str, Any],
    *,
    response_key: str,
    status_override: int | None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for attempt in attempts:
        parsed = attempt.get("parsed_response")
        if isinstance(parsed, list):
            response_payload: dict[str, Any] = {response_key: [row for row in parsed if isinstance(row, dict)]}
        elif isinstance(parsed, Mapping):
            response_payload = dict(parsed)
        else:
            response_payload = {response_key: []}
        pages.append(
            {
                "response": response_payload,
                "request_params": dict(params),
                "http_status": attempt.get("http_status") if isinstance(attempt.get("http_status"), int) else status_override,
                "request_attempt_no": attempt.get("attempt_no"),
                "request_started_at": attempt.get("request_started_at"),
                "request_finished_at": attempt.get("response_finished_at"),
                "response_body_size": attempt.get("response_body_size"),
                "response_content_type": attempt.get("response_content_type"),
                "raw_body": attempt.get("raw_body"),
                "empty_body": bool(attempt.get("empty_body", False)),
                "parse_error": bool(attempt.get("parse_error", False)),
                "unexpected_shape": bool(attempt.get("unexpected_shape", False)),
                "error_type": attempt.get("error_type"),
                "error_message": attempt.get("error_message"),
                "request_success": bool(attempt.get("success", False)),
                "rate_limit_retry": attempt.get("rate_limit_retry"),
                "rate_limit_limit": attempt.get("rate_limit_limit"),
                "rate_limit_reset": attempt.get("rate_limit_reset"),
                "request_url": attempt.get("request_url"),
                "request_endpoint": attempt.get("request_endpoint"),
                "cooldown_wait_sec": attempt.get("cooldown_wait_sec"),
            }
        )
    return pages


def _normalize_advert_ids(raw_ids: list[int]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        advert_id = int(raw)
        if advert_id in seen:
            continue
        seen.add(advert_id)
        normalized.append(advert_id)
    return normalized


def _extract_campaign_ids(payload: Mapping[str, Any]) -> list[int]:
    adverts = payload.get("adverts")
    if not isinstance(adverts, list):
        return []

    preferred_statuses = _parse_status_filter(WB_ADS_ACTIVE_STATUSES)
    preferred: list[tuple[int, datetime | None]] = []
    fallback: list[tuple[int, datetime | None]] = []
    seen: set[int] = set()

    for group in adverts:
        if not isinstance(group, Mapping):
            continue
        group_status = _as_int(group.get("status"))
        advert_list = group.get("advert_list")
        if not isinstance(advert_list, list):
            continue
        for advert in advert_list:
            if not isinstance(advert, Mapping):
                continue
            raw_id = advert.get("advertId") or advert.get("id")
            if raw_id is None:
                continue
            try:
                advert_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if advert_id in seen:
                continue
            seen.add(advert_id)
            change_time = _as_datetime(advert.get("changeTime"))
            if preferred_statuses and group_status in preferred_statuses:
                preferred.append((advert_id, change_time))
            else:
                fallback.append((advert_id, change_time))

    selected = preferred if preferred else fallback
    selected.sort(key=lambda item: item[1] or datetime.min, reverse=True)
    ids = [campaign_id for campaign_id, _ in selected]
    if WB_ADS_MAX_CAMPAIGNS > 0:
        ids = ids[:WB_ADS_MAX_CAMPAIGNS]
    logger.info(
        "WB ads campaign filter: preferred_statuses=%s preferred=%s fallback=%s selected=%s",
        sorted(preferred_statuses),
        len(preferred),
        len(fallback),
        len(ids),
    )
    return ids


def _chunk_ids(ids: list[int], chunk_size: int) -> list[list[int]]:
    if chunk_size <= 0:
        raise ValueError("WB_ADS_STATS_MAX_IDS must be > 0")
    return [ids[idx : idx + chunk_size] for idx in range(0, len(ids), chunk_size)]


def _parse_status_filter(raw: str) -> set[int]:
    result: set[int] = set()
    for token in raw.split(","):
        value = token.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError:
            logger.warning("Skip invalid WB_ADS_ACTIVE_STATUSES token: %s", value)
    return result


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


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _chunk_dates(date_from: str, date_to: str, max_days: int) -> list[tuple[str, str]]:
    if max_days <= 0:
        raise ValueError("WB_ADS_STATS_MAX_RANGE_DAYS must be > 0")

    from_date = date.fromisoformat(date_from)
    to_date = date.fromisoformat(date_to)
    if from_date > to_date:
        raise ValueError(f"Invalid WB ads period: {date_from} > {date_to}")

    ranges: list[tuple[str, str]] = []
    start = from_date
    while start <= to_date:
        end = min(start + timedelta(days=max_days - 1), to_date)
        ranges.append((start.isoformat(), end.isoformat()))
        start = end + timedelta(days=1)
    return ranges


def _resolve_campaign_ids(
    headers: Mapping[str, str],
    advert_ids: list[int] | None,
) -> tuple[list[int], list[dict[str, Any]]]:
    if advert_ids:
        return _normalize_advert_ids(advert_ids), []

    params: dict[str, Any] = {}
    attempt_diagnostics: list[dict[str, Any]] = []
    try:
        raw, status = request_json_with_status(
            method="GET",
            url=WB_ADS_CAMPAIGNS_URL,
            headers=headers,
            diagnostics_collector=attempt_diagnostics.append,
            retry_after_buffer_sec=5,
            rate_api_category="ads",
            rate_endpoint="/adv/v1/promotion/count",
        )
    except Exception as exc:
        if not attempt_diagnostics:
            attempt_diagnostics.append(
                {
                    "attempt_no": 1,
                    "request_started_at": None,
                    "response_finished_at": None,
                    "http_status": None,
                    "response_body_size": 0,
                    "response_content_type": None,
                    "raw_body": "",
                    "empty_body": True,
                    "parse_error": False,
                    "unexpected_shape": False,
                    "error_type": "unknown",
                    "error_message": str(exc),
                    "success": False,
                    "parsed_response": None,
                }
            )
        pages = _build_attempt_pages(
            attempt_diagnostics,
            params,
            response_key="ads_campaigns",
            status_override=None,
        )
        raise WbAdsFetchError(str(exc), diagnostic_pages=pages) from exc

    if not attempt_diagnostics:
        attempt_diagnostics.append(
            {
                "attempt_no": 1,
                "request_started_at": None,
                "response_finished_at": None,
                "http_status": status,
                "response_body_size": 0,
                "response_content_type": None,
                "raw_body": "",
                "empty_body": False,
                "parse_error": False,
                "unexpected_shape": False,
                "error_type": None,
                "error_message": None,
                "success": True,
                "parsed_response": raw,
            }
        )

    if not isinstance(raw, Mapping):
        attempt_diagnostics.append(
            {
                "attempt_no": len(attempt_diagnostics) + 1,
                "request_started_at": None,
                "response_finished_at": None,
                "http_status": status,
                "response_body_size": 0,
                "response_content_type": None,
                "raw_body": "",
                "empty_body": True,
                "parse_error": False,
                "unexpected_shape": True,
                "error_type": "unexpected_format",
                "error_message": "WB ads campaign list response must be JSON object",
                "success": False,
                "parsed_response": raw,
            }
        )
        pages = _build_attempt_pages(
            attempt_diagnostics,
            params,
            response_key="ads_campaigns",
            status_override=status,
        )
        raise WbAdsFetchError(
            "WB ads campaign list response must be JSON object",
            diagnostic_pages=pages,
        )

    campaign_ids = _extract_campaign_ids(raw)
    logger.info("Resolved WB ads campaign IDs: %s", len(campaign_ids))
    pages = _build_attempt_pages(
        attempt_diagnostics,
        params,
        response_key="ads_campaigns",
        status_override=status,
    )
    return campaign_ids, pages


def fetch_wb_ads_pages(
    date_from: str,
    date_to: str,
    advert_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    headers = get_wb_headers()
    lock_path = _acquire_ads_lock(headers)
    if lock_path is None:
        logger.warning("WB ads lock is busy; skipping ads fetch to avoid parallel promotion API calls.")
        return []
    try:
        return _fetch_wb_ads_pages_locked(
            date_from=date_from,
            date_to=date_to,
            advert_ids=advert_ids,
            headers=headers,
        )
    finally:
        _release_ads_lock(lock_path)


def _fetch_wb_ads_pages_locked(
    *,
    date_from: str,
    date_to: str,
    advert_ids: list[int] | None,
    headers: Mapping[str, str],
) -> list[dict[str, Any]]:
    campaign_ids, _campaign_pages = _resolve_campaign_ids(headers=headers, advert_ids=advert_ids)
    if not campaign_ids:
        logger.info("No WB ads campaigns found for stats request")
        return []

    pages: list[dict[str, Any]] = []
    batches = _chunk_ids(campaign_ids, WB_ADS_STATS_MAX_IDS)
    date_ranges = _chunk_dates(date_from, date_to, WB_ADS_STATS_MAX_RANGE_DAYS)
    stop_due_to_rate_limit = False
    stats_pages_count = 0
    for range_num, (range_from, range_to) in enumerate(date_ranges, start=1):
        for batch_num, campaign_batch in enumerate(batches, start=1):
            params: dict[str, Any] = {
                "ids": ",".join(str(advert_id) for advert_id in campaign_batch),
                "beginDate": range_from,
                "endDate": range_to,
            }
            attempt_diagnostics: list[dict[str, Any]] = []
            try:
                raw, status = request_json_with_status(
                    method="GET",
                    url=WB_ADS_STATS_URL,
                    headers=headers,
                    params=params,
                    diagnostics_collector=attempt_diagnostics.append,
                    retry_after_buffer_sec=5,
                    rate_api_category="ads",
                    rate_endpoint="/adv/v3/fullstats",
                )
            except RuntimeError as exc:
                is_rate_limit = "WB API HTTP error 429" in str(exc)
                if (not is_rate_limit) or stats_pages_count == 0:
                    if not attempt_diagnostics:
                        attempt_diagnostics.append(
                            {
                                "attempt_no": 1,
                                "request_started_at": None,
                                "response_finished_at": None,
                                "http_status": None,
                                "response_body_size": 0,
                                "response_content_type": None,
                                "raw_body": "",
                                "empty_body": True,
                                "parse_error": False,
                                "unexpected_shape": False,
                                "error_type": "unknown",
                                "error_message": str(exc),
                                "success": False,
                                "parsed_response": None,
                            }
                        )
                    pages.extend(
                        _build_attempt_pages(
                            attempt_diagnostics,
                            params,
                            response_key="ads",
                            status_override=None,
                        )
                    )
                if is_rate_limit:
                    logger.warning(
                        "WB ads stats rate-limited at range=%s/%s batch=%s/%s; stopping further requests",
                        range_num,
                        len(date_ranges),
                        batch_num,
                        len(batches),
                    )
                    stop_due_to_rate_limit = True
                    break
                raise WbAdsFetchError(str(exc), diagnostic_pages=pages) from exc

            if not attempt_diagnostics:
                attempt_diagnostics.append(
                    {
                        "attempt_no": 1,
                        "request_started_at": None,
                        "response_finished_at": None,
                        "http_status": status,
                        "response_body_size": 0,
                        "response_content_type": None,
                        "raw_body": "",
                        "empty_body": False,
                        "parse_error": False,
                        "unexpected_shape": False,
                        "error_type": None,
                        "error_message": None,
                        "success": True,
                        "parsed_response": raw,
                    }
                )

            if isinstance(raw, dict):
                rows: list[dict[str, Any]] = [dict(raw)]
            elif isinstance(raw, list):
                rows = [item for item in raw if isinstance(item, dict)]
            else:
                attempt_diagnostics.append(
                    {
                        "attempt_no": len(attempt_diagnostics) + 1,
                        "request_started_at": None,
                        "response_finished_at": None,
                        "http_status": status,
                        "response_body_size": 0,
                        "response_content_type": None,
                        "raw_body": "",
                        "empty_body": True,
                        "parse_error": False,
                        "unexpected_shape": True,
                        "error_type": "unexpected_format",
                        "error_message": "WB ads stats response must be a JSON object or JSON list",
                        "success": False,
                        "parsed_response": raw,
                    }
                )
                pages.extend(
                    _build_attempt_pages(
                        attempt_diagnostics,
                        params,
                        response_key="ads",
                        status_override=status,
                    )
                )
                raise WbAdsFetchError(
                    "WB ads stats response must be a JSON object or JSON list",
                    diagnostic_pages=pages,
                )

            logger.info(
                "Fetched WB ads stats range=%s/%s batch=%s/%s campaign_ids=%s rows=%s",
                range_num,
                len(date_ranges),
                batch_num,
                len(batches),
                len(campaign_batch),
                len(rows),
            )
            pages.extend(
                _build_attempt_pages(
                    attempt_diagnostics,
                    params,
                    response_key="ads",
                    status_override=status,
                )
            )
            stats_pages_count += 1
        if stop_due_to_rate_limit:
            break

    if stop_due_to_rate_limit and stats_pages_count == 0:
        raise WbAdsFetchError("WB ads stats rate-limited before any page was fetched", diagnostic_pages=pages)
    if stop_due_to_rate_limit:
        logger.warning("Returning partial WB ads pages due to rate limit: pages=%s", len(pages))

    return pages
