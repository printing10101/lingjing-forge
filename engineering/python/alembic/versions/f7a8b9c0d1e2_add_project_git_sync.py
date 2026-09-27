"""add_project_git_sync

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-14 19:30:00.000000

ADR-011 阶段 6 p6-2：新增项目级 Git 同步持久化表。

设计要点：
    - project_repos: 项目仓库主表（name/repo_path/remote_url/branch/commit/status）
    - project_resource_refs: 资源引用表（resource_type/uri/content_hash/sync_strategy）
    - project_sync_records: 同步记录表（direction/commit_sha/status/message）
    - 使用 JSONB.with_variant(JSON, "sqlite") 跨库兼容
    - 资源引用与同步记录通过 project_id 外键关联主表，ondelete CASCADE
    - (project_id, resource_uri) 复合唯一约束，确保同项目内资源 URI 不重复
    - 复用 training_task.py 的 Base，init_db 自动 create_all

资源 URI 体系（与 ADR-005 对齐）：
    dataset://<id>/<version>
    model://<name>/<version>
    workflow://<run_id>
    config://<spec_name>
    snapshot://<snapshot_id>
    template://<template_id>/<version>
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create project_repos / project_resource_refs / project_sync_records tables."""
    # -----------------------------------------------------------------------
    # project_repos 表：项目仓库主表
    # -----------------------------------------------------------------------
    op.create_table(
        "project_repos",
        sa.Column(
            "project_id",
            sa.String(64),
            primary_key=True,
            comment="项目 ID（prj_ 前缀，全局唯一）",
        ),
        sa.Column("name", sa.String(256), nullable=False, comment="项目显示名"),
        sa.Column(
            "repo_path",
            sa.String(512),
            nullable=False,
            comment="仓库本地路径（绝对路径或相对 output_dir 的路径）",
        ),
        sa.Column(
            "remote_url",
            sa.String(1024),
            nullable=True,
            comment="远端仓库 URL（空表示纯本地仓库）",
        ),
        sa.Column(
            "current_branch",
            sa.String(128),
            nullable=False,
            server_default="main",
            comment="当前分支名",
        ),
        sa.Column(
            "current_commit",
            sa.String(64),
            nullable=True,
            comment="当前 HEAD commit sha（空表示未提交）",
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="clean",
            comment="同步状态：clean/dirty/ahead/behind/conflict/error",
        ),
        sa.Column("description", sa.String(2048), nullable=True, comment="项目描述"),
        sa.Column("author", sa.String(128), nullable=True, comment="项目作者"),
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
        comment="ADR-011 阶段 6 p6-2：项目仓库主表",
    )
    op.create_index("ix_project_repos_status", "project_repos", ["status"])
    op.create_index("ix_project_repos_author", "project_repos", ["author"])
    op.create_index(
        "ix_project_repos_author_created", "project_repos", ["author", "created_at"]
    )
    op.create_index(
        "ix_project_repos_status_updated", "project_repos", ["status", "updated_at"]
    )

    # -----------------------------------------------------------------------
    # project_resource_refs 表：资源引用表
    # -----------------------------------------------------------------------
    op.create_table(
        "project_resource_refs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("project_repos.project_id", ondelete="CASCADE"),
            nullable=False,
            comment="所属项目 ID（关联 project_repos.project_id）",
        ),
        sa.Column(
            "resource_type",
            sa.String(32),
            nullable=False,
            comment="资源类型：dataset/model/workflow/config/snapshot/template",
        ),
        sa.Column(
            "resource_uri",
            sa.String(512),
            nullable=False,
            comment="资源 URI（如 dataset://phm2010/v3）",
        ),
        sa.Column(
            "content_hash",
            sa.String(64),
            nullable=True,
            comment="内容哈希（sha256 hex，64 字符；空表示未计算）",
        ),
        sa.Column(
            "sync_strategy",
            sa.String(32),
            nullable=False,
            server_default="hash_referenced",
            comment="同步策略：git_tracked/hash_referenced/git_lfs",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="附加元数据（文件大小、来源插件 id、自定义标签等）",
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
        comment="ADR-011 阶段 6 p6-2：项目资源引用表",
    )
    op.create_index(
        "ix_project_resource_refs_project_id",
        "project_resource_refs",
        ["project_id"],
    )
    op.create_index(
        "ix_project_resource_refs_resource_type",
        "project_resource_refs",
        ["resource_type"],
    )
    op.create_index(
        "ix_project_resource_refs_content_hash",
        "project_resource_refs",
        ["content_hash"],
    )
    op.create_index(
        "ix_project_resource_refs_type_project",
        "project_resource_refs",
        ["resource_type", "project_id"],
    )
    op.create_unique_constraint(
        "uq_project_resource_uri",
        "project_resource_refs",
        ["project_id", "resource_uri"],
    )

    # -----------------------------------------------------------------------
    # project_sync_records 表：同步记录表
    # -----------------------------------------------------------------------
    op.create_table(
        "project_sync_records",
        sa.Column("record_id", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.String(64),
            sa.ForeignKey("project_repos.project_id", ondelete="CASCADE"),
            nullable=False,
            comment="所属项目 ID（关联 project_repos.project_id）",
        ),
        sa.Column(
            "direction",
            sa.String(16),
            nullable=False,
            comment="同步方向：init/commit/push/pull/clone",
        ),
        sa.Column(
            "commit_sha",
            sa.String(64),
            nullable=True,
            comment="涉及的 commit sha（push/pull/commit 时填写）",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="success",
            comment="操作结果状态：success/failed/conflict",
        ),
        sa.Column(
            "message",
            sa.String(2048),
            nullable=True,
            comment="操作消息（commit message 或错误描述）",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
            comment="操作时间戳",
        ),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
            comment="附加详情（变更文件数、字节数、远端 URL 等）",
        ),
        comment="ADR-011 阶段 6 p6-2：项目同步记录表",
    )
    op.create_index(
        "ix_project_sync_records_project_id",
        "project_sync_records",
        ["project_id"],
    )
    op.create_index(
        "ix_project_sync_records_direction",
        "project_sync_records",
        ["direction"],
    )
    op.create_index(
        "ix_project_sync_records_timestamp",
        "project_sync_records",
        ["timestamp"],
    )
    op.create_index(
        "ix_project_sync_records_project_timestamp",
        "project_sync_records",
        ["project_id", "timestamp"],
    )


def downgrade() -> None:
    """Drop project_sync_records / project_resource_refs / project_repos tables."""
    # project_sync_records
    op.drop_index(
        "ix_project_sync_records_project_timestamp",
        table_name="project_sync_records",
    )
    op.drop_index(
        "ix_project_sync_records_timestamp", table_name="project_sync_records"
    )
    op.drop_index(
        "ix_project_sync_records_direction", table_name="project_sync_records"
    )
    op.drop_index(
        "ix_project_sync_records_project_id", table_name="project_sync_records"
    )
    op.drop_table("project_sync_records")

    # project_resource_refs
    op.drop_constraint(
        "uq_project_resource_uri", "project_resource_refs", type_="unique"
    )
    op.drop_index(
        "ix_project_resource_refs_type_project", table_name="project_resource_refs"
    )
    op.drop_index(
        "ix_project_resource_refs_content_hash", table_name="project_resource_refs"
    )
    op.drop_index(
        "ix_project_resource_refs_resource_type", table_name="project_resource_refs"
    )
    op.drop_index(
        "ix_project_resource_refs_project_id", table_name="project_resource_refs"
    )
    op.drop_table("project_resource_refs")

    # project_repos
    op.drop_index("ix_project_repos_status_updated", table_name="project_repos")
    op.drop_index("ix_project_repos_author_created", table_name="project_repos")
    op.drop_index("ix_project_repos_author", table_name="project_repos")
    op.drop_index("ix_project_repos_status", table_name="project_repos")
    op.drop_table("project_repos")
