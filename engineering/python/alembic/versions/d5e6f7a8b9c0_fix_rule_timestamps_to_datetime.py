"""fix_rule_timestamps_to_datetime

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-10 12:00:00.000000

P0-15 修复：将 rules / rule_groups 表的 created_at / updated_at 字段
从 String 改为 DateTime，与其他 ORM 模型保持一致。

设计要点：
    - 使用 batch_alter_table 兼容 SQLite（SQLite 不支持原生 ALTER COLUMN）。
    - PostgreSQL 端通过 ``postgresql_using`` 将已有字符串数据转换为时间戳，
      兼容 ``YYYY-MM-DD HH:MM:SS`` 格式（rule_db._now() 的输出）。
    - SQLite 端列类型仅为声明性提示，已有字符串数据无需转换即可被
      datetime 函数解析，batch 重建表后类型元信息自动更新。
    - 降级回滚为 String 类型（仅修改列类型元信息，不丢数据）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: String -> DateTime for rule timestamp columns."""
    # rules 表
    with op.batch_alter_table("rules", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using="created_at::timestamp",
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using="updated_at::timestamp",
        )

    # rule_groups 表
    with op.batch_alter_table("rule_groups", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using="created_at::timestamp",
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.String(),
            type_=sa.DateTime(),
            existing_nullable=False,
            postgresql_using="updated_at::timestamp",
        )


def downgrade() -> None:
    """Downgrade schema: DateTime -> String for rule timestamp columns."""
    with op.batch_alter_table("rules", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            type_=sa.String(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(),
            type_=sa.String(),
            existing_nullable=False,
        )

    with op.batch_alter_table("rule_groups", schema=None) as batch_op:
        batch_op.alter_column(
            "created_at",
            existing_type=sa.DateTime(),
            type_=sa.String(),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "updated_at",
            existing_type=sa.DateTime(),
            type_=sa.String(),
            existing_nullable=False,
        )
