"""统计方法组：任务统计与 Redis 进度查询。"""

from __future__ import annotations

from typing import Any


from app.services.redis_client import get_task_progress
from app.tasks.task_manager import TaskStatus


import logging

logger = logging.getLogger(__name__)


class _TaskStatsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _max_concurrent: Any
    _tasks: Any

    def get_stats(self) -> dict[str, Any]:
        total = len(self._tasks)
        active = sum(1 for t in self._tasks.values() if t.status == TaskStatus.RUNNING)
        queued = sum(1 for t in self._tasks.values() if t.status == TaskStatus.QUEUED)
        completed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in self._tasks.values() if t.status == TaskStatus.FAILED)

        return {
            "total_tasks": total,
            "active_tasks": active,
            "queued_tasks": queued,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "max_concurrent": self._max_concurrent,
            "available_slots": self._max_concurrent - active,
        }

    async def get_task_progress_from_redis(self, job_id: str) -> dict[str, Any]:
        return await get_task_progress(job_id)
