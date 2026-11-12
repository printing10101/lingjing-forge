"""历史序列必须按原时序进入真实网络，不能退化为末帧或零动作。"""

from copy import deepcopy

import numpy as np
import pytest

from app.contracts.world_model import ActionField, InvalidStateError
from app.services.world_model_history import prepare_fusion_history


@pytest.fixture
def history():
    return {"schema_version": 1, "source": "test fixture; synthetic", "sample_period_seconds": 0.1,
            "samples": [{"time_seconds": index * 0.1, "current_state": {
                "spindle_speed": index + 1, "feed_rate": 2, "depth_of_cut": 0.5,
                "tool_wear": 0.1, "vibration_rms": 0.2, "temperature": 25,
            }, "action": {name: 0.1 * (index + 1) for name in ActionField.all()}} for index in range(3)]}


def prepare(history, **kwargs):
    return prepare_fusion_history(history, {"bbox_dimensions": [1, 2, 3], "feature_vector": [0] * 4,
        "symmetry_score": 0.2, "complexity_score": 0.3}, training_steps=kwargs.get("training_steps", []),
        feedback_fields=kwargs.get("feedback_fields", []))


def test_history_keeps_each_state_and_actual_action_with_provenance(history):
    original = deepcopy(history)
    result = prepare(history, training_steps=[3])
    assert [item["dynamics"]["spindle_speed"] for item in result.states] == [1, 2, 3]
    np.testing.assert_allclose(result.actions[:, 0], [0.1, 0.2, 0.3])
    assert result.summary["training_window_match"] == "matched"
    assert result.summary["source_verification"] == "user_declared"
    assert result.summary["training_sample_period_match"] == "unknown"
    assert history == original
    assert prepare(history).summary["training_window_match"] == "unknown"


@pytest.mark.parametrize("mutation", [
    lambda h: h.update(schema_version=2), lambda h: h.update(source=" "),
    lambda h: h.update(source="a" * 513), lambda h: h.update(sample_period_seconds=0),
    lambda h: h.update(sample_period_seconds=True), lambda h: h.update(sample_period_seconds=float("inf")),
    lambda h: h.update(samples=[]), lambda h: h.update(samples=h["samples"] * 86),
    lambda h: h["samples"].__setitem__(0, None),
    lambda h: h["samples"][1].update(time_seconds=0),
    lambda h: h["samples"][1].update(time_seconds=0.11),
    lambda h: h["samples"][0].update(current_state=None),
    lambda h: h["samples"][0]["current_state"].pop("feed_rate"),
    lambda h: h["samples"][0]["current_state"].update(tool_wear=-1),
    lambda h: h["samples"][0]["action"].pop("spindle_speed_delta"),
    lambda h: h["samples"][0]["action"].update(spindle_speed_delta=float("nan")),
    lambda h: h["samples"][0]["action"].update(spindle_speed_delta=1e100),
])
def test_bad_history_is_rejected_instead_of_padded_or_resampled(history, mutation):
    mutation(history)
    with pytest.raises(InvalidStateError):
        prepare(history)


def test_known_window_mismatch_rejected(history):
    with pytest.raises(InvalidStateError, match="不匹配"):
        prepare(history, training_steps=[20])


def test_feedback_comes_from_latest_record_and_converts_temperature(history):
    fields = ["chatter_probability", "tool_wear", "surface_roughness", "vibration_rms",
              "temperature_over_100c", "cutting_force_norm", "mrr_norm", "confidence"]
    with pytest.raises(InvalidStateError, match="反馈"):
        prepare(history, feedback_fields=fields)
    latest = history["samples"][-1]["current_state"]
    latest.update(chatter_probability=0.2, surface_roughness=1.5, cutting_force_norm=0.3, mrr_norm=0.4, confidence=0.9)
    np.testing.assert_allclose(prepare(history, feedback_fields=fields).initial_state,
                               [0.2, 0.1, 1.5, 0.2, 0.25, 0.3, 0.4, 0.9])
    latest["confidence"] = 2
    with pytest.raises(InvalidStateError, match="0到1"):
        prepare(history, feedback_fields=fields)
    latest["confidence"] = -1
    with pytest.raises(InvalidStateError, match="负数"):
        prepare(history, feedback_fields=fields)


@pytest.mark.parametrize("state_feedback", [False, True])
def test_real_network_receives_full_history_and_future_actions(history, tmp_path, monkeypatch, state_feedback):
    torch = pytest.importorskip("torch")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import world_model as api
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.predictor import TrajectoryPredictor
    from app.services import world_model_inputs
    from app.services.world_model_service import WorldModelService

    torch.manual_seed(4)
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, feature_dim=4, d_model=8, fused_dim=16,
                              delta_prediction=state_feedback)
    if state_feedback:
        history["samples"][-1]["current_state"].update(
            chatter_probability=0.2, surface_roughness=1.5, cutting_force_norm=0.3, mrr_norm=0.4, confidence=0.9)
    path = tmp_path / "history.pt"
    torch.save({"model_config": config.to_dict(), "model_state_dict": WorldModelNet(config).state_dict(),
                "trainer_type": "FusionWorldModelTrainer",
                "state_feedback": state_feedback,
                "input_contract": {"schema_version": 1, "history_steps": [3], "training_horizon": 2}}, path)
    predictor = TrajectoryPredictor(device="cpu")
    predictor.load_model("model://world_model/history", str(path))
    assert predictor._model.training is False
    prepared = prepare(history)
    geometry = prepared.states[0]["geometry"]
    calls = []
    monkeypatch.setattr(world_model_inputs, "prepare_geometry", lambda *args: calls.append(args) or {"geometry": geometry})
    service = WorldModelService()
    monkeypatch.setattr(service, "_get_or_load_predictor", lambda _: predictor)
    monkeypatch.setattr(api, "get_world_model_service", lambda: service)
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    captured = {}
    def hook(model, args, kwargs):
        captured.update(kwargs)
    handle = predictor._model.register_forward_pre_hook(hook, with_kwargs=True)
    app = FastAPI()
    app.include_router(api.router)
    body = {"model_uri": "model://world_model/history", "current_state": {},
            "feature_task_id": "reviewed", "millimeters_per_unit": 1, "feature_task_updated_at": 2,
            "fusion_history": history, "candidate_action": {name: 0.4 for name in ActionField.all()}, "horizon": 2}
    try:
        client = TestClient(app)
        response = client.post("/api/v1/world-model/predict", json=body).json()
        assert response["code"] == 0, response
        assert calls == [("reviewed", 1.0, 2.0)]
        assert captured["unified_states"][0].shape == (1, 3, 9)
        np.testing.assert_allclose(captured["unified_states"][1][0, :, 0], [1, 2, 3])
        np.testing.assert_allclose(captured["actions"][0, :, 0], [0.1, 0.2, 0.3, 0.4, 0.4])
        assert response["data"]["model_info"]["input_history"]["history_steps"] == 3
        assert response["data"]["model_info"]["weights_source"] == "checkpoint"
        if state_feedback:
            np.testing.assert_allclose(captured["initial_state"][0], [0.2, 0.1, 1.5, 0.2, 0.25, 0.3, 0.4, 0.9])
        repeat = client.post("/api/v1/world-model/predict", json=body).json()
        assert repeat["data"] == response["data"]  # CPU显式选择也必须关闭dropout。
        # 保持末帧不变，仅扰动更早记录：网络预测须发生变化。
        first = np.asarray([step["predicted_state"]["surface_roughness"] for step in response["data"]["predicted_trajectory"]])
        body["fusion_history"]["samples"][0]["current_state"]["spindle_speed"] = 100
        changed = client.post("/api/v1/world-model/predict", json=body).json()
        assert changed["code"] == 0, changed
        second = np.asarray([step["predicted_state"]["surface_roughness"] for step in changed["data"]["predicted_trajectory"]])
        assert np.max(np.abs(first - second)) > 1e-7
        body["current_state"] = {"temperature": 30}
        assert client.post("/api/v1/world-model/predict", json=body).json()["code"] != 0
        body["current_state"] = {}
        body["feature_task_id"] = None
        assert client.post("/api/v1/world-model/predict", json=body).json()["code"] != 0
    finally:
        handle.remove()


def test_predictor_requires_real_actions_and_aligned_histories(history, tmp_path):
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig
    from app.plugins.world_model.predictor import TrajectoryPredictor

    predictor = TrajectoryPredictor(config=WorldModelConfig(hidden_dim=8, num_lstm_layers=1,
        num_ltc_layers=1, feature_dim=4, d_model=8, fused_dim=16), device="cpu")
    predictor.load_model("model://world_model/interface-only")
    prepared = prepare(history)
    kwargs = {"unified_history": prepared.states, "candidate_action": np.zeros((2, 4)), "horizon": 2}
    with pytest.raises(ValueError, match="实际历史动作"):
        predictor.predict(**kwargs)
    with pytest.raises(ValueError, match="一一对应"):
        predictor.predict(**kwargs, history_actions=np.zeros((2, 4)))
    with pytest.raises(ValueError, match="有限数"):
        predictor.predict(**kwargs, history_actions=np.full((3, 4), np.nan))
    with pytest.raises(ValueError, match="混用"):
        predictor.predict(**kwargs, history_actions=prepared.actions, unified_state=prepared.states[0])
    with pytest.raises(ValueError, match="2至256"):
        predictor.predict(unified_history=[], history_actions=np.zeros((0, 4)), candidate_action=np.zeros((2, 4)), horizon=2)


def test_legacy_multiframe_request_keeps_matching_action_time_dimension():
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig
    from app.plugins.world_model.predictor import TrajectoryPredictor

    predictor = TrajectoryPredictor(config=WorldModelConfig(use_fusion=False, hidden_dim=8,
        num_lstm_layers=1, num_ltc_layers=1), device="cpu")
    predictor.load_model("model://world_model/interface-only")
    result = predictor.predict(current_state=np.ones((3, 8), dtype=np.float32),
                               candidate_action=np.zeros((2, 4), dtype=np.float32), horizon=2)
    assert result.predicted_trajectory.shape == (2, 8)
