"""项目级 Git 同步契约：定义资源引用、项目同步清单、同步记录的统一接口.

对应 ADR-011（项目级 Git 同步）。本文件只定义数据结构与接口契约，
实现见 app/services/project_sync_service.py（服务层）、
app/api/v1/project_sync.py（路由层）、
app/database/models/project_sync.py（ORM 持久化）。

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点：
    1. 不修改现有 ProjectStore（.vrm ZIP 包保留给离线 CAD 工程包），
       新建独立的 ProjectSyncService 管理可同步项目
    2. 资源引用通过 URI + content_hash 实现内容寻址同步
    3. 同步策略（sync_strategy）根据资源类型与大小自动选择：
       git_tracked / hash_referenced / git_lfs
    4. 同步状态机：clean / dirty / ahead / behind / conflict / error
    5. 使用 subprocess.run(["git", ...]) 调用系统 git，不引入 gitpython 依赖

资源 URI 体系（与 ADR-005 对齐）：
    dataset://<dataset_id>/<version>
    model://<model_name>/<version>
    workflow://<run_id>
    config://<spec_name>
    snapshot://<snapshot_id>
    template://<template_id>/<version>
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# 资源类型常量（与 ADR-005 资源 URI 体系对齐）


class RESOURCE_TYPES:
    """项目同步资源类型常量.

    与 ADR-005 定义的资源 URI 体系对齐，每种类型对应一种 URI scheme。
    项目同步支持这 6 种资源类型的引用与内容寻址同步。
    """

    DATASET = "dataset"  # 数据集（含版本 + schema + content）
    MODEL = "model"  # 模型产物（.pt / .onnx / .safetensors）
    WORKFLOW = "workflow"  # 工作流运行（WorkflowSpec + run record）
    CONFIG = "config"  # 配置规格（ConfigSpec YAML）
    SNAPSHOT = "snapshot"  # 实验快照（ExperimentSnapshot）
    TEMPLATE = "template"  # 工作流模板（WorkflowTemplateManifest）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有支持的资源类型."""
        return [
            cls.DATASET,
            cls.MODEL,
            cls.WORKFLOW,
            cls.CONFIG,
            cls.SNAPSHOT,
            cls.TEMPLATE,
        ]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断资源类型是否合法."""
        return value in cls.all()


# 同步策略常量


class SYNC_STRATEGIES:
    """资源同步策略常量.

    根据资源类型与大小自动选择，决定资源如何被 Git 跟踪。

    策略选择参考：
        - git_tracked: 直接入 Git（文本文件：YAML / JSON 清单 / workflow spec）
        - hash_referenced: 仅记录 content_hash，实际数据通过 content-addressable
          storage 共享（数据集内容 .jsonl、模型文件 .pt、快照二进制）
        - git_lfs: 通过 Git LFS 跟踪（中等大小文件 10MB-1GB，需用户配置 LFS）
    """

    GIT_TRACKED = "git_tracked"
    HASH_REFERENCED = "hash_referenced"
    GIT_LFS = "git_lfs"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有同步策略."""
        return [cls.GIT_TRACKED, cls.HASH_REFERENCED, cls.GIT_LFS]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断同步策略是否合法."""
        return value in cls.all()


# 资源类型 默认同步策略映射（服务层创建 ResourceRef 时使用）
DEFAULT_SYNC_STRATEGY: dict[str, str] = {
    RESOURCE_TYPES.DATASET: SYNC_STRATEGIES.HASH_REFERENCED,
    RESOURCE_TYPES.MODEL: SYNC_STRATEGIES.HASH_REFERENCED,
    RESOURCE_TYPES.WORKFLOW: SYNC_STRATEGIES.GIT_TRACKED,
    RESOURCE_TYPES.CONFIG: SYNC_STRATEGIES.GIT_TRACKED,
    RESOURCE_TYPES.SNAPSHOT: SYNC_STRATEGIES.HASH_REFERENCED,
    RESOURCE_TYPES.TEMPLATE: SYNC_STRATEGIES.GIT_TRACKED,
}


# 同步状态常量


class SYNC_STATUS:
    """项目同步状态常量.

    状态机：
        init → clean
        clean → dirty（资源 hash 变化检测到）
        dirty → clean（commit 后）
        clean → ahead（本地 commit 未 push）
        ahead → clean（push 后）
        clean → behind（远端有新 commit 未 pull）
        behind → clean（pull 后）
        clean → conflict（merge 冲突）
        conflict → clean（手动 merge 解决后）
        任意 → error（Git 操作异常，如 git 不可用）
        error → clean（修复后重新检测）
    """

    CLEAN = "clean"  # 工作区干净，与远端同步
    DIRTY = "dirty"  # 本地有未提交变更
    AHEAD = "ahead"  # 本地领先远端（未 push 的 commit）
    BEHIND = "behind"  # 本地落后远端（未 pull 的 commit）
    CONFLICT = "conflict"  # merge 冲突
    ERROR = "error"  # Git 操作错误（如 git 不可用）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有同步状态."""
        return [cls.CLEAN, cls.DIRTY, cls.AHEAD, cls.BEHIND, cls.CONFLICT, cls.ERROR]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断同步状态是否合法."""
        return value in cls.all()


# 同步方向常量


class SYNC_DIRECTIONS:
    """同步方向常量（用于 SyncRecord.direction 字段）.

    每种方向对应一次 Git 写操作，生成一条 SyncRecord 审计记录。
    """

    INIT = "init"  # git init（初始化仓库）
    COMMIT = "commit"  # git commit（本地提交）
    PUSH = "push"  # git push（推送到远端）
    PULL = "pull"  # git pull（从远端拉取）
    CLONE = "clone"  # git clone（克隆远端仓库）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有同步方向."""
        return [cls.INIT, cls.COMMIT, cls.PUSH, cls.PULL, cls.CLONE]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断同步方向是否合法."""
        return value in cls.all()


# URI 解析工具


def parse_resource_uri(uri: str) -> tuple[str, str]:
    """解析资源 URI，返回 (resource_type, path) 元组.

    URI 格式：
        dataset://<dataset_id>/<version>
        model://<model_name>/<version>
        workflow://<run_id>
        config://<spec_name>
        snapshot://<snapshot_id>
        template://<template_id>/<version>

    Args:
        uri: 资源 URI 字符串

    Returns:
        (resource_type, path) 元组，path 为 URI 中 ``scheme://`` 之后的部分

    Raises:
        ValueError: URI 格式无效或 scheme 不在 RESOURCE_TYPES 中
    """
    if "://" not in uri:
        raise ValueError(f"资源 URI 格式无效（缺少 scheme://）: {uri}")
    scheme, path = uri.split("://", 1)
    if not RESOURCE_TYPES.is_valid(scheme):
        raise ValueError(f"资源 URI scheme 不支持: {scheme}（支持: {RESOURCE_TYPES.all()}）")
    if not path:
        raise ValueError(f"资源 URI path 为空: {uri}")
    return scheme, path


def build_resource_uri(resource_type: str, *path_parts: str) -> str:
    """构造资源 URI.

    Args:
        resource_type: 资源类型（必须在 RESOURCE_TYPES 中）
        path_parts: URI path 各部分（按顺序拼接，用 ``/`` 分隔）

    Returns:
        资源 URI 字符串

    Raises:
        ValueError: 资源类型不合法或 path_parts 为空
    """
    if not RESOURCE_TYPES.is_valid(resource_type):
        raise ValueError(f"资源类型不支持: {resource_type}")
    if not path_parts:
        raise ValueError("path_parts 不能为空")
    path = "/".join(str(p).strip("/") for p in path_parts if str(p).strip("/"))
    if not path:
        raise ValueError("path_parts 拼接后为空")
    return f"{resource_type}://{path}"


# 数据类：资源引用


@dataclass(frozen=True)
class ResourceRef:
    """资源引用契约：项目中的一个资源引用（不存储内容，仅记录 hash）.

    一个 ResourceRef 对应项目清单中的一个资源条目。资源的实际内容
    通过 content_hash 实现内容寻址，不直接存入 Git（除非
    sync_strategy=git_tracked）。

    属性:
        project_id: 所属项目 ID（对应 ProjectSyncManifest.project_id）
        resource_type: 资源类型（RESOURCE_TYPES 之一）
        resource_uri: 资源 URI（如 ``dataset://phm2010/v3``）
        content_hash: 内容哈希（sha256 hex，64 字符）。空字符串表示未计算
        sync_strategy: 同步策略（SYNC_STRATEGIES 之一）
        metadata: 附加元数据（如文件大小、来源插件 id、自定义标签）
    """

    project_id: str
    resource_type: str
    resource_uri: str
    content_hash: str = ""
    sync_strategy: str = SYNC_STRATEGIES.HASH_REFERENCED
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("ResourceRef.project_id 不能为空")
        if not RESOURCE_TYPES.is_valid(self.resource_type):
            raise ValueError(f"ResourceRef.resource_type 不支持: {self.resource_type}")
        if not self.resource_uri:
            raise ValueError("ResourceRef.resource_uri 不能为空")
        # 校验 URI scheme 与 resource_type 一致
        scheme, _ = parse_resource_uri(self.resource_uri)
        if scheme != self.resource_type:
            raise ValueError(f"ResourceRef URI scheme ({scheme}) 与 resource_type ({self.resource_type}) 不匹配")
        if not SYNC_STRATEGIES.is_valid(self.sync_strategy):
            raise ValueError(f"ResourceRef.sync_strategy 不支持: {self.sync_strategy}")

    @property
    def path(self) -> str:
        """URI 中 ``scheme://`` 之后的部分（如 ``phm2010/v3``）."""
        _, path = parse_resource_uri(self.resource_uri)
        return path

    @property
    def has_hash(self) -> bool:
        """是否已计算 content_hash."""
        return bool(self.content_hash)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应与 ORM 投影）."""
        return {
            "project_id": self.project_id,
            "resource_type": self.resource_type,
            "resource_uri": self.resource_uri,
            "content_hash": self.content_hash,
            "sync_strategy": self.sync_strategy,
            "metadata": dict(self.metadata),
        }


# 数据类：项目同步清单


@dataclass(frozen=True)
class ProjectSyncManifest:
    """项目同步清单契约：一个可同步项目的元数据 + 当前状态 + 资源引用列表.

    一个 ProjectSyncManifest 对应一个 Git 仓库，仓库根目录包含
    ``.lomo-project.yaml`` 文件（本清单的 YAML 投影）。

    属性:
        project_id: 项目 ID（UUID，主键）
        name: 项目显示名
        repo_path: 仓库本地路径（绝对路径或相对 output_dir 的路径）
        remote_url: 远端仓库 URL（空字符串表示纯本地仓库）
        current_branch: 当前分支名（默认 ``main``）
        current_commit: 当前 HEAD commit sha（空字符串表示未提交）
        status: 同步状态（SYNC_STATUS 之一）
        resource_refs: 资源引用列表（按 resource_uri 唯一）
        created_at: 创建时间（ISO8601 字符串）
        updated_at: 最后更新时间（ISO8601 字符串）
        description: 项目描述
        author: 项目作者
    """

    project_id: str
    name: str
    repo_path: str
    remote_url: str = ""
    current_branch: str = "main"
    current_commit: str = ""
    status: str = SYNC_STATUS.CLEAN
    resource_refs: tuple[ResourceRef, ...] = field(default_factory=tuple)
    created_at: str = ""
    updated_at: str = ""
    description: str = ""
    author: str = ""

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("ProjectSyncManifest.project_id 不能为空")
        if not self.name:
            raise ValueError("ProjectSyncManifest.name 不能为空")
        if not self.repo_path:
            raise ValueError("ProjectSyncManifest.repo_path 不能为空")
        if not self.current_branch:
            raise ValueError("ProjectSyncManifest.current_branch 不能为空")
        if not SYNC_STATUS.is_valid(self.status):
            raise ValueError(f"ProjectSyncManifest.status 不支持: {self.status}")
        # resource_refs 唯一性校验
        uris = [ref.resource_uri for ref in self.resource_refs]
        if len(uris) != len(set(uris)):
            raise ValueError("ProjectSyncManifest.resource_refs 存在重复的 resource_uri")

    @property
    def resource_count(self) -> int:
        """资源引用数量."""
        return len(self.resource_refs)

    @property
    def is_dirty(self) -> bool:
        """是否有未提交变更."""
        return self.status == SYNC_STATUS.DIRTY

    @property
    def has_remote(self) -> bool:
        """是否配置了远端仓库."""
        return bool(self.remote_url)

    def get_ref(self, resource_uri: str) -> ResourceRef | None:
        """按 URI 查询资源引用."""
        for ref in self.resource_refs:
            if ref.resource_uri == resource_uri:
                return ref
        return None

    def list_refs_by_type(self, resource_type: str) -> list[ResourceRef]:
        """按资源类型过滤资源引用."""
        return [ref for ref in self.resource_refs if ref.resource_type == resource_type]

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应与 ORM 投影）."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "repo_path": self.repo_path,
            "remote_url": self.remote_url,
            "current_branch": self.current_branch,
            "current_commit": self.current_commit,
            "status": self.status,
            "resource_refs": [ref.to_dict() for ref in self.resource_refs],
            "resource_count": self.resource_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "description": self.description,
            "author": self.author,
        }


# 数据类：同步记录


@dataclass(frozen=True)
class SyncRecord:
    """同步记录契约：一次 Git 操作（init/commit/push/pull/clone）的审计记录.

    每次 Git 写操作（init/commit/push/pull/clone）都生成一条 SyncRecord，
    持久化到 project_sync_records 表，用于审计与回溯。

    属性:
        record_id: 记录 ID（UUID，主键）
        project_id: 所属项目 ID
        direction: 同步方向（SYNC_DIRECTIONS 之一）
        commit_sha: 涉及的 commit sha（push/pull/commit 时填写，init/clone 可为空）
        status: 操作结果状态（``success`` / ``failed`` / ``conflict``）
        message: 操作消息（commit message 或错误描述）
        timestamp: 时间戳（ISO8601 字符串）
        details: 附加详情（如变更文件数、字节数、远端 URL）
    """

    record_id: str
    project_id: str
    direction: str
    commit_sha: str = ""
    status: str = "success"
    message: str = ""
    timestamp: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id:
            raise ValueError("SyncRecord.record_id 不能为空")
        if not self.project_id:
            raise ValueError("SyncRecord.project_id 不能为空")
        if not SYNC_DIRECTIONS.is_valid(self.direction):
            raise ValueError(f"SyncRecord.direction 不支持: {self.direction}")
        if self.status not in ("success", "failed", "conflict"):
            raise ValueError(f"SyncRecord.status 不支持: {self.status}")

    @property
    def is_success(self) -> bool:
        """操作是否成功."""
        return self.status == "success"

    @property
    def is_failed(self) -> bool:
        """操作是否失败."""
        return self.status == "failed"

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应与 ORM 投影）."""
        return {
            "record_id": self.record_id,
            "project_id": self.project_id,
            "direction": self.direction,
            "commit_sha": self.commit_sha,
            "status": self.status,
            "message": self.message,
            "timestamp": self.timestamp,
            "details": dict(self.details),
        }


__all__ = [
    # 常量类
    "RESOURCE_TYPES",
    "SYNC_STRATEGIES",
    "SYNC_STATUS",
    "SYNC_DIRECTIONS",
    # 默认策略映射
    "DEFAULT_SYNC_STRATEGY",
    # URI 工具
    "parse_resource_uri",
    "build_resource_uri",
    # 数据类
    "ResourceRef",
    "ProjectSyncManifest",
    "SyncRecord",
]
