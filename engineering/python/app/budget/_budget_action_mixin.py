"""执行联动（取消任务/挂起代理/日志）方法组。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from collections.abc import Callable
from app.budget.models import EnforcementResult
from app.models.budget import (
    BudgetLevel,
    ResourceType,
)


logger = logging.getLogger(__name__)


class _BudgetActionMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _task_canceller: Any
    _agent_suspender: Any
    _conn: Any
    _cost_tracker_ref: Any

    def set_task_canceller(self, canceller: Callable[[str], None]) -> None:
        self._task_canceller = canceller

    def set_agent_suspender(self, suspender: Callable[[str, str], None]) -> None:
        self._agent_suspender = suspender

    def set_cost_tracker(self, cost_tracker) -> None:
        self._cost_tracker_ref = cost_tracker

    def _cancel_pending_tasks(self, level: BudgetLevel, scope_id: str, resource_type: ResourceType) -> list[str]:
        cancelled = []
        if self._task_canceller is not None:
            try:
                if level == BudgetLevel.AGENT:
                    self._task_canceller(scope_id)
                    cancelled.append(scope_id)
                elif level == BudgetLevel.GLOBAL:
                    self._task_canceller("global")
                    cancelled.append("global")
            except (RuntimeError, ValueError, OSError):
                logger.error("Task cancellation error", exc_info=True)
        return cancelled

    def _log_enforcement(
        self,
        result: EnforcementResult,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
    ) -> None:
        details = json.dumps(
            {
                "actions": [a.value for a in result.actions_taken],
                "cancelled_tasks": result.cancelled_tasks,
                "suspended_agents": result.suspended_agents,
                "notifications_sent": result.notifications_sent,
            }
        )
        self._conn.execute(
            """INSERT INTO enforcement_log
               (action, level, scope_id, resource_type, details, executed_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                ",".join(a.value for a in result.actions_taken),
                level.value,
                scope_id,
                resource_type.value,
                details,
                time.time(),
            ),
        )
        self._conn.commit()

    def get_enforcement_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM enforcement_log ORDER BY executed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_reset_log(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM budget_reset_log ORDER BY reset_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
