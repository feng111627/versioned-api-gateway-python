"""请求/响应模型与按版本的字段序列化。

同一份内部订单数据，在不同 API 版本下结构与语义不同：

v1 (2024-01-01) —— 老版本，保持冻结，不再改变：
    {
        "order_id": "ord-1",          # 字段名为 order_id
        "amount": 100,                # 金额单位：元（整数）
        "item_name": "book",          # 字段名为 item_name
        "currency": "CNY"
    }

v2 (2025-06-01) —— 当前版本：
    {
        "id": "ord-1",                # 字段名重命名为 id
        "amount": {"value": 10000, "currency": "CNY"},
                                      # 金额改为结构化的最小货币单位（分）
        "item": "book",
        "created_at": "2026-10-08T00:00:00Z"
    }
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .versioning import ApiVersion


class OrderCreateRequest(BaseModel):
    amount: int = Field(..., gt=0, description="金额，单位：分（>0）")
    item: str = Field(..., min_length=1, max_length=200)


def serialize_order(order: dict[str, Any], version: ApiVersion) -> dict[str, Any]:
    """按版本序列化订单。"""
    if version is ApiVersion.v1:
        # v1: 平铺结构；金额对外以"元"表示（100 分 -> 100 的演示取整：
        # 本系统金额本身就是分，v1 历史契约为"元整数"，这里按 100 换算，
        # 无法整除时按契约向下取整并仅在老版本保持该历史行为）
        return {
            "order_id": order["id"],
            "amount": order["amount"] // 100,
            "item_name": order["item"],
            "currency": order["currency"],
        }

    if version is ApiVersion.v2:
        return {
            "id": order["id"],
            "amount": {"value": order["amount"], "currency": order["currency"]},
            "item": order["item"],
            "created_at": order["created_at"],
        }

    raise ValueError(f"unsupported version for serialization: {version}")
