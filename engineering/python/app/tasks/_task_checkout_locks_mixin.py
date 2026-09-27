"""签出锁方法组。"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Callable

from app.tasks._checkout_models import (
    CONFLICT_RETRY_DELAY_MINUTES,
    GPU_RETRY_DELAY_MINUTES,
    AgentMode,
    CheckoutFailureReason,
    CheckoutRequest,
    CheckoutResult,
    CheckoutStatus,
    TaskStatus,
)
from app.tasks.execution_lock import (
    LockConflictError,
    LockNotFoundError,
)

logger = logging.getLogger(__name__)


class _TaskCheckoutLocksMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _budget_checker: Any
    _get_unresolved_blockers: Callable[..., Any]
    _gpu_checker: Any
    get_task: Callable[..., Any]
    _checkout_lock: Any
    _conn: Any
    _lock_store: Any

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（从连接池）"""
        return self._conn

    def _ensure_tables(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkout_tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT 'execution',
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_to TEXT,
                parent_goal_id TEXT,
                project_id TEXT,
                required_gpu_memory REAL NOT NULL DEFAULT 0.0,
                blockers TEXT NOT NULL DEFAULT '[]',
                priority INTEGER NOT NULL DEFAULT 3,
                checked_out_at TEXT,
                checkout_expires_at TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkout_failure_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                message TEXT,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checkout_queue (
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 3,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_failure TEXT,
                next_retry_at REAL,
                created_at REAL NOT NULL,
                PRIMARY KEY (task_id, agent_id)
            )
        """)
        conn.commit()

    def checkout_task(self, request: CheckoutRequest) -> CheckoutResult:
        with self._checkout_lock:
            return self._perform_checkout(request)

    def _perform_checkout(self, request: CheckoutRequest) -> CheckoutResult:
        task = self.get_task(request.task_id)
        if task is None:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.ALREADY_CHECKED_OUT,
                f"Task '{request.task_id}' not found",
                retry=False,
            )

        if task.status == TaskStatus.COMPLETED.value:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.TASK_COMPLETED,
                f"Task '{request.task_id}' is already completed",
                retry=False,
            )

        if task.status == TaskStatus.FAILED.value:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.TASK_FAILED,
                f"Task '{request.task_id}' has failed",
                retry=False,
            )

        if task.status == TaskStatus.CANCELLED.value:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.ALREADY_CHECKED_OUT,
                f"Task '{request.task_id}' is cancelled",
                retry=False,
            )

        if task.status == TaskStatus.IN_PROGRESS.value and task.assigned_to != request.agent_id:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.ASSIGNED_TO_OTHER,
                f"Task is assigned to {task.assigned_to}",
                retry=False,
            )

        if task.assigned_to and task.assigned_to != request.agent_id:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.ASSIGNED_TO_OTHER,
                f"Task assigned to {task.assigned_to}",
                retry=True,
                retry_delay=CONFLICT_RETRY_DELAY_MINUTES,
            )

        if task.blockers:
            unresolved = self._get_unresolved_blockers(task.blockers)
            if unresolved:
                return self._checkout_fail(
                    request,
                    CheckoutFailureReason.BLOCKERS_UNRESOLVED,
                    f"Unresolved blockers: {unresolved}",
                    retry=True,
                    retry_delay=CONFLICT_RETRY_DELAY_MINUTES,
                )

        if request.agent_mode == AgentMode.SINGLE:
            active_lock = self._lock_store.get_active_lock_by_agent(request.agent_id)
            if active_lock and active_lock.task_id != request.task_id:
                return self._checkout_fail(
                    request,
                    CheckoutFailureReason.AGENT_BUSY,
                    f"Agent '{request.agent_id}' is holding lock on task '{active_lock.task_id}'",
                    retry=False,
                )

        if self._budget_checker is not None:
            project_id = task.project_id
            if not self._budget_checker(request.agent_id, project_id):
                self._record_failure(
                    request.task_id,
                    request.agent_id,
                    CheckoutFailureReason.BUDGET_EXCEEDED,
                    "Budget exceeded for agent/project",
                )
                return self._checkout_fail(
                    request,
                    CheckoutFailureReason.BUDGET_EXCEEDED,
                    "Budget exceeded",
                    retry=False,
                )

        if request.required_gpu_memory > 0 and self._gpu_checker is not None:
            if not self._gpu_checker(request.required_gpu_memory):
                self._record_failure(
                    request.task_id,
                    request.agent_id,
                    CheckoutFailureReason.GPU_UNAVAILABLE,
                    f"GPU memory {request.required_gpu_memory}GB unavailable",
                )
                return self._checkout_fail(
                    request,
                    CheckoutFailureReason.GPU_UNAVAILABLE,
                    f"GPU resources unavailable (need {request.required_gpu_memory}GB)",
                    retry=True,
                    retry_delay=GPU_RETRY_DELAY_MINUTES,
                )

        try:
            lock = self._lock_store.create_lock(
                task_id=request.task_id,
                agent_id=request.agent_id,
                timeout_hours=request.timeout_hours,
            )
        except LockConflictError as e:
            return self._checkout_fail(
                request,
                CheckoutFailureReason.LOCK_EXISTS,
                str(e),
                retry=True,
                retry_delay=CONFLICT_RETRY_DELAY_MINUTES,
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        expires_iso = (datetime.now(timezone.utc) + timedelta(hours=request.timeout_hours)).isoformat()

        conn = self._get_conn()
        conn.execute(
            """UPDATE checkout_tasks
               SET status = ?, assigned_to = ?, checked_out_at = ?, checkout_expires_at = ?
               WHERE id = ?""",
            (
                TaskStatus.IN_PROGRESS.value,
                request.agent_id,
                now_iso,
                expires_iso,
                request.task_id,
            ),
        )
        conn.commit()

        logger.info(
            "Task checked out: task=%s agent=%s expires=%s",
            request.task_id,
            request.agent_id,
            expires_iso,
        )
        return CheckoutResult(
            status=CheckoutStatus.SUCCESS,
            task_id=request.task_id,
            agent_id=request.agent_id,
            message="Task checked out successfully",
            lock=lock,
            checked_out_at=now_iso,
            expires_at=expires_iso,
        )

    def force_release_lock(self, task_id: str, admin_id: str = "admin") -> CheckoutResult:
        try:
            lock = self._lock_store.force_release(task_id, admin_id)

            conn = self._get_conn()
            conn.execute(
                "UPDATE checkout_tasks SET status = ?, assigned_to = NULL, "
                "checked_out_at = NULL, checkout_expires_at = NULL WHERE id = ?",
                (TaskStatus.PENDING.value, task_id),
            )
            conn.commit()

            self._record_failure(
                task_id,
                lock.agent_id,
                "force_released",
                f"Lock force-released by {admin_id}",
            )

            logger.warning(
                "Lock force-released and task reset: task=%s agent=%s by=%s",
                task_id,
                lock.agent_id,
                admin_id,
            )
            return CheckoutResult(
                status=CheckoutStatus.SUCCESS,
                task_id=task_id,
                agent_id=lock.agent_id,
                message=f"Lock force-released by {admin_id}, task returned to pending",
            )
        except LockNotFoundError as e:
            logger.warning("Lock not found for force-release: task_id=%s err=%s", task_id, e)
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id="",
                message="锁不存在或已过期，无法强制释放",
                failure_reason=CheckoutFailureReason.LOCK_EXISTS,
            )

    def cleanup_expired_locks(self) -> list[dict]:
        expired = self._lock_store.cleanup_expired_locks()

        for lock in expired:
            task = self.get_task(lock.task_id)
            if task and task.status == TaskStatus.IN_PROGRESS.value:
                conn = self._get_conn()
                conn.execute(
                    "UPDATE checkout_tasks SET status = ?, assigned_to = NULL, "
                    "checked_out_at = NULL, checkout_expires_at = NULL WHERE id = ?",
                    (TaskStatus.PENDING.value, lock.task_id),
                )
                conn.commit()
                self._record_failure(
                    lock.task_id,
                    lock.agent_id,
                    "lock_expired",
                    "Task returned to pending due to lock timeout",
                )
                logger.warning(
                    "Task returned to pending due to expired lock: task=%s agent=%s",
                    lock.task_id,
                    lock.agent_id,
                )

        return [e.to_dict() for e in expired]

    def _record_failure(self, task_id: str, agent_id: str, reason, message: str = ""):
        reason_str = reason.value if isinstance(reason, CheckoutFailureReason) else reason
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO checkout_failure_history (task_id, agent_id, reason, message, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (task_id, agent_id, reason_str, message, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    def _checkout_fail(
        self,
        request: CheckoutRequest,
        reason: CheckoutFailureReason,
        message: str,
        retry: bool = False,
        retry_delay: int = 0,
    ) -> CheckoutResult:
        self._record_failure(request.task_id, request.agent_id, reason, message)

        logger.warning(
            "Checkout failed: task=%s agent=%s reason=%s message=%s retry=%s",
            request.task_id,
            request.agent_id,
            reason.value,
            message,
            retry,
        )

        return CheckoutResult(
            status=CheckoutStatus.FAILED,
            task_id=request.task_id,
            agent_id=request.agent_id,
            message=message,
            failure_reason=reason,
            retry_recommended=retry,
            retry_delay_minutes=retry_delay,
        )
