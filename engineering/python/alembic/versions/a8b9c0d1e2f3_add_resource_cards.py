"""add_resource_cards

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-14 20:30:00.000000

ADR-012 阶段 6 p6-3：新增资源卡片持久化表（模型产物 + 数据集 README）。

设计要点：
    - model_artifacts: 模型产物元数据表（model_uri/version/framework/metrics/readme/tags/status）
    - dataset_readmes: 数据集 README 表（支持数据集级 + 版本级，通过 partial unique index 保证唯一）
    - 不修改现有 datasets/dataset_versions/lineage_records/experiment_snapshots
      表结构，保持 ADR-005 契约稳定性
    - model_uri 全局唯一，与 ADR-011 项目同步 URI 体系对齐（model://<name>/<version>）
    - dataset_readmes 通过 dataset_id 外键关联 datasets，ondelete CASCADE
    - 模型状态机与 DatasetStatus 对齐：draft/published/deprecated/archived
    - metrics_json/metrics_history_json/tags_json/readme_md 使用 Text + JSON 序列化
    - 复用 training_task.py 的 Base，init_db 自动 create_all

资源 URI 体系（与 ADR-005/ADR-011 对齐）：
    model://<name>/<version>     ← model_artifacts.model_uri
    dataset://<id>/<version>     ← dataset_readmes 通过 dataset_id + version 索引

数据集 README 两级作用域：
    - 数据集级：version IS NULL，每个 dataset 最多 1 条
    - 版本级：version IS NOT NULL，每个 (dataset_id, version) 最多 1 条
    使用 partial unique index 实现跨库兼容（sqlite_where + postgresql_where）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create model_artifacts / dataset_readmes tables."""
    # -----------------------------------------------------------------------
    # model_artifacts 表：模型产物元数据
    # -----------------------------------------------------------------------
    op.create_table(
        "model_artifacts",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="模型 ID（mdl_ 前缀 + uuid）",
        ),
        sa.Column(
            "model_uri",
            sa.String(512),
            nullable=False,
            comment="模型 URI（model://<name>/<version>），全局唯一",
        ),
        sa.Column(
            "name",
            sa.String(128),
            nullable=False,
            comment="模型显示名（如 LTC-ChatterPredictor）",
        ),
        sa.Column(
            "model_type",
            sa.String(32),
            nullable=False,
            comment="模型类型：lnn/pytorch/onnx/sklearn/other",
        ),
        sa.Column(
            "version",
            sa.String(32),
            nullable=False,
            comment="semver 版本号，如 1.0.0",
        ),
        sa.Column(
            "framework",
            sa.String(64),
            nullable=False,
            comment="框架版本，如 torch-2.1.0",
        ),
        sa.Column(
            "storage_uri",
            sa.String(512),
            nullable=False,
            comment="模型文件存储位置（file:// / s3:// 路径）",
        ),
        sa.Column(
            "metrics_json",
            sa.Text,
            nullable=False,
            server_default="{}",
            comment="当前指标快照 JSON，如 {accuracy: 0.95, loss: 0.05}",
        ),
        sa.Column(
            "metrics_history_json",
            sa.Text,
            nullable=False,
            server_default="[]",
            comment="指标历史 JSON 数组（追加式记录，每项含 timestamp + metrics）",
        ),
        sa.Column(
            "readme_md",
            sa.Text,
            nullable=False,
            server_default="",
            comment="markdown README",
        ),
        sa.Column(
            "tags_json",
            sa.Text,
            nullable=False,
            server_default="[]",
            comment="标签 JSON 数组",
        ),
        sa.Column(
            "owner_id",
            sa.String(128),
            nullable=False,
            comment="所有者 user_id 或 plugin_id",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="draft",
            comment="draft/published/deprecated/archived",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="更新时间",
        ),
        comment="ADR-012 阶段 6 p6-3：模型产物元数据表",
    )
    # 唯一约束（model_uri 全局唯一）
    op.create_unique_constraint(
        "uq_model_artifacts_model_uri",
        "model_artifacts",
        ["model_uri"],
    )
    # 单列索引
    op.create_index(
        "ix_model_artifacts_model_uri", "model_artifacts", ["model_uri"]
    )
    op.create_index("ix_model_artifacts_name", "model_artifacts", ["name"])
    op.create_index("ix_model_artifacts_owner_id", "model_artifacts", ["owner_id"])
    op.create_index("ix_model_artifacts_status", "model_artifacts", ["status"])
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_model_artifacts_owner_status",
        "model_artifacts",
        ["owner_id", "status"],
    )
    op.create_index(
        "ix_model_artifacts_type_status",
        "model_artifacts",
        ["model_type", "status"],
    )
    op.create_index(
        "ix_model_artifacts_name_version",
        "model_artifacts",
        ["name", "version"],
    )

    # -----------------------------------------------------------------------
    # dataset_readmes 表：数据集 README
    # -----------------------------------------------------------------------
    op.create_table(
        "dataset_readmes",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="README ID（readme_ 前缀 + uuid）",
        ),
        sa.Column(
            "dataset_id",
            sa.String(64),
            sa.ForeignKey("datasets.id", ondelete="CASCADE"),
            nullable=False,
            comment="关联 datasets.id",
        ),
        sa.Column(
            "version",
            sa.String(32),
            nullable=True,
            comment="版本号（NULL 表示数据集级 README）",
        ),
        sa.Column(
            "readme_md",
            sa.Text,
            nullable=False,
            comment="markdown README 内容",
        ),
        sa.Column(
            "updated_by",
            sa.String(128),
            nullable=False,
            comment="最后更新者 user_id",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="更新时间",
        ),
        comment="ADR-012 阶段 6 p6-3：数据集 README 表",
    )
    # 单列索引（dataset_id 用于按数据集查询 README）
    op.create_index(
        "ix_dataset_readmes_dataset_id", "dataset_readmes", ["dataset_id"]
    )
    # 版本级 README 唯一约束（version IS NOT NULL）
    # 使用 partial unique index 实现跨库兼容
    op.create_index(
        "uq_dataset_readmes_version_level",
        "dataset_readmes",
        ["dataset_id", "version"],
        unique=True,
        sqlite_where=sa.text("version IS NOT NULL"),
        postgresql_where=sa.text("version IS NOT NULL"),
    )
    # 数据集级 README 唯一约束（version IS NULL）
    op.create_index(
        "uq_dataset_readmes_dataset_level",
        "dataset_readmes",
        ["dataset_id"],
        unique=True,
        sqlite_where=sa.text("version IS NULL"),
        postgresql_where=sa.text("version IS NULL"),
    )


def downgrade() -> None:
    """Drop dataset_readmes / model_artifacts tables."""
    # dataset_readmes（先删 partial unique index，再删单列索引，最后删表）
    op.drop_index(
        "uq_dataset_readmes_dataset_level", table_name="dataset_readmes"
    )
    op.drop_index(
        "uq_dataset_readmes_version_level", table_name="dataset_readmes"
    )
    op.drop_index("ix_dataset_readmes_dataset_id", table_name="dataset_readmes")
    op.drop_table("dataset_readmes")

    # model_artifacts（先删复合索引，再删单列索引，再删唯一约束，最后删表）
    op.drop_index(
        "ix_model_artifacts_name_version", table_name="model_artifacts"
    )
    op.drop_index(
        "ix_model_artifacts_type_status", table_name="model_artifacts"
    )
    op.drop_index(
        "ix_model_artifacts_owner_status", table_name="model_artifacts"
    )
    op.drop_index("ix_model_artifacts_status", table_name="model_artifacts")
    op.drop_index("ix_model_artifacts_owner_id", table_name="model_artifacts")
    op.drop_index("ix_model_artifacts_name", table_name="model_artifacts")
    op.drop_index("ix_model_artifacts_model_uri", table_name="model_artifacts")
    op.drop_constraint(
        "uq_model_artifacts_model_uri", "model_artifacts", type_="unique"
    )
    op.drop_table("model_artifacts")
