from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping
from urllib import error, request

logger = logging.getLogger(__name__)


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

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            with request.urlopen(req, timeout=timeout) as resp:
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
