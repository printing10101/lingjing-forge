"""项目导入导出基础数据结构（从 project_package 拆分，D5）。

只包含无内部依赖的叶子类；依赖这些类型的上层契约见 project_package.py。
契约稳定性：Stable（v1.0.0），与 project_package.py 保持同版本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class PackageFormatVersion:
    """``.lomo`` 包格式版本常量.

    遵循 semver：``MAJOR.MINOR.PATCH``。
    - MAJOR：不兼容的清单结构变更（如字段重命名 / 删除）
    - MINOR：向后兼容的字段新增（导入旧版本包仍可用）
    - PATCH：错误修复与澄清

    导入时校验 ``manifest.format_version`` 的 MAJOR 是否与当前版本一致；
    MINOR / PATCH 差异通过兼容性矩阵处理。
    """

    V1_0_0 = "1.0.0"  # 初始版本：ZIP + manifest.json + 内容寻址

    CURRENT = V1_0_0  # 当前实现版本

    @classmethod
    def all(cls) -> list[str]:
        """返回所有支持的包格式版本."""
        return [cls.V1_0_0]

    @classmethod
    def is_supported(cls, version: str) -> bool:
        """判断包格式版本是否受支持.

        Args:
            version: 待校验的版本字符串

        Returns:
            True 表示当前实现可读取该版本包
        """
        return version in cls.all()

    @classmethod
    def is_major_compatible(cls, version: str) -> bool:
        """判断包格式版本的 MAJOR 段是否与当前版本一致.

        用于导入时兼容性预检：MAJOR 一致表示清单结构兼容，
        可尝试导入（MINOR / PATCH 差异由字段缺省值兜底）。

        Args:
            version: 待校验的版本字符串

        Returns:
            True 表示 MAJOR 段一致
        """
        if not version or "." not in version:
            return False
        try:
            major = int(version.split(".", 1)[0])
            current_major = int(cls.CURRENT.split(".", 1)[0])
            return major == current_major
        except (ValueError, IndexError):
            return False


class ContentPolicy:
    """内容策略常量：决定 ``.lomo`` 包打包资源内容的范围.

    - ``METADATA_ONLY``：仅打包元数据，不打包资源内容。适用项目结构分享、
      文档归档场景。包体积小，但导入后无法直接运行工作流。
    - ``INCLUDE_CONTENT``：打包所有资源内容（默认）。适用跨机器迁移、
      完整备份场景。包体积大，但导入后立即可用。
    - ``SMALL_FILES_ONLY``：仅打包 ≤ ``max_file_size_bytes`` 的资源文件，
      大文件仅元数据。适用网络受限场景（如邮件附件），平衡包体积与可用性。
    """

    METADATA_ONLY = "metadata_only"
    INCLUDE_CONTENT = "include_content"
    SMALL_FILES_ONLY = "small_files_only"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有内容策略."""
        return [cls.METADATA_ONLY, cls.INCLUDE_CONTENT, cls.SMALL_FILES_ONLY]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断内容策略是否合法."""
        return value in cls.all()

    @classmethod
    def default(cls) -> str:
        """返回默认内容策略."""
        return cls.INCLUDE_CONTENT


class ConflictStrategy:
    """冲突策略常量：导入时目标机器已存在同 URI 资源的处理方式.

    - ``SKIP``：跳过冲突资源（保留目标机器已有版本，默认）。最安全，但可能
      导致项目状态与源机器不一致。
    - ``OVERWRITE``：覆盖目标机器已有版本。危险，需前端二次确认；适用于
      目标机器资源明显过时的场景。
    - ``RENAME``：重命名导入资源（URI 追加 ``_imported_<timestamp>`` 后缀）。
      保留两份资源，由用户后续手动合并。
    - ``FAIL``：遇到冲突立即报错，不导入任何资源（事务性）。适用于要求
      "全有或全无"的批量导入场景。
    """

    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"
    FAIL = "fail"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有冲突策略."""
        return [cls.SKIP, cls.OVERWRITE, cls.RENAME, cls.FAIL]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断冲突策略是否合法."""
        return value in cls.all()

    @classmethod
    def default(cls) -> str:
        """返回默认冲突策略."""
        return cls.SKIP


class PackageTaskStatus:
    """包任务状态常量：导出 / 导入任务的异步执行状态.

    状态机：
        PENDING → RUNNING → COMPLETED
                ↘ FAILED
    """

    PENDING = "pending"  # 已创建任务记录，未开始执行
    RUNNING = "running"  # 正在执行（导出打包 / 导入解压）
    COMPLETED = "completed"  # 成功完成
    FAILED = "failed"  # 执行失败（error_message 记录原因）

    @classmethod
    def all(cls) -> list[str]:
        """返回所有任务状态."""
        return [cls.PENDING, cls.RUNNING, cls.COMPLETED, cls.FAILED]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断任务状态是否合法."""
        return value in cls.all()

    @classmethod
    def is_terminal(cls, value: str) -> bool:
        """判断任务状态是否为终态（不可再变更）."""
        return value in (cls.COMPLETED, cls.FAILED)


@dataclass(frozen=True)
class PackageResourceEntry:
    """包资源条目契约：``manifest.resources`` 数组中的一项.

    一个 PackageResourceEntry 对应包内一个资源文件，记录其 URI / 内容 hash /
    包内相对路径 / 大小 / 元数据。导入时按 ``resource_uri`` 寻址目标位置，
    按 ``content_hash`` 校验完整性。

    属性:
        resource_type: 资源类型（dataset/model/workflow/config/snapshot/lineage）
        resource_uri: 资源 URI（与 ADR-005 / ADR-011 / ADR-012 对齐）
        content_hash: 内容 sha256，格式 ``sha256:<hex>``；元数据策略下为空字符串
        path_in_package: 包内相对路径（如 ``datasets/<id>/versions/1.0.0/data.parquet``）
        size_bytes: 资源文件大小（字节）；元数据策略下为 0
        metadata: 资源元数据（如 row_count / schema / model_type / framework）
    """

    resource_type: str
    resource_uri: str
    content_hash: str
    path_in_package: str
    size_bytes: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_type:
            raise ValueError("PackageResourceEntry.resource_type 不能为空")
        if not self.resource_uri:
            raise ValueError("PackageResourceEntry.resource_uri 不能为空")
        if not self.path_in_package:
            raise ValueError("PackageResourceEntry.path_in_package 不能为空")
        if self.size_bytes < 0:
            raise ValueError(f"PackageResourceEntry.size_bytes 不能为负数: {self.size_bytes}")

    @property
    def has_content(self) -> bool:
        """是否包含内容（content_hash 非空且 size_bytes > 0）."""
        return bool(self.content_hash) and self.size_bytes > 0

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 manifest.json 与 API 响应）."""
        return {
            "resource_type": self.resource_type,
            "resource_uri": self.resource_uri,
            "content_hash": self.content_hash,
            "path_in_package": self.path_in_package,
            "size_bytes": self.size_bytes,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackageResourceEntry":
        """从 dict 反序列化（用于读取 manifest.json）."""
        return cls(
            resource_type=str(data["resource_type"]),
            resource_uri=str(data["resource_uri"]),
            content_hash=str(data.get("content_hash", "")),
            path_in_package=str(data["path_in_package"]),
            size_bytes=int(data.get("size_bytes", 0)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SourceMachineInfo:
    """源机器信息契约：导出时记录源机器环境，用于诊断兼容性问题.

    属性:
        hostname: 主机名（如 ``workshop-pc-01``）
        app_version: 导出时应用版本（如 ``4.0.0``）
        platform: 平台标识（``win32`` / ``linux`` / ``darwin``）
    """

    hostname: str
    app_version: str
    platform: str

    def __post_init__(self) -> None:
        if not self.hostname:
            raise ValueError("SourceMachineInfo.hostname 不能为空")
        if not self.app_version:
            raise ValueError("SourceMachineInfo.app_version 不能为空")
        if not self.platform:
            raise ValueError("SourceMachineInfo.platform 不能为空")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict."""
        return {
            "hostname": self.hostname,
            "app_version": self.app_version,
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceMachineInfo":
        """从 dict 反序列化."""
        return cls(
            hostname=str(data["hostname"]),
            app_version=str(data["app_version"]),
            platform=str(data["platform"]),
        )


@dataclass(frozen=True)
class PackageProjectInfo:
    """包项目元数据契约：``manifest.project`` 字段.

    与 ADR-011 ``ProjectSyncManifest`` 的核心字段对齐，导入时用于创建目标项目
    或匹配已有项目。

    属性:
        project_id: 源项目 ID（UUID）；导入时若 ``reinit_git=True`` 会生成新 ID
        name: 项目显示名
        description: 项目描述
        author: 项目作者
        remote_url: 源项目远端仓库 URL（空字符串表示纯本地项目）
        current_branch: 源项目当前分支名
        current_commit: 源项目当前 HEAD commit sha
    """

    project_id: str
    name: str
    description: str = ""
    author: str = ""
    remote_url: str = ""
    current_branch: str = "main"
    current_commit: str = ""

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("PackageProjectInfo.project_id 不能为空")
        if not self.name:
            raise ValueError("PackageProjectInfo.name 不能为空")
        if not self.current_branch:
            raise ValueError("PackageProjectInfo.current_branch 不能为空")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict."""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "remote_url": self.remote_url,
            "current_branch": self.current_branch,
            "current_commit": self.current_commit,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackageProjectInfo":
        """从 dict 反序列化."""
        return cls(
            project_id=str(data["project_id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            author=str(data.get("author", "")),
            remote_url=str(data.get("remote_url", "")),
            current_branch=str(data.get("current_branch", "main")),
            current_commit=str(data.get("current_commit", "")),
        )


@dataclass(frozen=True)
class ImportResourceRecord:
    """导入资源记录：单个资源的导入结果.

    属性:
        resource_uri: 资源 URI
        action: 导入动作（imported/skipped/renamed/failed）
        target_uri: 目标 URI（rename 策略下与 resource_uri 不同；其他策略下相同）
        error_message: 失败原因（action=failed 时非空）
    """

    resource_uri: str
    action: str  # imported / skipped / renamed / failed
    target_uri: str = ""
    error_message: str = ""

    def __post_init__(self) -> None:
        if not self.resource_uri:
            raise ValueError("ImportResourceRecord.resource_uri 不能为空")
        if self.action not in ("imported", "skipped", "renamed", "failed"):
            raise ValueError(f"ImportResourceRecord.action 不合法: {self.action}")
        if not self.target_uri:
            # 默认 target_uri = resource_uri（rename 策略下由服务层覆盖）
            object.__setattr__(self, "target_uri", self.resource_uri)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict."""
        return {
            "resource_uri": self.resource_uri,
            "action": self.action,
            "target_uri": self.target_uri,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ValidationResult:
    """包校验结果契约：``ProjectPackageService.validate_package()`` 返回值.

    校验项：
        1. ``manifest.json`` 可解析
        2. ``format_version`` 受支持
        3. ``checksum`` 与重新计算的 sha256 一致
        4. 每个资源条目的 ``content_hash`` 与包内文件实际 sha256 一致
        5. ``path_in_package`` 指向的文件存在于包内

    属性:
        package_path: ``.lomo`` 文件路径
        is_valid: 整体是否通过校验
        format_version: 包格式版本（解析失败时为空字符串）
        checksum_verified: checksum 是否一致
        resource_count: 资源条目总数
        verified_count: 通过校验的资源数
        errors: 错误信息列表（致命问题）
        warnings: 警告信息列表（非致命问题）
        validated_at: 校验时间
    """

    package_path: str
    is_valid: bool
    format_version: str = ""
    checksum_verified: bool = False
    resource_count: int = 0
    verified_count: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    validated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.package_path:
            raise ValueError("ValidationResult.package_path 不能为空")
        if self.resource_count < 0:
            raise ValueError(f"ValidationResult.resource_count 不能为负数: {self.resource_count}")
        if self.verified_count < 0 or self.verified_count > self.resource_count:
            raise ValueError(f"ValidationResult.verified_count 不合法: {self.verified_count}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应）."""
        return {
            "package_path": self.package_path,
            "is_valid": self.is_valid,
            "format_version": self.format_version,
            "checksum_verified": self.checksum_verified,
            "resource_count": self.resource_count,
            "verified_count": self.verified_count,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "validated_at": self.validated_at.isoformat() if self.validated_at else "",
        }
