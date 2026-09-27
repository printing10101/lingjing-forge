"""融合路径端到端集成测试（ADR-020 思路 1 → ADR-017 WorldModelNet 接入）.

本测试覆盖「零件齐了装到车上」的最后一公里：验证 UnifiedState 经
GeometryEncoder/DynamicsEncoder/FusionLayer 投影后的融合 embedding，
能真正流入 WorldModelNet.forward 的 LSTM+LTC 预测路径，输出合法的
predicted_trajectory / trajectory_metrics。

覆盖层级（4 个用例，自下而上）：
1. ``WorldModelConfig.use_fusion=True`` 校验路径
2. ``WorldModelNet.__init__`` 融合模式实例化（含三个编码器子模块）
3. ``WorldModelNet.forward(unified_states=...)`` 端到端前向
   - 输出 shape 满足 ADR-017 契约：``[batch, horizon, state_dim]`` / ``[batch, 3]``
   - 融合模式 ``states`` 参数可为 None（prev_state 由 state_head(h) 推导）
4. ``TrajectoryPredictor.predict(unified_state=...)`` 上层封装链路
   - 单样本 UnifiedState → T=1 → 前向 → 去 batch 维度

学术诚信对齐（D-2 硬约束）：
- torch 不可用时通过 ``pytest.importorskip`` 自然跳过，不注入桩模块伪装通过
- 测试不依赖随机种子（只校验形状与有限值，不校验具体数值）
- 不写入 MLflow / 不调用任何带副作用的外部依赖
"""

from __future__ import annotations

import numpy as np
import pytest

from app.plugins.world_model.net import WorldModelConfig
from app.plugins.world_model.unified_state import (
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)


# 公共 fixture：典型 6061-T6 立方零件 + HRC52 类切削工况
def _make_unified_state() -> UnifiedState:
    return UnifiedState(
        geometry=GeometryFeatures(
            bbox_dimensions=(100.0, 60.0, 40.0),
            feature_vector=[0.0] * 32,
            symmetry_score=0.85,
            complexity_score=0.42,
        ),
        dynamics=DynamicsState(
            spindle_speed=8000.0,
            feed_rate=1200.0,
            depth_of_cut=0.5,
            tool_wear=0.05,
            vibration_rms=0.32,
            temperature=42.5,
        ),
    )


def _make_fusion_config() -> WorldModelConfig:
    """融合模式配置（小尺寸，便于 CPU 快速跑通）。"""
    return WorldModelConfig(
        state_dim=8,
        action_dim=4,
        hidden_dim=32,
        num_lstm_layers=1,
        num_ltc_layers=1,
        max_trajectory_length=20,
        seed=42,
        use_fusion=True,
        feature_dim=32,
        d_model=32,
        fused_dim=64,
    )


# 用例 1：WorldModelConfig.use_fusion=True 校验
@pytest.mark.unit
def test_fusion_config_validation() -> None:
    """use_fusion=True 时 feature_dim/d_model/fused_dim 必须为正数。"""
    # 合法配置应通过校验
    cfg = _make_fusion_config()
    cfg.validate()  # 不抛异常即可

    # 非法配置应抛 ValueError
    bad_cfg = WorldModelConfig(use_fusion=True, feature_dim=0)
    with pytest.raises(ValueError, match="feature_dim"):
        bad_cfg.validate()

    bad_cfg = WorldModelConfig(use_fusion=True, d_model=-1)
    with pytest.raises(ValueError, match="d_model"):
        bad_cfg.validate()

    bad_cfg = WorldModelConfig(use_fusion=True, fused_dim=0)
    with pytest.raises(ValueError, match="fused_dim"):
        bad_cfg.validate()

    # use_fusion=False 时不校验融合字段（向后兼容）
    legacy_cfg = WorldModelConfig(use_fusion=False, feature_dim=-1)
    legacy_cfg.validate()  # 不抛异常


# 用例 2：WorldModelNet 融合模式实例化
@pytest.mark.unit
def test_world_model_net_fusion_init() -> None:
    """融合模式下 WorldModelNet 应实例化三个编码器子模块。"""
    pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)

    # 融合子模块应存在
    assert hasattr(net, "geometry_encoder")
    assert hasattr(net, "dynamics_encoder")
    assert hasattr(net, "fusion_layer")

    # LSTM 输入维度 = fused_dim + action_dim
    # nn.LSTM 的 input_size 属性可读
    assert net.encoder.input_size == cfg.fused_dim + cfg.action_dim

    # LTC 解码器输入维度仍为 state_dim + action_dim（保持 ADR-017 契约）
    assert net.decoder.input_dim == cfg.state_dim + cfg.action_dim

    # state_head 输出维度仍为 state_dim
    assert net.state_head.out_features == cfg.state_dim


# 用例 3：WorldModelNet.forward(unified_states=...) 端到端前向
@pytest.mark.unit
def test_world_model_net_fusion_forward() -> None:
    """融合路径 forward 输出应满足 ADR-017 契约。"""
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)
    net.eval()

    batch = 2
    T = 1  # 单步历史（与 predictor._predict_fused 一致）
    horizon = 5

    # 构造 unified_states：(geometry_tensor, dynamics_tensor)
    state = _make_unified_state()
    geo_input = torch.tensor(
        [state.geometry.to_tensor_input()] * (batch * T),
        dtype=torch.float32,
    ).reshape(batch, T, -1)  # (batch, T, 37)
    dyn_input = torch.tensor(
        [state.dynamics.to_tensor_input()] * (batch * T),
        dtype=torch.float32,
    ).reshape(batch, T, -1)  # (batch, T, 6)

    # 构造 actions: (batch, T + horizon, action_dim)
    actions = torch.zeros(batch, T + horizon, cfg.action_dim, dtype=torch.float32)

    with torch.inference_mode():
        outputs = net(
            states=None,  # 融合模式 states 可为 None
            actions=actions,
            horizon=horizon,
            unified_states=(geo_input, dyn_input),
        )

    # ADR-017 输出契约
    assert "predicted_trajectory" in outputs
    assert "trajectory_metrics" in outputs
    assert "final_hidden" in outputs

    traj = outputs["predicted_trajectory"]
    metrics = outputs["trajectory_metrics"]
    hidden = outputs["final_hidden"]

    # shape 校验
    assert traj.shape == (batch, horizon, cfg.state_dim)
    assert metrics.shape == (batch, 3)
    assert hidden.shape == (batch, cfg.hidden_dim)

    # 数值有限性（防止 NaN/Inf 渗漏）
    assert torch.isfinite(traj).all(), "predicted_trajectory 含 NaN/Inf"
    assert torch.isfinite(metrics).all(), "trajectory_metrics 含 NaN/Inf"


# 用例 3b：融合模式缺 unified_states 应抛 ValueError
@pytest.mark.unit
def test_world_model_net_fusion_missing_unified_states() -> None:
    """use_fusion=True 但未传 unified_states 应显式报错。"""
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.net import WorldModelNet

    cfg = _make_fusion_config()
    net = WorldModelNet(cfg)

    actions = torch.zeros(1, 6, cfg.action_dim, dtype=torch.float32)
    with pytest.raises(ValueError, match="unified_states"):
        net(states=None, actions=actions, horizon=5)


# 用例 4：TrajectoryPredictor.predict(unified_state=...) 上层封装链路
@pytest.mark.unit
def test_trajectory_predictor_fusion_path() -> None:
    """TrajectoryPredictor 融合路径应输出单样本轨迹（去 batch 维度）。"""
    pytest.importorskip("torch")
    from app.plugins.world_model.predictor import TrajectoryPredictor

    cfg = _make_fusion_config()
    predictor = TrajectoryPredictor(config=cfg, device="cpu")
    # 不加载权重（接口验证用，预测数值无意义）
    predictor.load_model(model_uri="model://world_model/test-fusion")

    state = _make_unified_state()
    horizon = 4
    # candidate_action: [horizon, action_dim]
    actions = np.zeros((horizon, cfg.action_dim), dtype=np.float32)

    prediction = predictor.predict(
        unified_state=state,
        candidate_action=actions,
        horizon=horizon,
    )

    # 单样本去 batch 维度
    assert prediction.predicted_trajectory.shape == (horizon, cfg.state_dim)
    assert prediction.trajectory_metrics.shape == (3,)
    assert prediction.horizon == horizon

    # model_info 应标记融合模式
    assert prediction.model_info.get("mode") == "fusion"
    assert prediction.model_info.get("backend") == "torch"
    assert prediction.model_info.get("fused_embedding_dim") == cfg.fused_dim

    # 数值有限性
    assert np.all(np.isfinite(prediction.predicted_trajectory))
    assert np.all(np.isfinite(prediction.trajectory_metrics))


# 用例 4b：use_fusion=False 时传入 unified_state 应抛 ValueError
@pytest.mark.unit
def test_predictor_fusion_config_mismatch() -> None:
    """config.use_fusion=False 但传 unified_state 应显式报错。"""
    pytest.importorskip("torch")
    from app.plugins.world_model.predictor import TrajectoryPredictor

    cfg = WorldModelConfig(use_fusion=False)
    predictor = TrajectoryPredictor(config=cfg, device="cpu")
    predictor.load_model(model_uri="model://world_model/test-legacy")

    state = _make_unified_state()
    actions = np.zeros((3, cfg.action_dim), dtype=np.float32)

    with pytest.raises(ValueError, match="use_fusion"):
        predictor.predict(
            unified_state=state,
            candidate_action=actions,
            horizon=3,
        )
