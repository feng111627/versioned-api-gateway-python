"""幂等键存储与并发保护。

语义：
* 同一个幂等键的重复提交（包括同时到达）只有一次真正执行业务，
  其余请求拿到与首次完全一致的响应；
* 同一个键携带不同请求内容 -> 409（在途并发为 ``idempotency_inflight``，
  事后复提为 ``idempotency_conflict``）；
* 不同键之间互不干扰（每键一把锁）。

``acquire()`` 返回 :class:`IdempotencyLease`，其生命周期仅属于本次请求，
记录了本次请求是否在锁上等待（``waited``）与本次内容哈希（``request_hash``）；
共享状态（首请求内容哈希、最终响应、完成标志）放在 :class:`IdempotencyRecord`。
调用方必须在 finally 中调用 ``release()``。
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IdempotencyRecord:
    """某个幂等键的共享状态（所有同键请求共同可见）。"""

    key: str
    request_hash: str  # 首次占位请求的内容哈希
    response: dict[str, Any] | None = None
    status_code: int = 200
    completed: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class IdempotencyLease:
    """一次 acquire 的私有租约（不跨请求共享，避免并发互相覆盖）。"""

    record: IdempotencyRecord
    request_hash: str  # 本次请求的内容哈希
    waited: bool  # 拿到锁时首次请求是否仍在途（在锁上等待且记录尚未完成）

    @property
    def key(self) -> str:
        return self.record.key

    @property
    def payload_matches_first(self) -> bool:
        return self.record.request_hash == self.request_hash

    @property
    def completed(self) -> bool:
        return self.record.completed

    def mark_completed(self, response: dict[str, Any], status_code: int) -> None:
        self.record.response = response
        self.record.status_code = status_code
        self.record.completed = True

    def release(self) -> None:
        self.record.lock.release()


class IdempotencyStore:
    """进程内幂等存储（演示用；生产可替换为 Redis + NX EX 方案）。"""

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}
        self._registry_lock = threading.Lock()

    def acquire(self, key: str, request_hash: str) -> IdempotencyLease:
        """获取某幂等键的租约并持有其锁。

        记录尚不存在时以 ``request_hash`` 原子创建；并发创建只有一个胜出。
        非阻塞探测判断本次是否需要等待，等待即意味着首次请求仍在途。
        """
        with self._registry_lock:
            record = self._records.get(key)
            if record is None:
                record = IdempotencyRecord(key=key, request_hash=request_hash)
                self._records[key] = record
                record.lock.acquire()
                waited = False
            elif record.lock.acquire(blocking=False):
                # 立即拿到锁：首次请求已释放锁（已结束）
                waited = False
            else:
                # 非阻塞获取失败，说明此刻首次请求仍持锁（在途）；阻塞等待
                record.lock.acquire()
                waited = True
        return IdempotencyLease(record=record, request_hash=request_hash, waited=waited)

    def discard(self, key: str) -> None:
        """删除某个键的占位记录（业务执行失败时允许同键重试）。

        调用方应仍持有该记录的锁；删除后到达的同键请求会创建全新记录。
        """
        with self._registry_lock:
            self._records.pop(key, None)

    @staticmethod
    def hash_payload(payload: Any) -> str:
        """对请求内容做稳定哈希（键排序，保证等价内容哈希一致）。"""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def reset(self) -> None:
        """清空存储（测试辅助）。"""
        with self._registry_lock:
            self._records.clear()
