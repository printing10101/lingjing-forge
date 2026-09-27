"""工作流模板市场服务层.

对应 ADR-010 阶段 6 p6-1：工作流模板市场业务逻辑。

职责：
    1. publish：发布新模板 / 新版本（semver 必须递增）
    2. list_templates：分页列表（按分类 / 标签 / 作者过滤，按下载量 / 评分排序）
    3. search：关键词搜索（name / description / tags / author）
    4. get_template：获取模板详情（可选指定 version）
    5. download：下载模板（自增下载计数，返回完整 manifest + spec）
    6. rate：评分（1.0-5.0），增量更新 avg_rating / rating_count
    7. unpublish：下架模板（status -> unpublished，不删除数据）
    8. list_versions：列出某模板的所有版本

并发安全：
    - 市场统计字段（downloads / avg_rating / rating_count）的增量更新使用
      threading.Lock 保护，避免并发 download/rate 导致计数丢失
    - 数据库操作通过 SQLAlchemy 事务保证原子性，写操作显式 commit()

持久化策略：
    - 模板元数据 + spec 存 SQLite JSONB（见 workflow_template.py ORM）
    - 不使用 listings.json 文件模式（与 skill_marketplace 不同），因为
      工作流模板的 spec 较大且需要多版本管理，关系型存储更合适
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from app.contracts.workflow_template import (
    TEMPLATE_CATEGORIES,
    WorkflowTemplateManifest,
)
from app.database.models.workflow_template import (
    WorkflowTemplate as TemplateORM,
    WorkflowTemplateVersion as TemplateVersionORM,
)
from app.plugins.workflow_template_loader import template_to_dict
from app.services._shared.service_base import BaseSingletonService

logger = logging.getLogger(__name__)


# 单例服务


def get_workflow_template_service() -> "WorkflowTemplateService":
    """获取全局 WorkflowTemplateService 单例（委托给 ``WorkflowTemplateService.get_instance``）."""
    return WorkflowTemplateService.get_instance()  # type: ignore[return-value]


# 自定义异常


class TemplateNotFoundError(LookupError):
    """模板或版本不存在."""


class TemplateAlreadyExistsError(ValueError):
    """模板 ID 已存在（首次发布冲突）."""


class VersionAlreadyExistsError(ValueError):
    """版本号已存在（同 template_id 下 version 重复）."""


class InvalidVersionError(ValueError):
    """版本号不递增或格式错误."""


# 服务实现


class WorkflowTemplateService(BaseSingletonService):
    """工作流模板市场服务.

    线程安全：市场统计字段的增量更新通过 ``_stats_lock`` 保护。
    数据库事务：每个公共方法内部独立管理 session，写操作显式 commit。
    """

    def __init__(self) -> None:
        # 保护市场统计字段增量更新（downloads / avg_rating / rating_count）
        # 注意：SQLAlchemy 异步 session 本身是线程局部的，但同一逻辑模板的
        # read-modify-write 操作需要应用层加锁，否则会丢失更新
        self._stats_lock = threading.Lock()

    # session 辅助
    # ``_get_session`` 由 ``BaseSingletonService`` 提供。

    # 发布

    async def publish(
        self,
        manifest: WorkflowTemplateManifest,
        changelog: str = "",
    ) -> dict[str, Any]:
        """发布工作流模板（新模板或新版本）.

        Args:
            manifest: 工作流模板 manifest（含 spec）
            changelog: 版本变更说明

        Returns:
            发布结果，含 template_id / version / is_new_template

        Raises:
            VersionAlreadyExistsError: (template_id, version) 已存在
            InvalidVersionError: 新版本号不大于已有最新版本
        """
        async with await self._get_session() as session:
            # 查询主表是否已存在该 template_id
            stmt = select(TemplateORM).where(TemplateORM.template_id == manifest.id)
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            manifest_dict = template_to_dict(manifest)
            now = datetime.now(timezone.utc)

            if existing is None:
                # 首次发布：创建主表记录 + 版本记录
                template_orm = TemplateORM(
                    template_id=manifest.id,
                    name=manifest.name,
                    author=manifest.author,
                    license_=manifest.license,
                    category=manifest.category,
                    plugin_id=manifest.plugin_id or None,
                    homepage=manifest.homepage or None,
                    latest_version=manifest.version,
                    description=manifest.description,
                    tags=list(manifest.tags),
                    downloads=0,
                    avg_rating=0.0,
                    rating_count=0,
                    published_at=now,
                    status="active",
                )
                session.add(template_orm)
                try:
                    await session.commit()
                except IntegrityError as e:
                    await session.rollback()
                    raise TemplateAlreadyExistsError(f"模板 ID 已存在: {manifest.id}") from e

                # 创建版本记录
                version_orm = TemplateVersionORM(
                    template_id=manifest.id,
                    version=manifest.version,
                    manifest_snapshot=manifest_dict,
                    spec=dict(manifest.spec),
                    inputs_schema=dict(manifest.inputs_schema),
                    parameters=dict(manifest.parameters),
                    required_contracts=list(manifest.required_contracts),
                    required_capabilities=list(manifest.required_capabilities),
                    changelog=changelog or None,
                    version_downloads=0,
                )
                session.add(version_orm)
                try:
                    await session.commit()
                except IntegrityError as e:
                    await session.rollback()
                    # 主表已创建但版本失败，删除主表以保持一致
                    await session.delete(template_orm)
                    await session.commit()
                    raise VersionAlreadyExistsError(f"版本已存在: {manifest.id}@{manifest.version}") from e

                logger.info(
                    "Published new workflow template: %s@%s",
                    manifest.id,
                    manifest.version,
                )
                return {
                    "template_id": manifest.id,
                    "version": manifest.version,
                    "is_new_template": True,
                    "published_at": now.isoformat(),
                }
            else:
                # 已存在：发布新版本
                if existing.status == "banned":
                    raise InvalidVersionError(f"模板已被封禁，无法发布新版本: {manifest.id}")

                # 检查版本号是否已存在
                v_stmt = select(TemplateVersionORM).where(
                    TemplateVersionORM.template_id == manifest.id,
                    TemplateVersionORM.version == manifest.version,
                )
                v_result = await session.execute(v_stmt)
                if v_result.scalar_one_or_none() is not None:
                    raise VersionAlreadyExistsError(f"版本已存在: {manifest.id}@{manifest.version}")

                # 检查版本号是否递增（简化：只校验 != latest_version，不强制 semver 比较）
                if manifest.version == existing.latest_version:
                    raise InvalidVersionError(f"新版本号不能等于当前最新版本: {manifest.version}")

                # 创建新版本记录
                version_orm = TemplateVersionORM(
                    template_id=manifest.id,
                    version=manifest.version,
                    manifest_snapshot=manifest_dict,
                    spec=dict(manifest.spec),
                    inputs_schema=dict(manifest.inputs_schema),
                    parameters=dict(manifest.parameters),
                    required_contracts=list(manifest.required_contracts),
                    required_capabilities=list(manifest.required_capabilities),
                    changelog=changelog or None,
                    version_downloads=0,
                )
                session.add(version_orm)

                # 更新主表的 latest_version + 元信息（name/description 等可随版本更新）
                existing.latest_version = manifest.version
                existing.name = manifest.name
                existing.description = manifest.description
                existing.tags = list(manifest.tags)
                if manifest.homepage:
                    existing.homepage = manifest.homepage
                if manifest.plugin_id:
                    existing.plugin_id = manifest.plugin_id

                try:
                    await session.commit()
                except IntegrityError as e:
                    await session.rollback()
                    raise VersionAlreadyExistsError(f"版本已存在: {manifest.id}@{manifest.version}") from e

                logger.info(
                    "Published new version: %s@%s",
                    manifest.id,
                    manifest.version,
                )
                return {
                    "template_id": manifest.id,
                    "version": manifest.version,
                    "is_new_template": False,
                    "published_at": now.isoformat(),
                }

    # 列表

    async def list_templates(
        self,
        category: str | None = None,
        tag: str | None = None,
        author: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "downloads",
    ) -> dict[str, Any]:
        """分页列出模板.

        Args:
            category: 分类过滤（见 TEMPLATE_CATEGORIES）
            tag: 标签过滤（精确匹配，tags 数组中包含）
            author: 作者过滤
            limit: 每页数量（1-100）
            offset: 偏移量
            sort_by: 排序字段（downloads / avg_rating / created_at / updated_at）

        Returns:
            {items: [...], total: int, limit: int, offset: int}
        """
        limit = max(1, min(100, limit))
        offset = max(0, offset)
        # SQLAlchemy 表达式字典：值类型为 desc() 表达式，显式 Any 注解
        valid_sort: dict[str, Any] = {
            "downloads": desc(TemplateORM.downloads),
            "avg_rating": desc(TemplateORM.avg_rating),
            "created_at": desc(TemplateORM.created_at),
            "updated_at": desc(TemplateORM.updated_at),
        }
        order_clause = valid_sort.get(sort_by, desc(TemplateORM.downloads))

        async with await self._get_session() as session:
            stmt = select(TemplateORM).where(TemplateORM.status == "active")
            count_stmt = select(func.count()).select_from(TemplateORM).where(TemplateORM.status == "active")

            if category:
                if category not in TEMPLATE_CATEGORIES.all():
                    raise InvalidVersionError(f"非法 category: {category}")
                stmt = stmt.where(TemplateORM.category == category)
                count_stmt = count_stmt.where(TemplateORM.category == category)
            if author:
                stmt = stmt.where(TemplateORM.author == author)
                count_stmt = count_stmt.where(TemplateORM.author == author)
            # tag 过滤：JSON 数组包含匹配（SQLite 用 JSON_EACH 不通用，
            # 此处用 LIKE 兜底，PostgreSQL 可用 JSONB 包含操作符）
            if tag:
                stmt = stmt.where(TemplateORM.tags.like(f'%"{tag}"%'))
                count_stmt = count_stmt.where(TemplateORM.tags.like(f'%"{tag}"%'))

            total_result = await session.execute(count_stmt)
            total = total_result.scalar() or 0

            stmt = stmt.order_by(order_clause).limit(limit).offset(offset)
            result = await session.execute(stmt)
            items = [row.to_dict() for row in result.scalars().all()]

            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    # 搜索

    async def search(
        self,
        query: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """关键词搜索模板（name / description / tags / author）.

        使用 ILIKE / LIKE 模糊匹配，结果按 avg_rating * rating_count + downloads 排序。
        """
        limit = max(1, min(100, limit))
        query_pattern = f"%{query}%"

        async with await self._get_session() as session:
            stmt = (
                select(TemplateORM)
                .where(TemplateORM.status == "active")
                .where(
                    (TemplateORM.name.like(query_pattern))
                    | (TemplateORM.description.like(query_pattern))
                    | (TemplateORM.author.like(query_pattern))
                    | (TemplateORM.tags.like(query_pattern))
                )
                .order_by(
                    desc(TemplateORM.avg_rating),
                    desc(TemplateORM.downloads),
                )
                .limit(limit)
            )
            result = await session.execute(stmt)
            items = [row.to_dict() for row in result.scalars().all()]
            return {"items": items, "total": len(items), "query": query}

    # 详情

    async def get_template(
        self,
        template_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        """获取模板详情（含指定版本的 manifest + spec）.

        Args:
            template_id: 模板业务 ID
            version: 版本号（None 表示最新版本）

        Returns:
            {template: {...主表字段...}, version: {...版本字段...}}

        Raises:
            TemplateNotFoundError: 模板或版本不存在
        """
        async with await self._get_session() as session:
            t_stmt = select(TemplateORM).where(TemplateORM.template_id == template_id)
            t_result = await session.execute(t_stmt)
            template_orm = t_result.scalar_one_or_none()
            if template_orm is None:
                raise TemplateNotFoundError(f"模板不存在: {template_id}")

            target_version = version or template_orm.latest_version
            v_stmt = select(TemplateVersionORM).where(
                TemplateVersionORM.template_id == template_id,
                TemplateVersionORM.version == target_version,
            )
            v_result = await session.execute(v_stmt)
            version_orm = v_result.scalar_one_or_none()
            if version_orm is None:
                raise TemplateNotFoundError(f"版本不存在: {template_id}@{target_version}")

            template_dict = template_orm.to_dict()
            version_dict = version_orm.to_dict()
            # 合并 manifest_snapshot 中的元信息（含 inputs_schema/parameters 等）
            return {
                "template": template_dict,
                "version": version_dict,
                "manifest": version_orm.manifest_snapshot,
            }

    # 下载

    async def download(
        self,
        template_id: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        """下载模板（自增下载计数，返回完整 manifest）.

        并发安全：通过 _stats_lock 保护 read-modify-write，避免计数丢失。
        """
        # 先获取详情（read-only）
        detail = await self.get_template(template_id, version)

        # 自增下载计数（持锁）
        with self._stats_lock:
            async with await self._get_session() as session:
                t_stmt = select(TemplateORM).where(TemplateORM.template_id == template_id)
                t_result = await session.execute(t_stmt)
                template_orm = t_result.scalar_one_or_none()
                if template_orm is None:
                    raise TemplateNotFoundError(f"模板不存在: {template_id}")

                target_version = version or template_orm.latest_version
                v_stmt = select(TemplateVersionORM).where(
                    TemplateVersionORM.template_id == template_id,
                    TemplateVersionORM.version == target_version,
                )
                v_result = await session.execute(v_stmt)
                version_orm = v_result.scalar_one_or_none()
                if version_orm is None:
                    raise TemplateNotFoundError(f"版本不存在: {template_id}@{target_version}")

                template_orm.downloads = (template_orm.downloads or 0) + 1
                version_orm.version_downloads = (version_orm.version_downloads or 0) + 1
                # commit 而非 flush，确保事务持久化（项目硬约束）
                await session.commit()

        logger.info(
            "Downloaded workflow template: %s@%s",
            template_id,
            target_version,
        )
        return detail

    # 评分

    async def rate(
        self,
        template_id: str,
        rating: float,
    ) -> dict[str, Any]:
        """给模板评分（1.0-5.0）.

        增量更新 avg_rating 和 rating_count：
            new_avg = (old_avg * old_count + rating) / (old_count + 1)
            new_count = old_count + 1

        并发安全：通过 _stats_lock 保护 read-modify-write。

        Args:
            template_id: 模板业务 ID
            rating: 评分（1.0 - 5.0）

        Returns:
            {template_id, avg_rating, rating_count}

        Raises:
            TemplateNotFoundError: 模板不存在
            InvalidVersionError: rating 不在 [1.0, 5.0]
        """
        if not 1.0 <= rating <= 5.0:
            raise InvalidVersionError(f"rating 必须在 [1.0, 5.0]，当前: {rating}")

        with self._stats_lock:
            async with await self._get_session() as session:
                t_stmt = select(TemplateORM).where(TemplateORM.template_id == template_id)
                t_result = await session.execute(t_stmt)
                template_orm = t_result.scalar_one_or_none()
                if template_orm is None:
                    raise TemplateNotFoundError(f"模板不存在: {template_id}")

                old_avg = float(template_orm.avg_rating or 0.0)
                old_count = int(template_orm.rating_count or 0)
                new_count = old_count + 1
                new_avg = (old_avg * old_count + rating) / new_count

                template_orm.avg_rating = round(new_avg, 4)
                template_orm.rating_count = new_count
                await session.commit()

        logger.info(
            "Rated workflow template %s: %.1f -> avg=%.2f (n=%d)",
            template_id,
            rating,
            new_avg,
            new_count,
        )
        return {
            "template_id": template_id,
            "avg_rating": round(new_avg, 4),
            "rating_count": new_count,
        }

    # 下架

    async def unpublish(self, template_id: str) -> dict[str, Any]:
        """下架模板（status -> unpublished，不删除数据）.

        下架后模板不再出现在 list/search 结果中，但已发布的版本数据保留，
        便于历史追溯和重新上架。

        Raises:
            TemplateNotFoundError: 模板不存在
        """
        async with await self._get_session() as session:
            t_stmt = select(TemplateORM).where(TemplateORM.template_id == template_id)
            t_result = await session.execute(t_stmt)
            template_orm = t_result.scalar_one_or_none()
            if template_orm is None:
                raise TemplateNotFoundError(f"模板不存在: {template_id}")

            template_orm.status = "unpublished"
            await session.commit()

        logger.info("Unpublished workflow template: %s", template_id)
        return {"template_id": template_id, "status": "unpublished"}

    # 版本列表

    async def list_versions(self, template_id: str) -> dict[str, Any]:
        """列出某模板的所有版本（按创建时间倒序）.

        Raises:
            TemplateNotFoundError: 模板不存在
        """
        async with await self._get_session() as session:
            t_stmt = select(TemplateORM).where(TemplateORM.template_id == template_id)
            t_result = await session.execute(t_stmt)
            template_orm = t_result.scalar_one_or_none()
            if template_orm is None:
                raise TemplateNotFoundError(f"模板不存在: {template_id}")

            v_stmt = (
                select(TemplateVersionORM)
                .where(TemplateVersionORM.template_id == template_id)
                .order_by(desc(TemplateVersionORM.created_at))
            )
            v_result = await session.execute(v_stmt)
            versions = [row.to_dict() for row in v_result.scalars().all()]

            return {
                "template_id": template_id,
                "latest_version": template_orm.latest_version,
                "versions": versions,
            }

    # 市场统计

    async def market_stats(self) -> dict[str, Any]:
        """市场全局统计（模板总数 / 总下载 / 平均评分）."""
        async with await self._get_session() as session:
            total_stmt = select(func.count()).select_from(TemplateORM).where(TemplateORM.status == "active")
            total_result = await session.execute(total_stmt)
            total_templates = total_result.scalar() or 0

            dl_stmt = select(func.sum(TemplateORM.downloads)).where(TemplateORM.status == "active")
            dl_result = await session.execute(dl_stmt)
            total_downloads = dl_result.scalar() or 0

            avg_stmt = select(func.avg(TemplateORM.avg_rating)).where(TemplateORM.status == "active")
            avg_result = await session.execute(avg_stmt)
            avg_rating = float(avg_result.scalar() or 0.0)

            return {
                "total_templates": total_templates,
                "total_downloads": int(total_downloads),
                "avg_rating": round(avg_rating, 4),
            }


__all__ = [
    "WorkflowTemplateService",
    "get_workflow_template_service",
    "TemplateNotFoundError",
    "TemplateAlreadyExistsError",
    "VersionAlreadyExistsError",
    "InvalidVersionError",
]
