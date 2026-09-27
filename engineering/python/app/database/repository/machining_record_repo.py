"""MachiningRecord Repository —— 同步 CRUD 封装。

设计要点：
    - 同步 API：与任务 M0.4 验收脚本（``repo.create(...)`` /
      ``repo.get(...)`` / ``repo.update(...)`` / ``repo.delete(...)``）
      的同步调用风格保持一致，便于 pytest 直接运行。
    - 依赖反转：可通过 ``session_factory`` 注入外部 sessionmaker
      （如 FastAPI ``Depends(get_db)``），未注入时使用本模块内部
      维护的懒加载全局 sessionmaker。
    - 字段映射：Pydantic 模型与 ORM 模型解耦，仓储层负责字段装配
      与 ``record_id`` / ``created_at`` / ``updated_at`` 等自动列处理。
    - 异常语义：未找到记录返回 ``None``，依赖完整性冲突抛回原始异常，
      便于上层根据业务需求捕获。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable, Sequence

from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models.machining_record import (
    MachiningRecord as MachiningRecordORM,
    _new_record_id,
)
from app.database.sync_session import get_sync_sessionmaker
from app.models.machining_record import (
    MachiningRecordCreate,
    MachiningRecordRead,
    MachiningRecordUpdate,
)

logger = logging.getLogger(__name__)


# 同步引擎与 sessionmaker 统一由 app.database.sync_session 提供，
# 避免与 knowledge_graph/repository.py 重复定义 _SyncSingletons。
# ``get_sync_sessionmaker`` 已通过 import 暴露在本模块命名空间，
# 外部代码 ``from app.database.repository.machining_record_repo import
# get_sync_sessionmaker`` 仍然可用（向后兼容）。


# Repository


SessionFactory = Callable[[], "Session"]


def _to_orm_dict(data: MachiningRecordCreate) -> dict[str, Any]:
    """将 Pydantic 创建模型转 ORM 字段字典。"""
    payload: dict[str, Any] = {
        "machine_id": data.machine_id,
        "tool_id": data.tool_id,
        "material": data.material,
        "timestamp": data.timestamp,
        "spindle_speed": data.spindle_speed,
        "feed_rate": data.feed_rate,
        "tdengine_series_id": data.tdengine_series_id,
        "process_params": dict(data.process_params or {}),
    }
    if data.record_id:
        payload["record_id"] = data.record_id
    return payload


def _apply_update(orm_obj: MachiningRecordORM, patch: MachiningRecordUpdate) -> bool:
    """将 Update 模型中的非 None 字段应用到 ORM 实例。"""
    changed = False
    payload = patch.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if value is None:
            continue
        if getattr(orm_obj, key) != value:
            setattr(orm_obj, key, value)
            changed = True
    return changed


def _orm_to_read(orm_obj: MachiningRecordORM) -> MachiningRecordRead:
    """ORM -> Pydantic Read 转换。"""
    return MachiningRecordRead.model_validate(orm_obj)


class MachiningRecordRepository:
    """MachiningRecord 同步仓储。

    用法::

        repo = MachiningRecordRepository()
        record_id = repo.create(MachiningRecordCreate(...))
        record = repo.get(record_id)
        ok = repo.update(record_id, MachiningRecordUpdate(spindle_speed=5000.0))
        ok = repo.delete(record_id)

    也支持 FastAPI 风格依赖注入::

        def get_repo() -> MachiningRecordRepository:
            return MachiningRecordRepository(session_factory=lambda: Session(...))
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        self._session_factory: SessionFactory | None = session_factory

    # ------------------------------------------------------------------ utils

    def _session(self) -> "Session":
        """获取一个新 Session。"""
        if self._session_factory is not None:
            return self._session_factory()
        factory = get_sync_sessionmaker()
        if factory is None:
            raise RuntimeError("Database not configured: set DB_URL or inject session_factory")
        return factory()

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    # ----------------------------------------------------------------- create

    def create(self, data: MachiningRecordCreate) -> str:
        """创建一条 MachiningRecord，返回 ``record_id``。"""
        payload = _to_orm_dict(data)
        if "record_id" not in payload:
            payload["record_id"] = _new_record_id()

        orm_obj = MachiningRecordORM(**payload)
        with self._session() as session:
            try:
                session.add(orm_obj)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.warning("Database error on create MachiningRecord: %s", exc)
                raise
            session.refresh(orm_obj)
        return str(orm_obj.record_id)

    # -------------------------------------------------------------------- get

    def get(self, record_id: str) -> MachiningRecordRead | None:
        """按主键查询；未找到返回 ``None``。"""
        with self._session() as session:
            orm_obj = session.get(MachiningRecordORM, record_id)
            if orm_obj is None:
                return None
            session.expunge(orm_obj)
            return _orm_to_read(orm_obj)

    def get_by_triple(self, machine_id: str, tool_id: str, timestamp: datetime) -> MachiningRecordRead | None:
        """按业务唯一键 ``(machine_id, tool_id, timestamp)`` 查询。"""
        with self._session() as session:
            stmt = select(MachiningRecordORM).where(
                and_(
                    MachiningRecordORM.machine_id == machine_id,
                    MachiningRecordORM.tool_id == tool_id,
                    MachiningRecordORM.timestamp == timestamp,
                )
            )
            orm_obj = session.execute(stmt).scalar_one_or_none()
            if orm_obj is None:
                return None
            session.expunge(orm_obj)
            return _orm_to_read(orm_obj)

    # ------------------------------------------------------------------ list

    def list_by_machine(
        self,
        machine_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MachiningRecordRead]:
        """按机床 ID 查询最近的记录，按时间倒序。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = (
                select(MachiningRecordORM)
                .where(MachiningRecordORM.machine_id == machine_id)
                .order_by(MachiningRecordORM.timestamp.desc())
                .limit(limit)
                .offset(max(offset, 0))
            )
            orm_objs: Sequence[MachiningRecordORM] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return [_orm_to_read(o) for o in orm_objs]

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[MachiningRecordRead]:
        """全表分页查询。"""
        if limit <= 0:
            return []
        with self._session() as session:
            stmt = (
                select(MachiningRecordORM)
                .order_by(MachiningRecordORM.timestamp.desc())
                .limit(limit)
                .offset(max(offset, 0))
            )
            orm_objs: Sequence[MachiningRecordORM] = session.execute(stmt).scalars().all()
            for obj in orm_objs:
                session.expunge(obj)
            return [_orm_to_read(o) for o in orm_objs]

    # ----------------------------------------------------------------- update

    def update(self, record_id: str, patch: MachiningRecordUpdate) -> MachiningRecordRead | None:
        """局部更新；返回更新后的 Read，未找到则返回 ``None``。"""
        with self._session() as session:
            orm_obj = session.get(MachiningRecordORM, record_id)
            if orm_obj is None:
                return None
            changed = _apply_update(orm_obj, patch)
            if not changed:
                session.expunge(orm_obj)
                return _orm_to_read(orm_obj)
            try:
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.warning("Database error on update MachiningRecord: %s", exc)
                raise
            session.refresh(orm_obj)
            session.expunge(orm_obj)
            return _orm_to_read(orm_obj)

    # ----------------------------------------------------------------- delete

    def delete(self, record_id: str) -> bool:
        """按主键删除；返回是否真的删除了一行。"""
        with self._session() as session:
            orm_obj = session.get(MachiningRecordORM, record_id)
            if orm_obj is None:
                return False
            try:
                session.delete(orm_obj)
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.warning("Database error on delete MachiningRecord: %s", exc)
                raise
            return True

    # --------------------------------------------------------------- counting

    def count(self) -> int:
        """返回总行数。"""
        with self._session() as session:
            from sqlalchemy import func

            stmt = select(func.count()).select_from(MachiningRecordORM)
            return int(session.execute(stmt).scalar_one())


__all__ = [
    "MachiningRecordRepository",
    "get_sync_sessionmaker",
    "SessionFactory",
]
