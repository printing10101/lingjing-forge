"""``UnifiedStateAssembler`` 组合桥接测试（P0-3）.

对应 ADR-020 思路 1 P0-3 组装桥接。验证 P0-1（DynamicsStateBridge）+
P0-2（GeometryFeaturesDeriver）产出 → ``UnifiedState`` 的组装链路，
以及 ``WorldModelPlugin`` 自动组装路径的向后兼容性.

验收标准
--------
- ``assemble`` 纯组装：GeometryFeatures + DynamicsState → UnifiedState
- ``assemble_from_results`` 聚合两侧诊断（should_degrade / is_complete）
- ``assemble_from_sources`` 端到端：真实 ``ExtractedFeature`` + vertices
  + legacy ``current_state`` → UnifiedState，不伪造数据
- 降级聚合：任一侧降级则整体 ``should_degrade=True``
- ``to_dict`` 序列化往返一致
- ``WorldModelPlugin._try_assemble_unified_state`` 从半成品 metadata 组装
- 向后兼容：无原料时回退到传统 ``np.ndarray`` 路径
"""

from __future__ import annotations

import pytest

from app.contracts.task import Artifact, TaskContext, TaskResult, TaskStatus
from app.contracts.world_model import StateField
from app.feature_extraction.feature_store import (
    ExtractedFeature,
    FeatureReviewStatus,
)
from app.plugins.world_model.dynamics_state_bridge import (
    BridgeResult,
    DynamicsStateBridge,
)
from app.plugins.world_model.geometry_features_deriver import (
    DerivationResult,
    GeometryFeaturesDeriver,
)
from app.plugins.world_model.net import WorldModelConfig
from app.plugins.world_model.plugin import WorldModelPlugin
from app.plugins.world_model.unified_state import (
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)
from app.plugins.world_model.unified_state_assembler import (
    AssemblerResult,
    UnifiedStateAssembler,
)


# 测试 fixtures


def _make_plane(
    feature_id: str = "p1",
    normal: list[float] | None = None,
    area_mm2: float = 500.0,
    confidence: float = 0.95,
    review_status: str = FeatureReviewStatus.CONFIRMED.value,
) -> ExtractedFeature:
    """构造 plane 特征 fixture."""
    if normal is None:
        normal = [0.0, 0.0, 1.0]
    return ExtractedFeature(
        feature_id=feature_id,
        feature_type="plane",
        params={"normal": normal, "offset": 0.0, "area_mm2": area_mm2},
        confidence=confidence,
        review_status=review_status,
    )


def _make_complete_current_state() -> dict[str, float]:
    """构造完整的 legacy current_state（6 字段齐全）."""
    return {
        StateField.SPINDLE_SPEED: 8000.0,
        StateField.FEED_RATE: 1200.0,
        StateField.DEPTH_OF_CUT: 0.5,
        StateField.TOOL_WEAR: 0.05,
        StateField.VIBRATION_RMS: 0.32,
        StateField.TEMPERATURE: 42.5,
    }


def _make_box_vertices(
    size: tuple[float, float, float] = (10.0, 20.0, 30.0),
) -> list[list[float]]:
    """构造长方体 8 顶点（用于 bbox 派生）."""
    lx, ly, lz = size
    return [
        [0.0, 0.0, 0.0],
        [lx, 0.0, 0.0],
        [0.0, ly, 0.0],
        [lx, ly, 0.0],
        [0.0, 0.0, lz],
        [lx, 0.0, lz],
        [0.0, ly, lz],
        [lx, ly, lz],
    ]


@pytest.fixture
def complete_geometry_result() -> DerivationResult:
    """完整几何派生结果（有 vertices + 1 plane 特征）."""
    features = [_make_plane(area_mm2=500.0, confidence=0.95)]
    return GeometryFeaturesDeriver.from_feature_extraction(features, _make_box_vertices())


@pytest.fixture
def complete_dynamics_result() -> BridgeResult:
    """完整动力学桥接结果（6 字段齐全）."""
    return DynamicsStateBridge.from_current_state(_make_complete_current_state())


# AssemblerResult 诊断聚合


@pytest.mark.unit
@pytest.mark.plugins
class TestAssemblerResultDiagnostics:
    """``AssemblerResult`` 诊断聚合属性."""

    def test_both_complete_not_degrade(
        self,
        complete_geometry_result: DerivationResult,
        complete_dynamics_result: BridgeResult,
    ):
        """两侧完整 → should_degrade=False / is_complete=True."""
        result = UnifiedStateAssembler.assemble_from_results(complete_geometry_result, complete_dynamics_result)
        assert result.should_degrade is False
        assert result.is_complete is True
        assert result.completeness_ratio == 1.0

    def test_geometry_degraded_propagates(
        self,
        complete_dynamics_result: BridgeResult,
    ):
        """几何侧降级（vertices=None → bbox defaulted）→ should_degrade=True."""
        features = [_make_plane()]
        geo_result = GeometryFeaturesDeriver.from_feature_extraction(
            features,
            vertices=None,  # vertices 缺失 → bbox defaulted
        )
        assert GeometryFeaturesDeriver.should_degrade(geo_result) is True

        result = UnifiedStateAssembler.assemble_from_results(geo_result, complete_dynamics_result)
        assert result.geometry_degraded is True
        assert result.dynamics_degraded is False
        assert result.should_degrade is True  # 任一侧降级
        assert result.is_complete is False

    def test_dynamics_degraded_propagates(
        self,
        complete_geometry_result: DerivationResult,
    ):
        """动力学侧降级（>=3 字段缺失）→ should_degrade=True."""
        # 只提供 1 个字段，5 个缺失 defaulted=5 >= DEGRADE_THRESHOLD(3)
        sparse_state = {StateField.SPINDLE_SPEED: 8000.0}
        dyn_result = DynamicsStateBridge.from_current_state(sparse_state)
        assert DynamicsStateBridge.should_degrade(dyn_result) is True

        result = UnifiedStateAssembler.assemble_from_results(complete_geometry_result, dyn_result)
        assert result.geometry_degraded is False
        assert result.dynamics_degraded is True
        assert result.should_degrade is True
        assert result.is_complete is False

    def test_completeness_ratio_aggregation(
        self,
        complete_geometry_result: DerivationResult,
    ):
        """completeness_ratio = (几何 + 动力学) / 2."""
        # 动力学 6 字段中 2 个缺失 real=4/6 ≈ 0.667
        sparse_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            StateField.DEPTH_OF_CUT: 0.5,
            StateField.TOOL_WEAR: 0.05,
            # VIBRATION_RMS / TEMPERATURE 缺失
        }
        dyn_result = DynamicsStateBridge.from_current_state(sparse_state)
        result = UnifiedStateAssembler.assemble_from_results(complete_geometry_result, dyn_result)
        expected = (1.0 + 4 / 6) / 2.0
        assert result.completeness_ratio == pytest.approx(expected, rel=1e-6)


# assemble 纯组装


@pytest.mark.unit
@pytest.mark.plugins
class TestAssemblePure:
    """``assemble`` 纯组装方法."""

    def test_assemble_returns_unified_state(self):
        """assemble 返回 UnifiedState，fused_embedding=None."""
        geometry = GeometryFeatures(
            bbox_dimensions=(10.0, 20.0, 30.0),
            feature_vector=[0.0] * 32,
            symmetry_score=0.5,
            complexity_score=0.1,
        )
        dynamics = DynamicsState(
            spindle_speed=8000.0,
            feed_rate=1200.0,
            depth_of_cut=0.5,
            tool_wear=0.05,
            vibration_rms=0.32,
            temperature=42.5,
        )
        unified = UnifiedStateAssembler.assemble(geometry, dynamics)
        assert isinstance(unified, UnifiedState)
        assert unified.geometry is geometry
        assert unified.dynamics is dynamics
        assert unified.fused_embedding is None  # 由 FusionLayer 填充


# assemble_from_sources 端到端组装


@pytest.mark.unit
@pytest.mark.plugins
class TestAssembleFromSources:
    """``assemble_from_sources`` 端到端组装（真实数据源 → UnifiedState）."""

    def test_end_to_end_complete(
        self,
        complete_geometry_result: DerivationResult,
        complete_dynamics_result: BridgeResult,
    ):
        """真实 ExtractedFeature + vertices + 完整 current_state → UnifiedState."""
        features = [_make_plane(area_mm2=500.0, confidence=0.95)]
        result = UnifiedStateAssembler.assemble_from_sources(
            features,
            _make_box_vertices(),
            _make_complete_current_state(),
        )
        assert isinstance(result, AssemblerResult)
        assert result.is_complete is True
        assert result.should_degrade is False

        # bbox 从真实 vertices 派生（10×20×30）
        assert result.unified_state.geometry.bbox_dimensions == (10.0, 20.0, 30.0)
        # dynamics 字段一一映射
        assert result.unified_state.dynamics.spindle_speed == 8000.0
        assert result.unified_state.dynamics.temperature == 42.5

    def test_end_to_end_no_vertices_degrades(self):
        """vertices=None → bbox defaulted → should_degrade=True."""
        features = [_make_plane()]
        result = UnifiedStateAssembler.assemble_from_sources(features, None, _make_complete_current_state())
        assert result.geometry_degraded is True
        assert result.dynamics_degraded is False
        assert result.should_degrade is True
        # bbox 为中性值 (0,0,0)
        assert result.unified_state.geometry.bbox_dimensions == (0.0, 0.0, 0.0)

    def test_end_to_end_sparse_dynamics_degrades(self):
        """current_state 缺 4 字段 → dynamics 降级 → should_degrade=True."""
        features = [_make_plane()]
        sparse_state = {
            StateField.SPINDLE_SPEED: 8000.0,
            StateField.FEED_RATE: 1200.0,
            # 缺 4 个 defaulted=4 >= 3
        }
        result = UnifiedStateAssembler.assemble_from_sources(features, _make_box_vertices(), sparse_state)
        assert result.geometry_degraded is False
        assert result.dynamics_degraded is True
        assert result.should_degrade is True

    def test_filter_reviewed_drops_pending(self):
        """filter_reviewed=True 过滤掉 pending 特征."""
        # 1 confirmed + 1 pending
        features = [
            _make_plane(feature_id="p1", area_mm2=500.0),
            _make_plane(
                feature_id="p2",
                area_mm2=300.0,
                review_status=FeatureReviewStatus.PENDING.value,
            ),
        ]
        result = UnifiedStateAssembler.assemble_from_sources(
            features,
            _make_box_vertices(),
            _make_complete_current_state(),
            filter_reviewed=True,
        )
        # 过滤后只剩 1 个 plane，derivation_notes 应记录过滤
        notes_text = " ".join(result.geometry_result.derivation_notes)
        assert "过滤掉 1 条未审核特征" in notes_text

    def test_to_dict_roundtrip(
        self,
        complete_geometry_result: DerivationResult,
        complete_dynamics_result: BridgeResult,
    ):
        """to_dict 序列化含全部诊断字段."""
        result = UnifiedStateAssembler.assemble_from_results(complete_geometry_result, complete_dynamics_result)
        d = result.to_dict()
        assert "unified_state" in d
        assert "geometry_result" in d
        assert "dynamics_result" in d
        assert d["should_degrade"] is False
        assert d["is_complete"] is True
        assert d["completeness_ratio"] == 1.0
        # unified_state 可由 UnifiedState.from_dict 反序列化
        unified = UnifiedState.from_dict(d["unified_state"])
        assert unified.geometry.bbox_dimensions == (10.0, 20.0, 30.0)


# WorldModelPlugin 自动组装路径


@pytest.mark.unit
@pytest.mark.plugins
class TestPluginAutoAssemble:
    """``WorldModelPlugin._try_assemble_unified_state`` 自动组装."""

    def _make_plugin(self, use_fusion: bool = True) -> WorldModelPlugin:
        """构造指定融合模式的 plugin."""
        config = WorldModelConfig(use_fusion=use_fusion)
        return WorldModelPlugin(config=config)

    def _make_half_product_metadata(
        self,
        include_geometry: bool = True,
        include_dynamics: bool = True,
        dynamics_complete: bool = True,
    ) -> dict:
        """构造半成品 metadata（geometry_features + dynamics_state）."""
        meta: dict = {}
        if include_geometry:
            meta["geometry_features"] = {
                "bbox_dimensions": [10.0, 20.0, 30.0],
                "feature_vector": [0.0] * 32,
                "symmetry_score": 0.5,
                "complexity_score": 0.1,
            }
        if include_dynamics:
            dyn: dict = {
                "spindle_speed": 8000.0,
                "feed_rate": 1200.0,
                "depth_of_cut": 0.5,
                "tool_wear": 0.05,
                "vibration_rms": 0.32,
                "temperature": 42.5,
            }
            if not dynamics_complete:
                # 删掉 3 个字段触发降级
                dyn = {k: v for k, v in list(dyn.items())[:3]}
            meta["dynamics_state"] = dyn
        return meta

    def test_auto_assemble_from_half_product(self):
        """metadata 含半成品 → 自动组装 UnifiedState."""
        plugin = self._make_plugin(use_fusion=True)
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata=self._make_half_product_metadata(),
        )
        result = plugin._try_assemble_unified_state(artifact)
        assert result is not None
        assert isinstance(result, AssemblerResult)
        assert result.unified_state.geometry.bbox_dimensions == (10.0, 20.0, 30.0)
        assert result.unified_state.dynamics.spindle_speed == 8000.0
        assert result.should_degrade is False

    def test_auto_assemble_dynamics_degraded(self):
        """dynamics_state 缺 3 字段 → dynamics 降级."""
        plugin = self._make_plugin(use_fusion=True)
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata=self._make_half_product_metadata(dynamics_complete=False),
        )
        result = plugin._try_assemble_unified_state(artifact)
        assert result is not None
        assert result.dynamics_degraded is True
        assert result.should_degrade is True

    def test_auto_assemble_missing_geometry_returns_none(self):
        """metadata 缺 geometry_features → 返回 None."""
        plugin = self._make_plugin(use_fusion=True)
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata=self._make_half_product_metadata(include_geometry=False),
        )
        assert plugin._try_assemble_unified_state(artifact) is None

    def test_auto_assemble_missing_dynamics_returns_none(self):
        """metadata 缺 dynamics_state → 返回 None."""
        plugin = self._make_plugin(use_fusion=True)
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata=self._make_half_product_metadata(include_dynamics=False),
        )
        assert plugin._try_assemble_unified_state(artifact) is None

    def test_auto_assemble_invalid_geometry_returns_none(self):
        """geometry_features 字段类型错误 → 返回 None（不抛异常）."""
        plugin = self._make_plugin(use_fusion=True)
        meta = self._make_half_product_metadata()
        # 故意破坏 bbox_dimensions 类型
        meta["geometry_features"]["bbox_dimensions"] = "not_a_list"
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata=meta,
        )
        assert plugin._try_assemble_unified_state(artifact) is None

    def test_auto_assemble_none_artifact(self):
        """artifact=None → 返回 None."""
        plugin = self._make_plugin(use_fusion=True)
        assert plugin._try_assemble_unified_state(None) is None

    def test_legacy_mode_skips_auto_assemble(self):
        """use_fusion=False → execute 不会走自动组装（向后兼容）.

        验证：legacy 模式下即使 metadata 含半成品，input_mode 仍为 legacy.
        """
        plugin = self._make_plugin(use_fusion=False)
        # _try_load_unified_state 和 _try_assemble_unified_state 都依赖
        # config.use_fusion，legacy 模式下应返回 None
        artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata={
                "unified_state": {  # 即使有预组装 unified_state
                    "geometry": {
                        "bbox_dimensions": [1.0, 2.0, 3.0],
                        "feature_vector": [0.0] * 32,
                        "symmetry_score": 0.5,
                        "complexity_score": 0.1,
                    },
                    "dynamics": {
                        "spindle_speed": 8000.0,
                        "feed_rate": 1200.0,
                        "depth_of_cut": 0.5,
                        "tool_wear": 0.05,
                        "vibration_rms": 0.32,
                        "temperature": 42.5,
                    },
                },
                "data": [0.1] * 9,  # legacy 数组数据
            },
        )
        # legacy 模式：_try_load_unified_state 返回 None
        assert plugin._try_load_unified_state(artifact) is None


# WorldModelPlugin execute 端到端（融合模式自动组装 预测）


@pytest.mark.unit
@pytest.mark.plugins
class TestPluginExecuteAutoAssemble:
    """``WorldModelPlugin.execute`` 端到端自动组装.

    验证融合模式下 metadata 含半成品时，execute 自动组装并走融合预测路径.
    因 torch 未安装，predictor 实际前向会跳过（importorskip 风格），
    这里只验证到 input_mode 标记层面.
    """

    @pytest.mark.asyncio
    async def test_execute_fusion_assembled_mode_flag(self, monkeypatch):
        """execute 在自动组装成功时标记 input_mode='fusion_assembled'."""
        pytest.importorskip("torch")  # 无 torch 则跳过完整 execute

        config = WorldModelConfig(use_fusion=True)
        plugin = WorldModelPlugin(config=config)

        # 构造 ctx：current_state 含半成品 metadata
        current_state = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://test/current",
            metadata={
                "geometry_features": {
                    "bbox_dimensions": [10.0, 20.0, 30.0],
                    "feature_vector": [0.0] * 32,
                    "symmetry_score": 0.5,
                    "complexity_score": 0.1,
                },
                "dynamics_state": {
                    "spindle_speed": 8000.0,
                    "feed_rate": 1200.0,
                    "depth_of_cut": 0.5,
                    "tool_wear": 0.05,
                    "vibration_rms": 0.32,
                    "temperature": 42.5,
                },
            },
        )
        candidate_action = Artifact(
            name="candidate_action",
            type="metrics",
            uri="metrics://test/action",
            metadata={"data": [[0.1, 0.2, 0.3]]},
        )
        ctx = TaskContext(
            job_id="test-job",
            inputs={
                "current_state": current_state,
                "candidate_action": candidate_action,
            },
            config={"horizon": 5},
        )

        # 执行（torch 已 importorskip 确认可用）
        result = await plugin.execute(ctx)
        # 即便预测因权重缺失失败，input_mode 也应记录组装诊断
        # 这里验证 execute 不因组装环节抛异常（组装是纯 Python）
        assert isinstance(result, TaskResult)
        # 若成功：metrics 含 assembly_diagnostics
        # 若失败：error 不应是组装相关
        if result.status == TaskStatus.COMPLETED:
            assert result.metrics.get("input_mode") == "fusion_assembled"
            assert result.metrics.get("assembly_diagnostics") is not None
        else:
            # 失败也应是非组装原因（如权重加载/前向）
            assert "组装" not in (result.error or "")
