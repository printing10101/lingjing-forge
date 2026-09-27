"""
Task Manager Module

Manages task lifecycle, status tracking, and task type definitions.
"""

from enum import Enum
from typing import Any
from dataclasses import dataclass

# TaskType 已提升到 contracts/task.py 以避免 models tasks 循环依赖
# 此处保留 re-import 以兼容历史代码
from app.contracts.task import TaskType

__all__ = ["TaskType", "TaskStatus", "TaskResult"]


class TaskStatus(str, Enum):
    """Task lifecycle status"""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskResult:
    """Standardized task result container"""

    job_id: str
    status: TaskStatus
    result_data: dict[str, Any] | None = None
    error_message: str | None = None
    metrics: dict[str, Any] | None = None
