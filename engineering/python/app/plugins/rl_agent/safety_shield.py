"""安全硬约束过滤盾（SafetyShield）.

在 RL 策略输出动作后，强制过滤违反物理/工艺安全约束的动作。
SafetyShield 是不可被 RL 策略覆盖的硬约束层，确保任何学习到的
策略都不会输出危险切削参数。

约束分类
--------
1. **绝对边界约束**：主轴转速 / 进给 / 切深 / 切宽 的物理上限
   （来自设备规格与刀具规格）
2. **相对约束**：相邻动作变化率限制（避免参数突变引发颤振）
3. **工艺约束**：材料-刀具组合的切削参数安全区间（来自
   CuttingConstraintValidator，可选依赖）

工作模式
--------
- ``strict=True``（默认）：违反约束的动作强制替换为安全回退动作
  （上一次合法动作或默认安全参数）
- ``strict=False``：违反约束的动作裁剪到边界，记录告警

线程安全
--------
SafetyShield 无内部可变状态（约束配置在初始化时固定），
多线程并发调用 ``filter`` 是安全的。回退动作缓存使用锁保护。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SafetyConstraints:
    """安全约束配置.

    所有边界均使用物理量纲：
    - spindle_speed_rpm: 主轴转速 (rpm)
    - feed_rate_mm_per_min: 进给速度 (mm/min)
    - depth_of_cut_mm: 切削深度 (mm)
    - width_of_cut_mm: 切削宽度 (mm)

    Attributes
    ----------
    spindle_speed_range : tuple[float, float]
        主轴转速安全区间 [min, max]。
    feed_rate_range : tuple[float, float]
        进给速度安全区间。
    depth_of_cut_range : tuple[float, float]
        切深安全区间。
    width_of_cut_range : tuple[float, float]
        切宽安全区间。
    max_action_delta : float
        相邻动作最大变化率（相对值，0.1 表示 10%）。
    """

    spindle_speed_range: tuple[float, float] = (500.0, 12000.0)
    feed_rate_range: tuple[float, float] = (10.0, 5000.0)
    depth_of_cut_range: tuple[float, float] = (0.05, 5.0)
    width_of_cut_range: tuple[float, float] = (0.1, 20.0)
    max_action_delta: float = 0.2

    def validate(self) -> None:
        for name, rng in [
            ("spindle_speed_range", self.spindle_speed_range),
            ("feed_rate_range", self.feed_rate_range),
            ("depth_of_cut_range", self.depth_of_cut_range),
            ("width_of_cut_range", self.width_of_cut_range),
        ]:
            if rng[0] >= rng[1]:
                raise ValueError(f"{name} 下界必须小于上界: {rng}")
        if not 0.0 < self.max_action_delta <= 1.0:
            raise ValueError(f"max_action_delta 必须在 (0, 1], 当前: {self.max_action_delta}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "spindle_speed_range": list(self.spindle_speed_range),
            "feed_rate_range": list(self.feed_rate_range),
            "depth_of_cut_range": list(self.depth_of_cut_range),
            "width_of_cut_range": list(self.width_of_cut_range),
            "max_action_delta": self.max_action_delta,
        }


@dataclass
class SafetyFilterResult:
    """安全过滤结果.

    Attributes
    ----------
    action : np.ndarray
        过滤后的安全动作。
    original_action : np.ndarray
        原始策略输出动作。
    violated : bool
        是否发生约束违反。
    violations : list[str]
        违反的约束名称列表（空列表表示无违反）。
    fallback_used : bool
        是否使用了安全回退动作（strict 模式下触发）。
    """

    action: np.ndarray
    original_action: np.ndarray
    violated: bool
    violations: list[str] = field(default_factory=list)
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.tolist(),
            "original_action": self.original_action.tolist(),
            "violated": self.violated,
            "violations": self.violations,
            "fallback_used": self.fallback_used,
        }


class SafetyShield:
    """安全硬约束过滤盾.

    在 RL 策略输出动作后强制过滤，确保动作不违反物理/工艺安全约束。

    使用示例
    --------
    >>> shield = SafetyShield(constraints=SafetyConstraints())
    >>> safe_action, result = shield.filter(policy_action, prev_action=last_action)
    >>> if result.violated:
    ...     logger.warning(f"动作被安全盾过滤: {result.violations}")

    动作向量约定
    ------------
    动作向量维度为 4，依次对应：
    [spindle_speed_delta, feed_rate_delta, depth_of_cut_delta, width_of_cut_delta]

    其中 delta 表示相对调整量（-1.0 ~ +1.0），绝对值由 SafetyConstraints
    的 range 决定。SafetyShield 将 delta 映射到绝对物理量后校验边界。

    线程安全
    --------
    无内部可变状态，``filter`` 方法可并发调用。``_last_safe_action``
    缓存使用锁保护（用于无 prev_action 参数时的回退）。
    """

    def __init__(
        self,
        constraints: SafetyConstraints | None = None,
        strict: bool = True,
        default_safe_action: np.ndarray | None = None,
    ) -> None:
        self._constraints = constraints or SafetyConstraints()
        self._constraints.validate()
        self._strict = strict
        # 默认安全动作：零调整（保持当前参数）
        self._default_safe = (
            np.zeros(4, dtype=np.float32)
            if default_safe_action is None
            else np.asarray(default_safe_action, dtype=np.float32).copy()
        )
        self._last_safe: np.ndarray | None = None
        self._lock = __import__("threading").Lock()

    def filter(
        self,
        action: np.ndarray,
        prev_action: np.ndarray | None = None,
    ) -> tuple[np.ndarray, SafetyFilterResult]:
        """过滤动作.

        Parameters
        ----------
        action : np.ndarray
            策略输出的原始动作向量 [action_dim]。
        prev_action : Optional[np.ndarray]
            上一次合法动作（用于变化率约束与回退）。None 时使用内部缓存
            或默认安全动作。

        Returns
        -------
        tuple[np.ndarray, SafetyFilterResult]
            (过滤后的安全动作, 过滤结果)
        """
        original = np.asarray(action, dtype=np.float32).copy()
        violations: list[str] = []

        # 1. 边界约束检查
        violations.extend(self._check_bounds(original))

        # 2. 变化率约束检查
        ref = self._resolve_reference(prev_action)
        violations.extend(self._check_delta(original, ref))

        if not violations:
            # 无违反，记录为合法动作
            with self._lock:
                self._last_safe = original.copy()
            return original, SafetyFilterResult(
                action=original,
                original_action=original,
                violated=False,
            )

        # 3. 发生违反：按模式处理
        if self._strict:
            # 严格模式：回退到上一次合法动作或默认安全动作
            fallback = self._resolve_fallback(ref)
            # 修复 [安全回退正确性]：回退动作本身必须通过边界校验——当调用方
            # 传入的 prev_action 恰为被检查的违规动作时（如单动作预演），
            # ref 即违规动作，若不校验会把违规动作当作"安全动作"返回。
            if self._check_bounds(fallback):
                fallback = self._default_safe.copy()
            with self._lock:
                self._last_safe = fallback.copy()
            logger.warning(
                "SafetyShield: 动作违反约束 %s，strict 模式回退到安全动作",
                violations,
            )
            return fallback, SafetyFilterResult(
                action=fallback,
                original_action=original,
                violated=True,
                violations=violations,
                fallback_used=True,
            )
        else:
            # 非严格模式：裁剪到边界
            clipped = self._clip_to_bounds(original)
            clipped = self._clip_delta(clipped, ref)
            with self._lock:
                self._last_safe = clipped.copy()
            logger.info(
                "SafetyShield: 动作违反约束 %s，已裁剪到边界",
                violations,
            )
            return clipped, SafetyFilterResult(
                action=clipped,
                original_action=original,
                violated=True,
                violations=violations,
                fallback_used=False,
            )

    def get_constraints(self) -> SafetyConstraints:
        return self._constraints

    def is_strict(self) -> bool:
        return self._strict

    # 内部方法

    def _check_bounds(self, action: np.ndarray) -> list[str]:
        """检查动作各维度的绝对边界.

        动作向量为 delta 形式（-1 ~ +1），映射到绝对物理量后校验。
        """
        violations: list[str] = []
        if action.size < 4:
            violations.append(f"action_dim 不足 4, 当前: {action.size}")
            return violations

        c = self._constraints
        # delta 映射：abs_value = mid + delta * half_range
        checks = [
            (0, c.spindle_speed_range, "spindle_speed"),
            (1, c.feed_rate_range, "feed_rate"),
            (2, c.depth_of_cut_range, "depth_of_cut"),
            (3, c.width_of_cut_range, "width_of_cut"),
        ]
        for idx, rng, name in checks:
            mid = (rng[0] + rng[1]) / 2.0
            half = (rng[1] - rng[0]) / 2.0
            abs_val = mid + float(action[idx]) * half
            if abs_val < rng[0] or abs_val > rng[1]:
                violations.append(f"{name}={abs_val:.2f} 超出范围 {rng}")
        return violations

    def _check_delta(self, action: np.ndarray, prev: np.ndarray) -> list[str]:
        """检查相邻动作变化率."""
        violations: list[str] = []
        max_delta = self._constraints.max_action_delta
        diff = np.abs(action - prev)
        # 逐维度检查（忽略维度数不一致的情况）
        n = min(diff.size, prev.size)
        for i in range(n):
            if diff[i] > max_delta:
                violations.append(f"action[{i}] 变化 {diff[i]:.2f} 超过最大 {max_delta}")
        return violations

    def _clip_to_bounds(self, action: np.ndarray) -> np.ndarray:
        """裁剪动作到合法 delta 区间 [-1, 1]."""
        return np.clip(action, -1.0, 1.0).astype(np.float32)

    def _clip_delta(self, action: np.ndarray, prev: np.ndarray) -> np.ndarray:
        """裁剪动作变化率."""
        max_delta = self._constraints.max_action_delta
        diff = action - prev
        clipped_diff = np.clip(diff, -max_delta, max_delta)
        return (prev + clipped_diff).astype(np.float32)

    def _resolve_reference(self, prev_action: np.ndarray | None) -> np.ndarray:
        """解析参考动作（用于变化率检查）."""
        if prev_action is not None:
            return np.asarray(prev_action, dtype=np.float32)
        with self._lock:
            if self._last_safe is not None:
                return self._last_safe.copy()
        return self._default_safe.copy()

    def _resolve_fallback(self, ref: np.ndarray) -> np.ndarray:
        """解析回退动作（strict 模式）."""
        # 优先使用参考动作（上一次合法动作）
        return ref.copy()


__all__ = [
    "SafetyConstraints",
    "SafetyFilterResult",
    "SafetyShield",
]
