"""冻结历史消融的有效性：真改历史、固定反馈、基线只拟合训练目标。"""

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "wm_audit_frozen_history.py"
spec = importlib.util.spec_from_file_location("wm_frozen_history_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def arrays(seed=42, n=4):
    rng = np.random.default_rng(seed)
    geo, dyn, act, state = [rng.random(shape).astype(np.float32) for shape in
        [(n, 4, 9), (n, 4, 6), (n, 6, 4), (n, 4, 8)]]
    state[:, :, 1] = dyn[:, :, 3]
    state[:, :, 3] = dyn[:, :, 4]
    state[:, :, 4] = dyn[:, :, 5] / 100
    return dict(geometry=geo, dynamics=dyn, actions=act, state_history=state,
                target=rng.random((n, 2, 8)).astype(np.float32))


@pytest.fixture
def files(tmp_path):
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
    evaluation, train, weights = tmp_path / "fold_val.npz", tmp_path / "fold_train.npz", tmp_path / "model.pt"
    np.savez(evaluation, **arrays())
    np.savez(train, **arrays(seed=43, n=3))
    config = WorldModelConfig(hidden_dim=8, num_lstm_layers=1, num_ltc_layers=1, feature_dim=4, d_model=8, fused_dim=16)
    torch.save({"model_config": config.to_dict(), "model_state_dict": WorldModelNet(config).state_dict(),
                "trainer_type": "FusionWorldModelTrainer", "state_feedback": True}, weights)
    (tmp_path / "fold.meta.json").write_text(json.dumps({"split": {"train": ["a", "b"], "val": ["c"]}}))
    return evaluation, train, weights


def test_controls_preserve_last_state_future_actions_and_paired_samples():
    data = arrays()
    original = {name: value.copy() for name, value in data.items()}
    geo, dyn, act = audit.ablation_inputs(data, "last_only", 42)
    np.testing.assert_array_equal(dyn, data["dynamics"][:, -1:])
    np.testing.assert_array_equal(act[:, 1:], data["actions"][:, 4:])
    assert geo.shape == (4, 1, 9)
    geo, dyn, act = audit.ablation_inputs(data, "repeat_last", 42)
    np.testing.assert_array_equal(dyn[:, 0], data["dynamics"][:, -1])
    np.testing.assert_array_equal(act, data["actions"])
    first = audit.ablation_inputs(data, "shuffle_past", 42)
    second = audit.ablation_inputs(data, "shuffle_past", 42)
    for value, repeated in zip(first, second):
        np.testing.assert_array_equal(value, repeated)
    np.testing.assert_array_equal(first[1][:, -1], data["dynamics"][:, -1])
    np.testing.assert_array_equal(first[2][:, 4:], data["actions"][:, 4:])
    assert not np.array_equal(first[1][:, :-1], data["dynamics"][:, :-1])
    for name, value in data.items():
        np.testing.assert_array_equal(value, original[name])
    with pytest.raises(ValueError, match="未知"):
        audit.ablation_inputs(data, "invented", 42)


@pytest.mark.parametrize("change", [
    lambda d: d.pop("state_history"), lambda d: d.update(geometry=d["geometry"][:, :, :4]),
    lambda d: d.update(actions=d["actions"][:, :3]), lambda d: d.update(target=d["target"][:0]),
    lambda d: d.update(dynamics=np.full_like(d["dynamics"], np.nan)),
    lambda d: d.update(state_history=d["state_history"] + 1),
    lambda d: d.update(target=d["target"][0]),
])
def test_invalid_evaluation_arrays_rejected(tmp_path, change):
    data = arrays()
    change(data)
    path = tmp_path / "invalid.npz"
    np.savez(path, **data)
    with pytest.raises(ValueError):
        audit.load_arrays(path)


def test_summaries_do_not_turn_zero_baseline_into_finite_gain():
    target = np.zeros((2, 3, 8))
    result = audit.summarize_predictions({"full": np.ones_like(target), "persistence": target}, target)
    assert result["full"]["gain_vs_persistence_pct"]["wear_mm"] is None
    assert result["full"]["window_mae_by_dim"] == [[1] * 8] * 2
    assert result["persistence"]["max_abs_change_vs_full_by_dim"]["wear_mm"] == 1


def test_frozen_evaluation_restores_config_and_uses_only_training_mean(files):
    evaluation, train, weights = files
    report = audit.evaluate(evaluation, weights, train, batch_size=2)
    assert report["checkpoint"]["config"]["hidden_dim"] == 8
    assert report["checkpoint"]["state_feedback"] is True
    assert report["independent_final_test_established"] is False
    assert report["dataset"]["metadata"]["content"]["split"]["val"] == ["c"]
    assert report["train_dataset"]["mean_baseline_fit_scope"] == "train_targets_only"
    validation = audit.load_arrays(evaluation)["target"].astype(np.float64)
    fitted_mean = audit.load_arrays(train)["target"].astype(np.float64).mean(axis=(0, 1))
    np.testing.assert_allclose(list(report["results"]["train_mean"]["mae_by_dim"].values()),
                               np.abs(validation - fitted_mean).mean(axis=(0, 1)))
    training = audit.load_arrays(train)
    drift = (training["target"].astype(np.float64) - training["state_history"][:, -1:]).mean(axis=0)
    expected_drift = audit.load_arrays(evaluation)["state_history"][:, -1:] + drift[None]
    np.testing.assert_allclose(list(report["results"]["train_drift"]["mae_by_dim"].values()),
                               np.abs(validation - expected_drift).mean(axis=(0, 1)))
    assert report["history_gradient_probe"]["windows"] == 4
    assert report["evaluator_sha256"] == audit.file_hash(SCRIPT)
    with pytest.raises(ValueError, match="batch_size"):
        audit.evaluate(evaluation, weights, train, batch_size=0)


def test_leakage_shape_and_output_schema_fail_closed(files):
    torch = pytest.importorskip("torch")
    evaluation, train, weights = files
    with pytest.raises(ValueError, match="相同"):
        audit.evaluate(evaluation, weights, evaluation)
    data = arrays(seed=43)
    data["target"] = np.zeros((4, 1, 8), dtype=np.float32)
    data["actions"] = data["actions"][:, :5]
    np.savez(train, **data)
    with pytest.raises(ValueError, match="形状"):
        audit.evaluate(evaluation, weights, train)
    np.savez(train, **arrays(seed=43))
    metadata = evaluation.with_name("fold.meta.json")
    metadata.write_text(json.dumps({"split": {"train": ["c"], "val": ["c"]}}))
    with pytest.raises(ValueError, match="组重叠"):
        audit.evaluate(evaluation, weights, train)
    metadata.unlink()
    assert audit.split_metadata(evaluation)["path"] is None
    payload = torch.load(weights, weights_only=True)
    payload["state_schema"] = "legacy"
    torch.save(payload, weights)
    with pytest.raises(ValueError, match="输出口径"):
        audit.evaluate(evaluation, weights, train)
    torch.save({}, weights)
    with pytest.raises(ValueError, match="model_config"):
        audit.evaluate(evaluation, weights, train)


def test_cli_requires_weights_and_preserves_old_outputs(files, tmp_path, monkeypatch):
    evaluation, train, weights = files
    output = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--dataset", str(evaluation), "--train-dataset", str(train),
                                     "--weights", str(weights), "--out", str(output)])
    assert audit.main() == 0
    original = output.read_bytes()
    assert audit.main() == 1
    assert output.read_bytes() == original
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])
    with pytest.raises(SystemExit):
        audit.main()


def test_history_gradient_and_ablation_probes_detect_a_history_sensitive_network():
    torch = pytest.importorskip("torch")
    class HistorySensitive(torch.nn.Module):
        def forward(self, *, states, actions, horizon, unified_states, initial_state):
            dyn = unified_states[1]
            weights = torch.arange(1, dyn.shape[1] + 1, dtype=dyn.dtype)[None, :, None]
            signal = (dyn * weights).sum(dim=(1, 2))
            return {"predicted_trajectory": signal[:, None, None].expand(-1, horizon, 8)}
    data = arrays()
    network = HistorySensitive()
    result = audit.predict_modes(network, data, feedback=False, batch_size=2, seed=42)
    assert not np.array_equal(result["full"], result["repeat_last"])
    assert not np.array_equal(result["full"], result["shuffle_past"])
    assert audit.history_gradient_probe(network, data, feedback=False)["nonzero_fraction"] == 1
    class Broken(HistorySensitive):
        def forward(self, **kwargs):
            output = super().forward(**kwargs)
            output["predicted_trajectory"] = output["predicted_trajectory"] * float("nan")
            return output
    with pytest.raises(ValueError, match="有限性"):
        audit.predict_modes(Broken(), data, feedback=False, batch_size=2, seed=42)
    with pytest.raises(ValueError, match="梯度"):
        audit.history_gradient_probe(Broken(), data, feedback=False)


def test_legacy_evaluator_restores_nondefault_config_and_auto_feedback(files, tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    evaluation, _, weights = files
    legacy_spec = importlib.util.spec_from_file_location("wm_legacy_evaluation", SCRIPT.with_name("wm_eval_real_wear.py"))
    legacy = importlib.util.module_from_spec(legacy_spec)
    legacy_spec.loader.exec_module(legacy)
    data = arrays()
    data["state_history"][:, :, 0] = 0
    data["target"][:, :, 0] = 0
    np.savez(evaluation, **data)
    output = tmp_path / "old-entry.json"
    argv = [str(legacy.__file__), "--dataset", str(evaluation), "--weights", str(weights), "--out", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    assert legacy.main() == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["weights_source"] == "checkpoint"
    assert result["state_feedback_trained"] is True
    assert result["state_feedback_used"] is True
    assert result["dims"][0]["gain_pct"] is None
    original = output.read_bytes()
    assert legacy.main() == 1
    assert output.read_bytes() == original
    payload = torch.load(weights, weights_only=True)
    del payload["model_config"]
    torch.save(payload, weights)
    monkeypatch.setattr(sys, "argv", argv[:-1] + [str(tmp_path / "missing-config.json")])
    assert legacy.main() == 1
