"""回滚数据结构与常量（从 rollback_manager 拆出）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.dreaming.apply_rules import RollbackResult


@dataclass
class RollbackDecision:
    """回滚决策。

    Attributes:
        rule_id: 规则 ID。
        should_rollback: 是否应该回滚。
        reason: 回滚原因（若 should_rollback=True）。
        severity: 严重级别（hard_constraint / production_error / metrics_degradation）。
        detected_at: 检测时间戳。
        metrics_snapshot: 触发回滚的指标快照。
    """

    rule_id: str
    should_rollback: bool
    reason: str = ""
    severity: str = "none"  # hard_constraint | production_error | metrics_degradation | none
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RollbackExecutionResult:
    """回滚执行结果。

    Attributes:
        success: 回滚是否成功。
        rule_id: 规则 ID。
        previous_stage: 回滚前的灰度阶段。
        current_stage: 回滚后的灰度阶段。
        fully_deprecated: 是否完全废弃（DEPRECATED 状态）。
        rollback_result: RuleApplicator.rollback 的返回值（若调用了）。
        operated_at: 操作时间戳。
        reason: 回滚原因。
        error: 失败时的错误信息。
    """

    success: bool
    rule_id: str
    previous_stage: str = ""
    current_stage: str = ""
    fully_deprecated: bool = False
    rollback_result: RollbackResult | None = None
    operated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reason: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.rollback_result is not None:
            d["rollback_result"] = self.rollback_result.to_dict()
        return d


# 回滚管理器


# 回滚历史持久化目录
ROLLBACK_HISTORY_DIR = "python/outputs/dreaming/rollback_history"

# 默认冷却期（小时）：回滚后规则进入冷却，期间不可重新发布
DEFAULT_COOLDOWN_HOURS = 24

# 连续异常次数阈值：连续 N 次指标低于阈值触发回滚
DEFAULT_CONSECUTIVE_ANOMALY_THRESHOLD = 3

# 生产异常率阈值：超过此值立即回滚
DEFAULT_PRODUCTION_ERROR_RATE_THRESHOLD = 0.25

# 硬约束违反次数阈值：任意一次即触发回滚
DEFAULT_HARD_CONSTRAINT_VIOLATION_THRESHOLD = 1
