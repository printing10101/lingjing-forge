"""
SQLAlchemy ORM models for training task persistence and RBAC.

Defines TrainingTask, Role, Permission, and RolePermission models.

本模块为门面：实现已拆分至 _base / _rbac_models / _presets / _seed_rbac。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import (
    Column,
    String,
    Integer,
    DateTime,
    JSON,
    text,
    Index,
)

from app.database.models._base import Base  # noqa: F401
from app.database.models._presets import (  # noqa: F401
    PRESET_PERMISSIONS,
    PRESET_ROLES,
)
from app.database.models._rbac_models import (  # noqa: F401
    Permission,
    Role,
    RolePermission,
)
from app.database.models._seed_rbac import (  # noqa: F401
    _seed_default_admin_user,
    _seed_rbac,
    _upgrade_rbac_permissions,
)

logger = logging.getLogger(__name__)


class TaskStatusEnum(str):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TrainingTask(Base):
    __tablename__ = "training_tasks"

    id = Column(
        String(64),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    task_type = Column(
        String(64),
        nullable=False,
        index=True,
        comment="Task type identifier (lnn_training, lnn_inference, etc.)",
    )
    status = Column(
        String(32),
        nullable=False,
        default=TaskStatusEnum.PENDING,
        index=True,
        comment="Task status: pending/running/completed/failed/cancelled",
    )
    progress = Column(
        Integer,
        nullable=False,
        default=0,
        comment="Progress percentage (0-100)",
    )
    params = Column(
        JSON,
        nullable=True,
        comment="Task parameters as JSON",
    )
    result = Column(
        JSON,
        nullable=True,
        comment="Task result data as JSON",
    )
    error = Column(
        String(2048),
        nullable=True,
        comment="Error message if task failed",
    )
    owner_id = Column(
        String(128),
        nullable=True,
        index=True,
    )
    idempotency_key = Column(
        String(256),
        nullable=True,
        index=True,
        unique=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="Task creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
        comment="Last update timestamp",
    )
    started_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_training_tasks_status_type", "status", "task_type"),
        Index("idx_training_tasks_created_at", "created_at"),
        Index("idx_training_tasks_owner", "owner_id", "created_at"),
    )

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "task_type": self.task_type,
            "status": self.status,
            "progress": self.progress,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "owner_id": self.owner_id,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if self.started_at and self.completed_at:
            d["duration_seconds"] = round((self.completed_at - self.started_at).total_seconds(), 2)
        return d

    def __repr__(self) -> str:
        return f"<TrainingTask(id={self.id}, type={self.task_type}, status={self.status})>"


async def init_db():
    """创建全部 4 套 SQLAlchemy Base 的表，并种子 RBAC 与默认 admin 用户。

    P0-2 修复：原本只创建 training_task 的 Base.metadata，导致 rule_models、
    machining_record、knowledge_graph 三套 Base 的表均不创建，运行时报
    ``no such table``。此处统一导入并 create_all 全部 Base。
    """
    from app.database.connection import get_engine

    engine = get_engine()
    if engine is None:
        return

    # P0-2 修复：显式导入全部 Base 持有者，确保 metadata 包含全部表定义
    # 顺序无关，但 import 触发模块级 declarative_base() 调用

    # 收集全部 Base.metadata
    metadatas = [Base.metadata]
    try:
        from app.database.rule_models import Base as _RuleBase

        metadatas.append(_RuleBase.metadata)
    except ImportError:
        logger.debug("rule_models.Base 未导入，跳过", exc_info=True)
    try:
        from app.database.models.machining_record import Base as _MachiningBase

        metadatas.append(_MachiningBase.metadata)
    except ImportError:
        logger.debug("machining_record.Base 未导入，跳过", exc_info=True)
    try:
        from app.knowledge_graph.models import Base as _KGBase

        metadatas.append(_KGBase.metadata)
    except ImportError:
        logger.debug("knowledge_graph.Base 未导入，跳过", exc_info=True)

    from sqlalchemy.exc import OperationalError

    async with engine.begin() as conn:
        for md in metadatas:
            try:
                await conn.run_sync(md.create_all)
            except OperationalError as e:
                # 并发启动场景：多个 worker 同时 create_all 竞态，
                # 其他 worker 已创建同名表时忽略（视为成功，避免 worker 启动失败）。
                if "already exists" in str(e).lower():
                    logger.debug("[init_db] 表已存在，跳过（并发启动由其他 worker 创建）: %s", e)
                    continue
                raise

    from app.database.connection import get_sessionmaker

    sessionmaker = get_sessionmaker()
    if sessionmaker:
        async with sessionmaker() as session:
            await _seed_rbac(session)
