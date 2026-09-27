"""ProjectSyncService 主类：组合所有 Mixin.

从原 ``project_sync_service.py`` 行 145-189、69-72 迁移而来。

业务逻辑分布：
    - ``_GitOpsMixin``：git 命令封装与状态查询
    - ``_ManifestMixin``：.lomo-project.yaml 清单读写
    - ``_HashingMixin``：content_hash 计算
    - ``_SyncRecordsMixin``：同步记录管理
    - ``_ProjectCrudMixin``：项目 CRUD
    - ``_ResourceRefMixin``：资源引用管理
    - ``_CommitMixin``：commit 流程
    - ``_RemoteMixin``：push / pull
    - ``_CloneMixin``：clone
"""

from __future__ import annotations

import logging
import os
import threading


from app.config import config
from app.services._shared.service_base import BaseSingletonService
from app.services.project_sync_service._clone import _CloneMixin
from app.services.project_sync_service._commit import _CommitMixin
from app.services.project_sync_service._git_ops import _GitOpsMixin
from app.services.project_sync_service._hashing import _HashingMixin
from app.services.project_sync_service._manifest import _ManifestMixin
from app.services.project_sync_service._project_crud import _ProjectCrudMixin
from app.services.project_sync_service._remote import _RemoteMixin
from app.services.project_sync_service._resource_refs import _ResourceRefMixin
from app.services.project_sync_service._sync_records import _SyncRecordsMixin

logger = logging.getLogger(__name__)


def get_project_sync_service() -> "ProjectSyncService":
    """获取全局 ProjectSyncService 单例（委托给 ``ProjectSyncService.get_instance``）."""
    return ProjectSyncService.get_instance()  # type: ignore[return-value]


class ProjectSyncService(
    _GitOpsMixin,
    _ManifestMixin,
    _HashingMixin,
    _SyncRecordsMixin,
    _ProjectCrudMixin,
    _ResourceRefMixin,
    _CommitMixin,
    _RemoteMixin,
    _CloneMixin,
    BaseSingletonService,
):
    """项目级 Git 同步服务.

    线程安全：
        - ``_project_locks``：按 project_id 维护独立的 Lock，确保同一项目内的
          Git 写操作串行化（commit/push/pull 互斥），不同项目可并发
        - ``_git_available_lock``：保护 git 可用性检测的缓存

    数据库事务：
        - 每个公共方法内部独立管理 session，写操作显式 commit
        - Git 操作失败时回滚已修改的 DB 状态，保持一致性
    """

    # git 命令默认超时（秒）
    _GIT_TIMEOUT = 60.0
    # clone/push 大仓库超时（秒）
    _GIT_TIMEOUT_LONG = 300.0
    # .lomo-project.yaml 文件名
    _MANIFEST_FILENAME = ".lomo-project.yaml"

    def __init__(self) -> None:
        # 按 project_id 维护独立的 Lock，确保同一项目 Git 写操作串行化
        self._project_locks: dict[str, threading.Lock] = {}
        self._project_locks_guard = threading.Lock()
        # git 可用性缓存（None 表示未检测；True/False 为检测结果）
        self._git_available: bool | None = None
        self._git_available_lock = threading.Lock()
        # 仓库存储根目录：<output_dir>/project_sync/
        self._repos_root = os.path.join(os.path.abspath(config.storage.output_dir), "project_sync")
        os.makedirs(self._repos_root, exist_ok=True)

    # 内部辅助：锁管理

    def _get_project_lock(self, project_id: str) -> threading.Lock:
        """获取（或创建）指定项目的专用锁."""
        with self._project_locks_guard:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = threading.Lock()
                self._project_locks[project_id] = lock
            return lock


__all__ = [
    "ProjectSyncService",
    "get_project_sync_service",
]
