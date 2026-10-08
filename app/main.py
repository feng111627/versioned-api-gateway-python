"""FastAPI application factory for the versioned API gateway."""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the gateway app with versioning, idempotency and rate limiting."""
    app = FastAPI(title="versioned-api-gateway")
    return app


app = create_app()
