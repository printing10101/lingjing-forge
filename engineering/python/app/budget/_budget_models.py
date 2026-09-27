"""预算数据类（从 budget 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.budget import BudgetLevel, BudgetStatus, ResourceType


@dataclass
class BudgetLimit:
    """预算限制配置"""

    resource_type: ResourceType
    limit_value: float
    warning_threshold: float = 0.8
    hard_stop_threshold: float = 1.0
    budget_level: BudgetLevel = BudgetLevel.GLOBAL
    scope_id: str = "default"
    reset_interval: str = "daily"
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "limit_value": self.limit_value,
            "warning_threshold": self.warning_threshold,
            "hard_stop_threshold": self.hard_stop_threshold,
            "budget_level": self.budget_level.value,
            "scope_id": self.scope_id,
            "reset_interval": self.reset_interval,
        }


@dataclass
class BudgetUsage:
    """资源使用量"""

    resource_type: ResourceType
    current_usage: float
    limit: float
    usage_ratio: float
    status: BudgetStatus
    budget_level: BudgetLevel
    scope_id: str
    last_updated: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "current_usage": self.current_usage,
            "limit": self.limit,
            "usage_ratio": self.usage_ratio,
            "status": self.status.value,
            "budget_level": self.budget_level.value,
            "scope_id": self.scope_id,
            "last_updated": self.last_updated,
        }


@dataclass
class BudgetCheckResult:
    """预算检查结果"""

    passed: bool
    status: BudgetStatus
    usages: list[BudgetUsage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status.value,
            "usages": [u.to_dict() for u in self.usages],
            "warnings": self.warnings,
            "blocked_reasons": self.blocked_reasons,
        }
