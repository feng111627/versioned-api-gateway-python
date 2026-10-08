"""API version negotiation and per-version response transforms.

Skeleton: type definitions only, no behavior yet.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

# A transform converts a canonical (latest-version) representation
# into the representation expected by a specific API version.
Transform = Callable[[dict[str, Any]], dict[str, Any]]


class VersionRegistry:
    """Tracks supported / retired API versions and their transforms."""

    def __init__(self) -> None:
        raise NotImplementedError

    def negotiate(self, requested: str | None) -> str:
        """Resolve the requested version string to an active version. (stub)"""
        raise NotImplementedError

    def transform(self, version: str, canonical: dict[str, Any]) -> dict[str, Any]:
        """Downgrade a canonical representation for the given version. (stub)"""
        raise NotImplementedError
