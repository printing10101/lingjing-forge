"""直接预测服务必须加载真实权重，不能只验证插件路径。"""

import pytest

torch = pytest.importorskip("torch")

from app.contracts.world_model import ActionField, InvalidStateError, ModelNotFoundError, StateField, WorldModelPredictRequest  # noqa: E402
from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402
from app.plugins.world_model.training import weights_resolver  # noqa: E402
from app.services.world_model_service import WorldModelService  # noqa: E402


@pytest.fixture
def checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(weights_resolver, "DEFAULT_MODELS_DIR", str(tmp_path))
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1,
                              feature_dim=4, d_model=8, fused_dim=16, use_fusion=True)
    model = WorldModelNet(config)
    with torch.no_grad():
        for param in model.parameters():
            param.zero_()
        model.state_head.bias.copy_(torch.tensor([0.2, 0.1, 1.5, 0.4, 0.25, 0.3, 0.5, 0.9]))
    path = tmp_path / "world_model" / "service-test.pt"
    path.parent.mkdir()
    torch.save({"model_state_dict": model.state_dict(), "model_config": config.to_dict(),
                "trainer_type": "FusionWorldModelTrainer"}, path)
    return path, config


def request(unified=True):
    return WorldModelPredictRequest(
        model_uri="model://world_model/service-test", horizon=2,
        current_state={} if unified else {field: 1.0 for field in StateField.all()},
        candidate_action={field: 0.0 for field in ActionField.all()},
        unified_state={
            "geometry": {"bbox_dimensions": [10, 20, 30], "feature_vector": [0.0] * 4,
                         "symmetry_score": 0.5, "complexity_score": 0.2},
            "dynamics": {"spindle_speed": 8000, "feed_rate": 500, "depth_of_cut": 0.5,
                         "tool_wear": 0.1, "vibration_rms": 0.4, "temperature": 25},
        } if unified else None,
    )


@pytest.mark.asyncio
async def test_direct_service_loads_checkpoint_and_maps_trained_columns(checkpoint):
    path, config = checkpoint
    service = WorldModelService()
    result = await service.predict(request())
    predictor = service._predictor_cache["model://world_model/service-test"]
    assert predictor._weights_source == "checkpoint"
    assert predictor.config.hidden_dim == config.hidden_dim
    assert predictor.config.use_fusion is True
    step = result.predicted_trajectory[0]
    assert step.chatter_probability == pytest.approx(0.2)
    assert step.surface_roughness == pytest.approx(1.5)
    assert step.predicted_state["tool_wear"] == pytest.approx(0.1)
    assert result.model_info.weights_source == "checkpoint"


def test_missing_weights_are_not_cached_as_random(monkeypatch):
    service = WorldModelService()
    monkeypatch.setattr(service, "_resolve_weights_path", lambda _: None)
    with pytest.raises(ModelNotFoundError, match="权重"):
        service._get_or_load_predictor("model://world_model/missing")
    assert not service._predictor_cache


def test_input_requirements_leave_legacy_training_window_unknown(checkpoint):
    result = WorldModelService().input_requirements("model://world_model/service-test")
    assert result["training_history_steps"] == []
    assert result["training_horizon"] is None


def test_input_requirements_expose_saved_training_window(checkpoint):
    path, _ = checkpoint
    payload = torch.load(path, weights_only=True)
    payload["input_contract"] = {"schema_version": 1, "history_steps": [20], "training_horizon": 5}
    torch.save(payload, path)
    result = WorldModelService().input_requirements("model://world_model/service-test")
    assert result["training_history_steps"] == [20]
    assert result["training_horizon"] == 5


@pytest.mark.parametrize("contract", [[], {"schema_version": 9},
    {"schema_version": 1, "history_steps": [0]},
    {"schema_version": 1, "history_steps": [True]},
    {"schema_version": 1, "training_horizon": -1}])
def test_invalid_training_window_is_not_silently_used(checkpoint, contract):
    path, _ = checkpoint
    payload = torch.load(path, weights_only=True)
    payload["input_contract"] = contract
    torch.save(payload, path)
    with pytest.raises(ModelNotFoundError):
        WorldModelService()._get_or_load_predictor("model://world_model/service-test")


def test_corrupt_weights_are_rejected(checkpoint):
    path, _ = checkpoint
    path.write_bytes(b"invalid checkpoint")
    with pytest.raises(ModelNotFoundError):
        WorldModelService()._get_or_load_predictor("model://world_model/service-test")


def test_failed_load_is_not_cached(checkpoint):
    path, _ = checkpoint
    payload = torch.load(path, weights_only=True)
    payload["model_state_dict"]["state_head.bias"] = torch.zeros(9)
    torch.save(payload, path)
    service = WorldModelService()
    with pytest.raises(ModelNotFoundError):
        service._get_or_load_predictor("model://world_model/service-test")
    assert not service._predictor_cache


def test_unknown_output_schema_is_rejected(checkpoint):
    path, _ = checkpoint
    payload = torch.load(path, weights_only=True)
    payload["state_schema"] = "unknown_columns"
    torch.save(payload, path)
    with pytest.raises(ModelNotFoundError, match="状态口径"):
        WorldModelService()._get_or_load_predictor("model://world_model/service-test")


@pytest.mark.asyncio
async def test_direct_api_reports_trained_weights(checkpoint, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import world_model as api

    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    service = WorldModelService()
    monkeypatch.setattr(api, "get_world_model_service", lambda: service)
    app = FastAPI()
    app.include_router(api.router)
    payload = request()
    body = TestClient(app).post("/api/v1/world-model/predict", json={
        "model_uri": payload.model_uri, "horizon": payload.horizon,
        "candidate_action": payload.candidate_action, "current_state": {},
        "unified_state": payload.unified_state,
    }).json()
    assert body["code"] == 0, body
    assert body["data"]["model_info"]["weights_source"] == "checkpoint"
    assert body["data"]["predicted_trajectory"][0]["surface_roughness"] == pytest.approx(1.5)


@pytest.mark.asyncio
async def test_fusion_checkpoint_requires_real_geometry(checkpoint):
    with pytest.raises(InvalidStateError, match="几何"):
        await WorldModelService().predict(request(unified=False))


@pytest.mark.asyncio
async def test_nonfinite_input_is_rejected(checkpoint):
    payload = request()
    payload.unified_state["dynamics"]["tool_wear"] = float("nan")
    with pytest.raises(InvalidStateError):
        await WorldModelService().predict(payload)


@pytest.mark.asyncio
async def test_feedback_checkpoint_requires_and_uses_initial_state(checkpoint):
    path, config = checkpoint
    config.delta_prediction = True
    model = WorldModelNet(config)
    with torch.no_grad():
        for param in model.parameters():
            param.zero_()
    torch.save({"model_state_dict": model.state_dict(), "model_config": config.to_dict(),
                "trainer_type": "FusionWorldModelTrainer", "state_feedback": True}, path)
    service = WorldModelService()
    with pytest.raises(InvalidStateError, match="状态反馈"):
        await service.predict(request())
    payload = request()
    payload.current_state = dict(zip(
        ["chatter_probability", "tool_wear", "surface_roughness", "vibration_rms",
         "temperature_over_100c", "cutting_force_norm", "mrr_norm", "confidence"],
        [0.2, 0.1, 1.5, 0.4, 0.25, 0.3, 0.5, 0.9],
    ))
    result = await service.predict(payload)
    assert result.predicted_trajectory[0].surface_roughness == pytest.approx(1.5)
    assert result.predicted_trajectory[1].tool_wear_increment == 0
