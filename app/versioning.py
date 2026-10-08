"""API version negotiation and per-version representation transforms.

Clients pin a version with the ``X-API-Version`` header. The canonical
internal representation is always the *latest* version; older versions
are served by downgrading responses and upgrading requests, so old
clients are never affected by new-version changes.

Version lifecycle:
    supported  -> served normally
    retired    -> rejected with 410 ``version_retired``
    unknown    -> rejected with 426 ``unsupported_version``
"""
from __future__ import annotations

from typing import Any, Callable

from app.errors import RetiredVersionError, UnsupportedVersionError

VERSION_HEADER = "X-API-Version"

# A transform converts a canonical (latest-version) representation
# into the representation expected by a specific API version.
Transform = Callable[[dict[str, Any]], dict[str, Any]]


def _identity(canonical: dict[str, Any]) -> dict[str, Any]:
    return dict(canonical)


def _canonical_to_v1(canonical: dict[str, Any]) -> dict[str, Any]:
    """v1 sees ``total`` as a float in major units; no currency field."""
    return {
        "id": canonical["id"],
        "item": canonical["item"],
        "total": canonical["total_cents"] / 100,
        "created_at": canonical["created_at"],
    }


class VersionRegistry:
    """Tracks supported / retired API versions and their transforms."""

    LATEST = "v2"
    DEFAULT = "v1"  # unversioned (legacy) clients keep the oldest behavior

    def __init__(self) -> None:
        self._transforms: dict[str, Transform] = {
            "v1": _canonical_to_v1,
            "v2": _identity,
        }
        self._retired: set[str] = {"v0"}

    @property
    def supported_versions(self) -> list[str]:
        return sorted(self._transforms)

    def negotiate(self, requested: str | None) -> str:
        """Resolve the requested version string to an active version.

        Raises:
            RetiredVersionError: version existed but is gone (410).
            UnsupportedVersionError: version never existed (426).
        """
        if requested is None or requested == "":
            return self.DEFAULT
        requested = requested.strip().lower()
        if requested in self._retired:
            raise RetiredVersionError(
                message=f"API version '{requested}' has been retired",
                details={"retired": requested, "supported": self.supported_versions},
            )
        if requested not in self._transforms:
            raise UnsupportedVersionError(
                message=f"API version '{requested}' is not supported",
                details={"requested": requested, "supported": self.supported_versions},
            )
        return requested

    def transform(self, version: str, canonical: dict[str, Any]) -> dict[str, Any]:
        """Downgrade a canonical representation for the given version."""
        return self._transforms[version](canonical)

    def to_canonical(self, version: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Upgrade a versioned write payload to the canonical shape."""
        if version == "v1":
            return {
                "item": payload["item"],
                "total_cents": round(payload["total"] * 100),
                "currency": "USD",
            }
        return {
            "item": payload["item"],
            "total_cents": payload["total_cents"],
            "currency": payload.get("currency", "USD"),
        }
