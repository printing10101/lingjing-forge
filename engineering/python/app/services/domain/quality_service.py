"""质量检验 Service 层。

封装质量检验记录与异常的业务逻辑及数据库操作，供
``app.api.v1.quality`` 路由调用。
所有函数返回原始数据（dict / None），不构造 HTTP 响应。
"""

from __future__ import annotations

import logging
from datetime import datetime, date


from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_sessionmaker
from app.database.models import QualityRecord, QualityAnomaly

logger = logging.getLogger(__name__)


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


async def list_quality_records(
    inspection_type: str | None = None,
    result: str | None = None,
    dt_from: datetime | None = None,
    dt_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取质量检验记录列表，支持按类型、结果、日期范围筛选。

    Args:
        dt_from: 起始 datetime（已由路由层解析）
        dt_to: 结束 datetime（已由路由层解析，含当日 23:59:59）

    Returns:
        {"records": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(QualityRecord).order_by(QualityRecord.created_at.desc())
        if inspection_type:
            stmt = stmt.where(QualityRecord.inspection_type == inspection_type)
        if result:
            stmt = stmt.where(QualityRecord.result == result)
        if dt_from:
            stmt = stmt.where(QualityRecord.created_at >= dt_from)
        if dt_to:
            stmt = stmt.where(QualityRecord.created_at <= dt_to)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "records": [r.to_dict() for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def create_quality_record(
    batch_no: str,
    inspection_type: str,
    result: str,
    inspector: str,
    notes: str | None = None,
) -> dict:
    """创建质量检验记录。

    Returns:
        新建记录 dict。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            # 生成检验编号 - 使用微秒时间戳避免并发冲突
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
            inspection_no = f"QC-{timestamp}"

            record = QualityRecord(
                inspection_no=inspection_no,
                batch_no=batch_no,
                inspection_type=inspection_type,
                result=result,
                inspector=inspector,
                notes=notes,
            )
            session.add(record)
            await session.commit()
            return record.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("创建质量检验记录失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("创建质量检验记录失败: %s", e, exc_info=True)
            raise


async def get_quality_record(record_id: str) -> dict | None:
    """按 ID 获取质量检验记录详情。

    Args:
        record_id: 记录主键 ID

    Returns:
        记录 dict，不存在时返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        record = await session.get(QualityRecord, record_id)
        return record.to_dict() if record else None


async def get_quality_stats() -> dict:
    """获取质量统计：今日检验数、合格率、异常数、异常类型分布。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        today_start = datetime.combine(date.today(), datetime.min.time())

        # 今日检验数
        today_count_stmt = (
            select(func.count()).select_from(QualityRecord).where(QualityRecord.created_at >= today_start)
        )
        today_count = (await session.execute(today_count_stmt)).scalar() or 0

        # 合格率
        total_stmt = select(func.count()).select_from(QualityRecord)
        total = (await session.execute(total_stmt)).scalar() or 0
        pass_stmt = select(func.count()).select_from(QualityRecord).where(QualityRecord.result == "合格")
        pass_count = (await session.execute(pass_stmt)).scalar() or 0
        pass_rate = round(pass_count / total * 100, 1) if total > 0 else 0.0

        # 异常数
        anomaly_count_stmt = select(func.count()).select_from(QualityAnomaly)
        anomaly_count = (await session.execute(anomaly_count_stmt)).scalar() or 0

        # 异常类型分布
        dist_stmt = select(QualityAnomaly.anomaly_type, func.count().label("cnt")).group_by(QualityAnomaly.anomaly_type)
        dist_rows = (await session.execute(dist_stmt)).all()
        anomaly_distribution = {row.anomaly_type: row.cnt for row in dist_rows}

    return {
        "today_count": today_count,
        "pass_rate": pass_rate,
        "anomaly_count": anomaly_count,
        "anomaly_distribution": anomaly_distribution,
    }


async def list_anomalies(
    anomaly_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取质量异常列表。

    Returns:
        {"anomalies": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(QualityAnomaly).order_by(QualityAnomaly.created_at.desc())
        if anomaly_type:
            stmt = stmt.where(QualityAnomaly.anomaly_type == anomaly_type)
        if status:
            stmt = stmt.where(QualityAnomaly.status == status)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "anomalies": [a.to_dict() for a in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def seed_quality_data() -> dict:
    """填充质量检验演示数据。

    Returns:
        {"already_exists": bool, "records_count": int, "anomalies_count": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        # 检查是否已有数据
        existing = (await session.execute(select(func.count()).select_from(QualityRecord))).scalar()
        if existing and existing > 0:
            return {"already_exists": True, "records_count": 0, "anomalies_count": 0}

        # 10 条质量记录
        records_data = [
            {
                "inspection_no": "QC-20260623-001",
                "batch_no": "B20260623-A01",
                "inspection_type": "进料检验",
                "result": "合格",
                "inspector": "张伟",
                "notes": "原材料检验合格",
            },
            {
                "inspection_no": "QC-20260623-002",
                "batch_no": "B20260623-A02",
                "inspection_type": "过程检验",
                "result": "合格",
                "inspector": "李明",
                "notes": "加工过程参数正常",
            },
            {
                "inspection_no": "QC-20260623-003",
                "batch_no": "B20260623-B01",
                "inspection_type": "成品检验",
                "result": "不合格",
                "inspector": "王芳",
                "notes": "表面粗糙度超标",
            },
            {
                "inspection_no": "QC-20260623-004",
                "batch_no": "B20260623-A03",
                "inspection_type": "进料检验",
                "result": "合格",
                "inspector": "张伟",
                "notes": "材料成分符合标准",
            },
            {
                "inspection_no": "QC-20260623-005",
                "batch_no": "B20260623-C01",
                "inspection_type": "过程检验",
                "result": "待判定",
                "inspector": "赵强",
                "notes": "需进一步检测",
            },
            {
                "inspection_no": "QC-20260623-006",
                "batch_no": "B20260623-B02",
                "inspection_type": "成品检验",
                "result": "合格",
                "inspector": "李明",
                "notes": "成品各项指标合格",
            },
            {
                "inspection_no": "QC-20260623-007",
                "batch_no": "B20260623-A04",
                "inspection_type": "进料检验",
                "result": "不合格",
                "inspector": "王芳",
                "notes": "材料硬度不达标",
            },
            {
                "inspection_no": "QC-20260623-008",
                "batch_no": "B20260623-D01",
                "inspection_type": "过程检验",
                "result": "合格",
                "inspector": "张伟",
                "notes": "加工精度满足要求",
            },
            {
                "inspection_no": "QC-20260623-009",
                "batch_no": "B20260623-C02",
                "inspection_type": "成品检验",
                "result": "合格",
                "inspector": "赵强",
                "notes": "成品外观及尺寸合格",
            },
            {
                "inspection_no": "QC-20260623-010",
                "batch_no": "B20260623-B03",
                "inspection_type": "过程检验",
                "result": "合格",
                "inspector": "李明",
                "notes": "工序参数稳定",
            },
        ]

        record_map = {}  # inspection_no -> id
        try:
            for rd in records_data:
                rec = QualityRecord(**rd)
                session.add(rec)
                await session.flush()
                record_map[rd["inspection_no"]] = rec.id

            # 8 条异常记录: 3 尺寸偏差, 2 表面缺陷, 2 材料问题, 1 其他
            anomalies_data = [
                {
                    "record_id": record_map["QC-20260623-003"],
                    "anomaly_type": "表面缺陷",
                    "description": "成品表面粗糙度Ra值超标",
                    "severity": "一般",
                    "status": "处理中",
                },
                {
                    "record_id": record_map["QC-20260623-005"],
                    "anomaly_type": "尺寸偏差",
                    "description": "轴径尺寸偏大0.02mm",
                    "severity": "严重",
                    "status": "待处理",
                },
                {
                    "record_id": record_map["QC-20260623-007"],
                    "anomaly_type": "材料问题",
                    "description": "材料硬度低于标准值HRC58",
                    "severity": "严重",
                    "status": "待处理",
                },
                {
                    "record_id": record_map["QC-20260623-003"],
                    "anomaly_type": "尺寸偏差",
                    "description": "孔径公差超出允许范围",
                    "severity": "一般",
                    "status": "已解决",
                },
                {
                    "record_id": record_map["QC-20260623-005"],
                    "anomaly_type": "尺寸偏差",
                    "description": "长度方向尺寸偏差0.05mm",
                    "severity": "一般",
                    "status": "处理中",
                },
                {
                    "record_id": record_map["QC-20260623-007"],
                    "anomaly_type": "材料问题",
                    "description": "材料成分中Cr含量偏低",
                    "severity": "严重",
                    "status": "待处理",
                },
                {
                    "record_id": record_map["QC-20260623-003"],
                    "anomaly_type": "表面缺陷",
                    "description": "表面有明显划痕",
                    "severity": "一般",
                    "status": "已解决",
                },
                {
                    "record_id": record_map["QC-20260623-005"],
                    "anomaly_type": "其他",
                    "description": "标签信息不完整",
                    "severity": "轻微",
                    "status": "已解决",
                },
            ]

            for ad in anomalies_data:
                session.add(QualityAnomaly(**ad))

            await session.commit()
            return {
                "already_exists": False,
                "records_count": len(records_data),
                "anomalies_count": len(anomalies_data),
            }
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充质量检验演示数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充质量检验演示数据失败: %s", e, exc_info=True)
            raise
