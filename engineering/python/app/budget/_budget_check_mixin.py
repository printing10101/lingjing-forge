"""预算检查 mixin（从 budget 拆出）。"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from app.budget._budget_models import BudgetCheckResult, BudgetLimit, BudgetUsage
from app.models.budget import BudgetLevel, BudgetStatus, ResourceType

logger = logging.getLogger(__name__)


class _BudgetCheckMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any
    _lock: Any
    tracker: Any

    def check_budget(self, agent_id: str, resource_types: list[ResourceType] | None = None) -> BudgetCheckResult:
        """
        执行预算检查

        Args:
            agent_id: 代理ID
            resource_types: 要检查的资源类型列表（默认检查所有）

        Returns:
            预算检查结果
        """
        self.tracker.reset_daily()
        self.tracker._update_current_metrics()

        if resource_types is None:
            resource_types = list(ResourceType)

        usages = []
        warnings = []
        blocked_reasons = []
        overall_status = BudgetStatus.OK
        passed = True

        for res_type in resource_types:
            current_usage = self.tracker.get_current_usage(res_type)
            budget_limit = self._get_budget_limit(res_type, agent_id)

            if budget_limit is None:
                continue

            usage_ratio = current_usage / budget_limit.limit_value if budget_limit.limit_value > 0 else 0.0

            if usage_ratio >= budget_limit.hard_stop_threshold:
                status = BudgetStatus.EXCEEDED
                passed = False
                blocked_reasons.append(
                    f"Resource {res_type.value} exceeded hard stop threshold: "
                    f"{current_usage:.2f}/{budget_limit.limit_value:.2f} ({usage_ratio * 100:.1f}%)"
                )
                overall_status = BudgetStatus.EXCEEDED

                self._record_notification(
                    agent_id,
                    "hard_stop",
                    blocked_reasons[-1],
                    res_type.value,
                    usage_ratio,
                )
            elif usage_ratio >= budget_limit.warning_threshold:
                status = BudgetStatus.WARNING
                warnings.append(
                    f"Resource {res_type.value} approaching limit: "
                    f"{current_usage:.2f}/{budget_limit.limit_value:.2f} ({usage_ratio * 100:.1f}%)"
                )
                if overall_status == BudgetStatus.OK:
                    overall_status = BudgetStatus.WARNING

                self._record_notification(agent_id, "warning", warnings[-1], res_type.value, usage_ratio)
            else:
                status = BudgetStatus.OK

            usage = BudgetUsage(
                resource_type=res_type,
                current_usage=current_usage,
                limit=budget_limit.limit_value,
                usage_ratio=usage_ratio,
                status=status,
                budget_level=budget_limit.budget_level,
                scope_id=budget_limit.scope_id,
                last_updated=time.time(),
            )
            usages.append(usage)

            self._log_usage(
                agent_id,
                res_type,
                current_usage,
                budget_limit.limit_value,
                usage_ratio,
                status,
            )

        if warnings:
            logger.warning("Budget warnings for agent %s: %s", agent_id, "; ".join(warnings))

        if blocked_reasons:
            logger.error("Budget exceeded for agent %s: %s", agent_id, "; ".join(blocked_reasons))

        return BudgetCheckResult(
            passed=passed,
            status=overall_status,
            usages=usages,
            warnings=warnings,
            blocked_reasons=blocked_reasons,
        )

    def _get_budget_limit(self, resource_type: ResourceType, agent_id: str) -> BudgetLimit | None:
        """获取预算限制（按代理级、项目级、全局级优先级）"""
        with self._lock:
            for level, scope in [
                (BudgetLevel.AGENT.value, agent_id),
                (BudgetLevel.PROJECT.value, "default"),
                (BudgetLevel.GLOBAL.value, "default"),
            ]:
                row = self._conn.execute(
                    """SELECT * FROM budget_limits
                       WHERE resource_type = ? AND budget_level = ? AND scope_id = ?""",
                    (resource_type.value, level, scope),
                ).fetchone()

                if row:
                    return BudgetLimit(
                        resource_type=resource_type,
                        limit_value=row["limit_value"],
                        warning_threshold=row["warning_threshold"],
                        hard_stop_threshold=row["hard_stop_threshold"],
                        budget_level=BudgetLevel(row["budget_level"]),
                        scope_id=row["scope_id"],
                        reset_interval=row["reset_interval"],
                    )

            return None

    def _log_usage(
        self,
        agent_id: str,
        resource_type: ResourceType,
        usage: float,
        limit: float,
        ratio: float,
        status: BudgetStatus,
    ) -> None:
        """记录使用量日志"""
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO budget_usage_log
                       (agent_id, resource_type, usage_value, limit_value, usage_ratio, status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (agent_id, resource_type.value, usage, limit, ratio, status.value),
                )
                self._conn.commit()
        except (OSError, IOError, sqlite3.Error):
            logger.warning("Failed to log budget usage", exc_info=True)

    def _record_notification(
        self,
        agent_id: str,
        notification_type: str,
        message: str,
        resource_type: str | None = None,
        usage_ratio: float | None = None,
    ) -> None:
        """记录预算通知"""
        try:
            with self._lock:
                self._conn.execute(
                    """INSERT INTO budget_notifications
                       (agent_id, notification_type, message, resource_type, usage_ratio)
                       VALUES (?, ?, ?, ?, ?)""",
                    (agent_id, notification_type, message, resource_type, usage_ratio),
                )
                self._conn.commit()
        except (OSError, IOError, sqlite3.Error):
            logger.warning("Failed to record budget notification", exc_info=True)

    def get_agent_budget_status(self, agent_id: str) -> dict[str, Any]:
        """获取代理预算状态概览"""
        result = self.check_budget(agent_id)
        return result.to_dict()

    def suspend_agent_tasks(self, agent_id: str, reason: str) -> None:
        """
        暂停代理的所有任务（当预算超出时调用）

        Args:
            agent_id: 代理ID
            reason: 暂停原因
        """
        from app.dependencies import get_scheduler

        try:
            scheduler = get_scheduler()
            tasks = scheduler.wakeup_queue.list_tasks(agent_id=agent_id)

            for task in tasks:
                if task.status.value not in ("completed", "failed"):
                    scheduler.pause_task(task.task_id)
                    logger.info(
                        "Task %s paused for agent %s: budget exceeded",
                        task.task_id,
                        agent_id,
                    )

            self._record_notification(agent_id, "suspended", reason)
        except (RuntimeError, ValueError, TypeError, AttributeError, OSError):
            logger.error("Failed to suspend agent tasks", exc_info=True)

    def get_notifications(self, agent_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """获取预算通知列表"""
        with self._lock:
            if agent_id:
                rows = self._conn.execute(
                    """SELECT * FROM budget_notifications
                       WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?""",
                    (agent_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """SELECT * FROM budget_notifications
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()

        return [dict(row) for row in rows]
