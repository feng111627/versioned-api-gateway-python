"""按调用方（``X-Caller-Id``）的固定窗口限流。

* 每个调用方独立计数，互不干扰；
* 每个窗口 ``limit`` 个请求，窗口结束自动重置；
* 被限请求返回 429 + ``rate_limited``，并通过标准限流响应头反映额度：
  ``X-RateLimit-Limit`` / ``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``
  以及 ``Retry-After``。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_at: int  # unix 时间戳（秒）

    @property
    def retry_after(self) -> int:
        """距窗口重置的整秒数，至少 1 秒。"""
        return max(1, int(self.reset_at - time.time()))

    def headers(self) -> dict[str, str]:
        """标准 + 自定义限流响应头。"""
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_at),
            "Retry-After": str(self.retry_after),
        }


@dataclass
class _Window:
    started_at: float
    count: int = 0


class FixedWindowRateLimiter:
    def __init__(self, limit: int = 10, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()

    def check(self, caller: str, now: float | None = None) -> RateLimitResult:
        """检查并消耗一个配额；窗口过期则滚动到新窗口。"""
        current = time.time() if now is None else now
        with self._lock:
            window = self._windows.get(caller)
            if window is None or current - window.started_at >= self.window_seconds:
                window = _Window(started_at=current)
                self._windows[caller] = window
            reset_at = int(window.started_at + self.window_seconds)

            if window.count >= self.limit:
                return RateLimitResult(
                    allowed=False,
                    limit=self.limit,
                    remaining=0,
                    reset_at=reset_at,
                )

            window.count += 1
            return RateLimitResult(
                allowed=True,
                limit=self.limit,
                remaining=self.limit - window.count,
                reset_at=reset_at,
            )

    def reset(self) -> None:
        """清空所有计数（测试辅助）。"""
        with self._lock:
            self._windows.clear()
