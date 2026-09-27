"""Tests for the remaining /api/v1/auth/* endpoints and dependencies.

覆盖范围（与本次加固任务直接相关）：
- /api/v1/auth/login  端点：成功、用户不存在、密码错误
- /api/v1/auth/refresh 端点：缺 token、被撤销、refresh 失败
- /api/v1/auth/logout  端点：撤销 token
- /api/v1/auth/me      端点：当前用户信息
- get_current_user 依赖：token 被撤销、解码失败、payload 异常、用户被禁用
- require_role 依赖：权限不足
- register 端点：create_user 抛 ValueError 时返回 409

说明：这些函数与 register 端点共享 ``app.api.v1.auth`` 模块，本次一并补齐
单元测试以提升 ``app.api.v1.auth`` 的整体覆盖率。
"""

from __future__ import annotations

from typing import Any, Generator

import pytest


@pytest.fixture(autouse=True)
def _auth_tests_enforce_permissions(monkeypatch):
    """auth 测试验证完整认证/权限流程（401/403 语义），必须开启权限强制。

    conftest 的 ``_env_setup`` 默认关闭权限以简化非认证测试；本文件
    覆盖回 ``true``（get_current_user/require_permission 均实时读取
    LNN_PERMISSION_ENFORCED，monkeypatch 在 app 构建前生效）。
    """
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")


import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.v1.auth import (
    get_current_user,
    require_role,
    router as auth_router,
)
from app.auth.security import (
    create_access_token,
    get_token_ban_list,
)
from app.middleware.rate_limiter import limiter as _registration_limiter
from app.models import user as user_module
from app.models.user import UserStore


# Fixtures (与 test_auth_register.py 保持一致的隔离策略)


@pytest.fixture
def isolated_user_store(tmp_path, monkeypatch) -> Generator[Any, None, None]:
    """为每个测试用例提供独立的 user store，避免用户数据相互污染。"""
    store_file = tmp_path / "users_test_auth.json"
    monkeypatch.setattr(user_module, "USER_STORE_FILE", str(store_file))
    # 复位模块级单例（_UserStoreHolder 内部维护 _instance）
    monkeypatch.setattr(user_module._holder, "_instance", None)

    def _factory(file_path: str = str(store_file)) -> UserStore:
        return UserStore(file_path=file_path)

    monkeypatch.setattr(user_module, "get_user_store", _factory)
    yield user_module


@pytest.fixture
def fresh_ban_list(tmp_path, monkeypatch) -> Generator[Any, None, None]:
    """为每个测试用例提供新的 TokenBanList 单例。"""
    ban_file = tmp_path / "banned_tokens.json"
    monkeypatch.setenv("LNN_BANNED_TOKENS_FILE", str(ban_file))
    import app.auth.security as security_module

    monkeypatch.setattr(security_module, "_token_ban_list", None)
    yield get_token_ban_list()
    monkeypatch.setattr(security_module, "_token_ban_list", None)


@pytest.fixture
def client(isolated_user_store) -> Generator[TestClient, None, None]:
    """提供挂载 auth_router 的 FastAPI 客户端。"""
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
    """每个用例开始前重置模块级速率限制器与 slowapi 存储。"""
    # 同时清空 slowapi 全局 limiter 存储，避免 login 限流（5/minute）跨用例累积
    try:
        storage = getattr(_registration_limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()
        elif storage is not None and hasattr(storage, "storage"):
            storage.storage.clear()
    except Exception:
        pass
    yield
    # 测试结束再清一次，确保下个测试用例的隔离
    try:
        storage = getattr(_registration_limiter, "_storage", None)
        if storage is not None and hasattr(storage, "reset"):
            storage.reset()
        elif storage is not None and hasattr(storage, "storage"):
            storage.storage.clear()
    except Exception:
        pass


@pytest.fixture
def registered_user(client, monkeypatch) -> Generator[dict, None, None]:
    """预先注册一个可用账户并返回凭据。"""
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
    yield {"username": "alice", "password": "Passw0rd!"}


# 登录 / Token 刷新 / 登出 / me 端点


class TestLoginEndpoint:
    """/api/v1/auth/login 端点行为。"""

    def test_login_success_returns_tokens(self, client, registered_user):
        """正确凭据应返回 access + refresh token。"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["message"] == "登录成功"
        data = body["data"]
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == registered_user["username"]

    def test_login_with_unknown_user_returns_401(self, client):
        """不存在的用户名应返回 401。"""
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "ghost", "password": "Passw0rd!"},
        )
        assert response.status_code == 401

    def test_login_with_wrong_password_returns_401(self, client, registered_user):
        """密码错误应返回 401。"""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": "WrongPass1!",
            },
        )
        assert response.status_code == 401


class TestRefreshEndpoint:
    """/api/v1/auth/refresh 端点行为。"""

    def test_refresh_without_token_returns_400(self, client):
        """请求体未提供 refresh_token 时返回 400。"""
        response = client.post("/api/v1/auth/refresh", json={})
        assert response.status_code == 400

    def test_refresh_with_invalid_token_returns_401(self, client):
        """无效 refresh token 返回 401。"""
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "not-a-real-jwt"},
        )
        assert response.status_code == 401

    def test_refresh_with_access_token_returns_401(self, client, registered_user):
        """使用 access token 当作 refresh token 应被拒绝。"""
        # 登录拿到 access
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        access_token = login_resp.json()["data"]["access_token"]
        # 用 access 去刷新
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": access_token},
        )
        assert response.status_code == 401

    def test_refresh_with_banned_token_returns_401(self, client, registered_user, fresh_ban_list):
        """已被撤销的 refresh token 返回 401。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        refresh_token = login_resp.json()["data"]["refresh_token"]
        # 撤销该 refresh token
        fresh_ban_list.ban(refresh_token)
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 401

    def test_refresh_success_returns_new_tokens(self, client, registered_user, fresh_ban_list):
        """使用有效 refresh token 应返回新的 access + refresh。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        refresh_token = login_resp.json()["data"]["refresh_token"]
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert "access_token" in body["data"]
        assert "refresh_token" in body["data"]


class TestLogoutEndpoint:
    """/api/v1/auth/logout 端点行为。"""

    def test_logout_with_empty_body_returns_200(self, client):
        """无 token 信息的登出请求也应返回 200。"""
        response = client.post("/api/v1/auth/logout", json={})
        assert response.status_code == 200
        assert response.json()["code"] == 0

    def test_logout_bans_access_token(self, client, registered_user, fresh_ban_list):
        """登出时 access token 会被加入 ban list。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        access_token = login_resp.json()["data"]["access_token"]
        # 登出
        response = client.post(
            "/api/v1/auth/logout",
            json={"access_token": access_token},
        )
        assert response.status_code == 200
        # token 已被撤销
        assert fresh_ban_list.is_banned(access_token) is True

    def test_logout_bans_refresh_token(self, client, registered_user, fresh_ban_list):
        """登出时 refresh token 也会被加入 ban list。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        refresh_token = login_resp.json()["data"]["refresh_token"]
        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        assert fresh_ban_list.is_banned(refresh_token) is True


class TestMeEndpoint:
    """/api/v1/auth/me 端点行为。"""

    def test_me_without_token_returns_401(self, client, registered_user):
        """未提供 Authorization 头时返回 401。"""
        response = client.get("/api/v1/auth/me")
        assert response.status_code in (401, 403)

    def test_me_with_valid_token_returns_user_info(self, client, registered_user):
        """提供有效 access token 时返回当前用户信息。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        access_token = login_resp.json()["data"]["access_token"]
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["username"] == registered_user["username"]


# get_current_user 依赖项


class TestGetCurrentUserDependency:
    """get_current_user 依赖项在各种异常情况下的行为。"""

    def _build_app_with_route(self):
        app = FastAPI()
        app.include_router(auth_router)

        @app.get("/whoami")
        async def whoami(user: dict = Depends(get_current_user)):
            return {"username": user["username"], "role": user["role"]}

        return app

    def test_get_current_user_with_invalid_token_returns_401(self):
        """无效 token 触发 401。"""
        app = self._build_app_with_route()
        with TestClient(app) as c:
            response = c.get(
                "/whoami",
                headers={"Authorization": "Bearer invalid-token"},
            )
        assert response.status_code == 401

    def test_get_current_user_with_banned_token_returns_401(self, registered_user, fresh_ban_list):
        """已被 ban 的 token 触发 401。"""
        # 注册并登录拿到有效 token
        app = self._build_app_with_route()
        with TestClient(app) as c:
            login_resp = c.post(
                "/api/v1/auth/login",
                json={
                    "username": registered_user["username"],
                    "password": registered_user["password"],
                },
            )
            access_token = login_resp.json()["data"]["access_token"]
            # 撤销 token
            fresh_ban_list.ban(access_token)
            response = c.get(
                "/whoami",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 401

    def test_get_current_user_with_token_for_missing_user_returns_401(self, monkeypatch, fresh_ban_list):
        """token 合法但 store 中无此用户时返回 401。"""
        # 直接签发一个 token，但 store 中并不存在该用户
        bogus_token = create_access_token({"sub": "phantom", "jti": "jti-1"})
        app = self._build_app_with_route()
        with TestClient(app) as c:
            response = c.get(
                "/whoami",
                headers={"Authorization": f"Bearer {bogus_token}"},
            )
        assert response.status_code == 401


class TestRequireRoleDependency:
    """require_role 依赖项的权限检查行为。"""

    def test_require_role_rejects_wrong_role(self, client, registered_user):
        """当前用户角色不在允许列表中时返回 403。"""
        # 登录拿到 token（默认角色为 user）
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        access_token = login_resp.json()["data"]["access_token"]

        # 构造一个只允许 admin 的路由
        app = FastAPI()

        @app.get("/admin-area")
        async def admin_area(user=Depends(require_role("admin"))):
            return {"username": user["username"]}

        with TestClient(app) as c:
            response = c.get(
                "/admin-area",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 403

    def test_require_role_allows_listed_role(self, client, registered_user):
        """角色在允许列表中时正常放行。"""
        login_resp = client.post(
            "/api/v1/auth/login",
            json={
                "username": registered_user["username"],
                "password": registered_user["password"],
            },
        )
        access_token = login_resp.json()["data"]["access_token"]

        app = FastAPI()

        @app.get("/user-area")
        async def user_area(user=Depends(require_role("user", "admin"))):
            return {"username": user["username"], "role": user["role"]}

        with TestClient(app) as c:
            response = c.get(
                "/user-area",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 200
        assert response.json()["username"] == registered_user["username"]


# register 端点的 ValueError 兜底分支


class TestRegisterValueErrorFallback:
    """注册时 store.create_user 抛 ValueError 时应返回 409。"""

    def test_register_value_error_returns_409(self, client, monkeypatch, isolated_user_store):
        """当 store.create_user 抛出 ValueError 时返回 409。

        污染修复说明（2026-09-13）：此前通过 monkeypatch auth 模块全局
        ``get_user_store`` 实现注入，单跑 PASS、全量跑 FAIL（实测返回 200，
        路由用到了未被补丁覆盖的 store 解析路径）。改为在 ``UserStore``
        类级别打补丁——无论路由经哪条路径拿到哪个 store 实例，
        ``create_user`` 都会抛 ValueError，测试的是端点本身的契约。
        """
        monkeypatch.setenv("LNN_REGISTRATION_CODE", "SECRET-1234")

        def _raising_create_user(
            self, username, password_hash, role="user", must_change_password=False
        ):
            raise ValueError("user store corruption: simulated")

        monkeypatch.setattr(UserStore, "create_user", _raising_create_user)

        response = client.post(
            "/api/v1/auth/register",
            json={
                "username": "alice",
                "password": "Passw0rd!",
                "invite_code": "SECRET-1234",
            },
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == 1009
        # 内部错误细节已脱敏（safe_error_message 兜底），仅断言消息非空
        assert body["message"]


# 访客模式登录端点（/api/v1/auth/guest）


class TestGuestLoginEndpoint:
    """/api/v1/auth/guest 端点行为。"""

    def test_guest_login_returns_temp_token(self, client, monkeypatch):
        """开启访客模式时返回临时 access token 且不签发 refresh token。"""
        from app.config import config

        monkeypatch.setattr(config.security, "guest_enabled", True)
        response = client.post("/api/v1/auth/guest")
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["message"] == "访客登录成功"
        data = body["data"]
        assert "access_token" in data
        assert "refresh_token" not in data
        assert data["token_type"] == "bearer"
        assert data["user"]["is_guest"] is True
        assert data["user"]["role"] == "guest"
        assert data["user"]["username"].startswith("guest_")

    def test_guest_login_when_disabled_returns_403(self, client, monkeypatch):
        """关闭访客模式时返回 403。"""
        from app.config import config

        monkeypatch.setattr(config.security, "guest_enabled", False)
        response = client.post("/api/v1/auth/guest")
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == 1003
        assert "访客模式已关闭" in body["message"]

    def test_guest_token_can_call_me(self, client, monkeypatch):
        """访客 token 可用于 /api/v1/auth/me 返回临时身份。"""
        from app.config import config

        monkeypatch.setattr(config.security, "guest_enabled", True)
        guest_resp = client.post("/api/v1/auth/guest")
        access_token = guest_resp.json()["data"]["access_token"]
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 0
        assert body["data"]["is_guest"] is True
        assert body["data"]["role"] == "guest"
        assert body["data"]["username"].startswith("guest_")


class TestGuestGetCurrentUser:
    """get_current_user 对访客 token 的支持。"""

    def test_guest_token_resolves_via_get_current_user(self, monkeypatch):
        """访客 token 通过 get_current_user 解析为 guest 身份，无需落用户存储。"""
        from app.config import config

        monkeypatch.setattr(config.security, "guest_enabled", True)
        app = FastAPI()
        app.include_router(auth_router)

        @app.get("/whoami")
        async def whoami(user: dict = Depends(get_current_user)):
            return {"username": user["username"], "role": user["role"], "is_guest": user.get("is_guest", False)}

        with TestClient(app) as c:
            guest_resp = c.post("/api/v1/auth/guest")
            access_token = guest_resp.json()["data"]["access_token"]
            response = c.get(
                "/whoami",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["is_guest"] is True
        assert body["role"] == "guest"
        assert body["username"].startswith("guest_")
