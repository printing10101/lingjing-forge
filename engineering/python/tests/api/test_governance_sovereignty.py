"""W9.1 主权设置 API + rl_agent 行为挂点测试。

信任红线 5 的回归防线：主权设置必须是后端真实行为——
- GET/PUT /api/v1/governance/sovereignty 可读写且持久化；
- PUT 需 governance:write 权限（权限强制开启时 401）；
- POST /api/v1/rl-agent/training/start 在主权策略要求确认时被拒绝。
"""

from __future__ import annotations

import os

# 与 test_monitor_ws 同款：API 测试环境关闭权限强制（个别用例显式开启验证门禁）
os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import governance, rl_agent
from app.services.sovereignty import (
    get_sovereignty_policy,
    reset_sovereignty_policy,
)


@pytest.fixture
def sovereignty_db(tmp_path, monkeypatch) -> str:
    """每个用例独立的主权数据库，防止污染 python/data/ 真实数据。"""
    db = str(tmp_path / "sov_api.db")
    monkeypatch.setenv("SOVEREIGNTY_DB", db)
    reset_sovereignty_policy()
    yield db
    reset_sovereignty_policy()


@pytest.fixture
def governance_client(sovereignty_db) -> TestClient:
    app = FastAPI()
    app.include_router(governance.router)
    return TestClient(app)


@pytest.mark.api
class TestSovereigntyAPI:
    def test_get_returns_recommended_defaults(self, governance_client: TestClient):
        resp = governance_client.get("/api/v1/governance/sovereignty")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["settings"]["ai_autonomy_level"] == 2
        assert body["data"]["storage"] == "backend"
        assert len(body["data"]["autonomy_labels"]) == 5

    def test_put_updates_and_persists(self, governance_client: TestClient, sovereignty_db, tmp_path):
        resp = governance_client.put(
            "/api/v1/governance/sovereignty",
            json={"ai_autonomy_level": 4, "require_confirmation_for_train": False},
        )
        assert resp.status_code == 200
        assert resp.json()["code"] == 0
        assert resp.json()["data"]["settings"]["ai_autonomy_level"] == 4

        # 持久化：新引擎实例（模拟重启）读到同样的值
        reset_sovereignty_policy()
        settings = get_sovereignty_policy().get_settings()
        assert settings.ai_autonomy_level == 4
        assert settings.require_confirmation_for_train is False
        assert sovereignty_db  # 使用的是用例独立 DB

    def test_put_rejects_out_of_range_level(self, governance_client: TestClient):
        resp = governance_client.put(
            "/api/v1/governance/sovereignty", json={"ai_autonomy_level": 9}
        )
        assert resp.status_code == 422  # pydantic ge/le 校验

    def test_put_rejects_empty_update(self, governance_client: TestClient):
        resp = governance_client.put("/api/v1/governance/sovereignty", json={})
        body = resp.json()
        assert body["code"] != 0

    def test_put_requires_write_permission(self, governance_client: TestClient, monkeypatch):
        """权限强制开启时，写入需要 governance:write（未认证 → 401）。"""
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")
        resp = governance_client.put(
            "/api/v1/governance/sovereignty", json={"ai_autonomy_level": 4}
        )
        assert resp.status_code == 401

    def test_get_requires_read_permission(self, governance_client: TestClient, monkeypatch):
        monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "true")
        resp = governance_client.get("/api/v1/governance/sovereignty")
        assert resp.status_code == 401


@pytest.mark.api
class TestRLAgentTrainingGate:
    @staticmethod
    def _make_rl_client() -> TestClient:
        app = FastAPI()
        app.include_router(rl_agent.router)
        return TestClient(app)

    @staticmethod
    def _training_payload() -> dict:
        from app.contracts.rl_agent import OptimizationTarget, PolicyAlgorithm

        return {
            "algorithm": PolicyAlgorithm.all()[0],
            "optimization_target": OptimizationTarget.all()[0],
            "max_steps": 1000,
        }

    def test_training_start_blocked_when_confirmation_required(
        self, sovereignty_db, monkeypatch
    ):
        """默认（推荐模式 L2）require_confirmation_for_train=True → 拒绝自动启动。"""
        client = self._make_rl_client()
        resp = client.post("/api/v1/rl-agent/training/start", json=self._training_payload())
        body = resp.json()
        assert body["code"] != 0
        assert "需人工确认" in body["message"]
        assert "AI 主权" in body["suggestion"] or "主权" in body["suggestion"]

    def test_training_start_allowed_after_level_raised(self, sovereignty_db, monkeypatch):
        """等级调到全自动（L4）后，同一请求放行（service 被 stub，不真训练）。"""
        get_sovereignty_policy().update_settings({"ai_autonomy_level": 4})

        class _StubStatus:
            def to_dict(self):
                return {"status": "RUNNING", "stub": True}

        class _StubService:
            async def start_training(self, req):
                return _StubStatus()

        monkeypatch.setattr(rl_agent, "get_rl_agent_service", lambda: _StubService())
        client = self._make_rl_client()
        resp = client.post("/api/v1/rl-agent/training/start", json=self._training_payload())
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"]["status"] == "RUNNING"

    def test_training_start_allowed_when_train_toggle_off(self, sovereignty_db, monkeypatch):
        """保持 L2 但用户显式关闭训练确认开关 → 放行（设置真实生效）。"""
        get_sovereignty_policy().update_settings({"require_confirmation_for_train": False})

        class _StubStatus:
            def to_dict(self):
                return {"status": "RUNNING", "stub": True}

        class _StubService:
            async def start_training(self, req):
                return _StubStatus()

        monkeypatch.setattr(rl_agent, "get_rl_agent_service", lambda: _StubService())
        client = self._make_rl_client()
        resp = client.post("/api/v1/rl-agent/training/start", json=self._training_payload())
        assert resp.json()["code"] == 0
