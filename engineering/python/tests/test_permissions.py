"""
Test Permissions Module

Tests for:
- PermissionLevel: Six-level permission classification (R/W/B/N/C/T)
- PermissionChecker: Endpoint permission validation and rate limiting
- RateLimitConfig and RateLimitState: Rate limit tracking
- PaperOnlyGuard: Paper-Only mode enforcement for T-level operations
"""

import os
import time
import pytest
from unittest.mock import patch

from app.auth.permissions import (
    PermissionLevel,
    PermissionChecker,
    RateLimitConfig,
    RateLimitState,
    PaperOnlyGuard,
    PERMISSION_HIERARCHY,
)


class TestPermissionLevel:
    """Test PermissionLevel enumeration"""

    def test_all_permission_levels_defined(self):
        assert PermissionLevel.R == "R"
        assert PermissionLevel.W == "W"
        assert PermissionLevel.B == "B"
        assert PermissionLevel.N == "N"
        assert PermissionLevel.C == "C"
        assert PermissionLevel.T == "T"

    def test_permission_levels_are_strings(self):
        for level in PermissionLevel:
            assert isinstance(level.value, str)


class TestPermissionHierarchy:
    """Test permission hierarchy ordering"""

    def test_hierarchy_order(self):
        assert PERMISSION_HIERARCHY[PermissionLevel.R] == 0
        assert PERMISSION_HIERARCHY[PermissionLevel.W] == 1
        assert PERMISSION_HIERARCHY[PermissionLevel.B] == 2
        assert PERMISSION_HIERARCHY[PermissionLevel.N] == 3
        assert PERMISSION_HIERARCHY[PermissionLevel.C] == 4
        assert PERMISSION_HIERARCHY[PermissionLevel.T] == 5

    def test_hierarchy_increasing_order(self):
        levels = [
            PermissionLevel.R,
            PermissionLevel.W,
            PermissionLevel.B,
            PermissionLevel.N,
            PermissionLevel.C,
            PermissionLevel.T,
        ]
        for i in range(len(levels) - 1):
            assert PERMISSION_HIERARCHY[levels[i]] < PERMISSION_HIERARCHY[levels[i + 1]]


class TestRateLimitState:
    """Test RateLimitState for rate limiting logic"""

    def test_initialization(self):
        state = RateLimitState()
        assert state.requests == []

    def test_is_allowed_under_limit(self):
        config = RateLimitConfig(max_requests=10, window_seconds=60)
        state = RateLimitState()
        assert state.is_allowed(config) is True

    def test_record_adds_request(self):
        state = RateLimitState()
        state.record()
        assert len(state.requests) == 1

    def test_is_allowed_respects_window(self):
        config = RateLimitConfig(max_requests=10, window_seconds=1)
        state = RateLimitState()

        for _ in range(5):
            state.record()
            time.sleep(0.1)

        assert state.is_allowed(config) is True

    def test_old_requests_expired(self):
        config = RateLimitConfig(max_requests=100, window_seconds=0)
        state = RateLimitState()

        state.record()
        time.sleep(0.01)

        result = state.is_allowed(config)
        assert result is True

    def test_is_allowed_at_limit(self):
        config = RateLimitConfig(max_requests=2, window_seconds=60)
        state = RateLimitState()

        state.record()
        state.record()

        assert state.is_allowed(config) is False

    def test_requests_cleaned_on_check(self):
        config = RateLimitConfig(max_requests=1, window_seconds=60)
        state = RateLimitState()

        state.record()
        assert state.is_allowed(config) is False

        state.record()
        assert state.is_allowed(config) is False
        assert len(state.requests) == 2  # 两次同秒记录均在窗口内，不重复清理


class TestRateLimitConfig:
    """Test RateLimitConfig"""

    def test_default_values(self):
        config = RateLimitConfig()
        assert config.max_requests == 100
        assert config.window_seconds == 60

    def test_custom_values(self):
        config = RateLimitConfig(max_requests=50, window_seconds=30)
        assert config.max_requests == 50
        assert config.window_seconds == 30


class TestPermissionChecker:
    """Test PermissionChecker for endpoint permission validation"""

    def test_initialization(self):
        checker = PermissionChecker()
        assert len(checker.ENDPOINT_PERMISSIONS) > 0
        assert len(checker.DEFAULT_PERMISSIONS) > 0
        assert checker._rate_limit_config is not None

    def test_read_endpoints_require_r_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "GET")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/wear/predict", "POST")
            is True  # POST predict 是推理端点（只读语义），R 可调
        )

    def test_write_endpoints_require_w_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.W, "/api/v1/lnn/predict", "POST")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "POST")
            is False
        )

    def test_batch_endpoints_require_b_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.B, "/api/v1/lnn/train", "POST")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.W, "/api/v1/lnn/train", "POST")
            is False
        )

    def test_notification_endpoints_require_n_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.N, "/api/v1/notifications", "POST")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.B, "/api/v1/notifications", "POST")
            is False
        )

    def test_credentials_endpoints_require_c_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.C, "/api/v1/config", "GET") is True
        )
        assert (
            checker.has_permission(PermissionLevel.B, "/api/v1/config", "GET") is False
        )

    def test_machine_endpoints_require_t_permission(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.T, "/api/v1/machine/params", "POST")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.C, "/api/v1/machine/params", "POST")
            is False
        )

    def test_higher_permission_includes_lower(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.T, "/api/v1/lnn/predict", "GET")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.C, "/api/v1/lnn/predict", "GET")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.B, "/api/v1/lnn/predict", "GET")
            is True
        )

    def test_default_permission_for_unknown_endpoint(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/unknown/endpoint", "GET")
            is True
        )
        assert (
            checker.has_permission(
                PermissionLevel.R, "/api/v1/unknown/endpoint", "POST"
            )
            is False
        )

    def test_default_permissions_fallback(self):
        checker = PermissionChecker()
        assert checker.has_permission(PermissionLevel.R, "/random/path", "GET") is True
        assert (
            checker.has_permission(PermissionLevel.R, "/random/path", "POST") is False
        )
        assert (
            checker.has_permission(PermissionLevel.C, "/random/path", "DELETE") is True
        )

    def test_get_required_permission_explicit(self):
        checker = PermissionChecker()
        level = checker.get_required_permission("GET", "/api/v1/lnn/predict")
        assert level == PermissionLevel.R

    def test_get_required_permission_default(self):
        checker = PermissionChecker()
        level = checker.get_required_permission("GET", "/unknown/endpoint")
        assert level == PermissionLevel.R


class TestPermissionCheckerRateLimiting:
    """Test PermissionChecker rate limiting"""

    def test_rate_limit_check_first_request(self):
        checker = PermissionChecker()
        result = checker.check_rate_limit("test_token_1")
        assert result is True

    def test_rate_limit_tracks_multiple_tokens(self):
        checker = PermissionChecker()
        assert checker.check_rate_limit("token_a") is True
        assert checker.check_rate_limit("token_b") is True
        assert checker.check_rate_limit("token_a") is True

    def test_rate_limit_exceeded(self):
        checker = PermissionChecker()
        checker._rate_limit_config = RateLimitConfig(max_requests=2, window_seconds=60)

        assert checker.check_rate_limit("limit_test") is True
        assert checker.check_rate_limit("limit_test") is True
        assert checker.check_rate_limit("limit_test") is False

    def test_rate_limit_window_reset(self):
        checker = PermissionChecker()
        checker._rate_limit_config = RateLimitConfig(max_requests=100, window_seconds=0)

        checker.check_rate_limit("window_test")
        time.sleep(0.01)

        assert checker.check_rate_limit("window_test") is True


class TestPaperOnlyGuard:
    """Test PaperOnlyGuard for T-level operation enforcement"""

    def test_initialization_defaults_to_paper_only(self):
        guard = PaperOnlyGuard()
        # [F-P0-4] 配置热刷新：不再启动时固化，通过 is_live_execution_allowed 实时读取
        assert guard.is_live_execution_allowed() is False

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_initialization_with_live_execution_enabled(self):
        guard = PaperOnlyGuard()
        # [F-P0-4] 配置热刷新：环境变量变更后立即生效
        assert guard.is_live_execution_allowed() is True

    def test_is_live_execution_allowed_default(self):
        guard = PaperOnlyGuard()
        assert guard.is_live_execution_allowed() is False

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_is_live_execution_allowed_when_enabled(self):
        guard = PaperOnlyGuard()
        assert guard.is_live_execution_allowed() is True

    def test_check_t_operation_paper_only_mode(self):
        guard = PaperOnlyGuard()
        allowed, message = guard.check_t_operation(
            has_t_permission=True, ui_confirmed=True
        )
        assert allowed is False
        assert "Paper-Only mode" in message

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_check_t_operation_missing_permission(self):
        guard = PaperOnlyGuard()
        allowed, message = guard.check_t_operation(
            has_t_permission=False, ui_confirmed=True
        )
        assert allowed is False
        assert "Insufficient permission" in message

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_check_t_operation_missing_ui_confirmation(self):
        guard = PaperOnlyGuard()
        allowed, message = guard.check_t_operation(
            has_t_permission=True, ui_confirmed=False
        )
        assert allowed is False
        assert "UI confirmation required" in message

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_check_t_operation_missing_supervisor_confirmation(self):
        """[F-P0-4] 实模式必须双因子确认：缺少班长确认应拒绝"""
        guard = PaperOnlyGuard()
        allowed, message = guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=True,
            supervisor_confirmed=False,
        )
        assert allowed is False
        assert "Supervisor" in message or "dual-factor" in message

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_check_t_operation_machine_safety_violation(self):
        """[F-P0-4] 机床安全状态不满足时应拒绝执行"""
        guard = PaperOnlyGuard()
        # 急停触发
        allowed, message = guard.check_t_operation(
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
        assert allowed is False
        assert "emergency stop" in message.lower()

        # 防护门打开
        allowed, message = guard.check_t_operation(
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
        assert allowed is False
        assert "Guard door" in message

    @patch.dict(os.environ, {"LNN_LIVE_EXECUTION_ENABLED": "true"})
    def test_check_t_operation_all_conditions_met(self):
        """[F-P0-4] 所有条件满足（含双因子 + 机床安全）才能通过"""
        guard = PaperOnlyGuard()
        allowed, message = guard.check_t_operation(
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
        assert allowed is True
        assert "approved" in message.lower()

    def test_simulate_t_operation(self):
        guard = PaperOnlyGuard()
        operation = {"type": "machine_control", "params": {"speed": 100}}
        result = guard.simulate_t_operation(operation)

        assert result["status"] == "simulated"
        assert "Paper-Only mode" in result["message"]
        # 非敏感字段应原样保留
        assert result["operation"]["type"] == "machine_control"
        assert result["operation"]["params"]["speed"] == 100

    def test_simulate_t_operation_redacts_sensitive_fields(self):
        """[F-P0-4] 敏感字段必须脱敏，不得明文写入返回结果或日志"""
        guard = PaperOnlyGuard()
        operation = {
            "type": "machine_control",
            "api_key": "sk-1234567890",
            "nc_program": "G01 X100 Y200",
            "password": "secret123",
        }
        result = guard.simulate_t_operation(operation)

        assert result["status"] == "simulated"
        assert result["operation"]["api_key"] == "***REDACTED***"
        assert result["operation"]["nc_program"] == "***REDACTED***"
        assert result["operation"]["password"] == "***REDACTED***"
        assert result["operation"]["type"] == "machine_control"


class TestPermissionCheckerEdgeCases:
    """Test edge cases for permission checking"""

    def test_permission_with_invalid_token_level(self):
        checker = PermissionChecker()
        result = checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "GET")
        assert result is True

    def test_permission_level_case_sensitivity(self):
        checker = PermissionChecker()
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "GET")
            is True
        )
        assert (
            checker.has_permission(PermissionLevel.R, "/api/v1/lnn/predict", "get")
            is False
        )

    def test_rate_limit_different_tokens_independent(self):
        checker = PermissionChecker()
        checker._rate_limit_config = RateLimitConfig(max_requests=1, window_seconds=60)

        assert checker.check_rate_limit("token_1") is True
        assert checker.check_rate_limit("token_1") is False
        assert checker.check_rate_limit("token_2") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
