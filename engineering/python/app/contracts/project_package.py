"""项目导入导出契约：定义 ``.lomo`` 包格式与导入导出操作的数据结构.

对应 ADR-015（项目导入导出）。本文件只定义数据结构与接口契约，
实现见 app/services/project_package_service.py（服务层）、
app/api/v1/project_packages.py（路由层）、
app/database/models/project_package.py（ORM 持久化）。

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点：
    1. ``.lomo`` 包本质是 ZIP 归档（扩展名 ``.lomo``），与 ADR-011 Git 同步互补——
       Git 同步是"引用同步"（仅 hash），``.lomo`` 包是"内容同步"（含文件）
    2. ``manifest.json`` 是包清单，含格式版本 + 项目元数据 + 资源清单 + 校验和，
       导入时优先校验 ``format_version`` 兼容性，再校验 ``checksum`` 完整性
    3. ``ContentPolicy`` 决定打包范围：``metadata_only`` / ``include_content`` /
       ``small_files_only``，适配车间大文件与网络受限场景
    4. ``ConflictStrategy`` 决定冲突处理：``skip`` / ``overwrite`` / ``rename`` /
       ``fail``，覆盖安全导入与事务性导入需求
    5. 资源 URI 体系与 ADR-005 / ADR-011 / ADR-012 对齐：
       ``dataset://<dataset_id>/<version>`` / ``model://<name>/<version>`` /
       ``workflow://<run_id>`` / ``config://<spec_name>`` / ``snapshot://<snapshot_id>``

包文件结构详见 ADR-015 第 1 节。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.contracts._base_models import (
    PackageFormatVersion,
    ContentPolicy,
    ConflictStrategy,
    PackageTaskStatus,
    PackageResourceEntry,
    SourceMachineInfo,
    PackageProjectInfo,
    ImportResourceRecord,
    ValidationResult,
)

# 流式传输缓冲区大小（1 MiB），与 config/limits.py 的 STREAM_CHUNK_SIZE 保持同步
# 定义在 contracts 层而非从 config 导入，避免 contracts config 循环依赖
STREAM_BUFFER_SIZE: int = 1024 * 1024


# 包格式版本常量


# 内容策略常量


# 冲突策略常量


# 任务状态常量（导出 / 导入共用）


# 默认值常量


#: : ``small_files_only`` 策略的默认文件大小阈值（10 MB）。
DEFAULT_MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024

#: : 流式读写缓冲区大小（64 KB），避免内存爆炸。
#: :
#: : 集中定义于 ``app.config.limits.STREAM_BUFFER_SIZE``（同义别名
#: : ``STREAM_CHUNK_SIZE``），本模块顶部已 ``import``，外部
#: : ``from app.contracts.project_package import STREAM_BUFFER_SIZE`` 仍可用。

#: : ``.lomo`` 包文件扩展名。
PACKAGE_FILE_EXTENSION: str = ".lomo"

#: : 导出包文件名模板（``<project_name>_<timestamp>.lomo``）。
PACKAGE_FILENAME_TEMPLATE: str = "{name}_{timestamp}.lomo"

#: : 源机器信息兜底默认值（socket.gethostname() / platform.system() 返回空时使用）。
SOURCE_MACHINE_INFO_DEFAULTS: dict[str, str] = {
    "hostname": "unknown-host",
    "app_version": "4.0.0",
    "platform": "unknown",
}


# 数据结构：包资源条目


# 数据结构：源机器信息


# 数据结构：包项目元数据


# 数据结构：包清单


@dataclass(frozen=True)
class PackageManifest:
    """包清单契约：``.lomo`` 包的 ``manifest.json`` 投影.

    一个 PackageManifest 对应一个 ``.lomo`` 包，记录格式版本 + 导出时间 +
    导出者 + 源机器 + 项目元数据 + 资源清单 + 内容策略 + 总大小 + 校验和。

    属性:
        format_version: 包格式版本（PackageFormatVersion 常量）
        exported_at: 导出时间（ISO8601 字符串）
        exported_by: 导出者 user_id 或 plugin_id
        source_machine: 源机器信息
        project: 项目元数据
        resources: 资源清单（按 resource_uri 唯一）
        content_policy: 内容策略（ContentPolicy 常量）
        total_size_bytes: 包内所有资源文件总大小（未压缩前）
        checksum: manifest.json 自身的 sha256（不含此字段），由服务层计算
    """

    format_version: str
    exported_at: str
    exported_by: str
    source_machine: SourceMachineInfo
    project: PackageProjectInfo
    resources: tuple[PackageResourceEntry, ...] = field(default_factory=tuple)
    content_policy: str = ContentPolicy.INCLUDE_CONTENT
    total_size_bytes: int = 0
    checksum: str = ""

    def __post_init__(self) -> None:
        if not PackageFormatVersion.is_supported(self.format_version):
            raise ValueError(
                f"PackageManifest.format_version 不受支持: {self.format_version}，"
                f"受支持版本: {PackageFormatVersion.all()}"
            )
        if not self.exported_at:
            raise ValueError("PackageManifest.exported_at 不能为空")
        if not self.exported_by:
            raise ValueError("PackageManifest.exported_by 不能为空")
        if not ContentPolicy.is_valid(self.content_policy):
            raise ValueError(
                f"PackageManifest.content_policy 不合法: {self.content_policy}，合法值: {ContentPolicy.all()}"
            )
        if self.total_size_bytes < 0:
            raise ValueError(f"PackageManifest.total_size_bytes 不能为负数: {self.total_size_bytes}")
        # resources 唯一性校验
        uris = [entry.resource_uri for entry in self.resources]
        if len(uris) != len(set(uris)):
            raise ValueError("PackageManifest.resources 存在重复的 resource_uri")

    @property
    def resource_count(self) -> int:
        """资源条目数量."""
        return len(self.resources)

    @property
    def has_content(self) -> bool:
        """是否包含任何资源内容（至少一个条目 has_content=True）."""
        return any(entry.has_content for entry in self.resources)

    def get_entry(self, resource_uri: str) -> PackageResourceEntry | None:
        """按 URI 查询资源条目."""
        for entry in self.resources:
            if entry.resource_uri == resource_uri:
                return entry
        return None

    def list_entries_by_type(self, resource_type: str) -> list[PackageResourceEntry]:
        """按资源类型过滤资源条目."""
        return [entry for entry in self.resources if entry.resource_type == resource_type]

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 manifest.json 写入）.

        注意：``checksum`` 字段由服务层在写入 manifest.json 后单独计算并回填，
        此处序列化时若 ``checksum`` 为空则不写入该字段（避免空值污染清单）。
        """
        result: dict[str, Any] = {
            "format_version": self.format_version,
            "exported_at": self.exported_at,
            "exported_by": self.exported_by,
            "source_machine": self.source_machine.to_dict(),
            "project": self.project.to_dict(),
            "resources": [entry.to_dict() for entry in self.resources],
            "content_policy": self.content_policy,
            "total_size_bytes": self.total_size_bytes,
        }
        if self.checksum:
            result["checksum"] = self.checksum
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PackageManifest":
        """从 dict 反序列化（用于读取 manifest.json）."""
        resources_data = data.get("resources") or []
        return cls(
            format_version=str(data["format_version"]),
            exported_at=str(data["exported_at"]),
            exported_by=str(data["exported_by"]),
            source_machine=SourceMachineInfo.from_dict(data["source_machine"]),
            project=PackageProjectInfo.from_dict(data["project"]),
            resources=tuple(PackageResourceEntry.from_dict(item) for item in resources_data),
            content_policy=str(data.get("content_policy", ContentPolicy.default())),
            total_size_bytes=int(data.get("total_size_bytes", 0)),
            checksum=str(data.get("checksum", "")),
        )


# 数据结构：导出选项


@dataclass(frozen=True)
class ExportOptions:
    """导出选项契约：``POST /export`` 请求体的核心字段.

    属性:
        content_policy: 内容策略（ContentPolicy 常量，默认 INCLUDE_CONTENT）
        include_datasets: 是否打包数据集资源（默认 True）
        include_models: 是否打包模型产物资源（默认 True）
        include_workflows: 是否打包工作流定义（默认 True）
        include_configs: 是否打包配置规格（默认 True）
        include_snapshots: 是否打包实验快照元数据（默认 True）
        include_lineage: 是否打包血缘记录（默认 True）
        max_file_size_bytes: ``small_files_only`` 策略下的文件大小阈值，
            默认 10MB（``DEFAULT_MAX_FILE_SIZE_BYTES``）
        output_filename: 自定义输出文件名（不含路径，服务层追加扩展名）；
            空字符串表示使用默认模板 ``<project_name>_<timestamp>.lomo``
    """

    content_policy: str = ContentPolicy.INCLUDE_CONTENT
    include_datasets: bool = True
    include_models: bool = True
    include_workflows: bool = True
    include_configs: bool = True
    include_snapshots: bool = True
    include_lineage: bool = True
    max_file_size_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES
    output_filename: str = ""

    def __post_init__(self) -> None:
        if not ContentPolicy.is_valid(self.content_policy):
            raise ValueError(
                f"ExportOptions.content_policy 不合法: {self.content_policy}，合法值: {ContentPolicy.all()}"
            )
        if self.max_file_size_bytes <= 0:
            raise ValueError(f"ExportOptions.max_file_size_bytes 必须为正数: {self.max_file_size_bytes}")

    def should_include(self, resource_type: str) -> bool:
        """根据资源类型判断是否打包.

        Args:
            resource_type: 资源类型（dataset/model/workflow/config/snapshot/lineage）

        Returns:
            True 表示该类型资源应被打包
        """
        mapping = {
            "dataset": self.include_datasets,
            "model": self.include_models,
            "workflow": self.include_workflows,
            "config": self.include_configs,
            "snapshot": self.include_snapshots,
            "lineage": self.include_lineage,
        }
        return mapping.get(resource_type, True)

    def should_pack_content(self, size_bytes: int) -> bool:
        """根据内容策略与文件大小判断是否打包内容.

        Args:
            size_bytes: 资源文件大小（字节）

        Returns:
            True 表示应打包该资源的内容
        """
        if self.content_policy == ContentPolicy.METADATA_ONLY:
            return False
        if self.content_policy == ContentPolicy.INCLUDE_CONTENT:
            return True
        # SMALL_FILES_ONLY
        return size_bytes <= self.max_file_size_bytes

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict."""
        return {
            "content_policy": self.content_policy,
            "include_datasets": self.include_datasets,
            "include_models": self.include_models,
            "include_workflows": self.include_workflows,
            "include_configs": self.include_configs,
            "include_snapshots": self.include_snapshots,
            "include_lineage": self.include_lineage,
            "max_file_size_bytes": self.max_file_size_bytes,
            "output_filename": self.output_filename,
        }


# 数据结构：导入选项


@dataclass(frozen=True)
class ImportOptions:
    """导入选项契约：``POST /import`` 请求体的核心字段.

    属性:
        conflict_strategy: 冲突策略（ConflictStrategy 常量，默认 SKIP）
        target_owner_id: 导入资源的目标所有者 user_id（默认继承源 manifest.exported_by）
        reinit_git: 导入后是否重新 ``git init``（默认 True）；False 时仅在文件系统
            恢复资源，不创建 Git 仓库
        dry_run: 仅校验不实际写入（默认 False）；True 时服务层返回预导入结果，
            不修改任何文件或数据库
        target_project_name: 目标项目名（空字符串表示使用源 manifest.project.name）；
            用于"导入为副本"场景
    """

    conflict_strategy: str = ConflictStrategy.SKIP
    target_owner_id: str = ""
    reinit_git: bool = True
    dry_run: bool = False
    target_project_name: str = ""

    def __post_init__(self) -> None:
        if not ConflictStrategy.is_valid(self.conflict_strategy):
            raise ValueError(
                f"ImportOptions.conflict_strategy 不合法: {self.conflict_strategy}，合法值: {ConflictStrategy.all()}"
            )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict."""
        return {
            "conflict_strategy": self.conflict_strategy,
            "target_owner_id": self.target_owner_id,
            "reinit_git": self.reinit_git,
            "dry_run": self.dry_run,
            "target_project_name": self.target_project_name,
        }


# 数据结构：导出结果


@dataclass(frozen=True)
class ExportResult:
    """导出结果契约：``ProjectPackageService.export_project()`` 返回值.

    属性:
        export_id: 导出任务 ID（``pexp_`` 前缀 + uuid）
        project_id: 源项目 ID
        package_path: 生成的 ``.lomo`` 文件绝对路径
        manifest: 包清单（含 checksum）
        resource_count: 资源条目总数
        packed_count: 实际打包内容的资源数（含内容的条目数）
        skipped_resources: 因策略跳过的资源 URI 列表（如 small_files_only 策略下
            的大文件）
        total_size_bytes: 包内所有资源文件总大小（未压缩前）
        package_size_bytes: ``.lomo`` 文件实际大小（压缩后）
        status: 任务状态（PackageTaskStatus 常量）
        error_message: 失败原因（status=FAILED 时非空）
        created_at: 任务创建时间
        completed_at: 任务完成时间（status=COMPLETED/FAILED 时非空）
    """

    export_id: str
    project_id: str
    package_path: str
    manifest: PackageManifest
    resource_count: int
    packed_count: int
    skipped_resources: tuple[str, ...] = field(default_factory=tuple)
    total_size_bytes: int = 0
    package_size_bytes: int = 0
    status: str = PackageTaskStatus.COMPLETED
    error_message: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.export_id:
            raise ValueError("ExportResult.export_id 不能为空")
        if not self.project_id:
            raise ValueError("ExportResult.project_id 不能为空")
        if not self.package_path:
            raise ValueError("ExportResult.package_path 不能为空")
        if not PackageTaskStatus.is_valid(self.status):
            raise ValueError(f"ExportResult.status 不合法: {self.status}，合法值: {PackageTaskStatus.all()}")
        if self.resource_count < 0:
            raise ValueError(f"ExportResult.resource_count 不能为负数: {self.resource_count}")
        if self.packed_count < 0 or self.packed_count > self.resource_count:
            raise ValueError(
                f"ExportResult.packed_count 不合法: {self.packed_count}，应在 [0, {self.resource_count}] 范围内"
            )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应）."""
        return {
            "export_id": self.export_id,
            "project_id": self.project_id,
            "package_path": self.package_path,
            "manifest": self.manifest.to_dict(),
            "resource_count": self.resource_count,
            "packed_count": self.packed_count,
            "skipped_resources": list(self.skipped_resources),
            "total_size_bytes": self.total_size_bytes,
            "package_size_bytes": self.package_size_bytes,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "completed_at": self.completed_at.isoformat() if self.completed_at else "",
        }


# 数据结构：导入结果


@dataclass(frozen=True)
class ImportResult:
    """导入结果契约：``ProjectPackageService.import_project()`` 返回值.

    属性:
        import_id: 导入任务 ID（``pimp_`` 前缀 + uuid）
        source_project_id: 源项目 ID（来自 manifest）
        target_project_id: 目标项目 ID（导入后创建或匹配的项目）
        source_package_path: 源 ``.lomo`` 文件路径
        format_version: 包格式版本
        conflict_strategy: 使用的冲突策略
        resource_records: 每个资源的导入记录（按 resource_uri 唯一）
        imported_count: 成功导入资源数
        skipped_count: 跳过资源数
        renamed_count: 重命名资源数
        failed_count: 失败资源数
        warnings: 警告信息列表（非致命问题）
        status: 任务状态（PackageTaskStatus 常量）
        error_message: 失败原因（status=FAILED 时非空）
        created_at: 任务创建时间
        completed_at: 任务完成时间
    """

    import_id: str
    source_project_id: str
    target_project_id: str
    source_package_path: str
    format_version: str
    conflict_strategy: str
    resource_records: tuple[ImportResourceRecord, ...] = field(default_factory=tuple)
    imported_count: int = 0
    skipped_count: int = 0
    renamed_count: int = 0
    failed_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    status: str = PackageTaskStatus.COMPLETED
    error_message: str = ""
    created_at: datetime | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.import_id:
            raise ValueError("ImportResult.import_id 不能为空")
        if not self.source_project_id:
            raise ValueError("ImportResult.source_project_id 不能为空")
        if not self.target_project_id:
            raise ValueError("ImportResult.target_project_id 不能为空")
        if not self.source_package_path:
            raise ValueError("ImportResult.source_package_path 不能为空")
        if not PackageFormatVersion.is_supported(self.format_version):
            raise ValueError(f"ImportResult.format_version 不受支持: {self.format_version}")
        if not ConflictStrategy.is_valid(self.conflict_strategy):
            raise ValueError(f"ImportResult.conflict_strategy 不合法: {self.conflict_strategy}")
        if not PackageTaskStatus.is_valid(self.status):
            raise ValueError(f"ImportResult.status 不合法: {self.status}")
        # 计数一致性校验
        total = self.imported_count + self.skipped_count + self.renamed_count + self.failed_count
        if total != len(self.resource_records):
            raise ValueError(
                f"ImportResult 计数不一致：imported({self.imported_count}) + "
                f"skipped({self.skipped_count}) + renamed({self.renamed_count}) + "
                f"failed({self.failed_count}) = {total}，"
                f"但 resource_records 长度为 {len(self.resource_records)}"
            )

    @property
    def total_count(self) -> int:
        """资源总数."""
        return len(self.resource_records)

    @property
    def is_partial_failure(self) -> bool:
        """是否部分失败（有失败但整体未 FAILED）."""
        return self.failed_count > 0 and self.status == PackageTaskStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 API 响应）."""
        return {
            "import_id": self.import_id,
            "source_project_id": self.source_project_id,
            "target_project_id": self.target_project_id,
            "source_package_path": self.source_package_path,
            "format_version": self.format_version,
            "conflict_strategy": self.conflict_strategy,
            "resource_records": [record.to_dict() for record in self.resource_records],
            "imported_count": self.imported_count,
            "skipped_count": self.skipped_count,
            "renamed_count": self.renamed_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "warnings": list(self.warnings),
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "completed_at": self.completed_at.isoformat() if self.completed_at else "",
        }


# 数据结构：校验结果


# 接口契约：服务接口


class IProjectPackageService:
    """项目包服务接口契约：定义导入导出服务的方法签名.

    实现见 app/services/project_package_service.py: ProjectPackageService。
    所有方法均应线程安全（按 project_id / package_path 串行化）。
    """

    def export_project(
        self,
        project_id: str,
        output_dir: str,
        options: ExportOptions,
        exported_by: str,
    ) -> ExportResult:
        """导出项目为 ``.lomo`` 包.

        Args:
            project_id: 源项目 ID
            output_dir: 输出目录（.lomo 文件将写入此目录）
            options: 导出选项
            exported_by: 导出者 user_id 或 plugin_id

        Returns:
            导出结果

        Raises:
            LookupError: 项目不存在
            ValueError: 选项不合法
            RuntimeError: 导出失败（IO 错误 / 资源缺失）
        """
        raise NotImplementedError

    def import_project(
        self,
        package_path: str,
        options: ImportOptions,
        imported_by: str,
    ) -> ImportResult:
        """从 ``.lomo`` 包导入项目.

        Args:
            package_path: ``.lomo`` 文件路径
            options: 导入选项
            imported_by: 导入者 user_id 或 plugin_id

        Returns:
            导入结果

        Raises:
            LookupError: 包文件不存在
            ValueError: 包格式不合法 / 版本不兼容
            RuntimeError: 导入失败（IO 错误 / 冲突策略 fail 触发）
        """
        raise NotImplementedError

    def validate_package(
        self,
        package_path: str,
    ) -> ValidationResult:
        """校验 ``.lomo`` 包完整性（不实际导入）.

        Args:
            package_path: ``.lomo`` 文件路径

        Returns:
            校验结果

        Raises:
            LookupError: 包文件不存在
            ValueError: 包格式不合法（无法解析 manifest）
        """
        raise NotImplementedError

    def preview_import(
        self,
        package_path: str,
    ) -> PackageManifest:
        """预览 ``.lomo`` 包内容（返回 manifest，不实际导入）.

        Args:
            package_path: ``.lomo`` 文件路径

        Returns:
            包清单

        Raises:
            LookupError: 包文件不存在
            ValueError: 包格式不合法 / 版本不兼容
        """
        raise NotImplementedError
