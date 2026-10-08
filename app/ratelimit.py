"""Per-caller token-bucket rate limiting.

Each caller (identified by the ``X-Caller-Id`` header, defaulting to
``anonymous``) gets an independent bucket. Every request consumes one
token; when the bucket is empty the request is rejected with 429 and the
response reports the limit, remaining quota and retry-after hint.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

CALLER_HEADER = "X-Caller-Id"
DEFAULT_CALLER = "anonymous"

LIMIT_HEADER = "X-RateLimit-Limit"
REMAINING_HEADER = "X-RateLimit-Remaining"
RESET_HEADER = "X-RateLimit-Reset"
RETRY_AFTER_HEADER = "Retry-After"


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit check for one request."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class _Bucket:
    __slots__ = ("tokens", "updated_at")

    def __init__(self, tokens: float, updated_at: float) -> None:
        self.tokens = tokens
        self.updated_at = updated_at


class RateLimiter:
    """Token-bucket limiter keyed by caller identity."""

    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._refill = refill_per_second
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, caller: str) -> RateLimitDecision:
        """Consume one token for ``caller`` if available."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(caller)
            if bucket is None:
                bucket = _Bucket(tokens=float(self._capacity), updated_at=now)
                self._buckets[caller] = bucket
            # refill proportionally to elapsed time
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(
                float(self._capacity), bucket.tokens + elapsed * self._refill
            )
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return RateLimitDecision(
                    allowed=True,
                    limit=self._capacity,
                    remaining=int(bucket.tokens),
                    retry_after_seconds=0.0,
                )
            retry_after = (1.0 - bucket.tokens) / self._refill
            return RateLimitDecision(
                allowed=False,
                limit=self._capacity,
                remaining=0,
                retry_after_seconds=round(retry_after, 3),
            )
