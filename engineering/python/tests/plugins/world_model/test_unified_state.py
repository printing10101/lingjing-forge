"""思路 1（统一表示异质对象）单元测试。

对应 ADR-020 第 1.7 节代码骨架 / app/plugins/world_model/unified_state.py 等。

覆盖（5 用例）：
1. UnifiedState 序列化/反序列化往返（to_dict ↔ from_dict 互逆）
2. UNIFIED_STATE_SCHEMA 结构与必填字段校验
3. GeometryEncoder 输出形状 (batch, d_model)
4. DynamicsEncoder 输出形状 (batch, d_model)
5. FusionLayer 端到端融合形状 (batch, fused_dim) + UnifiedState 张量化链路

学术诚信对齐：
- 测试不依赖随机种子（只校验形状与往返，不校验数值）
- torch 不可用时通过 pytest.importorskip 自然跳过，不注入桩模块伪装通过
"""

from __future__ import annotations

import pytest

from app.plugins.world_model.unified_state import (
    UNIFIED_STATE_SCHEMA,
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)


# 公共 fixture：构造一个典型 UnifiedState（6061-T6 立方零件 + 典型切削工况）
def _make_geometry() -> GeometryFeatures:
    return GeometryFeatures(
        bbox_dimensions=(100.0, 60.0, 40.0),
        feature_vector=[0.0] * 32,  # ADR-007 32 维特征统计向量
        symmetry_score=0.85,
        complexity_score=0.42,
    )


def _make_dynamics() -> DynamicsState:
    return DynamicsState(
        spindle_speed=8000.0,
        feed_rate=1200.0,
        depth_of_cut=0.5,
        tool_wear=0.05,
        vibration_rms=0.32,
        temperature=42.5,
    )


def _make_unified_state() -> UnifiedState:
    return UnifiedState(geometry=_make_geometry(), dynamics=_make_dynamics())


# 用例 1：UnifiedState 序列化/反序列化往返
@pytest.mark.unit
def test_unified_state_roundtrip() -> None:
    """to_dict → from_dict 应保持字段完全一致。"""
    original = _make_unified_state()
    serialized = original.to_dict()
    restored = UnifiedState.from_dict(serialized)

    # 几何字段
    assert list(restored.geometry.bbox_dimensions) == list(original.geometry.bbox_dimensions)
    assert restored.geometry.feature_vector == original.geometry.feature_vector
    assert restored.geometry.symmetry_score == original.geometry.symmetry_score
    assert restored.geometry.complexity_score == original.geometry.complexity_score

    # 动力学字段
    assert restored.dynamics.spindle_speed == original.dynamics.spindle_speed
    assert restored.dynamics.feed_rate == original.dynamics.feed_rate
    assert restored.dynamics.depth_of_cut == original.dynamics.depth_of_cut
    assert restored.dynamics.tool_wear == original.dynamics.tool_wear
    assert restored.dynamics.vibration_rms == original.dynamics.vibration_rms
    assert restored.dynamics.temperature == original.dynamics.temperature

    # fused_embedding 默认 None
    assert restored.fused_embedding is None


# 用例 2：UNIFIED_STATE_SCHEMA 结构与必填字段
@pytest.mark.unit
def test_unified_state_schema_structure() -> None:
    """Schema 应声明 geometry/dynamics 必填，并约束数值边界。"""
    assert UNIFIED_STATE_SCHEMA["type"] == "object"
    required = set(UNIFIED_STATE_SCHEMA["required"])
    assert {"geometry", "dynamics"}.issubset(required)

    geo_props = UNIFIED_STATE_SCHEMA["properties"]["geometry"]["properties"]
    assert "bbox_dimensions" in geo_props
    assert geo_props["bbox_dimensions"]["minItems"] == 3
    assert geo_props["bbox_dimensions"]["maxItems"] == 3
    # 对称性/复杂度评分应约束在 [0, 1]
    assert geo_props["symmetry_score"]["minimum"] == 0
    assert geo_props["symmetry_score"]["maximum"] == 1

    dyn_props = UNIFIED_STATE_SCHEMA["properties"]["dynamics"]["properties"]
    for field_name in (
        "spindle_speed",
        "feed_rate",
        "depth_of_cut",
        "tool_wear",
        "vibration_rms",
        "temperature",
    ):
        assert field_name in dyn_props

    # 合法 UnifiedState.to_dict() 应满足 Schema 的必填要求
    serialized = _make_unified_state().to_dict()
    assert "geometry" in serialized
    assert "dynamics" in serialized
    assert serialized["fused_embedding"] is None


# 用例 3：GeometryEncoder 输出形状
@pytest.mark.unit
def test_geometry_encoder_output_shape() -> None:
    """GeometryEncoder 输出 (batch, d_model)。"""
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.geometry_encoder import GeometryEncoder

    encoder = GeometryEncoder(feature_dim=32, d_model=64)
    geometry = _make_geometry()
    # batch=2，input_dim = 3 + 32 + 1 + 1 = 37
    input_tensor = torch.tensor(
        [geometry.to_tensor_input(), geometry.to_tensor_input()],
        dtype=torch.float32,
    )
    assert input_tensor.shape == (2, 37)

    output = encoder(input_tensor)
    assert output.shape == (2, 64)


# 用例 4：DynamicsEncoder 输出形状
@pytest.mark.unit
def test_dynamics_encoder_output_shape() -> None:
    """DynamicsEncoder 输出 (batch, d_model)。"""
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.dynamics_encoder import DynamicsEncoder

    encoder = DynamicsEncoder(d_model=64)
    dynamics = _make_dynamics()
    input_tensor = torch.tensor(
        [dynamics.to_tensor_input(), dynamics.to_tensor_input()],
        dtype=torch.float32,
    )
    assert input_tensor.shape == (2, 6)

    output = encoder(input_tensor)
    assert output.shape == (2, 64)


# 用例 5：FusionLayer 端到端融合 + UnifiedState 张量化链路
@pytest.mark.unit
def test_fusion_layer_end_to_end() -> None:
    """GeometryEncoder + DynamicsEncoder + FusionLayer 端到端输出 (batch, fused_dim)。"""
    torch = pytest.importorskip("torch")
    from app.plugins.world_model.dynamics_encoder import DynamicsEncoder
    from app.plugins.world_model.fusion_layer import FusionLayer
    from app.plugins.world_model.geometry_encoder import GeometryEncoder

    d_model = 64
    fused_dim = 128
    batch = 4

    state = _make_unified_state()
    geo_tensor = torch.tensor([state.geometry.to_tensor_input()] * batch, dtype=torch.float32)
    dyn_tensor = torch.tensor([state.dynamics.to_tensor_input()] * batch, dtype=torch.float32)

    geo_encoder = GeometryEncoder(feature_dim=32, d_model=d_model)
    dyn_encoder = DynamicsEncoder(d_model=d_model)
    fusion = FusionLayer(d_model=d_model, fused_dim=fused_dim)

    geo_emb = geo_encoder(geo_tensor)
    dyn_emb = dyn_encoder(dyn_tensor)
    assert geo_emb.shape == (batch, d_model)
    assert dyn_emb.shape == (batch, d_model)

    fused = fusion(geo_emb, dyn_emb)
    assert fused.shape == (batch, fused_dim)

    # 写回 UnifiedState.fused_embedding，验证张量 list 链路可序列化
    state.fused_embedding = fused[0].detach().tolist()
    serialized = state.to_dict()
    assert isinstance(serialized["fused_embedding"], list)
    assert len(serialized["fused_embedding"]) == fused_dim
    # 再次往返应保持 fused_embedding 不丢失
    restored = UnifiedState.from_dict(serialized)
    assert restored.fused_embedding is not None
    assert len(restored.fused_embedding) == fused_dim
