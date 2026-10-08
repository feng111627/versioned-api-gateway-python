"""Idempotency-key handling for write operations.

Semantics:
  * The first request seen for a key executes the operation; its
    (status, body) is recorded.
  * Later requests with the same key AND the same payload fingerprint
    replay the recorded response without re-executing — including
    requests that arrive concurrently (they wait on a per-key lock).
  * The same key with a DIFFERENT payload fingerprint is rejected with
    409 ``idempotency_conflict``.
  * Different keys are fully independent.
  * Failed operations (exceptions) are never recorded, so a client may
    retry after a transient failure.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.errors import IdempotencyConflictError

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"


@dataclass
class _Record:
    fingerprint: str
    status_code: int
    body: dict[str, Any]


class IdempotencyStore:
    """Records the first execution per idempotency key and replays it."""

    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _lock_for(self, key: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    async def execute_once(
        self,
        key: str,
        fingerprint: str,
        operation: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
    ) -> tuple[int, dict[str, Any], bool]:
        """Run ``operation`` at most once per key.

        Returns:
            (status_code, body, replayed) — ``replayed`` is True when a
            previously recorded response is returned without executing.

        Raises:
            IdempotencyConflictError: key reused with a different payload.
        """
        lock = await self._lock_for(key)
        async with lock:
            record = self._records.get(key)
            if record is not None:
                if record.fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        message="idempotency key was already used with a different payload",
                        details={"idempotency_key": key},
                    )
                return record.status_code, dict(record.body), True
            status_code, body = await operation()
            self._records[key] = _Record(
                fingerprint=fingerprint, status_code=status_code, body=dict(body)
            )
            return status_code, body, False
