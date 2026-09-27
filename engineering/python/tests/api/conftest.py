"""Fixtures for API tests.

已知怪癖（2026-09 工程体检记录）：pytest 9.1 + ``--import-mode=importlib`` 下，
当**显式以多文件参数**混合传入 ``tests/unit/``、``tests/`` 根与 ``tests/api/``
且 api 目录不在最前时，本 conftest 偶发不被加载（表现为 ``fixture 'client'
not found``，同目录自带本地 fixture 的文件不受影响）。目录形式（``pytest
tests/``、``pytest tests/api/``）与 CI 调用不受影响。
规避：混合调试时把 ``tests/api/`` 参数放最前，或直接用目录形式。
根治方向：统一 ``tests/`` 的 ``__init__.py`` 策略（需专项评估收集命名影响）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient fixture."""
    try:
        from app.main import app

        with TestClient(app) as c:
            yield c
    except Exception as exc:
        # FastAPI 启动失败属于基础设施故障，不应被 skip 掩盖；
        # 改为 fail 使 CI 真实反映启动问题。
        pytest.fail(f"FastAPI app 启动失败: {exc}")
