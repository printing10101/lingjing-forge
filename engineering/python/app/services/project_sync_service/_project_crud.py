"""项目 CRUD Mixin：create/get/list/delete project.

从原 ``project_sync_service.py`` 行 623-933 迁移而来。
"""

from __future__ import annotations

import logging
import os
import shutil
from typing import Any
from collections.abc import Callable

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from app.contracts.project_sync import SYNC_DIRECTIONS, SYNC_STATUS
from app.database.models.project_sync import ProjectRepo
from app.services.project_sync_service._exceptions import (
    GitOperationError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
)

logger = logging.getLogger(__name__)


class _ProjectCrudMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    _get_project_lock: Callable[..., Any]
    _project_locks: dict[str, Any]
    _project_locks_guard: Any
    _require_git: Callable[..., Any]
    _run_git: Callable[..., Any]
    _get_repo_path: Callable[..., Any]
    _build_manifest_dict: Callable[..., Any]
    _write_manifest_yaml: Callable[..., Any]
    _record_sync: Callable[..., Any]
    _MANIFEST_FILENAME: str
    """项目 CRUD Mixin：create/get/list/delete project.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
        - ``self._get_project_lock()``（在 service.py 中定义）
        - ``self._project_locks`` / ``self._project_locks_guard``
        - ``self._require_git()`` / ``self._run_git()`` / ``self._get_repo_path()``
          （来自 _GitOpsMixin）
        - ``self._build_manifest_dict()`` / ``self._write_manifest_yaml()``
          （来自 _ManifestMixin）
        - ``self._record_sync()``（来自 _SyncRecordsMixin）
        - 类属性 ``_MANIFEST_FILENAME``
    """

    async def create_project(
        self,
        name: str,
        *,
        description: str = "",
        author: str = "",
        remote_url: str = "",
        branch: str = "main",
        initial_commit: bool = True,
    ) -> dict[str, Any]:
        """创建可同步项目（git init + DB 记录 + 初始清单 commit）.

        Args:
            name: 项目显示名
            description: 项目描述
            author: 项目作者
            remote_url: 远端仓库 URL（空表示纯本地仓库）
            branch: 初始分支名（默认 main）
            initial_commit: 是否创建初始 commit（写入 .lomo-project.yaml）

        Returns:
            项目详情 dict（含 project_id / repo_path / status）

        Raises:
            ProjectAlreadyExistsError: 项目名已存在
            GitUnavailableError: 系统未安装 git
            GitOperationError: git init / commit 失败
        """
        self._validate_create_project_inputs(name)

        project_id, repo_path, project_orm = await self._create_project_db_record(
            name,
            description=description,
            author=author,
            remote_url=remote_url,
            branch=branch,
        )

        lock = self._get_project_lock(project_id)
        with lock:
            head_sha = self._init_project_repo_on_disk(project_orm, repo_path, branch, remote_url, initial_commit, name)
            await self._record_project_init(project_id, name, branch, remote_url, head_sha)

        logger.info("Created project_sync project: %s (%s)", name, project_id)
        return await self.get_project(project_id, include_refs=False)

    def _validate_create_project_inputs(self, name: str) -> None:
        """校验 create_project 输入参数.

        Raises:
            ValueError: name 为空
            GitUnavailableError: git 不可用
        """
        if not name:
            raise ValueError("项目名不能为空")
        self._require_git()

    async def _create_project_db_record(
        self,
        name: str,
        *,
        description: str,
        author: str,
        remote_url: str,
        branch: str,
    ) -> tuple[str, str, ProjectRepo]:
        """创建项目 DB 记录，返回 (project_id, repo_path, project_orm).

        包含两段事务：先插入获取 project_id，再更新 repo_path，均显式 commit。

        Raises:
            ProjectAlreadyExistsError: 项目名已存在
        """
        async with await self._get_session() as session:
            # 检查同名项目是否已存在
            existing_stmt = select(ProjectRepo).where(ProjectRepo.name == name)
            existing = (await session.execute(existing_stmt)).scalar_one_or_none()
            if existing is not None:
                raise ProjectAlreadyExistsError(f"项目名已存在: {name}")

            # 先创建 ORM 记录获取 project_id（路径需要 project_id 拼接）
            project_orm = ProjectRepo(
                name=name,
                repo_path="__pending__",  # 占位，下面更新
                remote_url=remote_url or None,
                current_branch=branch,
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
                raise ProjectAlreadyExistsError(f"项目创建失败（可能 name 冲突）: {name}") from e

            # 刷新以获取 project_id（default 已触发）
            await session.refresh(project_orm)
            project_id = str(project_orm.project_id)
            repo_path = self._get_repo_path(project_id)
            project_orm.repo_path = repo_path
            await session.commit()

        return project_id, repo_path, project_orm

    def _init_project_repo_on_disk(
        self,
        project_orm: ProjectRepo,
        repo_path: str,
        branch: str,
        remote_url: str,
        initial_commit: bool,
        name: str,
    ) -> str:
        """初始化仓库目录（git init + remote + manifest + 可选 initial commit）.

        Args:
            project_orm: 项目 ORM 实例（用于构造 manifest；session 已关闭，
                仅访问属性）
            repo_path: 仓库目录路径
            branch: 初始分支名
            remote_url: 远端 URL（空表示不配置）
            initial_commit: 是否创建初始 commit
            name: 项目名（用于 commit message）

        Returns:
            head_sha（无 initial_commit 时为空字符串）
        """
        os.makedirs(repo_path, exist_ok=True)
        try:
            # git init（指定分支名，git 2.28+ 支持 -b）
            self._run_git(["init", "-b", branch], cwd=repo_path)
        except GitOperationError:
            # 旧版 git 不支持 -b，回退到 init + checkout
            self._run_git(["init"], cwd=repo_path)
            try:
                self._run_git(["checkout", "-b", branch], cwd=repo_path)
            except GitOperationError:
                pass  # 分支已存在时忽略

        # 配置远端（若提供）
        if remote_url:
            self._run_git(["remote", "add", "origin", remote_url], cwd=repo_path)

        # 写入初始清单
        manifest_dict = self._build_manifest_dict(project_orm, refs=[])
        self._write_manifest_yaml(repo_path, manifest_dict)

        head_sha = ""
        if initial_commit:
            self._run_git(["add", self._MANIFEST_FILENAME], cwd=repo_path)
            self._run_git(
                ["commit", "-m", f"chore(project): init {name}"],
                cwd=repo_path,
            )
            head_res = self._run_git(["rev-parse", "HEAD"], cwd=repo_path)
            head_sha = head_res.stdout.strip()
        return head_sha

    async def _record_project_init(
        self,
        project_id: str,
        name: str,
        branch: str,
        remote_url: str,
        head_sha: str,
    ) -> None:
        """更新 DB current_commit + status + 写入 init SyncRecord（独立 commit）."""
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one()
            project_orm.current_commit = head_sha or None
            project_orm.status = SYNC_STATUS.CLEAN
            await self._record_sync(
                session,
                project_id,
                SYNC_DIRECTIONS.INIT,
                commit_sha=head_sha,
                status="success",
                message=f"初始化项目 {name}",
                details={"branch": branch, "remote_url": remote_url},
            )
            await session.commit()

    async def get_project(
        self,
        project_id: str,
        *,
        include_refs: bool = False,
        include_records: bool = False,
    ) -> dict[str, Any]:
        """获取项目详情.

        Raises:
            ProjectNotFoundError: 项目不存在
        """
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")
            return project_orm.to_dict(
                include_refs=include_refs,
                include_records=include_records,
            )

    async def list_projects(
        self,
        *,
        status_filter: str | None = None,
        author: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """分页列出项目（按 updated_at 倒序）."""
        limit = max(1, min(100, limit))
        offset = max(0, offset)

        async with await self._get_session() as session:
            stmt = select(ProjectRepo)
            count_stmt = select(func.count()).select_from(ProjectRepo)

            if status_filter:
                if not SYNC_STATUS.is_valid(status_filter):
                    raise ValueError(f"status_filter 不支持: {status_filter}")
                stmt = stmt.where(ProjectRepo.status == status_filter)
                count_stmt = count_stmt.where(ProjectRepo.status == status_filter)
            if author:
                stmt = stmt.where(ProjectRepo.author == author)
                count_stmt = count_stmt.where(ProjectRepo.author == author)

            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(desc(ProjectRepo.updated_at)).limit(limit).offset(offset)
            items = [row.to_dict() for row in (await session.execute(stmt)).scalars().all()]
            return {
                "items": items,
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    async def delete_project(self, project_id: str, *, delete_repo_dir: bool = True) -> dict[str, Any]:
        """删除项目（DB 记录 + 可选删除仓库目录）.

        Args:
            project_id: 项目 ID
            delete_repo_dir: 是否物理删除本地仓库目录（默认 True）

        Raises:
            ProjectNotFoundError: 项目不存在
        """
        # 先获取 repo_path 用于后续删除
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")
            repo_path = project_orm.repo_path
            # 删除 DB 记录（级联删除 resource_refs + sync_records）
            await session.delete(project_orm)
            await session.commit()

        # 物理删除仓库目录
        if delete_repo_dir and repo_path and os.path.isdir(repo_path):
            try:
                shutil.rmtree(repo_path)
            except OSError as e:
                logger.warning(
                    "ProjectSyncService: 删除仓库目录失败 (%s): %s",
                    repo_path,
                    e,
                )

        # 清理 project lock
        with self._project_locks_guard:
            self._project_locks.pop(project_id, None)

        logger.info("Deleted project_sync project: %s", project_id)
        return {"project_id": project_id, "deleted": True}
