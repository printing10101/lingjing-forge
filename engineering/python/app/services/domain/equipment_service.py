"""设备监控 Service 层。

封装设备 / 告警 / 维护计划的业务逻辑与数据库操作，供
``app.api.v1.equipment`` 路由调用。所有函数返回原始数据（dict / None），
不构造 HTTP 响应。业务错误通过 ``ValueError`` 表达，未找到返回 ``None``。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone


from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_engine, get_sessionmaker
from app.database.models import Base, Equipment, EquipmentAlarm, MaintenancePlan

logger = logging.getLogger(__name__)


# 允许更新的设备字段白名单
_EQUIPMENT_ALLOWED_FIELDS = {"status", "temperature", "vibration", "rpm", "power"}

# 允许更新的维护计划字段白名单
_MAINTENANCE_ALLOWED_FIELDS = {
    "title",
    "type",
    "frequency",
    "last_date",
    "next_date",
    "status",
}

# 合法的告警状态
_ALARM_VALID_STATUSES = ["未处理", "已确认", "已解决"]


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


async def _ensure_tables():
    """创建设备相关表（若不存在）。"""
    engine = get_engine()
    if engine is None:
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def list_equipment(
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """返回设备列表（分页 + 可选状态过滤）。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        base = select(Equipment)
        if status:
            base = base.where(Equipment.status == status)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = base.order_by(Equipment.created_at).limit(page_size).offset(offset)
        items = (await session.execute(stmt)).scalars().all()

    return {
        "items": [e.to_dict() for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


async def get_equipment_stats() -> dict:
    """返回设备状态聚合统计（单次聚合查询）。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(
            func.count(Equipment.id).label("total"),
            func.sum(case((Equipment.status == "运行中", 1), else_=0)).label("running"),
            func.sum(case((Equipment.status == "待机", 1), else_=0)).label("standby"),
            func.sum(case((Equipment.status == "维护中", 1), else_=0)).label("maintenance"),
            func.sum(case((Equipment.status == "故障", 1), else_=0)).label("fault"),
        )
        row = (await session.execute(stmt)).one()

    return {
        "total": row.total or 0,
        "running": int(row.running or 0),
        "standby": int(row.standby or 0),
        "maintenance": int(row.maintenance or 0),
        "fault": int(row.fault or 0),
    }


async def get_equipment(equipment_id: str) -> dict | None:
    """获取单台设备详情，未找到返回 None。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        equip = (await session.execute(select(Equipment).where(Equipment.id == equipment_id))).scalar_one_or_none()
        if not equip:
            return None
        return equip.to_dict()


async def update_equipment(equipment_id: str, body: dict) -> dict | None:
    """更新设备状态和指标。

    Returns:
        更新后的设备 dict；若设备未找到返回 None；若没有有效更新字段
        抛出 ``ValueError``。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            equip = (await session.execute(select(Equipment).where(Equipment.id == equipment_id))).scalar_one_or_none()
            if not equip:
                return None

            updated = []
            for key, value in body.items():
                if key in _EQUIPMENT_ALLOWED_FIELDS:
                    setattr(equip, key, value)
                    updated.append(key)

            if not updated:
                raise ValueError("没有有效的更新字段")

            equip.updated_at = datetime.now(timezone.utc)
            await session.commit()

            return equip.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新设备失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新设备失败: %s", e, exc_info=True)
            raise


async def list_alarms(
    equipment_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """返回告警列表（分页 + 多条件过滤）。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        base = select(EquipmentAlarm)
        if equipment_id:
            base = base.where(EquipmentAlarm.equipment_id == equipment_id)
        if status:
            base = base.where(EquipmentAlarm.status == status)
        if severity:
            base = base.where(EquipmentAlarm.severity == severity)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = base.order_by(EquipmentAlarm.created_at.desc()).limit(page_size).offset(offset)
        items = (await session.execute(stmt)).scalars().all()

    return {
        "items": [a.to_dict() for a in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


async def update_alarm_status(alarm_id: str, body: dict) -> dict | None:
    """更新告警状态。

    Returns:
        更新后的告警 dict；告警未找到返回 None；状态非法抛出 ``ValueError``。

    Note:
        与原路由行为一致：先检查告警存在性，再校验状态合法性。
        若告警不存在且状态非法，返回 None（NOT_FOUND 优先）。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            alarm = (
                await session.execute(select(EquipmentAlarm).where(EquipmentAlarm.id == alarm_id))
            ).scalar_one_or_none()
            if not alarm:
                return None

            new_status = body.get("status")
            if not new_status or new_status not in _ALARM_VALID_STATUSES:
                raise ValueError(f"无效状态，可选值: {_ALARM_VALID_STATUSES}")

            alarm.status = new_status
            await session.commit()

            return alarm.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新告警状态失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新告警状态失败: %s", e, exc_info=True)
            raise


async def list_maintenance_plans(
    equipment_id: str | None = None,
    status: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """返回维护计划列表（分页 + 过滤）。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        base = select(MaintenancePlan)
        if equipment_id:
            base = base.where(MaintenancePlan.equipment_id == equipment_id)
        if status:
            base = base.where(MaintenancePlan.status == status)

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = base.order_by(MaintenancePlan.created_at).limit(page_size).offset(offset)
        items = (await session.execute(stmt)).scalars().all()

    return {
        "items": [p.to_dict() for p in items],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


async def update_maintenance_plan(plan_id: str, body: dict) -> dict | None:
    """更新维护计划。

    Returns:
        更新后的计划 dict；计划未找到返回 None；无有效字段抛出 ``ValueError``。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            plan = (
                await session.execute(select(MaintenancePlan).where(MaintenancePlan.id == plan_id))
            ).scalar_one_or_none()
            if not plan:
                return None

            updated = []
            for key, value in body.items():
                if key in _MAINTENANCE_ALLOWED_FIELDS:
                    if key in ("last_date", "next_date") and isinstance(value, str):
                        value = datetime.fromisoformat(value)
                    setattr(plan, key, value)
                    updated.append(key)

            if not updated:
                raise ValueError("没有有效的更新字段")

            await session.commit()

            return plan.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新维护计划失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新维护计划失败: %s", e, exc_info=True)
            raise


async def seed_equipment_data() -> dict:
    """填充设备监控演示数据。

    Returns:
        {"already_exists": bool, "counts": {...}}
    """
    sessionmaker = _get_session()
    await _ensure_tables()

    now = datetime.now(timezone.utc)

    equipment_data = [
        {
            "id": str(uuid.uuid4()),
            "name": "CNC-001 五轴加工中心",
            "model": "DMG MORI DMU 50",
            "location": "A区-01号",
            "status": "运行中",
            "temperature": 45.2,
            "vibration": 0.12,
            "rpm": 12000.0,
            "power": 35.5,
        },
        {
            "id": str(uuid.uuid4()),
            "name": "CNC-002 三轴加工中心",
            "model": "FANUC α-D21MiB5",
            "location": "A区-02号",
            "status": "运行中",
            "temperature": 42.8,
            "vibration": 0.08,
            "rpm": 8000.0,
            "power": 28.3,
        },
        {
            "id": str(uuid.uuid4()),
            "name": "WEDM-001 线切割机",
            "model": "SODICK VZ300L",
            "location": "B区-01号",
            "status": "待机",
            "temperature": 25.1,
            "vibration": 0.02,
            "rpm": 0.0,
            "power": 2.1,
        },
        {
            "id": str(uuid.uuid4()),
            "name": "GRIND-001 数控磨床",
            "model": "STUDER S33",
            "location": "B区-02号",
            "status": "运行中",
            "temperature": 38.5,
            "vibration": 0.15,
            "rpm": 4500.0,
            "power": 22.7,
        },
        {
            "id": str(uuid.uuid4()),
            "name": "ROBOT-001 焊接机器人",
            "model": "FANUC R-2000iC",
            "location": "C区-01号",
            "status": "维护中",
            "temperature": 22.3,
            "vibration": 0.01,
            "rpm": 0.0,
            "power": 0.5,
        },
        {
            "id": str(uuid.uuid4()),
            "name": "CMM-001 三坐标测量机",
            "model": "ZEISS CONTURA",
            "location": "D区-01号",
            "status": "运行中",
            "temperature": 23.8,
            "vibration": 0.03,
            "rpm": 0.0,
            "power": 5.2,
        },
    ]

    async with sessionmaker() as session:
        existing = await session.execute(select(func.count(Equipment.id)))
        if existing.scalar() > 0:
            return {"already_exists": True, "counts": {}}

        try:
            # 创建设备
            for ed in equipment_data:
                session.add(Equipment(**ed))
            await session.flush()

            # 创建告警
            alarm_data = [
                {
                    "equipment_id": equipment_data[0]["id"],
                    "alarm_type": "温度异常",
                    "severity": "警告",
                    "message": "CNC-001 主轴温度偏高 (45.2°C)，接近阈值上限",
                    "status": "未处理",
                },
                {
                    "equipment_id": equipment_data[3]["id"],
                    "alarm_type": "振动异常",
                    "severity": "警告",
                    "message": "GRIND-001 振动值偏高 (0.15mm/s)，建议检查砂轮平衡",
                    "status": "未处理",
                },
                {
                    "equipment_id": equipment_data[4]["id"],
                    "alarm_type": "维护提醒",
                    "severity": "提示",
                    "message": "ROBOT-001 焊接机器人已进入计划维护周期",
                    "status": "已确认",
                },
                {
                    "equipment_id": equipment_data[1]["id"],
                    "alarm_type": "功率异常",
                    "severity": "紧急",
                    "message": "CNC-002 功率波动异常，瞬时峰值超过额定功率15%",
                    "status": "未处理",
                },
                {
                    "equipment_id": equipment_data[2]["id"],
                    "alarm_type": "维护提醒",
                    "severity": "提示",
                    "message": "WEDM-001 线切割机电极丝寿命即将到期，建议更换",
                    "status": "已解决",
                },
                {
                    "equipment_id": equipment_data[5]["id"],
                    "alarm_type": "温度异常",
                    "severity": "警告",
                    "message": "CMM-001 测量环境温度波动超出允许范围",
                    "status": "未处理",
                },
            ]

            for ad in alarm_data:
                session.add(EquipmentAlarm(**ad))
            await session.flush()

            # 创建维护计划
            maintenance_data = [
                {
                    "equipment_id": equipment_data[0]["id"],
                    "title": "主轴润滑保养",
                    "type": "定期保养",
                    "frequency": "每周",
                    "last_date": now - timedelta(days=3),
                    "next_date": now + timedelta(days=4),
                    "status": "待执行",
                },
                {
                    "equipment_id": equipment_data[1]["id"],
                    "title": "刀具磨损检查",
                    "type": "预防性维护",
                    "frequency": "每日",
                    "last_date": now - timedelta(hours=8),
                    "next_date": now + timedelta(hours=16),
                    "status": "待执行",
                },
                {
                    "equipment_id": equipment_data[2]["id"],
                    "title": "电极丝更换",
                    "type": "定期保养",
                    "frequency": "每月",
                    "last_date": now - timedelta(days=25),
                    "next_date": now + timedelta(days=5),
                    "status": "待执行",
                },
                {
                    "equipment_id": equipment_data[3]["id"],
                    "title": "砂轮修整与校准",
                    "type": "定期保养",
                    "frequency": "每季度",
                    "last_date": now - timedelta(days=60),
                    "next_date": now + timedelta(days=30),
                    "status": "待执行",
                },
                {
                    "equipment_id": equipment_data[4]["id"],
                    "title": "机械臂关节润滑与校准",
                    "type": "故障维修",
                    "frequency": "每月",
                    "last_date": now - timedelta(days=10),
                    "next_date": now - timedelta(days=3),
                    "status": "进行中",
                },
                {
                    "equipment_id": equipment_data[5]["id"],
                    "title": "测量探头校准",
                    "type": "预防性维护",
                    "frequency": "每周",
                    "last_date": now - timedelta(days=6),
                    "next_date": now + timedelta(days=1),
                    "status": "待执行",
                },
            ]

            for md in maintenance_data:
                session.add(MaintenancePlan(**md))

            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充设备种子数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充设备种子数据失败: %s", e, exc_info=True)
            raise

    return {
        "already_exists": False,
        "counts": {
            "equipment_count": len(equipment_data),
            "alarm_count": len(alarm_data),
            "maintenance_count": len(maintenance_data),
        },
    }
