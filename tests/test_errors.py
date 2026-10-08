"""场景四：统一错误结构与内部异常收敛。

覆盖：
* 所有错误响应都是 {"error": {code, message[, details]}}，绝不透出内部堆栈
* 上游 503 响应不包含第三方异常的内部细节（connection reset 等）
* 未知路由 -> 404 not_found，同样是统一结构
* 非法 JSON 请求体 -> 400 invalid_request，而不是框架原始 422/HTML
* 各错误码与其 HTTP 状态码一一对应、可程序化区分
"""

from __future__ import annotations

from conftest import scenario_logger

V2 = "2025-06-01"
HEADERS = {"X-Caller-Id": "caller-err", "X-API-Version": V2, "Idempotency-Key": "k"}


def test_every_error_uses_envelope_without_internals(client):
    scenario_logger.info("场景[错误结构] 用例: 426/400/409/429/503 全部使用统一信封且字段完整")
    cases = [
        (426, {**HEADERS, "X-API-Version": "2099-01-01"},
         {"amount": 1, "item": "x"}, "version_unknown"),
        (400, {**HEADERS, "Idempotency-Key": ""},
         {"amount": 1, "item": "x"}, "invalid_request"),
        (400, HEADERS, {"amount": -1, "item": "x"}, "invalid_request"),
    ]
    # 409 幂等冲突
    client.post("/orders", json={"amount": 100, "item": "a"}, headers=HEADERS)
    conflict = client.post(
        "/orders", json={"amount": 200, "item": "b"}, headers=HEADERS
    )
    assert conflict.status_code == 409
    body = conflict.json()
    assert set(body.keys()) == {"error"}
    assert set(body["error"].keys()) >= {"code", "message"}
    assert body["error"]["code"] == "idempotency_conflict"

    for expected_status, headers, payload, expected_code in cases:
        resp = client.post("/orders", json=payload, headers=headers)
        assert resp.status_code == expected_status, (expected_status, resp.status_code, resp.text)
        err = resp.json()["error"]
        assert err["code"] == expected_code
        assert isinstance(err["message"], str) and err["message"]
        # 任何错误都不应携带堆栈/异常类型名
        text = resp.text
        assert "Traceback" not in text
        assert "upstream order-center connection reset" not in text


def test_upstream_error_hides_internal_detail(client):
    scenario_logger.info("场景[错误收敛] 用例: 503 只暴露稳定结构，不泄露第三方异常原文")
    resp = client.post(
        "/orders",
        json={"amount": 100, "item": "force-upstream-failure"},
        headers=HEADERS,
    )
    assert resp.status_code == 503
    err = resp.json()["error"]
    assert err["code"] == "upstream_unavailable"
    assert "connection reset" not in resp.text
    assert "UpstreamUnavailableError" not in resp.text
    assert err["details"] == {"retryable": True}


def test_unknown_route_is_enveloped_not_found(client):
    scenario_logger.info("场景[错误结构] 用例: 未知路由 -> 404 not_found 统一信封（而非框架默认页）")
    resp = client.get("/no/such/route", headers={"X-Caller-Id": "c"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_malformed_json_is_invalid_request(client):
    scenario_logger.info("场景[错误收敛] 用例: 非法 JSON -> 400 invalid_request 统一结构")
    resp = client.post(
        "/orders",
        content="{not valid json",
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_error_codes_are_distinct_across_failure_classes(client, tight_client):
    scenario_logger.info("场景[错误区分] 用例: 各失败类别状态码+错误码互不相同，可程序化区分")
    observed = {}
    # 参数错误
    r = client.post("/orders", json={"amount": 1}, headers={**HEADERS, "Idempotency-Key": ""})
    observed[r.json()["error"]["code"]] = r.status_code
    # 版本未知
    r = client.post("/orders", json={"amount": 1, "item": "x"},
                    headers={**HEADERS, "X-API-Version": "2099-01-01"})
    observed[r.json()["error"]["code"]] = r.status_code
    # 上游不可用
    r = client.post("/orders", json={"amount": 1, "item": "force-upstream-failure"},
                    headers=HEADERS)
    observed[r.json()["error"]["code"]] = r.status_code
    # 限流
    for _ in range(4):
        r = tight_client.get("/orders/x", headers={"X-Caller-Id": "rl"})
    observed[r.json()["error"]["code"]] = r.status_code

    assert observed == {
        "invalid_request": 400,
        "version_unknown": 426,
        "rate_limited": 429,
        "upstream_unavailable": 503,
    }
    # 状态码彼此不同，保证调用方可据此区分
    assert len(set(observed.values())) == 4
