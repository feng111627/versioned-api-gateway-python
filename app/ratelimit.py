"""Per-caller token-bucket rate limiting.

Skeleton: type definitions only, no behavior yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit check for one request."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: float


class RateLimiter:
    """Token-bucket limiter keyed by caller identity."""

    def __init__(self, capacity: int, refill_per_second: float) -> None:
        raise NotImplementedError

    def check(self, caller: str) -> RateLimitDecision:
        """Consume one token for ``caller`` if available. (stub)"""
        raise NotImplementedError
