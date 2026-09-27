"""SQLAlchemy ORM models for project-level Git sync.

对应 ADR-011 阶段 6 p6-2：项目级 Git 同步持久化层。

设计要点：
    1. 复用 training_task.py 的 Base，统一 init_db 流程
    2. project_repos 主表：项目仓库元数据 + 当前状态（branch/commit/status）
    3. project_resource_refs 资源引用表：项目包含的资源（URI + content_hash）
    4. project_sync_records 同步记录表：每次 Git 写操作的审计记录
    5. metadata/details 使用 JSONB（PostgreSQL）或 JSON（SQLite）双兼容模式
    6. 资源引用与同步记录通过 project_id 外键关联主表，ondelete CASCADE
    7. (project_id, resource_uri) 复合唯一约束，确保同一项目内资源 URI 不重复
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import JSON
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _new_project_id() -> str:
    """生成 ProjectRepo 主键 ID."""
    return f"prj_{uuid.uuid4().hex[:20]}"


def _new_ref_id() -> str:
    """生成 ProjectResourceRef 主键 ID."""
    return f"pref_{uuid.uuid4().hex[:20]}"


def _new_record_id() -> str:
    """生成 ProjectSyncRecord 主键 ID."""
    return f"psr_{uuid.uuid4().hex[:20]}"


class ProjectRepo(Base):
    """项目仓库主表 ORM 模型.

    一个 project_id 对应一个 Git 仓库，仓库根目录包含 ``.lomo-project.yaml``
    清单文件。current_branch / current_commit / status 由服务层在 Git 操作后更新。
    """

    __tablename__ = "project_repos"

    project_id = Column(
        String(64),
        primary_key=True,
        default=_new_project_id,
        comment="项目 ID（prj_ 前缀，全局唯一）",
    )
    name = Column(
        String(256),
        nullable=False,
        comment="项目显示名",
    )
    repo_path = Column(
        String(512),
        nullable=False,
        comment="仓库本地路径（绝对路径或相对 output_dir 的路径）",
    )
    remote_url = Column(
        String(1024),
        nullable=True,
        comment="远端仓库 URL（空表示纯本地仓库）",
    )
    current_branch = Column(
        String(128),
        nullable=False,
        default="main",
        comment="当前分支名",
    )
    current_commit = Column(
        String(64),
        nullable=True,
        comment="当前 HEAD commit sha（空表示未提交）",
    )
    status = Column(
        String(32),
        nullable=False,
        default="clean",
        index=True,
        comment="同步状态：clean/dirty/ahead/behind/conflict/error",
    )
    description = Column(
        String(2048),
        nullable=True,
        comment="项目描述",
    )
    author = Column(
        String(128),
        nullable=True,
        index=True,
        comment="项目作者",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    # 关系：资源引用列表 + 同步记录列表
    resource_refs = relationship(
        "ProjectResourceRef",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    sync_records = relationship(
        "ProjectSyncRecord",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="ProjectSyncRecord.timestamp.desc()",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_project_repos_author_created", "author", "created_at"),
        Index("ix_project_repos_status_updated", "status", "updated_at"),
    )

    def to_dict(self, include_refs: bool = False, include_records: bool = False) -> dict:
        """序列化为 dict.

        Args:
            include_refs: 是否包含资源引用列表（默认不包含，避免列表查询时 N+1）
            include_records: 是否包含同步记录列表（默认不包含）
        """
        data = {
            "project_id": self.project_id,
            "name": self.name,
            "repo_path": self.repo_path,
            "remote_url": self.remote_url or "",
            "current_branch": self.current_branch,
            "current_commit": self.current_commit or "",
            "status": self.status,
            "description": self.description or "",
            "author": self.author or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_refs:
            data["resource_refs"] = [ref.to_dict() for ref in (self.resource_refs or [])]
            data["resource_count"] = len(self.resource_refs or [])
        if include_records:
            data["sync_records"] = [rec.to_dict() for rec in (self.sync_records or [])]
        return data


class ProjectResourceRef(Base):
    """项目资源引用表 ORM 模型.

    记录项目包含的所有资源（dataset/model/workflow/config/snapshot/template），
    通过 URI + content_hash 实现内容寻址同步。资源实际内容不入 Git（除非
    sync_strategy=git_tracked），仅记录 hash 用于变更检测。
    """

    __tablename__ = "project_resource_refs"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_ref_id,
    )
    project_id = Column(
        String(64),
        ForeignKey("project_repos.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID（关联 project_repos.project_id）",
    )
    resource_type = Column(
        String(32),
        nullable=False,
        index=True,
        comment="资源类型：dataset/model/workflow/config/snapshot/template",
    )
    resource_uri = Column(
        String(512),
        nullable=False,
        comment="资源 URI（如 dataset://phm2010/v3）",
    )
    content_hash = Column(
        String(64),
        nullable=True,
        index=True,
        comment="内容哈希（sha256 hex，64 字符；空表示未计算）",
    )
    sync_strategy = Column(
        String(32),
        nullable=False,
        default="hash_referenced",
        comment="同步策略：git_tracked/hash_referenced/git_lfs",
    )
    # SQLAlchemy Declarative API 保留 `metadata` 属性名（指向 MetaData），
    # 不能用作 ORM 列名。Python 属性名改为 `metadata_json`，数据库列名仍为
    # `metadata`（保持与既有迁移脚本和 API 契约兼容）。
    metadata_json = Column(
        "metadata",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="附加元数据（文件大小、来源插件 id、自定义标签等）",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

    project = relationship("ProjectRepo", back_populates="resource_refs")

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "resource_uri",
            name="uq_project_resource_uri",
        ),
        Index(
            "ix_project_resource_refs_type_project",
            "resource_type",
            "project_id",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "resource_type": self.resource_type,
            "resource_uri": self.resource_uri,
            "content_hash": self.content_hash or "",
            "sync_strategy": self.sync_strategy,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProjectSyncRecord(Base):
    """项目同步记录表 ORM 模型.

    每次 Git 写操作（init/commit/push/pull/clone）生成一条记录，
    用于审计与回溯。timestamp 由服务层填入 ISO8601 字符串。
    """

    __tablename__ = "project_sync_records"

    record_id = Column(
        String(64),
        primary_key=True,
        default=_new_record_id,
    )
    project_id = Column(
        String(64),
        ForeignKey("project_repos.project_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属项目 ID（关联 project_repos.project_id）",
    )
    direction = Column(
        String(16),
        nullable=False,
        index=True,
        comment="同步方向：init/commit/push/pull/clone",
    )
    commit_sha = Column(
        String(64),
        nullable=True,
        comment="涉及的 commit sha（push/pull/commit 时填写）",
    )
    status = Column(
        String(16),
        nullable=False,
        default="success",
        comment="操作结果状态：success/failed/conflict",
    )
    message = Column(
        String(2048),
        nullable=True,
        comment="操作消息（commit message 或错误描述）",
    )
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        index=True,
        comment="操作时间戳",
    )
    details = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="附加详情（变更文件数、字节数、远端 URL 等）",
    )

    project = relationship("ProjectRepo", back_populates="sync_records")

    __table_args__ = (
        Index(
            "ix_project_sync_records_project_timestamp",
            "project_id",
            "timestamp",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "project_id": self.project_id,
            "direction": self.direction,
            "commit_sha": self.commit_sha or "",
            "status": self.status,
            "message": self.message or "",
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "details": self.details or {},
        }


__all__ = [
    "ProjectRepo",
    "ProjectResourceRef",
    "ProjectSyncRecord",
    "_new_project_id",
    "_new_ref_id",
    "_new_record_id",
]
