"""W6a 世界模型物理预演卡 API 测试。

预演 = 轨迹预测 + 阈值带 + SafetyShield 物理裁决 + 主权保守缩放 + 主权决策。
世界模型服务打桩（真实模型缺失时 preview 依赖服务层异常映射，行为已有覆盖）。
"""

from __future__ import annotations

import os

os.environ.setdefault("LNN_PERMISSION_ENFORCED", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import world_model as world_model_api
from app.contracts.world_model import (
    ActionField,
    TrajectoryMetrics,
    TrajectoryStep,
    WorldModelInfo,
    WorldModelPredictResponse,
)
from app.services.sovereignty import reset_sovereignty_policy


def _steps(chatter: float, confidence: float = 0.9, steps: int = 3) -> list[TrajectoryStep]:
    return [
        TrajectoryStep(
            step=i,
            predicted_state={"spindle_speed": 6000.0},
            chatter_probability=chatter,
            tool_wear_increment=0.01,
            surface_roughness=1.2,
            confidence=confidence,
        )
        for i in range(steps)
    ]


def _predict_response(chatter: float, confidence: float = 0.9) -> WorldModelPredictResponse:
    return WorldModelPredictResponse(
        predicted_trajectory=_steps(chatter, confidence),
        trajectory_metrics=TrajectoryMetrics(
            mean_chatter_probability=chatter,
            max_chatter_probability=chatter,
            cumulative_tool_wear=0.03,
            final_surface_roughness=1.2,
        ),
        model_info=WorldModelInfo(
            world_model_version="1.0.0",
            training_data_size=1000,
            prediction_horizon=10,
            uncertainty_estimate=0.1,
        ),
    )


class _StubService:
    def __init__(self, chatter: float = 0.1, confidence: float = 0.9):
        self.chatter = chatter
        self.confidence = confidence
        self.calls = 0

    async def predict(self, request):
        self.calls += 1
        return _predict_response(self.chatter, self.confidence)


SAFE_ACTION = {
    ActionField.SPINDLE_SPEED_DELTA: -0.1,
    ActionField.FEED_RATE_DELTA: 0.05,
    ActionField.DEPTH_OF_CUT_DELTA: 0.0,
    ActionField.WIDTH_OF_CUT_DELTA: 0.0,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离主权 DB + 打桩世界模型服务。"""
    monkeypatch.setenv("SOVEREIGNTY_DB", str(tmp_path / "sov.db"))
    reset_sovereignty_policy()

    stub = _StubService()
    monkeypatch.setattr(world_model_api, "get_world_model_service", lambda: stub)

    app = FastAPI()
    app.include_router(world_model_api.router)
    yield {"client": TestClient(app), "stub": stub}
    reset_sovereignty_policy()


@pytest.mark.api
class TestPreviewCard:
    def test_safe_verdict_with_defaults(self, env):
        """默认档（L2 推荐模式）：温和动作 + 低颤振 → safe，主权决策随卡返回。"""
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={"current_state": {"spindle_speed": 6000.0}, "candidate_action": SAFE_ACTION},
        )
        body = resp.json()
        assert body["code"] == 0, body
        data = body["data"]
        assert data["verdict"] == "safe"
        assert data["shield"]["violated"] is False
        # 默认档不干预动作
        assert data["conservatism"] == {"level": 2, "factor": 1.0, "scaled": False}
        assert data["final_action"]["spindle_speed_delta"] == -0.1
        # 主权决策：L2 下 agent_action 需人工确认
        assert data["sovereignty"]["requires_confirmation"] is True
        # 轨迹与阈值带齐全
        assert len(data["trajectory"]) == 3
        assert data["bands"]["chatter_probability"] == {"safe_below": 0.3, "danger_at": 0.5}

    def test_danger_verdict_on_high_chatter(self, env, monkeypatch):
        env["stub"].chatter = 0.7
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={"current_state": {"spindle_speed": 6000.0}, "candidate_action": SAFE_ACTION},
        )
        data = resp.json()["data"]
        assert data["verdict"] == "danger"
        assert any("颤振" in r for r in data["reasons"])
        assert data["metrics"]["max_chatter_probability"] == 0.7

    def test_warning_verdict_on_low_model_confidence(self, env):
        env["stub"].confidence = 0.3
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={"current_state": {"spindle_speed": 6000.0}, "candidate_action": SAFE_ACTION},
        )
        data = resp.json()["data"]
        assert data["verdict"] == "warning"
        assert any("置信度" in r for r in data["reasons"])

    def test_shield_blocks_out_of_range_action(self, env):
        """越界动作：护盾必须拦截并回退安全动作（物理硬约束，任何等级不放行）。"""
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={
                "current_state": {"spindle_speed": 6000.0},
                "candidate_action": {ActionField.SPINDLE_SPEED_DELTA: 1.5},
            },
        )
        data = resp.json()["data"]
        assert data["verdict"] == "danger"
        assert data["shield"]["violated"] is True
        assert data["final_action"][ActionField.SPINDLE_SPEED_DELTA] == 0.0

    def test_low_autonomy_level_damps_final_action(self, env):
        """L0 完全手动：最终动作按保守系数 0.5 阻尼——等级切换真实改变输出。"""
        from app.services.sovereignty import get_sovereignty_policy

        get_sovereignty_policy().update_settings({"ai_autonomy_level": 0})
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={"current_state": {"spindle_speed": 6000.0}, "candidate_action": SAFE_ACTION},
        )
        data = resp.json()["data"]
        assert data["conservatism"]["factor"] == 0.5
        assert data["final_action"]["spindle_speed_delta"] == -0.05

    def test_missing_action_fields_default_to_zero(self, env):
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={
                "current_state": {"spindle_speed": 6000.0},
                "candidate_action": {ActionField.SPINDLE_SPEED_DELTA: -0.1},
            },
        )
        data = resp.json()["data"]
        assert data["verdict"] == "safe"
        final = data["final_action"]
        assert final[ActionField.FEED_RATE_DELTA] == 0.0
        assert final[ActionField.DEPTH_OF_CUT_DELTA] == 0.0

    def test_service_model_missing_maps_to_error(self, env, monkeypatch):
        from app.contracts.world_model import ModelNotFoundError

        class _NoModelService:
            async def predict(self, request):
                raise ModelNotFoundError("model://world_model/9.9.9 不存在")

        monkeypatch.setattr(world_model_api, "get_world_model_service", lambda: _NoModelService())
        resp = env["client"].post(
            "/api/v1/world-model/preview",
            json={"current_state": {"spindle_speed": 6000.0}, "candidate_action": SAFE_ACTION},
        )
        body = resp.json()
        assert body["code"] != 0
        assert "9.9.9" in body["message"] or body["message"]
