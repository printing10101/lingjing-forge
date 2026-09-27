"""恢复方法组：崩溃恢复与孤儿任务重排。"""

from __future__ import annotations

from datetime import datetime, timezone


from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import TrainingTask, TaskStatusEnum


import logging

logger = logging.getLogger(__name__)


class _TaskRecoveryMixin:
    async def _recover_running_tasks(self):
        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            return

        try:
            async with sessionmaker() as session:
                result = await session.execute(
                    select(TrainingTask).where(TrainingTask.status == TaskStatusEnum.RUNNING)
                )
                running_tasks = result.scalars().all()

            if not running_tasks:
                return

            logger.warning(
                "Found %d RUNNING tasks from previous session, marking as FAILED",
                len(running_tasks),
            )

            async with sessionmaker() as session:
                now = datetime.now(timezone.utc)
                for task in running_tasks:
                    task.status = TaskStatusEnum.FAILED
                    task.error = "Service restarted: task was running before shutdown"
                    task.completed_at = now
                    task.progress = task.progress or 0
                try:
                    await session.commit()
                except SQLAlchemyError:
                    await session.rollback()
                    raise

        except (RuntimeError, OSError, SQLAlchemyError) as e:
            logger.error("Task recovery failed: %s", e)

    async def requeue_orphan_tasks(
        self,
        *,
        task_types: list[str] | None = None,
        max_age_seconds: int = 3600,
    ) -> int:
        """将历史 RUNNING 任务重置为 QUEUED，便于重新调度。

        与 :meth:`_recover_running_tasks` 不同的是：后者把任务标记为
        ``FAILED``（用于生产环境的"宁可丢不可错跑"），而本方法将其
        重新放回队列（用于可恢复的训练/推理任务）。

        Args:
            task_types: 仅重置这些任务类型；为 ``None`` 时重置全部。
            max_age_seconds: 仅重置 ``updated_at`` 距今不超过此秒数的任务。

        Returns:
            实际重置的任务数量。
        """
        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            return 0
        try:
            from datetime import timedelta

            cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
            async with sessionmaker() as session:
                stmt = select(TrainingTask).where(TrainingTask.status == TaskStatusEnum.RUNNING)
                if task_types:
                    stmt = stmt.where(TrainingTask.task_type.in_(task_types))
                result = await session.execute(stmt)
                orphans = result.scalars().all()
                requeued = 0
                for task in orphans:
                    task.status = TaskStatusEnum.PENDING
                    task.error = None
                    task.started_at = None
                    task.completed_at = None
                    requeued += 1
                try:
                    await session.commit()
                except SQLAlchemyError:
                    await session.rollback()
                    raise
                if requeued:
                    logger.warning(
                        "requeue_orphan_tasks: 重置 %d 个 RUNNING→QUEUED (cutoff=%s)",
                        requeued,
                        cutoff.isoformat(),
                    )
                return requeued
        except (RuntimeError, OSError, SQLAlchemyError) as e:
            logger.error("requeue_orphan_tasks failed: %s", e)
            return 0
