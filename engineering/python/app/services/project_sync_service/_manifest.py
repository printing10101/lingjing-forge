"""Manifest IO Mixin：.lomo-project.yaml 读写.

从原 ``project_sync_service.py`` 行 282-347 迁移而来。
"""

from __future__ import annotations

import os
from typing import Any

import yaml  # type: ignore[import-untyped]

from app.database.models.project_sync import ProjectRepo, ProjectResourceRef


class _ManifestMixin:
    # 宿主契约：由主类提供（mypy 需要显式声明）
    _MANIFEST_FILENAME: str
    """Manifest IO Mixin：.lomo-project.yaml 读写.

    依赖：
        - 类属性 ``_MANIFEST_FILENAME``（由 ProjectSyncService 定义）
    """

    def _write_manifest_yaml(self, repo_path: str, manifest_dict: dict[str, Any]) -> None:
        """写入 .lomo-project.yaml 清单文件."""
        manifest_path = os.path.join(repo_path, self._MANIFEST_FILENAME)
        # 原子写入：先写临时文件，再 rename
        tmp_path = manifest_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(
                    manifest_dict,
                    f,
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                )
            os.replace(tmp_path, manifest_path)
        except OSError:
            # 清理临时文件（若存在）
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

    def _read_manifest_yaml(self, repo_path: str) -> dict[str, Any] | None:
        """读取 .lomo-project.yaml 清单文件（不存在返回 None）."""
        manifest_path = os.path.join(repo_path, self._MANIFEST_FILENAME)
        if not os.path.exists(manifest_path):
            return None
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None

    def _build_manifest_dict(
        self,
        project_orm: ProjectRepo,
        refs: list[ProjectResourceRef],
    ) -> dict[str, Any]:
        """根据 ORM 记录构造 .lomo-project.yaml 清单 dict."""
        return {
            "project_id": project_orm.project_id,
            "name": project_orm.name,
            "description": project_orm.description or "",
            "author": project_orm.author or "",
            "remote_url": project_orm.remote_url or "",
            "current_branch": project_orm.current_branch,
            "resource_refs": [
                {
                    "resource_type": ref.resource_type,
                    "resource_uri": ref.resource_uri,
                    "content_hash": ref.content_hash or "",
                    "sync_strategy": ref.sync_strategy,
                    "metadata": ref.metadata_json or {},
                }
                for ref in refs
            ],
            "schema_version": "1.0",
        }
