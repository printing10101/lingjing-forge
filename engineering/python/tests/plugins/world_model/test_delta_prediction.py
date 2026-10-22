"""Δ 状态预测回归测试（P1 架构改造，2026-10-21）.

动机：慢漂移量（刀具磨损 / 表面粗糙度）在 H 步内变化极小，但绝对水平经
LSTM 压缩后容易丢失，导致预测打不过「保持当前状态」的持久性基线。Δ 模式
让解码器输出「相对上一状态的增量」，模型可学到 delta≈0，从而在慢变量上
不劣于持久性基线，同时保留快变量的预测能力。

契约：
1. `delta_prediction=False`（默认）→ 不创建 delta_head，state_dict 键集合不变
   （既有 1.0.0 / 1.1.0 checkpoint 可直接加载）；
2. `delta_prediction=True` → 新增 delta_head 两个键，前向形状不变；
3. 把 delta_head 权重清零 → 整条轨迹恒定（Δ=0 的直接证据）。
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet  # noqa: E402


def _cfg(delta: bool) -> WorldModelConfig:
    return WorldModelConfig(
        state_dim=8,
        action_dim=4,
        hidden_dim=8,
        num_lstm_layers=1,
        num_ltc_layers=1,
        use_fusion=True,
        feature_dim=4,
        d_model=8,
        fused_dim=16,
        delta_prediction=delta,
        seed=3,
    )


def _inputs():
    torch.manual_seed(0)
    geo = torch.randn(2, 5, 9)  # bbox 3 + feature 4 + symmetry 1 + complexity 1
    dyn = torch.randn(2, 5, 6)
    actions = torch.randn(2, 5 + 3, 4)
    return geo, dyn, actions


def test_default_keeps_state_dict_keys() -> None:
    """默认（绝对预测）不得新增参数键，保证旧权重可加载。"""
    plain = WorldModelNet(_cfg(False))
    delta = WorldModelNet(_cfg(True))

    assert plain.delta_head is None
    extra = set(delta.state_dict().keys()) - set(plain.state_dict().keys())
    assert extra == {"delta_head.weight", "delta_head.bias"}


def test_delta_forward_shape() -> None:
    """Δ 模式前向形状仍为 [batch, horizon, state_dim]。"""
    geo, dyn, actions = _inputs()
    net = WorldModelNet(_cfg(True))
    with torch.inference_mode():
        out = net(states=None, actions=actions, horizon=3, unified_states=(geo, dyn))
    assert out["predicted_trajectory"].shape == (2, 3, 8)
    assert out["trajectory_metrics"].shape == (2, 3)


def test_zeroed_delta_head_yields_constant_trajectory() -> None:
    """Δ 头清零时整条轨迹恒定 —— Δ 语义的直接证据。"""
    geo, dyn, _unused = _inputs()
    horizon = 4
    actions = torch.randn(2, 5 + horizon, 4)  # forward 要求 [B, T+horizon, A]
    net = WorldModelNet(_cfg(True))
    assert net.delta_head is not None
    with torch.no_grad():
        for param in net.delta_head.parameters():
            param.zero_()

    with torch.inference_mode():
        out = net(states=None, actions=actions, horizon=horizon, unified_states=(geo, dyn))
    traj = out["predicted_trajectory"]

    for step in range(1, traj.shape[1]):
        assert torch.allclose(traj[:, 0, :], traj[:, step, :], atol=1e-6), (
            f"第 {step} 步偏离初始状态，Δ=0 时轨迹应恒定"
        )


def test_old_checkpoint_loads_with_strict_false() -> None:
    """旧 checkpoint（无 delta_head）可按 strict=False 迁移到 Δ 模式。"""
    plain = WorldModelNet(_cfg(False))
    delta = WorldModelNet(_cfg(True))

    result = delta.load_state_dict(plain.state_dict(), strict=False)
    assert set(result.missing_keys) == {"delta_head.weight", "delta_head.bias"}
    assert result.unexpected_keys == []
