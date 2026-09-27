"""提交 Mixin：get_project_status + commit_project.

从原 ``project_sync_service.py`` 行 1201-1504 迁移而来。
"""

from __future__ import annotations

import logging
import os
from typing import Any
from collections.abc import Callable

from sqlalchemy import select

from app.contracts.project_sync import SYNC_DIRECTIONS, SYNC_STATUS
from app.database.models.project_sync import ProjectRepo, ProjectResourceRef
from app.services.project_sync_service._exceptions import (
    ProjectNotFoundError,
)

logger = logging.getLogger(__name__)


class _CommitMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_session: Callable[..., Any]
    _get_project_lock: Callable[..., Any]
    _require_git: Callable[..., Any]
    _run_git: Callable[..., Any]
    _build_manifest_dict: Callable[..., Any]
    _write_manifest_yaml: Callable[..., Any]
    _record_sync: Callable[..., Any]
    _MANIFEST_FILENAME: str
    _compute_content_hash: Callable[..., Any]
    _derive_status: Callable[..., Any]
    _query_git_status: Callable[..., Any]
    """提交 Mixin：get_project_status + commit_project.

    依赖：
        - ``self._get_session()``（继承自 BaseSingletonService）
        - ``self._get_project_lock()``（在 service.py 中定义）
        - ``self._require_git()`` / ``self._run_git()`` / ``self._query_git_status()``
          / ``self._derive_status()``（来自 _GitOpsMixin）
        - ``self._build_manifest_dict()`` / ``self._write_manifest_yaml()``
          （来自 _ManifestMixin）
        - ``self._compute_content_hash()``（来自 _HashingMixin）
        - ``self._record_sync()``（来自 _SyncRecordsMixin）
        - 类属性 ``_MANIFEST_FILENAME``
    """

    async def get_project_status(self, project_id: str) -> dict[str, Any]:
        """查询项目当前 Git 状态（执行 git status）.

        返回的字段：
            - project_id / name / status（推导后的 SYNC_STATUS）
            - porcelain / head_sha / ahead / behind / has_remote
            - 更新 DB 中的 current_commit / status

        Raises:
            ProjectNotFoundError: 项目不存在
            GitUnavailableError: git 不可用
        """
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")
            repo_path = project_orm.repo_path
            name = project_orm.name

        if not os.path.isdir(repo_path):
            raise ProjectNotFoundError(f"项目仓库目录不存在: {repo_path}")

        git_status = self._query_git_status(repo_path)
        derived = self._derive_status(git_status)

        # 更新 DB 中的 current_commit + status
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one()
            project_orm.current_commit = git_status["head_sha"] or None
            project_orm.status = derived
            await session.commit()

        return {
            "project_id": project_id,
            "name": name,
            "status": derived,
            "porcelain": git_status["porcelain"],
            "head_sha": git_status["head_sha"],
            "ahead": git_status["ahead"],
            "behind": git_status["behind"],
            "has_remote": git_status["has_remote"],
        }

    async def commit_project(
        self,
        project_id: str,
        message: str,
    ) -> dict[str, Any]:
        """提交项目变更（更新清单 + git add + git commit）.

        流程：
            1. 重新计算所有资源引用的 content_hash
            2. 更新 .lomo-project.yaml 清单
            3. git add .lomo-project.yaml
            4. git commit -m <message>
            5. 更新 DB current_commit + status
            6. 写入 SyncRecord

        Args:
            project_id: 项目 ID
            message: commit message

        Returns:
            {project_id, commit_sha, status, changed_refs}

        Raises:
            ProjectNotFoundError: 项目不存在
            GitUnavailableError: git 不可用
            GitOperationError: git commit 失败
        """
        self._validate_commit_inputs(message)

        lock = self._get_project_lock(project_id)
        commit_sha = ""
        artifacts: dict[str, Any] = {}
        with lock:
            artifacts = await self._prepare_commit_artifacts(project_id)
            partial_state = {"artifacts": artifacts}
            try:
                commit_sha = await self._perform_db_commit(project_id, message, artifacts)
            except Exception:
                await self._rollback_commit(project_id, partial_state)
                raise

        result = {
            "project_id": project_id,
            "commit_sha": commit_sha,
            "status": SYNC_STATUS.CLEAN,
            "changed_refs": artifacts["changed_refs"],
        }
        self._notify_commit_success(project_id, result)
        return result

    def _validate_commit_inputs(self, message: str) -> None:
        """校验 commit_project 输入参数.

        Raises:
            ValueError: message 为空
            GitUnavailableError: git 不可用
        """
        if not message:
            raise ValueError("commit message 不能为空")
        self._require_git()

    async def _prepare_commit_artifacts(self, project_id: str) -> dict[str, Any]:
        """准备 commit 所需的 artifacts.

        包含 4 个步骤：
            1. 加载 project + refs（session1，read-only，不 commit）
            2. 校验仓库目录存在
            3. 重新计算所有资源引用的 content_hash，检测变更并逐条 commit 到 DB
               （每个 ref 独立 session + commit，与原实现事务边界一致）
            4. 重新加载 project + refs（session3，read-only，不 commit），
               构造 manifest_dict

        Returns:
            {"repo_path": str, "manifest_dict": dict, "changed_refs": list}

        Raises:
            ProjectNotFoundError: 项目不存在或仓库目录不存在
        """
        # 加载 project + refs（read-only session）
        async with await self._get_session() as session:
            stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(stmt)).scalar_one_or_none()
            if project_orm is None:
                raise ProjectNotFoundError(f"项目不存在: {project_id}")
            repo_path = project_orm.repo_path

            refs_stmt = (
                select(ProjectResourceRef)
                .where(ProjectResourceRef.project_id == project_id)
                .order_by(ProjectResourceRef.created_at)
            )
            ref_orms = list((await session.execute(refs_stmt)).scalars().all())

        # 校验仓库目录
        if not os.path.isdir(repo_path):
            raise ProjectNotFoundError(f"项目仓库目录不存在: {repo_path}")

        # 重新计算 hash + 检测变更 + 更新 DB（每 ref 独立 commit）
        changed_refs: list[dict[str, Any]] = []
        for ref_orm in ref_orms:
            old_hash = ref_orm.content_hash or ""
            new_hash = await self._compute_content_hash(ref_orm.resource_type, ref_orm.resource_uri)
            if new_hash != old_hash:
                changed_refs.append(
                    {
                        "resource_uri": ref_orm.resource_uri,
                        "old_hash": old_hash,
                        "new_hash": new_hash,
                    }
                )
                # 更新 DB 中的 hash（独立 session + commit）
                async with await self._get_session() as session:
                    r_stmt = select(ProjectResourceRef).where(ProjectResourceRef.id == ref_orm.id)
                    r_orm = (await session.execute(r_stmt)).scalar_one()
                    r_orm.content_hash = new_hash or None
                    await session.commit()

        # 重新加载 project + refs，构造 manifest
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one()
            refs_stmt = (
                select(ProjectResourceRef)
                .where(ProjectResourceRef.project_id == project_id)
                .order_by(ProjectResourceRef.created_at)
            )
            ref_orms = list((await session.execute(refs_stmt)).scalars().all())
            manifest_dict = self._build_manifest_dict(project_orm, ref_orms)

        return {
            "repo_path": repo_path,
            "manifest_dict": manifest_dict,
            "changed_refs": changed_refs,
        }

    async def _perform_db_commit(
        self,
        project_id: str,
        message: str,
        artifacts: dict[str, Any],
    ) -> str:
        """执行 git commit + DB 更新.

        流程：
            1. 写入 manifest yaml
            2. git add manifest
            3. git status --porcelain 检查变更
            4. 若有变更：git commit + rev-parse HEAD
            5. 更新 DB current_commit + status + 写 SyncRecord（一次 commit）

        Args:
            project_id: 项目 ID
            message: commit message
            artifacts: _prepare_commit_artifacts 返回的 dict

        Returns:
            commit_sha（无变更时为空字符串）
        """
        repo_path = artifacts["repo_path"]
        manifest_dict = artifacts["manifest_dict"]

        # 更新清单文件
        self._write_manifest_yaml(repo_path, manifest_dict)

        # git add + commit
        self._run_git(["add", self._MANIFEST_FILENAME], cwd=repo_path)

        # 检查是否有变更待提交
        status_res = self._run_git(["status", "--porcelain"], cwd=repo_path)
        commit_sha = ""
        if status_res.stdout.strip():
            self._run_git(["commit", "-m", message], cwd=repo_path)
            head_res = self._run_git(["rev-parse", "HEAD"], cwd=repo_path)
            commit_sha = head_res.stdout.strip()

        # 更新 DB + 写入 SyncRecord（独立 session + commit）
        async with await self._get_session() as session:
            p_stmt = select(ProjectRepo).where(ProjectRepo.project_id == project_id)
            project_orm = (await session.execute(p_stmt)).scalar_one()
            if commit_sha:
                project_orm.current_commit = commit_sha
            project_orm.status = SYNC_STATUS.CLEAN
            await self._record_sync(
                session,
                project_id,
                SYNC_DIRECTIONS.COMMIT,
                commit_sha=commit_sha,
                status="success",
                message=message,
                details={
                    "changed_refs": len(artifacts["changed_refs"]),
                    "had_changes": bool(commit_sha),
                },
            )
            await session.commit()

        return commit_sha

    def _notify_commit_success(self, project_id: str, result: dict[str, Any]) -> None:
        """提交成功后的通知（日志记录，在 lock 释放后执行）.

        与原实现一致：logger.info 在 ``with lock`` 块外执行，确保锁不阻塞
        通知阶段。
        """
        commit_sha = result.get("commit_sha", "")
        changed_count = len(result.get("changed_refs", []))
        logger.info(
            "Committed project %s: %s (changed_refs=%d)",
            project_id,
            commit_sha[:8] if commit_sha else "<no-changes>",
            changed_count,
        )

    async def _rollback_commit(self, project_id: str, partial_state: dict[str, Any]) -> None:
        """commit_project 异常时的回滚占位.

        原实现未引入显式回滚逻辑（DB 操作各自独立 commit；git 异常通过
        抛出向上传递），此处保留为可扩展点，便于后续在出现真正需要回滚
        的场景时统一处理。当前为 no-op。
        """
        # 原实现未做显式回滚；保留方法以便后续扩展。
        return None
