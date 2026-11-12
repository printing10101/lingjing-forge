"""损失和增量单位有独立契约；尺度只从训练残差拟合，推理保持原单位。"""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from app.plugins.world_model.state_loss_normalization import fit_state_scale, validate_state_scale


def data(n=6, offset=0):
    rng = np.random.default_rng(42 + offset)
    geo = np.ones((n, 3, 9), dtype=np.float32)
    dyn = rng.uniform(0.1, 0.3, (n, 3, 6)).astype(np.float32)
    dyn[:, :, 0], dyn[:, :, 1], dyn[:, :, 5] = 10000 + offset, 1000, 25
    state = np.ones((n, 3, 8), dtype=np.float32)
    state[:, :, 0], state[:, :, 1], state[:, :, 2] = 0.2, dyn[:, :, 3], 1.5
    state[:, :, 3], state[:, :, 4], state[:, :, 5] = dyn[:, :, 4], 0.25, 0.3
    target = np.repeat(state[:, -1:], 2, axis=1)
    target[:, :, 1] += [0.0001, 0.0002]
    target[:, :, 2] += [0.0003, 0.0006]
    target[:, :, 3] += [-0.01, 0.02]
    target[:, :, 5] += [-0.02, 0.03]
    return dict(geometry=geo, dynamics=dyn, actions=np.zeros((n, 5, 4), dtype=np.float32),
                target=target, state_history=state, initial_state=state[:, -1])


def make_trainer(tmp_path, *, loss=None, selection=None, weights=None, amp=False):
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.training.fusion_trainer import FusionWorldModelTrainer
    config = WorldModelConfig(feature_dim=4, hidden_dim=8, d_model=8, fused_dim=16,
                              num_lstm_layers=1, num_ltc_layers=1, delta_prediction=True)
    return FusionWorldModelTrainer(WorldModelNet(config), model_uri="model://world_model/loss-test", epochs=1,
        device="cpu", use_amp=amp, track_experiment=False, models_dir=str(tmp_path),
        loss_normalization=loss, selection_loss_normalization=selection, loss_weights=weights)


def test_fit_uses_training_changes_and_keeps_constant_channels_supervised():
    arrays = data()
    spec = fit_state_scale(arrays["target"], arrays["initial_state"])
    expected = np.sqrt(((arrays["target"].astype(np.float64) - arrays["initial_state"][:, None]) ** 2).mean(axis=(0, 1)))
    assert spec["constant_channels"] == [0, 4, 6, 7]
    assert all(spec["scale"][index] == 1 for index in spec["constant_channels"])
    np.testing.assert_allclose(np.asarray(spec["scale"])[[1, 2, 3, 5]], expected[[1, 2, 3, 5]])
    assert spec["train_windows"] == 6
    changed = arrays["target"].copy()
    changed[:, :, 1] += 0.1
    assert fit_state_scale(changed, arrays["initial_state"])["fit_inputs_sha256"] != spec["fit_inputs_sha256"]


@pytest.mark.parametrize("change", [
    lambda s: s.update(method="other"), lambda s: s.update(fit_scope="all_labels"),
    lambda s: s.update(state_order=[]), lambda s: s.update(scale=[1] * 7),
    lambda s: s.update(scale=[0] * 8), lambda s: s.update(scale=[1e-100] * 8),
    lambda s: s.update(scale=[float("nan")] * 8), lambda s: s.update(scale=["bad"] * 8),
])
def test_invalid_state_scale_rejected(change):
    arrays = data()
    spec = fit_state_scale(arrays["target"], arrays["initial_state"])
    change(spec)
    with pytest.raises(ValueError):
        validate_state_scale(spec)


def test_invalid_fit_inputs_rejected():
    for target, initial in ((np.zeros((0, 2, 8)), np.zeros((0, 8))), (np.zeros((2, 2, 7)), np.zeros((2, 8))),
                            (np.full((2, 2, 8), np.nan), np.zeros((2, 8)))):
        with pytest.raises(ValueError):
            fit_state_scale(target, initial)


def test_training_loss_selection_loss_and_amp_are_separate_and_finite(tmp_path):
    torch = pytest.importorskip("torch")
    arrays = data()
    spec = fit_state_scale(arrays["target"], arrays["initial_state"])
    trainer = make_trainer(tmp_path, selection=spec)
    target, prediction = torch.zeros(1, 2, 8), torch.ones(1, 2, 8, requires_grad=True)
    assert trainer._compute_loss(prediction, target).item() == 1
    expected = ((prediction / torch.tensor(spec["scale"])) ** 2).mean()
    torch.testing.assert_close(trainer._compute_loss(prediction, target, validation=True), expected)
    normalized = make_trainer(tmp_path, loss=spec, amp=True)
    half_loss = normalized._compute_loss(prediction.half(), target.half())
    assert half_loss.dtype == torch.float32 and torch.isfinite(half_loss)
    assert normalized.use_amp is False
    normalized._compute_loss(prediction, target).backward()
    assert torch.isfinite(prediction.grad).all()
    checkpoint = normalized.save_checkpoint("loss-contract")
    restored = make_trainer(tmp_path)
    restored.load_checkpoint(checkpoint)
    assert restored.loss_normalization == spec
    assert restored.selection_loss_normalization is None
    with pytest.raises(RuntimeError, match="叠加"):
        make_trainer(tmp_path, loss=spec, weights=[1] * 8)
    with pytest.raises(RuntimeError, match="非有限"):
        trainer._compute_loss(torch.full_like(prediction, float("nan")), target)


@pytest.mark.parametrize("weights", [[0] * 8, [float("nan")] * 8, [float("inf")] * 8, [-1] * 8, [1] * 7])
def test_loss_weights_cannot_create_fake_zero_or_nonfinite_training(tmp_path, weights):
    with pytest.raises(RuntimeError):
        make_trainer(tmp_path, weights=weights)


def test_scaled_head_returns_physical_units_requires_feedback_and_rejects_bad_checkpoints(tmp_path):
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    from app.plugins.world_model.predictor import TrajectoryPredictor
    arrays = data(n=1)
    spec = fit_state_scale(arrays["target"], arrays["initial_state"])
    config = WorldModelConfig(feature_dim=4, hidden_dim=8, d_model=8, fused_dim=16,
        num_lstm_layers=1, num_ltc_layers=1, delta_prediction=True, delta_output_normalization=spec)
    model = WorldModelNet(config).eval()
    torch.nn.init.zeros_(model.delta_head.weight)
    torch.nn.init.zeros_(model.delta_head.bias)
    arguments = dict(states=None, actions=torch.from_numpy(arrays["actions"]), horizon=2,
                     unified_states=(torch.from_numpy(arrays["geometry"]), torch.from_numpy(arrays["dynamics"])))
    initial = torch.from_numpy(arrays["initial_state"])
    with pytest.raises(ValueError, match="反馈"):
        model(**arguments)
    with torch.inference_mode():
        persistence = model(**arguments, initial_state=initial)["predicted_trajectory"]
    torch.testing.assert_close(persistence, initial[:, None].expand(-1, 2, -1), rtol=0, atol=0)
    with torch.no_grad():
        model.delta_head.bias[1] = 1
    with torch.inference_mode():
        prediction = model(**arguments, initial_state=initial)["predicted_trajectory"]
    expected = initial.clone()
    expected[:, 1] += spec["scale"][1]
    torch.testing.assert_close(prediction[:, 0], expected)
    checkpoint = {"model_config": config.to_dict(), "model_state_dict": model.state_dict(),
                  "trainer_type": "FusionWorldModelTrainer", "state_feedback": True}
    path = tmp_path / "scale.pt"
    torch.save(checkpoint, path)
    predictor = TrajectoryPredictor(device="cpu")
    predictor.load_model("model://world_model/scale", str(path))
    latest = arrays["dynamics"][0, -1]
    result = predictor.predict(unified_state={
        "geometry": {"bbox_dimensions": [1, 1, 1], "feature_vector": [1] * 4, "symmetry_score": 1, "complexity_score": 1},
        "dynamics": dict(zip(["spindle_speed", "feed_rate", "depth_of_cut", "tool_wear", "vibration_rms", "temperature"], latest.tolist())),
    }, initial_state=arrays["initial_state"][0], candidate_action=np.zeros((2, 4)), horizon=2)
    np.testing.assert_array_equal(result.predicted_trajectory[0], prediction[0, 0].numpy())
    for change in ("missing_metadata", "bad_buffer", "missing_buffer", "no_feedback"):
        bad = deepcopy(checkpoint)
        if change == "missing_metadata":
            bad["model_config"].pop("delta_output_normalization")
        elif change == "bad_buffer":
            bad["model_state_dict"]["delta_output_scaler.scale"][1] *= 2
        elif change == "missing_buffer":
            bad["model_state_dict"].pop("delta_output_scaler.scale")
        else:
            bad["state_feedback"] = False
        torch.save(bad, path)
        with pytest.raises((ValueError, RuntimeError)):
            predictor.load_model("model://world_model/bad", str(path))
    with pytest.raises(ValueError):
        WorldModelConfig(delta_output_normalization=spec).validate()
    with pytest.raises(ValueError):
        model.delta_output_scaler(torch.zeros(1, 7))
    with pytest.raises(ValueError):
        model.delta_output_scaler(torch.full((1, 8), float("inf")))


def test_factorial_pilot_is_matched_and_uses_training_scales(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    script = Path(__file__).resolve().parents[3] / "scripts/wm_train_state_scale_pilot.py"
    monkeypatch.syspath_prepend(str(script.parent))
    module_spec = importlib.util.spec_from_file_location("wm_state_scale_pilot_test", script)
    pilot = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(pilot)
    source = tmp_path / "data"
    source.mkdir()
    for split, count, offset in (("train", 6, 0), ("val", 4, 1)):
        np.savez(source / f"cv_c6_{split}.npz", **data(n=count, offset=offset))
    output = tmp_path / "experiment"
    ledger = pilot.run_pilot(source, output, groups=["c6"], epochs=1, seed=42, budget_seconds=60)
    assert ledger["status"] == "completed" and len(ledger["runs"]) == 4
    assert len({run["initial_parameters_sha256"] for run in ledger["runs"]}) == 1
    assert all(run["initial_persistence_max_error"] == 0 for run in ledger["runs"])
    assert len({run["trainable_parameters"] for run in ledger["runs"]}) == 1
    for run in ledger["runs"]:
        checkpoint = torch.load(run["checkpoint"], weights_only=True)
        assert checkpoint["selection_loss_normalization"]["train_windows"] == 6
        assert (checkpoint["loss_normalization"] is not None) == (run["loss_mode"] == "relative")
        assert (checkpoint["model_config"].get("delta_output_normalization") is not None) == (run["head_mode"] == "relative")
    with pytest.raises(FileExistsError):
        pilot.run_pilot(source, output, groups=["c6"], epochs=1, seed=42, budget_seconds=60)
    for groups, epochs, budget in ((["bad"], 1, 60), (["c6", "c6"], 1, 60), (["c6"], 0, 60), (["c6"], 1, float("nan"))):
        with pytest.raises(ValueError):
            pilot.run_pilot(source, tmp_path / "bad", groups=groups, epochs=epochs, seed=42, budget_seconds=budget)
    with pytest.raises(TimeoutError):
        pilot.run_pilot(source, tmp_path / "budget", groups=["c6"], epochs=1, seed=42, budget_seconds=1e-8)
    assert json.loads((tmp_path / "budget/ledger.json").read_text(encoding="utf-8"))["status"] == "failed"
    monkeypatch.setattr(sys, "argv", [str(script), "--data-dir", str(source), "--out-dir", str(output)])
    assert pilot.main() == 1


def test_training_cli_fits_loss_and_head_scales_only_on_training_labels(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    script = Path(__file__).resolve().parents[3] / "scripts/train_world_model.py"
    module_spec = importlib.util.spec_from_file_location("wm_state_scale_training_cli", script)
    cli = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "_REPO_ROOT", tmp_path)
    training, validation = data(n=60), data(n=4, offset=1)
    validation["target"] *= 100
    training_path, validation_path = tmp_path / "train.npz", tmp_path / "val.npz"
    np.savez(training_path, **training)
    np.savez(validation_path, **validation)
    argv = [str(script), "--dataset", str(training_path), "--val-dataset", str(validation_path),
        "--version", "state-scale-test", "--models-dir", str(tmp_path / "models"), "--normalize-inputs", "--no-tracking",
        "--device", "cpu", "--epochs", "1", "--batch-size", "64", "--delta-prediction", "--zero-init-delta-head",
        "--loss-scale", "train-persistence-rmse", "--selection-loss-scale", "train-persistence-rmse",
        "--delta-output-scale", "train-persistence-rmse"]
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 0
    checkpoint = torch.load(tmp_path / "models/world_model/state-scale-test.pt", weights_only=True)
    expected = fit_state_scale(training["target"], training["initial_state"])
    assert checkpoint["loss_normalization"] == expected
    assert checkpoint["selection_loss_normalization"] == expected
    assert checkpoint["model_config"]["delta_output_normalization"] == expected
    assert checkpoint["state_feedback"] is True
    assert checkpoint["use_amp"] is False
    monkeypatch.setattr(sys, "argv", argv + ["--loss-weights", "auto"])
    assert cli.main() == 1
