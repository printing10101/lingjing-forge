"""世界模型 ORM 模型：世界模型版本记录持久化.

对应 ADR-017（世界模型与 RL 模块）第 1 节。

新增 1 张表（与 training_task.Base 共享 metadata，与 explainability.py 同源）：
    - ``world_model_versions``：世界模型版本记录（version / model_uri /
      training_data_size / prediction_horizon / is_active）

设计要点
--------
    - 版本号 ``version`` 为 semver 字符串，``model_uri`` 唯一索引
    - ``is_active`` 标记当前激活版本，同一时刻仅允许一个激活版本
      （由服务层在注册新版本时保证）
    - 与 explainability.py 风格对齐：uuid 前缀生成器 + to_dict() + __repr__
    - 不存放大数组（轨迹预测结果在调用时按需生成，不入库）
"""

from __future__ import annotations

import uuid
from typing import Any

from app.utils.time import utcnow

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from app.database.models.training_task import Base


def _gen_world_model_version_id() -> str:
    """生成世界模型版本记录 ID（wmv_ 前缀 + uuid hex）."""
    return f"wmv_{uuid.uuid4().hex}"


class WorldModelVersionORM(Base):
    """世界模型版本记录 ORM.

    持久化已注册的世界模型版本元信息，支持前端列出版本 / 查询版本详情 /
    切换激活版本。轨迹预测结果不入库（按需生成）。

    注意
    ----
    - ``version`` 为 semver 字符串，``model_uri`` 全局唯一
    - ``is_active`` 同一时刻仅允许一个为 True（由服务层保证）
    - 与 ``app.contracts.world_model.WorldModelVersion`` dataclass 对齐
    """

    __tablename__ = "world_model_versions"

    id = Column(
        String(64),
        primary_key=True,
        default=_gen_world_model_version_id,
        comment="版本记录 ID（wmv_ 前缀 + uuid）",
    )
    version = Column(
        String(32),
        nullable=False,
        index=True,
        comment="版本号（semver，如 1.0.0）",
    )
    model_uri = Column(
        String(256),
        nullable=False,
        unique=True,
        index=True,
        comment="模型 URI（model://world_model/<version>）",
    )
    description = Column(
        Text,
        nullable=True,
        comment="版本描述",
    )
    training_data_size = Column(
        Integer,
        nullable=False,
        default=0,
        comment="训练数据样本数",
    )
    prediction_horizon = Column(
        Integer,
        nullable=False,
        default=10,
        comment="训练时的预测步长",
    )
    is_active = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
        comment="是否为当前激活版本",
    )
    created_at = Column(
        DateTime,
        nullable=False,
        default=utcnow,
        index=True,
        comment="创建时间",
    )

    __table_args__ = (
        Index(
            "ix_world_model_versions_version_active",
            "version",
            "is_active",
        ),
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 API 响应）."""
        return {
            "id": self.id,
            "version": self.version,
            "model_uri": self.model_uri,
            "description": self.description or "",
            "training_data_size": self.training_data_size,
            "prediction_horizon": self.prediction_horizon,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<WorldModelVersionORM(id={self.id}, version={self.version}, "
            f"model_uri={self.model_uri}, is_active={self.is_active})>"
        )


__all__ = ["WorldModelVersionORM", "_gen_world_model_version_id"]
