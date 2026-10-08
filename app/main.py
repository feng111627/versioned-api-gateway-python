"""FastAPI application factory for the versioned API gateway.

Wires together: request-id assignment, per-caller rate limiting,
version negotiation, idempotent writes and the unified error envelope.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import RateLimitedError, register_exception_handlers
from app.idempotency import IdempotencyStore
from app.ratelimit import (
    CALLER_HEADER,
    DEFAULT_CALLER,
    LIMIT_HEADER,
    REMAINING_HEADER,
    RETRY_AFTER_HEADER,
    RateLimiter,
)
from app.routes import router
from app.store import OrderStore
from app.versioning import VersionRegistry

logger = logging.getLogger("gateway")

#: Paths exempt from rate limiting (operational endpoints).
_RATE_LIMIT_EXEMPT = {"/healthz"}


@dataclass(frozen=True)
class GatewayConfig:
    """Tunable gateway behavior (kept small so tests can override it)."""

    rate_limit_capacity: int = 20
    rate_limit_refill_per_second: float = 5.0


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Build the gateway app with versioning, idempotency and rate limiting."""
    config = config or GatewayConfig()
    app = FastAPI(title="versioned-api-gateway")

    app.state.versions = VersionRegistry()
    app.state.orders = OrderStore()
    app.state.idempotency = IdempotencyStore()
    app.state.rate_limiter = RateLimiter(
        capacity=config.rate_limit_capacity,
        refill_per_second=config.rate_limit_refill_per_second,
    )

    register_exception_handlers(app)

    @app.middleware("http")
    async def request_id_and_rate_limit(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or str(
            uuid.uuid4()
        )

        if request.url.path not in _RATE_LIMIT_EXEMPT:
            caller = request.headers.get(CALLER_HEADER, DEFAULT_CALLER)
            decision = app.state.rate_limiter.check(caller)
            limit_headers = {
                LIMIT_HEADER: str(decision.limit),
                REMAINING_HEADER: str(decision.remaining),
            }
            if not decision.allowed:
                err = RateLimitedError(
                    message="rate limit exceeded",
                    details={
                        "caller": caller,
                        "limit": decision.limit,
                        "remaining": 0,
                        "retry_after_seconds": decision.retry_after_seconds,
                    },
                )
                logger.info(
                    "rate_limited caller=%s request_id=%s",
                    caller,
                    request.state.request_id,
                )
                response = err.to_response(request.state.request_id)
                response.headers.update(limit_headers)
                response.headers[RETRY_AFTER_HEADER] = str(
                    decision.retry_after_seconds
                )
                return response
            response = await call_next(request)
            response.headers.update(limit_headers)
        else:
            response = await call_next(request)

        response.headers["X-Request-Id"] = request.state.request_id
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)
    return app


app = create_app()
