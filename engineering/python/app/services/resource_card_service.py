"""资源卡片聚合服务层.

对应 ADR-012（资源卡片）。

职责：
    1. ModelArtifact CRUD：register_model / get_model / list_models / update_model /
       delete_model / append_model_metrics
    2. DatasetReadme CRUD：get_dataset_readme / upsert_dataset_readme / delete_dataset_readme
    3. 卡片聚合：get_dataset_card / get_model_card —— 调用 IDatasetStore /
       ILineageStore / ISnapshotStore 拼接
    4. lineage 摘要：get_lineage_summary —— 调用 ILineageStore.get_upstream /
       get_downstream，按层分组并裁剪到 max_depth，提取关键路径

线程安全：
    - 单例通过双重检查锁创建
    - 写操作（register/update/delete/upsert/append）按 resource_key 串行化，
      通过 _locks dict + _locks_guard 保护

错误处理风格（与 ProjectSyncService 对齐）：
    - 参数校验失败 → ValueError
    - 资源不存在 → LookupError 子类
    - 资源已存在 → ValueError 子类
    - 业务状态非法 → ResourceCardError 子类
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.contracts.resource_card import (
    DatasetCard,
    DatasetReadme,
    LineageSummary,
    ModelArtifact,
    ModelArtifactStatus,
    ModelArtifactType,
    ModelCard,
    VALID_MODEL_STATUS_TRANSITIONS,
)
from app.database.models.resource_card import (
    DatasetReadme as DatasetReadmeORM,
    ModelArtifact as ModelArtifactORM,
)
from app.services._shared.service_base import BaseSingletonService

from app.services._card_helpers import (
    _json_dumps,
    _orm_to_model_artifact,
    _orm_to_dataset_readme,
    _parse_iso_datetime,
    _build_layers,
    _collect_unique_nodes,
    _extract_key_path,
)

logger = logging.getLogger(__name__)


# 自定义异常层级（与 ProjectSyncService 对齐）


class ResourceCardError(RuntimeError):
    """资源卡片服务基类异常."""


class ModelArtifactNotFoundError(LookupError):
    """模型产物不存在."""


class ModelArtifactAlreadyExistsError(ValueError):
    """模型产物已存在（model_uri 唯一冲突）."""


class InvalidModelStatusTransitionError(ResourceCardError):
    """模型状态机非法转换."""


class DatasetReadmeNotFoundError(LookupError):
    """数据集 README 不存在."""


class LineageSummaryError(ResourceCardError):
    """lineage 摘要构建失败."""


# 单例


def get_resource_card_service() -> "ResourceCardService":
    """获取 ResourceCardService 单例（委托给 ``ResourceCardService.get_instance``）."""
    return ResourceCardService.get_instance()  # type: ignore[return-value]


def reset_resource_card_service() -> None:
    """重置单例（仅供测试，委托给 ``ResourceCardService.reset_instance``）."""
    ResourceCardService.reset_instance()


# 服务实现


class ResourceCardService(BaseSingletonService):
    """资源卡片聚合服务.

    内部组合 IDatasetStore / ILineageStore / ISnapshotStore 三个 store 单例，
    自身管理 model_artifacts + dataset_readmes 两张 ORM 表的持久化。

    设计原则：
        - 读操作无锁（store 内部已有自己的 session 管理）
        - 写操作按 resource_key 串行化（避免并发覆盖）
        - 卡片聚合调用三个 store 的 async 方法，本服务的 async 方法内不持有锁
          （锁只在 ORM 写操作前后短暂持有）
    """

    def __init__(self) -> None:
        # 按 resource_key 串行化写操作
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── 锁管理 ─────────────────────────────────────────────────────────

    def _get_lock(self, resource_key: str) -> threading.Lock:
        """获取或创建指定 resource_key 的锁（线程安全）."""
        # 先快速路径：大部分情况锁已存在
        lock = self._locks.get(resource_key)
        if lock is not None:
            return lock
        with self._locks_guard:
            lock = self._locks.get(resource_key)
            if lock is None:
                lock = threading.Lock()
                self._locks[resource_key] = lock
            return lock

    # ── ModelArtifact CRUD ────────────────────────────────────────────

    async def register_model(
        self,
        *,
        model_uri: str,
        name: str,
        model_type: str,
        version: str,
        framework: str,
        storage_uri: str,
        owner_id: str,
        readme_md: str = "",
        tags: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
        status: str = ModelArtifactStatus.DRAFT,
    ) -> ModelArtifact:
        """注册新模型产物.

        Args:
            model_uri: 模型 URI（model://<name>/<version>），必须全局唯一
            name: 显示名
            model_type: ModelArtifactType 常量
            version: semver 版本号
            framework: 框架版本（如 torch-2.1.0）
            storage_uri: 模型文件存储位置
            owner_id: 所有者
            readme_md: markdown README（可选）
            tags: 标签数组（可选）
            metrics: 初始指标快照（可选）
            status: 初始状态（默认 draft）

        Returns:
            ModelArtifact dataclass

        Raises:
            ValueError: 参数校验失败
            ModelArtifactAlreadyExistsError: model_uri 已存在
        """
        # 契约层校验（__post_init__ 会抛 ValueError）
        artifact = ModelArtifact(
            model_id="",  # 占位，DB 会自动生成
            model_uri=model_uri,
            name=name,
            model_type=model_type,
            version=version,
            framework=framework,
            storage_uri=storage_uri,
            owner_id=owner_id,
            status=status,
            metrics=metrics or {},
            metrics_history=[],
            readme_md=readme_md,
            tags=tags or [],
        )

        lock = self._get_lock(f"model:{model_uri}")
        with lock:
            async with await self._get_session() as session:
                # 检查唯一性
                existing = await session.execute(
                    select(ModelArtifactORM).where(ModelArtifactORM.model_uri == model_uri)
                )
                if existing.scalar_one_or_none() is not None:
                    raise ModelArtifactAlreadyExistsError(f"模型 URI 已存在: {model_uri}")

                orm = ModelArtifactORM(
                    model_uri=artifact.model_uri,
                    name=artifact.name,
                    model_type=artifact.model_type,
                    version=artifact.version,
                    framework=artifact.framework,
                    storage_uri=artifact.storage_uri,
                    metrics_json=_json_dumps(artifact.metrics),
                    metrics_history_json=_json_dumps(artifact.metrics_history),
                    readme_md=artifact.readme_md,
                    tags_json=_json_dumps(artifact.tags),
                    owner_id=artifact.owner_id,
                    status=artifact.status,
                )
                session.add(orm)
                try:
                    await session.commit()
                except IntegrityError as e:
                    await session.rollback()
                    raise ModelArtifactAlreadyExistsError(f"模型 URI 已存在: {model_uri}") from e

                # expire_on_commit=False，可直接访问 ORM 字段
                logger.info(
                    "Registered model artifact: %s (%s v%s)",
                    orm.id,
                    orm.name,
                    orm.version,
                )
                return _orm_to_model_artifact(orm)

    async def get_model(self, model_id: str) -> ModelArtifact:
        """获取模型产物（按 ID）.

        Raises:
            ModelArtifactNotFoundError: 模型不存在
        """
        async with await self._get_session() as session:
            result = await session.execute(select(ModelArtifactORM).where(ModelArtifactORM.id == model_id))
            orm = result.scalar_one_or_none()
            if orm is None:
                raise ModelArtifactNotFoundError(f"模型产物不存在: {model_id}")
            return _orm_to_model_artifact(orm)

    async def get_model_by_uri(self, model_uri: str) -> ModelArtifact:
        """获取模型产物（按 model_uri）.

        Raises:
            ModelArtifactNotFoundError: 模型不存在
        """
        async with await self._get_session() as session:
            result = await session.execute(select(ModelArtifactORM).where(ModelArtifactORM.model_uri == model_uri))
            orm = result.scalar_one_or_none()
            if orm is None:
                raise ModelArtifactNotFoundError(f"模型产物不存在（model_uri={model_uri}）")
            return _orm_to_model_artifact(orm)

    async def list_models(
        self,
        *,
        owner_id: str | None = None,
        model_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """列出模型产物（分页 + 过滤）.

        Returns:
            {"items": list[ModelArtifact], "total": int, "limit": int, "offset": int}
        """
        if limit < 1 or limit > 1000:
            raise ValueError(f"limit 越界（1-1000）: {limit}")
        if offset < 0:
            raise ValueError(f"offset 不能为负数: {offset}")
        if model_type is not None and not ModelArtifactType.is_valid(model_type):
            raise ValueError(f"model_type 不合法: {model_type}，合法值: {ModelArtifactType.all()}")
        if status is not None and not ModelArtifactStatus.is_valid(status):
            raise ValueError(f"status 不合法: {status}，合法值: {ModelArtifactStatus.all()}")

        async with await self._get_session() as session:
            # 构造查询
            stmt = select(ModelArtifactORM)
            count_stmt = select(func.count(ModelArtifactORM.id))
            if owner_id is not None:
                stmt = stmt.where(ModelArtifactORM.owner_id == owner_id)
                count_stmt = count_stmt.where(ModelArtifactORM.owner_id == owner_id)
            if model_type is not None:
                stmt = stmt.where(ModelArtifactORM.model_type == model_type)
                count_stmt = count_stmt.where(ModelArtifactORM.model_type == model_type)
            if status is not None:
                stmt = stmt.where(ModelArtifactORM.status == status)
                count_stmt = count_stmt.where(ModelArtifactORM.status == status)

            stmt = stmt.order_by(ModelArtifactORM.created_at.desc())
            stmt = stmt.limit(limit).offset(offset)

            result = await session.execute(stmt)
            orms = result.scalars().all()

            count_result = await session.execute(count_stmt)
            total = count_result.scalar() or 0

            return {
                "items": [_orm_to_model_artifact(orm) for orm in orms],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def update_model(
        self,
        model_id: str,
        *,
        readme_md: str | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        metrics: dict[str, Any] | None = None,
        framework: str | None = None,
        storage_uri: str | None = None,
    ) -> ModelArtifact:
        """更新模型卡片字段（部分更新，仅非 None 字段被写入）.

        status 转换受状态机约束（VALID_MODEL_STATUS_TRANSITIONS）。

        Raises:
            ModelArtifactNotFoundError: 模型不存在
            InvalidModelStatusTransitionError: 状态转换非法
            ValueError: 参数校验失败
        """
        if status is not None and not ModelArtifactStatus.is_valid(status):
            raise ValueError(f"status 不合法: {status}，合法值: {ModelArtifactStatus.all()}")
        if tags is not None and not isinstance(tags, list):
            raise ValueError(f"tags 必须是列表: {type(tags)}")

        lock = self._get_lock(f"model_id:{model_id}")
        with lock:
            async with await self._get_session() as session:
                result = await session.execute(select(ModelArtifactORM).where(ModelArtifactORM.id == model_id))
                orm = result.scalar_one_or_none()
                if orm is None:
                    raise ModelArtifactNotFoundError(f"模型产物不存在: {model_id}")

                # 状态机校验
                if status is not None and status != orm.status:
                    allowed = VALID_MODEL_STATUS_TRANSITIONS.get(orm.status, set())
                    if status not in allowed:
                        raise InvalidModelStatusTransitionError(
                            f"模型状态转换非法: {orm.status} → {status}，允许的目标状态: {sorted(allowed) or '<none>'}"
                        )
                    orm.status = status

                if readme_md is not None:
                    orm.readme_md = readme_md
                if tags is not None:
                    orm.tags_json = _json_dumps(tags)
                if metrics is not None:
                    orm.metrics_json = _json_dumps(metrics)
                if framework is not None:
                    orm.framework = framework
                if storage_uri is not None:
                    orm.storage_uri = storage_uri

                await session.commit()
                logger.info(
                    "Updated model artifact: %s (status=%s)",
                    orm.id,
                    orm.status,
                )
                return _orm_to_model_artifact(orm)

    async def delete_model(self, model_id: str) -> bool:
        """删除模型产物.

        Returns:
            True 表示已删除

        Raises:
            ModelArtifactNotFoundError: 模型不存在
        """
        lock = self._get_lock(f"model_id:{model_id}")
        with lock:
            async with await self._get_session() as session:
                result = await session.execute(select(ModelArtifactORM).where(ModelArtifactORM.id == model_id))
                orm = result.scalar_one_or_none()
                if orm is None:
                    raise ModelArtifactNotFoundError(f"模型产物不存在: {model_id}")
                await session.delete(orm)
                await session.commit()
                logger.info("Deleted model artifact: %s", model_id)
                return True

    async def append_model_metrics(
        self,
        model_id: str,
        metrics: dict[str, Any],
        *,
        timestamp: datetime | None = None,
    ) -> ModelArtifact:
        """追加一条指标记录到模型历史（同时更新当前指标快照）.

        Raises:
            ModelArtifactNotFoundError: 模型不存在
        """
        if not isinstance(metrics, dict):
            raise ValueError(f"metrics 必须是字典: {type(metrics)}")

        lock = self._get_lock(f"model_id:{model_id}")
        with lock:
            async with await self._get_session() as session:
                result = await session.execute(select(ModelArtifactORM).where(ModelArtifactORM.id == model_id))
                orm = result.scalar_one_or_none()
                if orm is None:
                    raise ModelArtifactNotFoundError(f"模型产物不存在: {model_id}")

                orm.append_metrics(metrics, timestamp=timestamp)
                await session.commit()
                logger.info(
                    "Appended metrics to model %s (history_len=%d)",
                    model_id,
                    len(orm.metrics_history),
                )
                return _orm_to_model_artifact(orm)

    # ── DatasetReadme CRUD ────────────────────────────────────────────

    async def get_dataset_readme(
        self,
        dataset_id: str,
        *,
        version: str | None = None,
    ) -> DatasetReadme | None:
        """获取数据集 README.

        优先取版本级（version 不为 None），回退到数据集级（version=None）。
        若两者都不存在，返回 None。

        Args:
            dataset_id: 数据集 ID
            version: 版本号，None 表示查数据集级 README

        Returns:
            DatasetReadme 或 None
        """
        async with await self._get_session() as session:
            # 优先查版本级
            if version is not None:
                result = await session.execute(
                    select(DatasetReadmeORM).where(
                        DatasetReadmeORM.dataset_id == dataset_id,
                        DatasetReadmeORM.version == version,
                    )
                )
                orm = result.scalar_one_or_none()
                if orm is not None:
                    return _orm_to_dataset_readme(orm)
                # 回退到数据集级
            # 查数据集级（version IS NULL）
            result = await session.execute(
                select(DatasetReadmeORM).where(
                    DatasetReadmeORM.dataset_id == dataset_id,
                    DatasetReadmeORM.version.is_(None),
                )
            )
            orm = result.scalar_one_or_none()
            if orm is None:
                return None
            return _orm_to_dataset_readme(orm)

    async def upsert_dataset_readme(
        self,
        dataset_id: str,
        readme_md: str,
        updated_by: str,
        *,
        version: str | None = None,
    ) -> DatasetReadme:
        """插入或更新数据集 README（按 dataset_id + version 唯一）.

        Raises:
            ValueError: 参数校验失败
        """
        if not dataset_id:
            raise ValueError("dataset_id 不能为空")
        if not readme_md:
            raise ValueError("readme_md 不能为空")
        if not updated_by:
            raise ValueError("updated_by 不能为空")

        # 契约层校验（version semver）
        DatasetReadme(
            readme_id="placeholder",
            dataset_id=dataset_id,
            readme_md=readme_md,
            updated_by=updated_by,
            version=version,
        )

        lock_key = f"readme:{dataset_id}:{version or '<dataset_level>'}"
        lock = self._get_lock(lock_key)
        with lock:
            async with await self._get_session() as session:
                # 查找现有
                if version is not None:
                    stmt = select(DatasetReadmeORM).where(
                        DatasetReadmeORM.dataset_id == dataset_id,
                        DatasetReadmeORM.version == version,
                    )
                else:
                    stmt = select(DatasetReadmeORM).where(
                        DatasetReadmeORM.dataset_id == dataset_id,
                        DatasetReadmeORM.version.is_(None),
                    )
                result = await session.execute(stmt)
                orm = result.scalar_one_or_none()

                if orm is None:
                    orm = DatasetReadmeORM(
                        dataset_id=dataset_id,
                        version=version,
                        readme_md=readme_md,
                        updated_by=updated_by,
                    )
                    session.add(orm)
                    logger.info(
                        "Created dataset readme: dataset=%s version=%s",
                        dataset_id,
                        version or "<dataset_level>",
                    )
                else:
                    orm.readme_md = readme_md
                    orm.updated_by = updated_by
                    logger.info(
                        "Updated dataset readme: dataset=%s version=%s",
                        dataset_id,
                        version or "<dataset_level>",
                    )

                try:
                    await session.commit()
                except IntegrityError as e:
                    await session.rollback()
                    raise ValueError(f"数据集 README 唯一约束冲突（dataset_id={dataset_id}, version={version}）") from e

                return _orm_to_dataset_readme(orm)

    async def delete_dataset_readme(
        self,
        dataset_id: str,
        *,
        version: str | None = None,
    ) -> bool:
        """删除数据集 README.

        Returns:
            True 表示已删除

        Raises:
            DatasetReadmeNotFoundError: README 不存在
        """
        lock_key = f"readme:{dataset_id}:{version or '<dataset_level>'}"
        lock = self._get_lock(lock_key)
        with lock:
            async with await self._get_session() as session:
                if version is not None:
                    stmt = select(DatasetReadmeORM).where(
                        DatasetReadmeORM.dataset_id == dataset_id,
                        DatasetReadmeORM.version == version,
                    )
                else:
                    stmt = select(DatasetReadmeORM).where(
                        DatasetReadmeORM.dataset_id == dataset_id,
                        DatasetReadmeORM.version.is_(None),
                    )
                result = await session.execute(stmt)
                orm = result.scalar_one_or_none()
                if orm is None:
                    raise DatasetReadmeNotFoundError(
                        f"数据集 README 不存在（dataset_id={dataset_id}, version={version}）"
                    )
                await session.delete(orm)
                await session.commit()
                logger.info(
                    "Deleted dataset readme: dataset=%s version=%s",
                    dataset_id,
                    version or "<dataset_level>",
                )
                return True

    # ── 卡片聚合 ──────────────────────────────────────────────────────

    async def get_dataset_card(
        self,
        dataset_id: str,
        *,
        include_lineage: bool = True,
        lineage_depth: int = 3,
    ) -> DatasetCard:
        """获取数据集卡片（聚合 Dataset + Version 指标 + README + lineage 摘要）.

        Raises:
            ValueError: 数据集不存在（透传 DatasetStore 异常）
        """
        from app.dependencies import get_dataset_store

        if lineage_depth < 1 or lineage_depth > 10:
            raise ValueError(f"lineage_depth 越界（1-10）: {lineage_depth}")

        dataset_store = get_dataset_store()

        # 获取数据集详情（dict，含 versions 列表）
        detail = await dataset_store.get_dataset(dataset_id)

        versions = detail.get("versions", [])
        version_count = len(versions)
        total_rows = sum(v.get("row_count", 0) for v in versions)
        total_size_bytes = sum(v.get("size_bytes", 0) for v in versions)
        latest_version = versions[0] if versions else None

        # 获取 README（优先版本级，回退数据集级）
        readme: DatasetReadme | None = None
        try:
            if latest_version is not None:
                readme = await self.get_dataset_readme(dataset_id, version=latest_version.get("version"))
            if readme is None:
                readme = await self.get_dataset_readme(dataset_id, version=None)
        except Exception as e:
            logger.warning(
                "Failed to fetch dataset readme (dataset=%s): %s",
                dataset_id,
                e,
            )
            readme = None

        # 获取 lineage 摘要
        lineage_summary: LineageSummary | None = None
        if include_lineage and latest_version is not None:
            target_uri = f"dataset://{dataset_id}/{latest_version.get('version', 'latest')}"
            try:
                lineage_summary = await self.get_lineage_summary(target_uri, max_depth=lineage_depth)
            except Exception as e:
                logger.warning(
                    "Failed to build lineage summary (target=%s): %s",
                    target_uri,
                    e,
                )
                lineage_summary = None

        return DatasetCard(
            dataset_id=detail["id"],
            name=detail["name"],
            description=detail.get("description", ""),
            owner_id=detail.get("owner_id", ""),
            status=detail.get("status", "draft"),
            schema=detail.get("schema", {}),
            version_count=version_count,
            total_rows=total_rows,
            total_size_bytes=total_size_bytes,
            latest_version=latest_version,
            readme=readme,
            lineage_summary=lineage_summary,
            created_at=_parse_iso_datetime(detail.get("created_at")),
            updated_at=_parse_iso_datetime(detail.get("updated_at")),
        )

    async def get_model_card(
        self,
        model_id: str,
        *,
        include_lineage: bool = True,
        lineage_depth: int = 3,
    ) -> ModelCard:
        """获取模型卡片（聚合 ModelArtifact + Snapshot 数 + lineage 摘要）.

        Raises:
            ModelArtifactNotFoundError: 模型不存在
        """
        from app.observability.snapshot import get_snapshot_store

        if lineage_depth < 1 or lineage_depth > 10:
            raise ValueError(f"lineage_depth 越界（1-10）: {lineage_depth}")

        # 获取模型产物
        artifact = await self.get_model(model_id)

        # 获取关联快照
        snapshot_count = 0
        latest_snapshot: dict[str, Any] | None = None
        try:
            snapshot_store = get_snapshot_store()
            snapshots = await snapshot_store.list(filters={"model_uri": artifact.model_uri})
            snapshot_count = len(snapshots)
            if snapshots:
                latest = snapshots[0]  # 已按 created_at desc 排序
                latest_snapshot = {
                    "snapshot_id": latest.snapshot_id,
                    "created_at": (latest.created_at.isoformat() if latest.created_at else None),
                    "created_by": latest.created_by,
                    "git_sha": latest.git_sha,
                    "metrics": dict(latest.metrics) if latest.metrics else {},
                    "mlflow_run_id": latest.mlflow_run_id,
                    "notes": latest.notes,
                }
        except Exception as e:
            logger.warning(
                "Failed to fetch snapshots (model_uri=%s): %s",
                artifact.model_uri,
                e,
            )

        # 获取 lineage 摘要
        lineage_summary: LineageSummary | None = None
        if include_lineage:
            try:
                lineage_summary = await self.get_lineage_summary(artifact.model_uri, max_depth=lineage_depth)
            except Exception as e:
                logger.warning(
                    "Failed to build lineage summary (target=%s): %s",
                    artifact.model_uri,
                    e,
                )

        logger.info(
            "Built model card: model_id=%s snapshots=%d",
            model_id,
            snapshot_count,
        )
        return ModelCard(
            model=artifact,
            snapshot_count=snapshot_count,
            lineage_summary=lineage_summary,
            latest_snapshot=latest_snapshot,
        )

    # ── Lineage 摘要 ──────────────────────────────────────────────────

    async def get_lineage_summary(
        self,
        target_uri: str,
        *,
        max_depth: int = 3,
        max_nodes_per_layer: int = 10,
    ) -> LineageSummary:
        """获取 lineage 摘要（按层分组 + 关键路径）.

        算法：
            1. 调用 ILineageStore.get_upstream / get_downstream（depth=max_depth）
            2. BFS 按层分组（每层最多 max_nodes_per_layer 个 URI）
            3. upstream_count / downstream_count 取全量计数（depth=50）
            4. key_path 取 target → 根的最短路径（BFS 第一个根节点回溯）

        Args:
            target_uri: 卡片目标的资源 URI
            max_depth: 每层返回的最大深度（默认 3）
            max_nodes_per_layer: 每层保留的最大节点数（默认 10）

        Returns:
            LineageSummary dataclass

        Raises:
            ValueError: 参数非法
            LineageSummaryError: lineage 查询失败
        """
        if not target_uri:
            raise ValueError("target_uri 不能为空")
        if max_depth < 1 or max_depth > 10:
            raise ValueError(f"max_depth 越界（1-10）: {max_depth}")
        if max_nodes_per_layer < 1 or max_nodes_per_layer > 100:
            raise ValueError(f"max_nodes_per_layer 越界（1-100）: {max_nodes_per_layer}")

        from app.data.lineage_store import get_lineage_store

        lineage_store = get_lineage_store()

        try:
            # 取近邻层（用于 layers 展示）
            upstream_records = await lineage_store.get_upstream(target_uri, depth=max_depth)
            downstream_records = await lineage_store.get_downstream(target_uri, depth=max_depth)

            # 取全量计数（depth 较大但不返回节点列表）
            upstream_full = await lineage_store.get_upstream(target_uri, depth=50)
            downstream_full = await lineage_store.get_downstream(target_uri, depth=50)
        except Exception as e:
            raise LineageSummaryError(f"lineage 查询失败 (target={target_uri}): {e}") from e

        # 构造 BFS 分层
        upstream_layers = _build_layers(
            target_uri,
            upstream_records,
            max_depth,
            max_nodes_per_layer,
            direction="upstream",
        )
        downstream_layers = _build_layers(
            target_uri,
            downstream_records,
            max_depth,
            max_nodes_per_layer,
            direction="downstream",
        )

        # 计算全量节点数（去重）
        upstream_nodes = _collect_unique_nodes(upstream_full, target_uri)
        downstream_nodes = _collect_unique_nodes(downstream_full, target_uri)
        upstream_count = len(upstream_nodes)
        downstream_count = len(downstream_nodes)
        total_nodes = upstream_count + downstream_count + 1  # +1 for target

        # 提取关键路径（target 根节点的最短路径）
        key_path = _extract_key_path(target_uri, upstream_full)

        return LineageSummary(
            target_uri=target_uri,
            upstream_count=upstream_count,
            downstream_count=downstream_count,
            upstream_layers=upstream_layers,
            downstream_layers=downstream_layers,
            key_path=key_path,
            total_nodes=total_nodes,
        )


# 辅助函数


__all__ = [
    # 服务
    "ResourceCardService",
    "get_resource_card_service",
    "reset_resource_card_service",
    # 异常
    "ResourceCardError",
    "ModelArtifactNotFoundError",
    "ModelArtifactAlreadyExistsError",
    "InvalidModelStatusTransitionError",
    "DatasetReadmeNotFoundError",
    "LineageSummaryError",
]
