"""资源引用 Mixin：add/remove/update/list resource refs.

从原 ``project_sync_service.py`` 行 934-1200 迁移而来。
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.contracts.project_sync import (
    DEFAULT_SYNC_STRATEGY,
    RESOURCE_TYPES,
    SYNC_STATUS,
    SYNC_STRATEGIES,
    parse_resource_uri,
)
from app.database.models.project_sync import ProjectRepo, ProjectResourceRef
from app.services.project_sync_service._exceptions import (
    ProjectNotFoundError,
    ResourceRefAlreadyExistsError,
    ResourceRefNotFoundError,
)

logger = logging.getLogger(__name__)


class _ResourceRefMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    _compute_content_hash: Callable[..., Any]
    """资源引用 Mixin：add/remove/update/list resource refs.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
        - ``self._compute_content_hash()``（来自 _HashingMixin）
    """

    async def add_resource_ref(
        self,
        project_id: str,
        resource_type: str,
        resource_uri: str,
        *,
        sync_strategy: str | None = None,
        metadata: dict[str, Any] | None = None,
        compute_hash: bool = True,
    ) -> dict[str, Any]:
        """添加资源引用到项目.

        Args:
            project_id: 所属项目 ID
            resource_type: RESOURCE_TYPES 之一
            resource_uri: 资源 URI（scheme 必须与 resource_type 一致）
            sync_strategy: 同步策略（None 使用 DEFAULT_SYNC_STRATEGY）
            metadata: 附加元数据
            compute_hash: 是否立即计算 content_hash

        Returns:
            创建的资源引用 dict

        Raises:
            ProjectNotFoundError: 项目不存在
            ResourceRefAlreadyExistsError: 资源 URI 已存在
            ValueError: 参数非法
        """
        strategy = self._validate_resource_ref_inputs(resource_type, resource_uri, sync_strategy)
        content_hash = await self._maybe_compute_ref_hash(resource_type, resource_uri, compute_hash)
        ref_dict = await self._persist_resource_ref(
            project_id,
            resource_type,
            resource_uri,
            content_hash,
            strategy,
            metadata,
        )
        logger.info(
            "Added resource ref to project %s: %s",
            project_id,
            resource_uri,
        )
        return ref_dict

    def _validate_resource_ref_inputs(
        self,
        resource_type: str,
        resource_uri: str,
        sync_strategy: str | None,
    ) -> str:
        """校验 add_resource_ref 输入参数，返回解析后的 sync_strategy.

        Raises:
            ValueError: resource_type 不支持 / URI scheme 不匹配 / strategy 不支持
        """
        if not RESOURCE_TYPES.is_valid(resource_type):
            raise ValueError(f"resource_type 不支持: {resource_type}")
        # 校验 URI scheme 与 resource_type 一致
        scheme, _ = parse_resource_uri(resource_uri)
        if scheme != resource_type:
            raise ValueError(f"URI scheme ({scheme}) 与 resource_type ({resource_type}) 不匹配")
        strategy = sync_strategy or DEFAULT_SYNC_STRATEGY[resource_type]
        if not SYNC_STRATEGIES.is_valid(strategy):
            raise ValueError(f"sync_strategy 不支持: {strategy}")
        return strategy

    async def _maybe_compute_ref_hash(
        self,
        resource_type: str,
        resource_uri: str,
        compute_hash: bool,
    ) -> str:
        """按需计算 content_hash（compute_hash=False 时返回空字符串）."""
        if not compute_hash:
            return ""
        return await self._compute_content_hash(resource_type, resource_uri)

    async def _persist_resource_ref(
        self,
        project_id: str,
        resource_type: str,
        resource_uri: str,
        content_hash: str,
        strategy: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """持久化资源引用 + 更新项目状态为 dirty.

        事务边界与原实现一致：ref 插入 commit 一次，project 状态更新再 commit
        一次（两次显式 commit 均保留）。

        Raises:
            ProjectNotFoundError: 项目不存在
            ResourceRefAlreadyExistsError: 资源 URI 已存在
        """
        async with await self._get_session() as session:
            # 校验项目存在
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")

            # 校验 URI 不重复
            dup_stmt = select(ProjectResourceRef).where(
                ProjectResourceRef.project_id == project_id,
                ProjectResourceRef.resource_uri == resource_uri,
            )
            if (await session.execute(dup_stmt)).scalar_one_or_none() is not None:
                raise ResourceRefAlreadyExistsError(f"资源 URI 已存在: {resource_uri}")

            ref_orm = ProjectResourceRef(
                project_id=project_id,
                resource_type=resource_type,
                resource_uri=resource_uri,
                content_hash=content_hash or None,
                sync_strategy=strategy,
                metadata_json=metadata or {},
            )
            session.add(ref_orm)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise ResourceRefAlreadyExistsError(f"资源 URI 已存在: {resource_uri}") from e

            # 项目状态置 dirty（资源引用变化未 commit）
            project_orm.status = SYNC_STATUS.DIRTY
            await session.commit()

        return ref_orm.to_dict()

    async def remove_resource_ref(self, project_id: str, resource_uri: str) -> dict[str, Any]:
        """删除资源引用.

        Raises:
            ProjectNotFoundError: 项目不存在
            ResourceRefNotFoundError: 资源引用不存在
        """
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")

            ref_stmt = select(ProjectResourceRef).where(
                ProjectResourceRef.project_id == project_id,
                ProjectResourceRef.resource_uri == resource_uri,
            )
            ref_orm = (await session.execute(ref_stmt)).scalar_one_or_none()
            if ref_orm is None:
                raise ResourceRefNotFoundError(f"资源引用不存在: {resource_uri}")

            await session.delete(ref_orm)
            project_orm.status = SYNC_STATUS.DIRTY
            await session.commit()

        logger.info(
            "Removed resource ref from project %s: %s",
            project_id,
            resource_uri,
        )
        return {
            "project_id": project_id,
            "resource_uri": resource_uri,
            "deleted": True,
        }

    async def update_resource_hash(self, project_id: str, resource_uri: str) -> dict[str, Any]:
        """重新计算并更新资源引用的 content_hash.

        Raises:
            ProjectNotFoundError: 项目不存在
            ResourceRefNotFoundError: 资源引用不存在
        """
        async with await self._get_session() as session:
            ref_stmt = select(ProjectResourceRef).where(
                ProjectResourceRef.project_id == project_id,
                ProjectResourceRef.resource_uri == resource_uri,
            )
            ref_orm = (await session.execute(ref_stmt)).scalar_one_or_none()
            if ref_orm is None:
                # 区分项目不存在 vs 资源不存在
                p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
                if (await session.execute(p_stmt)).scalar_one_or_none() is None:
                    raise ProjectNotFoundError(f"项目不存在: {project_id}")
                raise ResourceRefNotFoundError(f"资源引用不存在: {resource_uri}")

            old_hash = ref_orm.content_hash or ""
            new_hash = await self._compute_content_hash(ref_orm.resource_type, ref_orm.resource_uri)
            ref_orm.content_hash = new_hash or None
            await session.commit()

        return {
            "project_id": project_id,
            "resource_uri": resource_uri,
            "old_hash": old_hash,
            "new_hash": new_hash,
            "changed": old_hash != new_hash,
        }

    async def list_resource_refs(
        self,
        project_id: str,
        *,
        resource_type: str | None = None,
    ) -> dict[str, Any]:
        """列出项目的资源引用（可选按类型过滤）."""
        if resource_type is not None and not RESOURCE_TYPES.is_valid(resource_type):
            raise ValueError(f"resource_type 不支持: {resource_type}")

        async with await self._get_session() as session:
            # 校验项目存在
            p_stmt = select(ProjectRepo.project_id).where(ProjectRepo.project_id == project_id)
            if (await session.execute(p_stmt)).first() is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")

            stmt = select(ProjectResourceRef).where(ProjectResourceRef.project_id == project_id)
            if resource_type:
                stmt = stmt.where(ProjectResourceRef.resource_type == resource_type)
            stmt = stmt.order_by(ProjectResourceRef.created_at)
            refs = [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
            return {"project_id": project_id, "resource_refs": refs}
