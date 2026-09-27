"""参数推荐器（Phase D 核心，纯白盒）。

分层推荐策略：
- L0: 规则基线（材料×加工类型经验表）
- L1: 统计推荐（同材料同刀具历史实测均值，数据来自 cutting_experience）
- L2/L3: 预留（LNN 模型 / 贝叶斯优化，需 torch，本模块不依赖）

推荐管线：
1. 查基线（L0）→ 得到初始参数 + 安全区间
2. 若有历史统计数据（L1 回调提供），用统计均值覆盖
3. 物理安全钳制：所有参数 clamp 到安全区间
4. 产出 Recommendation（含 strategy 标记 + 依据，可审计）

设计要点：
- 零框架依赖（纯 dataclass + typing），CI 可独立跑全量覆盖
- 优雅降级：数据不足 → L0；无基线 → 返回 None（由调用方提示）
- 可解释：basis 记录推荐依据（基线来源 / 统计样本数）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from collections.abc import Callable


class OptimizationTarget(str, Enum):
    """优化目标（影响推荐倾向）。"""

    BALANCED = "balanced"  # 均衡
    CYCLE_TIME = "cycle_time"  # 节拍优先（提高转速/进给）
    TOOL_LIFE = "tool_life"  # 寿命优先（降低转速/进给）
    SURFACE = "surface"  # 表面质量优先（降低进给）


class RecommendationStrategy(str, Enum):
    """推荐策略层级。"""

    L0_BASELINE = "L0_baseline"
    L1_STATISTICAL = "L1_statistical"
    L2_MODEL = "L2_model"
    L3_BAYESIAN = "L3_bayesian"


@dataclass
class Recommendation:
    """一次参数推荐结果（可审计）。"""

    depth_of_cut_mm: float
    feed_mm_per_rev: float
    spindle_rpm: float
    cutting_speed_m_min: float
    strategy: RecommendationStrategy
    confidence: float = 0.0  # 0-1
    basis: list[dict[str, Any]] = field(default_factory=list)
    clamped: bool = False  # 是否发生了物理安全钳制

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth_of_cut_mm": self.depth_of_cut_mm,
            "feed_mm_per_rev": self.feed_mm_per_rev,
            "spindle_rpm": self.spindle_rpm,
            "cutting_speed_m_min": self.cutting_speed_m_min,
            "strategy": self.strategy.value,
            "confidence": self.confidence,
            "basis": self.basis,
            "clamped": self.clamped,
        }


# 统计回调：给定 (material, tool_id, machining_type) 返回历史均值参数
# （由调用方接入 cutting_experience_repository 聚合统计）
StatsCallback = Callable[[str, str, str], dict[str, float] | None]


def clamp_to_safe_bounds(
    value: float,
    min_value: float,
    max_value: float,
) -> tuple[float, bool]:
    """将值钳制到 [min, max]，返回 (钳制后值, 是否发生钳制)。"""
    if value < min_value:
        return min_value, True
    if value > max_value:
        return max_value, True
    return value, False


class ParameterRecommender:
    """分层参数推荐器。"""

    def __init__(
        self,
        baseline_lookup: Callable[[str, str], Any] | None = None,
        stats_callback: StatsCallback | None = None,
    ) -> None:
        """
        Args:
            baseline_lookup: (material, machining_type) → BaselineEntry 或 None。
                默认用 app.optimizer.baseline.lookup_baseline。
            stats_callback: (material, tool_id, machining_type) → 历史均值 dict
                或 None（无数据时降级 L0）。
        """
        if baseline_lookup is None:
            from .baseline import lookup_baseline

            baseline_lookup = lookup_baseline
        self._baseline_lookup = baseline_lookup
        self._stats_callback = stats_callback

    def recommend(
        self,
        material: str,
        machining_type: str,
        tool_id: str = "",
        target: OptimizationTarget = OptimizationTarget.BALANCED,
    ) -> Recommendation | None:
        """推荐切削参数。

        Returns:
            Recommendation；材料/加工类型无基线且无统计时返回 None。
        """
        # L0: 查基线
        entry = self._baseline_lookup(material, machining_type)
        if entry is None:
            # 尝试 L1 统计（即使无基线也可能有历史数据）
            stats = self._try_stats(material, machining_type, tool_id)
            if stats is None:
                return None
            return self._build_from_stats(stats, material, machining_type, tool_id)

        # ---- L1: 统计覆盖（若有数据）----
        stats = self._try_stats(material, machining_type, tool_id)
        if stats is not None:
            return self._build_from_stats(stats, material, machining_type, tool_id)

        # L0 基线推荐
        depth, clamped_depth = clamp_to_safe_bounds(entry.depth_of_cut_mm, entry.depth_min, entry.depth_max)
        feed, clamped_feed = clamp_to_safe_bounds(entry.feed_mm_per_rev, entry.feed_min, entry.feed_max)
        rpm, clamped_rpm = clamp_to_safe_bounds(entry.spindle_rpm, entry.rpm_min, entry.rpm_max)
        clamped = clamped_depth or clamped_feed or clamped_rpm

        # 按优化目标微调
        depth, feed, rpm, speed = self._apply_target(depth, feed, rpm, entry.cutting_speed_m_min, target)

        return Recommendation(
            depth_of_cut_mm=depth,
            feed_mm_per_rev=feed,
            spindle_rpm=rpm,
            cutting_speed_m_min=speed,
            strategy=RecommendationStrategy.L0_BASELINE,
            confidence=0.5,
            basis=[{"source": "baseline", "material": entry.material}],
            clamped=clamped,
        )

    # 内部

    def _try_stats(self, material: str, machining_type: str, tool_id: str) -> dict[str, float] | None:
        """尝试获取统计均值（L1）。"""
        if self._stats_callback is None:
            return None
        try:
            return self._stats_callback(material, tool_id, machining_type)
        except Exception:
            # 统计源异常不阻塞推荐（降级 L0）
            return None

    def _build_from_stats(
        self,
        stats: dict[str, float],
        material: str,
        machining_type: str,
        tool_id: str,
    ) -> Recommendation:
        """由统计均值构造推荐（L1）。"""
        depth = stats.get("depth_of_cut_mm", 1.0)
        feed = stats.get("feed_mm_per_rev", 0.15)
        rpm = stats.get("spindle_rpm", 6000.0)
        speed = stats.get("cutting_speed_m_min", 150.0)

        # 用基线安全区间钳制（若基线存在）
        entry = self._baseline_lookup(material, machining_type)
        if entry is not None:
            depth, cd = clamp_to_safe_bounds(depth, entry.depth_min, entry.depth_max)
            feed, cf = clamp_to_safe_bounds(feed, entry.feed_min, entry.feed_max)
            rpm, cr = clamp_to_safe_bounds(rpm, entry.rpm_min, entry.rpm_max)
        else:
            cd = cf = cr = False

        n = int(stats.get("sample_count", 0))
        return Recommendation(
            depth_of_cut_mm=depth,
            feed_mm_per_rev=feed,
            spindle_rpm=rpm,
            cutting_speed_m_min=speed,
            strategy=RecommendationStrategy.L1_STATISTICAL,
            confidence=min(0.9, 0.5 + n * 0.05),
            basis=[
                {
                    "source": "statistics",
                    "material": material,
                    "tool_id": tool_id,
                    "sample_count": n,
                }
            ],
            clamped=cd or cf or cr,
        )

    @staticmethod
    def _apply_target(
        depth: float,
        feed: float,
        rpm: float,
        speed: float,
        target: OptimizationTarget,
    ) -> tuple[float, float, float, float]:
        """按优化目标微调参数（±20% 内，保守调整）。"""
        if target == OptimizationTarget.CYCLE_TIME:
            # 节拍优先：提高进给与转速
            feed = round(feed * 1.2, 4)
            rpm = round(rpm * 1.1)
            speed = round(speed * 1.1, 1)
        elif target == OptimizationTarget.TOOL_LIFE:
            # 寿命优先：降低进给与转速
            feed = round(feed * 0.8, 4)
            rpm = round(rpm * 0.9)
            speed = round(speed * 0.9, 1)
        elif target == OptimizationTarget.SURFACE:
            # 表面质量：降低进给（减少残留高度）
            feed = round(feed * 0.7, 4)
        return depth, feed, rpm, speed


__all__ = [
    "OptimizationTarget",
    "Recommendation",
    "RecommendationStrategy",
    "ParameterRecommender",
    "StatsCallback",
    "clamp_to_safe_bounds",
]
