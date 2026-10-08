"""错误收敛：上游异常、校验错误、未知路由的统一信封。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import GatewayConfig, create_app


@pytest.fixture
def safe_client() -> TestClient:
    return TestClient(create_app(GatewayConfig()), raise_server_exceptions=False)


def test_upstream_failure_returns_stable_502_envelope(client):
    r = client.post(
        "/orders",
        json={"item": "upstream-fail", "total_cents": 1},
        headers={"X-API-Version": "v2"},
    )
    assert r.status_code == 502
    body = r.json()
    assert body["error"]["code"] == "upstream_error"
    assert body["error"]["details"]["dependency"] == "pricing-service"
    # 不泄露内部异常细节
    assert "connection refused" not in r.text
    assert "Traceback" not in r.text


def test_validation_error_returns_400_envelope(client):
    r = client.post("/orders", json={"item": 123}, headers={"X-API-Version": "v2"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["details"]["errors"]


def test_unknown_route_returns_404_envelope(client):
    r = client.get("/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_unexpected_exception_returns_500_without_leaking(safe_client, monkeypatch):
    def boom(canonical):
        raise ValueError("secret internal state: db password xyz")

    monkeypatch.setattr(safe_client.app.state.orders, "create", boom)
    r = safe_client.post(
        "/orders",
        json={"item": "book", "total_cents": 1},
        headers={"X-API-Version": "v2"},
    )
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "internal_error"
    assert "secret internal state" not in r.text
    assert "ValueError" not in r.text
    assert "Traceback" not in r.text


def test_envelope_carries_request_id(client):
    r = client.get("/definitely-not-a-route", headers={"X-Request-Id": "req-123"})
    assert r.json()["request_id"] == "req-123"
    assert r.headers["X-Request-Id"] == "req-123"
