"""Tests for Agent Gateway API endpoints (/api/agent/v1)."""

from __future__ import annotations


class TestAgentHealth:
    """Tests for GET /api/agent/v1/health."""

    def test_health_returns_healthy(self, client):
        response = client.get("/api/agent/v1/health")
        assert response.status_code == 200
        data = response.json()
        # V2.7.0 统一 SuccessResponse 包装：业务数据在 data 字段
        assert data["code"] == 0
        payload = data["data"]
        assert payload["status"] == "healthy"
        assert "timestamp" in payload
        assert "models_registered" in payload

    def test_health_models_registered_is_non_negative(self, client):
        response = client.get("/api/agent/v1/health")
        data = response.json()
        assert data["data"]["models_registered"] >= 0


class TestAgentModels:
    """Tests for GET /api/agent/v1/models."""

    def test_list_models_returns_success(self, client):
        response = client.get("/api/agent/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert "models" in data["data"]
        assert "total" in data["data"]
        assert isinstance(data["data"]["models"], list)

    def test_list_models_total_matches(self, client):
        response = client.get("/api/agent/v1/models")
        data = response.json()
        assert data["data"]["total"] == len(data["data"]["models"])


class TestAgentModelInfo:
    """Tests for GET /api/agent/v1/models/{name}/info."""

    def test_nonexistent_model_returns_not_found(self, client):
        response = client.get("/api/agent/v1/models/nonexistent_model/info")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001


class TestAgentPredict:
    """Tests for POST /api/agent/v1/predict."""

    def test_predict_nonexistent_model_returns_not_found(self, client):
        payload = {
            "model_name": "nonexistent_model",
            "input_data": [1.0, 2.0, 3.0],
        }
        response = client.post("/api/agent/v1/predict", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001

    def test_predict_empty_input_returns_error(self, client):
        payload = {
            "model_name": "test_model",
            "input_data": [],
        }
        response = client.post("/api/agent/v1/predict", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] in (1002, 1001)

    def test_predict_non_numeric_input_returns_error(self, client):
        payload = {
            "model_name": "test_model",
            "input_data": ["abc", "def"],
        }
        response = client.post("/api/agent/v1/predict", json=payload)
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            data = response.json()
            assert data["code"] in (1002, 1001)

    def test_predict_missing_field_returns_422(self, client):
        response = client.post("/api/agent/v1/predict", json={})
        assert response.status_code == 422


class TestAgentTrain:
    """Tests for POST /api/agent/v1/train."""

    def test_train_missing_model_name_returns_422(self, client):
        response = client.post("/api/agent/v1/train", json={})
        assert response.status_code == 422


class TestAgentGetTrain:
    """Tests for GET /api/agent/v1/train/{job_id}."""

    def test_get_nonexistent_job_returns_not_found(self, client):
        response = client.get("/api/agent/v1/train/nonexistent-job-123")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 1001


class TestAgentExecute:
    """Tests for POST /api/agent/v1/execute."""

    def test_execute_missing_params_returns_422(self, client):
        response = client.post("/api/agent/v1/execute", json={})
        assert response.status_code == 422

    def test_live_mode_returns_501_until_dispatch_wired(self, client, monkeypatch):
        """实模式（全部安全闸门通过）必须显式 501，不得谎报 'executed'。

        回归测试（空壳修复）：此前实模式在双因子确认通过后直接返回写死的
        ``{"status": "executed"}``，但机床下发通道从未接线——操作员会看到
        "已下发"的假象。修复后必须返回 501 并说明原因。
        """
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "true")
        payload = {
            "machine_id": "vmc_850",
            "parameters": {"spindle_speed": 8000.0},
            "simulate": False,
            "supervisor_confirmed": True,
            "machine_safety_status": {
                "emergency_stop_active": False,
                "guard_door_closed": True,
                "light_curtain_clear": True,
                "operator_present": True,
            },
        }
        response = client.post("/api/agent/v1/execute", json=payload)
        assert response.status_code == 501
        assert "下发通道未接线" in response.text
        assert "Operation executed successfully" not in response.text

    def test_paper_only_mode_still_simulates(self, client, monkeypatch):
        """Paper-Only 模式（默认）不受影响：返回模拟结果而非 501。"""
        monkeypatch.setenv("LNN_LIVE_EXECUTION_ENABLED", "false")
        payload = {
            "machine_id": "vmc_850",
            "parameters": {"spindle_speed": 8000.0},
            "simulate": True,
        }
        response = client.post("/api/agent/v1/execute", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0


class TestAgentAudit:
    """Tests for GET /api/agent/v1/audit-log."""

    def test_audit_log_returns_success(self, client):
        response = client.get("/api/agent/v1/audit-log")
        assert response.status_code == 200
        data = response.json()
        assert "code" in data


class TestAgentTokens:
    """Tests for POST/GET/DELETE /api/agent/v1/tokens."""

    def test_list_tokens_returns_success(self, client):
        response = client.get("/api/agent/v1/tokens")
        assert response.status_code == 200
        data = response.json()
        assert "code" in data

    def test_create_token_missing_fields_returns_422(self, client):
        response = client.post("/api/agent/v1/tokens", json={})
        assert response.status_code == 422

    def test_delete_nonexistent_token_returns_error(self, client):
        response = client.delete("/api/agent/v1/tokens/nonexistent_agent")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] in (1001, 0)

    def test_revoke_all_returns_success(self, client):
        response = client.post("/api/agent/v1/tokens/revoke-t-all")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
