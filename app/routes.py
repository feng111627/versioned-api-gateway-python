"""HTTP routes for the gateway.

Skeleton: endpoint signatures only.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/orders/{order_id}")
async def get_order(order_id: str) -> dict:
    """Fetch one order in the negotiated API version. (stub)"""
    raise NotImplementedError


@router.post("/orders", status_code=201)
async def create_order() -> dict:
    """Create an order idempotently in the negotiated API version. (stub)"""
    raise NotImplementedError
