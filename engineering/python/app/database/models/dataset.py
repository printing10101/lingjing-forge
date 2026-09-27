"""数据集 / 血缘 / 实验快照 ORM 模型.

对应 ADR-005 阶段 2 / core-contracts-design.md 第 4 章与第 7 章。

新增 4 张表（与 training_task.Base 共享 metadata）：
    - ``datasets``：数据集元数据（name + schema + owner）
    - ``dataset_versions``：数据集版本（不可变快照，content_hash 内容寻址）
    - ``lineage_records``：血缘记录（target / source / inputs / outputs）
    - ``experiment_snapshots``：实验快照（git_sha + config + metrics + 一键复现）

设计要点：
    - 字段 ``meta`` 而非 ``metadata``：SQLAlchemy Declarative API 保留 ``metadata``
    - 时间统一用 ``app.utils.time.utcnow``（避免 ``utcnow()`` 在
      Python 3.12+ 的 DeprecationWarning），避免时区依赖
    - JSON 字段用于灵活 schema（fields / config / metrics / environment）
    - content_hash / git_sha 建索引，便于内容寻址与按代码版本查询
"""

from __future__ import annotations

import uuid

from app.utils.time import utcnow
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _gen_uuid() -> str:
    return str(uuid.uuid4())


class Dataset(Base):
    """数据集元数据.

    一个 dataset 包含多个不可变版本（DatasetVersion）。
    """

    __tablename__ = "datasets"

    id = Column(String(64), primary_key=True, default=_gen_uuid)
    name = Column(String(128), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=False, default="")
    # schema 序列化为 JSON：{"fields": {...}, "primary_key": [...], "metadata": {...}}
    schema_json = Column(
        Text,
        nullable=False,
        comment="DatasetSchema 序列化 JSON",
    )
    owner_id = Column(String(128), nullable=False, index=True)
    status = Column(
        String(32),
        nullable=False,
        default="draft",
        comment="draft/published/deprecated/archived",
    )
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
    )

    versions = relationship(
        "DatasetVersion",
        back_populates="dataset",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (Index("ix_datasets_owner_status", "owner_id", "status"),)


class DatasetVersion(Base):
    """数据集版本（不可变快照）.

    一旦 status=published 即不可修改，只能 deprecate/archive。
    content_hash 为 sha256，内容寻址；storage_uri 指向实际存储位置。
    """

    __tablename__ = "dataset_versions"

    id = Column(String(64), primary_key=True, default=_gen_uuid)
    dataset_id = Column(
        String(64),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version = Column(String(32), nullable=False, comment="semver, e.g. 1.0.0")
    status = Column(
        String(32),
        nullable=False,
        default="published",
        comment="draft/published/deprecated/archived",
    )
    content_hash = Column(String(64), nullable=False, index=True)
    row_count = Column(Integer, nullable=False, default=0)
    size_bytes = Column(Integer, nullable=False, default=0)
    storage_uri = Column(String(512), nullable=False)
    lineage_record_id = Column(
        String(64),
        ForeignKey("lineage_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime, nullable=False, default=utcnow)
    created_by = Column(String(128), nullable=False)

    dataset = relationship("Dataset", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("dataset_id", "version", name="uq_dataset_version"),
        Index("ix_dataset_versions_status", "status"),
    )


class LineageRecord(Base):
    """血缘记录.

    记录 "谁在什么时候用什么输入产出了什么输出"。
    target / inputs / outputs 均为 URI 形式：
        - dataset://<name>/<version>
        - model://<name>/<version>
        - artifact://<uri>
    """

    __tablename__ = "lineage_records"

    id = Column(String(64), primary_key=True, default=_gen_uuid)
    target = Column(String(512), nullable=False, index=True)
    source_type = Column(
        String(32),
        nullable=False,
        comment="task/workflow/manual/external",
    )
    source_ref = Column(String(512), nullable=False)
    inputs_json = Column(Text, nullable=False, default="[]")
    outputs_json = Column(Text, nullable=False, default="[]")
    operation = Column(String(64), nullable=False, default="")
    timestamp = Column(DateTime, nullable=False, default=utcnow)
    meta_json = Column(Text, nullable=False, default="{}")

    __table_args__ = (
        Index("ix_lineage_target_ts", "target", "timestamp"),
        Index("ix_lineage_source", "source_type", "source_ref"),
    )


class ExperimentSnapshot(Base):
    """实验快照（一键复现的最小单元）.

    包含 git_sha + 配置 + 数据集版本 + 模型 URI + 指标 + 环境。
    复现时根据 snapshot 重建 WorkflowSpec 并提交执行。
    """

    __tablename__ = "experiment_snapshots"

    id = Column(String(64), primary_key=True, default=_gen_uuid)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    created_by = Column(String(128), nullable=False, index=True)
    git_sha = Column(String(64), nullable=False, index=True)
    code_dirty = Column(Boolean, nullable=False, default=False)
    config_json = Column(Text, nullable=False)
    dataset_versions_json = Column(Text, nullable=False, default="[]")
    model_uri = Column(String(512), nullable=False)
    metrics_json = Column(Text, nullable=False, default="{}")
    environment_json = Column(Text, nullable=False, default="{}")
    lineage_record_id = Column(
        String(64),
        ForeignKey("lineage_records.id", ondelete="SET NULL"),
        nullable=True,
    )
    mlflow_run_id = Column(String(128), nullable=True)
    notes = Column(Text, nullable=False, default="")

    __table_args__ = (
        Index("ix_snapshots_created_by_ts", "created_by", "created_at"),
        Index("ix_snapshots_git_sha", "git_sha"),
    )


__all__ = [
    "Dataset",
    "DatasetVersion",
    "LineageRecord",
    "ExperimentSnapshot",
]
