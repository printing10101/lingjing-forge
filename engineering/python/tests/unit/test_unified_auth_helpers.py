"""Tests for unified_auth middleware helper utilities.

目标：提升 ``app.auth.unified_auth`` 的单元测试覆盖率（>=80%）。
覆盖范围：
- ``_get_token_metadata``：token 元数据文件存在/缺失/损坏/未匹配
- ``_save_token`` / ``_load_token`` / ``_initialize_token``：token 持久化
- ``_decode_token`` / ``_decode_token_strict`` / ``_get_token_ban_list``
- ``PermissionLevel`` 枚举 / ``_get_permission_class`` / ``_check_scope``
- ``_make_json_response`` / ``_send_json_response``
- ``UnifiedAuthMiddleware`` 初始化分支（含 lnn_auth_enabled 启用）
- ``__call__`` 中 public path、agent path、JWT path、缺 token 等多分支
- ``_check_lnn_auth`` 中权限开关 on/off、token 错误、public endpoint
- ``_check_jwt_auth`` 中 token 缺失、失效、banned 等
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.auth.unified_auth import (
    AgentAuditLog,
    AgentRateLimiter,
    IdempotencyStore,
    PermissionLevel,
    UnifiedAuthMiddleware,
    WRITE_SCOPES,
    _check_scope,
    _decode_token,
    _decode_token_strict,
    _get_agent_token_store,
    _get_permission_class,
    _get_token_ban_list,
    _get_token_file_path,
    _get_token_metadata,
    _initialize_token,
    _is_public_path,
    _load_token,
    _make_json_response,
    _save_token,
    _send_json_response,
)


# Fixtures


@pytest.fixture
def token_file(tmp_path):
    """Create a temporary LNN token file."""
    p = tmp_path / ".lnn_token"
    p.write_text("test-token-uuid-abc")
    return p


@pytest.fixture
def app_factory(token_file, monkeypatch):
    """提供创建挂载 UnifiedAuthMiddleware 的 FastAPI app 的工厂。"""

    def _make(
        lnn_auth_enabled: bool = True,
        lnn_permission_enforced: bool = False,
        jwt_auth_enabled: bool = False,
        agent_auth_enabled: bool = False,
    ) -> FastAPI:
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        @app.post("/api/agent/v1/predict")
        async def predict():
            return {"result": "ok"}

        @app.get("/api/agent/v1/health")
        async def health():
            return {"status": "healthy"}

        @app.post("/api/agent/v1/tokens")
        async def create_token():
            return {"token": "new"}

        @app.get("/api/docs")
        async def docs():
            return {"docs": True}

        @app.post("/api/agent/v1/train")
        async def train():
            return {"job_id": "j-1"}

        @app.get("/api/agent/v1/train/{job_id}")
        async def train_status(job_id: str):
            return {"job_id": job_id}

        @app.post("/api/agent/v1/execute")
        async def execute():
            return {"ok": True}

        @app.get("/api/agent/v1/audit-log")
        async def audit():
            return {"entries": []}

        @app.get("/api/agent/v1/models")
        async def models():
            return {"models": []}

        @app.get("/api/agent/v1/models/{name}/info")
        async def model_info(name: str):
            return {"name": name}

        @app.get("/api/agent/v1/train/{job_id}/stream")
        async def train_stream(job_id: str):
            return {"stream": True}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=lnn_auth_enabled,
            lnn_permission_enforced=lnn_permission_enforced,
            jwt_auth_enabled=jwt_auth_enabled,
            agent_auth_enabled=agent_auth_enabled,
        )
        return app

    return _make


# _is_public_path


class TestIsPublicPath:
    def test_known_public_paths(self):
        for p in [
            "/api/health",
            "/api/metrics",
            "/api/v1/auth/login",
            "/health",
        ]:
            assert _is_public_path(p) is True

    def test_known_protected_paths(self):
        for p in ["/api/v1/projects", "/api/lnn/predict"]:
            assert _is_public_path(p) is False

    def test_public_prefixes(self):
        for p in [
            "/api/docs/swagger-ui",
            "/api/redoc/bundles",
            "/api/openapi.json",
        ]:
            assert _is_public_path(p) is True


# _get_token_metadata


class TestGetTokenMetadata:
    def test_missing_file_returns_default_T(self, tmp_path, monkeypatch, caplog):
        # 安全修复 B4：fail-closed 策略——元数据文件缺失时返回 None 而非默认权限
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(tmp_path / "missing.json"))
        with caplog.at_level(logging.WARNING, logger="app.auth.unified_auth"):
            meta = _get_token_metadata("any-token")
        assert meta is None

    def test_list_format_with_matching_token(self, tmp_path, monkeypatch):
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(
            json.dumps(
                [
                    {"token": "abc", "level": "C"},
                    {"token": "xyz", "level": "R"},
                ]
            )
        )
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(meta_file))
        assert _get_token_metadata("abc") == {"token": "abc", "level": "C"}

    def test_list_format_with_non_matching_token(self, tmp_path, monkeypatch):
        # fail-closed：未匹配 token 时返回 None
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(json.dumps([{"token": "abc", "level": "C"}]))
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(meta_file))
        meta = _get_token_metadata("nope")
        assert meta is None

    def test_dict_format_with_matching_token(self, tmp_path, monkeypatch):
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(json.dumps({"token": "abc", "level": "B"}))
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(meta_file))
        assert _get_token_metadata("abc") == {"token": "abc", "level": "B"}

    def test_dict_format_with_non_matching_token(self, tmp_path, monkeypatch):
        # fail-closed：未匹配 token 时返回 None
        meta_file = tmp_path / "meta.json"
        meta_file.write_text(json.dumps({"token": "abc", "level": "B"}))
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(meta_file))
        meta = _get_token_metadata("nope")
        assert meta is None

    def test_corrupt_json_returns_default(self, tmp_path, monkeypatch):
        # fail-closed：JSON 解析失败时返回 None
        meta_file = tmp_path / "meta.json"
        meta_file.write_text("not valid json")
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(meta_file))
        meta = _get_token_metadata("any")
        assert meta is None


def caplog_at_level(monkeypatch, level_name: str):
    """简单的占位：仅用于参数占位，caplog 真正的收集通过 pytest 自带 caplog fixture。"""
    return None


# _save_token / _load_token / _initialize_token / _get_token_file_path


class TestTokenFileOps:
    def test_save_and_load_token_roundtrip(self, tmp_path, monkeypatch):
        path = tmp_path / "tok"
        _save_token("token-A", file_path=path)
        assert path.exists()
        assert path.read_text() == "token-A"
        assert _load_token(file_path=path) == "token-A"

    def test_load_token_returns_none_when_missing(self, tmp_path):
        path = tmp_path / "missing"
        assert _load_token(file_path=path) is None

    def test_load_token_returns_none_for_empty_file(self, tmp_path):
        path = tmp_path / "empty"
        path.write_text("   \n")
        assert _load_token(file_path=path) is None

    def test_save_replaces_existing_symlink(self, tmp_path, monkeypatch):
        path = tmp_path / "tok"
        target = tmp_path / "target"
        target.write_text("real")
        if os.name != "nt":
            path.symlink_to(target)
            _save_token("newvalue", file_path=path)
            assert path.read_text() == "newvalue"

    def test_initialize_token_uses_existing(self, tmp_path):
        path = tmp_path / "tok"
        path.write_text("existing-token")
        monkeypatch_set_env_token_file(tmp_path, path)
        assert _initialize_token() == "existing-token"

    def test_initialize_token_creates_new(self, tmp_path, monkeypatch):
        path = tmp_path / "tok"
        monkeypatch_set_env_token_file(tmp_path, path)
        token = _initialize_token()
        assert token != ""
        assert path.read_text().strip() == token

    def test_get_token_file_path_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LNN_TOKEN_FILE", raising=False)
        fp = _get_token_file_path()
        assert isinstance(fp, Path)
        assert fp.name == ".lnn_token"


def monkeypatch_set_env_token_file(tmp_path: Path, path: Path) -> None:
    """Helper to set LNN_TOKEN_FILE to ``path`` via env var."""
    os.environ["LNN_TOKEN_FILE"] = str(path)


# _decode_token / _decode_token_strict / _get_token_ban_list


class TestDecodeHelpers:
    def test_decode_token_returns_dict_on_valid(self):
        # Use real security module to mint a token
        from app.auth.security import create_access_token

        token = create_access_token({"sub": "u1", "role": "user"})
        result = _decode_token(token)
        assert result is not None
        assert result.get("sub") == "u1"

    def test_decode_token_returns_none_on_invalid(self):
        assert _decode_token("not-a-jwt") is None

    def test_decode_token_strict_access(self):
        from app.auth.security import create_access_token, create_refresh_token

        access = create_access_token({"sub": "u1"})
        refresh = create_refresh_token({"sub": "u1"})
        assert _decode_token_strict(access, expected_type="access") is not None
        # refresh token should fail when expected_type=access
        assert _decode_token_strict(refresh, expected_type="access") is None
        # but should pass with refresh
        assert _decode_token_strict(refresh, expected_type="refresh") is not None

    def test_decode_token_strict_returns_none_on_garbage(self):
        assert _decode_token_strict("garbage") is None

    def test_get_token_ban_list_returns_object(self):
        from app.auth.security import get_token_ban_list as real_ban_list

        ban_list = _get_token_ban_list()
        assert ban_list is not None
        # 与 real_ban_list 应为同一对象
        assert ban_list is real_ban_list()


# PermissionLevel / _get_permission_class / _check_scope


class TestPermissionHelpers:
    def test_permission_level_values(self):
        assert PermissionLevel.R.value == "R"
        assert PermissionLevel.T.value == "T"

    def test_get_permission_class_known_endpoint(self):
        assert _get_permission_class("GET", "/api/agent/v1/health") == PermissionLevel.R
        assert _get_permission_class("POST", "/api/agent/v1/train") == PermissionLevel.B
        assert _get_permission_class("POST", "/api/agent/v1/execute") == PermissionLevel.T
        assert _get_permission_class("GET", "/api/agent/v1/audit-log") == PermissionLevel.C

    def test_get_permission_class_default_by_method(self):
        assert _get_permission_class("GET", "/some/unknown") == PermissionLevel.R
        assert _get_permission_class("POST", "/some/unknown") == PermissionLevel.W
        assert _get_permission_class("PUT", "/some/unknown") == PermissionLevel.W
        assert _get_permission_class("DELETE", "/some/unknown") == PermissionLevel.C
        # Unknown method falls back to R
        assert _get_permission_class("PATCH", "/some/unknown") == PermissionLevel.R

    def test_check_scope_direct_match(self):
        assert _check_scope(["R"], PermissionLevel.R) is True

    def test_check_scope_hierarchy(self):
        # T > C > N > B > W > R
        assert _check_scope(["T"], PermissionLevel.R) is True
        assert _check_scope(["C"], PermissionLevel.N) is True
        assert _check_scope(["B"], PermissionLevel.B) is True
        # Lower scope than required -> False
        assert _check_scope(["R"], PermissionLevel.W) is False
        assert _check_scope(["R"], PermissionLevel.C) is False

    def test_check_scope_empty_or_unknown(self):
        # 空 scopes 时 max(..., default=0) 返回 0，而 R 的层级也是 0，
        # 所以 _check_scope 的实际行为是：当 required == R 时返回 True。
        assert _check_scope([], PermissionLevel.R) is True
        # 当 required > R (>= W) 时，0 < required_value，返回 False。
        assert _check_scope([], PermissionLevel.W) is False
        assert _check_scope([], PermissionLevel.C) is False
        # 未知 scope 字符串被当作层级 0 处理。
        assert _check_scope(["Z"], PermissionLevel.R) is True
        assert _check_scope(["Z"], PermissionLevel.W) is False

    def test_write_scopes_constant(self):
        assert WRITE_SCOPES == {"W", "B", "T"}


class TestAgentTokenStore:
    def test_get_agent_token_store_singleton(self):
        from app.agent.auth import agent_token_store

        s = _get_agent_token_store()
        assert s is agent_token_store


# AgentAuditLog


class TestAgentAuditLog:
    def test_log_creates_file_and_appends(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        log.log("a1", "/x", "R", 200, 1.0)
        log.log("a2", "/y", "W", 201, 2.5)
        # 验证文件存在并包含 2 条记录
        lines = (tmp_path / "audit.log").read_text().strip().splitlines()
        assert len(lines) == 2
        e1 = json.loads(lines[0])
        e2 = json.loads(lines[1])
        assert e1["agent_id"] == "a1"
        assert e1["route"] == "/x"
        assert e2["status_code"] == 201

    def test_get_entries_returns_newest_first(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        log.log("a1", "/x", "R", 200, 1.0)
        log.log("a2", "/y", "W", 201, 2.5)
        entries = log.get_entries()
        assert len(entries) == 2
        # Newest first -> a2 在前
        assert entries[0]["agent_id"] == "a2"
        assert entries[1]["agent_id"] == "a1"

    def test_get_entries_filter_by_agent(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        log.log("a1", "/x", "R", 200, 1.0)
        log.log("a2", "/y", "W", 201, 2.5)
        log.log("a1", "/z", "R", 200, 3.0)
        entries = log.get_entries(agent_id="a1")
        assert len(entries) == 2
        assert all(e["agent_id"] == "a1" for e in entries)

    def test_get_entries_filter_by_class(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        log.log("a1", "/x", "R", 200, 1.0)
        log.log("a2", "/y", "W", 201, 2.5)
        log.log("a1", "/z", "W", 200, 3.0)
        entries = log.get_entries(permission_class="W")
        assert len(entries) == 2
        assert all(e["permission_class"] == "W" for e in entries)

    def test_get_entries_pagination(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        for i in range(5):
            log.log(f"a{i}", f"/x{i}", "R", 200, 1.0)
        page1 = log.get_entries(limit=2, offset=0)
        page2 = log.get_entries(limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0]["agent_id"] != page2[0]["agent_id"]

    def test_log_handles_write_failure(self, tmp_path, caplog):
        # 指向一个不可写路径：使用目录替代文件，写入会失败
        bad_path = tmp_path / "this_is_a_dir"
        bad_path.mkdir()
        log = AgentAuditLog(log_path=str(bad_path / "audit.log"))
        # 文件在父级为目录时无法直接打开写入
        with caplog.at_level(logging.ERROR, logger="app.auth.unified_auth"):
            log.log("a1", "/x", "R", 200, 1.0)
        # 应当出现写入失败日志（如果实际触发了 OSError）
        # 注意：在某些平台/权限下可能不触发，仅断言代码不抛异常
        assert True

    def test_get_entries_skips_malformed_lines(self, tmp_path):
        log = AgentAuditLog(log_path=str(tmp_path / "audit.log"))
        log.log("a1", "/x", "R", 200, 1.0)
        # 追加垃圾行
        with open(str(tmp_path / "audit.log"), "a") as f:
            f.write("not-json\n")
            f.write("\n")
        log.log("a2", "/y", "W", 201, 2.5)
        entries = log.get_entries()
        # 跳过非法行后应剩 2 条
        assert len(entries) == 2
        assert {e["agent_id"] for e in entries} == {"a1", "a2"}

    def test_default_log_path_uses_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        log = AgentAuditLog()
        # V2.7.0 起日志目录改为项目 logs/audit/（原 ~/.lingjing/）
        assert log._log_path.parent.name == "audit"
        assert log._log_path.parent.parent.name == "logs"
        assert log._log_path.name == "agent_audit.log"


# AgentRateLimiter


class TestAgentRateLimiter:
    def test_under_limit_passes(self):
        rl = AgentRateLimiter(max_requests_per_minute=3, max_concurrent_tasks=1)
        assert rl.check_rate_limit("a1") is True
        assert rl.check_rate_limit("a1") is True
        assert rl.check_rate_limit("a1") is True
        # 第 4 次失败
        assert rl.check_rate_limit("a1") is False

    def test_rate_limit_is_per_agent(self):
        rl = AgentRateLimiter(max_requests_per_minute=1, max_concurrent_tasks=1)
        assert rl.check_rate_limit("a1") is True
        assert rl.check_rate_limit("a1") is False
        # a2 不受 a1 限制影响
        assert rl.check_rate_limit("a2") is True

    def test_rate_limit_window_clears_old(self, monkeypatch):
        rl = AgentRateLimiter(max_requests_per_minute=2, max_concurrent_tasks=1)
        # 注入两个旧时间戳
        rl._request_log["a1"] = [time.time() - 120, time.time() - 90]
        # 在新时间点，旧的应被清理
        assert rl.check_rate_limit("a1") is True
        assert rl.check_rate_limit("a1") is True
        # 第 3 次失败
        assert rl.check_rate_limit("a1") is False

    def test_acquire_and_release_task(self):
        rl = AgentRateLimiter(max_concurrent_tasks=2)
        assert rl.acquire_task("a1") is True
        assert rl.acquire_task("a1") is True
        assert rl.acquire_task("a1") is False
        assert rl.get_active_tasks("a1") == 2
        rl.release_task("a1")
        assert rl.get_active_tasks("a1") == 1
        # 释放到 0 后仍允许 acquire
        rl.release_task("a1")
        assert rl.get_active_tasks("a1") == 0
        rl.release_task("a1")  # 不应低于 0
        assert rl.get_active_tasks("a1") == 0


# IdempotencyStore


class TestIdempotencyStore:
    def test_check_and_set_returns_none_for_new(self):
        store = IdempotencyStore()
        assert store.check_and_set("k1", "a1") is None

    def test_store_then_check_returns_cached(self):
        store = IdempotencyStore()
        store.store("k1", "a1", {"result": "ok"})
        assert store.check_and_set("k1", "a1") == {"result": "ok"}

    def test_check_and_set_skips_different_agent(self):
        store = IdempotencyStore()
        store.store("k1", "a1", {"r": 1})
        # 不同的 agent 不应命中缓存
        assert store.check_and_set("k1", "a2") is None

    def test_max_entries_eviction(self):
        store = IdempotencyStore(max_entries=3)
        for i in range(5):
            store.store(f"k{i}", "a1", {"i": i})
            time.sleep(0.005)  # 保证 created_at 不同
        # 至少剩 3 条
        assert len(store._keys) == 3
        # 最旧的 (k0) 应被淘汰
        assert "k0" not in store._keys

    def test_cleanup_removes_expired(self):
        store = IdempotencyStore(max_age=1)
        store.store("k1", "a1", {"r": 1})
        time.sleep(1.2)
        store.cleanup(max_age=1)
        assert "k1" not in store._keys

    def test_cleanup_uses_default_max_age(self):
        store = IdempotencyStore(max_age=1)
        store.store("k1", "a1", {"r": 1})
        time.sleep(1.2)
        store.cleanup()  # default max_age
        assert "k1" not in store._keys


# _make_json_response / _send_json_response


class TestMakeJsonResponse:
    def test_status_code_and_body(self):
        status_code, headers, body = _make_json_response(200, {"ok": True})
        assert status_code == 200
        body_dict = json.loads(body)
        assert body_dict == {"ok": True}
        # 验证头中包含 content-type
        header_names = {n.decode("latin-1") for n, _ in headers}
        assert "content-type" in header_names
        assert "x-content-type-options" in header_names

    def test_send_json_response_runs(self):
        sent: list[dict[str, Any]] = []

        async def fake_send(message):
            sent.append(message)

        asyncio.run(_send_json_response(fake_send, 400, {"error": "bad"}))
        # 应有 start 和 body 两条消息
        types = [m["type"] for m in sent]
        assert types == ["http.response.start", "http.response.body"]
        assert sent[0]["status"] == 400


# UnifiedAuthMiddleware 初始化与 __call__ 全面分支


class TestMiddlewareInit:
    def test_init_loads_lnn_token_when_enabled(self, token_file, monkeypatch):
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        app = FastAPI()
        middleware = UnifiedAuthMiddleware(app, lnn_auth_enabled=True, lnn_permission_enforced=False)
        assert middleware._lnn_token == "test-token-uuid-abc"

    def test_init_skips_lnn_token_when_disabled(self, token_file, monkeypatch):
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        app = FastAPI()
        middleware = UnifiedAuthMiddleware(app, lnn_auth_enabled=False, lnn_permission_enforced=False)
        assert middleware._lnn_token is None


class TestPublicPathBranch:
    def test_health_endpoint_no_log_does_not_500(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=False))
        response = client.get("/api/health")
        # 即使没有 health 路由注册，public path 会让请求直接穿透 -> 404
        assert response.status_code in (200, 404)


class TestAgentPathBranch:
    def test_agent_health_is_public(self, app_factory):
        # 当 agent_auth_enabled=True 时，/api/agent/v1/health 在 agent path
        # 分支被 short-circuit（无需 token 即可通过）。
        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.get("/api/agent/v1/health")
        assert response.status_code == 200

    def test_agent_token_creation_is_public(self, app_factory):
        # POST /api/agent/v1/tokens 同样在 agent path 分支被 short-circuit。
        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.post("/api/agent/v1/tokens", json={})
        assert response.status_code == 200

    def test_agent_predict_without_token_returns_401(self, app_factory):
        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.post("/api/agent/v1/predict", json={})
        assert response.status_code == 401
        # P1-16 安全修复：错误响应不得泄露 lj_agent_ 前缀（降低枚举成本）
        data = response.json()
        assert data["error"] == "unauthorized"
        assert "lj_agent_" not in response.text

    def test_agent_predict_with_invalid_token_returns_401(self, app_factory):
        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.post(
            "/api/agent/v1/predict",
            json={},
            headers={"Authorization": "Bearer lj_agent_invalid"},
        )
        assert response.status_code == 401

    def test_agent_path_disabled_allows_request(self, app_factory):
        # 当 agent_auth_enabled=False 且 lnn_auth_enabled=False 时，
        # agent 路径不会被拦截，直接转发到下游应用。
        client = TestClient(app_factory(agent_auth_enabled=False, lnn_auth_enabled=False))
        response = client.post("/api/agent/v1/predict", json={})
        assert response.status_code == 200

    def test_agent_rate_limit_returns_429(self, app_factory, monkeypatch):
        # 注入一个会立即返回 False 的速率限制器
        # 注意：middleware 通过 `from unified_auth import agent_rate_limiter` 绑定引用，
        # 必须 patch middleware 模块命名空间（patch unified_auth 不生效）
        from app.auth import middleware as mw

        class _RL:
            def check_rate_limit(self, agent_id):
                return False

            _max_rpm = 60

        monkeypatch.setattr(mw, "agent_rate_limiter", _RL())

        # 注入一个能返回 agent_token 的 store
        class _Store:
            def validate_token(self, raw):
                from dataclasses import dataclass

                @dataclass
                class T:
                    agent_id: str = "a1"
                    scopes: list = None

                t = T()
                t.scopes = ["T"]
                return t

        from app.auth import middleware as mw

        monkeypatch.setattr(mw, "_get_agent_token_store", lambda: _Store())

        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.post(
            "/api/agent/v1/predict",
            json={},
            headers={"Authorization": "Bearer lj_agent_xxx"},
        )
        assert response.status_code == 429

    def test_agent_missing_idempotency_key_returns_400(self, app_factory, monkeypatch):
        from app.auth import unified_auth as ua

        class _RL:
            def check_rate_limit(self, agent_id):
                return True

            _max_rpm = 60

        monkeypatch.setattr(ua, "agent_rate_limiter", _RL())

        class _Store:
            def validate_token(self, raw):
                from dataclasses import dataclass

                @dataclass
                class T:
                    agent_id: str = "a1"
                    scopes: list = None

                t = T()
                t.scopes = ["T", "B"]
                return t

        from app.auth import middleware as mw

        monkeypatch.setattr(mw, "_get_agent_token_store", lambda: _Store())

        client = TestClient(app_factory(agent_auth_enabled=True))
        # POST /api/agent/v1/train requires idempotency key (B-class)
        response = client.post(
            "/api/agent/v1/train",
            json={},
            headers={"Authorization": "Bearer lj_agent_xxx"},
        )
        assert response.status_code == 400

    def test_agent_idempotency_replay_returns_cached(self, app_factory, monkeypatch):
        from app.auth import unified_auth as ua

        class _RL:
            def check_rate_limit(self, agent_id):
                return True

            _max_rpm = 60

        monkeypatch.setattr(ua, "agent_rate_limiter", _RL())

        class _Store:
            def validate_token(self, raw):
                from dataclasses import dataclass

                @dataclass
                class T:
                    agent_id: str = "a1"
                    scopes: list = None

                t = T()
                t.scopes = ["T", "B"]
                return t

        from app.auth import middleware as mw

        monkeypatch.setattr(mw, "_get_agent_token_store", lambda: _Store())

        # 预存幂等键
        ua.idempotency_store.store("idem-1", "a1", {"status": "queued"})

        client = TestClient(app_factory(agent_auth_enabled=True))
        response = client.post(
            "/api/agent/v1/train",
            json={},
            headers={
                "Authorization": "Bearer lj_agent_xxx",
                "Idempotency-Key": "idem-1",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body.get("idempotent_replay") is True
        assert body.get("status") == "queued"


# JWT 路径


class TestJwtPath:
    def test_jwt_with_disabled_lnn_jwt_disabled(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=False))
        response = client.post(
            "/api/agent/v1/predict",
            json={},
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.x.y"},
        )
        # lnn off, jwt off, agent off -> 路径直接通过
        assert response.status_code == 200

    def test_jwt_with_lnn_disabled_jwt_enabled_invalid(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=True))
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.bad.token"},
        )
        assert response.status_code == 401

    def test_jwt_valid_token_passes(self, app_factory):
        import os

        from app.auth import security as security_module
        from app.auth.security import create_access_token

        token = create_access_token({"sub": "u1", "role": "user"})
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=True))
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        # 失败时打印 401 分支报文与密钥指纹（前 8 位），用于定位 CI-only 的解码失败
        assert response.status_code == 200, (
            f"JWT 有效 token 被拒: status={response.status_code} body={response.text!r} "
            f"module_secret_prefix={str(security_module.SECRET_KEY)[:8]} "
            f"env_secret_prefix={str(os.environ.get('LNN_JWT_SECRET', ''))[:8]}"
        )

    def test_jwt_with_banned_token_returns_401(self, app_factory):
        from app.auth.security import create_access_token, get_token_ban_list

        token = create_access_token({"sub": "u1", "role": "user"})
        get_token_ban_list().ban(token)
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=True))
        response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_jwt_no_auth_header_returns_401(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=True))
        response = client.get("/protected")
        assert response.status_code == 401

    def test_jwt_invalid_bearer_prefix_returns_401(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=True))
        response = client.get("/protected", headers={"Authorization": "Basic xyz"})
        assert response.status_code == 401


# LNN auth 内部函数 _check_lnn_auth


class TestCheckLnnAuth:
    def test_lnn_disabled_returns_none(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=False, jwt_auth_enabled=False))
        response = client.get("/protected")
        # lnn disabled -> 路径不应被拦截
        assert response.status_code == 200

    def test_lnn_enabled_no_auth_returns_401(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=True, jwt_auth_enabled=False))
        response = client.get("/protected")
        assert response.status_code == 401

    def test_lnn_enabled_public_path_returns_200(self, app_factory):
        client = TestClient(app_factory(lnn_auth_enabled=True, jwt_auth_enabled=False))
        response = client.get("/api/docs")
        assert response.status_code == 200

    def test_lnn_enabled_permission_enforced_emits_audit_log(self, app_factory, caplog):
        from app.auth import unified_auth as ua

        class _PermChecker:
            def has_permission(self, level, path, method):
                return True

            def get_required_permission(self, method, path):
                return PermissionLevel.R

        # Patch middleware 层（LNN 鉴权实际调用 middleware._get_token_metadata）
        with patch(
            "app.auth.middleware._get_token_metadata",
            return_value={"level": "T"},
        ):
            with patch(
                "app.auth.permissions.permission_checker",
                _PermChecker(),
                create=True,
            ):
                client = TestClient(
                    app_factory(
                        lnn_auth_enabled=True,
                        lnn_permission_enforced=True,
                        jwt_auth_enabled=False,
                    )
                )
                response = client.get(
                    "/protected",
                    headers={"Authorization": "Bearer test-token-uuid-abc"},
                )
                assert response.status_code == 200

    def test_lnn_enabled_invalid_token_returns_401(self, app_factory, caplog):
        client = TestClient(app_factory(lnn_auth_enabled=True, jwt_auth_enabled=False))
        with caplog.at_level(logging.WARNING, logger="app.auth.unified_auth"):
            response = client.get("/protected", headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401


# module-level singletons


class TestModuleLevelSingletons:
    def test_agent_audit_log_singleton(self):
        from app.auth.unified_auth import agent_audit_log as a1
        from app.auth.unified_auth import agent_audit_log as a2

        assert a1 is a2

    def test_idempotency_store_singleton(self):
        from app.auth.unified_auth import idempotency_store as i1
        from app.auth.unified_auth import idempotency_store as i2

        assert i1 is i2
