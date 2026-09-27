"""远程操作 Mixin：push + pull.

从原 ``project_sync_service.py`` 行 1505-1769 迁移而来。
"""

from __future__ import annotations

import logging
import os
from typing import Any
from collections.abc import Callable

from sqlalchemy import select

from app.contracts.project_sync import SYNC_DIRECTIONS, SYNC_STATUS
from app.database.models.project_sync import ProjectRepo
from app.services.project_sync_service._exceptions import (
    GitOperationError,
    InvalidProjectStateError,
    ProjectNotFoundError,
)

logger = logging.getLogger(__name__)


class _RemoteMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    _get_project_lock: Callable[..., Any]
    _require_git: Callable[..., Any]
    _run_git: Callable[..., Any]
    _record_sync: Callable[..., Any]
    _GIT_TIMEOUT_LONG: float
    _derive_status: Callable[..., Any]
    _query_git_status: Callable[..., Any]
    """远程操作 Mixin：push + pull.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
        - ``self._get_project_lock()``（在 service.py 中定义）
        - ``self._require_git()`` / ``self._run_git()`` / ``self._query_git_status()``
          / ``self._derive_status()``（来自 _GitOpsMixin）
        - ``self._record_sync()``（来自 _SyncRecordsMixin）
        - 类属性 ``_GIT_TIMEOUT_LONG``
    """

    async def push_project(self, project_id: str) -> dict[str, Any]:
        """推送到远端仓库.

        Raises:
            ProjectNotFoundError: 项目不存在
            GitUnavailableError: git 不可用
            InvalidProjectStateError: 未配置远端
            GitOperationError: git push 失败
        """
        self._require_git()
        lock = self._get_project_lock(project_id)
        with lock:
            repo_path, branch, remote_url = await self._load_project_for_push_pull(project_id, op="push")
            push_status, push_message, push_details = self._execute_git_push(repo_path, branch, remote_url)
            await self._persist_push_result(project_id, push_status, push_message, push_details)
            if push_status != "success":
                raise GitOperationError(["push", "origin", branch], -1, push_message)

        logger.info("Pushed project %s to %s", project_id, remote_url)
        return {
            "project_id": project_id,
            "status": SYNC_STATUS.CLEAN,
            "remote_url": remote_url,
            "branch": branch,
        }

    async def _load_project_for_push_pull(self, project_id: str, *, op: str) -> tuple[str, str, str]:
        """加载 project 并校验仓库目录 + remote_url（push/pull 通用）.

        Args:
            project_id: 项目 ID
            op: 操作名（"push" / "pull"），用于错误消息

        Returns:
            (repo_path, branch, remote_url)

        Raises:
            ProjectNotFoundError: 项目不存在或仓库目录不存在
            InvalidProjectStateError: 未配置 remote_url
        """
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")
            repo_path = project_orm.repo_path
            branch = project_orm.current_branch
            remote_url = project_orm.remote_url or ""

        if not os.path.isdir(repo_path):
            raise ProjectNotFoundError(f"项目仓库目录不存在: {repo_path}")
        if not remote_url:
            raise InvalidProjectStateError(f"项目未配置远端仓库 URL，无法 {op}: {project_id}")
        return repo_path, branch, remote_url

    def _execute_git_push(self, repo_path: str, branch: str, remote_url: str) -> tuple[str, str, dict[str, Any]]:
        """执行 git push，捕获异常并返回 (status, message, details).

        Returns:
            (push_status, push_message, push_details)
            - push_status: "success" / "failed"
        """
        push_status = "success"
        push_message = ""
        push_details: dict[str, Any] = {
            "remote_url": remote_url,
            "branch": branch,
        }
        try:
            res = self._run_git(
                ["push", "origin", branch],
                cwd=repo_path,
                timeout=self._GIT_TIMEOUT_LONG,
            )
            push_message = res.stdout.strip() or "push 成功"
        except GitOperationError as e:
            push_status = "failed"
            push_message = e.stderr.strip() or str(e)
            push_details["returncode"] = e.returncode
        return push_status, push_message, push_details

    async def _persist_push_result(
        self,
        project_id: str,
        push_status: str,
        push_message: str,
        push_details: dict[str, Any],
    ) -> None:
        """更新 DB status + 写入 push SyncRecord（独立 session + commit）.

        事务边界与原实现一致：status 更新 + record_sync 在同一 session 内
        一次 commit。
        """
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one()
            if push_status == "success":
                project_orm.status = SYNC_STATUS.CLEAN
            else:
                project_orm.status = SYNC_STATUS.ERROR
            head_sha = project_orm.current_commit or ""
            await self._record_sync(
                session,
                project_id,
                SYNC_DIRECTIONS.PUSH,
                commit_sha=head_sha,
                status=push_status,
                message=push_message,
                details=push_details,
            )
            await session.commit()

    async def pull_project(self, project_id: str) -> dict[str, Any]:
        """从远端仓库拉取变更.

        Raises:
            ProjectNotFoundError: 项目不存在
            GitUnavailableError: git 不可用
            InvalidProjectStateError: 未配置远端
            GitOperationError: git pull 失败（含冲突）
        """
        self._require_git()
        lock = self._get_project_lock(project_id)
        with lock:
            repo_path, branch, remote_url = await self._load_project_for_push_pull(project_id, op="pull")
            pull_status, pull_message, pull_details = self._execute_git_pull(repo_path, branch, remote_url)
            new_status, new_commit = self._derive_pull_status(repo_path, pull_status)
            await self._persist_pull_result(
                project_id,
                pull_status,
                pull_message,
                pull_details,
                new_status,
                new_commit,
            )
            if pull_status != "success":
                raise GitOperationError(["pull", "origin", branch], -1, pull_message)

        logger.info("Pulled project %s from %s", project_id, remote_url)
        return {
            "project_id": project_id,
            "status": new_status,
            "remote_url": remote_url,
            "branch": branch,
        }

    def _execute_git_pull(self, repo_path: str, branch: str, remote_url: str) -> tuple[str, str, dict[str, Any]]:
        """执行 git pull，捕获异常并返回 (status, message, details).

        与 push 的差异：pull 可能产生 conflict 状态。

        Returns:
            (pull_status, pull_message, pull_details)
            - pull_status: "success" / "failed" / "conflict"
        """
        pull_status = "success"
        pull_message = ""
        pull_details: dict[str, Any] = {
            "remote_url": remote_url,
            "branch": branch,
        }
        try:
            res = self._run_git(
                ["pull", "origin", branch],
                cwd=repo_path,
                timeout=self._GIT_TIMEOUT_LONG,
            )
            pull_message = res.stdout.strip() or "pull 成功"
        except GitOperationError as e:
            stderr = e.stderr.lower()
            if "conflict" in stderr or "merge conflict" in stderr:
                pull_status = "conflict"
                pull_message = e.stderr.strip()
            else:
                pull_status = "failed"
                pull_message = e.stderr.strip() or str(e)
            pull_details["returncode"] = e.returncode
        return pull_status, pull_message, pull_details

    def _derive_pull_status(self, repo_path: str, pull_status: str) -> tuple[str, str]:
        """根据 pull 结果 + git status 推导新状态 + 新 HEAD sha.

        Returns:
            (new_status, new_commit)
            - new_status: SYNC_STATUS 之一（CLEAN/DIRTY/AHEAD/BEHIND/CONFLICT/ERROR）
            - new_commit: 拉取后的 HEAD sha（无 commit 时为空字符串）
        """
        new_status = SYNC_STATUS.CLEAN
        new_commit = ""
        if os.path.isdir(repo_path):
            git_status = self._query_git_status(repo_path)
            new_commit = git_status["head_sha"]
            if pull_status == "conflict":
                new_status = SYNC_STATUS.CONFLICT
            elif pull_status == "failed":
                new_status = SYNC_STATUS.ERROR
            else:
                new_status = self._derive_status(git_status)
        return new_status, new_commit

    async def _persist_pull_result(
        self,
        project_id: str,
        pull_status: str,
        pull_message: str,
        pull_details: dict[str, Any],
        new_status: str,
        new_commit: str,
    ) -> None:
        """更新 DB current_commit + status + 写入 pull SyncRecord.

        事务边界与原实现一致：current_commit + status + record_sync 在同一
        session 内一次 commit。
        """
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one()
            if new_commit:
                project_orm.current_commit = new_commit
            project_orm.status = new_status
            await self._record_sync(
                session,
                project_id,
                SYNC_DIRECTIONS.PULL,
                commit_sha=new_commit,
                status=pull_status,
                message=pull_message,
                details=pull_details,
            )
            await session.commit()
