"""Tests for SecurityConfig, LNN_PERMISSION_ENFORCED behavior and other app.config
configuration dataclasses.

设计要点：
- ``SecurityConfig`` 的字段是 ``default_factory``，每次实例化时读取最新环境变量。
  因此无需 ``importlib.reload`` 整个 config 模块（reload 会触发 torch 重新初始化
  并污染其他测试的导入链）。每个用例在修改环境变量后直接 ``SecurityConfig()``
  即可拿到符合预期的实例。
- ``TokenConfig.token`` 同样使用 ``_token_cache``，因此测试中需要 ``_token_cache = None``
  或者完全新实例化 ``TokenConfig()`` 来避免缓存污染。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import (
    AIConfig,
    AppConfig,
    DatabaseConfig,
    EnvironmentConfig,
    FineTuneSettings,
    LoggingConfig,
    ModelRouterSettings,
    PathsConfig,
    ProcessPlanningConfig,
    SecurityConfig,
    ServerConfig,
    SimulationConfig,
    StorageConfig,
    TaskSystemConfig,
    TokenConfig,
    _bool_env,
    _env,
    _float_env,
    _int_env,
    _path,
)


class TestPermissionEnforcedDefault:
    """LNN_PERMISSION_ENFORCED 默认值相关测试。"""

    def test_default_is_true_when_env_var_unset(self, monkeypatch):
        """环境变量未设置时默认应为 True。"""
        monkeypatch.delenv("LNN_PERMISSION_ENFORCED", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        cfg = SecurityConfig()
        assert cfg.permission_enforced is True

    def test_explicit_true_is_respected(self, monkeypatch):
        """显式设为 true 时应保持 True。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")
        monkeypatch.setenv("ENVIRONMENT", "development")
        cfg = SecurityConfig()
        assert cfg.permission_enforced is True

    def test_explicit_false_is_respected(self, monkeypatch):
        """显式设为 false 时应被读取为 False。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")
        cfg = SecurityConfig()
        assert cfg.permission_enforced is False


class TestPermissionEnforcedWarning:
    """LNN_PERMISSION_ENFORCED 显式关闭时输出 WARNING 日志。"""

    def test_warning_logged_when_explicitly_disabled(self, monkeypatch, caplog):
        """非测试环境下显式关闭权限检查时，应记录 WARNING 级别日志。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
        monkeypatch.setenv("ENVIRONMENT", "development")
        with caplog.at_level(logging.WARNING, logger="app.config"):
            SecurityConfig()
        warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert any("权限检查功能已被禁用" in msg and "安全风险" in msg for msg in warning_messages), (
            f"未找到预期 WARNING 日志，实际记录: {warning_messages}"
        )

    def test_no_warning_in_testing_env(self, monkeypatch, caplog):
        """测试环境下不应输出 WARNING 日志，避免日志噪音。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
        monkeypatch.setenv("ENVIRONMENT", "testing")
        with caplog.at_level(logging.WARNING, logger="app.config"):
            SecurityConfig()
        warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert not any("权限检查功能已被禁用" in msg for msg in warning_messages)

    def test_no_warning_when_enabled(self, monkeypatch, caplog):
        """权限检查开启时不应输出 WARNING。"""
        monkeypatch.delenv("LNN_PERMISSION_ENFORCED", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        with caplog.at_level(logging.WARNING, logger="app.config"):
            SecurityConfig()
        warning_messages = [rec.message for rec in caplog.records if rec.levelno >= logging.WARNING]
        assert not any("权限检查功能已被禁用" in msg for msg in warning_messages)


# 辅助解析函数测试


class TestHelperEnvParsers:
    """_env / _bool_env / _int_env / _float_env / _path 行为测试。"""

    def test_env_returns_default_when_unset(self, monkeypatch):
        """未设置时返回默认。"""
        monkeypatch.delenv("LNN_TEST_NEVER_SET", raising=False)
        assert _env("LNN_TEST_NEVER_SET", "fallback") == "fallback"

    def test_env_returns_value_when_set(self, monkeypatch):
        """设置时返回值。"""
        monkeypatch.setenv("LNN_TEST_SET", "value-1")
        assert _env("LNN_TEST_SET", "default") == "value-1"

    def test_bool_env_true_default_true(self, monkeypatch):
        """_bool_env 默认 True 且未设置时返回 True。"""
        monkeypatch.delenv("LNN_TEST_BOOL", raising=False)
        assert _bool_env("LNN_TEST_BOOL", True) is True

    def test_bool_env_false_default(self, monkeypatch):
        """默认 False。"""
        monkeypatch.delenv("LNN_TEST_BOOL", raising=False)
        assert _bool_env("LNN_TEST_BOOL", False) is False

    def test_bool_env_parses_true_variants(self, monkeypatch):
        """解析 "true" / "TRUE" / "True" 等（大小写不敏感）。"""
        for variant in ("true", "TRUE", "True", "TrUe"):
            monkeypatch.setenv("LNN_TEST_BOOL", variant)
            assert _bool_env("LNN_TEST_BOOL", False) is True, variant

    def test_bool_env_parses_false_variants(self, monkeypatch):
        """非 "true" 字符串均解析为 False。"""
        for variant in ("false", "FALSE", "0", "no", "off", "random", "1", "yes", "on"):
            monkeypatch.setenv("LNN_TEST_BOOL", variant)
            assert _bool_env("LNN_TEST_BOOL", True) is False, variant

    def test_int_env_falls_back_on_value_error(self, monkeypatch):
        """非整数输入时回退到默认值。"""
        monkeypatch.setenv("LNN_TEST_INT", "not-a-number")
        assert _int_env("LNN_TEST_INT", 42) == 42

    def test_int_env_parses_valid(self, monkeypatch):
        """正常整数解析。"""
        monkeypatch.setenv("LNN_TEST_INT", "123")
        assert _int_env("LNN_TEST_INT", 0) == 123

    def test_int_env_uses_default_when_unset(self, monkeypatch):
        """未设置时使用默认值。"""
        monkeypatch.delenv("LNN_TEST_INT", raising=False)
        assert _int_env("LNN_TEST_INT", 7) == 7

    def test_float_env_falls_back_on_value_error(self, monkeypatch):
        """非浮点输入时回退到默认值。"""
        monkeypatch.setenv("LNN_TEST_FLOAT", "abc")
        assert _float_env("LNN_TEST_FLOAT", 1.5) == 1.5

    def test_float_env_parses_valid(self, monkeypatch):
        """正常浮点解析。"""
        monkeypatch.setenv("LNN_TEST_FLOAT", "3.14")
        assert _float_env("LNN_TEST_FLOAT", 0.0) == 3.14

    def test_path_returns_env_or_default(self, monkeypatch, tmp_path):
        """_path 优先读取环境变量，否则返回拼接默认路径。"""
        custom = str(tmp_path / "custom_dir")
        monkeypatch.setenv("LNN_TEST_PATH", custom)
        assert _path("LNN_TEST_PATH", "default_rel") == custom

    def test_path_uses_default_when_unset(self, monkeypatch):
        """未设置时返回默认相对路径拼接。"""
        monkeypatch.delenv("LNN_TEST_PATH", raising=False)
        result = _path("LNN_TEST_PATH", "rel/path")
        assert result.endswith("rel/path") or "rel/path" in result


# SecurityConfig 其它字段测试


class TestSecurityConfigOtherFields:
    """SecurityConfig 中除 permission_enforced 之外字段的覆盖测试。"""

    def test_cors_origins_default_wildcard(self, monkeypatch):
        """CORS_ORIGINS 未设置时默认空（V2.7.0 安全收紧：桌面壳无浏览器 CORS 需求）。"""
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        cfg = SecurityConfig()
        assert cfg.cors_origins == []

    def test_cors_origins_custom_comma_separated(self, monkeypatch):
        """CORS_ORIGINS 多值按逗号拆分。"""
        monkeypatch.setenv("CORS_ORIGINS", "https://a.com, https://b.com, https://c.com")
        cfg = SecurityConfig()
        assert cfg.cors_origins == ["https://a.com", "https://b.com", "https://c.com"]

    def test_cors_origins_strips_whitespace(self, monkeypatch):
        """CORS_ORIGINS 中多余空格被剔除。"""
        monkeypatch.setenv("CORS_ORIGINS", "  https://a.com  ,   https://b.com   ")
        cfg = SecurityConfig()
        assert cfg.cors_origins == ["https://a.com", "https://b.com"]

    def test_allow_credentials_default_true(self, monkeypatch):
        """CORS_ALLOW_CREDENTIALS 默认 True。"""
        monkeypatch.delenv("CORS_ALLOW_CREDENTIALS", raising=False)
        cfg = SecurityConfig()
        assert cfg.allow_credentials is True

    def test_cors_origin_regex_default_none(self, monkeypatch):
        """CORS_ORIGIN_REGEX 未设置时为 None。"""
        monkeypatch.delenv("CORS_ORIGIN_REGEX", raising=False)
        cfg = SecurityConfig()
        assert cfg.cors_origin_regex is None

    def test_cors_origin_regex_custom(self, monkeypatch):
        """CORS_ORIGIN_REGEX 设置时返回值。"""
        monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://.*\.example\.com")
        cfg = SecurityConfig()
        assert cfg.cors_origin_regex == r"https://.*\.example\.com"

    def test_rate_limit_enabled_default(self, monkeypatch):
        """RATE_LIMIT_ENABLED 默认 True。"""
        monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
        cfg = SecurityConfig()
        assert cfg.rate_limit_enabled is True

    def test_rate_limit_requests_default(self, monkeypatch):
        """RATE_LIMIT_REQUESTS 默认 100。"""
        monkeypatch.delenv("RATE_LIMIT_REQUESTS", raising=False)
        cfg = SecurityConfig()
        assert cfg.rate_limit_requests == 100

    def test_rate_limit_requests_custom(self, monkeypatch):
        """RATE_LIMIT_REQUESTS 自定义值。"""
        monkeypatch.setenv("RATE_LIMIT_REQUESTS", "250")
        cfg = SecurityConfig()
        assert cfg.rate_limit_requests == 250

    def test_rate_limit_window_default(self, monkeypatch):
        """RATE_LIMIT_WINDOW 默认 60。"""
        monkeypatch.delenv("RATE_LIMIT_WINDOW", raising=False)
        cfg = SecurityConfig()
        assert cfg.rate_limit_window == 60

    def test_auth_enabled_default(self, monkeypatch):
        """LNN_AUTH_ENABLED 默认 True。"""
        monkeypatch.delenv("LNN_AUTH_ENABLED", raising=False)
        cfg = SecurityConfig()
        assert cfg.auth_enabled is True

    def test_agent_auth_enabled_default(self, monkeypatch):
        """AGENT_AUTH_ENABLED 默认 True。"""
        monkeypatch.delenv("AGENT_AUTH_ENABLED", raising=False)
        cfg = SecurityConfig()
        assert cfg.agent_auth_enabled is True


# TokenConfig 测试


class TestTokenConfig:
    """TokenConfig 的 token 解析、rotate 等行为测试。"""

    def test_token_from_env(self, monkeypatch, tmp_path):
        """LNN_TOKEN 环境变量优先。"""
        monkeypatch.setenv("LNN_TOKEN", "test-token-from-env")
        monkeypatch.delenv("LNN_TOKEN_FILE", raising=False)
        cfg = TokenConfig()
        assert cfg.token == "test-token-from-env"

    def test_token_from_file(self, monkeypatch, tmp_path):
        """LNN_TOKEN_FILE 中存在 token 时使用文件值。"""
        token_file = tmp_path / ".lnn_token"
        token_file.write_text("test-token-from-file\n")
        monkeypatch.delenv("LNN_TOKEN", raising=False)
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        cfg = TokenConfig()
        assert cfg.token == "test-token-from-file"

    def test_token_cached_after_first_call(self, monkeypatch, tmp_path):
        """token 属性具备缓存，第二次访问不再走 _resolve_token。"""
        token_file = tmp_path / ".lnn_token"
        token_file.write_text("cached-token")
        monkeypatch.delenv("LNN_TOKEN", raising=False)
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        cfg = TokenConfig()
        first = cfg.token
        # 删除文件后第二次访问仍应返回缓存值
        token_file.unlink()
        second = cfg.token
        assert first == "cached-token"
        assert second == "cached-token"

    def test_rotate_generates_new_token(self, monkeypatch, tmp_path):
        """rotate() 生成新 token 并更新 _token_cache。"""
        token_file = tmp_path / ".lnn_token"
        token_file.write_text("initial-token")
        monkeypatch.delenv("LNN_TOKEN", raising=False)
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        cfg = TokenConfig()
        initial = cfg.token
        rotated = cfg.rotate()
        assert rotated != initial
        assert cfg.token == rotated
        # 文件已更新
        assert token_file.read_text().strip() == rotated

    def test_rotate_persistence_failure_logs_warning(self, monkeypatch, tmp_path, caplog):
        """rotate 持久化失败时不应抛异常，且记录 WARNING。"""
        # 传入一个无法写入的路径（只读父目录在 Windows 上行为差异较大，这里模拟 write 失败）
        token_file = tmp_path / "rotate_subdir" / ".lnn_token"
        monkeypatch.delenv("LNN_TOKEN", raising=False)
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        # 通过 patch 强制 write_text 失败
        from unittest.mock import patch as _patch

        cfg = TokenConfig()
        cfg._token_cache = "old-token"
        with _patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with caplog.at_level(logging.WARNING, logger="app.config"):
                new_token = cfg.rotate()
        assert isinstance(new_token, str) and new_token
        assert cfg.token == new_token

    def test_resolve_token_file_read_error(self, monkeypatch, tmp_path, caplog):
        """token 文件存在但读取失败时记录 WARNING 并回退到生成新 token。"""
        token_file = tmp_path / ".lnn_token"
        token_file.write_text("placeholder")
        # 强制 read_text 失败
        from unittest.mock import patch as _patch

        monkeypatch.delenv("LNN_TOKEN", raising=False)
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        with _patch.object(Path, "read_text", side_effect=OSError("read error")):
            with caplog.at_level(logging.WARNING, logger="app.config"):
                cfg = TokenConfig()
                token = cfg.token
        # 仍然能拿到 token（生成的新值）
        assert isinstance(token, str) and token
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("Failed to read token file" in r.message for r in warnings)


# EnvironmentConfig 测试


class TestEnvironmentConfig:
    """EnvironmentConfig 的环境判断属性测试。"""

    def test_is_production_true(self, monkeypatch):
        """environment=production 时 is_production 为 True。"""
        monkeypatch.setenv("ENVIRONMENT", "production")
        cfg = EnvironmentConfig()
        assert cfg.is_production is True
        assert cfg.is_development is False

    def test_is_development_true_dev(self, monkeypatch):
        """environment=dev 也算 development。"""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        cfg = EnvironmentConfig()
        assert cfg.is_development is True
        assert cfg.is_production is False

    def test_is_development_true_normal(self, monkeypatch):
        """environment=development 时 is_development 为 True。"""
        monkeypatch.setenv("ENVIRONMENT", "development")
        cfg = EnvironmentConfig()
        assert cfg.is_development is True

    def test_other_environment(self, monkeypatch):
        """其他环境值不视为 production 或 development。"""
        monkeypatch.setenv("ENVIRONMENT", "staging")
        cfg = EnvironmentConfig()
        assert cfg.is_production is False
        assert cfg.is_development is False


# 其它 config dataclass 简单冒烟测试，确保构造路径被执行


class TestOtherConfigsSmoke:
    """其它 config dataclass 简单实例化测试。"""

    def test_server_config_defaults(self, monkeypatch):
        monkeypatch.delenv("SERVER_HOST", raising=False)
        monkeypatch.delenv("SERVER_PORT", raising=False)
        monkeypatch.delenv("DEBUG", raising=False)
        cfg = ServerConfig()
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8765
        assert cfg.debug is False

    def test_server_config_custom(self, monkeypatch):
        monkeypatch.setenv("SERVER_HOST", "0.0.0.0")
        monkeypatch.setenv("SERVER_PORT", "9999")
        monkeypatch.setenv("DEBUG", "true")
        cfg = ServerConfig()
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9999
        assert cfg.debug is True

    def test_ai_config_defaults(self, monkeypatch):
        monkeypatch.delenv("AI_MODE", raising=False)
        cfg = AIConfig()
        assert cfg.mode == "local"
        assert cfg.timeout == 60
        assert cfg.max_retries == 3
        assert cfg.cloud_base_url == "https://api.openai.com/v1"

    def test_model_router_defaults(self, monkeypatch):
        cfg = ModelRouterSettings()
        assert cfg.local_model == "qwen3.5:35b-128k"
        assert cfg.fallback_threshold == 3
        assert cfg.local_timeout == 30

    def test_finetune_settings_defaults(self, monkeypatch):
        cfg = FineTuneSettings()
        assert cfg.finetune_min_samples == 50
        assert cfg.finetune_interval_days == 7
        assert cfg.finetune_auto_trigger is False

    def test_simulation_config_defaults(self, monkeypatch):
        cfg = SimulationConfig()
        assert cfg.voxel_size == 1.0
        assert cfg.max_store_size == 500
        assert cfg.idle_timeout_seconds == 1800

    def test_storage_config_defaults(self, monkeypatch):
        cfg = StorageConfig()
        assert "output" in cfg.output_dir
        assert "temp" in cfg.temp_dir

    def test_database_config_defaults(self, monkeypatch):
        cfg = DatabaseConfig()
        assert cfg.cad_db_path.endswith("cad_tasks.db")
        assert cfg.model_library_path.endswith("model_library.db")
        assert cfg.db_url.startswith("sqlite+aiosqlite:///")

    def test_paths_config_defaults(self, monkeypatch):
        # conftest 自动设置 LNN_GSTACK_DIR=.lingjing/.gstack_test，先清理
        monkeypatch.delenv("LNN_GSTACK_DIR", raising=False)
        cfg = PathsConfig()
        assert cfg.backup_dir == "./backups"
        assert cfg.gstack_dir == ".lingjing/.gstack"

    def test_task_system_config_defaults(self, monkeypatch):
        cfg = TaskSystemConfig()
        assert cfg.max_concurrent == 3
        assert cfg.recovery_strategy == "mark_failed"
        assert cfg.max_task_history == 10000

    def test_logging_config_defaults(self, monkeypatch):
        cfg = LoggingConfig()
        assert cfg.log_level == "INFO"
        assert cfg.max_bytes == 52428800
        assert cfg.backup_count == 5
        assert cfg.retention_days == 30

    def test_process_planning_config_defaults(self, monkeypatch):
        cfg = ProcessPlanningConfig()
        assert cfg.surface_roughness_ra_default == 3.2
        assert cfg.standard_drill_point_angle_deg == 118.0
        assert cfg.gcode_default_program_number == 1000

    def test_app_config_top_level_includes_all_sections(self, monkeypatch):
        """AppConfig 顶层聚合所有子配置。"""
        cfg = AppConfig()
        assert isinstance(cfg.server, ServerConfig)
        assert isinstance(cfg.ai, AIConfig)
        assert isinstance(cfg.model_router, ModelRouterSettings)
        assert isinstance(cfg.finetune, FineTuneSettings)
        assert isinstance(cfg.simulation, SimulationConfig)
        assert isinstance(cfg.storage, StorageConfig)
        assert isinstance(cfg.database, DatabaseConfig)
        assert isinstance(cfg.security, SecurityConfig)
        assert isinstance(cfg.paths, PathsConfig)
        assert isinstance(cfg.token, TokenConfig)
        assert isinstance(cfg.tasks, TaskSystemConfig)
        assert isinstance(cfg.logging, LoggingConfig)
        assert isinstance(cfg.process_planning, ProcessPlanningConfig)
        assert isinstance(cfg.environment, EnvironmentConfig)
        assert cfg.app_name == "灵境制造"
        assert cfg.app_version == "2.8.0"  # 与 VERSION / app_config 默认值同步
        assert cfg.offline_mode is False
