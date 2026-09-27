"""Git 信息采集器.

对应 core-contracts-design.md 第 7 章 / ADR-005 阶段 2.

设计目标：
    - 自动采集当前代码的 git commit SHA 与 dirty 状态
    - 在非 git 环境（解压包/无 git 安装）下优雅降级
    - 采集结果带 TTL 缓存，避免高频调用 subprocess

采集信息：
    - git_sha：``git rev-parse HEAD``
    - code_dirty：``git status --porcelain`` 输出非空则 True

非 git 环境降级策略：
    - git_sha 返回 ``"unknown"``
    - code_dirty 返回 ``True``（无法保证代码版本一致性，按最保守处理）
    - 同时记录 warning 日志，便于排查
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from app.config.limits import GIT_COMMAND_TIMEOUT_SEC

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GitInfo:
    """git 采集结果（不可变）."""

    git_sha: str
    code_dirty: bool
    collected_at: float  # Unix 时间戳
    is_real: bool  # True 表示从真实 git 仓库采集；False 表示降级占位


# 缓存 TTL（秒）。同进程内 60s 内不重复采集
_DEFAULT_TTL_SECONDS = 60.0


class GitCollector:
    """git 信息采集器（线程安全 + TTL 缓存）."""

    def __init__(
        self,
        *,
        repo_root: str | None = None,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._repo_root = repo_root or os.getcwd()
        self._ttl = max(0.0, ttl_seconds)
        self._cache: GitInfo | None = None
        self._lock = threading.RLock()

    # 公开 API

    def collect(self, *, force_refresh: bool = False) -> GitInfo:
        """采集 git 信息.

        Args:
            force_refresh: True 时跳过缓存强制重新采集

        Returns:
            GitInfo 数据类
        """
        with self._lock:
            if not force_refresh and self._cache is not None and (time.time() - self._cache.collected_at) < self._ttl:
                return self._cache

            info = self._collect_uncached()
            self._cache = info
            return info

    def get_sha(self) -> str:
        """便捷方法：返回当前 git SHA."""
        return self.collect().git_sha

    def is_dirty(self) -> bool:
        """便捷方法：返回当前 dirty 状态."""
        return self.collect().code_dirty

    # 内部实现

    def _collect_uncached(self) -> GitInfo:
        """实际执行 git 命令采集信息（无缓存）."""
        sha = self._run_git(["rev-parse", "HEAD"])
        if sha is None:
            logger.warning("GitCollector: 非 git 环境或 git 命令失败，降级为 unknown+dirty=True")
            return GitInfo(
                git_sha="unknown",
                code_dirty=True,
                collected_at=time.time(),
                is_real=False,
            )

        porcelain = self._run_git(["status", "--porcelain"])
        code_dirty = bool(porcelain and porcelain.strip())

        return GitInfo(
            git_sha=sha.strip(),
            code_dirty=code_dirty,
            collected_at=time.time(),
            is_real=True,
        )

    def _run_git(self, args: list[str]) -> str | None:
        """执行 git 命令，返回 stdout（失败返回 None）.

        失败原因可能是：
            - git 未安装（FileNotFoundError）
            - 当前目录非 git 仓库（非零退出码）
            - 超时（subprocess.TimeoutExpired）
        """
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self._repo_root,
                capture_output=True,
                text=True,
                timeout=GIT_COMMAND_TIMEOUT_SEC,
                # 避免在 Windows 弹出新窗口
                shell=False,
            )
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            logger.warning("GitCollector: git %s 超时", " ".join(args))
            return None
        except subprocess.SubprocessError as e:
            logger.warning("GitCollector: git %s 异常: %s", " ".join(args), e)
            return None

        if result.returncode != 0:
            return None
        return result.stdout


# 单例
_collector: GitCollector | None = None
_singleton_lock = threading.Lock()


def get_git_collector() -> GitCollector:
    """获取全局 GitCollector 单例."""
    global _collector
    if _collector is None:
        with _singleton_lock:
            if _collector is None:
                _collector = GitCollector()
    return _collector


def collect_git_info(*, force_refresh: bool = False) -> GitInfo:
    """便捷函数：调用全局单例采集 git 信息."""
    return get_git_collector().collect(force_refresh=force_refresh)


__all__ = [
    "GitInfo",
    "GitCollector",
    "get_git_collector",
    "collect_git_info",
]
