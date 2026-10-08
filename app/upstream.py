"""模拟上游依赖：演示内部/上游异常如何被网关统一处理。

注意：这里抛出的异常代表"第三方库风格"的内部异常，
网关必须把它转换成稳定的对外错误结构，禁止原样透出。
"""

from __future__ import annotations

import itertools
import threading
import time


class UpstreamUnavailableError(RuntimeError):
    """上游服务不可用（模拟第三方库异常，禁止透传给客户端）。"""


# 触发上游故障的哨兵商品名（测试用，确定性触发而非随机）
FAILURE_TRIGGER_ITEM = "force-upstream-failure"


class UpstreamService:
    """一个会偶发抛异常的模拟上游订单服务。"""

    def __init__(self, latency: float = 0.0) -> None:
        self._counter = itertools.count(1)
        self._lock = threading.Lock()
        self._calls = 0
        self._latency = latency

    @property
    def call_count(self) -> int:
        """create_order 被真正调用的次数（测试断言幂等生效一次用）。"""
        # itertools.count 没有公开当前值，单独维护
        return self._calls

    def create_order(self, amount: int, item: str) -> dict:
        """真正执行下单；item 为哨兵值时模拟上游崩溃。"""
        if item == FAILURE_TRIGGER_ITEM:
            # 模拟第三方库抛出的、不适合透传的异常
            raise UpstreamUnavailableError(
                "upstream order-center connection reset (internal detail)"
            )
        if self._latency:
            time.sleep(self._latency)
        with self._lock:
            self._calls += 1
            order_id = f"ord-{next(self._counter)}"
        return {
            "id": order_id,
            "amount": amount,
            "item": item,
            "currency": "CNY",
            "created_at": "2026-10-08T00:00:00Z",
        }
