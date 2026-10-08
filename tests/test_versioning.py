"""版本协商与兼容：默认版本、字段差异、未知/已下线版本拒绝。"""
from __future__ import annotations

V2_HEADERS = {"X-API-Version": "v2"}
V1_HEADERS = {"X-API-Version": "v1"}


def _create(client, headers, payload):
    return client.post("/orders", json=payload, headers=headers)


def test_default_version_is_v1(client):
    """不带版本头的老客户端按 v1 语义处理。"""
    r = _create(client, {}, {"item": "book", "total": 12.99})
    assert r.status_code == 201
    assert r.headers["X-API-Version"] == "v1"
    assert r.json() == {
        "id": r.json()["id"],
        "item": "book",
        "total": 12.99,
        "created_at": r.json()["created_at"],
    }


def test_v2_uses_minor_units_and_currency(client):
    """v2 暴露 total_cents 与 currency，不含 v1 的 total。"""
    r = _create(client, V2_HEADERS, {"item": "book", "total_cents": 1299, "currency": "EUR"})
    assert r.status_code == 201
    body = r.json()
    assert body["total_cents"] == 1299
    assert body["currency"] == "EUR"
    assert "total" not in body


def test_same_resource_rendered_per_version(client):
    """同一资源按请求版本返回不同表示，且互不污染。"""
    created = _create(client, V2_HEADERS, {"item": "pen", "total_cents": 250})
    oid = created.json()["id"]

    as_v1 = client.get(f"/orders/{oid}", headers=V1_HEADERS).json()
    as_v2 = client.get(f"/orders/{oid}", headers=V2_HEADERS).json()

    assert as_v1["total"] == 2.5 and "total_cents" not in as_v1
    assert as_v2["total_cents"] == 250 and "total" not in as_v2


def test_v1_write_read_back_as_v2_is_consistent(client):
    """v1 写入的数据以 v2 读取时值语义一致（12.99 -> 1299 分）。"""
    created = _create(client, V1_HEADERS, {"item": "mug", "total": 12.99})
    oid = created.json()["id"]
    as_v2 = client.get(f"/orders/{oid}", headers=V2_HEADERS).json()
    assert as_v2["total_cents"] == 1299
    assert as_v2["currency"] == "USD"


def test_unknown_version_rejected_with_426(client):
    r = client.get("/orders/ord_000001", headers={"X-API-Version": "v9"})
    assert r.status_code == 426
    body = r.json()
    assert body["error"]["code"] == "unsupported_version"
    assert "v1" in body["error"]["details"]["supported"]


def test_retired_version_rejected_with_410(client):
    r = client.get("/orders/ord_000001", headers={"X-API-Version": "v0"})
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "version_retired"


def test_version_errors_distinct_from_param_and_unavailable(client):
    """版本拒绝与参数错误(400)、服务不可用(5xx)在状态码与 code 上都可区分。"""
    version_r = client.get("/orders/x", headers={"X-API-Version": "v9"})
    param_r = _create(client, V2_HEADERS, {"item": "x"})  # 缺 total_cents
    assert version_r.status_code == 426
    assert param_r.status_code == 400
    assert param_r.json()["error"]["code"] == "invalid_request"
    assert version_r.json()["error"]["code"] != param_r.json()["error"]["code"]
