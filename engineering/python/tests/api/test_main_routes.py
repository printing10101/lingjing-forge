"""Tests for top-level routes defined directly in app.main (health, version, logs, metrics)."""

from __future__ import annotations

import pytest


class TestHealthEndpoints:
    """Tests for /health and /api/health endpoints."""

    def test_health_check_returns_healthy(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        # V2.7.0 统一响应包装：{code, message, data}
        assert body["code"] == 0
        data = body["data"]
        assert data["status"] == "healthy"
        assert "total_requests" in data

    def test_api_health_check_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"]  # 版本号非空（随 VERSION 文件动态）

    def test_ping_returns_true(self, client):
        response = client.get("/api/health/ping")
        assert response.status_code == 200
        data = response.json()
        assert data["ping"] is True

    def test_health_endpoint_has_security_headers(self, client):
        response = client.get("/health")
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert (
            response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        )


class TestVersionEndpoint:
    """Tests for GET /api/v1/version."""

    def test_version_returns_dict(self, client):
        response = client.get("/api/v1/version")
        assert response.status_code == 200
        data = response.json()
        assert "version" in data
        assert "commit" in data

    def test_version_is_string(self, client):
        response = client.get("/api/v1/version")
        data = response.json()
        assert isinstance(data["version"], str)
        assert len(data["version"]) > 0


class TestMetricsEndpoint:
    """Tests for GET /api/metrics."""

    @pytest.fixture(autouse=True)
    def _allow_metrics(self, monkeypatch):
        """测试客户端 IP 为 'testserver'（非合法 IP），绕过 metrics IP 白名单。"""
        import app.middleware.metrics_middleware as mm

        monkeypatch.setattr(mm, "is_metrics_allowed", lambda ip: True)

    def test_metrics_returns_text(self, client):
        response = client.get("/api/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers.get("content-type", "")

    def test_metrics_content_is_non_empty(self, client):
        response = client.get("/api/metrics")
        assert len(response.text) > 0


class TestLogsEndpoints:
    """Tests for GET /api/v1/logs/* endpoints."""

    def test_logs_stats_returns_success(self, client):
        response = client.get("/api/v1/logs/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_logs_stats_has_data_key(self, client):
        response = client.get("/api/v1/logs/stats")
        data = response.json()
        assert "data" in data

    @pytest.mark.parametrize(
        "buffer_type",
        [
            "request",
            "system_event",
        ],
    )
    def test_query_logs_valid_buffer_type(self, client, buffer_type):
        response = client.get(f"/api/v1/logs/{buffer_type}")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "data" in data

    def test_query_logs_invalid_buffer_type_returns_400(self, client):
        response = client.get("/api/v1/logs/invalid_type")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == 1002

    def test_query_logs_with_limit_and_offset(self, client):
        response = client.get("/api/v1/logs/request?limit=10&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_query_logs_with_level_filter(self, client):
        response = client.get("/api/v1/logs/request?level=INFO")
        assert response.status_code == 200


class TestAPIDocumentation:
    """Tests for auto-generated API documentation endpoints."""

    def test_openapi_json_returns_valid_schema(self, client):
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        data = response.json()
        assert data["info"]["title"] == "灵境制造 API"
        assert "paths" in data

    def test_docs_page_accessible(self, client):
        response = client.get("/api/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower() or "html" in response.headers.get(
            "content-type", ""
        )

    def test_redoc_page_accessible(self, client):
        response = client.get("/api/redoc")
        assert response.status_code == 200
