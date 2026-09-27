"""刀具库管理 Service 层。

封装刀具 CRUD、磨损跟踪、寿命预测的业务逻辑与数据库操作，供
``app.api.v1.tools`` 路由调用。
所有函数返回原始数据（dict / None），不构造 HTTP 响应。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, func, or_
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_sessionmaker
from app.database.models.tool import Tool

logger = logging.getLogger(__name__)


# 种子数据（与原路由保持一致）
SEED_TOOLS = [
    {
        "code": "T01",
        "name": "D10立铣刀",
        "type": "end_mill",
        "diameter": 10.0,
        "length": 75.0,
        "flute_count": 4,
        "material": "carbide",
        "coating": "TiAlN",
        "max_rpm": 12000,
        "max_feed": 3000,
        "vendor": "山特维克",
        "cost": 280.0,
    },
    {
        "code": "T02",
        "name": "D6球头铣刀",
        "type": "ball_mill",
        "diameter": 6.0,
        "length": 60.0,
        "flute_count": 2,
        "material": "carbide",
        "coating": "AlCrN",
        "max_rpm": 15000,
        "max_feed": 2000,
        "vendor": "肯纳金属",
        "cost": 320.0,
    },
    {
        "code": "T03",
        "name": "D8.5钻头",
        "type": "drill",
        "diameter": 8.5,
        "length": 100.0,
        "flute_count": 2,
        "material": "hss",
        "coating": "TiN",
        "max_rpm": 5000,
        "max_feed": 500,
        "vendor": "OSG",
        "cost": 45.0,
    },
    {
        "code": "T04",
        "name": "D10铰刀",
        "type": "reamer",
        "diameter": 10.0,
        "length": 90.0,
        "flute_count": 6,
        "material": "hss",
        "coating": "TiN",
        "max_rpm": 3000,
        "max_feed": 200,
        "vendor": "OSG",
        "cost": 120.0,
    },
    {
        "code": "T05",
        "name": "M6丝锥",
        "type": "tap",
        "diameter": 6.0,
        "length": 70.0,
        "flute_count": 4,
        "material": "hss",
        "coating": "TiN",
        "max_rpm": 1500,
        "max_feed": 100,
        "vendor": "YAMAWA",
        "cost": 35.0,
    },
    {
        "code": "T06",
        "name": "D16立铣刀",
        "type": "end_mill",
        "diameter": 16.0,
        "length": 90.0,
        "flute_count": 4,
        "material": "carbide",
        "coating": "TiAlN",
        "max_rpm": 10000,
        "max_feed": 4000,
        "vendor": "山特维克",
        "cost": 420.0,
    },
    {
        "code": "T07",
        "name": "D4立铣刀",
        "type": "end_mill",
        "diameter": 4.0,
        "length": 50.0,
        "flute_count": 4,
        "material": "carbide",
        "coating": "DLC",
        "max_rpm": 20000,
        "max_feed": 1500,
        "vendor": "三菱综合材料",
        "cost": 180.0,
    },
    {
        "code": "T08",
        "name": "切槽刀 3mm",
        "type": "grooving",
        "diameter": 3.0,
        "length": 40.0,
        "flute_count": 1,
        "material": "carbide",
        "coating": None,
        "max_rpm": 3000,
        "max_feed": 100,
        "vendor": "伊斯卡",
        "cost": 150.0,
    },
]


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置，请设置 DB_URL 环境变量")
    return sessionmaker


def _row_to_dict(t: Tool) -> dict:
    """将 Tool ORM 对象转换为含派生字段的 dict。"""
    return {
        **t.to_dict(),
        "wear_percentage": t.wear_percentage,
        "is_worn": t.is_worn,
        "tool_life_remaining": t.tool_life_remaining,
    }


def _get_recommendation(t: Tool) -> str:
    """根据刀具状态给出使用建议。"""
    if t.status == "broken":
        return "刀具已报废，请立即更换"
    if t.wear_percentage > 90:
        return "刀具磨损严重，建议立即更换或刃磨"
    if t.wear_percentage > 70:
        return "刀具磨损较多，建议安排刃磨计划"
    if t.wear_percentage > 50:
        return "刀具磨损中等，可降低进给率延长寿命"
    if t.tool_life_remaining < 20:
        return "剩余寿命不足20%，建议准备替换刀具"
    return "刀具状态良好，可正常使用"


async def list_tools(
    type: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
) -> list[dict]:
    """获取刀具列表，支持类型、状态筛选和关键词搜索。

    Returns:
        刀具 dict 列表。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(Tool).order_by(Tool.code)
        if type:
            stmt = stmt.where(Tool.type == type)
        if status:
            stmt = stmt.where(Tool.status == status)
        if keyword:
            pattern = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    Tool.name.ilike(pattern),
                    Tool.code.ilike(pattern),
                )
            )
        result = await session.execute(stmt)
        tools = result.scalars().all()
        return [_row_to_dict(t) for t in tools]


async def stats_summary() -> dict:
    """获取刀具统计汇总：总数、磨损数、报废数、活跃数。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        total = await session.execute(select(func.count(Tool.id)))
        worn = await session.execute(select(func.count(Tool.id)).where(Tool.status == "worn"))
        broken = await session.execute(select(func.count(Tool.id)).where(Tool.status == "broken"))
        active = await session.execute(select(func.count(Tool.id)).where(Tool.status == "active"))
        return {
            "total": total.scalar() or 0,
            "active": active.scalar() or 0,
            "worn": worn.scalar() or 0,
            "broken": broken.scalar() or 0,
        }


async def get_tool(tool_id: str) -> dict | None:
    """根据 ID 获取单个刀具详情。

    Returns:
        刀具 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        result = await session.execute(select(Tool).where(Tool.id == tool_id))
        t = result.scalar_one_or_none()
        if t is None:
            return None
        return _row_to_dict(t)


async def create_tool(body_data: dict[str, Any]) -> dict[str, Any]:
    """创建新刀具。

    Args:
        body_data: 刀具字段 dict（含 code, name, type, diameter 等）

    Returns:
        新建刀具 dict。

    Raises:
        ValueError: 刀具编码已存在。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            # 检查编码唯一性
            existing = await session.execute(select(Tool).where(Tool.code == body_data["code"]))
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"刀具编码 '{body_data['code']}' 已存在")

            t = Tool(**body_data)
            session.add(t)
            await session.commit()
            return _row_to_dict(t)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("创建刀具失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("创建刀具失败: %s", e, exc_info=True)
            raise


async def update_tool(tool_id: str, update_data: dict[str, Any]) -> dict[str, Any] | None:
    """更新刀具信息。

    Args:
        tool_id: 刀具 ID
        update_data: 待更新字段 dict（已 exclude_unset）

    Returns:
        更新后的刀具 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            result = await session.execute(select(Tool).where(Tool.id == tool_id))
            t = result.scalar_one_or_none()
            if t is None:
                return None

            for key, value in update_data.items():
                setattr(t, key, value)
            t.updated_at = datetime.now(timezone.utc)

            await session.commit()
            return _row_to_dict(t)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新刀具失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新刀具失败: %s", e, exc_info=True)
            raise


async def delete_tool(tool_id: str) -> bool | None:
    """删除刀具。

    Returns:
        True 表示删除成功；None 表示刀具未找到。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            result = await session.execute(select(Tool).where(Tool.id == tool_id))
            t = result.scalar_one_or_none()
            if t is None:
                return None

            await session.delete(t)
            await session.commit()
            return True
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("删除刀具失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("删除刀具失败: %s", e, exc_info=True)
            raise


async def update_tool_wear(
    tool_id: str,
    additional_usage_time: float,
    additional_wear: float,
    sharpened: bool,
) -> dict | None:
    """更新刀具磨损信息。

    累加使用时间和磨损量，如果进行了刃磨则重置磨损量并记录刃磨时间。

    Returns:
        更新后的刀具 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            result = await session.execute(select(Tool).where(Tool.id == tool_id))
            t = result.scalar_one_or_none()
            if t is None:
                return None

            if sharpened:
                # 刃磨：重置磨损量，记录刃磨时间
                t.wear_amount = 0.0
                t.last_sharpened = datetime.now(timezone.utc)
                t.status = "active"
                logger.info("刀具 %s 已刃磨，磨损量重置为0", t.code)
            else:
                # 正常磨损累加
                t.usage_time += additional_usage_time
                t.wear_amount += additional_wear

                # 自动更新状态
                if t.wear_percentage > 80.0:
                    t.status = "worn"
                    logger.warning("刀具 %s 磨损超过80%%，状态更新为worn", t.code)

            t.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _row_to_dict(t)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新刀具磨损失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新刀具磨损失败: %s", e, exc_info=True)
            raise


async def tool_life_prediction(tool_id: str) -> dict | None:
    """获取刀具寿命预测信息。

    基于使用时间和磨损量估算剩余寿命。

    Returns:
        寿命预测 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        result = await session.execute(select(Tool).where(Tool.id == tool_id))
        t = result.scalar_one_or_none()
        if t is None:
            return None

        return {
            "tool_id": tool_id,
            "tool_code": t.code,
            "tool_name": t.name,
            "status": t.status,
            "usage_time_min": t.usage_time,
            "wear_amount_mm": t.wear_amount,
            "wear_percentage": t.wear_percentage,
            "tool_life_remaining_pct": t.tool_life_remaining,
            "is_worn": t.is_worn,
            "last_sharpened": t.last_sharpened.isoformat() if t.last_sharpened else None,
            "recommendation": _get_recommendation(t),
        }


async def seed_tools() -> dict:
    """初始化种子数据（仅在刀具表为空时插入）。

    Returns:
        {"already_exists": bool, "existing_count": int, "inserted_count": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        count_result = await session.execute(select(func.count(Tool.id)))
        existing_count = count_result.scalar() or 0
        if existing_count > 0:
            return {"already_exists": True, "existing_count": existing_count, "inserted_count": 0}

        try:
            for item in SEED_TOOLS:
                t = Tool(**item)
                session.add(t)
            await session.commit()

            logger.info("已插入 %d 条刀具种子数据", len(SEED_TOOLS))
            return {
                "already_exists": False,
                "existing_count": 0,
                "inserted_count": len(SEED_TOOLS),
            }
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充刀具演示数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充刀具演示数据失败: %s", e, exc_info=True)
            raise
