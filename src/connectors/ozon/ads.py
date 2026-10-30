from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal, Mapping

from src.connectors.ozon.auth import MissingOzonCredentialsError, get_ozon_headers
from src.connectors.ozon.http import post_json_with_status

logger = logging.getLogger(__name__)

DEFAULT_ADS_STATS_URL_LEGACY = "https://api-seller.ozon.ru/v1/advertising/stats"
DEFAULT_ADS_STATS_URL_PERFORMANCE = "https://api-performance.ozon.ru/api/client/statistics/json"
PaginationMode = Literal["offset", "page"]
HTTP_STATUS_PATTERN = re.compile(r"Ozon API HTTP error (\d+):")


def _get_ads_headers() -> dict[str, str]:
    ads_token = os.getenv("OZON_ADS_TOKEN")
    if ads_token:
        return {
            "Authorization": f"Bearer {ads_token}",
            "Content-Type": "application/json",
        }

    ads_client_id = os.getenv("OZON_ADS_CLIENT_ID")
    ads_api_key = os.getenv("OZON_ADS_API_KEY")
    if ads_client_id and ads_api_key:
        return {
            "Client-Id": ads_client_id,
            "Api-Key": ads_api_key,
            "Content-Type": "application/json",
        }

    try:
        return get_ozon_headers()
    except MissingOzonCredentialsError as exc:
        raise MissingOzonCredentialsError(
            "Missing OZON_CLIENT_ID/OZON_API_KEY (or OZON_ADS_TOKEN / OZON_ADS_CLIENT_ID+OZON_ADS_API_KEY)"
        ) from exc


def _extract_batch_size(response: Mapping[str, Any]) -> int:
    blocks: list[Any] = []
    result = response.get("result")
    if result is not None:
        blocks.append(result)
    blocks.append(response)

    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        for key in ("campaigns", "items", "rows", "data", "statistics"):
            value = block.get(key)
            if isinstance(value, list):
                return len(value)
    return 0


def _parse_http_status(exc: RuntimeError) -> int | None:
    match = HTTP_STATUS_PATTERN.search(str(exc))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _build_ads_endpoint_candidates(endpoint: str | None, headers: Mapping[str, str]) -> list[str]:
    if endpoint:
        return [endpoint]

    env_endpoint = (os.getenv("OZON_ADS_STATS_URL") or "").strip()
    if env_endpoint:
        return [env_endpoint]

    has_bearer = headers.get("Authorization", "").startswith("Bearer ")
    if has_bearer:
        return [DEFAULT_ADS_STATS_URL_PERFORMANCE, DEFAULT_ADS_STATS_URL_LEGACY]
    return [DEFAULT_ADS_STATS_URL_LEGACY, DEFAULT_ADS_STATS_URL_PERFORMANCE]


def _parse_campaign_ids_from_env() -> list[int]:
    raw = (os.getenv("OZON_ADS_CAMPAIGN_IDS") or "").strip()
    if not raw:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            campaign_id = int(token)
        except ValueError:
            logger.warning("Skip invalid OZON_ADS_CAMPAIGN_IDS token: %s", token)
            continue
        if campaign_id in seen:
            continue
        seen.add(campaign_id)
        result.append(campaign_id)
    return result


def _build_payload(
    endpoint_url: str,
    date_from: str,
    date_to: str,
    limit: int,
    pagination_mode: PaginationMode,
    current_offset: int,
    current_page: int,
    extra_payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if "api-performance.ozon.ru" in endpoint_url:
        payload: dict[str, Any] = {
            "dateFrom": date_from,
            "dateTo": date_to,
            "groupBy": os.getenv("OZON_ADS_GROUP_BY", "SKU"),
        }
        campaigns = _parse_campaign_ids_from_env()
        if campaigns:
            payload["campaigns"] = campaigns
        if extra_payload:
            payload.update(extra_payload)
        return payload

    payload = {
        "date_from": date_from,
        "date_to": date_to,
        "limit": limit,
    }
    if extra_payload:
        payload.update(extra_payload)
    if pagination_mode == "offset":
        payload["offset"] = current_offset
    else:
        payload["page"] = current_page
    return payload


def _request_with_fallback(
    endpoint_candidates: list[str],
    headers: Mapping[str, str],
    *,
    date_from: str,
    date_to: str,
    limit: int,
    pagination_mode: PaginationMode,
    current_offset: int,
    current_page: int,
    extra_payload: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], int, str]:
    last_error: RuntimeError | None = None
    for endpoint_url in endpoint_candidates:
        payload = _build_payload(
            endpoint_url=endpoint_url,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            pagination_mode=pagination_mode,
            current_offset=current_offset,
            current_page=current_page,
            extra_payload=extra_payload,
        )
        try:
            response, http_status = post_json_with_status(endpoint_url, payload, headers)
            return response, http_status, endpoint_url
        except RuntimeError as exc:
            last_error = exc
            status = _parse_http_status(exc)
            if status == 404:
                logger.warning("Ads endpoint not found (%s), trying next candidate", endpoint_url)
                continue
            break

    if last_error is not None:
        raise RuntimeError(
            "Ozon ads request failed after endpoint candidates "
            f"{endpoint_candidates}. Last error: {last_error}"
        ) from last_error
    raise RuntimeError(f"Ozon ads request failed: no endpoint candidates provided ({endpoint_candidates})")


def fetch_ads_pages(
    date_from: str,
    date_to: str,
    limit: int = 100,
    max_pages: int = 1000,
    offset: int = 0,
    pagination_mode: PaginationMode = "offset",
    endpoint: str | None = None,
    extra_payload: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if max_pages <= 0:
        raise ValueError("max_pages must be > 0")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    headers = _get_ads_headers()
    endpoint_candidates = _build_ads_endpoint_candidates(endpoint=endpoint, headers=headers)
    logger.info("Ozon ads endpoint candidates: %s", endpoint_candidates)

    pages: list[dict[str, Any]] = []
    current_offset = offset
    current_page = 1
    selected_endpoint: str | None = None

    page_num = 0
    while page_num < max_pages:
        page_num += 1
        request_endpoints = [selected_endpoint] if selected_endpoint else endpoint_candidates
        response, http_status, used_endpoint = _request_with_fallback(
            endpoint_candidates=request_endpoints,
            headers=headers,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            pagination_mode=pagination_mode,
            current_offset=current_offset,
            current_page=current_page,
            extra_payload=extra_payload,
        )
        if selected_endpoint is None:
            selected_endpoint = used_endpoint
            logger.info("Selected Ozon ads endpoint: %s", selected_endpoint)

        probe_payload = _build_payload(
            endpoint_url=used_endpoint,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            pagination_mode=pagination_mode,
            current_offset=current_offset,
            current_page=current_page,
            extra_payload=extra_payload,
        )
        batch_size = _extract_batch_size(response)
        pages.append(
            {
                "response": response,
                "request_params": probe_payload,
                "http_status": http_status,
                "endpoint": used_endpoint,
            }
        )
        logger.info(
            "Fetched ads page=%s batch_size=%s endpoint=%s",
            page_num,
            batch_size,
            used_endpoint,
        )

        if batch_size == 0:
            break

        if pagination_mode == "offset":
            current_offset += batch_size
            if batch_size < limit:
                break
        else:
            current_page += 1
            if batch_size < limit:
                break

    if page_num >= max_pages:
        logger.warning("Reached max_pages=%s while fetching ads", max_pages)

    return pages
