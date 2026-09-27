"""add_workflow_tables

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-13 10:00:00.000000

ADR-005 阶段 1：新增工作流编排引擎持久化表。

设计要点：
    - workflow_runs: 工作流运行实例（spec/status/inputs/outputs）
    - workflow_run_nodes: 各节点运行状态（支持断点续跑）
    - 使用 JSONB.with_variant(JSON, "sqlite") 跨库兼容
    - status 字段使用 String 类型支持契约层 7 状态（含 skipped）
    - 复用 training_task.py 的 Base，init_db 自动 create_all
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workflow_runs and workflow_run_nodes tables."""
    # workflow_runs 表
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(256), nullable=False, comment="工作流名称"),
        sa.Column("version", sa.String(64), nullable=False, comment="工作流版本"),
        sa.Column(
            "spec",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            comment="序列化的 WorkflowSpec",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="工作流运行状态",
        ),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="工作流级输入 Artifact 字典",
        ),
        sa.Column(
            "outputs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="工作流级输出",
        ),
        sa.Column("owner_id", sa.String(128), nullable=True, comment="发起人 ID"),
        sa.Column("error", sa.String(2048), nullable=True, comment="失败原因"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="工作流元数据",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        comment="ADR-005 阶段 1：工作流运行实例表",
    )
    op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])
    op.create_index("ix_workflow_runs_owner_id", "workflow_runs", ["owner_id"])
    op.create_index(
        "ix_workflow_runs_status_created", "workflow_runs", ["status", "created_at"]
    )
    op.create_index(
        "ix_workflow_runs_owner_created", "workflow_runs", ["owner_id", "created_at"]
    )

    # workflow_run_nodes 表
    op.create_table(
        "workflow_run_nodes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "workflow_run_id",
            sa.String(64),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
            comment="所属工作流运行 ID",
        ),
        sa.Column("node_id", sa.String(128), nullable=False, comment="节点 ID"),
        sa.Column(
            "task_type", sa.String(64), nullable=False, comment="任务类型"
        ),
        sa.Column("job_id", sa.String(128), nullable=True, comment="ITaskExecutor job_id"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="节点状态",
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="节点参数",
        ),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="节点输入",
        ),
        sa.Column(
            "outputs",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="节点输出",
        ),
        sa.Column(
            "metrics",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="节点指标",
        ),
        sa.Column("error", sa.String(2048), nullable=True, comment="节点失败原因"),
        sa.Column(
            "retry_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="已重试次数",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        comment="ADR-005 阶段 1：工作流节点运行状态表",
    )
    op.create_index(
        "ix_workflow_run_nodes_workflow_run_id",
        "workflow_run_nodes",
        ["workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_run_nodes_node_id", "workflow_run_nodes", ["node_id"]
    )
    op.create_index(
        "ix_workflow_run_nodes_task_type", "workflow_run_nodes", ["task_type"]
    )
    op.create_index(
        "ix_workflow_run_nodes_job_id", "workflow_run_nodes", ["job_id"]
    )
    op.create_index(
        "ix_workflow_run_nodes_run_status",
        "workflow_run_nodes",
        ["workflow_run_id", "status"],
    )
    op.create_unique_constraint(
        "uq_workflow_run_node", "workflow_run_nodes", ["workflow_run_id", "node_id"]
    )


def downgrade() -> None:
    """Drop workflow tables."""
    op.drop_constraint("uq_workflow_run_node", "workflow_run_nodes", type_="unique")
    op.drop_index("ix_workflow_run_nodes_run_status", table_name="workflow_run_nodes")
    op.drop_index("ix_workflow_run_nodes_job_id", table_name="workflow_run_nodes")
    op.drop_index("ix_workflow_run_nodes_task_type", table_name="workflow_run_nodes")
    op.drop_index("ix_workflow_run_nodes_node_id", table_name="workflow_run_nodes")
    op.drop_index(
        "ix_workflow_run_nodes_workflow_run_id", table_name="workflow_run_nodes"
    )
    op.drop_table("workflow_run_nodes")

    op.drop_index("ix_workflow_runs_owner_created", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_status_created", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_owner_id", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_status", table_name="workflow_runs")
    op.drop_table("workflow_runs")
