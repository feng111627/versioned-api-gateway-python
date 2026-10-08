"""Idempotency-key handling for write operations.

Skeleton: type definitions only, no behavior yet.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable


class IdempotencyStore:
    """Records the first execution per idempotency key and replays it."""

    def __init__(self) -> None:
        raise NotImplementedError

    async def execute_once(
        self,
        key: str,
        fingerprint: str,
        operation: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any], bool]:
        """Run ``operation`` at most once per key. (stub)

        Returns (status_code, body, replayed).
        """
        raise NotImplementedError
