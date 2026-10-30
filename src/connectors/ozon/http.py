from __future__ import annotations

import json
import logging
import os
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib import error, request

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SslConfig:
    context: ssl.SSLContext
    mode: str
    ca_bundle_path: str | None
    ca_bundle_source: str | None
    verify_enabled: bool


_LOGGED_SSL_SIGNATURES: set[tuple[str, str | None, str | None, bool]] = set()


def _env_flag(name: str) -> bool:
    value = (os.getenv(name) or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _resolve_ca_bundle() -> tuple[str | None, str | None]:
    for env_name in ("OZON_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        raw_value = (os.getenv(env_name) or "").strip()
        if not raw_value:
            continue
        path = Path(raw_value).expanduser()
        if not path.exists():
            raise RuntimeError(
                f"Configured CA bundle from {env_name} not found: {path}"
            )
        if not path.is_file():
            raise RuntimeError(
                f"Configured CA bundle from {env_name} is not a file: {path}"
            )
        return str(path), env_name
    return None, None


def _build_ssl_config() -> SslConfig:
    ca_bundle_path, ca_bundle_source = _resolve_ca_bundle()
    if _env_flag("OZON_SSL_NO_VERIFY"):
        raise RuntimeError(
            "OZON_SSL_NO_VERIFY is unsupported: TLS certificate verification must remain enabled. "
            "Configure OZON_CA_BUNDLE when a trusted custom CA is required."
        )

    if ca_bundle_path:
        context = ssl.create_default_context(cafile=ca_bundle_path)
        return SslConfig(
            context=context,
            mode="custom_ca_bundle",
            ca_bundle_path=ca_bundle_path,
            ca_bundle_source=ca_bundle_source,
            verify_enabled=True,
        )

    context = ssl.create_default_context()
    return SslConfig(
        context=context,
        mode="default_verify",
        ca_bundle_path=None,
        ca_bundle_source=None,
        verify_enabled=True,
    )


def _log_ssl_config(config: SslConfig) -> None:
    signature = (
        config.mode,
        config.ca_bundle_path,
        config.ca_bundle_source,
        config.verify_enabled,
    )
    if signature in _LOGGED_SSL_SIGNATURES:
        return
    _LOGGED_SSL_SIGNATURES.add(signature)

    if config.mode == "default_verify":
        logger.info("SSL mode: default verify")
        return

    if config.mode == "custom_ca_bundle":
        logger.info("SSL mode: custom CA bundle")
        logger.info("CA bundle source: %s", config.ca_bundle_source)
        logger.info("CA bundle path: %s", config.ca_bundle_path)
        return



def post_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int = 30,
) -> dict[str, Any]:
    parsed, _status = post_json_with_status(url, payload, headers, timeout=timeout)
    return parsed


def post_json_with_status(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int = 30,
) -> tuple[dict[str, Any], int]:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url=url, data=body, method="POST")
    for name, value in headers.items():
        req.add_header(name, value)

    ssl_config = _build_ssl_config()
    _log_ssl_config(ssl_config)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            with request.urlopen(req, timeout=timeout, context=ssl_config.context) as resp:
                response_body = resp.read().decode("utf-8")
                status = resp.getcode()
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
            logger.error("HTTP error %s for %s. Body: %s", status, url, error_body)

            if status >= 500 and attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue
            raise RuntimeError(f"Ozon API HTTP error {status}: {error_body}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            logger.warning(
                "Network error on attempt %s/%s for %s: %s",
                attempt,
                max_attempts,
                url,
                exc,
            )
            if attempt < max_attempts:
                time.sleep(2 ** (attempt - 1))
                continue
            raise RuntimeError(f"Ozon API network error after {max_attempts} attempts: {exc}") from exc

        if status >= 500 and attempt < max_attempts:
            logger.warning("Retrying %s due to HTTP %s", url, status)
            time.sleep(2 ** (attempt - 1))
            continue
        if status >= 400:
            logger.error("HTTP error %s for %s. Body: %s", status, url, response_body)
            raise RuntimeError(f"Ozon API HTTP error {status}: {response_body}")

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Ozon API returned non-JSON response: {response_body[:400]}") from exc

        if not isinstance(parsed, dict):
            raise RuntimeError("Ozon API response must be a JSON object")

        return parsed, status

    raise RuntimeError("Unexpected retry flow termination")
