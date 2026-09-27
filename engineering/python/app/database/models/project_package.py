"""项目导入导出 ORM 模型：.lomo 包导出记录 + 导入记录持久化.

对应 ADR-015（项目导入导出）。

新增 2 张表（与 training_task.Base 共享 metadata，与 project_sync.py 同源）：
    - ``project_exports``：导出任务记录（package_path / format_version / checksum / status）
    - ``project_imports``：导入任务记录（source_package_path / conflict_strategy / 计数 / status）

设计要点：
    - 不修改现有 project_repos / project_resource_refs / project_sync_records 表结构，
      保持 ADR-011 契约稳定性
    - project_exports.project_id / project_imports.target_project_id 通过外键关联
      project_repos.project_id，ondelete=RESTRICT（避免删除项目时丢失导出/导入审计）
    - source_project_id 为字符串字段（不建外键），因为源项目 ID 来自 manifest，
      在目标机器上不一定存在对应 project_repos 记录
    - status 字段索引，便于轮询接口按状态过滤
    - error_message / completed_at 可空，pending/running 状态时为 NULL
    - format_version / content_policy / conflict_strategy 为字符串（与契约层常量对齐），
      不使用枚举类型以保持 SQLite 兼容性
    - 与 resource_card.py 风格对齐：uuid 前缀生成器 + to_dict() + __repr__
"""

from __future__ import annotations

import uuid
from typing import Any

from app.utils.time import utcnow

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _gen_export_id() -> str:
    """生成导出 ID（pexp_ 前缀 + uuid hex）."""
    return f"pexp_{uuid.uuid4().hex}"


def _gen_import_id() -> str:
    """生成导入 ID（pimp_ 前缀 + uuid hex）."""
    return f"pimp_{uuid.uuid4().hex}"


class ProjectExport(Base):
    """项目导出记录 ORM.

    持久化每次导出任务的元数据与状态，支持前端轮询查询导出进度与下载 URL。

    状态机（与 PackageTaskStatus 对齐）：
        pending → running → completed
                          → failed
    pending: 任务已提交，等待后台执行
    running: 后台正在打包资源
    completed: .lomo 文件已生成，package_path 可下载
    failed: 导出异常，error_message 记录原因
    """

    __tablename__ = "project_exports"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_export_id,
        comment="导出 ID（pexp_ 前缀 + uuid）",
    )
    project_id = Column(
        String(64),
        ForeignKey("project_repos.project_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="关联项目 ID（关联 project_repos.project_id）",
    )
    package_path = Column(
        String(512),
        nullable=False,
        comment=".lomo 文件绝对路径",
    )
    format_version = Column(
        String(32),
        nullable=False,
        comment="包格式版本（如 1.0.0），与 PackageFormatVersion 对齐",
    )
    content_policy = Column(
        String(32),
        nullable=False,
        comment="内容策略：metadata_only/include_content/small_files_only",
    )
    resource_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="资源数量（manifest.resources 长度）",
    )
    total_size_bytes = Column(
        BigInteger,
        nullable=False,
        default=0,
        comment="包总大小（未压缩前字节数，来自 manifest.total_size_bytes）",
    )
    checksum = Column(
        String(128),
        nullable=False,
        default="",
        comment="manifest.json sha256 校验和（sha256:<hex>）",
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        comment="任务状态：pending/running/completed/failed",
    )
    error_message = Column(
        Text,
        nullable=True,
        comment="失败原因（status=failed 时填写）",
    )
    exported_by = Column(
        String(128),
        nullable=False,
        index=True,
        comment="导出者 user_id 或 plugin_id",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        comment="任务创建时间",
    )
    completed_at = Column(
        DateTime,
        nullable=True,
        comment="任务完成时间（completed/failed 时填写）",
    )

    project = relationship(
        "ProjectRepo",
        lazy="selectin",
        foreign_keys=[project_id],
    )

    __table_args__ = (
        Index(
            "ix_project_exports_project_id_status",
            "project_id",
            "status",
        ),
        Index(
            "ix_project_exports_exported_by_created",
            "exported_by",
            "created_at",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "export_id": self.id,
            "project_id": self.project_id,
            "package_path": self.package_path,
            "format_version": self.format_version,
            "content_policy": self.content_policy,
            "resource_count": self.resource_count,
            "total_size_bytes": self.total_size_bytes,
            "checksum": self.checksum or "",
            "status": self.status,
            "error_message": self.error_message or "",
            "exported_by": self.exported_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ProjectExport(id={self.id}, project_id={self.project_id}, "
            f"status={self.status}, resource_count={self.resource_count})>"
        )


class ProjectImport(Base):
    """项目导入记录 ORM.

    持久化每次导入任务的元数据与资源处理计数，支持前端轮询查询导入进度。

    状态机（与 PackageTaskStatus 对齐）：
        pending → running → completed
                          → failed
    pending: 任务已提交，等待后台执行
    running: 后台正在解压并写入资源
    completed: 导入完成（可能含部分失败资源，由 imported/skipped/renamed/failed 计数体现）
    failed: 导入异常（如格式不兼容、校验失败），error_message 记录原因

    注意：
        - source_project_id 为字符串字段（不建外键），因为源项目 ID 来自 manifest，
          在目标机器上不一定存在对应 project_repos 记录
        - target_project_id 外键关联 project_repos.project_id，ondelete=RESTRICT
        - imported_count + skipped_count + renamed_count + failed_count 应等于
          包内资源总数（由服务层保证一致性）
    """

    __tablename__ = "project_imports"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_import_id,
        comment="导入 ID（pimp_ 前缀 + uuid）",
    )
    source_package_path = Column(
        String(512),
        nullable=False,
        comment="源 .lomo 文件绝对路径",
    )
    source_project_id = Column(
        String(64),
        nullable=False,
        comment="源项目 ID（来自 manifest.project.project_id，不建外键）",
    )
    target_project_id = Column(
        String(64),
        ForeignKey("project_repos.project_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="目标项目 ID（关联 project_repos.project_id）",
    )
    format_version = Column(
        String(32),
        nullable=False,
        comment="包格式版本（如 1.0.0）",
    )
    conflict_strategy = Column(
        String(32),
        nullable=False,
        comment="冲突策略：skip/overwrite/rename/fail",
    )
    imported_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="成功导入资源数",
    )
    skipped_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="跳过资源数（冲突策略 skip 或目标已存在）",
    )
    renamed_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="重命名资源数（冲突策略 rename）",
    )
    failed_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="失败资源数（写入异常或校验失败）",
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        comment="任务状态：pending/running/completed/failed",
    )
    error_message = Column(
        Text,
        nullable=True,
        comment="失败原因（status=failed 时填写）",
    )
    imported_by = Column(
        String(128),
        nullable=False,
        index=True,
        comment="导入者 user_id 或 plugin_id",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        comment="任务创建时间",
    )
    completed_at = Column(
        DateTime,
        nullable=True,
        comment="任务完成时间（completed/failed 时填写）",
    )

    target_project = relationship(
        "ProjectRepo",
        lazy="selectin",
        foreign_keys=[target_project_id],
    )

    __table_args__ = (
        Index(
            "ix_project_imports_target_project_id_status",
            "target_project_id",
            "status",
        ),
        Index(
            "ix_project_imports_imported_by_created",
            "imported_by",
            "created_at",
        ),
    )

    @property
    def total_count(self) -> int:
        """资源总数（imported + skipped + renamed + failed）."""
        return int(self.imported_count) + int(self.skipped_count) + int(self.renamed_count) + int(self.failed_count)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "import_id": self.id,
            "source_package_path": self.source_package_path,
            "source_project_id": self.source_project_id,
            "target_project_id": self.target_project_id,
            "format_version": self.format_version,
            "conflict_strategy": self.conflict_strategy,
            "imported_count": self.imported_count,
            "skipped_count": self.skipped_count,
            "renamed_count": self.renamed_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "status": self.status,
            "error_message": self.error_message or "",
            "imported_by": self.imported_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ProjectImport(id={self.id}, target_project_id={self.target_project_id}, "
            f"status={self.status}, imported={self.imported_count})>"
        )


__all__ = [
    "ProjectExport",
    "ProjectImport",
    "_gen_export_id",
    "_gen_import_id",
]
