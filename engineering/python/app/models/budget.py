"""
Budget Strategy Configuration Models

Hierarchical budget configuration: Global → Project → Agent → Task
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BudgetLevel(str, Enum):
    """预算层级"""

    GLOBAL = "global"
    PROJECT = "project"
    AGENT = "agent"
    TASK = "task"


class BudgetPeriod(str, Enum):
    """预算周期"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class BudgetStatus(str, Enum):
    """预算状态"""

    OK = "ok"
    WARNING = "warning"
    EXCEEDED = "exceeded"
    DISABLED = "disabled"


class ResourceType(str, Enum):
    """资源类型"""

    GPU_MEMORY = "gpu_memory"
    GPU_HOURS = "gpu_hours"
    GPU_TIME = "gpu_time"
    INFERENCE_COUNT = "inference_count"
    MEMORY_PEAK = "memory_peak"
    API_CALLS = "api_calls"
    DATA_TRANSFER = "data_transfer"
    TOTAL_COST = "total_cost"


@dataclass
class BudgetPolicy:
    """预算策略配置"""

    level: BudgetLevel = BudgetLevel.GLOBAL
    scope_id: str = "default"
    resource_type: ResourceType = ResourceType.TOTAL_COST

    limit: float = 100.0
    period: BudgetPeriod = BudgetPeriod.DAILY
    warning_threshold: float = 0.8
    hard_stop: bool = True
    auto_notify: bool = True

    enabled: bool = True
    current_usage: float = 0.0
    last_reset_at: float | None = None
    created_at: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "scope_id": self.scope_id,
            "resource_type": self.resource_type.value,
            "limit": self.limit,
            "period": self.period.value,
            "warning_threshold": self.warning_threshold,
            "hard_stop": self.hard_stop,
            "auto_notify": self.auto_notify,
            "enabled": self.enabled,
            "current_usage": self.current_usage,
            "last_reset_at": self.last_reset_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def usage_ratio(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(self.current_usage / self.limit, 1.0)

    @property
    def remaining(self) -> float:
        return max(self.limit - self.current_usage, 0.0)

    @property
    def status(self) -> BudgetStatus:
        if not self.enabled:
            return BudgetStatus.DISABLED
        if self.usage_ratio >= 1.0:
            return BudgetStatus.EXCEEDED
        if self.usage_ratio >= self.warning_threshold:
            return BudgetStatus.WARNING
        return BudgetStatus.OK


@dataclass
class BudgetCheckResult:
    """预算检查结果"""

    passed: bool = True
    policy: BudgetPolicy | None = None
    status: BudgetStatus = BudgetStatus.OK
    usage_ratio: float = 0.0
    remaining: float = 0.0
    limit: float = 0.0
    block_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    checked_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "policy": self.policy.to_dict() if self.policy else None,
            "status": self.status.value,
            "usage_ratio": round(self.usage_ratio, 4),
            "remaining": round(self.remaining, 4),
            "limit": round(self.limit, 4),
            "block_reason": self.block_reason,
            "warnings": self.warnings,
            "checked_at": self.checked_at,
        }


@dataclass
class BudgetAdjustment:
    """预算调整记录"""

    id: int | None = None
    level: BudgetLevel = BudgetLevel.GLOBAL
    scope_id: str = "default"
    resource_type: ResourceType = ResourceType.TOTAL_COST
    old_limit: float = 0.0
    new_limit: float = 0.0
    reason: str = ""
    adjusted_by: str = "admin"
    adjusted_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.value,
            "scope_id": self.scope_id,
            "resource_type": self.resource_type.value,
            "old_limit": self.old_limit,
            "new_limit": self.new_limit,
            "reason": self.reason,
            "adjusted_by": self.adjusted_by,
            "adjusted_at": self.adjusted_at,
        }


@dataclass
class BudgetAlert:
    """预算告警"""

    id: int | None = None
    level: BudgetLevel = BudgetLevel.GLOBAL
    scope_id: str = "default"
    resource_type: ResourceType = ResourceType.TOTAL_COST
    status: BudgetStatus = BudgetStatus.WARNING
    current_usage: float = 0.0
    limit: float = 0.0
    usage_ratio: float = 0.0
    message: str = ""
    is_read: bool = False
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level.value,
            "scope_id": self.scope_id,
            "resource_type": self.resource_type.value,
            "status": self.status.value,
            "current_usage": round(self.current_usage, 4),
            "limit": round(self.limit, 4),
            "usage_ratio": round(self.usage_ratio, 4),
            "message": self.message,
            "is_read": self.is_read,
            "created_at": self.created_at,
        }


@dataclass
class CostOptimizationSuggestion:
    """成本优化建议"""

    suggestion_id: str = ""
    category: str = ""
    title: str = ""
    description: str = ""
    current_cost: float = 0.0
    estimated_savings: float = 0.0
    savings_percentage: float = 0.0
    priority: str = "medium"
    recommendation: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    generated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "category": self.category,
            "title": self.title,
            "description": self.description,
            "current_cost": round(self.current_cost, 6),
            "estimated_savings": round(self.estimated_savings, 6),
            "savings_percentage": round(self.savings_percentage, 2),
            "priority": self.priority,
            "recommendation": self.recommendation,
            "metrics": self.metrics,
            "generated_at": self.generated_at,
        }


DEFAULT_GLOBAL_BUDGETS: list[BudgetPolicy] = [
    BudgetPolicy(
        level=BudgetLevel.GLOBAL,
        scope_id="default",
        resource_type=ResourceType.TOTAL_COST,
        limit=1000.0,
        period=BudgetPeriod.DAILY,
        warning_threshold=0.8,
        hard_stop=True,
        auto_notify=True,
    ),
    BudgetPolicy(
        level=BudgetLevel.GLOBAL,
        scope_id="default",
        resource_type=ResourceType.GPU_HOURS,
        limit=24.0,
        period=BudgetPeriod.DAILY,
        warning_threshold=0.8,
        hard_stop=True,
        auto_notify=True,
    ),
    BudgetPolicy(
        level=BudgetLevel.GLOBAL,
        scope_id="default",
        resource_type=ResourceType.API_CALLS,
        limit=50000.0,
        period=BudgetPeriod.DAILY,
        warning_threshold=0.8,
        hard_stop=False,
        auto_notify=True,
    ),
    BudgetPolicy(
        level=BudgetLevel.GLOBAL,
        scope_id="default",
        resource_type=ResourceType.INFERENCE_COUNT,
        limit=10000.0,
        period=BudgetPeriod.DAILY,
        warning_threshold=0.8,
        hard_stop=False,
        auto_notify=True,
    ),
    BudgetPolicy(
        level=BudgetLevel.GLOBAL,
        scope_id="default",
        resource_type=ResourceType.GPU_MEMORY,
        limit=16384.0,
        period=BudgetPeriod.DAILY,
        warning_threshold=0.8,
        hard_stop=False,
        auto_notify=True,
    ),
]
