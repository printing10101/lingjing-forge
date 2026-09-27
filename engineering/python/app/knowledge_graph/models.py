"""SQLAlchemy ORM models for the knowledge graph storage (M1.2).

设计原则：
    - 与 M0.4 ``machining_records`` ORM 风格保持一致；
    - 使用独立 ``declarative_base``，便于 alembic env.py 单独合并元数据；
    - 属性数据（properties）使用 PostgreSQL 原生 ``JSONB`` 类型；
    - 为 ``node_id`` / ``node_type`` / ``edge_type`` 建立索引以支持高频查询；
    - 业务唯一性约束：
        * 节点：``(node_id)`` 主键即可。
        * 关系：``(source_id, target_id, edge_type)`` 视为同一逻辑关系，
          避免重复入库。
"""

from __future__ import annotations

import logging

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, relationship

logger = logging.getLogger(__name__)

Base = declarative_base()


# SQLite 外键约束


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """为每个新数据库连接启用 SQLite 外键约束（用于级联删除）。

    SQLite 默认不强制外键，需要显式开启。PostgreSQL 始终强制外键，
    此事件对 PG 无副作用。
    """
    # 延迟导入以避免在不可用环境下引发 ImportError
    try:
        # 判定驱动是否为 sqlite3，通过 dialect 名判断
        # 注意：此处 connection_record.engine.dialect.name 包含 "sqlite"
        dialect_name = getattr(getattr(connection_record, "engine", None), "dialect", None)
        if dialect_name is not None and dialect_name.name.startswith("sqlite"):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
    except (OSError, RuntimeError) as exc:  # pragma: no cover - 防御性兜底
        # 任何异常都不应阻塞连接；外键失效仅影响级联行为
        logger.debug("SQLite foreign keys enabling skipped: %s", exc)
        pass


# 节点表（kg_nodes）


class KGNode(Base):  # type: ignore[valid-type, misc]  # declarative_base() 返回值 mypy 不识别为类型
    """知识图谱节点表。

    存储 Material / Tool / Feature / Process 等实体，每个节点拥有唯一
    ``node_id``（遵循 ``<type>-<slug>`` 格式）以及 ``node_type`` 字段
    以便按类型筛选。
    """

    __tablename__ = "kg_nodes"

    node_id = Column(
        String(128),
        primary_key=True,
        comment="节点唯一 ID，格式 '<type>-<slug>'，例如 'material-45steel'",
    )
    node_type = Column(
        String(64),
        nullable=False,
        comment="节点类型，对应 Material / Tool / Feature / Process 等实体类别",
    )
    properties = Column(
        # PostgreSQL 使用 JSONB，SQLite 测试环境回退为 JSON，行为兼容。
        # 默认值由 ORM 层 ``default=dict`` 注入，不写 server_default 以兼容 SQLite。
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="节点属性数据（name / category / 业务字段等），JSONB 类型",
    )
    created_at = Column(
        String(32),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="记录入库时间（ISO8601 字符串，跨库兼容）",
    )
    updated_at = Column(
        String(32),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="记录最后更新时间（ISO8601 字符串，跨库兼容）",
    )

    __table_args__ = (Index("ix_kg_nodes_node_type", "node_type"),)

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典。"""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "properties": self.properties or {},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def __repr__(self) -> str:
        return f"<KGNode(node_id={self.node_id}, node_type={self.node_type})>"


# 关系表（kg_edges）


class KGEdge(Base):  # type: ignore[valid-type, misc]  # declarative_base() 返回值 mypy 不识别为类型
    """知识图谱关系表。

    存储 (source_id) -[edge_type]-> (target_id) 三元组及其属性（如
    ``confidence`` / ``source`` / ``evidence`` 等）。
    """

    __tablename__ = "kg_edges"

    edge_id = Column(
        String(64),
        primary_key=True,
        comment="关系主键 ID（kgedge_ 前缀 + uuid4 hex）",
    )
    source_id = Column(
        String(128),
        ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
        comment="关系起始节点 ID（对应 kg_nodes.node_id）",
    )
    target_id = Column(
        String(128),
        ForeignKey("kg_nodes.node_id", ondelete="CASCADE"),
        nullable=False,
        comment="关系目标节点 ID（对应 kg_nodes.node_id）",
    )
    edge_type = Column(
        String(64),
        nullable=False,
        comment="关系类型（如 SUITABLE_FOR / APPLIED_TO / USED 等）",
    )
    confidence = Column(
        Float,
        nullable=False,
        default=0.5,
        comment="关系可信度，取值 [0, 1]，由 Pydantic 层保证范围",
    )
    properties = Column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="关系附加属性（source / evidence 等），JSONB 类型",
    )
    created_at = Column(
        String(32),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="记录入库时间（ISO8601 字符串）",
    )

    # 关系方向由 source_id / target_id 显式表达，使用 relationship
    # 仅为 ORM 便利（不在查询中触发 lazy load）。
    source_node = relationship(
        "KGNode",
        foreign_keys=[source_id],
        lazy="noload",
    )
    target_node = relationship(
        "KGNode",
        foreign_keys=[target_id],
        lazy="noload",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "target_id",
            "edge_type",
            name="uq_kg_edges_source_target_type",
        ),
        Index("ix_kg_edges_edge_type", "edge_type"),
        Index("ix_kg_edges_source_id", "source_id"),
        Index("ix_kg_edges_target_id", "target_id"),
        Index("ix_kg_edges_confidence", "confidence"),
    )

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典。"""
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
            "properties": self.properties or {},
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return (
            f"<KGEdge(edge_id={self.edge_id}, "
            f"{self.source_id} -[{self.edge_type}]-> {self.target_id}, "
            f"confidence={self.confidence})>"
        )


__all__ = ["Base", "KGNode", "KGEdge"]
