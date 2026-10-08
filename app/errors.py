"""统一错误结构与错误码定义。

对外错误响应体固定为：

    {
        "error": {
            "code": "machine_readable_code",
            "message": "human readable message",
            "details": {...}
        }
    }

所有错误都能通过 HTTP 状态码 + ``error.code`` 被程序化区分，
内部堆栈 / 第三方库异常不会透出给调用方。
"""

from __future__ import annotations

from typing import Any

# ---- 错误码常量 -----------------------------------------------------------

CODE_INTERNAL = "internal_error"
CODE_INVALID_REQUEST = "invalid_request"
CODE_VERSION_UNKNOWN = "version_unknown"
CODE_VERSION_RETIRED = "version_retired"
CODE_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
CODE_IDEMPOTENCY_REPLAY_INFLIGHT = "idempotency_inflight"
CODE_RATE_LIMITED = "rate_limited"
CODE_UPSTREAM_UNAVAILABLE = "upstream_unavailable"


def error_body(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造统一错误响应体。"""
    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    return {"error": body}


class GatewayError(Exception):
    """网关层所有对外可见错误的基类。"""

    status_code: int = 500
    code: str = CODE_INTERNAL

    def __init__(
        self,
        message: str = "",
        details: dict[str, Any] | None = None,
        *,
        status_code: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.message = message or "internal server error"
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.headers = headers

    def to_response(self) -> tuple[dict[str, Any], int, dict[str, str]]:
        """返回 (响应体, 状态码, 响应头)。"""
        headers = {"Content-Type": "application/json"}
        if self.headers:
            headers.update(self.headers)
        return error_body(self.code, self.message, self.details), self.status_code, headers


class VersionError(GatewayError):
    """版本协商失败：未知或已下线版本，426 Upgrade Required。"""

    status_code = 426

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details, status_code=426, code=code)


class IdempotencyConflictError(GatewayError):
    """同一个幂等键携带了不同的请求内容。"""

    status_code = 409
    code = CODE_IDEMPOTENCY_CONFLICT


class IdempotencyInflightError(GatewayError):
    """同一幂等键的首次请求仍在处理中，且本次内容不同（并发冲突）。

    对相同内容的并发请求则等待并重放，不会产生此错误。
    """

    status_code = 409
    code = CODE_IDEMPOTENCY_REPLAY_INFLIGHT

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, details, status_code=409,
                         code=CODE_IDEMPOTENCY_REPLAY_INFLIGHT)


class RateLimitExceededError(GatewayError):
    """调用方超出配额。"""

    status_code = 429
    code = CODE_RATE_LIMITED

    def __init__(self, message: str, headers: dict[str, str]) -> None:
        super().__init__(
            message,
            status_code=429,
            code=CODE_RATE_LIMITED,
            headers=headers,
        )


class UpstreamUnavailableError(GatewayError):
    """上游依赖不可用（对外结构稳定，不透出内部异常）。"""

    status_code = 503
    code = CODE_UPSTREAM_UNAVAILABLE
