"""pytest 公共夹具。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    """默认客户端：配额宽松，用于版本/幂等等与限流无关的场景。"""
    app = create_app(rate_limit=1000, window_seconds=60.0)
    return TestClient(app)


@pytest.fixture()
def tight_client() -> TestClient:
    """小配额客户端：每 60 秒仅 3 个请求，专测限流边界。"""
    app = create_app(rate_limit=3, window_seconds=60.0)
    return TestClient(app)



# ---- 场景化运行日志 -------------------------------------------------------
# 输出形如：
#   [场景] 版本协商 :: 显式 v1 返回老结构
#   [结果] PASS ...
# 方便从测试日志直接看出覆盖了哪些场景、每个用例及其结果。

import logging

scenario_logger = logging.getLogger("scenario")
if not scenario_logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    scenario_logger.addHandler(handler)
    scenario_logger.setLevel(logging.INFO)
    scenario_logger.propagate = False


def pytest_runtest_logstart(nodeid, location):
    scenario_logger.info("用例开始 :: %s", nodeid)


def pytest_runtest_logreport(report):
    if report.when == "call":
        outcome = report.outcome.upper()
        scenario_logger.info("用例结果 :: %s :: %s", outcome, report.nodeid)


# ---- 真实 HTTP 服务器夹具（TestClient 会串行化端点，无法测真并发） ----------

import socket
import subprocess
import sys
import time as _time
from pathlib import Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def server():
    """启动一个真实的多线程 uvicorn 子进程，返回 base_url。

    通过环境变量注入：小而确定的上游延迟(0.5s) 与宽松配额，
    使同键并发请求在服务端真实重叠。
    """
    port = _free_port()
    root = Path(__file__).resolve().parent.parent
    env = {
        "GATEWAY_RATE_LIMIT": "1000",
        "GATEWAY_RATE_WINDOW": "60",
        "GATEWAY_UPSTREAM_LATENCY": "1.0",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--workers", "1", "--loop", "asyncio",
        ],
        cwd=root,
        env={**__import__("os").environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    deadline = _time.time() + 15
    import httpx as _httpx
    while _time.time() < deadline:
        try:
            if _httpx.get(base_url + "/", timeout=0.5).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            _time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("test server failed to start")
    yield base_url
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
