"""add_explainability

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-07-14 22:30:00.000000

ADR-016 阶段 7 p7-alembic：新增可解释性可视化持久化表
（解释记录 + 解释对比记录）。

设计要点：
    - explanation_records: 解释任务记录表
      (explanation_type / model_uri / source_snapshot_id / input_signature /
       payload_path / payload_size_bytes / metadata_json)
    - explanation_comparisons: 解释对比记录表
      (base_explanation_id / compared_explanation_id / comparison_type /
       diff_payload_path)
    - payload（含大型数组：隐状态投影坐标 / 门控值序列 / 反事实扫描曲线 /
      MC dropout 直方图）以 JSON 文件存盘，数据库只存元数据 + payload_path，
      避免 JSON 数组膨胀数据库（与 project_package.py 风格一致）
    - input_signature 字段索引，支持相同输入 + 相同模型 + 相同解释类型的
      解释结果去重与缓存命中查询
    - explanation_type / comparison_type 为字符串（与契约层常量对齐），
      不使用枚举类型以保持 SQLite 兼容性
    - source_snapshot_id 为字符串字段（不建外键），因为快照 ID 来自
      ExperimentSnapshot，在解释生成时不一定存在对应记录
    - base_explanation_id / compared_explanation_id 外键关联
      explanation_records.id，ondelete=CASCADE
      （删除解释时连带删除对比记录）
    - expires_at 可空，过期解释由清理任务删除 payload 文件 + 数据库记录
    - 与 b9c0d1e2f3a4 风格对齐：单列索引 + 复合索引组合

记录生命周期：
    - 解释生成是同步操作，生成成功即写入 explanation_records，失败则不写入
    - payload_path 指向 JSON 文件，文件内容为对应 ExplanationXxx.to_payload() 结果
    - expires_at 过期后由清理任务删除 payload 文件 + 数据库记录

对比类型（comparison_type，与 ComparisonType 对齐）：
    - same_model_diff_input：同模型不同输入（诊断输入敏感性）
    - diff_model_same_input：不同模型同输入（模型版本对比）
    - diff_model_diff_input：不同模型不同输入（综合对比）
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create explanation_records / explanation_comparisons tables."""
    # -----------------------------------------------------------------------
    # explanation_records 表：解释任务记录
    # -----------------------------------------------------------------------
    op.create_table(
        "explanation_records",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="解释 ID（exp_ 前缀 + uuid）",
        ),
        sa.Column(
            "explanation_type",
            sa.String(32),
            nullable=False,
            comment="解释类型：hidden_state/gate_dynamics/counterfactual/confidence",
        ),
        sa.Column(
            "model_uri",
            sa.String(256),
            nullable=False,
            comment="解释所用模型 URI（如 model://LTC-ChatterPredictor/1.0.0）",
        ),
        sa.Column(
            "source_snapshot_id",
            sa.String(64),
            nullable=True,
            comment="关联实验快照 ID（来自 ExperimentSnapshot，不建外键）",
        ),
        sa.Column(
            "input_signature",
            sa.String(64),
            nullable=False,
            comment="输入签名 sha256 前 16 字符（去重与缓存命中查询）",
        ),
        sa.Column(
            "payload_path",
            sa.String(512),
            nullable=False,
            comment="payload JSON 文件绝对路径（含大型数组）",
        ),
        sa.Column(
            "payload_size_bytes",
            sa.BigInteger,
            nullable=False,
            server_default="0",
            comment="payload 文件大小（字节）",
        ),
        sa.Column(
            "metadata_json",
            sa.Text,
            nullable=True,
            comment="附加元数据 JSON（降维方法/采样次数/异常帧数等）",
        ),
        sa.Column(
            "created_by",
            sa.String(128),
            nullable=True,
            comment="创建者 user_id 或 plugin_id",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="过期时间（过期后由清理任务删除 payload 文件 + 记录）",
        ),
        comment="ADR-016 阶段 7 p7-alembic：解释任务记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_explanation_records_explanation_type",
        "explanation_records",
        ["explanation_type"],
    )
    op.create_index(
        "ix_explanation_records_model_uri",
        "explanation_records",
        ["model_uri"],
    )
    op.create_index(
        "ix_explanation_records_source_snapshot_id",
        "explanation_records",
        ["source_snapshot_id"],
    )
    op.create_index(
        "ix_explanation_records_input_signature",
        "explanation_records",
        ["input_signature"],
    )
    op.create_index(
        "ix_explanation_records_created_by",
        "explanation_records",
        ["created_by"],
    )
    op.create_index(
        "ix_explanation_records_created_at",
        "explanation_records",
        ["created_at"],
    )
    op.create_index(
        "ix_explanation_records_expires_at",
        "explanation_records",
        ["expires_at"],
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_explanation_records_type_model",
        "explanation_records",
        ["explanation_type", "model_uri"],
    )
    op.create_index(
        "ix_explanation_records_signature_type",
        "explanation_records",
        ["input_signature", "explanation_type"],
    )

    # -----------------------------------------------------------------------
    # explanation_comparisons 表：解释对比记录
    # -----------------------------------------------------------------------
    op.create_table(
        "explanation_comparisons",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="对比 ID（cmp_ 前缀 + uuid）",
        ),
        sa.Column(
            "base_explanation_id",
            sa.String(64),
            sa.ForeignKey("explanation_records.id", ondelete="CASCADE"),
            nullable=False,
            comment="基准解释记录 ID",
        ),
        sa.Column(
            "compared_explanation_id",
            sa.String(64),
            sa.ForeignKey("explanation_records.id", ondelete="CASCADE"),
            nullable=False,
            comment="对比解释记录 ID",
        ),
        sa.Column(
            "comparison_type",
            sa.String(64),
            nullable=False,
            comment="对比类型：same_model_diff_input/diff_model_same_input/diff_model_diff_input",
        ),
        sa.Column(
            "diff_payload_path",
            sa.String(512),
            nullable=False,
            comment="差异 payload JSON 文件路径",
        ),
        sa.Column(
            "created_by",
            sa.String(128),
            nullable=True,
            comment="创建者 user_id 或 plugin_id",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="创建时间",
        ),
        comment="ADR-016 阶段 7 p7-alembic：解释对比记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_explanation_comparisons_base_explanation_id",
        "explanation_comparisons",
        ["base_explanation_id"],
    )
    op.create_index(
        "ix_explanation_comparisons_compared_explanation_id",
        "explanation_comparisons",
        ["compared_explanation_id"],
    )
    op.create_index(
        "ix_explanation_comparisons_created_by",
        "explanation_comparisons",
        ["created_by"],
    )
    op.create_index(
        "ix_explanation_comparisons_created_at",
        "explanation_comparisons",
        ["created_at"],
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_explanation_comparisons_base_compared",
        "explanation_comparisons",
        ["base_explanation_id", "compared_explanation_id"],
    )


def downgrade() -> None:
    """Drop explanation_comparisons / explanation_records tables."""
    # explanation_comparisons（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_explanation_comparisons_base_compared",
        table_name="explanation_comparisons",
    )
    op.drop_index(
        "ix_explanation_comparisons_created_at",
        table_name="explanation_comparisons",
    )
    op.drop_index(
        "ix_explanation_comparisons_created_by",
        table_name="explanation_comparisons",
    )
    op.drop_index(
        "ix_explanation_comparisons_compared_explanation_id",
        table_name="explanation_comparisons",
    )
    op.drop_index(
        "ix_explanation_comparisons_base_explanation_id",
        table_name="explanation_comparisons",
    )
    op.drop_table("explanation_comparisons")

    # explanation_records（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_explanation_records_signature_type",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_type_model",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_expires_at",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_created_at",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_created_by",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_input_signature",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_source_snapshot_id",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_model_uri",
        table_name="explanation_records",
    )
    op.drop_index(
        "ix_explanation_records_explanation_type",
        table_name="explanation_records",
    )
    op.drop_table("explanation_records")
