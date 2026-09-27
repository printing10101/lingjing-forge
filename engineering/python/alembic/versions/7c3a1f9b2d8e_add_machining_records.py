"""add_machining_records

Revision ID: 7c3a1f9b2d8e
Revises: be7e8d82d196
Create Date: 2026-06-11 22:00:00.000000

新增统一加工记录表 ``machining_records``，用于车间数据标准化与模型训练闭环。

设计要点：
    - 核心字段 5-7 个：machine_id / tool_id / material / timestamp /
      spindle_speed / feed_rate / tdengine_series_id；
    - 时序数据通过 ``tdengine_series_id`` 字符串引用，原始高频数据
      保留在 TDengine，避免在 PostgreSQL 重复存储；
    - ``process_params`` 使用 PostgreSQL 原生 ``JSONB`` 类型（禁止 TEXT）；
    - 在 ``machine_id`` / ``tool_id`` / ``material`` / ``timestamp`` 上
      建立索引以适配典型查询模式；
    - 业务唯一性约束：``(machine_id, tool_id, timestamp)`` 视为同一物理
      加工事件，避免重复入库。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "7c3a1f9b2d8e"
down_revision: Union[str, Sequence[str], None] = "be7e8d82d196"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: create machining_records table & related indexes."""

    op.create_table(
        "machining_records",
        sa.Column(
            "record_id",
            sa.String(length=64),
            primary_key=True,
            comment="记录主键 ID（mrec_ 前缀 + UUID4 hex）",
        ),
        sa.Column(
            "machine_id",
            sa.String(length=64),
            nullable=False,
            comment="机床标识，关联 machines.json 中的 machine.id",
        ),
        sa.Column(
            "tool_id",
            sa.String(length=64),
            nullable=False,
            comment="刀具标识，关联 tools.json 中的 tool.id",
        ),
        sa.Column(
            "material",
            sa.String(length=128),
            nullable=False,
            comment="工件材料名称",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="加工事件发生时间（带时区）",
        ),
        sa.Column(
            "spindle_speed",
            sa.Float(),
            nullable=False,
            comment="主轴转速，单位 RPM（>=0 物理约束由 Pydantic 层保证）",
        ),
        sa.Column(
            "feed_rate",
            sa.Float(),
            nullable=False,
            comment="进给速度，单位 mm/min（>=0 物理约束由 Pydantic 层保证）",
        ),
        sa.Column(
            "tdengine_series_id",
            sa.String(length=128),
            nullable=True,
            comment="TDengine 时序数据引用 ID，spindle_actual / feed_actual / "
            "vibration 等高频数据存储在 TDengine",
        ),
        sa.Column(
            "process_params",
            # 生产环境（PostgreSQL）使用原生 JSONB 以支持高效索引；
            # SQLite 单元测试环境回退为 JSON（行为兼容），均满足
            # "禁止使用 TEXT" 的约束。
            # 默认值 ``{}`` 由 ORM 层 ``default=dict`` 注入（不写
            # PostgreSQL 专属 ``server_default``，避免 SQLite 无法解析
            # ``::jsonb`` 语法）。
            postgresql.JSONB(astext_type=sa.Text()).with_variant(
                sa.JSON(), "sqlite"
            ),
            nullable=False,
            comment="附加工艺参数（depth_of_cut / coolant / operation 等），"
            "使用 PostgreSQL JSONB 类型以支持高效查询与索引。"
            "默认值 ``{}`` 由 ORM 层 ``default=dict`` 注入（不写 PostgreSQL "
            "专属 ``server_default``，避免 SQLite 单元测试环境无法解析 ``::jsonb`` 语法）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录入库时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="记录最后更新时间（数据库自动维护）",
        ),
        sa.UniqueConstraint(
            "machine_id",
            "tool_id",
            "timestamp",
            name="uq_machining_records_machine_tool_ts",
        ),
    )

    # 单列索引
    op.create_index(
        "ix_machining_records_machine_id",
        "machining_records",
        ["machine_id"],
    )
    op.create_index(
        "ix_machining_records_tool_id",
        "machining_records",
        ["tool_id"],
    )
    op.create_index(
        "ix_machining_records_material",
        "machining_records",
        ["material"],
    )
    op.create_index(
        "ix_machining_records_timestamp",
        "machining_records",
        ["timestamp"],
    )

    # 复合索引：典型"按机床查询时间窗口"模式
    op.create_index(
        "ix_machining_records_machine_ts",
        "machining_records",
        ["machine_id", "timestamp"],
    )


def downgrade() -> None:
    """Downgrade schema: drop machining_records table & related indexes."""

    op.drop_index("ix_machining_records_machine_ts", table_name="machining_records")
    op.drop_index("ix_machining_records_timestamp", table_name="machining_records")
    op.drop_index("ix_machining_records_material", table_name="machining_records")
    op.drop_index("ix_machining_records_tool_id", table_name="machining_records")
    op.drop_index("ix_machining_records_machine_id", table_name="machining_records")
    op.drop_table("machining_records")
