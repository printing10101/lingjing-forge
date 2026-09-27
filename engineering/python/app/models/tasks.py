"""
Enhanced Task Model with Goal Alignment

Extends the existing task model with:
- goal_chain: Full chain from parent goal to mission
- blockers: Dependency management
- Strict task type and status enum control
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

from app.models.goals import GoalRef


class EnhancedTaskType(str, Enum):
    """Strict task type enumeration"""

    PREDICTION = "prediction"
    TRAINING = "training"
    ANALYSIS = "analysis"
    EXECUTION = "execution"
    REVIEW = "review"


class EnhancedTaskStatus(str, Enum):
    """Strict task lifecycle status"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class EnhancedTask:
    """Enhanced task model with goal alignment"""

    id: str
    title: str
    description: str
    task_type: EnhancedTaskType
    status: EnhancedTaskStatus = EnhancedTaskStatus.PENDING
    goal_chain: list[GoalRef] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    params: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None

    @staticmethod
    def validate_task_type(value: str) -> EnhancedTaskType:
        try:
            return EnhancedTaskType(value)
        except ValueError:
            valid = [t.value for t in EnhancedTaskType]
            raise ValueError(f"Invalid task_type '{value}'. Must be one of: {valid}")

    @staticmethod
    def validate_task_status(value: str) -> EnhancedTaskStatus:
        try:
            return EnhancedTaskStatus(value)
        except ValueError:
            valid = [s.value for s in EnhancedTaskStatus]
            raise ValueError(f"Invalid status '{value}'. Must be one of: {valid}")

    def can_transition_to(self, new_status: EnhancedTaskStatus) -> bool:
        transitions = {
            EnhancedTaskStatus.PENDING: {
                EnhancedTaskStatus.IN_PROGRESS,
                EnhancedTaskStatus.CANCELLED,
            },
            EnhancedTaskStatus.IN_PROGRESS: {
                EnhancedTaskStatus.COMPLETED,
                EnhancedTaskStatus.FAILED,
                EnhancedTaskStatus.CANCELLED,
            },
            EnhancedTaskStatus.COMPLETED: set(),
            EnhancedTaskStatus.FAILED: {EnhancedTaskStatus.PENDING},
            EnhancedTaskStatus.CANCELLED: set(),
        }
        return new_status in transitions.get(self.status, set())

    def are_blockers_resolved(self, completed_task_ids: set) -> bool:
        return all(b in completed_task_ids for b in self.blockers)

    def get_mission(self) -> GoalRef | None:
        if self.goal_chain:
            return self.goal_chain[-1]
        return None

    def get_parent_goal(self) -> GoalRef | None:
        if self.goal_chain:
            return self.goal_chain[0]
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["task_type"] = self.task_type.value
        d["status"] = self.status.value
        d["goal_chain"] = [gr.to_dict() for gr in self.goal_chain]
        if self.started_at and self.completed_at:
            d["duration_seconds"] = round(self.completed_at - self.started_at, 2)
        return d
