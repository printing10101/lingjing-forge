"""Cutting Experience 存储仓库层（P2-2，修正版，避免依赖 __init__ 导出）。

实现 `app.contracts.cutting_experience` 契约的 SQLAlchemy 持久化与查询。
遵循现有服务层约定：
- 所有函数返回纯数据结构（dict / list / None），不构造 HTTP 响应
- 使用 `get_sessionmaker()` 获取异步 session
- 数据库未配置时抛 RuntimeError（由上层 API 捕获转 503）

与 `cutting_experience_service.py` 的关系：
本文件是 service 的仓库层形态，直接子模块导入 ORM 模型，不依赖
`app.database.models.__init__` 的导出（该文件因环境锁暂未更新）。
API 层（P2-3）应导入本模块。待锁解除后两个文件可合并或其一废弃。
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select, func

from app.contracts.cutting_experience import (
    CuttingExperience,
    ExperienceQuery,
    ExperienceStats,
    MachiningResult,
)
from app.database.connection import get_sessionmaker
from app.database.models.cutting_experience import CuttingExperienceRecord

logger = logging.getLogger(__name__)


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


# 写入


async def create_cutting_experience(record: CuttingExperience) -> dict:
    """持久化一条切削实测记录。

    Args:
        record: 完整契约对象（由 API 层完成校验后传入）。

    Returns:
        持久化后的记录 dict（含数据库分配的 created_at/updated_at）。

    Raises:
        RuntimeError: 数据库未配置。
    """
    sessionmaker = _get_session()
    model = CuttingExperienceRecord.from_contract(record)
    async with sessionmaker() as session:
        session.add(model)
        await session.commit()
        await session.refresh(model)
        logger.info(
            "cutting_experience created: id=%s machine=%s tool=%s",
            model.id,
            model.machine_id,
            model.tool_id,
        )
        return model.to_contract_dict()


async def create_many_cutting_experiences(records: list[CuttingExperience]) -> int:
    """批量持久化切削实测记录（MTConnect 采集管道批量落库用）。

    Args:
        records: 契约对象列表。

    Returns:
        成功写入条数。
    """
    if not records:
        return 0
    sessionmaker = _get_session()
    models = [CuttingExperienceRecord.from_contract(r) for r in records]
    async with sessionmaker() as session:
        session.add_all(models)
        await session.commit()
        logger.info("cutting_experience batch created: %d records", len(models))
        return len(models)


# 查询


async def list_cutting_experiences(query: ExperienceQuery) -> dict:
    """按条件分页查询切削实测记录。

    Args:
        query: 查询条件（ExperienceQuery 契约）。

    Returns:
        {"records": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    stmt = select(CuttingExperienceRecord).order_by(CuttingExperienceRecord.created_at.desc())
    if query.machine_id:
        stmt = stmt.where(CuttingExperienceRecord.machine_id == query.machine_id)
    if query.tool_id:
        stmt = stmt.where(CuttingExperienceRecord.tool_id == query.tool_id)
    if query.material:
        stmt = stmt.where(CuttingExperienceRecord.material == query.material)
    if query.machining_type:
        stmt = stmt.where(CuttingExperienceRecord.machining_type == query.machining_type.value)
    if query.result:
        stmt = stmt.where(CuttingExperienceRecord.result == query.result.value)
    if query.has_anomaly is not None:
        if query.has_anomaly:
            stmt = stmt.where(CuttingExperienceRecord.anomaly_count > 0)
        else:
            stmt = stmt.where(CuttingExperienceRecord.anomaly_count == 0)
    if query.start_time:
        stmt = stmt.where(CuttingExperienceRecord.created_at >= query.start_time)
    if query.end_time:
        stmt = stmt.where(CuttingExperienceRecord.created_at <= query.end_time)

    count_stmt = select(func.count()).select_from(stmt.subquery())

    async with sessionmaker() as session:
        total = (await session.execute(count_stmt)).scalar_one()
        rows = (await session.execute(stmt.limit(query.limit).offset(query.offset))).scalars().all()
        records = [row.to_contract_dict() for row in rows]
        return {
            "records": records,
            "total": total,
            "limit": query.limit,
            "offset": query.offset,
        }


async def get_cutting_experience(record_id: UUID | str) -> dict | None:
    """按 ID 获取单条记录。

    Args:
        record_id: 记录 ID，支持 UUID 对象、UUID 字符串或 ORM 主键（``exp_`` 前缀）。

    Returns:
        记录 dict；不存在返回 None。
    """
    sessionmaker = _get_session()
    pk = _normalize_pk(record_id)
    async with sessionmaker() as session:
        row = await session.get(CuttingExperienceRecord, pk)
        return row.to_contract_dict() if row else None


async def aggregate_experience_stats(query: ExperienceQuery) -> ExperienceStats:
    """聚合统计（节拍均值/粗糙度均值/合格率/异常率）。

    供前端仪表盘与参数优化模型的数据体检使用。
    """
    sessionmaker = _get_session()
    stmt = select(CuttingExperienceRecord)
    if query.machine_id:
        stmt = stmt.where(CuttingExperienceRecord.machine_id == query.machine_id)
    if query.tool_id:
        stmt = stmt.where(CuttingExperienceRecord.tool_id == query.tool_id)
    if query.start_time:
        stmt = stmt.where(CuttingExperienceRecord.created_at >= query.start_time)
    if query.end_time:
        stmt = stmt.where(CuttingExperienceRecord.created_at <= query.end_time)

    async with sessionmaker() as session:
        rows = (await session.execute(stmt)).scalars().all()

    if not rows:
        return ExperienceStats(total_records=0)

    n = len(rows)
    avg_cycle = sum(r.cycle_time_s for r in rows if r.cycle_time_s is not None) / n
    avg_ra = None
    ra_values = [r.surface_roughness_ra for r in rows if r.surface_roughness_ra is not None]
    if ra_values:
        avg_ra = sum(ra_values) / len(ra_values)
    avg_wear = None
    wear_values = [r.tool_wear_percent for r in rows if r.tool_wear_percent is not None]
    if wear_values:
        avg_wear = sum(wear_values) / len(wear_values)
    ok_count = sum(1 for r in rows if r.result == MachiningResult.OK.value)
    anomaly_count = sum(1 for r in rows if r.anomaly_count and r.anomaly_count > 0)

    return ExperienceStats(
        total_records=n,
        avg_cycle_time_s=avg_cycle,
        avg_surface_roughness_ra=avg_ra,
        avg_tool_wear_percent=avg_wear,
        ok_rate=ok_count / n,
        anomaly_rate=anomaly_count / n,
    )


# 删除


async def delete_cutting_experience(record_id: UUID | str) -> bool:
    """删除一条记录（管理用途，正常飞轮流程不调用）。

    Args:
        record_id: 记录 ID，支持 UUID 对象、UUID 字符串或 ORM 主键（``exp_`` 前缀）。

    Returns:
        True 删除成功；False 记录不存在。
    """
    sessionmaker = _get_session()
    pk = _normalize_pk(record_id)
    async with sessionmaker() as session:
        row = await session.get(CuttingExperienceRecord, pk)
        if row is None:
            return False
        await session.delete(row)
        await session.commit()
        logger.info("cutting_experience deleted: id=%s", record_id)
        return True


def _normalize_pk(record_id: UUID | str) -> str:
    """主键归一化为 ORM 存储形态（``exp_`` 前缀 + UUID hex）。

    与 ``CuttingExperienceRecord._id_or_new`` 保持同一约定：调用方传契约
    UUID（``550e8400-...``）或 ORM 主键（``exp_550e8400...``）都能命中。
    """
    pk = str(record_id)
    if not pk.startswith("exp_"):
        pk = f"exp_{pk.replace('-', '')}"
    return pk
