"""SQLAlchemy ORM models for workflow templates marketplace.

对应 ADR-010 阶段 6 p6-1：工作流模板市场持久化层。

设计要点：
    1. 复用 training_task.py 的 Base，统一 init_db 流程
    2. workflow_templates 主表：模板唯一标识 + 最新版本指针 + 市场统计
    3. workflow_template_versions 多版本表：每个 semver 版本一条记录，
       复合唯一约束 (template_id, version)
    4. spec/inputs_schema/parameters/tags 使用 JSONB（PostgreSQL）或 JSON（SQLite）
    5. 市场统计字段（downloads/avg_rating/rating_count）反范式存储在主表，
       由服务层在评分/下载时增量更新，避免每次 list 都聚合全版本数据
    6. 主表 latest_version 指向当前最新版本号，便于 list 接口直接 join
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
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


def _new_template_id() -> str:
    """生成 WorkflowTemplate 主键 ID."""
    return f"wft_{uuid.uuid4().hex[:20]}"


def _new_version_id() -> str:
    """生成 WorkflowTemplateVersion 主键 ID."""
    return f"wftv_{uuid.uuid4().hex[:20]}"


class WorkflowTemplate(Base):
    """工作流模板主表 ORM 模型.

    一个 template_id 对应一行，latest_version 指向当前最新版本号。
    多版本通过 workflow_template_versions 表关联。
    """

    __tablename__ = "workflow_templates"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_template_id,
        comment="模板主键 ID（wft_ 前缀，内部使用）",
    )
    template_id = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        comment="模板业务 ID（来自 manifest.id，全局唯一）",
    )
    name = Column(
        String(256),
        nullable=False,
        comment="模板显示名（来自 manifest.name）",
    )
    author = Column(
        String(128),
        nullable=False,
        index=True,
        comment="模板作者（来自 manifest.author）",
    )
    license_ = Column(
        "license",
        String(64),
        nullable=False,
        comment="开源协议（来自 manifest.license）",
    )
    category = Column(
        String(32),
        nullable=False,
        default="general",
        index=True,
        comment="模板分类（见 TEMPLATE_CATEGORIES）",
    )
    plugin_id = Column(
        String(128),
        nullable=True,
        index=True,
        comment="贡献此模板的插件 id（空表示用户自定义模板）",
    )
    homepage = Column(
        String(512),
        nullable=True,
        comment="模板主页 URL",
    )
    latest_version = Column(
        String(64),
        nullable=False,
        comment="当前最新版本号（semver）",
    )
    description = Column(
        String(2048),
        nullable=False,
        comment="模板描述（来自 manifest.description）",
    )
    tags = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="标签列表（来自 manifest.tags）",
    )
    # 市场统计字段（反范式，由服务层增量更新）
    downloads = Column(
        Integer,
        nullable=False,
        default=0,
        comment="累计下载次数（所有版本）",
    )
    avg_rating = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="平均评分（0.0 - 5.0）",
    )
    rating_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="评分人数",
    )
    published_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="首次发布时间",
    )
    # 审核字段（预留：未来支持模板审核流程）
    status = Column(
        String(32),
        nullable=False,
        default="active",
        comment="模板状态：active/unpublished/banned",
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

    versions = relationship(
        "WorkflowTemplateVersion",
        back_populates="template",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_workflow_templates_category_created", "category", "created_at"),
        Index("ix_workflow_templates_author_created", "author", "created_at"),
        Index("ix_workflow_templates_downloads", "downloads"),
        Index("ix_workflow_templates_avg_rating", "avg_rating"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "name": self.name,
            "author": self.author,
            "license": self.license_,
            "category": self.category,
            "plugin_id": self.plugin_id,
            "homepage": self.homepage,
            "latest_version": self.latest_version,
            "description": self.description,
            "tags": self.tags or [],
            "downloads": self.downloads,
            "avg_rating": self.avg_rating,
            "rating_count": self.rating_count,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class WorkflowTemplateVersion(Base):
    """工作流模板版本表 ORM 模型.

    每个 (template_id, version) 组合一条记录，存储该版本的完整 manifest + spec。
    版本不可变：发布后只读，删除模板时级联删除所有版本。
    """

    __tablename__ = "workflow_template_versions"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_version_id,
    )
    template_id = Column(
        String(128),
        ForeignKey("workflow_templates.template_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属模板业务 ID（关联 workflow_templates.template_id）",
    )
    version = Column(
        String(64),
        nullable=False,
        comment="版本号（semver，来自 manifest.version）",
    )
    # 完整 manifest 快照（除 spec 外的元信息）
    manifest_snapshot = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        comment="该版本的完整 manifest 快照（含 spec/inputs_schema/parameters 等）",
    )
    spec = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        comment="WorkflowSpec dict（nodes/edges/inputs/outputs/metadata）",
    )
    inputs_schema = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="输入参数 JSON Schema",
    )
    parameters = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="默认参数",
    )
    required_contracts = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="依赖的契约及版本约束列表",
    )
    required_capabilities = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="运行此模板必须授权的能力列表",
    )
    changelog = Column(
        String(2048),
        nullable=True,
        comment="版本变更说明",
    )
    # 该版本独立的市场统计（细化到版本粒度，便于"按版本下载"统计）
    version_downloads = Column(
        Integer,
        nullable=False,
        default=0,
        comment="该版本下载次数",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    template = relationship("WorkflowTemplate", back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "template_id",
            "version",
            name="uq_workflow_template_version",
        ),
        Index(
            "ix_workflow_template_versions_tpl_created",
            "template_id",
            "created_at",
        ),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "template_id": self.template_id,
            "version": self.version,
            "manifest_snapshot": self.manifest_snapshot,
            "spec": self.spec,
            "inputs_schema": self.inputs_schema,
            "parameters": self.parameters,
            "required_contracts": self.required_contracts or [],
            "required_capabilities": self.required_capabilities or [],
            "changelog": self.changelog,
            "version_downloads": self.version_downloads,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = [
    "WorkflowTemplate",
    "WorkflowTemplateVersion",
    "_new_template_id",
    "_new_version_id",
]
