"""Unified error envelope and gateway exception types.

Skeleton: type definitions only, no behavior yet.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    """Stable, machine-readable error detail."""

    code: str
    message: str
    details: dict[str, Any] = {}


class ErrorEnvelope(BaseModel):
    """Top-level error response shape returned to clients."""

    error: ErrorBody
    request_id: str


class GatewayError(Exception):
    """Base class for all errors intentionally raised by the gateway."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.message = message or self.code
        self.details = details or {}
        super().__init__(self.message)


def register_exception_handlers(app: Any) -> None:
    """Attach exception handlers to the FastAPI app. (stub)"""
    raise NotImplementedError
