"""Tests for /api/v1/auth/register endpoint.

覆盖范围：
- 开放注册（LNN_REGISTRATION_CODE 为空）：任意用户可直接注册，HTTP 200
- 邀请码模式（LNN_REGISTRATION_CODE 非空）：邀请码缺失/错误 → HTTP 403
- 用户名冲突：HTTP 409
- 注册成功：HTTP 200，标准化 JSON 响应
- IP 速率限制：60 分钟内第 6 次请求触发 HTTP 429 且响应头包含 Retry-After
"""

from __future__ import annotations

from typing import Any, Generator

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """每测试前清空限流器：IP 限流状态跨测试累积会导致 429 断言提前触发。

    存量测试隔离修复（2026-09）：注册端点有两层限流——
    1. ``permission_checker._rate_limiter``（自研，IP 级）；
    2. slowapi ``@limiter.limit("3/hour")``（注册专用，MemoryStorage）。
    此前只清了前者：同进程内更早执行的测试文件只要调用过注册端点，
    slowapi 计数就会累积，本文件的 429/409 断言被提前触发的 429 击穿。
    """
    from app.auth.permissions import permission_checker

    permission_checker._rate_limiter.clear()
    _registration_limiter.reset()
    yield
    permission_checker._rate_limiter.clear()
    _registration_limiter.reset()


from app.config import config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.auth import (
    router as auth_router,
)
from app.middleware.rate_limiter import limiter as _registration_limiter
from app.models import user as user_module
from app.models.user import UserStore


# Fixtures


@pytest.fixture
def isolated_user_store(tmp_path, monkeypatch) -> Generator[Any, None, None]:
    """为每个测试用例提供独立的 user store。

    实现策略：
    - 直接用 ``monkeypatch.setattr`` 覆盖 ``app.models.user.USER_STORE_FILE``，
      避免 ``importlib.reload`` 触发 torch/扩展模块的重复初始化（与 coverage 工具链
      存在已知冲突，会抛 ``RuntimeError: function '_has_torch_function' already
      has a docstring``）。
    - 同时把 ``get_user_store`` 函数替换为返回新 store 实例的工厂，确保模块级
      单例被重置。
    """
    store_file = tmp_path / "users_test.json"
    monkeypatch.setattr(user_module, "USER_STORE_FILE", str(store_file))
    # 复位模块级单例（_UserStoreHolder 内部维护 _instance）
    monkeypatch.setattr(user_module._holder, "_instance", None)

    def _factory(file_path: str = str(store_file)) -> UserStore:
        return UserStore(file_path=file_path)

    monkeypatch.setattr(user_module, "get_user_store", _factory)
    yield user_module


@pytest.fixture
def client(isolated_user_store) -> Generator[TestClient, None, None]:
    """提供独立的 FastAPI 测试客户端。

    注册 slowapi 的 ``RateLimitExceeded`` 异常处理器，确保 429 响应携带
    ``Retry-After`` 头与统一的中文提示消息。
    """
    from slowapi.errors import RateLimitExceeded

    from app.middleware.rate_limiter import rate_limit_handler

    app = FastAPI()
    app.state.limiter = _registration_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
    app.include_router(auth_router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_rate_limiter() -> Generator[None, None, None]:
    """每个用例开始前重置模块级速率限制器，避免相互污染。"""
    # 清空 slowapi 全局 limiter 存储（注册端点 3/hour 限流）
    try:
        storage = getattr(_registration_limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()
        elif storage is not None and hasattr(storage, "storage"):
            storage.storage.clear()
    except Exception:
        pass
    yield
    try:
        storage = getattr(_registration_limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()
        elif storage is not None and hasattr(storage, "storage"):
            storage.storage.clear()
    except Exception:
        pass


# 注册功能测试


class TestRegisterOpenRegistration:
    """开放注册模式（LNN_REGISTRATION_CODE 为空）下的注册行为。

    修复（2026-08-23）：LNN_REGISTRATION_CODE 为空字符串的语义由
    "注册功能已关闭" 改为 "开放注册"，即任意用户可直接自助注册。
    """

    def test_open_registration_succeeds_without_invite_code(self, client, monkeypatch):
        """未配置邀请码（空字符串）时，任意用户可直接注册，返回 200。"""
        monkeypatch.setattr(config.security, "registration_code", "")
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "Passw0rd!"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "注册成功"
        assert body["data"]["username"] == "alice"
        assert body["data"]["is_guest"] is False

    def test_open_registration_ignores_invite_code(self, client, monkeypatch):
        """开放注册模式下即使携带 invite_code 也不校验，直接放行。"""
        monkeypatch.setattr(config.security, "registration_code", "")
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "bob", "password": "Passw0rd!", "invite_code": "ANY"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "注册成功"


class TestRegisterInvalidInviteCode:
    """邀请码错误或缺失时的注册行为。"""

    def test_missing_invite_code_returns_403(self, client, monkeypatch):
        """请求体不包含 invite_code 时返回 403。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "password": "Passw0rd!"},
        )
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == 1003
        assert "无效的邀请码" in body["message"]

    def test_wrong_invite_code_returns_403(self, client, monkeypatch):
        """邀请码值不匹配时返回 403。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "password": "Passw0rd!",
                "invite_code": "WRONG",
            },
        )
        assert response.status_code == 403
        body = response.json()
        assert "无效的邀请码" in body["message"]


class TestRegisterSuccess:
    """注册成功的标准路径。"""

    def test_valid_invite_code_creates_user(self, client, monkeypatch):
        """正确邀请码 + 新用户名应返回 200。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["message"] == "注册成功"
        assert body["data"]["username"] == "alice"

    def test_duplicate_username_returns_409(self, client, monkeypatch):
        """用户名已存在时返回 409。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "bob",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "bob",
                "password": "OtherPass1!",
                "invite_code": "SECRET-1234",
            },
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == 1009
        assert "用户名已存在" in body["message"]


# 速率限制测试


class TestRegisterRateLimitEndpoint:
    """通过 HTTP 接口验证端到端速率限制。

    slowapi 的 @limiter.limit("3/hour") 装饰器对注册端点限流，3 次之后的
    请求会返回 429。验证行为：
    1. 短时间内连续 4 次注册，第 4 次应触发 429；
    2. 响应中包含 ``Retry-After`` 头。
    """

    def test_fourth_request_returns_429_with_retry_after(self, client, monkeypatch):
        """同一 IP 在 1 小时内连续 4 次注册，第 4 次返回 429 且响应头有 Retry-After。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        # 使用不同的用户名避免 409 干扰
        for i in range(3):
            response = client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user_{i}",
                    "password": "Passw0rd!",
                    "invite_code": "SECRET-1234",
                },
            )
            assert response.status_code == 200, response.text

        # 第 4 次触发限流
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "user_4th",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        assert response.status_code == 429
        assert "Retry-After" in response.headers
        retry_after = int(response.headers["Retry-After"])
        assert 0 < retry_after <= 3600


# 响应格式一致性测试


class TestResponseFormat:
    """验证错误响应统一使用 code/message 字段。"""

    def test_403_response_uses_code_and_message(self, client, monkeypatch):
        """403 响应必须包含 code 和 message 字段。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "password": "Passw0rd!",
                "invite_code": "WRONG",
            },
        )
        body = response.json()
        assert "code" in body
        assert "message" in body
        assert body["code"] == 1003

    def test_409_response_uses_code_and_message(self, client, monkeypatch):
        """409 响应必须包含 code 和 message 字段。"""
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")
        # 先注册一次
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "carol",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        # 第二次同名
        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "carol",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        body = response.json()
        assert "code" in body
        assert "message" in body
        assert body["code"] == 1009
