from __future__ import annotations

import json
import logging
import os
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error, parse, request

from src.utils.rate_limit_coordinator import (
    RateLimitKey,
    get_rate_limit_coordinator,
    token_fingerprint,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SslConfig:
    context: ssl.SSLContext
    mode: str
    ca_bundle_path: str | None
    ca_bundle_source: str | None


_LOGGED_SSL_SIGNATURES: set[tuple[str, str | None, str | None]] = set()
_RATE_LOCK = threading.Lock()
_NEXT_ALLOWED_AT = 0.0
_PARALLEL_LOCK = threading.Lock()
_PARALLEL_LIMIT = 0
_PARALLEL_SEMAPHORE: threading.BoundedSemaphore | None = None


def _env_flag(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _resolve_ca_bundle() -> tuple[str | None, str | None]:
    for env_name in ("WB_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        raw_value = (os.getenv(env_name) or "").strip()
        if not raw_value:
            continue
        path = Path(raw_value).expanduser()
        if not path.exists():
            raise RuntimeError(f"Configured CA bundle from {env_name} not found: {path}")
        if not path.is_file():
            raise RuntimeError(f"Configured CA bundle from {env_name} is not a file: {path}")
        return str(path), env_name
    return None, None


def _build_ssl_config() -> SslConfig:
    ca_bundle_path, ca_bundle_source = _resolve_ca_bundle()
    if _env_flag("WB_SSL_NO_VERIFY"):
        raise RuntimeError(
            "WB_SSL_NO_VERIFY is unsupported: TLS certificate verification must remain enabled. "
            "Configure WB_CA_BUNDLE when a trusted custom CA is required."
        )

    if ca_bundle_path:
        return SslConfig(
            context=ssl.create_default_context(cafile=ca_bundle_path),
            mode="custom_ca_bundle",
            ca_bundle_path=ca_bundle_path,
            ca_bundle_source=ca_bundle_source,
        )

    return SslConfig(
        context=ssl.create_default_context(),
        mode="default_verify",
        ca_bundle_path=None,
        ca_bundle_source=None,
    )


def _log_ssl_config(config: SslConfig) -> None:
    signature = (config.mode, config.ca_bundle_path, config.ca_bundle_source)
    if signature in _LOGGED_SSL_SIGNATURES:
        return
    _LOGGED_SSL_SIGNATURES.add(signature)

    if config.mode == "default_verify":
        logger.info("WB SSL mode: default verify")
        return
    if config.mode == "custom_ca_bundle":
        logger.info("WB SSL mode: custom CA bundle")
        logger.info("WB CA bundle source: %s", config.ca_bundle_source)
        logger.info("WB CA bundle path: %s", config.ca_bundle_path)
        return


def _max_attempts() -> int:
    raw = (os.getenv("WB_HTTP_MAX_ATTEMPTS") or "").strip()
    if not raw:
        return 5
    try:
        return max(int(raw), 1)
    except ValueError:
        return 5


def _retry_delays() -> list[int]:
    raw = (os.getenv("WB_HTTP_RETRY_DELAYS_SEC") or "").strip()
    if not raw:
        return [5, 10, 20, 40]
    values: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value >= 0:
            values.append(value)
    return values or [5, 10, 20, 40]


def _retry_delay(attempt: int, delays_override: list[int] | None = None) -> int:
    delays = delays_override or _retry_delays()
    index = max(attempt - 1, 0)
    if index < len(delays):
        return delays[index]
    return delays[-1]


def _global_rps_limit(api_category: str | None = None) -> float:
    category = (api_category or "").strip().upper()
    if category:
        raw = (os.getenv(f"WB_MAX_REQUESTS_PER_SECOND_{category}") or "").strip()
        if raw:
            try:
                parsed = float(raw)
            except ValueError:
                parsed = 1.0
            return parsed if parsed > 0 else 1.0
        raw = (os.getenv(f"WB_HTTP_GLOBAL_RPS_{category}") or "").strip()
        if raw:
            try:
                parsed = float(raw)
            except ValueError:
                parsed = 1.0
            return parsed if parsed > 0 else 1.0

    raw = (os.getenv("WB_MAX_REQUESTS_PER_SECOND") or "").strip()
    if not raw:
        raw = (os.getenv("WB_HTTP_GLOBAL_RPS") or "").strip()
    if not raw:
        raw = (os.getenv("WB_GLOBAL_MAX_RPS") or "").strip()
    if not raw:
        return 1.0
    try:
        parsed = float(raw)
    except ValueError:
        return 1.0
    return parsed if parsed > 0 else 1.0


def _max_parallel_requests_per_source(api_category: str | None = None) -> int:
    category = (api_category or "").strip().upper()
    if category:
        raw = (os.getenv(f"WB_MAX_PARALLEL_REQUESTS_PER_SOURCE_{category}") or "").strip()
        if raw:
            try:
                parsed = int(raw)
            except ValueError:
                parsed = 1
            return max(parsed, 1)
    raw = (os.getenv("WB_MAX_PARALLEL_REQUESTS_PER_SOURCE") or "").strip()
    if not raw:
        raw = (os.getenv("WB_HTTP_MAX_PARALLEL_REQUESTS_PER_SOURCE") or "").strip()
    if not raw:
        return 1
    try:
        parsed = int(raw)
    except ValueError:
        return 1
    return max(parsed, 1)


def _get_parallel_semaphore(api_category: str | None = None) -> threading.BoundedSemaphore:
    global _PARALLEL_LIMIT, _PARALLEL_SEMAPHORE
    limit = _max_parallel_requests_per_source(api_category)
    with _PARALLEL_LOCK:
        if _PARALLEL_SEMAPHORE is None or _PARALLEL_LIMIT != limit:
            _PARALLEL_SEMAPHORE = threading.BoundedSemaphore(limit)
            _PARALLEL_LIMIT = limit
        return _PARALLEL_SEMAPHORE


def _apply_global_rate_limit(api_category: str | None = None) -> None:
    global _NEXT_ALLOWED_AT

    rps = _global_rps_limit(api_category)
    min_interval = 1.0 / rps
    with _RATE_LOCK:
        now = time.monotonic()
        if now < _NEXT_ALLOWED_AT:
            sleep_for = _NEXT_ALLOWED_AT - now
            if sleep_for > 0:
                time.sleep(sleep_for)
            now = time.monotonic()
        _NEXT_ALLOWED_AT = max(_NEXT_ALLOWED_AT, now) + min_interval


def request_json(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any] | list[Any]:
    parsed, _ = request_json_with_status(
        method=method,
        url=url,
        headers=headers,
        payload=payload,
        params=params,
        timeout=timeout,
    )
    return parsed


def request_json_with_status(
    method: str,
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: int = 30,
    diagnostics_collector: Callable[[dict[str, Any]], None] | None = None,
    max_attempts_override: int | None = None,
    retry_delays_override: list[int] | None = None,
    retry_after_buffer_sec: int = 0,
    rate_api_category: str | None = None,
    rate_endpoint: str | None = None,
    seller_token: str | None = None,
) -> tuple[dict[str, Any] | list[Any], int]:
    encoded_params = parse.urlencode(
        {k: v for k, v in (params or {}).items() if v is not None},
        doseq=True,
    )
    full_url = f"{url}?{encoded_params}" if encoded_params else url

    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    req = request.Request(url=full_url, data=body, method=method.upper())
    for name, value in headers.items():
        req.add_header(name, value)

    ssl_config = _build_ssl_config()
    _log_ssl_config(ssl_config)
    coordinator = get_rate_limit_coordinator()
    rate_key = _build_rate_key(
        headers=headers,
        full_url=full_url,
        api_category=rate_api_category,
        endpoint=rate_endpoint,
        seller_token=seller_token,
    )

    max_attempts = max_attempts_override if (max_attempts_override and max_attempts_override > 0) else _max_attempts()
    attempt_error_types: list[str] = []
    for attempt in range(1, max_attempts + 1):
        cooldown_wait_sec = coordinator.wait_if_needed(rate_key)
        request_started_at = _now_iso()
        response_finished_at = request_started_at
        response_content_type: str | None = None
        response_body = ""
        response_headers: dict[str, str] | None = None
        try:
            semaphore = _get_parallel_semaphore(rate_api_category)
            semaphore.acquire()
            try:
                _apply_global_rate_limit(rate_api_category)
                with request.urlopen(req, timeout=timeout, context=ssl_config.context) as resp:
                    response_body = resp.read().decode("utf-8")
                    status = resp.getcode()
                    response_content_type = resp.headers.get("Content-Type")
                    response_headers = _headers_to_dict(resp.headers)
                    rate_headers = _extract_rate_headers(resp.headers)
                    response_finished_at = _now_iso()
            finally:
                semaphore.release()
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
            response_content_type = exc.headers.get("Content-Type") if exc.headers is not None else None
            response_headers = _headers_to_dict(exc.headers)
            rate_headers = _extract_rate_headers(exc.headers)
            response_finished_at = _now_iso()
            retry_after_sec = _retry_after_from_headers(rate_headers)
            error_type = "rate_limit" if status == 429 else "http_error"
            if status == 429:
                coordinator.register_rate_limit(
                    key=rate_key,
                    retry_after_sec=retry_after_sec,
                    headers=rate_headers,
                    http_status=status,
                )
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=status,
                response_content_type=response_content_type,
                response_body=error_body,
                response_headers=response_headers,
                parse_error=False,
                unexpected_shape=False,
                error_type=error_type,
                error_message=f"WB API HTTP error {status}",
                success=False,
                rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
                rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
                rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            attempt_error_types.append(error_type)
            logger.error("HTTP error %s for %s. Body: %s", status, full_url, error_body)
            if (status >= 500 or status == 429) and attempt < max_attempts:
                if status == 429 and retry_after_sec and retry_after_sec > 0:
                    wait_total = retry_after_sec + max(int(retry_after_buffer_sec), 0)
                    time.sleep(wait_total)
                else:
                    time.sleep(_retry_delay(attempt, retry_delays_override))
                continue
            raise RuntimeError(f"WB API HTTP error {status}: {error_body}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            response_finished_at = _now_iso()
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=None,
                response_content_type=response_content_type,
                response_body="",
                response_headers=response_headers,
                parse_error=False,
                unexpected_shape=False,
                error_type="network_before_body",
                error_message=str(exc),
                success=False,
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            attempt_error_types.append("network_before_body")
            logger.warning(
                "Network error on attempt %s/%s for %s: %s",
                attempt,
                max_attempts,
                full_url,
                exc,
            )
            if attempt < max_attempts:
                time.sleep(_retry_delay(attempt, retry_delays_override))
                continue
            raise RuntimeError(f"WB API network error after {max_attempts} attempts: {exc}") from exc

        if status >= 500 and attempt < max_attempts:
            logger.warning("Retrying %s due to HTTP %s", full_url, status)
            time.sleep(_retry_delay(attempt, retry_delays_override))
            continue
        if status >= 400:
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=status,
                response_content_type=response_content_type,
                response_body=response_body,
                parse_error=False,
                unexpected_shape=False,
                error_type="http_error",
                error_message=f"WB API HTTP error {status}",
                success=False,
                rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
                rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
                rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            logger.error("HTTP error %s for %s. Body: %s", status, full_url, response_body)
            raise RuntimeError(f"WB API HTTP error {status}: {response_body}")

        if not response_body.strip():
            empty_error_type = _classify_empty_body_error_type(
                status=status,
                prior_error_types=attempt_error_types,
            )
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=status,
                response_content_type=response_content_type,
                response_body=response_body,
                response_headers=response_headers,
                parse_error=False,
                unexpected_shape=False,
                error_type=empty_error_type,
                error_message=f"WB API {empty_error_type}",
                success=False,
                rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
                rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
                rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            attempt_error_types.append(empty_error_type)
            if attempt < max_attempts:
                logger.warning(
                    "WB API empty response body on attempt %s/%s for %s",
                    attempt,
                    max_attempts,
                    full_url,
                )
                time.sleep(_retry_delay(attempt, retry_delays_override))
                continue
            raise RuntimeError(f"WB API {empty_error_type}")

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            parse_error_type = _classify_json_parse_error_type(response_body, exc)
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=status,
                response_content_type=response_content_type,
                response_body=response_body,
                response_headers=response_headers,
                parse_error=True,
                unexpected_shape=False,
                error_type=parse_error_type,
                error_message=f"WB API {parse_error_type}",
                success=False,
                rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
                rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
                rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            attempt_error_types.append(parse_error_type)
            raise RuntimeError(f"WB API {parse_error_type}: {response_body[:400]}") from exc

        if parsed is None:
            parsed = []

        if not isinstance(parsed, (dict, list)):
            _emit_attempt_diagnostic(
                diagnostics_collector=diagnostics_collector,
                attempt=attempt,
                request_started_at=request_started_at,
                response_finished_at=response_finished_at,
                http_status=status,
                response_content_type=response_content_type,
                response_body=response_body,
                response_headers=response_headers,
                parse_error=False,
                unexpected_shape=True,
                error_type="unexpected_format",
                error_message="WB API response must be a JSON object or JSON list",
                success=False,
                rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
                rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
                rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
                request_url=full_url,
                request_endpoint=rate_key.endpoint,
                cooldown_wait_sec=cooldown_wait_sec,
            )
            raise RuntimeError("WB API response must be a JSON object or JSON list")

        _emit_attempt_diagnostic(
            diagnostics_collector=diagnostics_collector,
            attempt=attempt,
            request_started_at=request_started_at,
            response_finished_at=response_finished_at,
            http_status=status,
            response_content_type=response_content_type,
            response_body=response_body,
            response_headers=response_headers,
            parse_error=False,
            unexpected_shape=False,
            error_type=None,
            error_message=None,
            success=True,
            parsed_response=parsed,
            rate_limit_retry=rate_headers.get("X-Ratelimit-Retry"),
            rate_limit_limit=rate_headers.get("X-Ratelimit-Limit"),
            rate_limit_reset=rate_headers.get("X-Ratelimit-Reset"),
            request_url=full_url,
            request_endpoint=rate_key.endpoint,
            cooldown_wait_sec=cooldown_wait_sec,
        )
        return parsed, status

    raise RuntimeError("Unexpected retry flow termination")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_attempt_diagnostic(
    *,
    diagnostics_collector: Callable[[dict[str, Any]], None] | None,
    attempt: int,
    request_started_at: str,
    response_finished_at: str,
    http_status: int | None,
    response_content_type: str | None,
    response_body: str,
    response_headers: Mapping[str, str] | None,
    parse_error: bool,
    unexpected_shape: bool,
    error_type: str | None,
    error_message: str | None,
    success: bool,
    parsed_response: dict[str, Any] | list[Any] | None = None,
    rate_limit_retry: str | None = None,
    rate_limit_limit: str | None = None,
    rate_limit_reset: str | None = None,
    request_url: str | None = None,
    request_endpoint: str | None = None,
    cooldown_wait_sec: float | None = None,
) -> None:
    if diagnostics_collector is None:
        return
    diagnostics_collector(
        {
            "attempt_no": attempt,
            "request_started_at": request_started_at,
            "response_finished_at": response_finished_at,
            "http_status": http_status,
            "response_body_size": len(response_body.encode("utf-8")),
            "response_content_type": response_content_type,
            "response_headers": dict(response_headers or {}),
            "raw_body": response_body,
            "empty_body": not response_body.strip(),
            "parse_error": parse_error,
            "unexpected_shape": unexpected_shape,
            "error_type": error_type,
            "error_message": error_message,
            "success": success,
            "parsed_response": parsed_response,
            "rate_limit_retry": rate_limit_retry,
            "rate_limit_limit": rate_limit_limit,
            "rate_limit_reset": rate_limit_reset,
            "request_url": request_url,
            "request_endpoint": request_endpoint,
            "cooldown_wait_sec": cooldown_wait_sec,
        }
    )


def _headers_to_dict(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key): str(value) for key, value in headers.items()}


def _classify_empty_body_error_type(*, status: int | None, prior_error_types: list[str]) -> str:
    had_rate_limit = any(err == "rate_limit" for err in prior_error_types)
    if status == 204:
        return "http_no_content_after_429" if had_rate_limit else "http_no_content"
    if status is not None and 200 <= status < 300:
        return "http_success_empty_body_after_429" if had_rate_limit else "http_success_empty_body"
    return "empty_response"


def _classify_json_parse_error_type(response_body: str, exc: json.JSONDecodeError) -> str:
    body = response_body.strip()
    msg = (exc.msg or "").lower()
    if body and (
        "unterminated" in msg
        or "expecting value" in msg
        or "delimiter" in msg
        or exc.pos >= max(len(response_body) - 2, 0)
    ):
        return "http_success_truncated_body"
    return "http_success_non_json"


def _extract_rate_headers(headers: Mapping[str, str] | None) -> dict[str, str | None]:
    if headers is None:
        return {
            "X-Ratelimit-Retry": None,
            "X-Ratelimit-Limit": None,
            "X-Ratelimit-Reset": None,
        }
    return {
        "X-Ratelimit-Retry": headers.get("X-Ratelimit-Retry"),
        "X-Ratelimit-Limit": headers.get("X-Ratelimit-Limit"),
        "X-Ratelimit-Reset": headers.get("X-Ratelimit-Reset"),
    }


def _retry_after_from_headers(headers: Mapping[str, str | None]) -> int | None:
    retry_raw = (headers.get("X-Ratelimit-Retry") or "").strip()
    if retry_raw:
        try:
            parsed = int(float(retry_raw))
            if parsed >= 0:
                return parsed
        except ValueError:
            pass

    reset_raw = (headers.get("X-Ratelimit-Reset") or "").strip()
    if reset_raw:
        try:
            reset_epoch = int(float(reset_raw))
            now_epoch = int(time.time())
            delta = max(reset_epoch - now_epoch, 0)
            return delta
        except ValueError:
            return None
    return None


def _build_rate_key(
    *,
    headers: Mapping[str, str],
    full_url: str,
    api_category: str | None,
    endpoint: str | None,
    seller_token: str | None,
) -> RateLimitKey:
    resolved_endpoint = endpoint or parse.urlparse(full_url).path or "/"
    resolved_category = (api_category or _guess_api_category(resolved_endpoint)).strip().lower() or "generic"
    auth_header = seller_token
    if not auth_header:
        auth_header = headers.get("Authorization") or headers.get("authorization")
    return RateLimitKey(
        marketplace="wb",
        seller_token_fingerprint=token_fingerprint(auth_header),
        api_category=resolved_category,
        endpoint=resolved_endpoint,
    )


def _guess_api_category(endpoint: str) -> str:
    low = endpoint.lower()
    if "adv" in low:
        return "ads"
    if "reportdetailbyperiod" in low or "finance" in low:
        return "finance"
    if "sales" in low:
        return "sales"
    if "orders" in low:
        return "orders"
    if "stocks" in low:
        return "stock"
    return "generic"
