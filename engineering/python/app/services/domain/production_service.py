"""生产报表 Service 层。

封装生产记录、工单管理及仪表盘的业务逻辑与数据库操作，供
``app.api.v1.production`` 路由调用。
所有函数返回原始数据（dict / None），不构造 HTTP 响应。
"""

from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError

from app.config import config
from app.database.connection import get_sessionmaker
from app.database.models import ProductionRecord, WorkOrder, EquipmentAlarm

logger = logging.getLogger(__name__)


# 产线与班次常量（与原路由保持一致）
LINES = ["产线A", "产线B", "产线C", "产线D", "产线E"]
SHIFTS = ["早班", "中班", "晚班"]

# 演示数据生成参数（P1 学术诚信：仅用于演示，禁止用于生产或学术论文实验）
SEED_DATA_DAYS = 14
SEED_PLANNED_QTY_MIN = 80
SEED_PLANNED_QTY_MAX = 120
SEED_ACTUAL_QTY_RATIO_MIN = 0.85
SEED_QUALIFIED_RATIO_MIN = 0.92
SEED_QUALIFIED_RATIO_MAX = 0.99
SEED_UTILIZATION_MIN = 75.0
SEED_UTILIZATION_MAX = 98.0
SEED_ENERGY_MIN = 200.0
SEED_ENERGY_MAX = 500.0


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


async def get_dashboard() -> dict:
    """仪表盘 KPI：今日产量、良品率、OEE、活跃告警数。

    Returns:
        {"today_output": int, "today_planned": int, "yield_rate": float,
         "oee": float, "equipment_utilization": float, "active_alarms": int}
    """
    sessionmaker = _get_session()
    today = date.today()

    async with sessionmaker() as session:
        # 今日产量
        stmt_today = select(
            func.sum(ProductionRecord.actual_qty),
            func.sum(ProductionRecord.qualified_qty),
            func.sum(ProductionRecord.planned_qty),
            func.avg(ProductionRecord.equipment_utilization),
        ).where(ProductionRecord.date == today)
        row = (await session.execute(stmt_today)).one()

        today_output = int(row[0] or 0)
        today_qualified = int(row[1] or 0)
        today_planned = int(row[2] or 0)
        avg_util = round(float(row[3] or 0), 1)

        yield_rate = round(today_qualified / today_output * 100, 1) if today_output > 0 else 0.0
        oee = round(yield_rate * avg_util / 100, 1) if avg_util > 0 else 0.0

        # 活跃告警数：查询真实未处理告警数量（替代原 random.randint 模拟值）
        try:
            alarm_stmt = select(func.count()).select_from(EquipmentAlarm).where(EquipmentAlarm.status == "未处理")
            active_alarms = (await session.execute(alarm_stmt)).scalar() or 0
        except Exception as e:
            # 表不存在或查询失败时返回 0，避免随机数误导
            logger.warning("查询活跃告警数失败，降级为 0: %s", e, exc_info=True)
            active_alarms = 0

    return {
        "today_output": today_output,
        "today_planned": today_planned,
        "yield_rate": yield_rate,
        "oee": oee,
        "equipment_utilization": avg_util,
        "active_alarms": active_alarms,
    }


async def list_production_records(
    date_from: date | None = None,
    date_to: date | None = None,
    line_name: str | None = None,
    shift: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取生产记录列表，支持按日期范围、产线、班次筛选。

    Returns:
        {"records": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(ProductionRecord).order_by(ProductionRecord.date.desc(), ProductionRecord.line_name)
        if date_from:
            stmt = stmt.where(ProductionRecord.date >= date_from)
        if date_to:
            stmt = stmt.where(ProductionRecord.date <= date_to)
        if line_name:
            stmt = stmt.where(ProductionRecord.line_name == line_name)
        if shift:
            stmt = stmt.where(ProductionRecord.shift == shift)

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


async def list_work_orders(
    status: str | None = None,
    priority: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """获取工单列表，支持按状态、优先级筛选。

    Returns:
        {"work_orders": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(WorkOrder).order_by(WorkOrder.created_at.desc())
        if status:
            stmt = stmt.where(WorkOrder.status == status)
        if priority:
            stmt = stmt.where(WorkOrder.priority == priority)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "work_orders": [w.to_dict() for w in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_work_order(wo_id: str) -> dict | None:
    """获取单个工单详情。

    Returns:
        工单 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
        row = (await session.execute(stmt)).scalar_one_or_none()

    if not row:
        return None
    return row.to_dict()


async def update_work_order(wo_id: str, update_data: dict[str, Any]) -> dict[str, Any] | None:
    """更新工单信息。

    Args:
        wo_id: 工单 ID
        update_data: 待更新字段 dict（已 exclude_unset）

    Returns:
        更新后的工单 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            stmt = select(WorkOrder).where(WorkOrder.id == wo_id)
            row = (await session.execute(stmt)).scalar_one_or_none()
            if not row:
                return None

            for key, value in update_data.items():
                setattr(row, key, value)

            await session.commit()
            return row.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新工单失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新工单失败: %s", e, exc_info=True)
            raise


async def get_production_lines() -> dict:
    """获取产线列表及各班次数据。

    Returns:
        {"lines": [{"line_name": str, "shifts": {...}}, ...]}
    """
    sessionmaker = _get_session()
    today = date.today()

    async with sessionmaker() as session:
        # 单次查询获取今天所有产线的记录，替代 N 次（LINES 长度）独立查询。
        # 将 N+1 查询模式降为 1 次查询 + 内存分组。
        stmt = select(ProductionRecord).where(
            ProductionRecord.date == today,
            ProductionRecord.line_name.in_(LINES),
        )
        all_rows = (await session.execute(stmt)).scalars().all()

    # 按产线名称分组
    rows_by_line: dict[str, list] = {}
    for r in all_rows:
        rows_by_line.setdefault(r.line_name, []).append(r)

    lines_data = []
    for line in LINES:
        rows = rows_by_line.get(line, [])
        shifts = {}
        for r in rows:
            shifts[r.shift] = {
                "planned_qty": r.planned_qty,
                "actual_qty": r.actual_qty,
                "qualified_qty": r.qualified_qty,
                "defect_qty": r.defect_qty,
                "equipment_utilization": r.equipment_utilization,
                "energy_consumption": r.energy_consumption,
            }

        lines_data.append(
            {
                "line_name": line,
                "shifts": shifts,
            }
        )

    return {"lines": lines_data}


async def get_monthly_summary() -> dict:
    """月度汇总 KPI。"""
    sessionmaker = _get_session()
    today = date.today()
    month_start = today.replace(day=1)

    async with sessionmaker() as session:
        stmt = select(
            func.sum(ProductionRecord.actual_qty),
            func.sum(ProductionRecord.qualified_qty),
            func.sum(ProductionRecord.planned_qty),
            func.sum(ProductionRecord.defect_qty),
            func.avg(ProductionRecord.equipment_utilization),
            func.sum(ProductionRecord.energy_consumption),
            func.count(ProductionRecord.id),
        ).where(ProductionRecord.date >= month_start)
        row = (await session.execute(stmt)).one()

        total_output = int(row[0] or 0)
        total_qualified = int(row[1] or 0)
        total_planned = int(row[2] or 0)
        total_defect = int(row[3] or 0)
        avg_util = round(float(row[4] or 0), 1)
        total_energy = round(float(row[5] or 0), 1)
        record_count = int(row[6] or 0)

        yield_rate = round(total_qualified / total_output * 100, 1) if total_output > 0 else 0.0
        completion_rate = round(total_output / total_planned * 100, 1) if total_planned > 0 else 0.0

    return {
        "month": today.strftime("%Y-%m"),
        "total_output": total_output,
        "total_planned": total_planned,
        "total_qualified": total_qualified,
        "total_defect": total_defect,
        "yield_rate": yield_rate,
        "completion_rate": completion_rate,
        "avg_equipment_utilization": avg_util,
        "total_energy_consumption": total_energy,
        "record_count": record_count,
    }


async def get_daily_stats(days: int = 7) -> list[dict]:
    """按天聚合生产统计（近 N 天）。

    Returns:
        按日期升序的列表，每项含 date / plan_output / actual_output /
        yield_rate / utilization / achievement_rate。
    """
    sessionmaker = _get_session()
    start = date.today() - timedelta(days=days - 1)

    async with sessionmaker() as session:
        stmt = (
            select(
                ProductionRecord.date,
                func.sum(ProductionRecord.planned_qty).label("plan_output"),
                func.sum(ProductionRecord.actual_qty).label("actual_output"),
                func.sum(ProductionRecord.qualified_qty).label("qualified_qty"),
                func.avg(ProductionRecord.equipment_utilization).label("utilization"),
            )
            .where(ProductionRecord.date >= start)
            .group_by(ProductionRecord.date)
            .order_by(ProductionRecord.date)
        )
        rows = (await session.execute(stmt)).all()

    result = []
    for r in rows:
        actual = int(r.actual_output or 0)
        planned = int(r.plan_output or 0)
        qualified = int(r.qualified_qty or 0)
        result.append(
            {
                "date": r.date.isoformat(),
                "plan_output": planned,
                "actual_output": actual,
                "yield_rate": round(qualified / actual * 100, 1) if actual > 0 else 0.0,
                "utilization": round(float(r.utilization or 0), 1),
                "achievement_rate": round(actual / planned * 100, 1) if planned > 0 else 0.0,
            }
        )
    return result


async def seed_production_data() -> dict:
    """填充生产演示数据：14天 x 5产线 x 3班次 + 8个工单。

    P1 学术诚信警告：
        本函数使用 random.randint/uniform 生成模拟生产数据，仅用于
        演示环境的功能验证，不包含真实生产数据。
        - 禁止在生产环境调用（本函数入口有 is_production 守卫）
        - 禁止用于学术论文实验（项目目标期刊：Journal of Intelligent Manufacturing）
        - 任何基于此数据的统计分析、KPI 报告均不具备生产参考价值

    Returns:
        {"already_exists": bool, "record_count": int, "work_order_count": int}

    Raises:
        RuntimeError: 数据库未配置 或 当前为生产环境
    """
    # P1 学术诚信守卫：禁止在生产环境生成演示数据
    if config.environment.is_production:
        logger.error(
            "seed_production_data 在生产环境被调用，已拒绝执行（ENVIRONMENT=%s）",
            config.environment.environment,
        )
        raise RuntimeError("禁止在生产环境调用 seed_production_data 演示数据填充")

    sessionmaker = _get_session()
    async with sessionmaker() as session:
        existing = (await session.execute(select(func.count()).select_from(ProductionRecord))).scalar()
        if existing and existing > 0:
            return {"already_exists": True, "record_count": 0, "work_order_count": 0}

        today = date.today()
        record_count = 0

        try:
            for day_offset in range(SEED_DATA_DAYS):
                d = today - timedelta(days=day_offset)

                for line in LINES:
                    for shift in SHIFTS:
                        planned = random.randint(SEED_PLANNED_QTY_MIN, SEED_PLANNED_QTY_MAX)
                        actual = random.randint(int(planned * SEED_ACTUAL_QTY_RATIO_MIN), planned)
                        qualified = int(actual * random.uniform(SEED_QUALIFIED_RATIO_MIN, SEED_QUALIFIED_RATIO_MAX))
                        defect = actual - qualified
                        util = round(random.uniform(SEED_UTILIZATION_MIN, SEED_UTILIZATION_MAX), 1)
                        energy = round(random.uniform(SEED_ENERGY_MIN, SEED_ENERGY_MAX), 1)

                        rec = ProductionRecord(
                            date=d,
                            line_name=line,
                            planned_qty=planned,
                            actual_qty=actual,
                            qualified_qty=qualified,
                            defect_qty=defect,
                            equipment_utilization=util,
                            energy_consumption=energy,
                            shift=shift,
                        )
                        session.add(rec)
                        record_count += 1

            # 8 个工单
            work_orders_data = [
                {
                    "order_no": "WO-20260623-001",
                    "product_name": "精密轴类零件-A型",
                    "planned_qty": 500,
                    "completed_qty": 320,
                    "status": "进行中",
                    "priority": "紧急",
                    "start_date": date(2026, 6, 20),
                    "due_date": date(2026, 6, 28),
                },
                {
                    "order_no": "WO-20260623-002",
                    "product_name": "齿轮组件-B型",
                    "planned_qty": 300,
                    "completed_qty": 300,
                    "status": "已完成",
                    "priority": "高",
                    "start_date": date(2026, 6, 15),
                    "due_date": date(2026, 6, 22),
                },
                {
                    "order_no": "WO-20260623-003",
                    "product_name": "箱体铸件-C型",
                    "planned_qty": 100,
                    "completed_qty": 45,
                    "status": "进行中",
                    "priority": "中",
                    "start_date": date(2026, 6, 21),
                    "due_date": date(2026, 7, 5),
                },
                {
                    "order_no": "WO-20260623-004",
                    "product_name": "模具核心-D型",
                    "planned_qty": 50,
                    "completed_qty": 0,
                    "status": "待开始",
                    "priority": "高",
                    "start_date": date(2026, 6, 25),
                    "due_date": date(2026, 7, 10),
                },
                {
                    "order_no": "WO-20260623-005",
                    "product_name": "焊接支架-E型",
                    "planned_qty": 200,
                    "completed_qty": 180,
                    "status": "进行中",
                    "priority": "中",
                    "start_date": date(2026, 6, 18),
                    "due_date": date(2026, 6, 26),
                },
                {
                    "order_no": "WO-20260623-006",
                    "product_name": "精密轴承座-F型",
                    "planned_qty": 150,
                    "completed_qty": 150,
                    "status": "已完成",
                    "priority": "低",
                    "start_date": date(2026, 6, 10),
                    "due_date": date(2026, 6, 20),
                },
                {
                    "order_no": "WO-20260623-007",
                    "product_name": "涡轮叶片-G型",
                    "planned_qty": 80,
                    "completed_qty": 20,
                    "status": "已延期",
                    "priority": "紧急",
                    "start_date": date(2026, 6, 12),
                    "due_date": date(2026, 6, 22),
                },
                {
                    "order_no": "WO-20260623-008",
                    "product_name": "液压缸体-H型",
                    "planned_qty": 120,
                    "completed_qty": 0,
                    "status": "待开始",
                    "priority": "中",
                    "start_date": date(2026, 6, 28),
                    "due_date": date(2026, 7, 15),
                },
            ]

            for wod in work_orders_data:
                session.add(WorkOrder(**wod))

            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充生产演示数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充生产演示数据失败: %s", e, exc_info=True)
            raise

    return {
        "already_exists": False,
        "record_count": record_count,
        "work_order_count": len(work_orders_data),
    }
