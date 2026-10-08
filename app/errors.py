"""Unified error envelope and gateway exception types.

Every error that leaves the gateway uses the same envelope::

    {"error": {"code": str, "message": str, "details": {...}},
     "request_id": str}

Internal tracebacks and third-party exception types are never leaked:
unexpected exceptions are logged server-side and surfaced as a generic
``internal_error``.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("gateway.errors")


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
    """Base class for errors intentionally raised by the gateway."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str = "", details: dict[str, Any] | None = None) -> None:
        self.message = message or self.code
        self.details = details or {}
        super().__init__(self.message)

    # -- response shaping -------------------------------------------------
    def to_envelope(self, request_id: str) -> ErrorEnvelope:
        return ErrorEnvelope(
            error=ErrorBody(code=self.code, message=self.message, details=self.details),
            request_id=request_id,
        )

    def to_response(self, request_id: str, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse(
            status_code=self.status_code,
            content=self.to_envelope(request_id).model_dump(),
            headers=headers,
        )


# ---------------------------------------------------------------------------
# Concrete gateway errors. Each maps to a distinct HTTP status + stable code
# so clients can tell version problems, parameter problems, conflicts,
# rate limiting and dependency failures apart.
# ---------------------------------------------------------------------------


class InvalidRequestError(GatewayError):
    """Malformed parameters / failed validation."""

    status_code = 400
    code = "invalid_request"


class UnsupportedVersionError(GatewayError):
    """The requested API version does not exist."""

    status_code = 426  # Upgrade Required: clearly distinct from 400/503
    code = "unsupported_version"


class RetiredVersionError(GatewayError):
    """The requested API version existed but has been retired."""

    status_code = 410  # Gone
    code = "version_retired"


class NotFoundError(GatewayError):
    """The target resource does not exist."""

    status_code = 404
    code = "not_found"


class IdempotencyConflictError(GatewayError):
    """Same idempotency key was reused with a different payload."""

    status_code = 409
    code = "idempotency_conflict"


class RateLimitedError(GatewayError):
    """Caller exceeded their request budget."""

    status_code = 429
    code = "rate_limited"


class UpstreamError(GatewayError):
    """An internal/upstream dependency failed."""

    status_code = 502
    code = "upstream_error"


class ServiceUnavailableError(GatewayError):
    """The service is temporarily unable to serve requests."""

    status_code = 503
    code = "service_unavailable"


def request_id_of(request: Request) -> str:
    """Return the request id assigned by middleware (or a fresh one)."""
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers so every error leaves as an ``ErrorEnvelope``."""

    @app.exception_handler(GatewayError)
    async def handle_gateway_error(request: Request, exc: GatewayError) -> JSONResponse:
        headers = getattr(exc, "headers", None)
        logger.info(
            "gateway_error code=%s status=%s request_id=%s",
            exc.code,
            exc.status_code,
            request_id_of(request),
        )
        return exc.to_response(request_id_of(request), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        err = InvalidRequestError(
            message="request parameters failed validation",
            details={"errors": exc.errors()},
        )
        return err.to_response(request_id_of(request))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Log the real exception server-side; never leak internals.
        logger.exception(
            "unhandled_exception request_id=%s", request_id_of(request)
        )
        err = GatewayError(message="an unexpected internal error occurred")
        return err.to_response(request_id_of(request))
