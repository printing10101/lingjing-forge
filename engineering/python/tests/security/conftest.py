"""安全测试套件共享 fixtures。

主要为速率限制 / Token 撤销 / 路径遍历测试提供：
- 隔离的临时 ban 列表文件，避免跨测试污染
- 必要的环境变量（``LNN_JWT_SECRET``）
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _security_env_setup(monkeypatch):
    """每个测试前后清理与安全相关的全局状态。"""

    # 确保 JWT 密钥存在（某些环境未设置）
    if not os.environ.get("LNN_JWT_SECRET"):
        monkeypatch.setenv(
            "LNN_JWT_SECRET",
            "test_conftest_default_secret_value_min_32chars_safe",
        )
    # 关闭速率限制之外的认证开关
    monkeypatch.setenv("LNN_AUTH_ENABLED", "false")
    monkeypatch.setenv("AGENT_AUTH_ENABLED", "false")
    yield
