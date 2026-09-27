"""RL 奖励函数.

对应 ADR-017 第 4 节。奖励函数将世界模型预测的状态 + RL 策略输出的动作
映射为标量奖励，引导策略向"低颤振 + 低磨损 + 高质量 + 高效率"方向优化。

奖励组成
--------
1. **安全惩罚**（safety_penalty）：违反安全约束 → 大幅负奖励（-10）
2. **颤振惩罚**（chatter_penalty）：颤振概率越高，惩罚越大（系数 ``chatter_weight``）
3. **磨损惩罚**（wear_penalty）：刀具磨损增量越大，惩罚越大（系数 ``wear_weight``）
4. **质量奖励**（quality_bonus）：表面粗糙度低于阈值 → 正奖励
5. **材料去除率**（material_removal）：进给量越大，材料去除越多（系数 ``mrr_weight``）

奖励函数参数化（``RewardConfig``），α/β 系数可调，避免奖励函数设计偏差
导致策略退化（见 ADR-017 风险表）。

线程安全
--------
``RewardFunction`` 无内部可变状态（配置在初始化时固定），
多线程并发调用 ``compute`` 是安全的。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any


logger = logging.getLogger(__name__)


# 默认状态/动作字段名

# 状态字典中预期的字段名（与世界模型输出对齐）
STATE_CHATTER_PROB = "chatter_probability"
STATE_TOOL_WEAR_INC = "tool_wear_increment"
STATE_SURFACE_ROUGHNESS = "surface_roughness"

# 动作字典中预期的字段名（与 RLAgentPlugin 动作向量标签对齐）
ACTION_SPINDLE_DELTA = "spindle_speed_delta"
ACTION_FEED_DELTA = "feed_rate_delta"
ACTION_DEPTH_DELTA = "depth_of_cut_delta"
ACTION_WIDTH_DELTA = "width_of_cut_delta"


# 奖励配置


@dataclass
class RewardConfig:
    """奖励函数配置.

    所有权重均为正数，奖励符号由 ``RewardFunction`` 内部决定
    （惩罚项取负，奖励项取正）。

    Attributes
    ----------
    chatter_weight : float
        颤振概率惩罚权重（``reward -= chatter_prob * chatter_weight``）。
    wear_weight : float
        刀具磨损增量惩罚权重（``reward -= wear_inc * wear_weight``）。
    quality_bonus : float
        表面质量达标奖励（``reward += quality_bonus``）。
    quality_threshold : float
        表面粗糙度阈值（Ra μm），低于此值触发质量奖励。
    mrr_weight : float
        材料去除率奖励权重（``reward += feed_delta * mrr_weight``）。
    safety_penalty : float
        安全约束违反惩罚（负值，默认 -10.0）。
    chatter_critical_threshold : float
        颤振概率临界阈值，超过此值额外惩罚（避免临界颤振）。
    chatter_critical_extra_penalty : float
        临界颤振额外惩罚（负值）。
    """

    chatter_weight: float = 5.0
    wear_weight: float = 100.0
    quality_bonus: float = 0.5
    quality_threshold: float = 1.0
    mrr_weight: float = 0.001
    safety_penalty: float = -10.0
    chatter_critical_threshold: float = 0.5
    chatter_critical_extra_penalty: float = -2.0

    def validate(self) -> None:
        """校验配置合法性."""
        if self.chatter_weight < 0:
            raise ValueError(f"chatter_weight 不能为负数: {self.chatter_weight}")
        if self.wear_weight < 0:
            raise ValueError(f"wear_weight 不能为负数: {self.wear_weight}")
        if self.quality_bonus < 0:
            raise ValueError(f"quality_bonus 不能为负数: {self.quality_bonus}")
        if self.quality_threshold <= 0:
            raise ValueError(f"quality_threshold 必须为正数: {self.quality_threshold}")
        if self.mrr_weight < 0:
            raise ValueError(f"mrr_weight 不能为负数: {self.mrr_weight}")
        if self.safety_penalty >= 0:
            raise ValueError(f"safety_penalty 必须为负数（惩罚项）: {self.safety_penalty}")
        if not 0.0 <= self.chatter_critical_threshold <= 1.0:
            raise ValueError(f"chatter_critical_threshold 必须在 [0, 1], 当前: {self.chatter_critical_threshold}")
        if self.chatter_critical_extra_penalty >= 0:
            raise ValueError(f"chatter_critical_extra_penalty 必须为负数: {self.chatter_critical_extra_penalty}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chatter_weight": self.chatter_weight,
            "wear_weight": self.wear_weight,
            "quality_bonus": self.quality_bonus,
            "quality_threshold": self.quality_threshold,
            "mrr_weight": self.mrr_weight,
            "safety_penalty": self.safety_penalty,
            "chatter_critical_threshold": self.chatter_critical_threshold,
            "chatter_critical_extra_penalty": self.chatter_critical_extra_penalty,
        }


# 奖励分解（用于可解释性）


@dataclass
class RewardBreakdown:
    """奖励分解结果.

    记录每个奖励分量的贡献，用于训练日志与策略可解释性分析。

    Attributes
    ----------
    total : float
        总奖励（所有分量之和）。
    safety_penalty : float
        安全惩罚分量（0 表示未违反）。
    chatter_penalty : float
        颤振惩罚分量（负值或 0）。
    wear_penalty : float
        磨损惩罚分量（负值或 0）。
    quality_bonus : float
        质量奖励分量（正值或 0）。
    material_removal : float
        材料去除率奖励分量（可正可负）。
    critical_chatter_penalty : float
        临界颤振额外惩罚（负值或 0）。
    safety_violated : bool
        是否违反安全约束。
    """

    total: float
    safety_penalty: float = 0.0
    chatter_penalty: float = 0.0
    wear_penalty: float = 0.0
    quality_bonus: float = 0.0
    material_removal: float = 0.0
    critical_chatter_penalty: float = 0.0
    safety_violated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "safety_penalty": self.safety_penalty,
            "chatter_penalty": self.chatter_penalty,
            "wear_penalty": self.wear_penalty,
            "quality_bonus": self.quality_bonus,
            "material_removal": self.material_removal,
            "critical_chatter_penalty": self.critical_chatter_penalty,
            "safety_violated": self.safety_violated,
        }


# 奖励函数


class RewardFunction:
    """RL 奖励函数.

    将世界模型预测的状态 + RL 策略输出的动作映射为标量奖励。

    使用示例
    --------
    >>> config = RewardConfig()
    >>> reward_fn = RewardFunction(config)
    >>> breakdown = reward_fn.compute(
    ...     predicted_state={"chatter_probability": 0.2,
    ...                      "tool_wear_increment": 0.005,
    ...                      "surface_roughness": 0.8},
    ...     action={"feed_rate_delta": 100.0},
    ...     safety_violation=False,
    ... )
    >>> print(breakdown.total)

    线程安全
    --------
    无内部可变状态，``compute`` 方法可并发调用。
    """

    def __init__(self, config: RewardConfig | None = None) -> None:
        self._config = config or RewardConfig()
        self._config.validate()

    @property
    def config(self) -> RewardConfig:
        """奖励函数配置."""
        return self._config

    def compute(
        self,
        predicted_state: dict[str, Any],
        action: dict[str, Any],
        safety_violation: bool,
    ) -> RewardBreakdown:
        """计算奖励.

        Args:
            predicted_state: 世界模型预测的状态字典，应包含
                ``chatter_probability`` / ``tool_wear_increment`` /
                ``surface_roughness`` 字段。
            action: RL 策略输出的动作字典，应包含
                ``feed_rate_delta`` 等字段。
            safety_violation: 是否违反安全约束（来自 SafetyShield）。

        Returns
        -------
        RewardBreakdown
            奖励分解结果。
        """
        cfg = self._config

        # 1. 安全约束违反 大幅负奖励，直接返回
        if safety_violation:
            return RewardBreakdown(
                total=cfg.safety_penalty,
                safety_penalty=cfg.safety_penalty,
                safety_violated=True,
            )

        # 2. 颤振惩罚（线性）
        chatter_prob = float(predicted_state.get(STATE_CHATTER_PROB, 0.0))
        chatter_penalty = -chatter_prob * cfg.chatter_weight

        # 3. 磨损惩罚（线性）
        wear_inc = float(predicted_state.get(STATE_TOOL_WEAR_INC, 0.0))
        wear_penalty = -wear_inc * cfg.wear_weight

        # 4. 质量奖励（阶跃）
        surface_roughness = float(predicted_state.get(STATE_SURFACE_ROUGHNESS, float("inf")))
        quality_bonus = cfg.quality_bonus if surface_roughness < cfg.quality_threshold else 0.0

        # 5. 材料去除率奖励（线性，进给量越大材料去除越多）
        feed_delta = float(action.get(ACTION_FEED_DELTA, 0.0))
        material_removal = feed_delta * cfg.mrr_weight

        # 6. 临界颤振额外惩罚
        critical_chatter_penalty = 0.0
        if chatter_prob > cfg.chatter_critical_threshold:
            critical_chatter_penalty = cfg.chatter_critical_extra_penalty

        total = chatter_penalty + wear_penalty + quality_bonus + material_removal + critical_chatter_penalty

        return RewardBreakdown(
            total=total,
            chatter_penalty=chatter_penalty,
            wear_penalty=wear_penalty,
            quality_bonus=quality_bonus,
            material_removal=material_removal,
            critical_chatter_penalty=critical_chatter_penalty,
            safety_violated=False,
        )

    def compute_batch(
        self,
        states: list[dict[str, Any]],
        actions: list[dict[str, Any]],
        safety_violations: list[bool],
    ) -> list[RewardBreakdown]:
        """批量计算奖励.

        Args:
            states: 状态字典列表.
            actions: 动作字典列表.
            safety_violations: 安全违反标志列表.

        Returns
        -------
        list[RewardBreakdown]
            奖励分解列表，长度与输入一致.
        """
        n = len(states)
        if len(actions) != n or len(safety_violations) != n:
            raise ValueError(
                f"输入长度不一致: states={n}, actions={len(actions)}, safety_violations={len(safety_violations)}"
            )
        return [self.compute(s, a, v) for s, a, v in zip(states, actions, safety_violations)]


__all__ = [
    "RewardConfig",
    "RewardFunction",
    "RewardBreakdown",
    "STATE_CHATTER_PROB",
    "STATE_TOOL_WEAR_INC",
    "STATE_SURFACE_ROUGHNESS",
    "ACTION_SPINDLE_DELTA",
    "ACTION_FEED_DELTA",
    "ACTION_DEPTH_DELTA",
    "ACTION_WIDTH_DELTA",
]
