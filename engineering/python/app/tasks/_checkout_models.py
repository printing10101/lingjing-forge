"""任务签出模型与常量（从 task_checkout 拆分，D5）。

只包含数据结构定义；实现见 task_checkout.TaskCheckoutManager。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


from app.tasks.execution_lock import DEFAULT_LOCK_TIMEOUT_HOURS, ExecutionLock


class CheckoutStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class CheckoutFailureReason(str, Enum):
    ALREADY_CHECKED_OUT = "already_checked_out"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    ASSIGNED_TO_OTHER = "assigned_to_other"
    BUDGET_EXCEEDED = "budget_exceeded"
    GPU_UNAVAILABLE = "gpu_unavailable"
    AGENT_BUSY = "agent_busy"
    BLOCKERS_UNRESOLVED = "blockers_unresolved"
    LOCK_EXISTS = "lock_exists"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentMode(str, Enum):
    SINGLE = "single"
    BATCH = "batch"


class CheckoutPriority(int, Enum):
    CRITICAL = 1
    HIGH = 2
    NORMAL = 3
    LOW = 4


@dataclass
class CheckoutRequest:
    task_id: str
    agent_id: str
    agent_mode: AgentMode = AgentMode.SINGLE
    priority: CheckoutPriority = CheckoutPriority.NORMAL
    required_gpu_memory: float = 0.0
    timeout_hours: float = DEFAULT_LOCK_TIMEOUT_HOURS


@dataclass
class CheckoutResult:
    status: CheckoutStatus
    task_id: str
    agent_id: str
    message: str = ""
    failure_reason: CheckoutFailureReason | None = None
    retry_recommended: bool = False
    retry_delay_minutes: int = 0
    lock: ExecutionLock | None = None
    checked_out_at: str | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "message": self.message,
            "failure_reason": self.failure_reason.value if self.failure_reason else None,
            "retry_recommended": self.retry_recommended,
            "retry_delay_minutes": self.retry_delay_minutes,
            "checked_out_at": self.checked_out_at,
            "expires_at": self.expires_at,
            "lock": self.lock.to_dict() if self.lock else None,
        }


@dataclass
class CheckoutQueueEntry:
    task_id: str
    agent_id: str
    priority: CheckoutPriority
    created_at: float
    retry_count: int = 0
    last_failure: CheckoutFailureReason | None = None
    next_retry_at: float | None = None


@dataclass
class TaskRecord:
    id: str
    title: str = ""
    description: str = ""
    task_type: str = "execution"
    status: str = "pending"
    assigned_to: str | None = None
    parent_goal_id: str | None = None
    project_id: str | None = None
    required_gpu_memory: float = 0.0
    blockers: list[str] = field(default_factory=list)
    priority: int = 3
    checked_out_at: str | None = None
    checkout_expires_at: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    failure_history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "task_type": self.task_type,
            "status": self.status,
            "assigned_to": self.assigned_to,
            "parent_goal_id": self.parent_goal_id,
            "project_id": self.project_id,
            "required_gpu_memory": self.required_gpu_memory,
            "blockers": self.blockers,
            "priority": self.priority,
            "checked_out_at": self.checked_out_at,
            "checkout_expires_at": self.checkout_expires_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "failure_history": self.failure_history,
        }


# 签出重试配置常量（从 task_checkout 移入，mixin 与门面共用）
MAX_RETRY_COUNT = 5
BUDGET_RETRY_DELAY_MINUTES = 0
GPU_RETRY_DELAY_MINUTES = 5
CONFLICT_RETRY_DELAY_MINUTES = 1
