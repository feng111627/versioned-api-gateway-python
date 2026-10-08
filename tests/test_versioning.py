"""场景一：接口版本协商与版本兼容。

覆盖：
* 显式 v1 返回冻结的老字段结构
* 显式 v2 返回新结构（金额结构化、字段重命名）
* 缺省请求头走默认版本（v2），老客户端显式钉住 v1 不受影响
* 未知版本 -> 426 version_unknown
* 已下线版本 -> 426 version_retired（与未知区分）
* 错误类型可区分：版本问题(426) / 参数错误(400) / 上游不可用(503)
* 响应头始终回写实际生效版本
"""

from __future__ import annotations

import logging

import pytest
from conftest import scenario_logger

V1 = "2024-01-01"
V2 = "2025-06-01"

HEADERS = {"X-Caller-Id": "caller-version", "Idempotency-Key": "seed-key"}

scenario_logger.setLevel(logging.INFO)


def _create_order(client, version: str | None, key: str = "k"):
    headers = dict(HEADERS)
    headers["Idempotency-Key"] = key
    if version is not None:
        headers["X-API-Version"] = version
    return client.post("/orders", json={"amount": 10000, "item": "book"}, headers=headers)


def test_v1_returns_legacy_shape(client):
    scenario_logger.info("场景[版本兼容] 用例: 显式 v1 应返回老结构(order_id/amount元/item_name)")
    resp = _create_order(client, V1, key="v1-key")
    assert resp.status_code == 201
    assert resp.headers["x-api-version"] == V1
    body = resp.json()
    assert set(body.keys()) == {"order_id", "amount", "item_name", "currency"}
    assert body["order_id"].startswith("ord-")
    assert body["amount"] == 100  # 10000 分 -> 100 元（历史契约）
    assert body["item_name"] == "book"


def test_v2_returns_new_shape(client):
    scenario_logger.info("场景[版本兼容] 用例: 显式 v2 应返回新结构(id/结构化金额/item/created_at)")
    resp = _create_order(client, V2, key="v2-key")
    assert resp.status_code == 201
    assert resp.headers["x-api-version"] == V2
    body = resp.json()
    assert set(body.keys()) == {"id", "amount", "item", "created_at"}
    assert body["amount"] == {"value": 10000, "currency": "CNY"}
    # 老版本字段绝不能出现在新版本
    assert "order_id" not in body and "item_name" not in body


def test_default_version_is_v2_and_v1_is_pinned(client):
    scenario_logger.info("场景[版本兼容] 用例: 缺省版本走 v2；显式 v1 的老客户端不被新版本影响")
    # 不显式指定 -> 默认 v2
    resp_default = _create_order(client, None, key="default-key")
    assert resp_default.headers["x-api-version"] == V2
    assert "id" in resp_default.json()
    # 同一批数据里，显式 v1 的老客户端依旧拿到老结构
    resp_old = _create_order(client, V1, key="pinned-v1-key")
    assert resp_old.headers["x-api-version"] == V1
    assert "order_id" in resp_old.json()


def test_unknown_version_is_rejected_with_426(client):
    scenario_logger.info("场景[版本兼容] 用例: 未知版本 -> 426 version_unknown，且给出 supported 列表")
    headers = dict(HEADERS)
    headers["X-API-Version"] = "2099-01-01"
    resp = client.post("/orders", json={"amount": 100, "item": "x"}, headers=headers)
    assert resp.status_code == 426
    err = resp.json()["error"]
    assert err["code"] == "version_unknown"
    assert err["details"]["supported"] == [V1, V2]
    assert resp.headers["x-api-version"] == "2099-01-01"


def test_retired_version_is_distinct_from_unknown(client):
    scenario_logger.info("场景[版本兼容] 用例: 已下线版本 -> 426 version_retired（与 unknown 区分）")
    headers = dict(HEADERS)
    headers["X-API-Version"] = "2023-01-01"
    resp = client.post("/orders", json={"amount": 100, "item": "x"}, headers=headers)
    assert resp.status_code == 426
    err = resp.json()["error"]
    assert err["code"] == "version_retired"
    assert "retired_reason" in err["details"]


@pytest.mark.parametrize(
    "version,expected_code",
    [
        ("2099-01-01", "version_unknown"),
        ("2023-01-01", "version_retired"),
    ],
    ids=["未知版本", "已下线版本"],
)
def test_version_errors_distinct_from_param_and_upstream(client, version, expected_code):
    scenario_logger.info("场景[错误区分] 用例: 版本错误(426) 与 参数错误(400)/上游不可用(503) 可区分 - %s", version)
    # 版本问题：426
    headers = dict(HEADERS)
    headers["X-API-Version"] = version
    resp_ver = client.post("/orders", json={"amount": 100, "item": "x"}, headers=headers)
    assert resp_ver.status_code == 426
    assert resp_ver.json()["error"]["code"] == expected_code

    # 参数错误：400 invalid_request（合法版本 + 非法金额）
    headers_ok = dict(HEADERS)
    headers_ok["X-API-Version"] = V2
    resp_bad = client.post("/orders", json={"amount": -5, "item": "x"}, headers=headers_ok)
    assert resp_bad.status_code == 400
    assert resp_bad.json()["error"]["code"] == "invalid_request"

    # 上游不可用：503 upstream_unavailable（合法版本 + 哨兵商品）
    headers_ok["Idempotency-Key"] = "upstream-fail-key"
    resp_up = client.post(
        "/orders", json={"amount": 100, "item": "force-upstream-failure"},
        headers=headers_ok,
    )
    assert resp_up.status_code == 503
    assert resp_up.json()["error"]["code"] == "upstream_unavailable"


def test_get_order_also_versioned(client):
    scenario_logger.info("场景[版本兼容] 用例: GET 同一订单在 v1/v2 下结构不同")
    created = _create_order(client, V2, key="get-versioned")
    order_id = created.json()["id"]
    r2 = client.get(f"/orders/{order_id}", headers={"X-Caller-Id": "c", "X-API-Version": V2})
    r1 = client.get(f"/orders/{order_id}", headers={"X-Caller-Id": "c", "X-API-Version": V1})
    assert r2.json()["id"] == order_id and "amount" in r2.json()
    assert r1.json()["order_id"] == order_id and r1.json()["amount"] == 100
