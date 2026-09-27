"""UnifiedState 组装器：组合 GeometryFeatures + DynamicsState → UnifiedState.

填补 P0-1（DynamicsStateBridge）与 P0-2（GeometryFeaturesDeriver）产出
到 WorldModelNet 融合路径输入之间的"组装"gap。

这是 ADR-020 思路 1 数据流的最后一环（P0-3）：让真实数据源（ADR-007
RANSAC 几何特征 + ADR-017 legacy 动力学状态）产出的零件，真正组装成
UnifiedState，喂给 WorldModelNet 的融合路径（GeometryEncoder /
DynamicsEncoder / FusionLayer）。在此之前，生产代码中 ``UnifiedState``
的实例化点为零——P0-1/P0-2 的产出无人消费，融合架构的输入端是断裂的。

工程边界
========
- 纯 Python 实现，无 numpy/torch 硬依赖（与 Deriver/Bridge 一致）
- 不伪造数据：任一子结果 ``should_degrade`` 时，整体标记降级
- 复用两个子结果的完整性诊断，聚合为 ``AssemblerResult``
- 不修改 ``UnifiedState`` 契约，只做组装
- 线程安全：所有方法为静态/类方法，无状态

对应 ADR：ADR-020 思路 1（P0-3 组装桥接）/ ADR-017 / ADR-007 / ADR-013
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from collections.abc import Sequence

from app.plugins.world_model.dynamics_state_bridge import (
    BridgeResult,
    DynamicsStateBridge,
)
from app.plugins.world_model.geometry_features_deriver import (
    DerivationResult,
    GeometryFeaturesDeriver,
)
from app.plugins.world_model.unified_state import (
    DynamicsState,
    GeometryFeatures,
    UnifiedState,
)


# 组装结果


@dataclass
class AssemblerResult:
    """``UnifiedStateAssembler`` 组装结果.

    聚合 P0-1（``BridgeResult``）与 P0-2（``DerivationResult``）的诊断信息，
    供上层（``WorldModelPlugin`` / ``WorldModelService``）决策是否走融合路径.

    Attributes
    ----------
    unified_state : UnifiedState
        组装后的统一状态. ``fused_embedding=None``，由 ``FusionLayer``
        在 WorldModelNet 前向时填充.
    geometry_result : DerivationResult
        P0-2 派生结果（透传诊断，供 MLflow 追踪与日志）.
    dynamics_result : BridgeResult
        P0-1 桥接结果（透传诊断，供 MLflow 追踪与日志）.
    geometry_degraded : bool
        几何侧是否降级（``GeometryFeaturesDeriver.should_degrade``）.
    dynamics_degraded : bool
        动力学侧是否降级（``DynamicsStateBridge.should_degrade``）.
    """

    unified_state: UnifiedState
    geometry_result: DerivationResult
    dynamics_result: BridgeResult
    geometry_degraded: bool
    dynamics_degraded: bool

    @property
    def should_degrade(self) -> bool:
        """任一侧降级则整体降级.

        降级语义：调用方应回退到传统 ``np.ndarray`` 路径
        （``unified_state=None``），避免融合 embedding 学到的信号
        主要来自中性填充值而非真实数据.
        """
        return self.geometry_degraded or self.dynamics_degraded

    @property
    def is_complete(self) -> bool:
        """两侧都完整（无任何中性值填充）."""
        return self.geometry_result.is_complete and self.dynamics_result.is_complete

    @property
    def completeness_ratio(self) -> float:
        """聚合完整性比例 = (几何完整性 + 动力学完整性) / 2.

        几何侧 4 字段、动力学侧 6 字段，各自 ``completeness_ratio`` 已归一化
        到 [0, 1]，这里取算术平均作为整体完整性指标.
        """
        return (self.geometry_result.completeness_ratio + self.dynamics_result.completeness_ratio) / 2.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（供 MLflow 记录与日志输出）."""
        return {
            "unified_state": self.unified_state.to_dict(),
            "geometry_result": self.geometry_result.to_dict(),
            "dynamics_result": self.dynamics_result.to_dict(),
            "geometry_degraded": self.geometry_degraded,
            "dynamics_degraded": self.dynamics_degraded,
            "should_degrade": self.should_degrade,
            "is_complete": self.is_complete,
            "completeness_ratio": self.completeness_ratio,
        }


# 组装器


class UnifiedStateAssembler:
    """UnifiedState 组装器：组合几何特征 + 动力学状态 → UnifiedState.

    所有方法为静态/类方法，无状态，线程安全.

    使用示例
    --------
    >>> from app.feature_extraction.feature_store import ExtractedFeature
    >>> features = [ExtractedFeature(
    ...     feature_id="p1", feature_type="plane",
    ...     params={"normal": [0, 0, 1], "offset": 0.0, "area_mm2": 500.0},
    ...     confidence=0.95,
    ... )]
    >>> vertices = [[0, 0, 0], [10, 0, 0], [0, 20, 0], [0, 0, 30]]
    >>> current_state = {
    ...     "spindle_speed": 8000.0, "feed_rate": 1200.0, "depth_of_cut": 0.5,
    ...     "tool_wear": 0.05, "vibration_rms": 0.32, "temperature": 42.5,
    ... }
    >>> result = UnifiedStateAssembler.assemble_from_sources(
    ...     features, vertices, current_state
    ... )
    >>> result.unified_state.geometry.bbox_dimensions
    (10.0, 20.0, 30.0)
    >>> result.should_degrade
    False
    """

    @staticmethod
    def assemble(
        geometry: GeometryFeatures,
        dynamics: DynamicsState,
    ) -> UnifiedState:
        """纯组装：两个已派生结果 → UnifiedState.

        Parameters
        ----------
        geometry : GeometryFeatures
            已派生的几何特征（来自 ``GeometryFeaturesDeriver``）.
        dynamics : DynamicsState
            已桥接的动力学状态（来自 ``DynamicsStateBridge``）.

        Returns
        -------
        UnifiedState
            组装后的统一状态. ``fused_embedding=None``，由 ``FusionLayer``
            在 WorldModelNet 前向时填充.
        """
        return UnifiedState(geometry=geometry, dynamics=dynamics)

    @classmethod
    def assemble_from_results(
        cls,
        geometry_result: DerivationResult,
        dynamics_result: BridgeResult,
    ) -> AssemblerResult:
        """从两个子结果组装 + 聚合诊断.

        Parameters
        ----------
        geometry_result : DerivationResult
            P0-2 ``GeometryFeaturesDeriver.from_feature_extraction`` 的返回值.
        dynamics_result : BridgeResult
            P0-1 ``DynamicsStateBridge.from_current_state`` 的返回值.

        Returns
        -------
        AssemblerResult
            含组装后的 ``UnifiedState`` 与聚合诊断.
        """
        unified = cls.assemble(geometry_result.geometry, dynamics_result.dynamics)
        geometry_degraded = GeometryFeaturesDeriver.should_degrade(geometry_result)
        dynamics_degraded = DynamicsStateBridge.should_degrade(dynamics_result)
        return AssemblerResult(
            unified_state=unified,
            geometry_result=geometry_result,
            dynamics_result=dynamics_result,
            geometry_degraded=geometry_degraded,
            dynamics_degraded=dynamics_degraded,
        )

    @classmethod
    def assemble_from_sources(
        cls,
        features: list[Any],
        vertices: Sequence[Sequence[float]] | Any | None,
        current_state: dict[str, float],
        filter_reviewed: bool = False,
    ) -> AssemblerResult:
        """端到端组装：ADR-007 特征 + legacy current_state → UnifiedState.

        组合 P0-2（``GeometryFeaturesDeriver``）+ P0-1（``DynamicsStateBridge``）
        + 组装，一步到位. 这是"零件装到车上"的完整路径，让真实数据源产出的
        几何特征与动力学状态真正流入 WorldModelNet 融合路径.

        Parameters
        ----------
        features : list[ExtractedFeature]
            ADR-007 ``FeatureExtractionPipeline`` 产出的特征列表
            （plane/cylinder/hole/boss/unknown）.
        vertices : array-like, optional
            mesh 顶点数组，形状 (N, 3). 用于派生 ``bbox_dimensions``.
            ``None`` 时 bbox 用 (0,0,0) 填充并标记 defaulted.
        current_state : dict[str, float]
            ADR-017 legacy 状态字典（``StateField`` 字段名）. 允许部分缺失，
            缺失字段用 0.0 填充并标记 defaulted.
        filter_reviewed : bool
            True 时只保留 ``confirmed`` / ``edited`` 状态的特征
            （与 ADR-007 导出 ``confirmed_features.json`` 的过滤规则一致）.

        Returns
        -------
        AssemblerResult
            含组装后的 ``UnifiedState`` 与聚合诊断. 不抛异常
            （与 P0-1/P0-2 一致，缺失字段用中性值填充并标记）.
        """
        geometry_result = GeometryFeaturesDeriver.from_feature_extraction(
            features, vertices, filter_reviewed=filter_reviewed
        )
        dynamics_result = DynamicsStateBridge.from_current_state(current_state)
        return cls.assemble_from_results(geometry_result, dynamics_result)


__all__ = ["AssemblerResult", "UnifiedStateAssembler"]
