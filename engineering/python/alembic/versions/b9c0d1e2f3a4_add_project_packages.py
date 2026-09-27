"""add_project_packages

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-14 21:00:00.000000

ADR-015 阶段 6 p6-4：新增项目导入导出持久化表（.lomo 包导出记录 + 导入记录）。

设计要点：
    - project_exports: 导出任务记录表（package_path / format_version / checksum / status）
    - project_imports: 导入任务记录表（source_package_path / conflict_strategy / 计数 / status）
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
    - 与 resource_card.py 风格对齐：单列索引 + 复合索引组合

状态机（与 PackageTaskStatus 对齐）：
    pending → running → completed
                      → failed
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create project_exports / project_imports tables."""
    # -----------------------------------------------------------------------
    # project_exports 表：项目导出任务记录
    # -----------------------------------------------------------------------
    op.create_table(
        "project_exports",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="导出 ID（pexp_ 前缀 + uuid）",
        ),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("project_repos.project_id", ondelete="RESTRICT"),
            nullable=False,
            comment="关联项目 ID（关联 project_repos.project_id）",
        ),
        sa.Column(
            "package_path",
            sa.String(512),
            nullable=False,
            comment=".lomo 文件绝对路径",
        ),
        sa.Column(
            "format_version",
            sa.String(32),
            nullable=False,
            comment="包格式版本（如 1.0.0），与 PackageFormatVersion 对齐",
        ),
        sa.Column(
            "content_policy",
            sa.String(32),
            nullable=False,
            comment="内容策略：metadata_only/include_content/small_files_only",
        ),
        sa.Column(
            "resource_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="资源数量（manifest.resources 长度）",
        ),
        sa.Column(
            "total_size_bytes",
            sa.BigInteger,
            nullable=False,
            server_default="0",
            comment="包总大小（未压缩前字节数，来自 manifest.total_size_bytes）",
        ),
        sa.Column(
            "checksum",
            sa.String(128),
            nullable=False,
            server_default="",
            comment="manifest.json sha256 校验和（sha256:<hex>）",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="任务状态：pending/running/completed/failed",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="失败原因（status=failed 时填写）",
        ),
        sa.Column(
            "exported_by",
            sa.String(128),
            nullable=False,
            comment="导出者 user_id 或 plugin_id",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="任务创建时间",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="任务完成时间（completed/failed 时填写）",
        ),
        comment="ADR-015 阶段 6 p6-4：项目导出任务记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_project_exports_project_id", "project_exports", ["project_id"]
    )
    op.create_index(
        "ix_project_exports_status", "project_exports", ["status"]
    )
    op.create_index(
        "ix_project_exports_exported_by", "project_exports", ["exported_by"]
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_project_exports_project_id_status",
        "project_exports",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_project_exports_exported_by_created",
        "project_exports",
        ["exported_by", "created_at"],
    )

    # -----------------------------------------------------------------------
    # project_imports 表：项目导入任务记录
    # -----------------------------------------------------------------------
    op.create_table(
        "project_imports",
        sa.Column(
            "id",
            sa.String(64),
            primary_key=True,
            comment="导入 ID（pimp_ 前缀 + uuid）",
        ),
        sa.Column(
            "source_package_path",
            sa.String(512),
            nullable=False,
            comment="源 .lomo 文件绝对路径",
        ),
        sa.Column(
            "source_project_id",
            sa.String(64),
            nullable=False,
            comment="源项目 ID（来自 manifest.project.project_id，不建外键）",
        ),
        sa.Column(
            "target_project_id",
            sa.String(64),
            sa.ForeignKey("project_repos.project_id", ondelete="RESTRICT"),
            nullable=False,
            comment="目标项目 ID（关联 project_repos.project_id）",
        ),
        sa.Column(
            "format_version",
            sa.String(32),
            nullable=False,
            comment="包格式版本（如 1.0.0）",
        ),
        sa.Column(
            "conflict_strategy",
            sa.String(32),
            nullable=False,
            comment="冲突策略：skip/overwrite/rename/fail",
        ),
        sa.Column(
            "imported_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="成功导入资源数",
        ),
        sa.Column(
            "skipped_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="跳过资源数（冲突策略 skip 或目标已存在）",
        ),
        sa.Column(
            "renamed_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="重命名资源数（冲突策略 rename）",
        ),
        sa.Column(
            "failed_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="失败资源数（写入异常或校验失败）",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
            comment="任务状态：pending/running/completed/failed",
        ),
        sa.Column(
            "error_message",
            sa.Text,
            nullable=True,
            comment="失败原因（status=failed 时填写）",
        ),
        sa.Column(
            "imported_by",
            sa.String(128),
            nullable=False,
            comment="导入者 user_id 或 plugin_id",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="任务创建时间",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="任务完成时间（completed/failed 时填写）",
        ),
        comment="ADR-015 阶段 6 p6-4：项目导入任务记录表",
    )
    # 单列索引（与 ORM index=True 对齐）
    op.create_index(
        "ix_project_imports_target_project_id",
        "project_imports",
        ["target_project_id"],
    )
    op.create_index(
        "ix_project_imports_status", "project_imports", ["status"]
    )
    op.create_index(
        "ix_project_imports_imported_by", "project_imports", ["imported_by"]
    )
    # 复合索引（与 ORM __table_args__ 对齐）
    op.create_index(
        "ix_project_imports_target_project_id_status",
        "project_imports",
        ["target_project_id", "status"],
    )
    op.create_index(
        "ix_project_imports_imported_by_created",
        "project_imports",
        ["imported_by", "created_at"],
    )


def downgrade() -> None:
    """Drop project_imports / project_exports tables."""
    # project_imports（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_project_imports_imported_by_created", table_name="project_imports"
    )
    op.drop_index(
        "ix_project_imports_target_project_id_status",
        table_name="project_imports",
    )
    op.drop_index(
        "ix_project_imports_imported_by", table_name="project_imports"
    )
    op.drop_index("ix_project_imports_status", table_name="project_imports")
    op.drop_index(
        "ix_project_imports_target_project_id", table_name="project_imports"
    )
    op.drop_table("project_imports")

    # project_exports（先删复合索引，再删单列索引，最后删表）
    op.drop_index(
        "ix_project_exports_exported_by_created", table_name="project_exports"
    )
    op.drop_index(
        "ix_project_exports_project_id_status", table_name="project_exports"
    )
    op.drop_index(
        "ix_project_exports_exported_by", table_name="project_exports"
    )
    op.drop_index("ix_project_exports_status", table_name="project_exports")
    op.drop_index(
        "ix_project_exports_project_id", table_name="project_exports"
    )
    op.drop_table("project_exports")
