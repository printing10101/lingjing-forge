"""
Governance & Approval Strategy Configuration Models

Four configurable approval strategies with multi-dimensional policy configuration:
- AUTO_EXECUTE: Direct execution without approval
- EXECUTE_AFTER_RECORD: Execute first, record for later review
- APPROVE_BEFORE_EXECUTE: Must be approved before execution
- MULTI_APPROVAL: Requires multiple approvers (sequential or parallel)

Multi-dimensional policy: Global → TaskType → AgentRole → ResourceSensitivity
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ApprovalStrategy(str, Enum):
    """审批策略类型"""

    AUTO_EXECUTE = "auto_execute"
    EXECUTE_AFTER_RECORD = "execute_after_record"
    APPROVE_BEFORE_EXECUTE = "approve_before_execute"
    MULTI_APPROVAL = "multi_approval"


class TaskType(str, Enum):
    """任务类型"""

    TRAINING = "training"
    EXECUTION = "execution"
    ANALYSIS = "analysis"


class AgentRole(str, Enum):
    """代理角色"""

    ENGINEER = "engineer"
    ANALYST = "analyst"
    OPERATOR = "operator"


class ResourceSensitivity(str, Enum):
    """资源敏感度级别"""

    NORMAL = "normal"
    CONFIDENTIAL = "confidential"
    CORE = "core"


class ApprovalStatus(str, Enum):
    """审批状态"""

    PENDING = "pending"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    EMERGENCY = "emergency"


class ApprovalPriority(str, Enum):
    """审批优先级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalMode(str, Enum):
    """多人审批模式"""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass
class ApprovalDecision:
    """审批决策记录"""

    approver_id: str
    decision: str  # approved/rejected/request_info/escalated
    comment: str = ""
    decided_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "approver_id": self.approver_id,
            "decision": self.decision,
            "comment": self.comment,
            "decided_at": self.decided_at,
        }


@dataclass
class ApprovalPolicy:
    """审批策略配置"""

    id: str | None = None
    dimension: str = "global"  # global/task_type/agent_role/resource_sensitivity
    dimension_value: str = "default"
    strategy: ApprovalStrategy = ApprovalStrategy.AUTO_EXECUTE
    priority: ApprovalPriority = ApprovalPriority.MEDIUM

    multi_approval_mode: ApprovalMode = ApprovalMode.PARALLEL
    required_approvals: int = 1
    approval_timeout_hours: float = 24.0
    auto_escalate_on_timeout: bool = True
    auto_reject_on_timeout: bool = False

    enabled: bool = True
    created_at: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dimension": self.dimension,
            "dimension_value": self.dimension_value,
            "strategy": self.strategy.value,
            "priority": self.priority.value,
            "multi_approval_mode": self.multi_approval_mode.value,
            "required_approvals": self.required_approvals,
            "approval_timeout_hours": self.approval_timeout_hours,
            "auto_escalate_on_timeout": self.auto_escalate_on_timeout,
            "auto_reject_on_timeout": self.auto_reject_on_timeout,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class ApprovalRequest:
    """审批请求"""

    request_id: str
    task_id: str
    requester: str
    requested_at: float
    priority: ApprovalPriority = ApprovalPriority.MEDIUM
    context: dict[str, Any] = field(default_factory=dict)

    status: ApprovalStatus = ApprovalStatus.PENDING
    assigned_approver: str | None = None
    approvers: list[str] = field(default_factory=list)
    decisions: list[ApprovalDecision] = field(default_factory=list)
    required_approvals: int = 1

    risk_score: float = 0.0
    risk_factors: list[str] = field(default_factory=list)
    suggested_decision: str = ""

    escalated_from: str | None = None
    escalated_at: float | None = None
    emergency_override: bool = False
    emergency_reason: str = ""

    expires_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "task_id": self.task_id,
            "requester": self.requester,
            "requested_at": self.requested_at,
            "priority": self.priority.value,
            "context": self.context,
            "status": self.status.value,
            "assigned_approver": self.assigned_approver,
            "approvers": self.approvers,
            "decisions": [d.to_dict() for d in self.decisions],
            "required_approvals": self.required_approvals,
            "risk_score": round(self.risk_score, 2),
            "risk_factors": self.risk_factors,
            "suggested_decision": self.suggested_decision,
            "escalated_from": self.escalated_from,
            "escalated_at": self.escalated_at,
            "emergency_override": self.emergency_override,
            "emergency_reason": self.emergency_reason,
            "expires_at": self.expires_at,
            "completed_at": self.completed_at,
        }


@dataclass
class ApprovalDelegation:
    """审批委托记录"""

    id: str | None = None
    delegator_id: str = ""
    delegate_id: str = ""
    start_time: float | None = None
    end_time: float | None = None
    reason: str = ""
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "delegator_id": self.delegator_id,
            "delegate_id": self.delegate_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass
class EmergencyOperation:
    """紧急操作记录"""

    id: str | None = None
    request_id: str = ""
    task_id: str = ""
    operator_id: str = ""
    reason: str = ""
    emergency_type: str = ""
    executed_at: float | None = None
    retroactive_approval_required: bool = True
    retroactive_approval_completed: bool = False
    created_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "operator_id": self.operator_id,
            "reason": self.reason,
            "emergency_type": self.emergency_type,
            "executed_at": self.executed_at,
            "retroactive_approval_required": self.retroactive_approval_required,
            "retroactive_approval_completed": self.retroactive_approval_completed,
            "created_at": self.created_at,
        }


@dataclass
class GovernanceReport:
    """治理报告"""

    report_id: str = ""
    period_start: float | None = None
    period_end: float | None = None
    generated_at: float | None = None

    total_requests: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    escalated_count: int = 0
    emergency_count: int = 0

    avg_approval_time_hours: float = 0.0
    rejection_rate: float = 0.0
    escalation_rate: float = 0.0

    risk_trend: list[dict[str, Any]] = field(default_factory=list)
    top_risk_operations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "generated_at": self.generated_at,
            "total_requests": self.total_requests,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "escalated_count": self.escalated_count,
            "emergency_count": self.emergency_count,
            "avg_approval_time_hours": round(self.avg_approval_time_hours, 2),
            "rejection_rate": round(self.rejection_rate, 4),
            "escalation_rate": round(self.escalation_rate, 4),
            "risk_trend": self.risk_trend,
            "top_risk_operations": self.top_risk_operations,
        }


DEFAULT_GLOBAL_POLICIES: list[ApprovalPolicy] = [
    ApprovalPolicy(
        dimension="global",
        dimension_value="default",
        strategy=ApprovalStrategy.EXECUTE_AFTER_RECORD,
        priority=ApprovalPriority.MEDIUM,
        approval_timeout_hours=24.0,
    ),
    ApprovalPolicy(
        dimension="task_type",
        dimension_value=TaskType.TRAINING.value,
        strategy=ApprovalStrategy.APPROVE_BEFORE_EXECUTE,
        priority=ApprovalPriority.HIGH,
        required_approvals=1,
        approval_timeout_hours=24.0,
    ),
    ApprovalPolicy(
        dimension="task_type",
        dimension_value=TaskType.EXECUTION.value,
        strategy=ApprovalStrategy.MULTI_APPROVAL,
        priority=ApprovalPriority.CRITICAL,
        required_approvals=2,
        multi_approval_mode=ApprovalMode.SEQUENTIAL,
        approval_timeout_hours=12.0,
    ),
    ApprovalPolicy(
        dimension="task_type",
        dimension_value=TaskType.ANALYSIS.value,
        strategy=ApprovalStrategy.AUTO_EXECUTE,
        priority=ApprovalPriority.LOW,
    ),
    ApprovalPolicy(
        dimension="agent_role",
        dimension_value=AgentRole.ENGINEER.value,
        strategy=ApprovalStrategy.EXECUTE_AFTER_RECORD,
        priority=ApprovalPriority.MEDIUM,
    ),
    ApprovalPolicy(
        dimension="agent_role",
        dimension_value=AgentRole.ANALYST.value,
        strategy=ApprovalStrategy.AUTO_EXECUTE,
        priority=ApprovalPriority.LOW,
    ),
    ApprovalPolicy(
        dimension="agent_role",
        dimension_value=AgentRole.OPERATOR.value,
        strategy=ApprovalStrategy.APPROVE_BEFORE_EXECUTE,
        priority=ApprovalPriority.HIGH,
    ),
    ApprovalPolicy(
        dimension="resource_sensitivity",
        dimension_value=ResourceSensitivity.NORMAL.value,
        strategy=ApprovalStrategy.AUTO_EXECUTE,
        priority=ApprovalPriority.LOW,
    ),
    ApprovalPolicy(
        dimension="resource_sensitivity",
        dimension_value=ResourceSensitivity.CONFIDENTIAL.value,
        strategy=ApprovalStrategy.APPROVE_BEFORE_EXECUTE,
        priority=ApprovalPriority.HIGH,
    ),
    ApprovalPolicy(
        dimension="resource_sensitivity",
        dimension_value=ResourceSensitivity.CORE.value,
        strategy=ApprovalStrategy.MULTI_APPROVAL,
        priority=ApprovalPriority.CRITICAL,
        required_approvals=3,
        multi_approval_mode=ApprovalMode.SEQUENTIAL,
        approval_timeout_hours=8.0,
    ),
]


STRATEGY_PRIORITY_MAP = {
    ApprovalStrategy.AUTO_EXECUTE: 0,
    ApprovalStrategy.EXECUTE_AFTER_RECORD: 1,
    ApprovalStrategy.APPROVE_BEFORE_EXECUTE: 2,
    ApprovalStrategy.MULTI_APPROVAL: 3,
}

DIMENSION_PRIORITY = {
    "resource_sensitivity": 4,
    "task_type": 3,
    "agent_role": 2,
    "global": 1,
}
