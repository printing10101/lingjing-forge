"""持久化方法组：任务元数据入库/查询。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.database.models import TrainingTask
from app.tasks._task_types import (
    TaskRecord,
)
from app.tasks.task_manager import TaskStatus, TaskType


import logging

logger = logging.getLogger(__name__)


class _TaskPersistenceMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_task_lock: Callable[..., Any]
    _tasks: Any

    async def _persist_task_to_db(self, record: TaskRecord):
        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            return

        try:
            async with sessionmaker() as session:
                existing = await session.get(TrainingTask, record.job_id)
                if existing:
                    existing.status = record.status.value
                    existing.progress = int(record.progress)
                    existing.result = record.result
                    existing.error = record.error
                    existing.params = record.params
                    if record.started_at:
                        existing.started_at = datetime.fromtimestamp(record.started_at, tz=timezone.utc)
                    if record.completed_at:
                        existing.completed_at = datetime.fromtimestamp(record.completed_at, tz=timezone.utc)
                else:
                    task_model = TrainingTask(
                        id=record.job_id,
                        task_type=record.task_type.value,
                        status=record.status.value,
                        progress=int(record.progress),
                        params=record.params,
                        result=record.result,
                        error=record.error,
                        owner_id=record.owner_id,
                        idempotency_key=record.idempotency_key,
                        created_at=datetime.fromtimestamp(record.created_at, tz=timezone.utc),
                        started_at=datetime.fromtimestamp(record.started_at, tz=timezone.utc)
                        if record.started_at
                        else None,
                        completed_at=datetime.fromtimestamp(record.completed_at, tz=timezone.utc)
                        if record.completed_at
                        else None,
                    )
                    session.add(task_model)
                try:
                    await session.commit()
                except SQLAlchemyError:
                    await session.rollback()
                    raise
        except (RuntimeError, OSError, SQLAlchemyError) as e:
            logger.error("Failed to persist task %s to DB: %s", record.job_id, e)

    async def get_task(self, job_id: str) -> TaskRecord | None:
        async with self._get_task_lock():
            if job_id in self._tasks:
                return self._tasks[job_id]

        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            return None

        try:
            async with sessionmaker() as session:
                result = await session.execute(select(TrainingTask).where(TrainingTask.id == job_id))
                db_task = result.scalar_one_or_none()
                if db_task:
                    record = TaskRecord.from_db_model(db_task)
                    async with self._get_task_lock():
                        self._tasks[job_id] = record
                    return record
        except (RuntimeError, OSError) as e:
            logger.error("Failed to load task %s from DB: %s", job_id, e)

        return None

    async def count_tasks(
        self,
        owner_id: str | None = None,
        task_type: TaskType | None = None,
        status: TaskStatus | None = None,
    ) -> int:
        """统计符合条件的任务总数（用于分页）"""
        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            async with self._get_task_lock():
                tasks = list(self._tasks.values())
            filtered = self._filter_tasks(tasks, owner_id, task_type, status, limit=len(tasks), offset=0)
            return len(filtered)

        try:
            async with sessionmaker() as session:
                from sqlalchemy import func

                query = select(func.count(TrainingTask.id))

                filters = []
                if owner_id:
                    filters.append(TrainingTask.owner_id == owner_id)
                if task_type:
                    filters.append(TrainingTask.task_type == task_type.value)
                if status:
                    filters.append(TrainingTask.status == status.value)

                if filters:
                    from sqlalchemy import and_

                    query = query.where(and_(*filters))

                result = await session.execute(query)
                return result.scalar() or 0
        except (RuntimeError, OSError) as e:
            logger.error("Failed to count tasks from DB: %s", e)
            async with self._get_task_lock():
                tasks = list(self._tasks.values())
            filtered = self._filter_tasks(tasks, owner_id, task_type, status, limit=len(tasks), offset=0)
            return len(filtered)

    async def list_tasks(
        self,
        owner_id: str | None = None,
        task_type: TaskType | None = None,
        status: TaskStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        from app.tasks.task_system import get_sessionmaker  # 惰性导入：使测试 monkeypatch 生效

        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            async with self._get_task_lock():
                tasks = list(self._tasks.values())
            return self._filter_tasks(tasks, owner_id, task_type, status, limit, offset)

        try:
            async with sessionmaker() as session:
                query = select(TrainingTask)

                filters = []
                if owner_id:
                    filters.append(TrainingTask.owner_id == owner_id)
                if task_type:
                    filters.append(TrainingTask.task_type == task_type.value)
                if status:
                    filters.append(TrainingTask.status == status.value)

                if filters:
                    from sqlalchemy import and_

                    query = query.where(and_(*filters))

                query = query.order_by(TrainingTask.created_at.desc())
                query = query.offset(offset).limit(limit)

                result = await session.execute(query)
                db_tasks = result.scalars().all()

                records = []
                for db_task in db_tasks:
                    record = TaskRecord.from_db_model(db_task)
                    records.append(record)
                    async with self._get_task_lock():
                        if record.job_id not in self._tasks:
                            self._tasks[record.job_id] = record

                return records
        except (RuntimeError, OSError) as e:
            logger.error("Failed to list tasks from DB: %s", e)
            async with self._get_task_lock():
                tasks = list(self._tasks.values())
            return self._filter_tasks(tasks, owner_id, task_type, status, limit, offset)

    def _filter_tasks(
        self,
        tasks: list[TaskRecord],
        owner_id: str | None,
        task_type: TaskType | None,
        status: TaskStatus | None,
        limit: int,
        offset: int,
    ) -> list[TaskRecord]:
        if owner_id:
            tasks = [t for t in tasks if t.owner_id == owner_id]
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[offset : offset + limit]
