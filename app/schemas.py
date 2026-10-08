"""Pydantic schemas for each supported API version.

Skeleton: model definitions only.
"""
from __future__ import annotations

from pydantic import BaseModel


class OrderCreateV1(BaseModel):
    """v1 write model: float ``total`` in major currency units."""

    item: str
    total: float


class OrderCreateV2(BaseModel):
    """v2 write model: integer minor units plus explicit currency."""

    item: str
    total_cents: int
    currency: str = "USD"
