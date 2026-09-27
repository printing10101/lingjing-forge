"""Tests for unified_auth middleware access logging helpers.

覆盖范围：
- _get_client_ip：从 X-Forwarded-For / X-Real-IP / scope.client 中提取客户端 IP
- _log_access：访问日志格式（方法/路径/客户端IP/状态码/时间戳）
- UnifiedAuthMiddleware：即使在权限检查关闭时仍记录访问审计日志
"""

from __future__ import annotations

import logging
from typing import Generator

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.auth.unified_auth import (
    UnifiedAuthMiddleware,
    _get_client_ip,
    _log_access,
)


# Fixtures


@pytest.fixture
def token_file(tmp_path):
    """为 LNN auth 提供临时 token 文件。"""
    path = tmp_path / ".lnn_token"
    path.write_text("test-token-uuid-12345")
    return path


@pytest.fixture
def app_with_middleware(token_file, monkeypatch) -> Generator[FastAPI, None, None]:
    """提供挂载 UnifiedAuthMiddleware 的 FastAPI 应用。"""
    monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    app.add_middleware(
        UnifiedAuthMiddleware,
        lnn_auth_enabled=True,
        lnn_permission_enforced=False,  # 显式关闭权限检查
        jwt_auth_enabled=False,
        agent_auth_enabled=False,
    )
    yield app


# _get_client_ip 测试


class TestGetClientIp:
    """_get_client_ip 函数从 ASGI scope 提取客户端 IP。"""

    def test_prefers_x_forwarded_for(self):
        """X-Forwarded-For 优先级最高，取最左侧 IP。"""
        scope = {
            "headers": [
                (b"x-forwarded-for", b"203.0.113.5, 10.0.0.1, 10.0.0.2"),
                (b"x-real-ip", b"198.51.100.1"),
            ],
            "client": ("127.0.0.1", 12345),
        }
        assert _get_client_ip(scope) == "203.0.113.5"

    def test_falls_back_to_x_real_ip(self):
        """无 X-Forwarded-For 时使用 X-Real-IP。"""
        scope = {
            "headers": [(b"x-real-ip", b"198.51.100.1")],
            "client": ("127.0.0.1", 12345),
        }
        assert _get_client_ip(scope) == "198.51.100.1"

    def test_falls_back_to_scope_client(self):
        """无代理头时回退到 scope.client。"""
        scope = {
            "headers": [],
            "client": ("192.0.2.10", 54321),
        }
        assert _get_client_ip(scope) == "192.0.2.10"

    def test_returns_unknown_when_no_client_info(self):
        """完全无客户端信息时返回 'unknown'。"""
        scope = {"headers": []}
        assert _get_client_ip(scope) == "unknown"

    def test_x_forwarded_for_empty_value_falls_back(self):
        """X-Forwarded-For 为空值时应跳过并使用 X-Real-IP。"""
        scope = {
            "headers": [
                (b"x-forwarded-for", b""),
                (b"x-real-ip", b"198.51.100.42"),
            ],
            "client": ("127.0.0.1", 12345),
        }
        assert _get_client_ip(scope) == "198.51.100.42"


# _log_access 测试


class TestLogAccess:
    """_log_access 写入统一格式的访问审计日志。"""

    def test_log_access_writes_info(self, caplog):
        """默认级别为 INFO，应记录访问日志。"""
        with caplog.at_level(logging.INFO, logger="app.auth.middleware"):
            _log_access(
                method="GET",
                path="/api/test",
                client_ip="1.2.3.4",
                status_code=200,
            )
        assert any("method=GET" in rec.message for rec in caplog.records)
        assert any("path=/api/test" in rec.message for rec in caplog.records)
        assert any("client_ip=1.2.3.4" in rec.message for rec in caplog.records)
        assert any("status=200" in rec.message for rec in caplog.records)

    def test_log_access_supports_custom_level(self, caplog):
        """支持自定义日志级别。"""
        with caplog.at_level(logging.WARNING, logger="app.auth.middleware"):
            _log_access(
                method="POST",
                path="/api/secure",
                client_ip="5.6.7.8",
                status_code=403,
                level=logging.WARNING,
            )
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("status=403" in r.message for r in warning_records)


# 端到端：权限检查关闭时仍记录访问日志


class TestAccessLogWhenPermissionDisabled:
    """即使权限检查关闭（lnn_permission_enforced=False）也必须记录访问日志。"""

    def test_authorized_request_logs_access(self, app_with_middleware, monkeypatch, caplog):
        """通过认证的请求应当产生一条访问日志。"""
        client = TestClient(app_with_middleware)
        with caplog.at_level(logging.INFO, logger="app.auth.middleware"):
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer test-token-uuid-12345"},
            )
        assert response.status_code == 200
        # 验证至少有一条 access 日志
        access_records = [r for r in caplog.records if "access" in r.message]
        assert any("path=/protected" in r.message for r in access_records)
        assert any("status=200" in r.message for r in access_records)

    def test_public_endpoint_logs_access(self, app_with_middleware, caplog):
        """公共端点也应记录访问日志。"""
        client = TestClient(app_with_middleware)
        with caplog.at_level(logging.INFO, logger="app.auth.middleware"):
            response = client.get("/api/health")
        assert response.status_code == 200
        access_records = [r for r in caplog.records if "access" in r.message]
        assert any("path=/api/health" in r.message for r in access_records)

    def test_permission_disabled_logs_audit_info(self, app_with_middleware, caplog):
        """权限检查被关闭时，应记录 INFO 审计日志提示绕过检查。"""
        client = TestClient(app_with_middleware)
        with caplog.at_level(logging.INFO, logger="app.auth.middleware"):
            response = client.get(
                "/protected",
                headers={"Authorization": "Bearer test-token-uuid-12345"},
            )
        assert response.status_code == 200
        # 验证包含 "permission check disabled" 的审计日志
        audit_records = [r for r in caplog.records if "permission check disabled" in r.message]
        assert len(audit_records) >= 1
        assert any("path=/protected" in r.message for r in audit_records)
