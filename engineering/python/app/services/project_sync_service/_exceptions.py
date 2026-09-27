"""ProjectSyncService 自定义异常 + Git 操作结果数据类.

从原 ``project_sync_service.py`` 行 80-144 迁移而来。
"""


class ProjectSyncError(RuntimeError):
    """项目同步服务基类异常."""


class ProjectNotFoundError(LookupError):
    """项目不存在."""


class ProjectAlreadyExistsError(ValueError):
    """项目名或路径已存在."""


class GitUnavailableError(ProjectSyncError):
    """系统未安装 git 或 git 命令不可用."""


class GitOperationError(ProjectSyncError):
    """git 命令执行失败（非零退出码）."""

    def __init__(self, args: list[str], returncode: int, stderr: str) -> None:
        self.args_ = args
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(args)} 失败 (rc={returncode}): {stderr.strip()}")


class ResourceRefNotFoundError(LookupError):
    """资源引用不存在."""


class ResourceRefAlreadyExistsError(ValueError):
    """资源 URI 已存在（同项目内重复）."""


class InvalidProjectStateError(ProjectSyncError):
    """项目状态不允许该操作（如未配置 remote 时 push）."""


# Git 操作结果数据类


class _GitResult:
    """git 命令执行结果（内部使用）."""

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


__all__ = [
    "ProjectSyncError",
    "ProjectNotFoundError",
    "ProjectAlreadyExistsError",
    "GitUnavailableError",
    "GitOperationError",
    "ResourceRefNotFoundError",
    "ResourceRefAlreadyExistsError",
    "InvalidProjectStateError",
    "_GitResult",
]
