"""FastAPI 应用入口：统一接口网关。

装配四大能力：
1. 版本协商中间件（X-API-Version）
2. 按调用方限流依赖（X-Caller-Id）
3. 写操作幂等（Idempotency-Key）
4. 统一错误结构（任何异常都不透出内部细节）
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import (
    CODE_INTERNAL,
    CODE_INVALID_REQUEST,
    GatewayError,
    IdempotencyConflictError,
    IdempotencyInflightError,
    RateLimitExceededError,
    error_body,
)
from .errors import (
    UpstreamUnavailableError as GatewayUpstreamUnavailableError,
)
from .idempotency import IdempotencyStore
from .ratelimit import FixedWindowRateLimiter
from .schemas import OrderCreateRequest, serialize_order
from .upstream import UpstreamService
from .upstream import UpstreamUnavailableError as InternalUpstreamError
from .versioning import VERSION_HEADER, VersionError, negotiate_version

logger = logging.getLogger("api_gateway")

CALLER_HEADER = "x-caller-id"
IDEMPOTENCY_HEADER = "idempotency-key"
REPLAY_HEADER = "idempotency-replayed"

CODE_NOT_FOUND = "not_found"


def create_app(
    rate_limit: int = 100,
    window_seconds: float = 60.0,
    upstream_latency: float | None = None,
) -> FastAPI:
    """构造网关应用（测试可注入小配额/短窗口/确定性延迟）。"""
    app = FastAPI(title="Versioned API Gateway", version="1.0.0")

    app.state.idempotency = IdempotencyStore()
    app.state.limiter = FixedWindowRateLimiter(limit=rate_limit, window_seconds=window_seconds)
    latency = (
        float(os.environ.get("GATEWAY_UPSTREAM_LATENCY", "0"))
        if upstream_latency is None
        else upstream_latency
    )
    app.state.upstream = UpstreamService(latency=latency)
    app.state.orders: dict[str, dict[str, Any]] = {}
    app.state.orders_lock = threading.Lock()

    # ---- 版本协商中间件 ---------------------------------------------------
    @app.middleware("http")
    async def version_middleware(request: Request, call_next):
        requested = request.headers.get(VERSION_HEADER)
        result = None
        try:
            result = negotiate_version(requested)
            request.state.version_result = result
            response = await call_next(request)
        except VersionError as exc:
            body, status, headers = exc.to_response()
            response = JSONResponse(body, status_code=status, headers=headers)
        # 成功路径回写实际生效版本；拒绝路径回写客户端请求值，便于排查
        active = result.version.value if result is not None else (requested or "")
        response.headers[VERSION_HEADER.title()] = active
        # 合并限流额度头（限流依赖执行过才有；参数错误等更早退的路径没有）
        rate_result = getattr(request.state, "rate_limit_result", None)
        if rate_result is not None:
            for name, value in rate_result.headers().items():
                response.headers[name] = value
        return response

    # ---- 统一异常处理 -----------------------------------------------------
    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError):
        body, status, headers = exc.to_response()
        logger.warning("gateway error: code=%s status=%s path=%s",
                       exc.code, status, request.url.path)
        return JSONResponse(body, status_code=status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        # 参数错误：400 invalid_request，与版本问题(426)/限流(429)明确区分
        return JSONResponse(
            error_body(CODE_INVALID_REQUEST, "request validation failed",
                       {"errors": exc.errors()}),
            status_code=400,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        code = CODE_NOT_FOUND if exc.status_code == 404 else CODE_INVALID_REQUEST
        return JSONResponse(
            error_body(code, str(exc.detail)),
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        # 内部异常：记录完整堆栈到服务端日志，对外只给稳定结构
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            error_body(CODE_INTERNAL, "internal server error"),
            status_code=500,
        )

    # ---- 限流依赖 ---------------------------------------------------------
    def rate_limited(request: Request) -> str:
        caller = request.headers.get(CALLER_HEADER)
        if not caller or not caller.strip():
            raise GatewayError(
                f"missing required header: {CALLER_HEADER}",
                status_code=400,
                code=CODE_INVALID_REQUEST,
            )
        caller = caller.strip()
        result = request.app.state.limiter.check(caller)
        # 结果暂存到 request.state，由中间件统一合并进最终响应头，
        # 无论端点返回 dict 还是 JSONResponse、甚至抛限流异常都能带上额度头
        request.state.rate_limit_result = result
        if not result.allowed:
            raise RateLimitExceededError(
                f"rate limit exceeded for caller {caller}",
                result.headers(),
            )
        return caller

    # ---- 路由 -------------------------------------------------------------
    @app.get("/")
    def read_root() -> dict[str, str]:
        return {"service": "versioned-api-gateway", "status": "ok"}

    @app.post("/orders", status_code=201)
    def create_order(
        request: Request,
        payload: OrderCreateRequest,
        _caller: str = Depends(rate_limited),
    ) -> JSONResponse:
        result = request.state.version_result
        version = result.version

        idem_key = request.headers.get(IDEMPOTENCY_HEADER)
        if not idem_key or not idem_key.strip():
            raise GatewayError(
                f"missing required header: {IDEMPOTENCY_HEADER}",
                status_code=400,
                code=CODE_INVALID_REQUEST,
            )
        scoped_key = f"POST /orders:{idem_key.strip()}"
        payload_hash = IdempotencyStore.hash_payload(payload.model_dump())

        store: IdempotencyStore = request.app.state.idempotency
        lease = store.acquire(scoped_key, payload_hash)
        try:
            # 1) 本次请求在首次执行仍持锁时到达（在途并发）：
            #    同内容 -> 等待后拿到最终结果（下面统一重放）；
            #    不同内容 -> idempotency_inflight
            if lease.waited and not lease.payload_matches_first:
                raise IdempotencyInflightError(
                    "a request with the same idempotency key is already in progress "
                    "but carries a different payload",
                    details={"key": idem_key.strip()},
                )

            # 2) 首次已完成：同内容重放首次响应
            if lease.completed:
                if not lease.payload_matches_first:
                    # 首次早已结束、锁空闲时带着不同内容到达 -> 事后冲突
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different request payload",
                        details={"key": idem_key.strip()},
                    )
                return JSONResponse(
                    lease.record.response,
                    status_code=lease.record.status_code,
                    headers={REPLAY_HEADER: "true"},
                )

            # 3) 走到这里：本次是首次执行（内容哈希即占位哈希）
            try:
                order = request.app.state.upstream.create_order(
                    amount=payload.amount, item=payload.item
                )
            except InternalUpstreamError as exc:
                # 失败结果不缓存，允许客户端用同键重试；删除占位记录
                store.discard(scoped_key)
                logger.warning("upstream unavailable: %s", exc)
                raise GatewayUpstreamUnavailableError(
                    "upstream dependency is temporarily unavailable",
                    details={"retryable": True},
                )
            except Exception:
                store.discard(scoped_key)
                logger.exception("unexpected error calling upstream")
                raise GatewayError(
                    "internal server error",
                    status_code=500,
                    code=CODE_INTERNAL,
                )

            with request.app.state.orders_lock:
                request.app.state.orders[order["id"]] = order

            body = serialize_order(order, version)
            lease.mark_completed(body, 201)
            return JSONResponse(body, status_code=201, headers={REPLAY_HEADER: "false"})
        finally:
            lease.release()

    @app.get("/orders/{order_id}", status_code=200)
    def get_order(
        order_id: str,
        request: Request,
        _caller: str = Depends(rate_limited),
    ) -> dict[str, Any]:
        version = request.state.version_result.version
        order = request.app.state.orders.get(order_id)
        if order is None:
            raise GatewayError(
                f"order not found: {order_id}",
                status_code=404,
                code=CODE_NOT_FOUND,
            )
        return serialize_order(order, version)

    return app


app = create_app(
    rate_limit=int(os.environ.get("GATEWAY_RATE_LIMIT", "100")),
    window_seconds=float(os.environ.get("GATEWAY_RATE_WINDOW", "60")),
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
