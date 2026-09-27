"""SQLAlchemy ORM model for unified machining records.

设计原则：
    - 核心字段保持 5-7 个，与 Pydantic 模型对应；
    - 时序数据通过 ``tdengine_series_id`` 字符串引用，原始高频数据
      保留在 TDengine，避免在 PostgreSQL 重复存储；
    - ``process_params`` 使用 PostgreSQL 原生 ``JSONB`` 类型，支持高效
      索引与查询（禁止使用 TEXT）；同时通过 ``with_variant`` 在 SQLite
      环境下回退为 ``JSON``，便于本地单元测试；
    - 在 ``machine_id`` / ``tool_id`` / ``material`` / ``timestamp`` 上
      建立索引以适配典型查询模式；
    - 业务唯一性约束：同一（machine_id, tool_id, timestamp）组合视为
      同一物理加工事件，避免重复入库。

注：本模块使用独立 :class:`declarative_base`，便于在 alembic env.py 中
单独合并元数据，与既有 ``app.database.models.Base`` / ``app.database.
rule_models.Base`` 解耦。
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Column,
    String,
    Float,
    DateTime,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base

from app.utils.id import new_id

Base = declarative_base()


def _new_record_id() -> str:
    """生成 MachiningRecord 主键 ID。"""
    return new_id("mrec")


class MachiningRecord(Base):  # type: ignore[misc, valid-type]
    """统一加工记录 ORM 模型。"""

    __tablename__ = "machining_records"

    record_id = Column(
        String(64),
        primary_key=True,
        default=_new_record_id,
        comment="记录主键 ID（mrec_ 前缀 + UUID4 hex）",
    )
    machine_id = Column(
        String(64),
        nullable=False,
        comment="机床标识，关联 machines.json 中的 machine.id",
    )
    tool_id = Column(
        String(64),
        nullable=False,
        comment="刀具标识，关联 tools.json 中的 tool.id",
    )
    material = Column(
        String(128),
        nullable=False,
        comment="工件材料名称",
    )
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="加工事件发生时间（带时区）",
    )
    spindle_speed = Column(
        Float,
        nullable=False,
        comment="主轴转速，单位 RPM（>=0 物理约束由 Pydantic 层保证）",
    )
    feed_rate = Column(
        Float,
        nullable=False,
        comment="进给速度，单位 mm/min（>=0 物理约束由 Pydantic 层保证）",
    )
    tdengine_series_id = Column(
        String(128),
        nullable=True,
        comment="TDengine 时序数据引用 ID，spindle_actual / feed_actual / vibration 等高频数据存储在 TDengine",
    )
    process_params = Column(
        # 生产环境使用 PostgreSQL JSONB；测试环境（SQLite）回退为 JSON，
        # 行为兼容（都能存储 dict[str, Any]），同时满足"禁止使用 TEXT"约束。
        # 默认值使用 Python 端 ``default=dict`` 而非 ``server_default``，
        # 原因：``'{}'::jsonb`` 是 PostgreSQL 专属语法，SQLite 测试环境无法解析。
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        comment="附加工艺参数（depth_of_cut / coolant / operation 等），使用 PostgreSQL JSONB 类型以支持高效查询与索引",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="记录入库时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="记录最后更新时间（数据库自动维护）",
    )

    __table_args__ = (
        UniqueConstraint(
            "machine_id",
            "tool_id",
            "timestamp",
            name="uq_machining_records_machine_tool_ts",
        ),
        Index("ix_machining_records_machine_id", "machine_id"),
        Index("ix_machining_records_tool_id", "tool_id"),
        Index("ix_machining_records_material", "material"),
        Index("ix_machining_records_timestamp", "timestamp"),
        Index(
            "ix_machining_records_machine_ts",
            "machine_id",
            "timestamp",
        ),
    )

    def to_dict(self) -> dict:
        """序列化为可 JSON 化的字典（用于 API/日志输出）。"""
        return {
            "record_id": self.record_id,
            "machine_id": self.machine_id,
            "tool_id": self.tool_id,
            "material": self.material,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "spindle_speed": self.spindle_speed,
            "feed_rate": self.feed_rate,
            "tdengine_series_id": self.tdengine_series_id,
            "process_params": self.process_params or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<MachiningRecord(record_id={self.record_id}, "
            f"machine_id={self.machine_id}, tool_id={self.tool_id}, "
            f"timestamp={self.timestamp})>"
        )


__all__ = ["Base", "MachiningRecord", "_new_record_id"]
