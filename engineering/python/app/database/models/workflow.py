"""SQLAlchemy ORM models for workflow runs and nodes.

对应 ADR-005 阶段 1：工作流编排引擎持久化层。

设计要点：
    1. 复用 training_task.py 的 Base，统一 init_db 流程
    2. workflow_runs 记录工作流运行实例，workflow_run_nodes 记录各节点状态
    3. status 字段使用 String 类型（非 Enum），支持契约层 7 状态（含 skipped）
    4. spec/inputs/outputs/params/result/metrics 使用 JSONB（PostgreSQL）或 JSON（SQLite）
    5. 断点续跑：通过 node status 字段判断已完成节点，避免重跑
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
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


def _new_run_id() -> str:
    """生成 WorkflowRun 主键 ID."""
    return f"wfr_{uuid.uuid4().hex[:20]}"


def _new_node_id() -> str:
    """生成 WorkflowRunNode 主键 ID."""
    return f"wfnode_{uuid.uuid4().hex[:20]}"


class WorkflowRun(Base):
    """工作流运行实例 ORM 模型."""

    __tablename__ = "workflow_runs"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_run_id,
        comment="工作流运行 ID（wfr_ 前缀）",
    )
    name = Column(
        String(256),
        nullable=False,
        comment="工作流名称（来自 WorkflowSpec.name）",
    )
    version = Column(
        String(64),
        nullable=False,
        default="1.0.0",
        comment="工作流版本（来自 WorkflowSpec.version）",
    )
    spec = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        comment="序列化的 WorkflowSpec（nodes/edges/inputs/outputs/metadata）",
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
        comment="工作流运行状态：pending/running/completed/failed/cancelled",
    )
    inputs = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="工作流级输入 Artifact 字典",
    )
    outputs = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="工作流级输出（节点完成后解析）",
    )
    owner_id = Column(
        String(128),
        nullable=True,
        index=True,
        comment="工作流发起人 ID",
    )
    error = Column(
        String(2048),
        nullable=True,
        comment="工作流失败原因（顶层错误信息）",
    )
    # 注意：Python 属性名不能用 `metadata`，SQLAlchemy Declarative API 保留此名
    # （Base.metadata 是 sqlalchemy.MetaData 实例）。使用 `meta` 作为 Python 属性，
    # 通过 Column 第一参数 "metadata" 显式指定 DB 列名，保持数据库 schema 兼容。
    meta = Column(
        "metadata",
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="工作流元数据（来自 WorkflowSpec.metadata）",
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
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    nodes = relationship(
        "WorkflowRunNode",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_workflow_runs_status_created", "status", "created_at"),
        Index("ix_workflow_runs_owner_created", "owner_id", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "spec": self.spec,
            "status": self.status,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "owner_id": self.owner_id,
            "error": self.error,
            "metadata": self.meta or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class WorkflowRunNode(Base):
    """工作流节点运行状态 ORM 模型."""

    __tablename__ = "workflow_run_nodes"

    id = Column(
        String(64),
        primary_key=True,
        default=_new_node_id,
    )
    workflow_run_id = Column(
        String(64),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属工作流运行 ID",
    )
    node_id = Column(
        String(128),
        nullable=False,
        comment="节点 ID（来自 WorkflowSpec.nodes[].node_id）",
    )
    task_type = Column(
        String(64),
        nullable=False,
        index=True,
        comment="任务类型（来自 WorkflowSpec.nodes[].task_type）",
    )
    job_id = Column(
        String(128),
        nullable=True,
        index=True,
        comment="ITaskExecutor 返回的 job_id",
    )
    status = Column(
        String(32),
        nullable=False,
        default="pending",
        comment="节点状态：pending/running/completed/failed/cancelled/skipped",
    )
    params = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="节点参数（已解析 artifact 引用后的最终参数）",
    )
    inputs = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="节点输入（已解析的 Artifact 字典）",
    )
    outputs = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="节点输出（TaskResult.outputs 序列化）",
    )
    metrics = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="节点指标（TaskResult.metrics）",
    )
    error = Column(
        String(2048),
        nullable=True,
        comment="节点失败原因",
    )
    retry_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="已重试次数",
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
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    run = relationship("WorkflowRun", back_populates="nodes")

    __table_args__ = (
        UniqueConstraint("workflow_run_id", "node_id", name="uq_workflow_run_node"),
        Index("ix_workflow_run_nodes_run_status", "workflow_run_id", "status"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workflow_run_id": self.workflow_run_id,
            "node_id": self.node_id,
            "task_type": self.task_type,
            "job_id": self.job_id,
            "status": self.status,
            "params": self.params,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "metrics": self.metrics,
            "error": self.error,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


__all__ = [
    "WorkflowRun",
    "WorkflowRunNode",
    "_new_run_id",
    "_new_node_id",
]
