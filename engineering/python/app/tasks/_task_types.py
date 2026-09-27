"""任务系统共享类型与常量（从 task_system 拆出，供 mixin 与门面共用）。"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.database.models import TrainingTask
from app.tasks.task_manager import TaskStatus, TaskType


DEFAULT_TASK_TIMEOUT_SECONDS = 3600

DEFAULT_MAX_RETRIES = 3

VALID_STATUS_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
}

RETRYABLE_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)


@dataclass
class TaskRecord:
    job_id: str
    task_type: TaskType
    status: TaskStatus
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    params: dict[str, Any] | None = None
    owner_id: str | None = None
    idempotency_key: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        d["task_type"] = self.task_type.value
        d["created_at_iso"] = datetime.fromtimestamp(self.created_at).isoformat()
        if self.started_at:
            d["started_at_iso"] = datetime.fromtimestamp(self.started_at).isoformat()
        if self.completed_at:
            d["completed_at_iso"] = datetime.fromtimestamp(self.completed_at).isoformat()
        if self.started_at and self.completed_at:
            d["duration_seconds"] = round(self.completed_at - self.started_at, 2)
        return d

    @classmethod
    def from_db_model(cls, model: TrainingTask) -> "TaskRecord":
        return cls(
            job_id=str(model.id),
            task_type=TaskType(str(model.task_type)) if model.task_type else TaskType.UNKNOWN,
            status=TaskStatus(str(model.status)) if model.status else TaskStatus.PENDING,
            progress=float(model.progress or 0),
            result=dict(model.result) if model.result else None,
            error=str(model.error) if model.error else None,
            params=dict(model.params) if model.params else None,
            owner_id=str(model.owner_id) if model.owner_id else None,
            idempotency_key=str(model.idempotency_key) if model.idempotency_key else None,
            created_at=model.created_at.timestamp() if model.created_at else time.time(),
            started_at=model.started_at.timestamp() if model.started_at else None,
            completed_at=model.completed_at.timestamp() if model.completed_at else None,
        )
