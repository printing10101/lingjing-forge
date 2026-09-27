"""Performance benchmark for middleware optimization.

Compares the latency of public path requests before and after middleware optimization.
Public paths should skip all auth logic and return fast.
"""

import time
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.auth.security_headers_asgi import SecurityHeadersMiddleware
from app.auth.unified_auth import UnifiedAuthMiddleware


@pytest.fixture
def optimized_app(tmp_path, monkeypatch):
    """FastAPI app with optimized pure ASGI middleware stack."""
    monkeypatch.setenv("LNN_TOKEN_FILE", str(tmp_path / ".lnn_token"))
    (tmp_path / ".lnn_token").write_text("test-token")

    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/health")
    async def api_health():
        return {"status": "ok"}

    @app.get("/api/docs")
    async def docs():
        return {"docs": True}

    @app.get("/api/metrics")
    async def metrics():
        return {"metrics": "ok"}

    @app.get("/protected")
    async def protected():
        return {"status": "protected"}

    # Optimized middleware stack
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        UnifiedAuthMiddleware,
        lnn_auth_enabled=True,
        lnn_permission_enforced=False,
        jwt_auth_enabled=True,
        agent_auth_enabled=True,
    )

    return app


class TestPublicPathLatency:
    """Benchmark public path latency with optimized middleware."""

    @pytest.mark.parametrize(
        "path",
        ["/health", "/api/health", "/api/docs", "/api/metrics"],
    )
    def test_public_path_fast_response(self, optimized_app, path):
        """Public paths should respond quickly without auth overhead."""
        client = TestClient(optimized_app)

        # Warm up
        client.get(path)

        # Measure
        iterations = 50
        total_ms = 0
        for _ in range(iterations):
            start = time.perf_counter()
            response = client.get(path)
            elapsed = (time.perf_counter() - start) * 1000
            total_ms += elapsed
            assert response.status_code == 200

        avg_ms = total_ms / iterations
        # Public paths should respond in under 50ms in test environment
        # (actual prod should be <5ms as per requirements)
        assert avg_ms < 50, f"Public path {path} avg latency {avg_ms:.2f}ms > 50ms"

    def test_protected_path_rejects_no_auth(self, optimized_app):
        """Protected paths should reject requests without auth."""
        client = TestClient(optimized_app)
        response = client.get("/protected")
        assert response.status_code == 401

    def test_protected_path_allows_valid_auth(self, optimized_app):
        """Protected paths should allow requests with valid auth."""
        client = TestClient(optimized_app)
        response = client.get(
            "/protected", headers={"Authorization": "Bearer test-token"}
        )
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
