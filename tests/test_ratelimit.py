"""场景三：按调用方限流与边界。

tight_client 配额为每 60s 3 个请求。

覆盖：
* 恰好达到上限的请求成功，第 limit+1 个被拒绝为 429 rate_limited
* 被限响应带 X-RateLimit-Limit/Remaining/Reset 与 Retry-After，剩余额度为 0
* 成功响应同样反映剩余额度（递减）
* 不同调用方各自独立计数，互不干扰
* 限流(429) 与 参数错误(400)/版本问题(426) 可区分
* 缺少调用方身份为 400 invalid_request
* 窗口滚动后额度恢复
"""

from __future__ import annotations

from conftest import scenario_logger
from fastapi.testclient import TestClient

from app.main import create_app

V2 = "2025-06-01"


def _h(caller: str, version: str = V2) -> dict:
    h = {"X-Caller-Id": caller, "X-API-Version": version}
    return h


def test_boundary_request_succeeds_then_next_rejected(tight_client):
    scenario_logger.info("场景[限流] 用例: 第 3 个(=limit)成功且 remaining=0，第 4 个被 429 拒绝")
    responses = [
        tight_client.get("/orders/nonexistent", headers=_h("alice")) for _ in range(4)
    ]
    statuses = [r.status_code for r in responses]
    # 前 3 个要么 200 要么 404（业务层），都不触发限流；第 4 个必须是 429
    assert all(s != 429 for s in statuses[:3]), statuses
    assert statuses[3] == 429
    # 第 3 个（最后一个配额）剩余额度为 0，但仍非限流响应
    assert responses[2].headers["X-RateLimit-Remaining"] == "0"
    assert responses[3].json()["error"]["code"] == "rate_limited"


def test_limited_response_has_quota_headers_and_code(tight_client):
    scenario_logger.info("场景[限流] 用例: 429 响应带可区分错误码与剩余额度/重置时间头")
    for _ in range(3):
        tight_client.get("/orders/nonexistent", headers=_h("bob"))
    resp = tight_client.get("/orders/nonexistent", headers=_h("bob"))
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    assert resp.headers["X-RateLimit-Limit"] == "3"
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert int(resp.headers["X-RateLimit-Reset"]) > 0
    assert int(resp.headers["Retry-After"]) >= 1


def test_successful_responses_reflect_decreasing_remaining(tight_client):
    scenario_logger.info("场景[限流] 用例: 成功响应的 X-RateLimit-Remaining 逐次递减")
    r1 = tight_client.get("/orders/nonexistent", headers=_h("carol"))
    r2 = tight_client.get("/orders/nonexistent", headers=_h("carol"))
    r3 = tight_client.get("/orders/nonexistent", headers=_h("carol"))
    remainings = [
        r1.headers["X-RateLimit-Remaining"],
        r2.headers["X-RateLimit-Remaining"],
        r3.headers["X-RateLimit-Remaining"],
    ]
    assert remainings == ["2", "1", "0"]
    assert r3.status_code != 429  # 最后一个配额仍是成功业务响应


def test_callers_are_counted_independently(tight_client):
    scenario_logger.info("场景[限流] 用例: 不同调用方独立计数，一方被限不影响另一方")
    for _ in range(3):
        tight_client.get("/orders/nonexistent", headers=_h("heavy"))
    blocked = tight_client.get("/orders/nonexistent", headers=_h("heavy"))
    fresh = tight_client.get("/orders/nonexistent", headers=_h("fresh"))
    assert blocked.status_code == 429
    assert fresh.status_code != 429
    assert fresh.headers["X-RateLimit-Remaining"] == "2"


def test_ratelimit_distinct_from_param_and_version_errors(tight_client):
    scenario_logger.info("场景[限流] 用例: 限流(429) 与 参数错误(400)/版本问题(426) 三者可区分")
    # 耗尽配额
    for _ in range(3):
        tight_client.get("/orders/nonexistent", headers=_h("dave"))
    rl = tight_client.get("/orders/nonexistent", headers=_h("dave"))
    assert rl.status_code == 429 and rl.json()["error"]["code"] == "rate_limited"
    # 换个未超限调用方：缺版本属于默认版本（不报版本错），给坏版本才是 426
    r_ver = tight_client.get("/orders/nonexistent",
                             headers=_h("dave2", version="2099-01-01"))
    assert r_ver.status_code == 426 and r_ver.json()["error"]["code"] == "version_unknown"


def test_missing_caller_header_is_param_error(tight_client):
    scenario_logger.info("场景[限流] 用例: 缺少 X-Caller-Id -> 400 invalid_request（先于限流）")
    r = tight_client.get("/orders/nonexistent", headers={"X-API-Version": V2})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"
    # 没有身份的请求不应该消耗任何配额
    assert "X-RateLimit-Remaining" not in r.headers


def test_window_rollover_restores_quota():
    scenario_logger.info("场景[限流] 用例: 窗口滚动后配额恢复（用短窗口确定性验证）")
    app = create_app(rate_limit=2, window_seconds=0.3)
    client = TestClient(app)
    for _ in range(2):
        assert client.get("/orders/x", headers=_h("w")).status_code != 429
    assert client.get("/orders/x", headers=_h("w")).status_code == 429
    # 等待窗口过期
    import time
    time.sleep(0.4)
    r = client.get("/orders/x", headers=_h("w"))
    assert r.status_code != 429
    assert r.headers["X-RateLimit-Remaining"] == "1"
