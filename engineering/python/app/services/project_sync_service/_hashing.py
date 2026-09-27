"""内容哈希 Mixin：资源 sha256 计算.

从原 ``project_sync_service.py`` 行 348-507 迁移而来。
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from collections.abc import Callable

from sqlalchemy import select

from app.contracts.project_sync import RESOURCE_TYPES, parse_resource_uri

logger = logging.getLogger(__name__)


class _HashingMixin:
    # 宿主契约：由主类提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    """内容哈希 Mixin：资源 sha256 计算.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
    """

    @staticmethod
    def _sha256_bytes(data: bytes) -> str:
        """计算 bytes 的 sha256 hex（64 字符）."""
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _sha256_file(file_path: str) -> str:
        """计算文件的 sha256 hex（分块读取，支持大文件）."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _sha256_json(obj: Any) -> str:
        """计算 JSON 序列化后的 sha256（sort_keys 确保确定性）."""
        payload = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    async def _compute_content_hash(
        self,
        resource_type: str,
        resource_uri: str,
    ) -> str:
        """根据资源类型计算 content_hash.

        Args:
            resource_type: RESOURCE_TYPES 之一
            resource_uri: 资源 URI

        Returns:
            sha256 hex 字符串；无法计算时返回空字符串（表示未计算）
        """
        try:
            _, path = parse_resource_uri(resource_uri)
        except ValueError:
            return ""

        try:
            if resource_type == RESOURCE_TYPES.DATASET:
                # dataset://<dataset_id>/<version> 查 DatasetVersion.content_hash
                return await self._lookup_dataset_hash(path)
            if resource_type == RESOURCE_TYPES.MODEL:
                # model://<model_name>/<version> 模型文件 sha256
                # 当前无 model_artifacts ORM，返回空字符串占位
                # （ADR-012 将补全 model_artifacts 表后启用文件路径查找）
                return ""
            if resource_type == RESOURCE_TYPES.WORKFLOW:
                # workflow://<run_id> WorkflowRun.spec JSONB sha256
                return await self._lookup_workflow_hash(path)
            if resource_type == RESOURCE_TYPES.CONFIG:
                # config://<spec_name> YAML 文件 sha256（ConfigStore）
                return await self._lookup_config_hash(path)
            if resource_type == RESOURCE_TYPES.SNAPSHOT:
                # snapshot://<snapshot_id> ExperimentSnapshot.git_sha
                return await self._lookup_snapshot_hash(path)
            if resource_type == RESOURCE_TYPES.TEMPLATE:
                # template://<template_id>/<version> manifest_snapshot sha256
                return await self._lookup_template_hash(path)
        except Exception as e:
            logger.warning(
                "ProjectSyncService: 计算 content_hash 失败 (%s): %s",
                resource_uri,
                e,
            )
            return ""
        return ""

    async def _lookup_dataset_hash(self, path: str) -> str:
        """从 DatasetVersion 表查询 content_hash.

        path 格式：<dataset_id>/<version>
        """
        parts = path.split("/", 1)
        if len(parts) != 2:
            return ""
        dataset_id, version = parts
        # 延迟导入，避免循环依赖
        from app.database.models.dataset import DatasetVersion

        async with await self._get_session() as session:
            stmt = select(DatasetVersion.content_hash).where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.version == version,
            )
            result = await session.execute(stmt)
            row = result.first()
            return (row[0] if row else "") or ""

    async def _lookup_workflow_hash(self, run_id: str) -> str:
        """从 WorkflowRun 表查询 spec JSONB sha256."""
        from app.database.models.workflow import WorkflowRun

        async with await self._get_session() as session:
            stmt = select(WorkflowRun.spec).where(WorkflowRun.run_id == run_id)
            result = await session.execute(stmt)
            row = result.first()
            if not row or not row[0]:
                return ""
            return self._sha256_json(row[0])

    async def _lookup_config_hash(self, spec_name: str) -> str:
        """从 ConfigStore 查询 spec，序列化后计算 sha256.

        ConfigStore 当前是内存 + YAML 文件（无 DB 持久化），此处直接读
        ConfigStore 单例。
        """
        try:
            from app.config.spec import get_config_store

            store = get_config_store()
            spec = store.get_spec(spec_name)
            if spec is None:
                return ""
            return self._sha256_json(spec.to_dict() if hasattr(spec, "to_dict") else str(spec))
        except Exception:
            # 哈希失败静默返回 "" 会导致 project_sync 的变更检测失效
            # （内容变更可能被误判为无变化），记录日志便于排查。
            logger.warning("_lookup_config_hash failed for spec %r", spec_name, exc_info=True)
            return ""

    async def _lookup_snapshot_hash(self, snapshot_id: str) -> str:
        """从 ExperimentSnapshot 表查询 git_sha 作为 content_hash."""
        from app.database.models.dataset import ExperimentSnapshot

        async with await self._get_session() as session:
            stmt = select(ExperimentSnapshot.git_sha).where(ExperimentSnapshot.snapshot_id == snapshot_id)
            result = await session.execute(stmt)
            row = result.first()
            return (row[0] if row else "") or ""

    async def _lookup_template_hash(self, path: str) -> str:
        """从 WorkflowTemplateVersion 表查询 manifest_snapshot sha256.

        path 格式：<template_id>/<version>
        """
        parts = path.split("/", 1)
        if len(parts) != 2:
            return ""
        template_id, version = parts
        from app.database.models.workflow_template import (
            WorkflowTemplateVersion as TemplateVersionORM,
        )

        async with await self._get_session() as session:
            stmt = select(TemplateVersionORM.manifest_snapshot).where(
                TemplateVersionORM.template_id == template_id,
                TemplateVersionORM.version == version,
            )
            result = await session.execute(stmt)
            row = result.first()
            if not row or not row[0]:
                return ""
            return self._sha256_json(row[0])
