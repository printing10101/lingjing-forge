"""种子稳健性与特征消融工具函数单测。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from wm_residual_robustness import aggregate_arm, feature_ablation, ridge_deltas_for_slice  # noqa: E402


def _synthetic_arrays(n: int, history: int, horizon: int, *, seed: int) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    dynamics = rng.normal(0, 1, size=(n, history, 6)).astype(np.float32)
    state_history = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    actions = rng.normal(0, 0.1, size=(n, history + horizon, 4)).astype(np.float32)
    geometry = rng.normal(0, 1, size=(n, history, 8)).astype(np.float32)
    # 增量只依赖末条状态第5维（力通道自相关），不依赖动力学/动作
    delta = np.repeat(state_history[:, -1, 4:5] * 0.7, 8, axis=1)
    delta = np.repeat(delta[:, None, :], horizon, axis=1)
    target = (state_history[:, -1, :][:, None, :] + delta).astype(np.float32)
    return {"geometry": geometry, "dynamics": dynamics, "actions": actions,
            "target": target, "state_history": state_history}


def test_ridge_deltas_for_slice_full_equals_all_features():
    train = _synthetic_arrays(150, 4, 2, seed=51)
    val = _synthetic_arrays(30, 4, 2, seed=52)
    full = ridge_deltas_for_slice(slice(0, 18), train, val)
    state_only = ridge_deltas_for_slice(slice(0, 8), train, val)
    assert full.shape == (30, 2, 8)
    assert state_only.shape == full.shape
    # 信号只来自状态通道：仅状态特征应接近精确，且与全特征预测接近
    truth = val["target"].astype(np.float64) - val["state_history"][:, -1, :][:, None, :]
    assert float(np.abs(state_only - truth).max()) < 1e-3
    assert float(np.abs(full - state_only).max()) < 1e-2


def test_feature_ablation_reports_all_slices_and_channels():
    train = _synthetic_arrays(150, 4, 2, seed=61)
    val = _synthetic_arrays(30, 4, 2, seed=62)
    ablation = feature_ablation(train, val)
    assert set(ablation) == {"state_only", "state_dynamics", "state_dynamics_actions"}
    for entry in ablation.values():
        assert set(entry["r2_vs_persistence"]) == {
            "chatter_probability", "tool_wear", "surface_roughness", "vibration_rms",
            "temperature_over_100c", "cutting_force_norm", "mrr_norm", "confidence"}
    # 力通道自相关可学：三档都应给出正 R²
    for entry in ablation.values():
        assert entry["r2_vs_persistence"]["cutting_force_norm"] > 0.9


def test_aggregate_arm_statistics():
    runs = [
        {"r2_vs_persistence": {"tool_wear": 0.10}, "metrics": {"tool_wear": {"mae": 0.2}}},
        {"r2_vs_persistence": {"tool_wear": 0.20}, "metrics": {"tool_wear": {"mae": 0.4}}},
        {"r2_vs_persistence": {"tool_wear": 0.30}, "metrics": {"tool_wear": {"mae": 0.6}}},
    ]
    stats = aggregate_arm(runs, "tool_wear")
    assert stats["seeds"] == 3
    assert stats["r2_mean"] == pytest.approx(0.2)
    assert stats["r2_min"] == pytest.approx(0.1)
    assert stats["r2_max"] == pytest.approx(0.3)
    assert stats["mae_mean"] == pytest.approx(0.4)


def test_aggregate_arm_rejects_all_none():
    runs = [{"r2_vs_persistence": {"tool_wear": None}, "metrics": {"tool_wear": {"mae": 0.1}}}]
    with pytest.raises(ValueError):
        aggregate_arm(runs, "tool_wear")
