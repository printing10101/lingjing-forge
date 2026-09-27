"""解释记录数据库仓储.

从原 ``explainability_service.py`` 拆分。封装 ``explanation_records`` 与
``explanation_comparisons`` 两张 ORM 表的 CRUD 操作，所有方法均为 async。

设计原则
--------
- 仓储只负责 DB 操作，不涉及 payload 文件 IO（由 ``PayloadStore`` 负责）
- ``session_factory`` 由调用方传入（通常是 ``BaseSingletonService._get_session``）
- 写操作通过 SQLAlchemy 事务保证原子性，显式 commit()
- 查询失败统一映射为 ``ExplanationLookupError`` / ``ProjectionError``
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, cast
from collections.abc import Callable, Coroutine

from sqlalchemy import desc, func, select

from app.contracts.explainability import (
    ExplanationLookupError,
    ExplanationRecord,
    ExplanationType,
    ExplanationValidationError,
    ProjectionError,
)
from app.database.models.explainability import (
    ExplanationComparison as ExplanationComparisonORM,
    ExplanationRecord as ExplanationRecordORM,
    _gen_comparison_id,
    _gen_explanation_id,
)
from app.utils.time import utcnow

logger = logging.getLogger(__name__)

# Session 工厂类型：调用返回 AsyncSession
SessionFactory = Callable[[], Coroutine[Any, Any, Any]]


class ExplanationRecordRepo:
    """解释记录 ORM 仓储（async DB）.

    Parameters
    ----------
    session_factory : SessionFactory
        返回 ``AsyncSession`` 的可调用对象（通常是
        ``BaseSingletonService._get_session``）。
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def create_record(
        self,
        *,
        explanation_type: str,
        model_uri: str,
        source_snapshot_id: str | None,
        input_signature: str,
        payload_path: str,
        payload_size_bytes: int,
        metadata: dict[str, Any],
        created_by: str | None,
    ) -> ExplanationRecord:
        """写入解释记录到数据库."""
        record_orm = ExplanationRecordORM(
            id=_gen_explanation_id(),
            explanation_type=explanation_type,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            input_signature=input_signature,
            payload_path=payload_path,
            payload_size_bytes=payload_size_bytes,
            metadata_json=json.dumps(metadata, ensure_ascii=False, default=str),
            created_by=created_by,
            created_at=utcnow(),
        )
        session = await self._session_factory()
        try:
            async with session.begin():
                session.add(record_orm)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise ProjectionError(f"写入解释记录失败: {exc}") from exc
        finally:
            await session.close()

        return ExplanationRecord(
            id=str(record_orm.id),
            explanation_type=str(record_orm.explanation_type),
            model_uri=str(record_orm.model_uri),
            source_snapshot_id=str(record_orm.source_snapshot_id) if record_orm.source_snapshot_id else None,
            input_signature=str(record_orm.input_signature),
            payload_path=str(record_orm.payload_path),
            payload_size_bytes=int(record_orm.payload_size_bytes),
            metadata_json=metadata,
            created_by=str(record_orm.created_by) if record_orm.created_by else None,
            created_at=cast(datetime, record_orm.created_at),  # ORM nullable=False
            expires_at=cast(datetime, record_orm.expires_at) if record_orm.expires_at else None,
        )

    async def find_record_orm(self, explanation_id: str) -> ExplanationRecordORM:
        """查询解释记录 ORM（不存在抛 ExplanationLookupError）."""
        session = await self._session_factory()
        try:
            async with session.begin():
                stmt = select(ExplanationRecordORM).where(ExplanationRecordORM.id == explanation_id)
                result = await session.execute(stmt)
                record_orm = result.scalar_one_or_none()
            if record_orm is None:
                raise ExplanationLookupError(explanation_id)
            return record_orm
        finally:
            await session.close()

    async def list_records(
        self,
        *,
        explanation_type: str | None = None,
        model_uri: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExplanationRecord], int]:
        """列出历史解释记录.

        Raises
        ------
        ExplanationValidationError
            limit / offset / explanation_type 不合法。
        """
        if limit < 1 or limit > 500:
            raise ExplanationValidationError(f"limit 必须在 [1, 500]，当前: {limit}")
        if offset < 0:
            raise ExplanationValidationError(f"offset 必须 >= 0，当前: {offset}")

        session = await self._session_factory()
        try:
            async with session.begin():
                # 构造查询条件
                conditions = []
                if explanation_type:
                    if not ExplanationType.is_valid(explanation_type):
                        raise ExplanationValidationError(f"explanation_type 不合法: {explanation_type}")
                    conditions.append(ExplanationRecordORM.explanation_type == explanation_type)
                if model_uri:
                    conditions.append(ExplanationRecordORM.model_uri == model_uri)

                # 总数查询
                count_stmt = select(func.count()).select_from(ExplanationRecordORM)
                for cond in conditions:
                    count_stmt = count_stmt.where(cond)
                total = (await session.execute(count_stmt)).scalar_one()

                # 分页查询
                list_stmt = (
                    select(ExplanationRecordORM)
                    .order_by(desc(ExplanationRecordORM.created_at))
                    .offset(offset)
                    .limit(limit)
                )
                for cond in conditions:
                    list_stmt = list_stmt.where(cond)
                records = (await session.execute(list_stmt)).scalars().all()
            records_list = [
                ExplanationRecord(
                    id=r.id,
                    explanation_type=r.explanation_type,
                    model_uri=r.model_uri,
                    source_snapshot_id=r.source_snapshot_id,
                    input_signature=r.input_signature,
                    payload_path=r.payload_path,
                    payload_size_bytes=r.payload_size_bytes,
                    metadata_json=json.loads(r.metadata_json) if r.metadata_json else {},
                    created_by=r.created_by,
                    created_at=r.created_at,
                    expires_at=r.expires_at,
                )
                for r in records
            ]
            return records_list, int(total)
        finally:
            await session.close()

    async def delete_record(self, record_orm: ExplanationRecordORM) -> None:
        """删除解释记录 ORM（不删除 payload 文件）."""
        session = await self._session_factory()
        try:
            async with session.begin():
                await session.delete(record_orm)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise ProjectionError(f"删除解释记录失败: {exc}") from exc
        finally:
            await session.close()

    async def create_comparison(
        self,
        *,
        base_explanation_id: str,
        compared_explanation_id: str,
        comparison_type: str,
        diff_payload_path: str,
        created_by: str | None,
    ) -> ExplanationComparisonORM:
        """写入对比记录到数据库."""
        comparison_orm = ExplanationComparisonORM(
            id=_gen_comparison_id(),
            base_explanation_id=base_explanation_id,
            compared_explanation_id=compared_explanation_id,
            comparison_type=comparison_type,
            diff_payload_path=diff_payload_path,
            created_by=created_by,
            created_at=utcnow(),
        )
        session = await self._session_factory()
        try:
            async with session.begin():
                session.add(comparison_orm)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise ProjectionError(f"写入对比记录失败: {exc}") from exc
        finally:
            await session.close()
        return comparison_orm


__all__ = ["ExplanationRecordRepo", "SessionFactory"]
