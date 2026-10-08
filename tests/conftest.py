"""Shared fixtures and scenario-level test logging.

The ``pytest_runtest_*`` hooks emit one log line when each scenario
(test module) starts and one line per case with its outcome, so a test
run reads as a coverage report::

    SCENARIO BEGIN tests/test_versioning.py :: 版本协商与兼容
    CASE PASS  tests/test_versioning.py::test_default_version_is_v1
"""
from __future__ import annotations

import logging
from typing import Callable

import pytest
from fastapi.testclient import TestClient

from app.main import GatewayConfig, create_app

logger = logging.getLogger("gateway.tests")

#: Human-readable scenario title per test module.
SCENARIOS = {
    "test_versioning": "版本协商与兼容：默认版本、字段差异、未知/已下线版本拒绝",
    "test_idempotency": "幂等与并发：重复提交、并发同键、键冲突、失败可重试",
    "test_ratelimit": "限流：额度内放行、超限 429、额度暴露、调用方隔离",
    "test_errors": "错误收敛：上游异常、校验错误、未知路由的统一信封",
}

_seen_modules: set[str] = set()


@pytest.fixture
def make_client() -> Callable[..., TestClient]:
    """Build a TestClient, optionally overriding gateway config."""

    def _make(config: GatewayConfig | None = None) -> TestClient:
        return TestClient(create_app(config))

    return _make


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def pytest_runtest_setup(item: pytest.Item) -> None:
    module = item.module.__name__.rsplit(".", 1)[-1]
    if module not in _seen_modules:
        _seen_modules.add(module)
        title = SCENARIOS.get(module, "")
        logger.info("SCENARIO BEGIN %s :: %s", module, title)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when != "call":
        return
    outcome = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(
        report.outcome, report.outcome.upper()
    )
    logger.info("CASE %-4s %s", outcome, report.nodeid)
