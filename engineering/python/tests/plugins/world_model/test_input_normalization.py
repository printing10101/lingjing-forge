"""融合标准化只拟合训练输入；训练、重载和预测共用一次变换。"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

from app.plugins.world_model.input_normalization import fit_fusion_normalization


def training_arrays(n=5):
    rng = np.random.default_rng(42)
    geo = np.full((n, 3, 9), 60, dtype=np.float32)
    geo[:, :, 7:] = [0.5, 0.2]
    dyn = rng.uniform(0.1, 1, size=(n, 3, 6)).astype(np.float32)
    dyn[:, :, 0] += 10400
    dyn[:, :, 1] += 1555
    dyn[:, :, 5] = 25
    return geo, dyn


def test_training_fit_centers_constants_and_records_training_input_identity():
    geo, dyn = training_arrays()
    spec = fit_fusion_normalization(geo, dyn)
    assert spec["train_windows"] == 5
    assert spec["train_history_points"] == 15
    assert spec["fit_scope"] == "train_history_inputs_only"
    assert spec["geometry"]["scale"] == [1] * 9
    assert spec["dynamics"]["scale"][5] == 1
    normalized = (dyn - np.asarray(spec["dynamics"]["mean"])) / np.asarray(spec["dynamics"]["scale"])
    np.testing.assert_allclose(normalized.mean(axis=(0, 1)), 0, atol=1e-10)
    np.testing.assert_allclose(normalized.std(axis=(0, 1))[:5], 1)
    changed = dyn.copy()
    changed[0, 0, 0] += 1
    assert fit_fusion_normalization(geo, changed)["fit_inputs_sha256"] != spec["fit_inputs_sha256"]


@pytest.mark.parametrize("change", [
    lambda s: s.update(schema_version=2), lambda s: s.update(method="guessed_scale"),
    lambda s: s.update(fit_scope="train_and_validation"), lambda s: s.update(constant_scale=0),
    lambda s: s.update(dynamics_order=[]), lambda s: s.pop("geometry"),
    lambda s: s["geometry"].update(mean=[0]), lambda s: s["dynamics"].update(scale=[0] * 6),
    lambda s: s["dynamics"].update(scale=[1e-100] * 6),
    lambda s: s["dynamics"].update(mean=[float("nan")] * 6),
    lambda s: s["dynamics"].update(mean=[1e100] * 6),
    lambda s: s["dynamics"].update(mean=["bad"] * 6),
])
def test_invalid_normalization_contract_rejected(change):
    from app.plugins.world_model.net import WorldModelConfig
    spec = fit_fusion_normalization(*training_arrays())
    change(spec)
    with pytest.raises(ValueError):
        WorldModelConfig(feature_dim=4, input_normalization=spec).validate()


def test_invalid_fit_inputs_and_legacy_config_rejected():
    from app.plugins.world_model.net import WorldModelConfig
    geo, dyn = training_arrays()
    for invalid_geo, invalid_dyn in ((geo[0], dyn), (geo, dyn[:, :2]), (geo[:0], dyn[:0]),
                                     (geo, np.full_like(dyn, np.nan))):
        with pytest.raises(ValueError):
            fit_fusion_normalization(invalid_geo, invalid_dyn)
    with pytest.raises(ValueError, match="原始"):
        WorldModelConfig(use_fusion=False, input_normalization=fit_fusion_normalization(geo, dyn)).validate()


def test_model_and_predictor_apply_exactly_one_transform_and_reload(tmp_path):
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.predictor import TrajectoryPredictor
    geo, dyn = training_arrays(n=1)
    spec = fit_fusion_normalization(geo, dyn)
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, feature_dim=4, d_model=8,
                              fused_dim=16, input_normalization=spec)
    normalized = WorldModelNet(config).eval()
    plain_config = WorldModelConfig(**{key: value for key, value in config.to_dict().items() if key != "input_normalization"})
    plain = WorldModelNet(plain_config).eval()
    plain.load_state_dict({key: value for key, value in normalized.state_dict().items() if not key.startswith("input_normalizer.")})
    manual_geo = (torch.from_numpy(geo) - torch.tensor(spec["geometry"]["mean"])) / torch.tensor(spec["geometry"]["scale"])
    manual_dyn = (torch.from_numpy(dyn) - torch.tensor(spec["dynamics"]["mean"])) / torch.tensor(spec["dynamics"]["scale"])
    actions = torch.zeros(1, 5, 4)
    with torch.inference_mode():
        actual = normalized(states=None, actions=actions, horizon=2, unified_states=(torch.from_numpy(geo), torch.from_numpy(dyn)))["predicted_trajectory"]
        expected = plain(states=None, actions=actions, horizon=2, unified_states=(manual_geo, manual_dyn))["predicted_trajectory"]
    torch.testing.assert_close(actual, expected, rtol=0, atol=0)
    checkpoint = {"model_config": config.to_dict(), "model_state_dict": normalized.state_dict(), "trainer_type": "FusionWorldModelTrainer"}
    path = tmp_path / "normalized.pt"
    torch.save(checkpoint, path)
    predictor = TrajectoryPredictor(device="cpu")
    predictor.load_model("model://world_model/normalized", str(path))
    states = [{"geometry": {"bbox_dimensions": row[:3].tolist(), "feature_vector": row[3:7].tolist(),
                            "symmetry_score": float(row[7]), "complexity_score": float(row[8])},
               "dynamics": dict(zip(["spindle_speed", "feed_rate", "depth_of_cut", "tool_wear", "vibration_rms", "temperature"], dynamic.tolist()))}
              for row, dynamic in zip(geo[0], dyn[0])]
    result = predictor.predict(unified_history=states, history_actions=np.zeros((3, 4)), candidate_action=np.zeros((2, 4)), horizon=2)
    np.testing.assert_array_equal(result.predicted_trajectory, actual[0].numpy())
    # 原始数组不被就地标准化；存盘配置也不共享调用方可变字典。
    np.testing.assert_array_equal(dyn, training_arrays(n=1)[1])
    copied = config.to_dict()
    copied["input_normalization"]["dynamics"]["mean"][0] = -1
    assert config.input_normalization["dynamics"]["mean"][0] > 10000
    for mutation in ("metadata_missing", "buffer_changed", "buffer_missing"):
        bad = deepcopy(checkpoint)
        if mutation == "metadata_missing":
            bad["model_config"].pop("input_normalization")
        elif mutation == "buffer_changed":
            bad["model_state_dict"]["input_normalizer.dynamics_scale"][0] += 1
        else:
            bad["model_state_dict"].pop("input_normalizer.dynamics_scale")
        torch.save(bad, path)
        with pytest.raises(RuntimeError):
            predictor.load_model("model://world_model/bad", str(path))
        assert predictor._model is None


def test_normalizer_checks_forward_shape_finiteness_and_legacy_keys():
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.input_normalization import FusionInputNormalizer
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    geo, dyn = training_arrays()
    module = FusionInputNormalizer(fit_fusion_normalization(geo, dyn), 9)
    with pytest.raises(ValueError, match="列数"):
        module(torch.zeros(1, 1, 8), torch.zeros(1, 1, 6))
    with pytest.raises(ValueError, match="非有限"):
        module(torch.full((1, 1, 9), float("inf")), torch.zeros(1, 1, 6))
    legacy = WorldModelNet(WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1))
    assert not any(key.startswith("input_normalizer.") for key in legacy.state_dict())
    assert "input_normalization" not in legacy.config.to_dict()


def test_direct_api_receives_original_units_and_applies_the_checkpoint_transform(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1 import world_model as api
    from app.contracts.world_model import ActionField
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.training import weights_resolver
    from app.services import world_model_inputs
    from app.services.world_model_service import WorldModelService
    geo, dyn = training_arrays(n=1)
    spec = fit_fusion_normalization(geo, dyn)
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, feature_dim=4, d_model=8,
                              fused_dim=16, input_normalization=spec)
    weights = tmp_path / "world_model/normalized-api.pt"
    weights.parent.mkdir()
    torch.save({"model_config": config.to_dict(), "model_state_dict": WorldModelNet(config).state_dict(),
                "trainer_type": "FusionWorldModelTrainer"}, weights)
    monkeypatch.setattr(weights_resolver, "DEFAULT_MODELS_DIR", str(tmp_path))
    geometry = {"bbox_dimensions": geo[0, 0, :3].tolist(), "feature_vector": geo[0, 0, 3:7].tolist(),
                "symmetry_score": 0.5, "complexity_score": 0.2}
    monkeypatch.setattr(world_model_inputs, "prepare_geometry", lambda *args: {"geometry": geometry})
    service = WorldModelService()
    monkeypatch.setattr(api, "get_world_model_service", lambda: service)
    monkeypatch.setenv("LNN_PERMISSION_ENFORCED", "false")
    uri = "model://world_model/normalized-api"
    predictor = service._get_or_load_predictor(uri)
    captured = []
    hook = predictor._model.dynamics_encoder.register_forward_pre_hook(lambda _, args: captured.append(args[0].detach()))
    app = FastAPI()
    app.include_router(api.router)
    try:
        response = TestClient(app).post("/api/v1/world-model/predict", json={
            "model_uri": uri, "current_state": {}, "feature_task_id": "reviewed", "millimeters_per_unit": 1,
            "horizon": 2, "candidate_action": {name: 0 for name in ActionField.all()},
            "fusion_history": {"schema_version": 1, "source": "synthetic regression fixture", "sample_period_seconds": 1,
                "samples": [{"time_seconds": index, "current_state": dict(zip(spec["dynamics_order"], row.tolist())),
                             "action": {name: 0 for name in ActionField.all()}} for index, row in enumerate(dyn[0])]},
        }).json()
        assert response["code"] == 0, response
        assert response["data"]["model_info"]["weights_source"] == "checkpoint"
        device = captured[0].device
        expected = (torch.from_numpy(dyn[0]).to(device) - torch.tensor(spec["dynamics"]["mean"], device=device)) / torch.tensor(spec["dynamics"]["scale"], device=device)
        torch.testing.assert_close(captured[0], expected, rtol=0, atol=0)
        requirements = service.input_requirements(uri)
        assert requirements["input_normalization"] == "train_zscore"
        assert requirements["caller_input_units"] == "original"
    finally:
        hook.remove()


def test_training_cli_fits_only_training_partition_and_preserves_existing_version(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    script = Path(__file__).resolve().parents[3] / "scripts/train_world_model.py"
    module_spec = importlib.util.spec_from_file_location("wm_normalized_training_cli", script)
    cli = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    geo, dyn = training_arrays(n=60)
    target = np.ones((60, 2, 8), dtype=np.float32)
    training_path, validation_path = tmp_path / "train.npz", tmp_path / "val.npz"
    np.savez(training_path, geometry=geo, dynamics=dyn, actions=np.zeros((60, 5, 4), dtype=np.float32), target=target)
    np.savez(validation_path, geometry=geo[:4], dynamics=dyn[:4] + 100000,
             actions=np.zeros((4, 5, 4), dtype=np.float32), target=target[:4] * 1000)
    monkeypatch.setattr(sys, "argv", [str(script), "--dataset", str(training_path), "--val-dataset", str(validation_path),
        "--version", "norm-test", "--models-dir", str(tmp_path / "models"), "--normalize-inputs", "--no-tracking",
        "--device", "cpu", "--epochs", "1", "--batch-size", "64", "--no-amp", "--seed", "43"])
    assert cli.main() == 0
    path = tmp_path / "models/world_model/norm-test.pt"
    payload = torch.load(path, weights_only=True)
    assert payload["model_config"]["seed"] == 43
    assert payload["model_config"]["input_normalization"] == fit_fusion_normalization(geo, dyn)
    assert payload["model_selection"]["applied"] is True
    assert payload["input_contract"]["history_steps"] == [3]
    original = path.read_bytes()
    assert cli.main() == 1
    assert path.read_bytes() == original


def test_matched_pilot_uses_same_initial_parameters_and_keeps_failures(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    script = Path(__file__).resolve().parents[3] / "scripts/wm_train_normalization_pilot.py"
    monkeypatch.syspath_prepend(str(script.parent))
    module_spec = importlib.util.spec_from_file_location("wm_normalization_pilot_test", script)
    pilot = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(pilot)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    for split, count in (("train", 6), ("val", 4)):
        geo, dyn = training_arrays(n=count)
        if split == "val":
            dyn[:, :, 0] += 0.25
        states = np.ones((count, 3, 8), dtype=np.float32)
        states[:, :, 1], states[:, :, 3], states[:, :, 4] = dyn[:, :, 3], dyn[:, :, 4], dyn[:, :, 5] / 100
        np.savez(data_dir / f"cv_c6_{split}.npz", geometry=geo, dynamics=dyn,
                 actions=np.zeros((count, 5, 4), dtype=np.float32), target=states[:, :2],
                 state_history=states, initial_state=states[:, -1])
    output = tmp_path / "experiment"
    result = pilot.run_pilot(data_dir, output, groups=["c6"], epochs=1, seed=42, budget_seconds=60)
    assert result["status"] == "completed"
    assert len(result["runs"]) == 2
    raw, normalized = result["runs"]
    assert raw["initial_parameters_sha256"] == normalized["initial_parameters_sha256"]
    assert raw["trainable_parameters"] == normalized["trainable_parameters"]
    checkpoint = torch.load(normalized["checkpoint"], weights_only=True)
    assert checkpoint["model_config"]["input_normalization"]["train_windows"] == 6
    with pytest.raises(FileExistsError):
        pilot.run_pilot(data_dir, output, groups=["c6"], epochs=1, seed=42, budget_seconds=60)
    for groups, epochs, budget in ((["unknown"], 1, 60), (["c6", "c6"], 1, 60), (["c6"], 0, 60), (["c6"], 1, 0)):
        with pytest.raises(ValueError):
            pilot.run_pilot(data_dir, tmp_path / "invalid", groups=groups, epochs=epochs, seed=42, budget_seconds=budget)
    with pytest.raises(TimeoutError):
        pilot.run_pilot(data_dir, tmp_path / "budget", groups=["c6"], epochs=1, seed=42, budget_seconds=1e-8)
    import json
    assert json.loads((tmp_path / "budget/ledger.json").read_text(encoding="utf-8"))["status"] == "failed"
    monkeypatch.setattr(sys, "argv", [str(script), "--data-dir", str(data_dir), "--out-dir", str(output)])
    assert pilot.main() == 1
