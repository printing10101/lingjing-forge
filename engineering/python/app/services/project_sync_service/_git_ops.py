"""Git 操作 Mixin：封装 git 命令执行与状态查询.

从原 ``project_sync_service.py`` 行 195-277、550-622 迁移而来。
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

from app.contracts.project_sync import SYNC_STATUS
from app.services.project_sync_service._exceptions import (
    GitOperationError,
    GitUnavailableError,
    _GitResult,
)

logger = logging.getLogger(__name__)


class _GitOpsMixin:
    # 宿主契约：由主类提供（mypy 需要显式声明）
    _project_locks: dict[str, Any]
    _project_locks_guard: Any
    _git_available: bool | None
    _git_available_lock: Any
    _repos_root: str
    _GIT_TIMEOUT: float
    """Git 操作 Mixin：封装 git 命令执行与状态查询.

    依赖：
        - ``self._project_locks`` / ``self._project_locks_guard``（在
          ``ProjectSyncService.__init__`` 中初始化）
        - ``self._git_available`` / ``self._git_available_lock``
        - ``self._repos_root``
        - 类属性 ``_GIT_TIMEOUT``
    """

    # 注：_GIT_TIMEOUT / _GIT_TIMEOUT_LONG / _MANIFEST_FILENAME 由
    # ProjectSyncService 在 service.py 中定义为类属性，Mixin 通过继承访问。

    def _check_git_available(self) -> bool:
        """检测系统 git 是否可用（结果缓存）."""
        with self._git_available_lock:
            if self._git_available is not None:
                return self._git_available
            try:
                result = subprocess.run(
                    ["git", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    shell=False,
                )
                self._git_available = result.returncode == 0
            except FileNotFoundError:
                logger.warning("ProjectSyncService: 系统未安装 git，Git 写操作将不可用（查询类操作仍可执行）")
                self._git_available = False
            except subprocess.TimeoutExpired:
                logger.warning("ProjectSyncService: git --version 超时，Git 写操作将不可用")
                self._git_available = False
            return self._git_available

    def _require_git(self) -> None:
        """断言 git 可用，否则抛 GitUnavailableError."""
        if not self._check_git_available():
            raise GitUnavailableError(
                "系统未安装 git 或 git 不可用，无法执行 Git 写操作。请安装 git 并确保其在 PATH 中。"
            )

    def _run_git(
        self,
        args: list[str],
        *,
        cwd: str,
        timeout: float | None = None,
    ) -> _GitResult:
        """执行 git 命令，返回 _GitResult.

        Args:
            args: git 子命令参数列表（如 ``["init"]`` / ``["commit", "-m", msg]``）
            cwd: 工作目录（仓库根路径）
            timeout: 超时秒数（None 使用默认 _GIT_TIMEOUT）

        Returns:
            _GitResult 实例

        Raises:
            GitUnavailableError: git 未安装
            GitOperationError: git 命令返回非零退出码
        """
        self._require_git()
        actual_timeout = timeout or self._GIT_TIMEOUT
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=actual_timeout,
                shell=False,
            )
        except FileNotFoundError as e:
            raise GitUnavailableError("系统未安装 git") from e
        except subprocess.TimeoutExpired as e:
            raise GitOperationError(args, -1, f"git 命令超时 ({actual_timeout}s)") from e

        if result.returncode != 0:
            raise GitOperationError(args, result.returncode, result.stderr)
        return _GitResult(result.returncode, result.stdout, result.stderr)

    def _get_repo_path(self, project_id: str) -> str:
        """返回项目仓库本地路径（不保证存在）."""
        return os.path.join(self._repos_root, project_id)

    def _query_git_status(self, repo_path: str) -> dict[str, Any]:
        """执行 git status / rev-parse，返回状态信息.

        Returns:
            {
                "porcelain": str,         # git status --porcelain 输出
                "head_sha": str,          # 当前 HEAD sha（无 commit 时为空）
                "ahead": int,             # 本地领先远端的 commit 数
                "behind": int,            # 本地落后远端的 commit 数
                "has_remote": bool,       # 是否配置了远端
            }
        """
        result: dict[str, Any] = {
            "porcelain": "",
            "head_sha": "",
            "ahead": 0,
            "behind": 0,
            "has_remote": False,
        }

        # HEAD sha
        try:
            head_res = self._run_git(["rev-parse", "HEAD"], cwd=repo_path)
            result["head_sha"] = head_res.stdout.strip()
        except GitOperationError:
            # 无 commit 时 rev-parse HEAD 失败，正常情况
            pass

        # porcelain status
        status_res = self._run_git(["status", "--porcelain"], cwd=repo_path)
        result["porcelain"] = status_res.stdout.strip()

        # 远端配置检查
        remote_res = self._run_git(["remote"], cwd=repo_path)
        remotes = [r for r in remote_res.stdout.splitlines() if r.strip()]
        result["has_remote"] = bool(remotes)

        # ahead/behind（仅当有远端 + 有上游分支时计算）
        if result["has_remote"] and result["head_sha"]:
            try:
                ahead_res = self._run_git(
                    ["rev-list", "--count", "@{u}..HEAD"],
                    cwd=repo_path,
                )
                result["ahead"] = int(ahead_res.stdout.strip() or "0")
            except GitOperationError:
                # 无上游分支时静默
                pass
            try:
                behind_res = self._run_git(
                    ["rev-list", "--count", "HEAD..@{u}"],
                    cwd=repo_path,
                )
                result["behind"] = int(behind_res.stdout.strip() or "0")
            except GitOperationError:
                pass

        return result

    def _derive_status(self, git_status: dict[str, Any]) -> str:
        """根据 _query_git_status 结果推导 SYNC_STATUS."""
        if git_status["porcelain"]:
            return SYNC_STATUS.DIRTY
        if git_status["ahead"] > 0:
            return SYNC_STATUS.AHEAD
        if git_status["behind"] > 0:
            return SYNC_STATUS.BEHIND
        return SYNC_STATUS.CLEAN
