"""In-memory canonical storage for gateway resources.

Skeleton: type definitions only, no behavior yet.
"""
from __future__ import annotations

from typing import Any


class OrderStore:
    """Stores orders in their canonical (latest-version) representation."""

    def __init__(self) -> None:
        raise NotImplementedError

    def create(self, canonical: dict[str, Any]) -> dict[str, Any]:
        """Persist a canonical order and return it. (stub)"""
        raise NotImplementedError

    def get(self, order_id: str) -> dict[str, Any] | None:
        """Fetch a canonical order by id, or None. (stub)"""
        raise NotImplementedError
