"""预算执行相关数据模型.

从原 ``app/budget/budget_enforcer.py`` 拆分而来，集中存放预算强制执行器
所需的本地数据类与枚举。``BudgetPolicy`` / ``BudgetAlert`` /
``BudgetCheckResult`` / ``CostOptimizationSuggestion`` 等业务模型仍由
``app.models.budget`` 提供，本模块只补充执行器专属类型。
"""

from dataclasses import dataclass, field
from enum import Enum


from app.models.budget import BudgetAlert, BudgetCheckResult


class EnforcementAction(str, Enum):
    """强制执行动作"""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    CANCEL_PENDING = "cancel_pending"
    SUSPEND_AGENT = "suspend_agent"
    NOTIFY_ADMIN = "notify_admin"


@dataclass
class EnforcementResult:
    """强制执行结果"""

    actions_taken: list[EnforcementAction] = field(default_factory=list)
    check_result: BudgetCheckResult | None = None
    alerts_generated: list[BudgetAlert] = field(default_factory=list)
    cancelled_tasks: list[str] = field(default_factory=list)
    suspended_agents: list[str] = field(default_factory=list)
    notifications_sent: bool = False


__all__ = [
    "EnforcementAction",
    "EnforcementResult",
]
