"""克隆 Mixin：clone_project.

从原 ``project_sync_service.py`` 行 1770-2016 迁移而来。
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.contracts.project_sync import (
    SYNC_DIRECTIONS,
    SYNC_STATUS,
    SYNC_STRATEGIES,
)
from app.database.models.project_sync import ProjectRepo, ProjectResourceRef
from app.services.project_sync_service._exceptions import (
    GitOperationError,
    ProjectAlreadyExistsError,
)

logger = logging.getLogger(__name__)


class _CloneMixin:
    """克隆 Mixin：clone_project.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
        - ``self._get_project_lock()``（在 service.py 中定义）
        - ``self._require_git()`` / ``self._run_git()`` / ``self._get_repo_path()``
          / ``self._read_manifest_yaml()``（来自 _GitOpsMixin / _ManifestMixin）
        - ``self._record_sync()``（来自 _SyncRecordsMixin）
        - ``self.get_project()``（来自 _ProjectCrudMixin）
        - 类属性 ``_GIT_TIMEOUT_LONG``
    """

    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    _get_project_lock: Callable[..., Any]
    _require_git: Callable[..., Any]
    _run_git: Callable[..., Any]
    _get_repo_path: Callable[..., Any]
    _record_sync: Callable[..., Any]
    _GIT_TIMEOUT_LONG: float
    _read_manifest_yaml: Callable[..., Any]
    get_project: Callable[..., Any]

    async def clone_project(
        self,
        remote_url: str,
        *,
        name: str = "",
        author: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """克隆远端项目仓库.

        流程：
            1. 创建 DB 记录获取 project_id
            2. git clone <remote_url> <repo_path>
            3. 解析 .lomo-project.yaml 清单
            4. 同步资源引用到 DB
            5. 写入 clone SyncRecord

        Args:
            remote_url: 远端仓库 URL
            name: 项目显示名（空则使用清单中的 name 或 URL 末段）
            author: 项目作者
            description: 项目描述

        Returns:
            项目详情 dict

        Raises:
            GitUnavailableError: git 不可用
            GitOperationError: git clone 失败
        """
        self._validate_clone_inputs(remote_url)

        project_id, repo_path = await self._create_clone_db_record(
            remote_url, name=name, author=author, description=description
        )

        lock = self._get_project_lock(project_id)
        refs_added = 0
        with lock:
            try:
                self._execute_git_clone(repo_path, remote_url)
            except GitOperationError:
                await self._rollback_clone_db_record(project_id)
                raise

            manifest = self._read_manifest_yaml(repo_path)
            head_sha = self._read_clone_head_sha(repo_path)
            branch = self._read_clone_branch(repo_path)

            refs_added = await self._sync_clone_refs_to_db(project_id, manifest)

            await self._persist_clone_result(
                project_id,
                remote_url,
                branch,
                head_sha,
                refs_added,
                manifest,
                name=name,
                author=author,
                description=description,
            )

        logger.info(
            "Cloned project %s from %s (refs=%d)",
            project_id,
            remote_url,
            refs_added,
        )
        return await self.get_project(project_id, include_refs=True)

    def _validate_clone_inputs(self, remote_url: str) -> None:
        """校验 clone_project 输入参数.

        Raises:
            ValueError: remote_url 为空
            GitUnavailableError: git 不可用
        """
        if not remote_url:
            raise ValueError("remote_url 不能为空")
        self._require_git()

    async def _create_clone_db_record(
        self,
        remote_url: str,
        *,
        name: str,
        author: str,
        description: str,
    ) -> tuple[str, str]:
        """创建 clone 项目的 DB 记录，返回 (project_id, repo_path).

        事务边界与原实现一致：先插入获取 project_id（commit 一次），再更新
        repo_path（commit 一次）。

        Raises:
            ProjectAlreadyExistsError: 项目创建失败（name 冲突）
        """
        async with await self._get_session() as session:
            project_orm = ProjectRepo(
                name=name or remote_url.rstrip("/").split("/")[-1],
                repo_path="__pending__",
                remote_url=remote_url,
                current_branch="main",
                current_commit=None,
                status=SYNC_STATUS.CLEAN,
                description=description or None,
                author=author or None,
            )
            session.add(project_orm)
            try:
                await session.commit()
            except IntegrityError as e:
                await session.rollback()
                raise ProjectAlreadyExistsError(f"项目创建失败: {name}") from e
            await session.refresh(project_orm)
            project_id = str(project_orm.project_id)
            repo_path = self._get_repo_path(project_id)
            project_orm.repo_path = repo_path
            await session.commit()
        return project_id, repo_path

    def _execute_git_clone(self, repo_path: str, remote_url: str) -> None:
        """执行 git clone（清空目录后 clone 到 repo_path）."""
        if os.path.isdir(repo_path):
            shutil.rmtree(repo_path)
        parent_dir = os.path.dirname(repo_path)
        os.makedirs(parent_dir, exist_ok=True)
        self._run_git(
            ["clone", remote_url, repo_path],
            cwd=parent_dir,
            timeout=self._GIT_TIMEOUT_LONG,
        )

    async def _rollback_clone_db_record(self, project_id: str) -> None:
        """clone 失败时回滚 DB 记录（删除 project_orm + commit）.

        事务边界与原实现一致：独立 session + commit。
        """
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            p_orm = (await session.execute(p_stmt)).scalar_one_or_none()
            if p_orm is not None:
                await session.delete(p_orm)
                await session.commit()

    def _read_clone_head_sha(self, repo_path: str) -> str:
        """读取 clone 后的 HEAD sha（失败时返回空字符串）."""
        head_sha = ""
        try:
            head_res = self._run_git(["rev-parse", "HEAD"], cwd=repo_path)
            head_sha = head_res.stdout.strip()
        except GitOperationError:
            pass
        return head_sha

    def _read_clone_branch(self, repo_path: str) -> str:
        """读取 clone 后的当前分支名（失败时返回 'main'）."""
        try:
            branch_res = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
            branch = branch_res.stdout.strip() or "main"
        except GitOperationError:
            branch = "main"
        return branch

    async def _sync_clone_refs_to_db(
        self,
        project_id: str,
        manifest: dict[str, Any] | None,
    ) -> int:
        """将清单中的资源引用同步到 DB，返回添加数量.

        事务边界与原实现一致：所有 ref 在同一 session 内 add，最后一次 commit。
        """
        refs_added = 0
        if not manifest or "resource_refs" not in manifest:
            return refs_added
        async with await self._get_session() as session:
            for ref_data in manifest["resource_refs"]:
                ref_orm = ProjectResourceRef(
                    project_id=project_id,
                    resource_type=ref_data.get("resource_type", ""),
                    resource_uri=ref_data.get("resource_uri", ""),
                    content_hash=ref_data.get("content_hash") or None,
                    sync_strategy=ref_data.get("sync_strategy", SYNC_STRATEGIES.HASH_REFERENCED),
                    metadata_json=ref_data.get("metadata") or {},
                )
                session.add(ref_orm)
                refs_added += 1
            await session.commit()
        return refs_added

    async def _persist_clone_result(
        self,
        project_id: str,
        remote_url: str,
        branch: str,
        head_sha: str,
        refs_added: int,
        manifest: dict[str, Any] | None,
        *,
        name: str,
        author: str,
        description: str,
    ) -> None:
        """更新 DB + 写入 clone SyncRecord.

        事务边界与原实现一致：项目字段更新 + record_sync 在同一 session 内
        一次 commit。
        """
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one()
            project_orm.current_branch = branch
            project_orm.current_commit = head_sha or None
            project_orm.status = SYNC_STATUS.CLEAN
            if manifest:
                if not name and manifest.get("name"):
                    project_orm.name = manifest["name"]
                if manifest.get("description") and not description:
                    project_orm.description = manifest["description"]
                if manifest.get("author") and not author:
                    project_orm.author = manifest["author"]
            await self._record_sync(
                session,
                project_id,
                SYNC_DIRECTIONS.CLONE,
                commit_sha=head_sha,
                status="success",
                message=f"克隆项目 {remote_url}",
                details={
                    "remote_url": remote_url,
                    "branch": branch,
                    "refs_added": refs_added,
                },
            )
            await session.commit()
