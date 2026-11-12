"""界面几何输入：真实网格、审核结果、单位换算及无数据分支。"""

from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from app.contracts.world_model import InvalidStateError
from app.feature_extraction.feature_store import ExtractedFeature, FeatureExtractionTask, FeatureStore
from app.services import world_model_inputs as inputs


@pytest.fixture
def source(tmp_path, monkeypatch):
    mesh_path = tmp_path / "part.stl"
    trimesh.creation.box(extents=[10, 20, 30]).export(mesh_path)
    store = FeatureStore(tmp_path / "tasks")
    task = FeatureExtractionTask(
        task_id="reviewed", created_at=1, updated_at=2, status="reviewed", input_mesh_path=str(mesh_path),
        features=[ExtractedFeature("plane", "plane", {"normal": [0, 0, 1], "area_mm2": 50}, 0.9, review_status="confirmed"),
                  ExtractedFeature("hole", "hole", {"radius_mm": 1}, 0.8, review_status="edited", edited_params={"radius_mm": 3})],
    )
    store.create(task)
    monkeypatch.setattr(inputs, "get_feature_store", lambda: store)
    return task, store, mesh_path


def test_mesh_and_edited_features_share_scale_without_mutating_source(source):
    task, _, _ = source
    result = inputs.prepare_geometry(task.task_id, 2)
    assert result["geometry"]["bbox_dimensions"] == [20, 40, 60]
    assert result["geometry"]["feature_vector"][0] == pytest.approx(200 / 10000)
    assert result["geometry"]["feature_vector"][24] == pytest.approx(6 / 50)
    assert result["source"]["scale_source"] == "user_supplied"
    assert task.features[1].edited_params["radius_mm"] == 3


@pytest.mark.parametrize("scale", [None, 0, -1, float("inf"), float("nan")])
def test_uncalibrated_mesh_needs_explicit_valid_scale(source, scale):
    with pytest.raises(InvalidStateError, match="尺度"):
        inputs.prepare_geometry("reviewed", scale)


def test_review_and_source_must_exist(source):
    task, _, _ = source
    with pytest.raises(InvalidStateError, match="已变更"):
        inputs.prepare_geometry("reviewed", 1, expected_updated_at=1)
    with pytest.raises(InvalidStateError, match="未找到"):
        inputs.prepare_geometry("missing", 1)
    task.status = "running"
    assert inputs.list_geometry_sources() == []
    with pytest.raises(InvalidStateError, match="审核"):
        inputs.prepare_geometry("reviewed", 1)
    task.status = "reviewed"
    task.features[0].review_status = "pending"
    with pytest.raises(InvalidStateError, match="审核"):
        inputs.prepare_geometry("reviewed", 1)
    task.features = []
    with pytest.raises(InvalidStateError, match="没有已确认"):
        inputs.prepare_geometry("reviewed", 1)


def test_calibration_is_bound_to_same_upstream_mesh(source, monkeypatch):
    import app.image_to_3d as reconstruction

    task, _, path = source
    task.source_reconstruction_task_id = "upstream"
    upstream = SimpleNamespace(status="succeeded", calibrated=True, output_mesh_path=str(path))
    monkeypatch.setattr(reconstruction, "get_task_store", lambda: SimpleNamespace(get=lambda _: upstream))
    assert inputs.list_geometry_sources()[0]["calibrated"] is True
    assert inputs.prepare_geometry("reviewed")["source"]["scale_source"] == "upstream_calibration"
    with pytest.raises(InvalidStateError, match="重复缩放"):
        inputs.prepare_geometry("reviewed", 2)
    upstream.output_mesh_path = str(path.with_name("another.stl"))
    assert inputs.calibrated_mesh(task) is False
    with pytest.raises(InvalidStateError, match="尺度"):
        inputs.prepare_geometry("reviewed")


def test_removed_mesh_is_not_replaced_by_placeholder(source):
    _, _, path = source
    path.unlink()
    with pytest.raises(InvalidStateError, match="无法读取"):
        inputs.prepare_geometry("reviewed", 1)


@pytest.mark.parametrize("vertices", [np.zeros((4, 3)), np.full((4, 3), np.nan), np.zeros((3, 3))])
def test_invalid_mesh_vertices_are_rejected(source, monkeypatch, vertices):
    from app.feature_extraction import pipeline
    monkeypatch.setattr(pipeline, "_load_mesh", lambda *_: (vertices, []))
    with pytest.raises(InvalidStateError):
        inputs.prepare_geometry("reviewed", 1)


@pytest.mark.parametrize("attribute,value", [("confidence", float("nan")), ("area_mm2", -1)])
def test_invalid_reviewed_feature_is_rejected(source, attribute, value):
    task, _, _ = source
    if attribute == "confidence":
        task.features[0].confidence = value
    else:
        task.features[0].params[attribute] = value
    with pytest.raises(InvalidStateError):
        inputs.prepare_geometry("reviewed", 1)


def test_requirements_and_geometry_api(source, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import world_model as api
    from app.services.world_model_service import WorldModelService

    service = WorldModelService()
    predictor = SimpleNamespace(config=SimpleNamespace(use_fusion=True, feature_dim=32), state_feedback=True)
    monkeypatch.setattr(service, "_get_or_load_predictor", lambda _: predictor)
    monkeypatch.setattr(api, "get_world_model_service", lambda: service)
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    result = client.get("/api/v1/world-model/input-requirements", params={"model_uri": "model://world_model/test"}).json()
    assert result["data"]["geometry_sources"][0]["task_id"] == "reviewed"
    assert "surface_roughness" in result["data"]["feedback_fields"]
    response = client.post("/api/v1/world-model/prepare-geometry", json={"feature_task_id": "reviewed", "millimeters_per_unit": 1}).json()
    assert response["code"] == 0
    assert response["data"]["geometry"]["bbox_dimensions"] == [10, 20, 30]
    assert client.post("/api/v1/world-model/prepare-geometry", json={"feature_task_id": "reviewed"}).json()["code"] != 0


@pytest.mark.asyncio
async def test_prediction_reloads_task_geometry_and_requires_all_dynamics(source, monkeypatch):
    from app.contracts.world_model import ActionField, WorldModelPredictRequest
    from app.services.world_model_service import WorldModelService

    captured = {}
    def predict(**kwargs):
        captured.update(kwargs)
        return object()
    service = WorldModelService()
    predictor = SimpleNamespace(config=SimpleNamespace(use_fusion=True), state_feedback=False, predict=predict)
    monkeypatch.setattr(service, "_get_or_load_predictor", lambda _: predictor)
    monkeypatch.setattr(service, "_build_response", lambda **_: "predicted")
    state = {"spindle_speed": 8000, "feed_rate": 1000, "depth_of_cut": 1, "tool_wear": 0.1, "vibration_rms": 0.3, "temperature": 25}
    req = WorldModelPredictRequest(current_state=state, candidate_action={k: 0 for k in ActionField.all()},
                                  feature_task_id="reviewed", millimeters_per_unit=2)
    assert await service.predict(req) == "predicted"
    assert captured["unified_state"]["geometry"]["bbox_dimensions"] == [20, 40, 60]
    del state["tool_wear"]
    with pytest.raises(InvalidStateError, match="tool_wear"):
        await service.predict(req)
    state["tool_wear"] = -0.1
    with pytest.raises(InvalidStateError, match="非负"):
        await service.predict(req)
    req.unified_state = {"geometry": {}}
    with pytest.raises(InvalidStateError, match="同时提供"):
        await service.predict(req)
    req.unified_state = None
    predictor.config.use_fusion = False
    with pytest.raises(InvalidStateError, match="不使用"):
        await service.predict(req)


def test_full_source_to_checkpoint_api_chain(source, tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import world_model as api
    from app.contracts.world_model import ActionField
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.training import weights_resolver
    from app.services.world_model_service import WorldModelService

    cfg = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, use_fusion=True)
    model = WorldModelNet(cfg)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.state_head.bias.copy_(torch.tensor([0.2, 0.1, 1.5, 0.4, 0.25, 0.3, 0.5, 0.9]))
    weights = tmp_path / "world_model" / "ui-source.pt"
    weights.parent.mkdir()
    torch.save({"model_config": cfg.to_dict(), "model_state_dict": model.state_dict(), "trainer_type": "FusionWorldModelTrainer"}, weights)
    monkeypatch.setattr(weights_resolver, "DEFAULT_MODELS_DIR", str(tmp_path))
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    monkeypatch.setattr(api, "get_world_model_service", lambda: WorldModelService())
    app = FastAPI()
    app.include_router(api.router)
    result = TestClient(app).post("/api/v1/world-model/predict", json={
        "model_uri": "model://world_model/ui-source", "horizon": 2,
        "feature_task_id": "reviewed", "millimeters_per_unit": 2,
        "current_state": {"spindle_speed": 8000, "feed_rate": 1000, "depth_of_cut": 1, "tool_wear": 0.1, "vibration_rms": 0.3, "temperature": 25},
        "candidate_action": {key: 0 for key in ActionField.all()},
    }).json()
    assert result["code"] == 0, result
    assert result["data"]["model_info"]["weights_source"] == "checkpoint"
    assert result["data"]["predicted_trajectory"][0]["surface_roughness"] == pytest.approx(1.5)
