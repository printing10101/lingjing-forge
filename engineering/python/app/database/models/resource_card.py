"""资源卡片 ORM 模型：模型产物 + 数据集 README 持久化.

对应 ADR-012（资源卡片）。

新增 2 张表（与 training_task.Base 共享 metadata，与 dataset.py 同源）：
    - ``model_artifacts``：模型产物元数据（model_uri / version / framework / metrics / readme）
    - ``dataset_readmes``：数据集 README（支持数据集级 + 版本级）

设计要点：
    - 不修改现有 datasets / dataset_versions / lineage_records / experiment_snapshots
      表结构，保持 ADR-005 契约稳定性
    - model_uri 唯一索引，与 ADR-011 项目同步 URI 体系对齐
    - dataset_readmes 通过 partial unique index 保证同一 dataset_id + version 唯一，
      其中 version IS NULL 时表示数据集级 README（每个 dataset 最多 1 条）
    - metrics_json / metrics_history_json / tags_json / readme_md 使用 Text + JSON
      序列化，与 dataset.py 风格对齐
    - 模型状态机与 DatasetStatus 对齐：draft/published/deprecated/archived
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from app.utils.time import utcnow

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import relationship

from app.database.models.training_task import Base


def _gen_uuid() -> str:
    return str(uuid.uuid4())


def _gen_model_id() -> str:
    """生成模型 ID（mdl_ 前缀 + uuid）."""
    return f"mdl_{uuid.uuid4().hex}"


def _gen_readme_id() -> str:
    """生成 README ID（readme_ 前缀 + uuid）."""
    return f"readme_{uuid.uuid4().hex}"


def _json_dumps(value: Any) -> str:
    """安全 JSON 序列化（确保非 ASCII 字符不被转义）."""
    if value is None:
        return "[]"
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    """安全 JSON 反序列化."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


class ModelArtifact(Base):
    """模型产物 ORM.

    持久化模型元数据 + 指标 + README + 标签，与 LNNModelRegistry 内存单例互补。
    model_uri 是全局唯一标识（model://<name>/<version>），与 ADR-011 项目同步对齐。

    状态机（与 DatasetStatus 对齐）：
        draft → published（不可变）
        published → deprecated → archived
        draft → archived
    """

    __tablename__ = "model_artifacts"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_model_id,
        comment="模型 ID（mdl_ 前缀 + uuid）",
    )
    model_uri = Column(
        String(512),
        nullable=False,
        unique=True,
        index=True,
        comment="模型 URI（model://<name>/<version>），全局唯一",
    )
    name = Column(
        String(128),
        nullable=False,
        index=True,
        comment="模型显示名（如 LTC-ChatterPredictor）",
    )
    model_type = Column(
        String(32),
        nullable=False,
        comment="模型类型：lnn/pytorch/onnx/sklearn/other",
    )
    version = Column(
        String(32),
        nullable=False,
        comment="semver 版本号，如 1.0.0",
    )
    framework = Column(
        String(64),
        nullable=False,
        comment="框架版本，如 torch-2.1.0",
    )
    storage_uri = Column(
        String(512),
        nullable=False,
        comment="模型文件存储位置（file:// / s3:// 路径）",
    )
    metrics_json = Column(
        Text,
        nullable=False,
        default="{}",
        comment="当前指标快照 JSON，如 {accuracy: 0.95, loss: 0.05}",
    )
    metrics_history_json = Column(
        Text,
        nullable=False,
        default="[]",
        comment="指标历史 JSON 数组（追加式记录，每项含 timestamp + metrics）",
    )
    readme_md = Column(
        Text,
        nullable=False,
        default="",
        comment="markdown README",
    )
    tags_json = Column(
        Text,
        nullable=False,
        default="[]",
        comment="标签 JSON 数组",
    )
    owner_id = Column(
        String(128),
        nullable=False,
        index=True,
        comment="所有者 user_id 或 plugin_id",
    )
    status = Column(
        String(32),
        nullable=False,
        default="draft",
        index=True,
        comment="draft/published/deprecated/archived",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        comment="创建时间",
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间",
    )

    __table_args__ = (
        Index(
            "ix_model_artifacts_owner_status",
            "owner_id",
            "status",
        ),
        Index(
            "ix_model_artifacts_type_status",
            "model_type",
            "status",
        ),
        Index(
            "ix_model_artifacts_name_version",
            "name",
            "version",
        ),
    )

    @property
    def metrics(self) -> dict[str, Any]:
        """当前指标快照（反序列化）."""
        return _json_loads(str(self.metrics_json), {}) or {}

    @metrics.setter
    def metrics(self, value: dict[str, Any]) -> None:
        self.metrics_json = _json_dumps(value)  # type: ignore[assignment]

    @property
    def metrics_history(self) -> list[dict[str, Any]]:
        """指标历史列表（反序列化）."""
        return _json_loads(str(self.metrics_history_json), []) or []

    @metrics_history.setter
    def metrics_history(self, value: list[dict[str, Any]]) -> None:
        self.metrics_history_json = _json_dumps(value)  # type: ignore[assignment]

    @property
    def tags(self) -> list[str]:
        """标签列表（反序列化）."""
        return _json_loads(str(self.tags_json), []) or []

    @tags.setter
    def tags(self, value: list[str]) -> None:
        self.tags_json = _json_dumps(value)  # type: ignore[assignment]

    def append_metrics(self, metrics: dict[str, Any], *, timestamp: datetime | None = None) -> None:
        """追加一条指标记录到历史.

        Args:
            metrics: 指标字典，如 {accuracy: 0.95, loss: 0.05}
            timestamp: 记录时间，None 取当前时间
        """
        ts = timestamp or utcnow()
        history = self.metrics_history
        history.append(
            {
                "timestamp": ts.isoformat(),
                "metrics": dict(metrics),
            }
        )
        self.metrics_history = history
        # 同步更新当前指标快照
        self.metrics = dict(metrics)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "model_id": self.id,
            "model_uri": self.model_uri,
            "name": self.name,
            "model_type": self.model_type,
            "version": self.version,
            "framework": self.framework,
            "storage_uri": self.storage_uri,
            "metrics": self.metrics,
            "metrics_history": self.metrics_history,
            "readme_md": self.readme_md,
            "tags": self.tags,
            "owner_id": self.owner_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<ModelArtifact(id={self.id}, name={self.name}, version={self.version}, status={self.status})>"


class DatasetReadme(Base):
    """数据集 README ORM.

    支持两级 README：
        - 数据集级：version IS NULL，每个 dataset 最多 1 条（partial unique index 保证）
        - 版本级：version IS NOT NULL，每个 (dataset_id, version) 最多 1 条

    前端展示时优先取版本级，回退到数据集级，再回退到 Dataset.description。
    """

    __tablename__ = "dataset_readmes"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_readme_id,
        comment="README ID（readme_ 前缀 + uuid）",
    )
    dataset_id = Column(
        String(64),
        ForeignKey("datasets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="关联 datasets.id",
    )
    version = Column(
        String(32),
        nullable=True,
        comment="版本号（NULL 表示数据集级 README）",
    )
    readme_md = Column(
        Text,
        nullable=False,
        comment="markdown README 内容",
    )
    updated_by = Column(
        String(128),
        nullable=False,
        comment="最后更新者 user_id",
    )
    updated_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        onupdate=utcnow,
        comment="更新时间",
    )

    dataset = relationship(
        "Dataset",
        lazy="selectin",
        backref="readmes",
    )

    __table_args__ = (
        # 版本级 README 唯一约束（version IS NOT NULL）
        Index(
            "uq_dataset_readmes_version_level",
            "dataset_id",
            "version",
            unique=True,
            sqlite_where=text("version IS NOT NULL"),
            postgresql_where=text("version IS NOT NULL"),
        ),
        # 数据集级 README 唯一约束（version IS NULL）
        Index(
            "uq_dataset_readmes_dataset_level",
            "dataset_id",
            unique=True,
            sqlite_where=text("version IS NULL"),
            postgresql_where=text("version IS NULL"),
        ),
    )

    @property
    def scope(self) -> str:
        """返回 README 作用域：dataset_level / version_level."""
        return "version_level" if self.version else "dataset_level"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "readme_id": self.id,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "scope": self.scope,
            "readme_md": self.readme_md,
            "updated_by": self.updated_by,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<DatasetReadme(id={self.id}, dataset_id={self.dataset_id}, version={self.version})>"


__all__ = [
    "ModelArtifact",
    "DatasetReadme",
]
