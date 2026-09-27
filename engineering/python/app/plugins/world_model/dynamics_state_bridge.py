"""legacy current_state → DynamicsState 桥接器.

ADR-020 思路 1 的 P0 数据解锁工具。将 ADR-017 的 legacy ``current_state``
（``StateField`` 字典）中的 6 个动力学字段提取为 ``DynamicsState``，
让融合架构在不伪造数据的前提下获得真实动力学输入.

设计边界（与 ADR-020 §1.3 一致）
--------------------------------
- **纯字段映射，非伪造**：``DynamicsState`` 的 6 个字段在 ``StateField`` 中
  全部存在且语义一致（同名同单位），本桥接器只做"字段重命名 + 子集提取"，
  不创造任何新数据
- **缺失字段显式标记**：返回 ``BridgeResult.defaulted_fields``，调用方据此
  决策是否降级到传统路径（``defaulted_fields`` 过多则不应走融合路径）
- **不抛异常**：即使 ``current_state`` 完全为空也返回 ``BridgeResult``，
  ``missing_fields`` / ``defaulted_fields`` 会反映真实完整性

字段映射表
----------
+-------------------------+---------------------------+--------+
| StateField (legacy)     | DynamicsState (融合)      | 单位   |
+-------------------------+---------------------------+--------+
| SPINDLE_SPEED           | spindle_speed             | rpm    |
| FEED_RATE               | feed_rate                 | mm/min |
| DEPTH_OF_CUT            | depth_of_cut              | mm     |
| TOOL_WEAR               | tool_wear                 | mm     |
| VIBRATION_RMS           | vibration_rms             | g      |
| TEMPERATURE             | temperature               | °C     |
+-------------------------+---------------------------+--------+

注意：``StateField.WIDTH_OF_CUT`` 与 ``StateField.CHATTER_PROBABILITY``
不在映射表中——前者在 ``DynamicsState`` v1 设计中未包含（简化），
后者是预测输出而非动力学输入.

对应 ADR：ADR-020 思路 1 / ADR-017 世界模型与 RL 模块
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.contracts.world_model import StateField
from app.plugins.world_model.unified_state import DynamicsState

logger = logging.getLogger(__name__)


# 字段映射：StateField DynamicsState 字段名

FIELD_MAPPING: dict[str, str] = {
    StateField.SPINDLE_SPEED: "spindle_speed",
    StateField.FEED_RATE: "feed_rate",
    StateField.DEPTH_OF_CUT: "depth_of_cut",
    StateField.TOOL_WEAR: "tool_wear",
    StateField.VIBRATION_RMS: "vibration_rms",
    StateField.TEMPERATURE: "temperature",
}
"""legacy ``StateField`` → ``DynamicsState`` 字段名映射（6 个字段，一一对应）."""

REQUIRED_FIELDS: tuple[str, ...] = tuple(FIELD_MAPPING.keys())
"""桥接所需的最小字段集合（6 个，全部来自 ``StateField``）."""


# 桥接结果


@dataclass
class BridgeResult:
    """``DynamicsStateBridge`` 桥接结果.

    同时返回桥接后的 ``DynamicsState`` 与完整性诊断信息，让调用方
    （``WorldModelService`` / ``WorldModelPlugin``）决策是否走融合路径.

    Attributes
    ----------
    dynamics : DynamicsState
        桥接后的动力学状态. 缺失字段用 ``default`` 填充.
    missing_fields : list[str]
        ``current_state`` 中完全缺失的字段（既无值也未提供）.
    defaulted_fields : list[str]
        实际用 ``default`` 填充的字段（``missing_fields`` 的子集，
        用于上层降级决策）.
    source : str
        数据来源标记，固定为 ``"legacy_current_state"``，便于 MLflow
        追踪与审计.
    """

    dynamics: DynamicsState
    missing_fields: list[str] = field(default_factory=list)
    defaulted_fields: list[str] = field(default_factory=list)
    source: str = "legacy_current_state"

    @property
    def is_complete(self) -> bool:
        """是否所有 6 个字段都有真实值（无默认填充）."""
        return len(self.defaulted_fields) == 0

    @property
    def completeness_ratio(self) -> float:
        """完整性比例 = 真实字段数 / 6.

        调用方可据此阈值降级：如 ``completeness_ratio < 0.5`` 则不走融合路径.
        """
        real_field_count = len(REQUIRED_FIELDS) - len(self.defaulted_fields)
        return real_field_count / len(REQUIRED_FIELDS)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（供 MLflow 记录与日志输出）."""
        return {
            "dynamics": {
                "spindle_speed": self.dynamics.spindle_speed,
                "feed_rate": self.dynamics.feed_rate,
                "depth_of_cut": self.dynamics.depth_of_cut,
                "tool_wear": self.dynamics.tool_wear,
                "vibration_rms": self.dynamics.vibration_rms,
                "temperature": self.dynamics.temperature,
            },
            "missing_fields": list(self.missing_fields),
            "defaulted_fields": list(self.defaulted_fields),
            "source": self.source,
            "is_complete": self.is_complete,
            "completeness_ratio": self.completeness_ratio,
        }


# 桥接器


class DynamicsStateBridge:
    """legacy ``current_state`` → ``DynamicsState`` 桥接器.

    所有方法均为类方法或静态方法，无状态，线程安全.

    使用示例
    --------
    >>> from app.contracts.world_model import StateField
    >>> current_state = {
    ...     StateField.SPINDLE_SPEED: 8000.0,
    ...     StateField.FEED_RATE: 1200.0,
    ...     StateField.DEPTH_OF_CUT: 0.5,
    ...     StateField.TOOL_WEAR: 0.12,
    ...     StateField.VIBRATION_RMS: 0.8,
    ...     StateField.TEMPERATURE: 45.0,
    ... }
    >>> result = DynamicsStateBridge.from_current_state(current_state)
    >>> result.is_complete
    True
    >>> result.dynamics.spindle_speed
    8000.0
    """

    DEFAULT_FILL_VALUE: float = 0.0
    """缺失字段的默认填充值. 取 0.0 是中性值，不会引入虚假动力学信号."""

    # 降级阈值：defaulted_fields 数量超过此值时，调用方应降级到传统路径
    DEGRADE_THRESHOLD: int = 3
    """降级阈值. ``defaulted_fields`` 数量 >= 此值时，``should_degrade`` 为 True.

    取 3 的理由：6 个字段中若有 3 个以上缺失，完整性 < 50%，
    融合 embedding 学到的信号将主要来自默认填充值而非真实动力学.
    """

    @classmethod
    def from_current_state(
        cls,
        current_state: dict[str, float],
        default: float | None = None,
    ) -> BridgeResult:
        """从 legacy ``current_state`` 桥接出 ``DynamicsState``.

        Parameters
        ----------
        current_state : dict[str, float]
            ADR-017 legacy 状态字典，字段名见 ``StateField``.
            允许部分缺失，缺失字段用 ``default`` 填充.
        default : float, optional
            缺失字段的填充值. ``None`` 时使用 ``DEFAULT_FILL_VALUE`` (0.0).
            调用方不应传入"看起来像真实数据"的值（如 8000.0），那等同于伪造.

        Returns
        -------
        BridgeResult
            包含 ``DynamicsState`` 与完整性诊断. 不抛异常.

        Notes
        -----
        本方法 **不伪造数据**：
        - 缺失字段用 0.0 填充（中性值），并在 ``defaulted_fields`` 中显式标记
        - 调用方必须检查 ``BridgeResult.is_complete`` 或
          ``completeness_ratio``，决定是否走融合路径
        - 若 ``should_degrade`` 为 True，强烈建议降级到传统路径
          （``unified_state=None``）
        """
        fill_value = cls.DEFAULT_FILL_VALUE if default is None else default
        values: dict[str, float] = {}
        missing: list[str] = []
        defaulted: list[str] = []

        for state_field, dynamics_field in FIELD_MAPPING.items():
            if state_field in current_state:
                values[dynamics_field] = float(current_state[state_field])
            else:
                missing.append(state_field)
                defaulted.append(state_field)
                values[dynamics_field] = fill_value

        if missing:
            logger.warning(
                "DynamicsStateBridge: current_state 缺失 %d/%d 个动力学字段: %s. 这些字段用 %s 填充，融合路径可能降级.",
                len(missing),
                len(REQUIRED_FIELDS),
                missing,
                fill_value,
            )

        dynamics = DynamicsState(
            spindle_speed=values["spindle_speed"],
            feed_rate=values["feed_rate"],
            depth_of_cut=values["depth_of_cut"],
            tool_wear=values["tool_wear"],
            vibration_rms=values["vibration_rms"],
            temperature=values["temperature"],
        )

        return BridgeResult(
            dynamics=dynamics,
            missing_fields=missing,
            defaulted_fields=defaulted,
            source="legacy_current_state",
        )

    @classmethod
    def from_current_state_strict(
        cls,
        current_state: dict[str, float],
    ) -> DynamicsState:
        """严格模式：任一字段缺失则抛 ``ValueError``.

        适用于测试场景或调用方确定 ``current_state`` 完整的情况.
        生产路径推荐使用 ``from_current_state`` + ``BridgeResult`` 决策.

        Raises
        ------
        ValueError
            若 ``current_state`` 缺失任一动力学字段.
        """
        missing: list[str] = [f for f in REQUIRED_FIELDS if f not in current_state]
        if missing:
            raise ValueError(
                f"current_state 缺失动力学字段: {missing}. "
                f"严格模式要求全部 {len(REQUIRED_FIELDS)} 个字段齐全: "
                f"{list(REQUIRED_FIELDS)}"
            )

        return cls.from_current_state(current_state).dynamics

    @staticmethod
    def should_degrade(result: BridgeResult, threshold: int | None = None) -> bool:
        """判断是否应降级到传统路径.

        Parameters
        ----------
        result : BridgeResult
            ``from_current_state`` 的返回值.
        threshold : int, optional
            降级阈值. ``None`` 时使用 ``DEGRADE_THRESHOLD`` (3).

        Returns
        -------
        bool
            ``True`` 表示 ``defaulted_fields`` 数量 >= 阈值，应降级.
        """
        thresh = DynamicsStateBridge.DEGRADE_THRESHOLD if threshold is None else threshold
        return len(result.defaulted_fields) >= thresh


__all__ = [
    "FIELD_MAPPING",
    "REQUIRED_FIELDS",
    "BridgeResult",
    "DynamicsStateBridge",
]
