"""残差匹配试跑核心函数单测。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_label_predictability_probe import channel_deltas  # noqa: E402
from wm_residual_pilot import (  # noqa: E402
    channel_metrics,
    fit_ridge_models,
    r2_vs_persistence,
    ridge_delta_predictions,
    shift_sample_targets,
)


def _synthetic_arrays(n: int, history: int, horizon: int, *, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    dynamics = rng.normal(0, 1, size=(n, history, 6)).astype(np.float32)
    state_history = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    actions = rng.normal(0, 0.1, size=(n, history + horizon, 4)).astype(np.float32)
    geometry = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    # 可被线性模型完全解释的增量：末条动力学第0维 + 未来动作第0维均值
    delta = (dynamics[:, -1, 0:1] * 0.5 + actions[:, history:, 0].mean(axis=1, keepdims=True) * 1.0)
    delta = np.repeat(np.repeat(delta[:, :, None], 8, axis=2), horizon, axis=1).astype(np.float32)
    target = (state_history[:, -1, :][:, None, :] + delta).astype(np.float32)
    return {"geometry": geometry, "dynamics": dynamics, "actions": actions,
            "target": target, "state_history": state_history}


def test_fit_and_predict_ridge_delta_recovers_linear_signal():
    train = _synthetic_arrays(200, 4, 3, seed=21)
    val = _synthetic_arrays(40, 4, 3, seed=22)
    models = fit_ridge_models(train)
    assert len(models) == 3 * 8
    prediction = ridge_delta_predictions(models, val)
    assert prediction.shape == (40, 3, 8)
    truth = channel_deltas(val)
    # 线性信号下岭回归增量预测应近乎精确
    assert float(np.abs(prediction - truth).max()) < 1e-4


def test_shift_sample_targets_matches_broadcast_subtraction():
    arrays = _synthetic_arrays(10, 4, 2, seed=31)
    ridge_delta = ridge_delta_predictions(fit_ridge_models(arrays), arrays)
    samples = [{"target_trajectory": arrays["target"][index].copy(),
                "initial_state": arrays["state_history"][index, -1].copy()} for index in range(10)]
    shift_sample_targets(samples, ridge_delta)
    expected = arrays["target"].astype(np.float64) - ridge_delta
    for index, sample in enumerate(samples):
        assert np.allclose(sample["target_trajectory"], expected[index], atol=1e-6)
        # initial_state 与输入不被改动
        assert np.allclose(sample["initial_state"], arrays["state_history"][index, -1])


def test_shift_sample_targets_rejects_non_finite():
    sample = [{"target_trajectory": np.full((2, 8), np.nan, dtype=np.float32)}]
    with pytest.raises(ValueError):
        shift_sample_targets(sample, np.zeros((1, 2, 8)))


def test_channel_metrics_and_r2_known_values():
    target = np.zeros((2, 1, 8))
    target[:, :, 5] = 1.0  # cutting_force_norm
    persistence_mse = {"cutting_force_norm": 1.0}
    perfect = target.copy()
    metrics = channel_metrics(perfect, target)
    assert metrics["cutting_force_norm"]["mae"] == 0.0
    r2 = r2_vs_persistence(metrics, persistence_mse)
    assert r2["cutting_force_norm"] == 1.0
    worse = np.full((2, 1, 8), 2.0)
    r2_worse = r2_vs_persistence(channel_metrics(worse, target), persistence_mse)
    assert r2_worse["cutting_force_norm"] == 0.0  # 预测2.0对真值1.0，MSE=1，R² = 1 - 1/1
    # 零方差通道 R² 为 None
    assert r2_vs_persistence(channel_metrics(perfect, target), {"tool_wear": 0.0})["tool_wear"] is None


def test_ridge_delta_predictions_zero_for_constant_deltas():
    arrays = _synthetic_arrays(30, 4, 2, seed=41)
    arrays["target"] = arrays["state_history"][:, -1:, :].repeat(2, axis=1)  # 增量恒为0
    models = fit_ridge_models(arrays)
    prediction = ridge_delta_predictions(models, arrays)
    assert float(np.abs(prediction).max()) < 1e-6
