from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitKey:
    marketplace: str
    seller_token_fingerprint: str
    api_category: str
    endpoint: str


@dataclass
class RateLimitState:
    cooldown_until_monotonic: float = 0.0
    last_http_status: int | None = None
    last_retry_after_sec: int | None = None
    last_limit: str | None = None
    last_reset: str | None = None
    updated_at_monotonic: float = 0.0


class RateLimitCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[RateLimitKey, RateLimitState] = {}

    def wait_if_needed(self, key: RateLimitKey) -> float:
        sleep_for = 0.0
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return 0.0
            now = time.monotonic()
            if now < state.cooldown_until_monotonic:
                sleep_for = state.cooldown_until_monotonic - now
        if sleep_for > 0:
            logger.info(
                "RateLimitCoordinator cooldown sleep marketplace=%s category=%s endpoint=%s sleep=%.3fs",
                key.marketplace,
                key.api_category,
                key.endpoint,
                sleep_for,
            )
            time.sleep(sleep_for)
        return sleep_for

    def register_rate_limit(
        self,
        *,
        key: RateLimitKey,
        retry_after_sec: int | None,
        headers: Mapping[str, str | None] | None,
        http_status: int | None,
    ) -> None:
        cooldown = max(int(retry_after_sec or 0), 0)
        now = time.monotonic()
        with self._lock:
            state = self._states.setdefault(key, RateLimitState())
            state.last_http_status = http_status
            state.last_retry_after_sec = retry_after_sec
            state.last_limit = (headers or {}).get("X-Ratelimit-Limit")
            state.last_reset = (headers or {}).get("X-Ratelimit-Reset")
            state.updated_at_monotonic = now
            if cooldown > 0:
                state.cooldown_until_monotonic = max(state.cooldown_until_monotonic, now + cooldown)

    def snapshot(self) -> dict[RateLimitKey, RateLimitState]:
        with self._lock:
            return {k: RateLimitState(**vars(v)) for k, v in self._states.items()}


_COORDINATOR = RateLimitCoordinator()


def get_rate_limit_coordinator() -> RateLimitCoordinator:
    return _COORDINATOR


def token_fingerprint(token: str | None) -> str:
    value = (token or "").strip()
    if not value:
        return "anon"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:16]
