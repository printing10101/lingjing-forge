"""add_kg_tables

Revision ID: a1b2c3d4e5f6
Revises: 7c3a1f9b2d8e
Create Date: 2026-06-11 23:00:00.000000

新增知识图谱持久化表（kg_nodes / kg_edges），用于 M1.2 任务的 NetworkX
内存图持久化与事务支持。

设计要点：
    - 节点表 ``kg_nodes`` 存储实体（Material / Tool / Feature / Process 等），
      主键为字符串 ``node_id``（遵循 ``<type>-<slug>`` 格式），
      额外 ``node_type`` 列用于按类型批量查询。
    - 关系表 ``kg_edges`` 存储 (source) -[edge_type]-> (target) 三元组，
      ``confidence`` 列单独建索引便于按可信度区间筛选。
    - 属性数据（properties）使用 PostgreSQL 原生 ``JSONB``（SQLite
      单元测试环境下回退为 JSON）。
    - 为 ``node_id`` / ``node_type`` / ``edge_type`` 创建索引以优化查询。
    - 关系级联删除：删除节点时级联删除其关联关系。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "7c3a1f9b2d8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create kg_nodes & kg_edges tables with indexes."""

    # ------------------------------------------------------------------ nodes
    op.create_table(
        "kg_nodes",
        sa.Column(
            "node_id",
            sa.String(length=128),
            primary_key=True,
            comment="节点唯一 ID，格式 '<type>-<slug>'，例如 'material-45steel'",
        ),
        sa.Column(
            "node_type",
            sa.String(length=64),
            nullable=False,
            comment="节点类型（material / tool / feature / process 等）",
        ),
        sa.Column(
            "properties",
            # 生产环境 PostgreSQL 使用 JSONB 以支持高效索引与查询；
            # SQLite 单元测试环境回退为 JSON，行为兼容。
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            nullable=False,
            comment="节点属性（name / category / 业务字段等），JSONB 类型。"
            "默认值 ``{}`` 由 ORM 层 ``default=dict`` 注入。",
        ),
        sa.Column(
            "created_at",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录入库时间（ISO8601 字符串，跨库兼容）",
        ),
        sa.Column(
            "updated_at",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录最后更新时间（ISO8601 字符串，跨库兼容）",
        ),
    )

    # 节点类型索引
    op.create_index("ix_kg_nodes_node_type", "kg_nodes", ["node_type"])

    # ------------------------------------------------------------------ edges
    op.create_table(
        "kg_edges",
        sa.Column(
            "edge_id",
            sa.String(length=64),
            primary_key=True,
            comment="关系主键 ID（kgedge_ 前缀 + uuid4 hex）",
        ),
        sa.Column(
            "source_id",
            sa.String(length=128),
            sa.ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
            nullable=False,
            comment="关系起始节点 ID（对应 kg_nodes.node_id）",
        ),
        sa.Column(
            "target_id",
            sa.String(length=128),
            sa.ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
            nullable=False,
            comment="关系目标节点 ID（对应 kg_nodes.node_id）",
        ),
        sa.Column(
            "edge_type",
            sa.String(length=64),
            nullable=False,
            comment="关系类型（如 SUITABLE_FOR / APPLIED_TO / USED 等）",
        ),
        sa.Column(
            "confidence",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.5"),
            comment="关系可信度，取值 [0, 1]，由 Pydantic 层保证范围",
        ),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            nullable=False,
            comment="关系附加属性（source / evidence 等），JSONB 类型。"
            "默认值 ``{}`` 由 ORM 层 ``default=dict`` 注入。",
        ),
        sa.Column(
            "created_at",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录入库时间（ISO8601 字符串）",
        ),
        # 业务唯一性约束：同一 (source, target, type) 视为同一逻辑关系
        sa.UniqueConstraint(
            "source_id",
            "target_id",
            "edge_type",
            name="uq_kg_edges_source_target_type",
        ),
    )

    # 关系索引
    op.create_index("ix_kg_edges_edge_type", "kg_edges", ["edge_type"])
    op.create_index("ix_kg_edges_source_id", "kg_edges", ["source_id"])
    op.create_index("ix_kg_edges_target_id", "kg_edges", ["target_id"])
    op.create_index("ix_kg_edges_confidence", "kg_edges", ["confidence"])


def downgrade() -> None:
    """Downgrade schema: drop kg_edges & kg_nodes tables."""

    op.drop_index("ix_kg_edges_confidence", table_name="kg_edges")
    op.drop_index("ix_kg_edges_target_id", table_name="kg_edges")
    op.drop_index("ix_kg_edges_source_id", table_name="kg_edges")
    op.drop_index("ix_kg_edges_edge_type", table_name="kg_edges")
    op.drop_table("kg_edges")

    op.drop_index("ix_kg_nodes_node_type", table_name="kg_nodes")
    op.drop_table("kg_nodes")
