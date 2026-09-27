"""签出业务方法组。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from app.tasks._checkout_models import (
    CheckoutFailureReason,
    CheckoutResult,
    CheckoutStatus,
    TaskRecord,
    TaskStatus,
)
from app.tasks.execution_lock import (
    LockConflictError,
    LockNotFoundError,
)

logger = logging.getLogger(__name__)


class _TaskCheckoutOpsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_conn: Callable[..., Any]
    _record_failure: Callable[..., Any]
    _row_to_task: Callable[..., Any]
    _serialize_blockers: Callable[..., Any]
    _conn: Any
    _lock_store: Any
    _pool: Any

    def register_task(self, task: TaskRecord):
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO checkout_tasks
               (id, title, description, task_type, status, assigned_to, parent_goal_id,
                project_id, required_gpu_memory, blockers, priority,
                checked_out_at, checkout_expires_at, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.title,
                task.description,
                task.task_type,
                task.status,
                task.assigned_to,
                task.parent_goal_id,
                task.project_id,
                task.required_gpu_memory,
                self._serialize_blockers(task.blockers),
                task.priority,
                task.checked_out_at,
                task.checkout_expires_at,
                task.created_at,
                task.completed_at,
            ),
        )
        conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM checkout_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def complete_task(self, task_id: str, agent_id: str) -> CheckoutResult:
        task = self.get_task(task_id)
        if task is None:
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task '{task_id}' not found",
                failure_reason=CheckoutFailureReason.ALREADY_CHECKED_OUT,
            )

        if task.status != TaskStatus.IN_PROGRESS.value:
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task is not in progress (current: {task.status})",
                failure_reason=CheckoutFailureReason.ALREADY_CHECKED_OUT,
            )

        if task.assigned_to != agent_id:
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task is assigned to {task.assigned_to}, not {agent_id}",
                failure_reason=CheckoutFailureReason.ASSIGNED_TO_OTHER,
            )

        try:
            self._lock_store.release_lock(task_id, agent_id, "task completed")
        except (LockNotFoundError, LockConflictError) as lock_err:
            # 任务完成时锁可能已过期/被回收，不影响主流程
            logger.debug(
                "Lock release on task complete skipped for %s: %s",
                task_id,
                lock_err,
                exc_info=True,
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            "UPDATE checkout_tasks SET status = ?, completed_at = ? WHERE id = ?",
            (TaskStatus.COMPLETED.value, now_iso, task_id),
        )
        conn.commit()

        logger.info("Task completed: task=%s agent=%s", task_id, agent_id)
        return CheckoutResult(
            status=CheckoutStatus.SUCCESS,
            task_id=task_id,
            agent_id=agent_id,
            message="Task completed successfully",
        )

    def fail_task(self, task_id: str, agent_id: str, reason: str = "") -> CheckoutResult:
        task = self.get_task(task_id)
        if task is None:
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task '{task_id}' not found",
                failure_reason=CheckoutFailureReason.ALREADY_CHECKED_OUT,
            )

        try:
            self._lock_store.release_lock(task_id, agent_id, f"task failed: {reason}")
        except (LockNotFoundError, LockConflictError) as lock_err:
            # 任务失败时锁可能已被其他流程释放，记录以便排查
            logger.debug(
                "Lock release on task failed skipped for %s: %s",
                task_id,
                lock_err,
                exc_info=True,
            )

        # [C3] 修复死代码：原 datetime.now(timezone.utc).isoformat() 返回值未使用，
        # 且 fail_task 未记录 completed_at。现在在 UPDATE 中写入完成时间。
        failed_at = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute(
            "UPDATE checkout_tasks SET status = ?, assigned_to = NULL, completed_at = ? WHERE id = ?",
            (TaskStatus.FAILED.value, failed_at, task_id),
        )
        conn.commit()

        self._record_failure(task_id, agent_id, CheckoutFailureReason.TASK_FAILED, reason)

        logger.warning("Task failed: task=%s agent=%s reason=%s", task_id, agent_id, reason)
        return CheckoutResult(
            status=CheckoutStatus.SUCCESS,
            task_id=task_id,
            agent_id=agent_id,
            message=f"Task marked as failed: {reason}",
        )

    def abandon_task(self, task_id: str, agent_id: str) -> CheckoutResult:
        task = self.get_task(task_id)
        if task is None:
            return CheckoutResult(
                status=CheckoutStatus.FAILED,
                task_id=task_id,
                agent_id=agent_id,
                message=f"Task '{task_id}' not found",
                failure_reason=CheckoutFailureReason.ALREADY_CHECKED_OUT,
            )

        try:
            self._lock_store.release_lock(task_id, agent_id, "task abandoned")
        except (LockNotFoundError, LockConflictError) as lock_err:
            # 任务放弃时锁可能已被自动回收，记录以便排查
            logger.debug(
                "Lock release on task abandoned skipped for %s: %s",
                task_id,
                lock_err,
                exc_info=True,
            )

        conn = self._get_conn()
        conn.execute(
            "UPDATE checkout_tasks SET status = ?, assigned_to = NULL, "
            "checked_out_at = NULL, checkout_expires_at = NULL WHERE id = ?",
            (TaskStatus.PENDING.value, task_id),
        )
        conn.commit()

        logger.info("Task abandoned: task=%s agent=%s", task_id, agent_id)
        return CheckoutResult(
            status=CheckoutStatus.SUCCESS,
            task_id=task_id,
            agent_id=agent_id,
            message="Task abandoned, returned to pending",
        )

    def get_task_board(self) -> dict[str, list[dict]]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM checkout_tasks ORDER BY priority ASC, created_at ASC").fetchall()

        board: dict[str, list[dict]] = {
            "pending": [],
            "in_progress": [],
            "completed": [],
            "failed": [],
            "cancelled": [],
        }

        for row in rows:
            task = self._row_to_task(row)
            task_dict = task.to_dict()

            active_lock = self._lock_store.get_lock(task.id)
            if active_lock and active_lock.status.value == "active":
                task_dict["lock_info"] = active_lock.to_dict()
            else:
                task_dict["lock_info"] = None

            status = task.status
            if status in board:
                board[status].append(task_dict)
            else:
                board["pending"].append(task_dict)

        return board

    def get_task_checkout_history(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            return {"task": None, "lock_history": [], "failure_history": []}

        lock_history = self._lock_store.get_lock_history(task_id)

        conn = self._get_conn()
        failure_rows = conn.execute(
            "SELECT * FROM checkout_failure_history WHERE task_id = ? ORDER BY timestamp DESC",
            (task_id,),
        ).fetchall()

        failure_history = [
            {
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "reason": row["reason"],
                "message": row["message"],
                "timestamp": row["timestamp"],
            }
            for row in failure_rows
        ]

        return {
            "task": task.to_dict(),
            "lock_history": lock_history,
            "failure_history": failure_history,
        }

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        active_lock = self._lock_store.get_active_lock_by_agent(agent_id)
        pending_count = self._count_tasks_by_agent(agent_id, TaskStatus.PENDING.value)
        in_progress_count = self._count_tasks_by_agent(agent_id, TaskStatus.IN_PROGRESS.value)
        completed_count = self._count_tasks_by_agent(agent_id, TaskStatus.COMPLETED.value)

        return {
            "agent_id": agent_id,
            "active_lock": active_lock.to_dict() if active_lock else None,
            "has_active_task": active_lock is not None,
            "pending_tasks": pending_count,
            "in_progress_tasks": in_progress_count,
            "completed_tasks": completed_count,
        }

    def _count_tasks_by_agent(self, agent_id: str, status: str) -> int:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM checkout_tasks WHERE assigned_to = ? AND status = ?",
            (agent_id, status),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_all_locks(self) -> list[dict]:
        return [lock.to_dict() for lock in self._lock_store.list_all_locks()]

    def close(self):
        # 修复：原实现直接调用 self._conn.close() 关闭连接，
        # 但连接是从 SQLiteConnectionPool 借出的——直接 close 会导致：
        # 1. 连接池 _active_count / _created_count 不会减少（计数器永久漂移）
        # 2. _borrowed 字典残留借出记录，check_leaked_connections 误报泄漏
        # 3. 后续 close_all() 再次 close 同一连接（虽 sqlite3 允许重复 close，
        # 但日志会刷错误且掩盖真实问题）
        # 现通过 return_connection 将连接归还到连接池，由连接池统一决定
        # 是否放回池中复用或关闭（连接池满时由 return_connection 内部 close）。
        if self._conn is not None:
            try:
                self._pool.return_connection(self._conn)
            except (OSError, RuntimeError, ValueError) as e:
                logger.warning("Failed to return SQLite connection to pool: %s", e)
                try:
                    self._conn.close()
                except (OSError, RuntimeError) as close_err:
                    logger.debug(
                        "Fallback close after return failure also failed: %s",
                        close_err,
                    )
            self._conn = None
