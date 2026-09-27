"""
Goal Alignment Data Models

Four-level goal hierarchy based on Paperclip's Goal Alignment design:
- Mission: Company mission (fixed)
- Strategic Goal: Core strategic objectives
- Project: Active projects
- Task: Execution units
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any


class GoalLevel(str, Enum):
    """Goal hierarchy level"""

    MISSION = "mission"
    STRATEGIC_GOAL = "strategic_goal"
    PROJECT = "project"
    TASK = "task"


class GoalStatus(str, Enum):
    """Goal lifecycle status"""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NEEDS_REVIEW = "needs_review"


@dataclass
class Goal:
    """Unified goal data model for all hierarchy levels"""

    id: str
    name: str
    description: str
    level: GoalLevel
    parent_id: str | None = None
    status: GoalStatus = GoalStatus.NOT_STARTED
    created_at: float | None = None
    completed_at: float | None = None
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "level": self.level.value,
            "parent_id": self.parent_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "version": self.version,
        }


@dataclass
class GoalRef:
    """Reference to a goal in the chain"""

    id: str
    level: GoalLevel
    name: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.level.value,
            "name": self.name,
            "description": self.description,
        }


@dataclass
class GoalVersion:
    """Version history record for goal changes"""

    id: int | None = None
    goal_id: str = ""
    version: int = 1
    changed_at: float | None = None
    changed_by: str = "system"
    change_type: str = ""
    field_name: str = ""
    old_value: str = ""
    new_value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "version": self.version,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
            "change_type": self.change_type,
            "field_name": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


@dataclass
class GoalProgress:
    """Computed progress for a goal based on child tasks"""

    goal_id: str = ""
    goal_name: str = ""
    level: GoalLevel = GoalLevel.PROJECT
    total_tasks: int = 0
    completed_tasks: int = 0
    in_progress_tasks: int = 0
    progress_percent: float = 0.0
    last_updated: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "goal_name": self.goal_name,
            "level": self.level.value,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "in_progress_tasks": self.in_progress_tasks,
            "progress_percent": round(self.progress_percent, 1),
            "last_updated": self.last_updated,
        }


DEFAULT_MISSION = Goal(
    id="mission-001",
    name="成为智能制造领域的领导者",
    description="致力于通过人工智能和先进制造技术的深度融合，成为中国智能制造领域的标杆企业，推动制造业数字化转型。",
    level=GoalLevel.MISSION,
    status=GoalStatus.IN_PROGRESS,
)

DEFAULT_STRATEGIC_GOAL = Goal(
    id="strategic-001",
    name="将45钢铣削表面粗糙度降低到Ra 0.8",
    description="通过优化加工工艺参数、改进刀具路径规划、引入智能预测模型等手段，将45号钢铣削加工的表面粗糙度从当前水平降低到Ra 0.8微米以下。",
    level=GoalLevel.STRATEGIC_GOAL,
    parent_id="mission-001",
    status=GoalStatus.IN_PROGRESS,
)

DEFAULT_PROJECT = Goal(
    id="proj-001",
    name="优化精加工切削参数",
    description="针对精加工工序，系统性地优化切削速度、进给量、切削深度等关键参数，结合LNN预测模型实现切削参数的智能推荐。",
    level=GoalLevel.PROJECT,
    parent_id="strategic-001",
    status=GoalStatus.IN_PROGRESS,
)

DEFAULT_GOALS: list[Goal] = [DEFAULT_MISSION, DEFAULT_STRATEGIC_GOAL, DEFAULT_PROJECT]
