"""场景二：写操作幂等与并发保护。

覆盖：
* 同键同内容顺序重复提交 -> 只生效一次，响应完全一致，第二次标记为 replay
* 同键同内容"同时到达"（真线程并发）-> 只真正执行一次上游，二者拿到同一订单
* 同键不同内容 -> 409 idempotency_conflict
* 不同键互不干扰，各自创建独立订单
* 上游失败不缓存结果，允许用同一个幂等键重试并最终成功
* 缺少幂等键属于 400 参数错误
"""

from __future__ import annotations

import threading
import time

from conftest import scenario_logger

V2 = "2025-06-01"


def _headers(key: str, caller: str = "caller-idem") -> dict:
    return {
        "X-Caller-Id": caller,
        "X-API-Version": V2,
        "Idempotency-Key": key,
    }


def test_duplicate_submission_executes_once(client):
    scenario_logger.info("场景[幂等] 用例: 同键同内容顺序重复 -> 只生效一次且响应一致")
    payload = {"amount": 2500, "item": "keyboard"}
    r1 = client.post("/orders", json=payload, headers=_headers("dup-seq"))
    r2 = client.post("/orders", json=payload, headers=_headers("dup-seq"))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.headers["idempotency-replayed"] == "false"
    assert r2.headers["idempotency-replayed"] == "true"
    b1, b2 = r1.json(), r2.json()
    assert b1 == b2  # 完全一致的结果
    # 只真正调用了一次上游、只创建了一个订单
    assert client.app.state.upstream.call_count == 1
    assert b1["id"] in client.app.state.orders
    assert len(client.app.state.orders) == 1


def test_concurrent_same_key_single_effect(server):
    scenario_logger.info("场景[幂等] 用例: 同键并发(真实多请求重叠) -> 只生效一次，两者拿到同一订单")
    import httpx

    payload = {"amount": 3000, "item": "monitor"}
    results: list = []
    errors: list = []
    barrier = threading.Barrier(2)

    def fire():
        try:
            # 真实 uvicorn 服务器 + 屏障同时放行：两个请求在 0.5s 上游窗口内重叠
            barrier.wait()
            resp = httpx.post(
                server + "/orders", json=payload,
                headers=_headers("dup-race"), timeout=10,
            )
            results.append(
                (resp.status_code, resp.json(), resp.headers.get("idempotency-replayed"))
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=fire)
    t2 = threading.Thread(target=fire)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert not errors, errors
    assert len(results) == 2
    statuses = sorted(r[0] for r in results)
    assert statuses == [201, 201]
    ids = {r[1]["id"] for r in results}
    assert len(ids) == 1  # 同一订单
    replays = sorted(r[2] for r in results)
    assert replays == ["false", "true"]  # 一个首次、一个在途等待后重放


def test_same_key_different_payload_is_conflict(client):
    scenario_logger.info("场景[幂等] 用例: 同键不同内容 -> 409 idempotency_conflict")
    r1 = client.post("/orders", json={"amount": 100, "item": "a"},
                     headers=_headers("conflict-key"))
    assert r1.status_code == 201
    r2 = client.post("/orders", json={"amount": 999, "item": "different"},
                     headers=_headers("conflict-key"))
    assert r2.status_code == 409
    err = r2.json()["error"]
    assert err["code"] == "idempotency_conflict"
    assert err["details"]["key"] == "conflict-key"
    # 首次结果不受影响
    assert r1.json()["amount"]["value"] == 100


def test_different_keys_do_not_interfere(client):
    scenario_logger.info("场景[幂等] 用例: 不同键互不干扰，各自创建独立订单")
    r1 = client.post("/orders", json={"amount": 100, "item": "x"},
                     headers=_headers("key-A"))
    r2 = client.post("/orders", json={"amount": 200, "item": "y"},
                     headers=_headers("key-B"))
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]
    assert r1.json()["amount"]["value"] == 100
    assert r2.json()["amount"]["value"] == 200


def test_failure_is_not_cached_and_key_can_retry(client):
    scenario_logger.info("场景[幂等] 用例: 上游失败不占用幂等键，同键重试可成功")
    fail_headers = _headers("retry-key")
    r_fail = client.post("/orders", json={"amount": 100, "item": "force-upstream-failure"},
                         headers=fail_headers)
    assert r_fail.status_code == 503
    assert r_fail.json()["error"]["code"] == "upstream_unavailable"
    # 用同一个幂等键、换成正常内容重试 -> 成功
    r_ok = client.post("/orders", json={"amount": 100, "item": "mouse"},
                       headers=_headers("retry-key"))
    assert r_ok.status_code == 201
    assert r_ok.headers["idempotency-replayed"] == "false"


def test_missing_idempotency_key_is_param_error(client):
    scenario_logger.info("场景[幂等] 用例: 缺少幂等键 -> 400 invalid_request（与版本/限流区分）")
    headers = {"X-Caller-Id": "c", "X-API-Version": V2}
    r = client.post("/orders", json={"amount": 100, "item": "x"}, headers=headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_concurrent_same_key_different_payload_rejected(server):
    scenario_logger.info("场景[幂等] 用例: 同键并发但内容不同 -> 在途排队者 409 idempotency_inflight，首次结果不受影响")
    import httpx

    results: list = []
    errors: list = []

    def fire(payload):
        try:
            resp = httpx.post(
                server + "/orders", json=payload,
                headers=_headers("dup-race-diff"), timeout=10,
            )
            results.append((resp.status_code, resp.json()))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=fire, args=({"amount": 100, "item": "first"},))
    t2 = threading.Thread(target=fire, args=({"amount": 200, "item": "second"},))
    t1.start()
    time.sleep(0.2)  # 首个请求已进入 1.0s 上游执行窗口，再发出第二个
    t2.start()
    t1.join(); t2.join()
    assert not errors, errors
    statuses = sorted(code for code, _ in results)
    assert statuses == [201, 409], results
    conflict = next(body for code, body in results if code == 409)
    assert conflict["error"]["code"] == "idempotency_inflight"
    created = next(body for code, body in results if code == 201)
    # 保留的是首个请求的内容
    assert created["amount"]["value"] == 100
    # 随后用"首个内容"重放，应得到首次结果（证明首次结果完好、且只生效一次）
    replay = httpx.post(
        server + "/orders",
        json={"amount": 100, "item": "first"},
        headers=_headers("dup-race-diff"), timeout=10,
    )
    assert replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["id"] == created["id"]
