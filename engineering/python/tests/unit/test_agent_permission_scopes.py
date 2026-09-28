"""Agent token 权限集成回归测试。

背景（2026-09-28）：AgentAuthMiddleware 校验通过后不写 ``state.username``，
而 ``require_permission`` 系列依赖只认 username —— 网关 HTTP 工具
（/api/agent/v1/*）带合法 agent token 也一律 401（code 1003）。

修复：中间件成功分支写 ``state.agent_id/agent_scopes``；
``require_permission`` 系列优先按 agent_scopes 复用 URL 权限类模型判定。
"""

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.auth import permissions as perm_module
from app.auth.middleware import UnifiedAuthMiddleware
from app.auth.permissions import (
    require_all_permissions,
    require_any_permission,
    require_permission,
)

READ_PATH = "/api/agent/v1/gcode/failure-stats"


def _make_request(method: str = "GET", path: str = READ_PATH, state: dict | None = None) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "server": ("testserver", 80),
        "headers": [],
        "query_string": b"",
    }
    request = Request(scope)
    for key, value in (state or {}).items():
        setattr(request.state, key, value)
    return request


@pytest.fixture(autouse=True)
def _enforce_permissions(monkeypatch):
    """确保测试跑在权限强制开启口径下（不受外部 dev 环境变量影响）。"""
    monkeypatch.delenv("LNN_PERMISSION_ENFORCED", raising=False)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_read_scope_passes_read_route():
    checker = require_permission("agent:read")
    request = _make_request(state={"agent_scopes": ["R"]})
    assert await checker(request) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_scope_hierarchy_higher_scope_covers_read():
    checker = require_permission("agent:read")
    request = _make_request(state={"agent_scopes": ["W"]})
    assert await checker(request) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_read_scope_forbidden_on_write_class():
    checker = require_permission("agent:execute")
    request = _make_request(method="POST", path="/api/agent/v1/models/predict", state={"agent_scopes": ["R"]})
    with pytest.raises(HTTPException) as exc:
        await checker(request)
    assert exc.value.status_code == 403


@pytest.mark.unit
@pytest.mark.asyncio
async def test_agent_write_scope_passes_post_route():
    checker = require_permission("agent:execute")
    request = _make_request(method="POST", path="/api/agent/v1/models/predict", state={"agent_scopes": ["W"]})
    assert await checker(request) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_any_and_all_checkers_honor_agent_scopes():
    request = _make_request(state={"agent_scopes": ["R"]})
    assert await require_any_permission("agent:read", "agent:execute")(request) is None
    assert await require_all_permissions("agent:read", "agent:audit:read")(request) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_branch_untouched_when_no_agent_scopes(monkeypatch):
    """无 agent_scopes 时回退原用户逻辑：无 username 401，有 username 走 DB 检查。"""
    checker = require_permission("agent:read")

    with pytest.raises(HTTPException) as exc:
        await checker(_make_request())
    assert exc.value.status_code == 401

    async def fake_has_permission(username: str, required: str) -> bool:
        return True

    monkeypatch.setattr(perm_module, "check_user_has_permission", fake_has_permission)
    assert await checker(_make_request(state={"username": "alice"})) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_middleware_writes_agent_state_downstream(monkeypatch, tmp_path: Path):
    """核心回归：中间件校验成功后必须把 agent_id/agent_scopes 写进 scope.state。"""
    from app.agent.auth import AgentTokenStore

    store = AgentTokenStore(storage_path=str(tmp_path / "agent_tokens.json"))
    raw_token, _token = store.create_token(scopes=["R", "W"])
    monkeypatch.setattr(
        "app.auth.middleware._get_agent_token_store", lambda: store
    )

    captured: dict = {}

    async def capture_app(scope, receive, send):
        captured["state"] = dict(scope.get("state") or {})

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        return None

    middleware = UnifiedAuthMiddleware(capture_app, lnn_auth_enabled=False)
    scope = {
        "type": "http",
        "method": "GET",
        "path": READ_PATH,
        "scheme": "http",
        "server": ("testserver", 80),
        "headers": [(b"authorization", f"Bearer {raw_token}".encode())],
        "query_string": b"",
    }
    await middleware(scope, receive, send)

    assert captured["state"]["agent_id"]
    assert captured["state"]["agent_scopes"] == ["R", "W"]
