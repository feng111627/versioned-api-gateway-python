"""幂等与并发：重复提交、并发同键、键冲突、失败可重试。"""
from __future__ import annotations

import asyncio

import httpx

from app.main import create_app

KEY = {"Idempotency-Key": "order-abc-123"}
PAYLOAD = {"item": "book", "total_cents": 500}


def test_same_key_same_payload_replays(client):
    """同键同内容：只生效一次，返回一致结果并标记重放。"""
    r1 = client.post("/orders", json=PAYLOAD, headers={**KEY, "X-API-Version": "v2"})
    r2 = client.post("/orders", json=PAYLOAD, headers={**KEY, "X-API-Version": "v2"})
    assert r1.status_code == 201 and r2.status_code == 201
    assert "Idempotency-Replayed" not in r1.headers
    assert r2.headers["Idempotency-Replayed"] == "true"
    assert r1.json() == r2.json()


def test_same_key_different_payload_conflicts(client):
    """同键不同内容：409 冲突。"""
    client.post("/orders", json=PAYLOAD, headers={**KEY, "X-API-Version": "v2"})
    r = client.post(
        "/orders",
        json={"item": "other", "total_cents": 900},
        headers={**KEY, "X-API-Version": "v2"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "idempotency_conflict"


def test_different_keys_are_independent(client):
    """不同键互不影响，各自创建各自资源。"""
    r1 = client.post("/orders", json=PAYLOAD, headers={"Idempotency-Key": "k1", "X-API-Version": "v2"})
    r2 = client.post("/orders", json=PAYLOAD, headers={"Idempotency-Key": "k2", "X-API-Version": "v2"})
    assert r1.status_code == r2.status_code == 201
    assert r1.json()["id"] != r2.json()["id"]


def test_failed_operation_is_not_recorded(client):
    """失败的执行不落记录：同键修正内容后重试可以成功。"""
    bad = client.post(
        "/orders",
        json={"item": "upstream-fail", "total_cents": 1},
        headers={**KEY, "X-API-Version": "v2"},
    )
    assert bad.status_code == 502
    retry = client.post("/orders", json=PAYLOAD, headers={**KEY, "X-API-Version": "v2"})
    assert retry.status_code == 201


def test_concurrent_same_key_executes_once(make_client):
    """并发同键同内容：只真正创建一次，所有请求拿到同一结果。"""
    client = make_client()

    async def run():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            return await asyncio.gather(
                *[
                    ac.post("/orders", json=PAYLOAD, headers={**KEY, "X-API-Version": "v2"})
                    for _ in range(10)
                ]
            )

    responses = asyncio.run(run())
    bodies = [r.json() for r in responses]
    assert all(r.status_code == 201 for r in responses)
    assert len({b["id"] for b in bodies}) == 1  # 只有一个订单被创建
    replays = [r.headers.get("Idempotency-Replayed") for r in responses]
    assert replays.count(None) == 1  # 恰好一次真实执行
    assert replays.count("true") == 9
