"""IDatasetStore 的 SQLite + 文件系统实现.

对应 ADR-005 阶段 2 / core-contracts-design.md 第 4 章。

存储策略：
    - 元数据（dataset / version 记录）存 SQLite，通过 SQLAlchemy 异步 ORM
    - 内容（records 数据）存文件系统，按 content_hash 内容寻址
      路径格式：``<base_dir>/<hash[:2]>/<hash[2:4]>/<hash>.jsonl``
      相同内容只存一份（去重），storage_uri 用 ``file://`` 前缀

content_hash 计算：
    sha256(canonical_json(records))，其中 canonical_json 使用 sort_keys=True
    确保不同字段顺序的相同内容得到相同 hash。

版本语义：
    - version=None 时自动递增 patch（基于该 dataset 已有最新版本）
    - version 显式指定时必须为合法 semver 且未冲突
    - 一旦 PUBLISHED 即不可修改，只能 deprecate/archive

状态转换：
    DRAFT → PUBLISHED → DEPRECATED → ARCHIVED
    本实现 commit_version 直接以 PUBLISHED 状态写入（不可变快照语义）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, cast
from collections.abc import AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.dataset import (
    DatasetSchema,
    DatasetStatus,
    DatasetVersion as DatasetVersionContract,
    IDatasetStore,
    LineageRecord,
    VALID_DATASET_STATUS_TRANSITIONS,
)
from app.database.connection import get_sessionmaker
from app.database.models.dataset import (
    Dataset as DatasetORM,
    DatasetVersion as DatasetVersionORM,
)

logger = logging.getLogger(__name__)

# 默认内容存储根目录：与 data/training_data/ 平级
_DEFAULT_BASE_DIR = "data/datasets"


def _get_base_dir() -> Path:
    """获取内容存储根目录.

    受环境变量 ``DATASET_STORE_DIR`` 控制，默认 ``data/datasets``。
    多实例部署时需指向共享存储。
    """
    return Path(os.getenv("DATASET_STORE_DIR", _DEFAULT_BASE_DIR)).resolve()


def _compute_content_hash(records: list[dict[str, Any]]) -> str:
    """计算 records 的 sha256（canonical JSON，sort_keys）."""
    canonical = json.dumps(records, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _storage_path_for_hash(content_hash: str, base_dir: Path | None = None) -> Path:
    """根据 content_hash 计算分片存储路径."""
    root = base_dir if base_dir is not None else _get_base_dir()
    return root / content_hash[:2] / content_hash[2:4] / f"{content_hash}.jsonl"


def _storage_uri_for_hash(content_hash: str) -> str:
    """生成 file:// URI."""
    path = _storage_path_for_hash(content_hash)
    return f"file:///{path.as_posix()}"


def _parse_file_uri(uri: str) -> Path:
    """解析 file:// URI 为本地路径。

    Windows 兼容：``file:///C:/x/y`` 的 URI path 段为 ``/C:/x/y``，
    直接 ``Path`` 会解析为 ；需去掉前导斜杠保留盘符。
    """
    p = uri[len("file://") :]
    if len(p) >= 3 and p[0] == "/" and p[2] == ":":
        p = p[1:]
    return Path(p)


def _write_records(content_hash: str, records: list[dict[str, Any]]) -> tuple[Path, int]:
    """将 records 以 JSONL 写入内容寻址路径，返回 (path, size_bytes).

    若文件已存在（相同 hash），直接返回，不重复写入（去重）。
    """
    path = _storage_path_for_hash(content_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False, default=str))
                f.write("\n")
    size_bytes = path.stat().st_size
    return path, size_bytes


def _read_records_from_path(path: Path, batch_size: int) -> list[list[dict[str, Any]]]:
    """同步分批读取 JSONL，返回 batch 列表."""
    batches: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            batch.append(json.loads(line))
            if len(batch) >= batch_size:
                batches.append(batch)
                batch = []
    if batch:
        batches.append(batch)
    return batches


# ORM Contract 转换


def _schema_to_json(schema: DatasetSchema) -> str:
    return json.dumps(
        {
            "fields": schema.fields,
            "primary_key": schema.primary_key,
            "metadata": schema.metadata,
        },
        ensure_ascii=False,
    )


def _schema_from_json(raw: str) -> DatasetSchema:
    data = json.loads(raw)
    return DatasetSchema(
        fields=data.get("fields", {}),
        primary_key=data.get("primary_key", []),
        metadata=data.get("metadata", {}),
    )


def _version_orm_to_contract(orm: DatasetVersionORM, schema: DatasetSchema) -> DatasetVersionContract:
    return DatasetVersionContract(
        dataset_id=str(orm.dataset_id),
        version=str(orm.version),
        status=DatasetStatus(str(orm.status)),
        schema=schema,
        content_hash=str(orm.content_hash),
        row_count=int(orm.row_count),
        size_bytes=int(orm.size_bytes),
        created_at=cast(datetime, orm.created_at),  # ORM nullable=False
        created_by=str(orm.created_by),
        storage_uri=str(orm.storage_uri),
        lineage=str(orm.lineage_record_id) if orm.lineage_record_id else None,
    )


def _bump_patch_version(latest_version: str | None) -> str:
    """基于 latest_version 递增 patch 段，返回新 semver.

    latest_version=None 时返回 "0.0.1"。
    """
    if not latest_version:
        return "0.0.1"
    main = latest_version.split("-", 1)[0]
    parts = main.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return "0.0.1"
    major, minor, patch = (int(p) for p in parts)
    return f"{major}.{minor}.{patch + 1}"


class DatasetStore(IDatasetStore):
    """IDatasetStore 默认实现：SQLite 元数据 + 文件系统内容."""

    async def _get_session(self) -> AsyncSession:
        sessionmaker = get_sessionmaker()
        if sessionmaker is None:
            raise RuntimeError("数据库未配置，无法获取 session")
        return sessionmaker()

    async def create(
        self,
        name: str,
        schema: DatasetSchema,
        *,
        owner_id: str,
        description: str = "",
    ) -> str:
        """创建数据集（返回 dataset_id）。初始状态 DRAFT，无版本。"""
        if not name:
            raise ValueError("数据集 name 不能为空")
        if not owner_id:
            raise ValueError("owner_id 不能为空")
        schema_errors = schema.validate()
        if schema_errors:
            raise ValueError(f"DatasetSchema 校验失败: {schema_errors}")

        async with await self._get_session() as session:
            # 唯一名校验
            existing = await session.execute(select(DatasetORM).where(DatasetORM.name == name))
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"数据集 name 已存在: {name}")

            orm = DatasetORM(
                name=name,
                description=description,
                schema_json=_schema_to_json(schema),
                owner_id=owner_id,
                status=DatasetStatus.DRAFT.value,
            )
            session.add(orm)
            await session.commit()
            logger.info("数据集已创建: id=%s name=%s owner=%s", orm.id, name, owner_id)
            return str(orm.id)

    async def commit_version(
        self,
        dataset_id: str,
        records: list[dict[str, Any]],
        *,
        version: str | None = None,
        lineage: LineageRecord | None = None,
    ) -> DatasetVersionContract:
        """提交一个不可变版本（PUBLISHED）。"""
        if not dataset_id:
            raise ValueError("dataset_id 不能为空")

        # 血缘必须先独立落库：dataset_versions.lineage_record_id 带
        # ForeignKey(lineage_records.id)，历史上只把 id 字符串写进版本行、
        # 从不持久化 LineageRecord 本体 → 插入版本必炸 FOREIGN KEY
        # constraint failed（2026-09 修复）。
        if lineage is not None:
            from app.data.lineage_store import get_lineage_store

            await get_lineage_store().record(lineage)
            lineage_id: str | None = lineage.record_id
        else:
            lineage_id = None

        async with await self._get_session() as session:
            ds_orm = await session.execute(select(DatasetORM).where(DatasetORM.id == dataset_id))
            dataset = ds_orm.scalar_one_or_none()
            if dataset is None:
                raise ValueError(f"数据集不存在: {dataset_id}")

            # 查找已有最新版本，用于自动递增与冲突检测
            existing_stmt = (
                select(DatasetVersionORM)
                .where(DatasetVersionORM.dataset_id == dataset_id)
                .order_by(DatasetVersionORM.created_at.desc())
            )
            existing_result = await session.execute(existing_stmt)
            existing_versions = existing_result.scalars().all()

            latest_version_str = str(existing_versions[0].version) if existing_versions else None
            if version is None:
                resolved_version = _bump_patch_version(latest_version_str)
            else:
                resolved_version = version
                for v in existing_versions:
                    if str(v.version) == resolved_version:
                        raise ValueError(f"版本已存在: dataset_id={dataset_id} version={resolved_version}")

            # 计算 content_hash 并写入文件系统
            content_hash = _compute_content_hash(records)
            _, size_bytes = _write_records(content_hash, records)
            storage_uri = _storage_uri_for_hash(content_hash)

            # lineage 关联（已在上方面持久化，此处仅引用 id）
            orm = DatasetVersionORM(
                dataset_id=dataset_id,
                version=resolved_version,
                status=DatasetStatus.PUBLISHED.value,
                content_hash=content_hash,
                row_count=len(records),
                size_bytes=size_bytes,
                storage_uri=storage_uri,
                lineage_record_id=lineage_id,
                created_by=dataset.owner_id,
            )
            session.add(orm)

            # 若 dataset 仍为 DRAFT，提交版本后升级为 PUBLISHED
            if dataset.status == DatasetStatus.DRAFT.value:
                dataset.status = DatasetStatus.PUBLISHED.value  # type: ignore[assignment]

            await session.commit()
            logger.info(
                "数据集版本已提交: dataset_id=%s version=%s hash=%s rows=%d",
                dataset_id,
                resolved_version,
                content_hash[:12],
                len(records),
            )

            schema = _schema_from_json(str(dataset.schema_json))
            return _version_orm_to_contract(orm, schema)

    async def get_version(self, dataset_id: str, version: str | None = None) -> DatasetVersionContract:
        """获取版本。version=None 返回最新 published 版本。"""
        async with await self._get_session() as session:
            ds_orm = await session.execute(select(DatasetORM).where(DatasetORM.id == dataset_id))
            dataset = ds_orm.scalar_one_or_none()
            if dataset is None:
                raise ValueError(f"数据集不存在: {dataset_id}")

            schema = _schema_from_json(str(dataset.schema_json))

            if version is None:
                stmt = (
                    select(DatasetVersionORM)
                    .where(DatasetVersionORM.dataset_id == dataset_id)
                    .order_by(DatasetVersionORM.created_at.desc())
                )
            else:
                stmt = select(DatasetVersionORM).where(
                    DatasetVersionORM.dataset_id == dataset_id,
                    DatasetVersionORM.version == version,
                )

            result = await session.execute(stmt)
            if version is None:
                # 取最新（任意状态）
                orm = result.scalars().first()
            else:
                orm = result.scalar_one_or_none()

            if orm is None:
                if version is None:
                    raise ValueError(f"数据集无任何版本: {dataset_id}")
                raise ValueError(f"版本不存在: dataset_id={dataset_id} version={version}")

            return _version_orm_to_contract(orm, schema)

    async def read(
        self,
        dataset_id: str,
        version: str | None = None,
        *,
        batch_size: int = 1000,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """流式读取数据集版本内容（按 batch_size 分批）。

        使用 async generator 包装同步文件读取；每批之间 await asyncio.sleep(0)
        让出事件循环，避免大文件阻塞协程调度。
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size 必须为正数: {batch_size}")

        ver = await self.get_version(dataset_id, version)
        # storage_uri 形如 file:///abs/path/to/hash.jsonl
        if not ver.storage_uri.startswith("file://"):
            raise ValueError(f"不支持的 storage_uri scheme（仅支持 file://）: {ver.storage_uri}")
        path = _parse_file_uri(ver.storage_uri)
        if not path.exists():
            raise FileNotFoundError(f"数据集内容文件不存在（可能已被归档清理）: {path}")

        # 同步读取分批结果，然后逐批 yield（批间让出事件循环）
        batches = await asyncio.to_thread(_read_records_from_path, path, batch_size)
        for batch in batches:
            yield batch
            await asyncio.sleep(0)

    async def list_versions(self, dataset_id: str) -> list[DatasetVersionContract]:
        """列出数据集的所有版本（按创建时间倒序）。"""
        async with await self._get_session() as session:
            ds_orm = await session.execute(select(DatasetORM).where(DatasetORM.id == dataset_id))
            dataset = ds_orm.scalar_one_or_none()
            if dataset is None:
                raise ValueError(f"数据集不存在: {dataset_id}")

            schema = _schema_from_json(str(dataset.schema_json))
            stmt = (
                select(DatasetVersionORM)
                .where(DatasetVersionORM.dataset_id == dataset_id)
                .order_by(DatasetVersionORM.created_at.desc())
            )
            result = await session.execute(stmt)
            return [_version_orm_to_contract(orm, schema) for orm in result.scalars().all()]

    async def deprecate(self, dataset_id: str, version: str) -> None:
        """将版本标记为 DEPRECATED（不可逆，但内容仍可读）。"""
        async with await self._get_session() as session:
            stmt = select(DatasetVersionORM).where(
                DatasetVersionORM.dataset_id == dataset_id,
                DatasetVersionORM.version == version,
            )
            result = await session.execute(stmt)
            orm = result.scalar_one_or_none()
            if orm is None:
                raise ValueError(f"版本不存在: dataset_id={dataset_id} version={version}")

            current = DatasetStatus(orm.status)
            if DatasetStatus.DEPRECATED not in VALID_DATASET_STATUS_TRANSITIONS.get(current, set()):
                raise ValueError(f"非法状态转换: {current.value} → deprecated")

            orm.status = DatasetStatus.DEPRECATED.value  # type: ignore[assignment]
            await session.commit()
            logger.info(
                "数据集版本已 deprecate: dataset_id=%s version=%s",
                dataset_id,
                version,
            )

    # 扩展方法（非契约，但 API 路由与 UI 需要）

    async def list_datasets(
        self,
        *,
        owner_id: str | None = None,
        status: DatasetStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出数据集（轻量元信息，避免加载完整 schema）.

        返回字典列表（非 dataclass），仅含 UI 列表所需字段：
            id / name / description / owner_id / status / created_at / updated_at /
            version_count / latest_version
        """
        if limit <= 0 or limit > 1000:
            raise ValueError(f"limit 必须在 [1, 1000]: {limit}")
        if offset < 0:
            raise ValueError(f"offset 不能为负数: {offset}")

        async with await self._get_session() as session:
            stmt = select(DatasetORM).order_by(DatasetORM.created_at.desc())
            if owner_id is not None:
                stmt = stmt.where(DatasetORM.owner_id == owner_id)
            if status is not None:
                stmt = stmt.where(DatasetORM.status == status.value)
            stmt = stmt.offset(offset).limit(limit)
            result = await session.execute(stmt)
            orms = result.scalars().all()

            items: list[dict[str, Any]] = []
            for orm in orms:
                # versions 关系 lazy=selectin，已自动加载
                versions = sorted(orm.versions, key=lambda v: v.created_at, reverse=True)
                latest = versions[0] if versions else None
                items.append(
                    {
                        "id": orm.id,
                        "name": orm.name,
                        "description": orm.description,
                        "owner_id": orm.owner_id,
                        "status": orm.status,
                        "created_at": orm.created_at.isoformat() if orm.created_at else None,
                        "updated_at": orm.updated_at.isoformat() if orm.updated_at else None,
                        "version_count": len(orm.versions),
                        "latest_version": latest.version if latest else None,
                    }
                )
            return items

    async def count_datasets(
        self,
        *,
        owner_id: str | None = None,
        status: DatasetStatus | None = None,
    ) -> int:
        """统计数据集数量（与 list_datasets 同一组过滤条件）。"""
        async with await self._get_session() as session:
            stmt = select(func.count()).select_from(DatasetORM)
            if owner_id is not None:
                stmt = stmt.where(DatasetORM.owner_id == owner_id)
            if status is not None:
                stmt = stmt.where(DatasetORM.status == status.value)
            result = await session.execute(stmt)
            return int(result.scalar_one())

    async def get_metrics(self) -> dict[str, Any]:
        """全局数据集指标：总数 / 按状态分布 / 版本数 / 总行数 / 总大小。"""
        async with await self._get_session() as session:
            total_datasets = (await session.execute(select(func.count()).select_from(DatasetORM))).scalar_one()
            status_rows = await session.execute(select(DatasetORM.status, func.count()).group_by(DatasetORM.status))
            datasets_by_status = {str(row[0]): int(row[1]) for row in status_rows.all()}
            total_versions = (await session.execute(select(func.count()).select_from(DatasetVersionORM))).scalar_one()
            total_rows = (
                await session.execute(select(func.coalesce(func.sum(DatasetVersionORM.row_count), 0)))
            ).scalar_one()
            total_bytes = (
                await session.execute(select(func.coalesce(func.sum(DatasetVersionORM.size_bytes), 0)))
            ).scalar_one()
            return {
                "total_datasets": int(total_datasets),
                "datasets_by_status": datasets_by_status,
                "total_versions": int(total_versions),
                "total_rows": int(total_rows),
                "total_size_bytes": int(total_bytes),
            }

    async def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        """获取单个数据集详情（含 schema 与版本列表概要）."""
        async with await self._get_session() as session:
            ds_orm = await session.execute(select(DatasetORM).where(DatasetORM.id == dataset_id))
            orm = ds_orm.scalar_one_or_none()
            if orm is None:
                raise ValueError(f"数据集不存在: {dataset_id}")

            schema = _schema_from_json(str(orm.schema_json))
            versions = sorted(orm.versions, key=lambda v: v.created_at, reverse=True)
            return {
                "id": orm.id,
                "name": orm.name,
                "description": orm.description,
                "owner_id": orm.owner_id,
                "status": orm.status,
                "created_at": orm.created_at.isoformat() if orm.created_at else None,
                "updated_at": orm.updated_at.isoformat() if orm.updated_at else None,
                "schema": {
                    "fields": schema.fields,
                    "primary_key": schema.primary_key,
                    "metadata": schema.metadata,
                },
                "versions": [
                    {
                        "version": v.version,
                        "status": v.status,
                        "content_hash": v.content_hash,
                        "row_count": v.row_count,
                        "size_bytes": v.size_bytes,
                        "storage_uri": v.storage_uri,
                        "created_at": v.created_at.isoformat() if v.created_at else None,
                        "created_by": v.created_by,
                        "lineage_record_id": v.lineage_record_id,
                    }
                    for v in versions
                ],
            }


# 单例访问


_singleton: DatasetStore | None = None


def get_dataset_store() -> DatasetStore:
    """获取 DatasetStore 单例."""
    global _singleton
    if _singleton is None:
        _singleton = DatasetStore()
    return _singleton


__all__ = [
    "DatasetStore",
    "get_dataset_store",
]
