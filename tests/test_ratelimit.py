"""限流：额度内放行、超限 429、额度暴露、调用方隔离。"""
from __future__ import annotations

import pytest

from app.main import GatewayConfig
from app.ratelimit import RateLimiter


@pytest.fixture
def limited_client(make_client):
    # 小桶便于触达边界； refill 放慢避免测试期间回充
    return make_client(GatewayConfig(rate_limit_capacity=3, rate_limit_refill_per_second=0.001))


def test_within_limit_requests_pass_with_quota_headers(limited_client):
    for expected_remaining in (2, 1, 0):
        r = limited_client.get("/orders/ord_none")
        assert r.status_code == 404  # 资源不存在但请求被放行
        assert r.headers["X-RateLimit-Limit"] == "3"
        assert r.headers["X-RateLimit-Remaining"] == str(expected_remaining)


def test_over_limit_rejected_with_429_and_details(limited_client):
    for _ in range(3):
        limited_client.get("/orders/ord_none")
    r = limited_client.get("/orders/ord_none")
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "rate_limited"
    assert body["error"]["details"]["remaining"] == 0
    assert body["error"]["details"]["limit"] == 3
    assert body["error"]["details"]["retry_after_seconds"] > 0
    assert r.headers["X-RateLimit-Remaining"] == "0"
    assert "Retry-After" in r.headers


def test_callers_have_independent_buckets(limited_client):
    headers = {"X-Caller-Id": "caller-b"}
    for _ in range(3):
        limited_client.get("/orders/ord_none", headers=headers)
    assert limited_client.get("/orders/ord_none", headers=headers).status_code == 429
    # 默认匿名调用方仍有自己的额度
    assert limited_client.get("/orders/ord_none").status_code == 404


def test_healthz_is_exempt_from_rate_limit(limited_client):
    for _ in range(10):
        assert limited_client.get("/healthz").status_code == 200


def test_bucket_refills_over_time():
    """单元级：令牌随时间回充，边界值精确。"""
    now = [0.0]
    limiter = RateLimiter(capacity=2, refill_per_second=2.0, clock=lambda: now[0])
    assert limiter.check("c").allowed
    assert limiter.check("c").allowed
    denied = limiter.check("c")
    assert not denied.allowed
    assert denied.retry_after_seconds == pytest.approx(0.5)
    now[0] = 0.5  # 回充 1 个令牌
    assert limiter.check("c").allowed
    denied_again = limiter.check("c")
    assert not denied_again.allowed
