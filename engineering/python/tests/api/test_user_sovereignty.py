"""Tests for User Sovereignty API endpoints (/api/v1/user-sovereignty)."""

from __future__ import annotations


class TestUserSovereigntyPredict:
    """Tests for POST /api/v1/user-sovereignty/predict."""

    def test_predict_nonexistent_model_returns_not_found(self, client):
        payload = {"model_name": "nonexistent_model_xyz", "input_data": [1.0, 2.0]}
        response = client.post("/api/v1/user-sovereignty/predict", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001

    def test_predict_empty_input_returns_error(self, client):
        payload = {"model_name": "test_model", "input_data": []}
        response = client.post("/api/v1/user-sovereignty/predict", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] in (1002, 1001)

    def test_predict_missing_fields_returns_422(self, client):
        response = client.post("/api/v1/user-sovereignty/predict", json={})
        assert response.status_code == 422


class TestAuditLogRecord:
    """Tests for POST /api/v1/user-sovereignty/audit-log/record."""

    def test_record_with_valid_params(self, client):
        params = {
            "ai_module": "lnn_predict",
            "user_decision": "accept",
            "operation_status": "success",
        }
        json_body = {
            "ai_recommendation": {"value": 0.85, "confidence": 0.9},
            "final_execution": {"result": "success"},
            "confidence": 0.9,
        }
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/record",
            params=params,
            json=json_body,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "timestamp_ms" in data["data"]

    def test_record_with_invalid_module_returns_error(self, client):
        params = {
            "ai_module": "invalid_module",
            "user_decision": "accept",
            "operation_status": "success",
        }
        json_body = {
            "ai_recommendation": {"value": 0.85},
            "final_execution": {"result": "success"},
        }
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/record",
            params=params,
            json=json_body,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002

    def test_record_with_invalid_decision_returns_error(self, client):
        params = {
            "ai_module": "lnn_predict",
            "user_decision": "invalid_decision",
            "operation_status": "success",
        }
        json_body = {
            "ai_recommendation": {"value": 0.85},
            "final_execution": {"result": "success"},
        }
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/record",
            params=params,
            json=json_body,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002

    def test_record_with_invalid_status_returns_error(self, client):
        params = {
            "ai_module": "lnn_predict",
            "user_decision": "accept",
            "operation_status": "invalid_status",
        }
        json_body = {
            "ai_recommendation": {"value": 0.85},
            "final_execution": {"result": "success"},
        }
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/record",
            params=params,
            json=json_body,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1002


class TestAuditLogQuery:
    """Tests for POST /api/v1/user-sovereignty/audit-log/query."""

    def test_query_with_default_params_returns_success(self, client):
        response = client.post("/api/v1/user-sovereignty/audit-log/query", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "logs" in data["data"]

    def test_query_with_limit(self, client):
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/query", json={"limit": 10, "offset": 0}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestAuditLogSearch:
    """Tests for POST /api/v1/user-sovereignty/audit-log/search."""

    def test_search_with_keyword_returns_success(self, client):
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/search", json={"keyword": "test"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestAuditLogExport:
    """Tests for POST /api/v1/user-sovereignty/audit-log/export."""

    def test_export_json_format(self, client):
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/export",
            json={"format": "json"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0

    def test_export_csv_format(self, client):
        response = client.post(
            "/api/v1/user-sovereignty/audit-log/export",
            json={"format": "csv"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestAuditLogStatistics:
    """Tests for GET /api/v1/user-sovereignty/audit-log/statistics."""

    def test_statistics_returns_success(self, client):
        response = client.get("/api/v1/user-sovereignty/audit-log/statistics")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestAuditLogClear:
    """Tests for DELETE /api/v1/user-sovereignty/audit-log/clear."""

    def test_clear_returns_success(self, client):
        response = client.delete("/api/v1/user-sovereignty/audit-log/clear")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "cleared_entries" in data["data"]


class TestUserSovereigntySettings:
    """Tests for GET /api/v1/user-sovereignty/settings."""

    def test_get_settings_returns_success(self, client):
        response = client.get("/api/v1/user-sovereignty/settings")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
