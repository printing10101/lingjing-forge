"""签出队列方法组。"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from collections.abc import Callable

from app.tasks._checkout_models import (
    MAX_RETRY_COUNT,
    CheckoutFailureReason,
    CheckoutPriority,
    CheckoutQueueEntry,
    CheckoutRequest,
    CheckoutResult,
    CheckoutStatus,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class _TaskCheckoutQueueMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_conn: Callable[..., Any]
    checkout_task: Callable[..., Any]
    fail_task: Callable[..., Any]
    get_task: Callable[..., Any]
    _queue_lock: Any

    def enqueue_checkout(self, request: CheckoutRequest) -> CheckoutQueueEntry:
        with self._queue_lock:
            conn = self._get_conn()
            now = time.time()
            conn.execute(
                """INSERT OR REPLACE INTO checkout_queue
                   (task_id, agent_id, priority, retry_count, last_failure, next_retry_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    request.task_id,
                    request.agent_id,
                    request.priority.value,
                    0,
                    None,
                    now,
                    now,
                ),
            )
            conn.commit()

        logger.debug(
            "Checkout enqueued: task=%s agent=%s priority=%s",
            request.task_id,
            request.agent_id,
            request.priority.name,
        )
        return CheckoutQueueEntry(
            task_id=request.task_id,
            agent_id=request.agent_id,
            priority=request.priority,
            created_at=now,
        )

    def process_queue(self, max_batch: int = 10) -> list[CheckoutResult]:
        with self._queue_lock:
            conn = self._get_conn()
            now = time.time()
            rows = conn.execute(
                """SELECT * FROM checkout_queue
                   WHERE next_retry_at IS NULL OR next_retry_at <= ?
                   ORDER BY priority ASC, created_at ASC
                   LIMIT ?""",
                (now, max_batch),
            ).fetchall()

            results: list[CheckoutResult] = []
            for row in rows:
                # [C4] 枚举构造防御：数据库可能存在老版本/手动修改的非法值，
                # 直接构造会抛 ValueError 中断整个队列处理，降级为默认值并记录
                try:
                    priority = CheckoutPriority(row["priority"])
                except (ValueError, KeyError):
                    priority = CheckoutPriority.NORMAL
                    logger.warning(
                        "Invalid priority %r in queue row task=%s, using NORMAL",
                        row.get("priority"),
                        row.get("task_id"),
                    )
                try:
                    last_failure = CheckoutFailureReason(row["last_failure"]) if row["last_failure"] else None
                except (ValueError, KeyError):
                    last_failure = None
                    logger.warning(
                        "Invalid last_failure %r in queue row task=%s, set to None",
                        row.get("last_failure"),
                        row.get("task_id"),
                    )
                entry = CheckoutQueueEntry(
                    task_id=row["task_id"],
                    agent_id=row["agent_id"],
                    priority=priority,
                    created_at=row["created_at"],
                    retry_count=row["retry_count"],
                    last_failure=last_failure,
                    next_retry_at=row["next_retry_at"],
                )

                task = self.get_task(entry.task_id)
                required_gpu = task.required_gpu_memory if task else 0.0

                request = CheckoutRequest(
                    task_id=entry.task_id,
                    agent_id=entry.agent_id,
                    priority=entry.priority,
                    required_gpu_memory=required_gpu,
                )

                result = self.checkout_task(request)

                if result.status == CheckoutStatus.SUCCESS:
                    conn.execute(
                        "DELETE FROM checkout_queue WHERE task_id = ? AND agent_id = ?",
                        (entry.task_id, entry.agent_id),
                    )
                else:
                    new_retry_count = entry.retry_count + 1
                    if new_retry_count >= MAX_RETRY_COUNT:
                        conn.execute(
                            "DELETE FROM checkout_queue WHERE task_id = ? AND agent_id = ?",
                            (entry.task_id, entry.agent_id),
                        )
                        self.fail_task(
                            entry.task_id,
                            entry.agent_id,
                            f"Max retries ({MAX_RETRY_COUNT}) exceeded: {result.failure_reason}",
                        )
                    else:
                        next_retry = now + result.retry_delay_minutes * 60
                        conn.execute(
                            """UPDATE checkout_queue
                               SET retry_count = ?, last_failure = ?, next_retry_at = ?
                               WHERE task_id = ? AND agent_id = ?""",
                            (
                                new_retry_count,
                                result.failure_reason.value if result.failure_reason else None,
                                next_retry,
                                entry.task_id,
                                entry.agent_id,
                            ),
                        )

                results.append(result)

            conn.commit()
            return results

    def get_queue_status(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM checkout_queue ORDER BY priority ASC, created_at ASC").fetchall()
        return [
            {
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "priority": row["priority"],
                "retry_count": row["retry_count"],
                "last_failure": row["last_failure"],
                "next_retry_at": datetime.fromtimestamp(row["next_retry_at"]).isoformat()
                if row["next_retry_at"]
                else None,
                "created_at": datetime.fromtimestamp(row["created_at"]).isoformat(),
            }
            for row in rows
        ]

    def _get_unresolved_blockers(self, blockers: list[str]) -> list[str]:
        unresolved = []
        for blocker_id in blockers:
            blocker_task = self.get_task(blocker_id)
            if blocker_task is None or blocker_task.status != TaskStatus.COMPLETED.value:
                unresolved.append(blocker_id)
        return unresolved
