"""
Test CORS Configuration Module

Tests for:
- Development environment: explicit localhost origins (no wildcards)
- Production environment: regex-based localhost matching
- Environment variable override via ALLOWED_ORIGINS
- Config validation (wildcard + credentials detection)
- Default values and error handling for invalid LINGJING_ENV values
- Hardened startup enforcement (non-zero exit, Chinese ERROR log)
"""

import logging
import os
import pytest
from unittest.mock import patch

from app.middleware.cors_config import (
    CorsSettings,
    CorsConfigError,
    enforce_startup_security,
    get_environment,
    get_cors_origins,
    get_cors_origin_regex,
    get_cors_config,
    is_allowed_origin,
    is_development,
    is_production,
    validate_cors_config,
    PRODUCTION_ORIGIN_REGEX,
    PRODUCTION_ORIGINS,
    DEVELOPMENT_ORIGINS,
)


# Validation


class TestValidateCorsConfig:
    """Tests for the ``validate_cors_config`` security guard."""

    def test_accepts_safe_config(self):
        """Explicit origins with credentials should pass validation."""
        # Should not raise
        validate_cors_config(
            ["http://localhost:3000", "http://example.com"],
            True,
        )

    def test_accepts_empty_origins(self):
        """Empty origin list with credentials should pass."""
        validate_cors_config([], True)

    def test_accepts_none_origins(self):
        """None origins with credentials should pass."""
        validate_cors_config(None, True)

    def test_rejects_wildcard_with_credentials(self):
        """Wildcard '*' with credentials=True must raise CorsConfigError."""
        with pytest.raises(CorsConfigError) as exc:
            validate_cors_config(["*"], True)
        assert "wildcard" in str(exc.value).lower()

    def test_accepts_wildcard_without_credentials(self):
        """Wildcard '*' is acceptable when credentials=False."""
        # Should not raise
        validate_cors_config(["*"], False)

    def test_rejects_wildcard_in_multi_origin_list(self):
        """Wildcard '*' among explicit origins with credentials must raise."""
        with pytest.raises(CorsConfigError):
            validate_cors_config(
                ["http://localhost:3000", "*", "http://example.com"],
                True,
            )

    # Partial wildcard detection (new in this hardening pass)

    def test_rejects_partial_wildcard_subdomain(self):
        """Partial wildcard ``*.example.com`` with credentials must raise."""
        with pytest.raises(CorsConfigError) as exc:
            validate_cors_config(["https://*.example.com"], True)
        assert "wildcard" in str(exc.value).lower()

    def test_rejects_partial_wildcard_scheme(self):
        """Partial wildcard ``https://*`` with credentials must raise."""
        with pytest.raises(CorsConfigError):
            validate_cors_config(["https://*"], True)

    def test_rejects_partial_wildcard_port(self):
        """Partial wildcard in port (``http://localhost:*``) must raise."""
        with pytest.raises(CorsConfigError):
            validate_cors_config(["http://localhost:*"], True)

    def test_rejects_broad_origin_regex(self):
        """A broad origin_regex like ``.*`` with credentials must raise."""
        with pytest.raises(CorsConfigError):
            validate_cors_config(
                [],
                True,
                origin_regex=".*",
            )

    def test_partial_wildcard_emits_error_log_with_chinese(self, caplog):
        """ERROR log must contain the required Chinese security warning."""
        caplog.set_level(logging.ERROR, logger="app.middleware.cors_config")
        with pytest.raises(CorsConfigError):
            validate_cors_config(["*.example.com"], True)

        # Locate the relevant error record
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "expected at least one ERROR log record"
        combined = "\n".join(r.getMessage() for r in error_records)
        assert "通配符*与allow_credentials=True同时使用存在严重安全风险" in combined


# Development environment


class TestDevEnvironment:
    """Tests for CORS behavior in development environment."""

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_origins_are_explicit(self):
        settings = CorsSettings()
        assert settings.get_origins() == DEVELOPMENT_ORIGINS
        assert "*" not in settings.get_origins()

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_origin_regex_is_none(self):
        settings = CorsSettings()
        assert settings.get_origin_regex() is None

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_allows_known_localhost_ports(self):
        settings = CorsSettings()
        for origin in DEVELOPMENT_ORIGINS:
            assert settings.is_allowed_origin(origin) is True

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_blocks_external_origins(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("https://example.com") is False
        assert settings.is_allowed_origin("https://evil.com") is False
        assert settings.is_allowed_origin("http://localhost:9999") is False
        assert settings.is_allowed_origin("http://127.0.0.1:3000") is False

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_blocks_empty_origin(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("") is False

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_environment_detection(self):
        assert get_environment() == "development"
        assert is_development() is True
        assert is_production() is False

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_get_cors_origins_func(self):
        assert get_cors_origins() == DEVELOPMENT_ORIGINS

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_get_cors_origin_regex_func(self):
        assert get_cors_origin_regex() is None

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_get_cors_config(self):
        config = get_cors_config()
        assert config["allow_origins"] == DEVELOPMENT_ORIGINS
        assert config["allow_origin_regex"] is None
        assert config["allow_credentials"] is True
        assert config["max_age"] == 3600


# Production environment


class TestProdEnvironment:
    """Tests for CORS behavior in production environment."""

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_origins_are_empty(self):
        settings = CorsSettings()
        assert settings.get_origins() == PRODUCTION_ORIGINS
        assert settings.get_origins() == []

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_origin_regex_matches_localhost_ports(self):
        settings = CorsSettings()
        regex = settings.get_origin_regex()
        assert regex == PRODUCTION_ORIGIN_REGEX

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_allows_localhost_with_port(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("http://localhost:5173") is True
        assert settings.is_allowed_origin("http://localhost:8080") is True
        assert settings.is_allowed_origin("http://localhost:3000") is True
        assert settings.is_allowed_origin("http://localhost:9999") is True

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_allows_localhost_without_port(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("http://localhost") is True

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_blocks_https_localhost(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("https://localhost:5173") is False

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_blocks_external_origins(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("https://example.com") is False
        assert settings.is_allowed_origin("https://evil.com") is False
        assert settings.is_allowed_origin("http://127.0.0.1:8080") is False
        assert settings.is_allowed_origin("http://0.0.0.0:3000") is False

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_blocks_tauri_direct(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("tauri://localhost") is False

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_environment_detection(self):
        assert get_environment() == "production"
        assert is_production() is True
        assert is_development() is False

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_get_cors_origins_func(self):
        assert get_cors_origins() == PRODUCTION_ORIGINS
        assert get_cors_origins() == []

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_get_cors_origin_regex_func(self):
        assert get_cors_origin_regex() == PRODUCTION_ORIGIN_REGEX

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_get_cors_config(self):
        config = get_cors_config()
        assert config["allow_origins"] == PRODUCTION_ORIGINS
        assert config["allow_origins"] == []
        assert config["allow_origin_regex"] == PRODUCTION_ORIGIN_REGEX
        assert config["allow_credentials"] is True
        assert config["max_age"] == 600


# Environment variable override


class TestEnvOverride:
    """Tests for ALLOWED_ORIGINS environment variable override."""

    @patch.dict(
        os.environ,
        {
            "LINGJING_ENV": "production",
            "ALLOWED_ORIGINS": "http://custom.local,https://custom.local",
        },
        clear=True,
    )
    def test_override_origins_in_production(self):
        settings = CorsSettings()
        assert settings.get_origins() == ["http://custom.local", "https://custom.local"]
        assert settings.get_origin_regex() is None

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "development", "ALLOWED_ORIGINS": "http://custom.local"},
        clear=True,
    )
    def test_override_origins_in_development(self):
        settings = CorsSettings()
        assert settings.get_origins() == ["http://custom.local"]
        assert settings.get_origin_regex() is None

    @patch.dict(
        os.environ,
        {"ALLOWED_ORIGINS": "http://custom.local,https://custom.local"},
        clear=True,
    )
    def test_override_is_allowed_origin(self):
        settings = CorsSettings()
        assert settings.is_allowed_origin("http://custom.local") is True
        assert settings.is_allowed_origin("https://custom.local") is True
        assert settings.is_allowed_origin("https://evil.com") is False

    @patch.dict(
        os.environ,
        {"ALLOWED_ORIGINS": "http://custom.local, https://custom.local"},
        clear=True,
    )
    def test_override_handles_whitespace(self):
        settings = CorsSettings()
        assert settings.get_origins() == ["http://custom.local", "https://custom.local"]

    @patch.dict(
        os.environ,
        {"ALLOWED_ORIGINS": "*"},
        clear=True,
    )
    def test_override_wildcard_raises_on_init(self):
        """ALLOWED_ORIGINS=* with default credentials=True must raise."""
        with pytest.raises(CorsConfigError):
            CorsSettings()


# Default fallback


class TestDefaultFallback:
    """Tests for default behavior when LINGJING_ENV is not set."""

    @patch.dict(os.environ, {}, clear=True)
    def test_default_is_production(self):
        assert get_environment() == "production"
        assert is_production() is True

    @patch.dict(os.environ, {}, clear=True)
    def test_default_settings_are_production(self):
        settings = CorsSettings()
        assert settings.get_origins() == PRODUCTION_ORIGINS
        assert settings.get_origin_regex() == PRODUCTION_ORIGIN_REGEX

    @patch.dict(os.environ, {"LINGJING_ENV": "staging"}, clear=True)
    def test_invalid_env_falls_back_to_production(self):
        assert get_environment() == "production"
        settings = CorsSettings()
        assert settings.get_origins() == PRODUCTION_ORIGINS

    @patch.dict(os.environ, {"LINGJING_ENV": ""}, clear=True)
    def test_empty_env_falls_back_to_production(self):
        settings = CorsSettings()
        assert settings.get_origins() == PRODUCTION_ORIGINS


# Override environment parameter (standalone helpers)


class TestOverrideEnvParam:
    """Tests for override_env parameter in helper functions."""

    def test_get_cors_origins_with_override(self):
        assert get_cors_origins(override_env="development") == DEVELOPMENT_ORIGINS
        assert get_cors_origins(override_env="production") == PRODUCTION_ORIGINS
        assert get_cors_origins(override_env="production") == []

    def test_get_cors_origin_regex_with_override(self):
        assert get_cors_origin_regex(override_env="development") is None
        assert get_cors_origin_regex(override_env="production") == PRODUCTION_ORIGIN_REGEX

    def test_is_allowed_origin_with_override(self):
        assert is_allowed_origin("http://localhost:3000", override_env="development") is True
        assert is_allowed_origin("https://evil.com", override_env="development") is False
        assert is_allowed_origin("http://localhost:5173", override_env="production") is True
        assert is_allowed_origin("https://evil.com", override_env="production") is False


# Security headers


class TestSecurityHeaders:
    """Tests for security headers helper."""

    def test_get_security_headers(self):
        from app.middleware.cors_config import get_security_headers

        headers = get_security_headers()
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-XSS-Protection"] == "1; mode=block"


# Startup-time enforcement (non-zero exit on wildcard + credentials)


class TestEnforceStartupSecurity:
    """Tests for ``enforce_startup_security`` — the runtime gate."""

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "development", "ALLOWED_ORIGINS": "*"},
        clear=True,
    )
    def test_wildcard_override_aborts_startup(self, caplog):
        """ALLOWED_ORIGINS='*' must abort the startup gate with CorsConfigError."""
        caplog.set_level(logging.ERROR, logger="app.middleware.cors_config")
        with pytest.raises(CorsConfigError):
            enforce_startup_security()
        # And the ERROR log must contain the required Chinese text.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records
        combined = "\n".join(r.getMessage() for r in error_records)
        assert "通配符*与allow_credentials=True同时使用存在严重安全风险" in combined

    @patch.dict(os.environ, {}, clear=True)
    def test_safe_default_production_passes(self):
        """Default (production, no ALLOWED_ORIGINS) must not raise."""
        # Should not raise.
        enforce_startup_security()

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "development"},
        clear=True,
    )
    def test_dev_default_origins_pass(self):
        """Default development origins (no wildcards) must not raise."""
        enforce_startup_security()

    @patch.dict(
        os.environ,
        {
            "LINGJING_ENV": "production",
            "ALLOWED_ORIGINS": "https://app.example.com,https://admin.example.com",
        },
        clear=True,
    )
    def test_explicit_prod_origins_pass(self):
        """Explicit production origins (no wildcards) must not raise."""
        enforce_startup_security()

    @patch.dict(
        os.environ,
        {
            "LINGJING_ENV": "production",
            "ALLOWED_ORIGINS": "https://*.example.com",
        },
        clear=True,
    )
    def test_partial_wildcard_in_prod_aborts(self, caplog):
        """Partial wildcard in production must abort with the Chinese log."""
        caplog.set_level(logging.ERROR, logger="app.middleware.cors_config")
        with pytest.raises(CorsConfigError):
            enforce_startup_security()
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        combined = "\n".join(r.getMessage() for r in error_records)
        assert "通配符*与allow_credentials=True同时使用存在严重安全风险" in combined


class TestStartupExitCode:
    """Test the documented startup termination pattern (sys.exit(1))."""

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "development", "ALLOWED_ORIGINS": "*"},
        clear=True,
    )
    def test_sys_exit_on_wildcard(self, caplog):
        """The documented wrap-as-script pattern must exit non-zero."""
        caplog.set_level(logging.ERROR, logger="app.middleware.cors_config")
        import sys

        with pytest.raises(SystemExit) as exc_info:
            try:
                enforce_startup_security()
            except CorsConfigError:
                # Mirror the production pattern from main.py
                sys.exit(1)
        assert exc_info.value.code != 0


# Environment isolation (dev must not leak into prod and vice versa)


class TestEnvironmentIsolation:
    """Verify that the development origins never appear in production and that
    the production config is a closed allowlist with no wildcards."""

    @patch.dict(os.environ, {"LINGJING_ENV": "production"}, clear=True)
    def test_prod_does_not_include_dev_origins(self):
        settings = CorsSettings()
        prod_origins = settings.get_origins()
        # The prod origin list is the (empty) PRODUCTION_ORIGINS — it must
        # not contain any of the dev defaults.
        assert all(o not in DEVELOPMENT_ORIGINS for o in prod_origins)
        # Specifically: prod must not include the dev Vite/CRA ports.
        assert "http://localhost:5173" not in prod_origins
        assert "http://localhost:3000" not in prod_origins

    @patch.dict(os.environ, {"LINGJING_ENV": "development"}, clear=True)
    def test_dev_origins_are_explicit_no_wildcards(self):
        settings = CorsSettings()
        origins = settings.get_origins()
        # All dev origins must be explicit, full http://localhost URLs
        for o in origins:
            assert o.startswith("http://localhost")
            assert "*" not in o

    def test_production_origin_list_has_no_wildcards(self):
        """Static check on the module constant: PRODUCTION_ORIGINS must be
        free of any wildcard character."""
        for o in PRODUCTION_ORIGINS:
            assert "*" not in o, f"PRODUCTION_ORIGINS contains wildcard: {o!r}"

    def test_development_origin_list_has_no_wildcards(self):
        """Static check on the module constant: DEVELOPMENT_ORIGINS must be
        free of any wildcard character."""
        for o in DEVELOPMENT_ORIGINS:
            assert "*" not in o, f"DEVELOPMENT_ORIGINS contains wildcard: {o!r}"

    def test_production_regex_is_not_wildcard(self):
        """The production regex is bounded — not a wildcard pattern."""
        # The regex must not contain a bare ``*`` character (it uses
        # character classes like ``\\d+`` but no wildcard).
        assert "*" not in PRODUCTION_ORIGIN_REGEX

    def test_dev_includes_required_localhost_ports(self):
        """Acceptance criterion: dev must include :5173 and :3000."""
        assert "http://localhost:5173" in DEVELOPMENT_ORIGINS
        assert "http://localhost:3000" in DEVELOPMENT_ORIGINS

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "development"},
        clear=True,
    )
    def test_dev_helper_matches_settings(self):
        """Standalone helper agrees with CorsSettings in dev."""
        settings = CorsSettings()
        assert get_cors_origins() == settings.get_origins()

    @patch.dict(
        os.environ,
        {"LINGJING_ENV": "production"},
        clear=True,
    )
    def test_prod_helper_matches_settings(self):
        """Standalone helper agrees with CorsSettings in prod."""
        settings = CorsSettings()
        assert get_cors_origins() == settings.get_origins()


# Source-level guard rails


class TestSourceHasNoWildcardOrigin:
    """Source-level assertions mirroring the grep acceptance check.

    ``grep -n '"\\*"' python/app/core/cors_config.py`` must return no
    matches: the file should not contain the bare string ``"*"`` outside
    of documentation, comments, and the Chinese log message.
    """

    def test_no_bare_star_origin_in_module(self):
        from app.middleware import cors_config

        with open(cors_config.__file__, encoding="utf-8") as f:
            source = f.read()
        # Strip comments to avoid false positives.
        non_comment_lines = []
        for line in source.splitlines():
            stripped = line.split("#", 1)[0]
            non_comment_lines.append(stripped)
        non_comment = "\n".join(non_comment_lines)

        # The only allowed occurrence is in the _WILDCARD_PATTERN
        # detector or in error messages, never as a default origin.
        assert '"*"' not in non_comment, (
            "Bare '\"*\"' string found in cors_config.py — possible default wildcard origin regression."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
