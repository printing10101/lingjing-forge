"""权限/RBAC 安全测试。

覆盖内容：

- ``PermissionLevel`` 枚举与 ``PERMISSION_HIERARCHY`` 顺序
- ``PermissionChecker`` 端点级权限映射：R/W/B/N/C/T 各等级
- 未显式配置的端点使用默认策略（GET→R, POST/PUT→W, DELETE→C）
- ``PaperOnlyGuard`` 对 T 级操作的"纸面模式"强制约束
- ``RateLimitState`` 速率限制状态机：窗口滑动 / 超限拒绝
- ``permission_required`` 装饰器签名与元数据保留
"""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException

from app.auth.permissions import (
    PERMISSION_HIERARCHY,
    PaperOnlyGuard,
    PermissionChecker,
    PermissionLevel,
    RateLimitConfig,
    RateLimitState,
    permission_required,
    require_permission,
)


# 枚举与常量


class TestPermissionEnums:
    def test_all_levels_present(self):
        levels = {level.value for level in PermissionLevel}
        assert {"R", "W", "B", "N", "C", "T"} <= levels

    def test_hierarchy_strictly_ordered(self):
        # 数字越大，权限越高
        ordered = sorted(PERMISSION_HIERARCHY.values())
        assert ordered == list(range(len(PERMISSION_HIERARCHY)))

    def test_hierarchy_keys_match_levels(self):
        assert set(PERMISSION_HIERARCHY.keys()) == set(PermissionLevel)

    def test_T_is_highest(self):
        assert PERMISSION_HIERARCHY[PermissionLevel.T] == max(PERMISSION_HIERARCHY.values())

    def test_R_is_lowest(self):
        assert PERMISSION_HIERARCHY[PermissionLevel.R] == min(PERMISSION_HIERARCHY.values())


# PermissionChecker - 端点级权限检查


class TestEndpointPermissions:
    @pytest.fixture
    def checker(self) -> PermissionChecker:
        return PermissionChecker()

    def test_read_endpoint_allowed_for_R(self, checker):
        # predict, list, info 等读端点显式映射为 R
        level = checker.get_required_permission("GET", "/api/v1/lnn/predict")
        assert level == PermissionLevel.R

    def test_unknown_endpoint_uses_default(self, checker):
        # 未在 ENDPOINT_PERMISSIONS 中注册的端点应使用默认策略
        # 默认：GETR, POSTW, DELETEC
        assert checker.get_required_permission("GET", "/api/v1/unknown") == PermissionLevel.R
        assert checker.get_required_permission("POST", "/api/v1/unknown") == PermissionLevel.W
        assert checker.get_required_permission("DELETE", "/api/v1/unknown") == PermissionLevel.C

    def test_post_to_wear_predict_is_R(self, checker):
        level = checker.get_required_permission("POST", "/api/v1/wear/predict")
        assert level == PermissionLevel.R

    def test_admin_endpoints_require_C(self, checker):
        # 显式映射为 C 的端点必须要求 C
        for path, method in [
            ("/api/v1/api-keys", "POST"),
            ("/api/v1/api-keys/{key_id}", "DELETE"),
            ("/api/v1/config", "GET"),
            ("/api/v1/config", "PUT"),
        ]:
            level = checker.get_required_permission(method, path)
            assert level == PermissionLevel.C, f"{method} {path} 应需要 C，实际为 {level}"

    def test_T_endpoints_require_T(self, checker):
        for path, method in [
            ("/api/v1/machine/params", "POST"),
            ("/api/v1/machine/execute", "POST"),
            ("/api/v1/machine/{machine_id}/params", "PUT"),
        ]:
            level = checker.get_required_permission(method, path)
            assert level == PermissionLevel.T, f"{method} {path} 应需要 T，实际为 {level}"

    def test_B_endpoints_require_B(self, checker):
        for path, method in [
            ("/api/v1/lnn/train", "POST"),
            ("/api/v1/lnn/batch_predict", "POST"),
            ("/api/v1/wear/train", "POST"),
        ]:
            level = checker.get_required_permission(method, path)
            assert level == PermissionLevel.B

    def test_W_endpoints_require_W(self, checker):
        for path, method in [
            ("/api/v1/lnn/predict", "POST"),
            ("/api/v1/lnn/save_prediction", "POST"),
            ("/api/v1/projects", "POST"),
        ]:
            level = checker.get_required_permission(method, path)
            assert level == PermissionLevel.W

    def test_check_permission_with_sufficient_level(self, checker):
        # R 端点：持有 R 或更高权限应放行
        assert checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "GET") is True
        assert checker.has_permission(PermissionLevel.C, "/api/v1/lnn/predict", "GET") is True
        assert checker.has_permission(PermissionLevel.T, "/api/v1/lnn/predict", "GET") is True

    def test_check_permission_with_insufficient_level(self, checker):
        # T 端点：R 应当被拒
        assert checker.has_permission(PermissionLevel.R, "/api/v1/machine/execute", "POST") is False
        # C 端点：R 应当被拒
        assert checker.has_permission(PermissionLevel.R, "/api/v1/api-keys", "POST") is False

    def test_check_permission_exact_match(self, checker):
        # B 端点：B 通过，R 拒绝
        assert checker.has_permission(PermissionLevel.B, "/api/v1/lnn/train", "POST") is True
        assert checker.has_permission(PermissionLevel.R, "/api/v1/lnn/train", "POST") is False

    def test_rate_limit_default_allows(self, checker):
        # 默认配置下首次访问应通过
        assert checker.check_rate_limit("token-A") is True

    def test_rate_limit_blocks_excess(self, checker):
        # 使用小窗口/小上限的实例进行超限测试
        checker._rate_limit_config = RateLimitConfig(max_requests=2, window_seconds=60)
        assert checker.check_rate_limit("token-B") is True
        assert checker.check_rate_limit("token-B") is True
        # 第三次被拒
        assert checker.check_rate_limit("token-B") is False

    def test_rate_limit_independent_tokens(self, checker):
        checker._rate_limit_config = RateLimitConfig(max_requests=1, window_seconds=60)
        assert checker.check_rate_limit("t-1") is True
        # 不同 token 不应相互影响
        assert checker.check_rate_limit("t-2") is True


# 速率限制状态机


class TestRateLimitState:
    def test_default_config(self):
        cfg = RateLimitConfig()
        assert cfg.max_requests > 0
        assert cfg.window_seconds > 0

    def test_under_limit_allows(self):
        cfg = RateLimitConfig(max_requests=3, window_seconds=60)
        state = RateLimitState()
        for _ in range(3):
            assert state.is_allowed(cfg) is True
            state.record()

    def test_exceeding_limit_blocks(self):
        cfg = RateLimitConfig(max_requests=2, window_seconds=60)
        state = RateLimitState()
        state.record()
        state.record()
        # 第三次应被拒
        assert state.is_allowed(cfg) is False

    def test_window_slides(self, monkeypatch):
        cfg = RateLimitConfig(max_requests=2, window_seconds=1)
        state = RateLimitState()
        state.record()
        state.record()
        # 模拟时间已过窗口
        future = time.time() + 5
        monkeypatch.setattr("time.time", lambda: future)
        assert state.is_allowed(cfg) is True

    def test_independent_states(self):
        cfg = RateLimitConfig(max_requests=1, window_seconds=60)
        s1, s2 = RateLimitState(), RateLimitState()
        s1.record()
        assert s1.is_allowed(cfg) is False
        assert s2.is_allowed(cfg) is True


# PaperOnlyGuard


class TestPaperOnlyGuard:
    def test_paper_only_mode_blocks_real_dispatch(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(has_t_permission=True, ui_confirmed=True)
        assert ok is False
        assert "Paper-Only" in msg

    def test_paper_only_blocks_even_with_ui_confirmed(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(has_t_permission=True, ui_confirmed=True)
        assert ok is False
        assert "Paper-Only" in msg

    def test_live_mode_requires_t_permission(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(has_t_permission=False, ui_confirmed=True)
        assert ok is False
        assert "T-level" in msg

    def test_live_mode_requires_ui_confirmed(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(has_t_permission=True, ui_confirmed=False)
        assert ok is False
        assert "UI confirmation" in msg

    def test_live_mode_requires_supervisor_confirmation(self, monkeypatch):
        """[F-P0-4] 实模式必须双因子确认：缺少班长确认应拒绝"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=True,
            supervisor_confirmed=False,
        )
        assert ok is False
        assert "Supervisor" in msg or "dual-factor" in msg

    def test_live_mode_blocks_on_emergency_stop(self, monkeypatch):
        """[F-P0-4] 急停触发时必须阻止执行"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=True,
            supervisor_confirmed=True,
            machine_safety_status={
                "emergency_stop_active": True,
                "guard_door_closed": True,
                "light_curtain_clear": True,
                "operator_present": True,
            },
        )
        assert ok is False
        assert "emergency stop" in msg.lower()

    def test_live_mode_blocks_on_guard_door_open(self, monkeypatch):
        """[F-P0-4] 防护门打开时必须阻止执行"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=True,
            supervisor_confirmed=True,
            machine_safety_status={
                "emergency_stop_active": False,
                "guard_door_closed": False,
                "light_curtain_clear": True,
                "operator_present": True,
            },
        )
        assert ok is False
        assert "Guard door" in msg

    def test_live_mode_full_approval_passes(self, monkeypatch):
        """[F-P0-4] 所有条件满足（含双因子 + 机床安全）才能通过"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        guard = PaperOnlyGuard()
        ok, msg = guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=True,
            supervisor_confirmed=True,
            machine_safety_status={
                "emergency_stop_active": False,
                "guard_door_closed": True,
                "light_curtain_clear": True,
                "operator_present": True,
            },
        )
        assert ok is True
        assert "approved" in msg.lower()

    def test_simulate_t_operation_returns_simulated_status(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        guard = PaperOnlyGuard()
        result = guard.simulate_t_operation({"machine": "M-01", "params": {"rpm": 1200}})
        assert result["status"] == "simulated"
        assert "Paper-Only" in result["message"]
        assert result["operation"] == {
            "machine": "M-01",
            "params": {"rpm": 1200},
        }

    def test_simulate_t_operation_redacts_sensitive_fields(self, monkeypatch):
        """[F-P0-4] NC 程序、API Key 等敏感字段必须脱敏"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        guard = PaperOnlyGuard()
        result = guard.simulate_t_operation(
            {
                "machine": "M-01",
                "nc_program": "G01 X100",
                "api_key": "sk-secret",
            }
        )
        assert result["status"] == "simulated"
        assert result["operation"]["machine"] == "M-01"
        assert result["operation"]["nc_program"] == "***REDACTED***"
        assert result["operation"]["api_key"] == "***REDACTED***"

    def test_is_live_execution_allowed_reflects_env(self, monkeypatch):
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        assert PaperOnlyGuard().is_live_execution_allowed() is False

        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        assert PaperOnlyGuard().is_live_execution_allowed() is True


# permission_required 装饰器


class TestCheckPermissionDecorator:
    def test_decorator_returns_callable(self):
        @permission_required("project:read")
        def view():
            return "ok"

        assert callable(view)

    def test_decorator_preserves_metadata(self):
        @permission_required("project:read")
        def some_view():
            """docstring"""
            return 1

        # wrapped function should still have name/docstring
        assert callable(some_view)
        assert some_view.__wrapped__.__name__ == "some_view"
        assert some_view.__wrapped__.__doc__ == "docstring"

    def test_decorator_attaches_required_permission_attr(self):
        @permission_required("project:create")
        def create_view():
            return None

        # 装饰器会把所需权限记录在被装饰函数上
        assert getattr(create_view, "_required_permission") == "project:create"


# require_permission 依赖注入工厂


class TestRequirePermissionDependency:
    @pytest.fixture(autouse=True)
    def _enforce_permissions(self, monkeypatch):
        """require_permission 验证完整认证语义（401），需开启权限强制。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")

    @pytest.mark.asyncio
    async def test_requires_authentication(self):
        from starlette.requests import Request

        checker = require_permission("project:read")
        # 构造一个没有 username state 的 request
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
        }
        req = Request(scope)
        with pytest.raises(HTTPException) as exc:
            await checker(req)
        assert exc.value.status_code == 401
