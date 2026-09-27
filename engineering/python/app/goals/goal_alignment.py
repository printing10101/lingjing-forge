"""
Goal Alignment Verification System

Ensures all tasks are properly aligned with the goal hierarchy.
Features:
- Mandatory goal association on task creation
- Periodic alignment scanning (every 24 hours)
- Goal change propagation to child tasks
- Progress computation based on task completion
"""

from __future__ import annotations
import logging
import time
from typing import Any

from app.models.goals import GoalStatus, GoalProgress
from app.models.tasks import EnhancedTask, EnhancedTaskStatus
from app.dependencies import get_goal_chain_store
from app.goals.goal_chain_store import GoalChainStore
from app.context.context_builder import ContextBuilder

logger = logging.getLogger(__name__)


class GoalAlignmentError(Exception):
    """Raised when a task fails goal alignment checks"""

    pass


class GoalAlignmentChecker:
    """Validates and maintains goal alignment for all tasks"""

    def __init__(self, store: GoalChainStore | None = None):
        self._store = store or get_goal_chain_store()
        self._context_builder = ContextBuilder()
        self._last_scan_at: float | None = None
        self._scan_interval_seconds = 86400
        self._task_status_map: dict[str, str] = {}
        self._task_map: dict[str, EnhancedTask] = {}

    def validate_task_goal_chain(self, task: EnhancedTask) -> bool:
        if not task.goal_chain:
            raise GoalAlignmentError(
                f"Task '{task.id}' has no goal chain. All tasks must be associated with at least one parent goal."
            )

        for ref in task.goal_chain:
            goal = self._store.get_goal(ref.id)
            if goal is None:
                raise GoalAlignmentError(f"Goal '{ref.id}' in task '{task.id}' chain does not exist.")
            if goal.status == GoalStatus.CANCELLED:
                raise GoalAlignmentError(f"Goal '{ref.name}' ({ref.id}) in task '{task.id}' chain is cancelled.")

        return True

    def register_task(self, task: EnhancedTask):
        self._task_map[task.id] = task
        self._task_status_map[task.id] = task.status.value

    def update_task_status(self, task_id: str, new_status: EnhancedTaskStatus):
        if task_id in self._task_map:
            self._task_map[task_id].status = new_status
        self._task_status_map[task_id] = new_status.value

    def run_alignment_scan(self) -> dict[str, Any]:
        self._last_scan_at = time.time()
        issues: list[dict[str, Any]] = []

        for task_id, task in list(self._task_map.items()):
            if task.status == EnhancedTaskStatus.IN_PROGRESS:
                try:
                    self.validate_task_goal_chain(task)
                except GoalAlignmentError as e:
                    issues.append(
                        {
                            "task_id": task_id,
                            "task_title": task.title,
                            "issue": str(e),
                            "severity": "high",
                        }
                    )

        result = {
            "scan_time": self._last_scan_at,
            "tasks_checked": len(self._task_map),
            "issues_found": len(issues),
            "issues": issues,
        }

        logger.info(f"Goal alignment scan completed: {len(issues)} issues found in {len(self._task_map)} tasks")
        return result

    def should_scan(self) -> bool:
        if self._last_scan_at is None:
            return True
        return (time.time() - self._last_scan_at) >= self._scan_interval_seconds

    def propagate_goal_change(self, changed_goal_id: str) -> dict[str, Any]:
        affected_task_ids = self._store.propagate_cancellation(changed_goal_id)

        updated_tasks = []
        for task_id in affected_task_ids:
            if task_id in self._task_map:
                task = self._task_map[task_id]
                old_status = task.status
                task.status = EnhancedTaskStatus.PENDING
                updated_tasks.append(
                    {
                        "task_id": task_id,
                        "old_status": old_status.value,
                        "new_status": task.status.value,
                    }
                )

        for task in self._task_map.values():
            if task.status != EnhancedTaskStatus.IN_PROGRESS:
                continue
            chain_ids = {ref.id for ref in task.goal_chain}
            if changed_goal_id in chain_ids:
                self._refresh_goal_chain(task)

        result = {
            "changed_goal_id": changed_goal_id,
            "tasks_marked_for_review": len(affected_task_ids),
            "tasks_chains_refreshed": len(updated_tasks),
            "affected_task_details": updated_tasks,
        }

        logger.info(
            f"Goal change propagated: {len(affected_task_ids)} tasks marked for review, "
            f"{len(updated_tasks)} chains refreshed for goal '{changed_goal_id}'"
        )
        return result

    def compute_goal_progress(self, goal_id: str) -> GoalProgress:
        return self._store.compute_progress(goal_id, self._task_status_map)

    def compute_all_progress(self) -> list[GoalProgress]:
        all_goals = self._store.get_all_goals()
        results = []
        for goal in all_goals:
            progress = self.compute_goal_progress(goal.id)
            results.append(progress)
        return results

    def build_task_context(self, task: EnhancedTask) -> dict[str, Any]:
        return self._context_builder.build_context(task)

    def _refresh_goal_chain(self, task: EnhancedTask):
        if task.goal_chain:
            first_ref = task.goal_chain[0]
            new_chain = self._store.resolve_goal_chain(first_ref.id)
            task.goal_chain = new_chain

    def get_alignment_summary(self) -> dict[str, Any]:
        total_tasks = len(self._task_map)
        aligned_tasks = 0
        unaligned_tasks = 0
        alignment_issues = []

        for task_id, task in self._task_map.items():
            if task.goal_chain:
                aligned_tasks += 1
            else:
                unaligned_tasks += 1
                alignment_issues.append(
                    {
                        "task_id": task_id,
                        "task_title": task.title,
                        "issue": "No goal chain associated",
                    }
                )

        return {
            "total_tasks": total_tasks,
            "aligned_tasks": aligned_tasks,
            "unaligned_tasks": unaligned_tasks,
            "alignment_rate": round((aligned_tasks / total_tasks * 100) if total_tasks > 0 else 0.0, 1),
            "issues": alignment_issues,
        }

    def set_scan_interval(self, seconds: int):
        self._scan_interval_seconds = seconds
