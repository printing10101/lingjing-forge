"""短程标签可预测性探针的核心函数单测。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_label_predictability_probe import (  # noqa: E402
    build_features,
    channel_deltas,
    evaluate_baselines,
    ridge_fit,
    ridge_predict,
    select_lambda,
)


def _synthetic_arrays(n: int, history: int, horizon: int, *, linear_signal: bool, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    dynamics = rng.normal(0, 1, size=(n, history, 6)).astype(np.float32)
    state_history = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    actions = rng.normal(0, 0.1, size=(n, history + horizon, 4)).astype(np.float32)
    geometry = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    if linear_signal:
        # 未来增量 = 末条动力学第0维 × 0.5 + 未来动作第0维均值 × 1.0（可被线性模型完全解释）
        delta = (dynamics[:, -1, 0:1] * 0.5 + actions[:, history:, 0].mean(axis=1, keepdims=True) * 1.0)
        delta = np.repeat(delta[:, :, None], 8, axis=2)
        delta = np.repeat(delta, horizon, axis=1)
    else:
        delta = rng.normal(0, 1, size=(n, horizon, 8))
    target = (state_history[:, -1, :][:, None, :] + delta.astype(np.float32)).astype(np.float32)
    return {"geometry": geometry, "dynamics": dynamics, "actions": actions,
            "target": target, "state_history": state_history}


def test_ridge_recovers_exact_linear_signal():
    train = _synthetic_arrays(200, 4, 2, linear_signal=True, seed=7)
    val = _synthetic_arrays(50, 4, 2, linear_signal=True, seed=8)
    results = evaluate_baselines(train, val)
    channel = results["per_channel"]["chatter_probability"]
    assert channel["ridge_beats_persistence"] is True
    assert channel["ridge_vs_persistence_r2"] > 0.99
    assert channel["mae"]["ridge"] < channel["mae"]["persistence"]


def test_ridge_no_signal_does_not_beat_persistence_on_average():
    rng = np.random.default_rng(11)
    train = _synthetic_arrays(200, 4, 2, linear_signal=False, seed=12)
    val = _synthetic_arrays(50, 4, 2, linear_signal=False, seed=13)
    results = evaluate_baselines(train, val)
    channel = results["per_channel"]["tool_wear"]
    # 纯噪声目标下岭回归不应系统性优于持久性（5% 显著水平按 MSE 比例留裕度）
    assert channel["ridge_vs_persistence_r2"] < 0.10


def test_ridge_fit_predict_roundtrip_constant_target():
    features = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0], [7.0, 8.0]])
    targets = np.full(4, 2.5)
    model = ridge_fit(features, targets, lam=0.1)
    prediction = ridge_predict(model, features)
    assert np.allclose(prediction, 2.5, atol=1e-8)


def test_select_lambda_deterministic_and_validates_input():
    features = np.random.default_rng(3).normal(size=(40, 3))
    targets = features @ np.array([1.0, -1.0, 0.5])
    lam_first = select_lambda(features, targets)
    lam_second = select_lambda(features, targets)
    assert lam_first == lam_second
    with pytest.raises(ValueError):
        select_lambda(features, targets, internal_fraction=0.0)


def test_build_features_rejects_non_finite():
    arrays = _synthetic_arrays(6, 4, 2, linear_signal=False, seed=5)
    arrays["dynamics"][0, -1, 0] = np.nan
    with pytest.raises(ValueError):
        build_features(arrays)


def test_channel_deltas_shape():
    arrays = _synthetic_arrays(6, 4, 2, linear_signal=False, seed=9)
    deltas = channel_deltas(arrays)
    assert deltas.shape == (6, 2, 8)
    assert np.allclose(deltas[:, 0, :], arrays["target"][:, 0, :] - arrays["state_history"][:, -1, :])
