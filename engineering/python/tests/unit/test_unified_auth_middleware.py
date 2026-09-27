"""Tests for UnifiedAuthMiddleware (pure ASGI)."""

import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.auth.unified_auth import (
    UnifiedAuthMiddleware,
    _is_public_path,
    IdempotencyStore,
    AgentRateLimiter,
    AgentAuditLog,
)


@pytest.fixture
def token_file(tmp_path):
    """Create a temporary token file."""
    path = tmp_path / ".lnn_token"
    path.write_text("test-token-uuid-12345")
    return path


@pytest.fixture
def token_meta_file(tmp_path, token_file):
    """Create a token metadata file matching the token_file fixture.

    安全修复 B4：fail-closed 策略要求 token 必须在元数据文件中匹配，
    否则拒绝授权。本 fixture 提供匹配 test-token-uuid-12345 的元数据。
    """
    meta_path = tmp_path / ".lnn_token_meta.json"
    meta_path.write_text(
        json.dumps([{"token": "test-token-uuid-12345", "level": "T"}])
    )
    return meta_path


@pytest.fixture
def app_with_auth(token_file, monkeypatch):
    """Create a FastAPI app with unified auth middleware."""
    monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("LNN_AUTH_ENABLED", "true")
    monkeypatch.setenv("LNN_JWT_AUTH_ENABLED", "true")
    monkeypatch.setenv("AGENT_AUTH_ENABLED", "true")
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")

    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health():
        return {"status": "ok"}

    @app.get("/api/docs")
    async def docs():
        return {"docs": True}

    @app.post("/api/agent/v1/predict")
    async def agent_predict():
        return {"prediction": "result"}

    @app.get("/api/agent/v1/health")
    async def agent_health():
        return {"status": "healthy"}

    UnifiedAuthMiddleware(
        app,
        lnn_auth_enabled=True,
        lnn_permission_enforced=False,
        jwt_auth_enabled=True,
        agent_auth_enabled=True,
    )

    # Re-create app with middleware
    app = FastAPI()

    @app.get("/protected")
    async def protected2():
        return {"status": "ok"}

    @app.get("/health")
    async def health2():
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health2():
        return {"status": "ok"}

    @app.get("/api/docs")
    async def docs2():
        return {"docs": True}

    @app.post("/api/agent/v1/predict")
    async def agent_predict2():
        return {"prediction": "result"}

    @app.get("/api/agent/v1/health")
    async def agent_health2():
        return {"status": "healthy"}

    app.add_middleware(
        UnifiedAuthMiddleware,
        lnn_auth_enabled=True,
        lnn_permission_enforced=False,
        jwt_auth_enabled=True,
        agent_auth_enabled=True,
    )

    return app


@pytest.fixture
def app_lnn_only(token_file, monkeypatch):
    """App with only LNN auth enabled."""
    monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

    app = FastAPI()

    @app.get("/protected")
    async def protected():
        return {"status": "ok"}

    app.add_middleware(
        UnifiedAuthMiddleware,
        lnn_auth_enabled=True,
        lnn_permission_enforced=False,
        jwt_auth_enabled=False,
        agent_auth_enabled=False,
    )

    return app


class TestPublicPathDetection:
    """Test early public path detection."""

    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/api/health",
            "/api/health/ping",
            "/api/metrics",
            "/api/docs",
            "/api/redoc",
            "/api/openapi.json",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
        ],
    )
    def test_public_paths(self, path):
        assert _is_public_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/lnn/predict",
            "/api/v1/projects",
            "/api/agent/v1/predict",
            "/api/v1/lnn/train",
        ],
    )
    def test_protected_paths(self, path):
        assert _is_public_path(path) is False

    @pytest.mark.parametrize(
        "path",
        [
            "/api/docs/swagger-ui",
            "/api/redoc/bundles",
            "/api/openapi.json/extra",
        ],
    )
    def test_public_prefixes(self, path):
        assert _is_public_path(path) is True


class TestUnifiedAuthMiddlewareLNN:
    """Test LNN flat token authentication."""

    def test_public_endpoint_no_auth(self, token_file, monkeypatch):
        """Public endpoints should not require auth."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/health")
        async def health():
            return {"status": "healthy"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

    def test_missing_auth_header(self, token_file, monkeypatch):
        """Missing auth header should return 401."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get("/protected")
        assert response.status_code == 401

    def test_invalid_token(self, token_file, monkeypatch):
        """Invalid token should return 401."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get(
            "/protected", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_valid_token(self, token_file, token_meta_file, monkeypatch):
        """Valid token should pass."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))
        # 安全修复 B4：fail-closed 策略要求提供匹配的元数据文件
        monkeypatch.setenv("LNN_TOKEN_META_FILE", str(token_meta_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get(
            "/protected", headers={"Authorization": "Bearer test-token-uuid-12345"}
        )
        assert response.status_code == 200

    def test_non_bearer_scheme_rejected(self, token_file, monkeypatch):
        """Non-Bearer auth scheme should be rejected."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get(
            "/protected", headers={"Authorization": "Basic test-token"}
        )
        assert response.status_code == 401


class TestUnifiedAuthMiddlewareJWT:
    """Test JWT authentication."""

    def test_jwt_token_rejected_when_disabled(self, token_file, monkeypatch):
        """JWT should be rejected when JWT auth is disabled but LNN enabled."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=False,
        )

        client = TestClient(app)
        # JWT tokens start with "eyJ"
        response = client.get(
            "/protected",
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.invalid"},
        )
        assert response.status_code == 401


class TestUnifiedAuthMiddlewareAgent:
    """Test Agent API authentication."""

    def test_agent_health_public(self, token_file, monkeypatch):
        """Agent health endpoint should be accessible without auth."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/api/agent/v1/health")
        async def agent_health():
            return {"status": "healthy"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=True,
            jwt_auth_enabled=True,
            agent_auth_enabled=True,
        )

        client = TestClient(app)
        response = client.get("/api/agent/v1/health")
        assert response.status_code == 200


class TestUnifiedAuthMiddlewareDisabled:
    """Test when all auth is disabled."""

    def test_all_disabled(self, token_file, monkeypatch):
        """When all auth is disabled, all paths should be accessible."""
        monkeypatch.setenv("LNN_TOKEN_FILE", str(token_file))

        app = FastAPI()

        @app.get("/protected")
        async def protected():
            return {"status": "ok"}

        app.add_middleware(
            UnifiedAuthMiddleware,
            lnn_auth_enabled=False,
            jwt_auth_enabled=False,
            agent_auth_enabled=False,
        )

        client = TestClient(app)
        response = client.get("/protected")
        assert response.status_code == 200


class TestIdempotencyStore:
    """Test idempotency store."""

    def test_store_and_retrieve(self):
        store = IdempotencyStore()

        store.store("key-1", "agent-1", {"result": "data"})
        result = store.check_and_set("key-1", "agent-1")

        assert result is not None
        assert result["result"] == "data"

    def test_different_agent(self):
        store = IdempotencyStore()

        store.store("key-1", "agent-1", {"result": "data"})
        result = store.check_and_set("key-1", "agent-2")

        assert result is None

    def test_cleanup_expired(self):
        store = IdempotencyStore()

        import time
        store._keys["old-key"] = {
            "agent_id": "agent-1",
            "result": {"data": True},
            "created_at": time.time() - 7200,  # 2 hours ago
        }

        store.cleanup(max_age=3600)

        assert "old-key" not in store._keys


class TestAgentRateLimiter:
    """Test agent rate limiter."""

    def test_within_limit(self):
        limiter = AgentRateLimiter(max_requests_per_minute=10, max_concurrent_tasks=3)

        for i in range(5):
            assert limiter.check_rate_limit("agent-1") is True

    def test_exceed_limit(self):
        limiter = AgentRateLimiter(max_requests_per_minute=3, max_concurrent_tasks=3)

        assert limiter.check_rate_limit("agent-1") is True
        assert limiter.check_rate_limit("agent-1") is True
        assert limiter.check_rate_limit("agent-1") is True
        assert limiter.check_rate_limit("agent-1") is False

    def test_task_concurrency(self):
        limiter = AgentRateLimiter(max_requests_per_minute=100, max_concurrent_tasks=2)

        assert limiter.acquire_task("agent-1") is True
        assert limiter.acquire_task("agent-1") is True
        assert limiter.acquire_task("agent-1") is False

        limiter.release_task("agent-1")
        assert limiter.acquire_task("agent-1") is True


class TestAgentAuditLog:
    """Test agent audit log."""

    def test_log_and_retrieve(self, tmp_path):
        log_path = str(tmp_path / "audit.log")
        audit_log = AgentAuditLog(log_path=log_path)

        audit_log.log(
            agent_id="agent-1",
            route="/api/agent/v1/predict",
            permission_class="R",
            status_code=200,
            latency_ms=10.5,
        )

        entries = audit_log.get_entries()
        assert len(entries) == 1
        assert entries[0]["agent_id"] == "agent-1"

    def test_filter_by_agent(self, tmp_path):
        log_path = str(tmp_path / "audit.log")
        audit_log = AgentAuditLog(log_path=log_path)

        audit_log.log(
            agent_id="agent-1",
            route="/api/agent/v1/predict",
            permission_class="R",
            status_code=200,
            latency_ms=10.5,
        )
        audit_log.log(
            agent_id="agent-2",
            route="/api/agent/v1/predict",
            permission_class="R",
            status_code=200,
            latency_ms=12.0,
        )

        entries = audit_log.get_entries(agent_id="agent-1")
        assert len(entries) == 1
        assert entries[0]["agent_id"] == "agent-1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
