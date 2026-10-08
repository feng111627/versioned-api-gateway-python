"""HTTP routes for the gateway.

Every endpoint:
  1. negotiates the API version from ``X-API-Version``;
  2. (writes) runs under the idempotency layer when ``Idempotency-Key``
     is present;
  3. stores/reads the canonical representation and converts it to the
     negotiated version on the way out.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.errors import InvalidRequestError, NotFoundError
from app.idempotency import IDEMPOTENCY_KEY_HEADER, IDEMPOTENCY_REPLAYED_HEADER
from app.schemas import OrderCreateV1, OrderCreateV2
from app.versioning import VERSION_HEADER

router = APIRouter()

_WRITE_MODELS = {"v1": OrderCreateV1, "v2": OrderCreateV2}


def _negotiate(request: Request) -> str:
    return request.app.state.versions.negotiate(request.headers.get(VERSION_HEADER))


def _fingerprint(version: str, canonical: dict[str, Any]) -> str:
    raw = json.dumps(
        {"version": version, "payload": canonical}, sort_keys=True
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _versioned_response(
    request: Request, version: str, canonical: dict[str, Any], status_code: int = 200
) -> JSONResponse:
    body = request.app.state.versions.transform(version, canonical)
    return JSONResponse(
        status_code=status_code,
        content=body,
        headers={VERSION_HEADER: version},
    )


@router.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request) -> JSONResponse:
    """Fetch one order in the negotiated API version."""
    version = _negotiate(request)
    order = request.app.state.orders.get(order_id)
    if order is None:
        raise NotFoundError(
            message=f"order '{order_id}' does not exist",
            details={"order_id": order_id},
        )
    return _versioned_response(request, version, order)


@router.post("/orders")
async def create_order(request: Request) -> JSONResponse:
    """Create an order; idempotent when ``Idempotency-Key`` is provided."""
    version = _negotiate(request)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise InvalidRequestError(message="request body must be valid JSON")

    model = _WRITE_MODELS[version]
    try:
        parsed = model.model_validate(payload)
    except ValidationError as exc:
        raise InvalidRequestError(
            message="request body failed validation",
            details={"errors": exc.errors(include_url=False)},
        )

    canonical_in = request.app.state.versions.to_canonical(
        version, parsed.model_dump()
    )

    async def operation() -> tuple[int, dict[str, Any]]:
        created = request.app.state.orders.create(canonical_in)
        return 201, created

    key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
    if key:
        status, canonical_out, replayed = await request.app.state.idempotency.execute_once(
            key, _fingerprint(version, canonical_in), operation
        )
    else:
        status, canonical_out = await operation()
        replayed = False

    response = _versioned_response(request, version, canonical_out, status_code=status)
    if key and replayed:
        response.headers[IDEMPOTENCY_REPLAYED_HEADER] = "true"
    return response
