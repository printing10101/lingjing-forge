"""闭环决策数据模型与顶层入口（从 closed_loop 拆分，D5）。

数据类 + 独立入口函数；ClosedLoop 实现见 closed_loop.ClosedLoop。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ClosedLoopDecision:
    """闭环决策结果。

    Attributes:
        rule_id: 规则 ID。
        action: 决策动作（promote / demote / keep / rollback）。
        target_stage: 目标阶段（仅 promote/demote 有效）。
        reason: 决策原因（人类可读）。
        fused_confidence: Dempster-Shafer 融合后的置信度。
        conflict: 融合冲突系数（越高越不可信）。
        ds_mass: Dempster-Shafer 聚合质量。
        sample_count: 决策依据的样本数。
        evaluated_at: 决策时间戳。
        applied: 是否已应用（通过 ProgressivePublisher）。
        apply_error: 应用失败时的错误信息。
    """

    rule_id: str
    action: str  # promote | demote | keep | rollback
    target_stage: str | None = None
    reason: str = ""
    fused_confidence: float = 0.0
    conflict: float = 0.0
    ds_mass: float = 0.0
    sample_count: int = 0
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    applied: bool = False
    apply_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleOutcomeRecord:
    """单次规则触发的结果记录（用于滚动窗口）。

    Attributes:
        rule_id: 规则 ID。
        success: 是否成功。
        confidence: 触发时的置信度（0.0-1.0）。
        source: 来源（如 "mlflow_run" / "cam_validation" / "audit_log"）。
        recorded_at: 记录时间戳。
    """

    rule_id: str
    success: bool
    confidence: float
    source: str = "manual"
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def run_closed_loop(
    rule_ids: list[str] | None = None,
    apply: bool = True,
) -> list[ClosedLoopDecision]:
    """便捷函数：执行一次闭环迭代。

    Args:
        rule_ids: 指定评估的规则 ID 列表。None 表示评估所有。
        apply: 是否自动应用决策。

    Returns:
        决策结果列表。
    """
    from app.dreaming.closed_loop import ClosedLoop  # 延迟导入避免循环

    loop = ClosedLoop()
    return loop.run_closed_loop_iteration(rule_ids=rule_ids, apply=apply)


def record_rule_outcome(
    rule_id: str,
    success: bool,
    confidence: float,
    source: str = "manual",
) -> None:
    """便捷函数：记录规则触发结果。

    Args:
        rule_id: 规则 ID。
        success: 是否成功。
        confidence: 触发时的置信度。
        source: 来源标签。
    """
    from app.dreaming.closed_loop import ClosedLoop  # 延迟导入避免循环

    loop = ClosedLoop()
    loop.record_outcome(
        rule_id=rule_id,
        success=success,
        confidence=confidence,
        source=source,
    )


# 闭环默认参数（从 closed_loop 移入，mixin 与门面共用）
DEFAULT_ROUTER_CONFIDENCE_THRESHOLD = 0.7
DEFAULT_FUSION_MIN_CONFIDENCE = 0.3
DEFAULT_FUSION_CONFLICT_THRESHOLD = 0.8
DEFAULT_RULE_WINDOW_SIZE = 64  # 每条规则维护的滚动样本窗口
DEFAULT_PROMOTE_CONFIDENCE = 0.75  # 融合置信度 ≥ 该值 → 建议晋级
DEFAULT_DEMOTE_CONFIDENCE = 0.45  # 融合置信度 ≤ 该值 → 建议降级
DEFAULT_MAX_CONFLICT_FOR_PROMOTE = 0.25  # 冲突高于此值不晋级
DEFAULT_MIN_SAMPLES_FOR_DECISION = 5  # 样本数不足则 keep，避免噪声决策
DEFAULT_HRC52_CONFIDENCE_PENALTY = 0.5  # HRC52 pending_calibration 乘子
CLOSED_LOOP_STATE_DIR = "python/outputs/dreaming/closed_loop"
