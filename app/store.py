"""In-memory canonical storage for gateway resources.

Orders are always stored in the canonical (latest-version) shape;
version-specific views are produced by ``app.versioning``.

The store talks to a (simulated) upstream pricing dependency. When that
dependency fails it raises ``UpstreamError`` so the gateway can prove it
converts dependency failures into a stable error envelope instead of
leaking internal exceptions.
"""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone
from typing import Any

from app.errors import UpstreamError

#: Item name that makes the simulated upstream dependency fail.
UPSTREAM_FAIL_ITEM = "upstream-fail"


class OrderStore:
    """Stores orders in their canonical (latest-version) representation."""

    def __init__(self) -> None:
        self._orders: dict[str, dict[str, Any]] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def _check_upstream(self, item: str) -> None:
        """Simulated upstream dependency call."""
        if item == UPSTREAM_FAIL_ITEM:
            # Deliberately a low-level looking failure; the store wraps it.
            raise RuntimeError("pricing-service connection refused")

    def create(self, canonical: dict[str, Any]) -> dict[str, Any]:
        """Persist a canonical order and return it.

        Raises:
            UpstreamError: if the upstream dependency rejects the call.
        """
        try:
            self._check_upstream(canonical["item"])
        except RuntimeError as exc:
            raise UpstreamError(
                message="upstream dependency failed",
                details={"dependency": "pricing-service"},
            ) from exc
        with self._lock:
            order_id = f"ord_{next(self._counter):06d}"
            order = {
                "id": order_id,
                "item": canonical["item"],
                "total_cents": canonical["total_cents"],
                "currency": canonical["currency"],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._orders[order_id] = order
            return dict(order)

    def get(self, order_id: str) -> dict[str, Any] | None:
        """Fetch a canonical order by id, or None."""
        with self._lock:
            order = self._orders.get(order_id)
            return dict(order) if order is not None else None
