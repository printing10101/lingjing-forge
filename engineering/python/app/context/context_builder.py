"""
Context Builder with Goal Chain Injection

Builds task execution context that automatically includes
goal chain information so AI agents understand the full
alignment context of every task they execute.
"""

from __future__ import annotations
from typing import Any

from app.models.tasks import EnhancedTask
from app.models.goals import GoalLevel


CONTEXT_TEMPLATE = """当前任务：{task_title}
任务描述：{task_description}
所属项目：{project_name}
战略目标：{strategic_goal_name}
公司使命：{mission_name}

任务重要性说明：
{importance_explanation}"""


class ContextBuilder:
    """Builds execution context with goal alignment information"""

    def build_context(self, task: EnhancedTask) -> dict[str, Any]:
        parent_goal = task.get_parent_goal()
        mission = task.get_mission()

        project_name = ""
        strategic_goal_name = ""
        mission_name = ""

        for ref in task.goal_chain:
            if ref.level == GoalLevel.PROJECT:
                project_name = ref.name
            elif ref.level == GoalLevel.STRATEGIC_GOAL:
                strategic_goal_name = ref.name
            elif ref.level == GoalLevel.MISSION:
                mission_name = ref.name

        if parent_goal is None:
            parent_goal_name = "未关联目标"
            parent_goal_desc = ""
        else:
            parent_goal_name = parent_goal.name
            parent_goal_desc = parent_goal.description

        importance = self._generate_importance_explanation(
            task_title=task.title,
            task_description=task.description,
            parent_goal_name=parent_goal_name,
            parent_goal_desc=parent_goal_desc,
            project_name=project_name,
            strategic_goal_name=strategic_goal_name,
            mission_name=mission_name,
        )

        formatted_context = CONTEXT_TEMPLATE.format(
            task_title=task.title,
            task_description=task.description,
            project_name=project_name or "未指定",
            strategic_goal_name=strategic_goal_name or "未指定",
            mission_name=mission_name or "未指定",
            importance_explanation=importance,
        )

        return {
            "task_title": task.title,
            "task_description": task.description,
            "task_type": task.task_type.value,
            "task_id": task.id,
            "parent_goal": {
                "name": parent_goal_name,
                "description": parent_goal_desc,
                "id": parent_goal.id if parent_goal else None,
                "type": parent_goal.level.value if parent_goal else None,
            },
            "final_mission": {
                "name": mission_name,
                "description": mission.description if mission else "",
                "id": mission.id if mission else None,
            },
            "goal_chain": [gr.to_dict() for gr in task.goal_chain],
            "formatted_context": formatted_context,
            "importance_explanation": importance,
        }

    def _generate_importance_explanation(
        self,
        task_title: str,
        task_description: str,
        parent_goal_name: str,
        parent_goal_desc: str,
        project_name: str,
        strategic_goal_name: str,
        mission_name: str,
    ) -> str:
        parts = []

        if project_name:
            parts.append(f"本任务「{task_title}」属于项目「{project_name}」的一部分。")
            if parent_goal_desc:
                parts.append(f"该项目旨在{parent_goal_desc}。")

        if strategic_goal_name:
            parts.append(f"通过完成此任务，将直接支持战略目标「{strategic_goal_name}」的实现。")

        if mission_name:
            parts.append(f"最终服务于公司使命：「{mission_name}」。")

        if not parts:
            parts.append(f"本任务「{task_title}」暂无关联目标。建议将任务关联到具体项目以确保目标对齐。")

        return " ".join(parts)

    def build_minimal_context(self, task: EnhancedTask) -> dict[str, Any]:
        return {
            "task_title": task.title,
            "task_description": task.description,
            "task_id": task.id,
            "task_type": task.task_type.value,
            "goal_chain_summary": [{"name": g.name, "type": g.level.value} for g in task.goal_chain],
        }
