"""Smoke test: the app factory produces a working FastAPI app."""
from fastapi.testclient import TestClient

from app.main import create_app


def test_app_builds():
    client = TestClient(create_app())
    assert client is not None
