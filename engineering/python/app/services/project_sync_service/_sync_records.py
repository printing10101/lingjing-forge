"""同步记录 Mixin：record_sync + list_sync_records.

从原 ``project_sync_service.py`` 行 508-549、2022-2092 迁移而来。
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.project_sync import SYNC_DIRECTIONS
from app.database.models.project_sync import ProjectRepo, ProjectSyncRecord


class _SyncRecordsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    """同步记录 Mixin：record_sync + list_sync_records.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
    """

    async def _record_sync(
        self,
        session: AsyncSession,
        project_id: str,
        direction: str,
        *,
        commit_sha: str = "",
        status: str = "success",
        message: str = "",
        details: dict[str, Any] | None = None,
    ) -> ProjectSyncRecord:
        """写入一条同步记录（不 commit，由调用方负责 commit）.

        Args:
            session: 当前 SQLAlchemy 异步 session
            project_id: 所属项目 ID
            direction: SYNC_DIRECTIONS 之一
            commit_sha: 涉及的 commit sha
            status: success / failed / conflict
            message: 操作消息
            details: 附加详情

        Returns:
            创建的 ProjectSyncRecord ORM 实例
        """
        if not SYNC_DIRECTIONS.is_valid(direction):
            raise ValueError(f"direction 不支持: {direction}")
        record = ProjectSyncRecord(
            project_id=project_id,
            direction=direction,
            commit_sha=commit_sha or None,
            status=status,
            message=message or None,
            details=details or {},
        )
        session.add(record)
        return record

    async def list_sync_records(
        self,
        project_id: str,
        *,
        direction: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """列出项目的同步记录（按时间倒序）.

        Raises:
            ProjectNotFoundError: 项目不存在
            ValueError: direction 不支持
        """
        if direction is not None and not SYNC_DIRECTIONS.is_valid(direction):
            raise ValueError(f"direction 不支持: {direction}")
        limit = max(1, min(100, limit))
        offset = max(0, offset)

        async with await self._get_session() as session:
            # 校验项目存在
            p_stmt = select(ProjectRepo.project_id).where(ProjectRepo.project_id == project_id)
            if (await session.execute(p_stmt)).first() is None:
                from app.services.project_sync_service._exceptions import (
                    ProjectNotFoundError,
                )

                raise ProjectNotFoundError(f"项目不存在: {project_id}")

            stmt = select(ProjectSyncRecord).where(ProjectSyncRecord.project_id == project_id)
            count_stmt = (
                select(func.count()).select_from(ProjectSyncRecord).where(ProjectSyncRecord.project_id == project_id)
            )
            if direction:
                stmt = stmt.where(ProjectSyncRecord.direction == direction)
                count_stmt = count_stmt.where(ProjectSyncRecord.direction == direction)

            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(desc(ProjectSyncRecord.timestamp)).limit(limit).offset(offset)
            records = [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
            return {
                "project_id": project_id,
                "records": records,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
