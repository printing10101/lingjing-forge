"""工艺路线 Service 层。

封装工艺路线及工序的业务逻辑与数据库操作，供 ``app.api.v1.process_routes`` 路由调用。
所有函数返回原始数据（dict / None），不构造 HTTP 响应。
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import select, func, delete
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_sessionmaker
from app.database.models import ProcessRoute, ProcessStep

logger = logging.getLogger(__name__)


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


async def list_process_routes(
    status: str | None = None,
    part_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """返回工艺路线列表，支持按状态、零件类型筛选。

    Returns:
        {"routes": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(ProcessRoute).order_by(ProcessRoute.created_at.desc())
        if status:
            stmt = stmt.where(ProcessRoute.status == status)
        if part_type:
            stmt = stmt.where(ProcessRoute.part_type == part_type)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "routes": [r.to_dict() for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_process_route(route_id: str) -> dict | None:
    """获取工艺路线详情（含所有工序步骤）。

    Returns:
        含 steps 的路线 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        route_stmt = select(ProcessRoute).where(ProcessRoute.id == route_id)
        route = (await session.execute(route_stmt)).scalar_one_or_none()
        if not route:
            return None

        steps_stmt = select(ProcessStep).where(ProcessStep.route_id == route_id).order_by(ProcessStep.sequence)
        steps = (await session.execute(steps_stmt)).scalars().all()

    result = route.to_dict()
    result["steps"] = [s.to_dict() for s in steps]
    return result


async def create_process_route(body_data: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """创建工艺路线（含工序步骤）。

    Args:
        body_data: 路线字段 dict（name, part_type, status, description）
        steps: 工序步骤列表

    Returns:
        新建路线 dict。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            route = ProcessRoute(
                name=body_data["name"],
                part_type=body_data["part_type"],
                status=body_data["status"],
                description=body_data["description"],
                steps_count=len(steps),
            )
            session.add(route)
            await session.flush()

            for step_data in steps:
                step = ProcessStep(
                    route_id=route.id,
                    **step_data,
                )
                session.add(step)

            await session.commit()
            return route.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("创建工艺路线失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("创建工艺路线失败: %s", e, exc_info=True)
            raise


async def update_process_route(
    route_id: str,
    update_fields: dict,
    steps: list[dict] | None = None,
) -> dict | None:
    """更新工艺路线（含工序步骤替换）。

    Args:
        route_id: 路线 ID
        update_fields: 待更新的路线字段 dict（name/part_type/status/description）
        steps: 若不为 None，则替换所有工序步骤

    Returns:
        更新后的路线 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            route_stmt = select(ProcessRoute).where(ProcessRoute.id == route_id)
            route = (await session.execute(route_stmt)).scalar_one_or_none()
            if not route:
                return None

            # 更新基本字段
            if "name" in update_fields:
                route.name = update_fields["name"]
            if "part_type" in update_fields:
                route.part_type = update_fields["part_type"]
            if "status" in update_fields:
                route.status = update_fields["status"]
            if "description" in update_fields:
                route.description = update_fields["description"]

            # 如果提供了 steps，则替换所有工序
            if steps is not None:
                # 删除旧工序
                del_stmt = delete(ProcessStep).where(ProcessStep.route_id == route_id)
                await session.execute(del_stmt)

                for step_data in steps:
                    step = ProcessStep(
                        route_id=route_id,
                        **step_data,
                    )
                    session.add(step)

                route.steps_count = len(steps)

            await session.commit()
            return route.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新工艺路线失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新工艺路线失败: %s", e, exc_info=True)
            raise


async def delete_process_route(route_id: str) -> bool | None:
    """删除工艺路线及其所有工序。

    Returns:
        True 表示删除成功；None 表示路线未找到。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            route_stmt = select(ProcessRoute).where(ProcessRoute.id == route_id)
            route = (await session.execute(route_stmt)).scalar_one_or_none()
            if not route:
                return None

            # 删除工序
            del_steps = delete(ProcessStep).where(ProcessStep.route_id == route_id)
            await session.execute(del_steps)

            # 删除路线
            del_route = delete(ProcessRoute).where(ProcessRoute.id == route_id)
            await session.execute(del_route)

            await session.commit()
            return True
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("删除工艺路线失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("删除工艺路线失败: %s", e, exc_info=True)
            raise


async def seed_process_routes() -> dict:
    """填充工艺路线演示数据：6条路线及其工序。

    Returns:
        {"already_exists": bool, "routes_count": int, "steps_count": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        existing = (await session.execute(select(func.count()).select_from(ProcessRoute))).scalar()
        if existing and existing > 0:
            return {"already_exists": True, "routes_count": 0, "steps_count": 0}

        routes_seed = [
            {
                "name": "轴类零件加工工艺",
                "part_type": "轴类零件",
                "status": "已发布",
                "description": "适用于直径20-200mm的精密轴类零件加工",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "下料",
                        "work_center": "下料车间",
                        "hours": 30,
                        "equipment": "带锯床",
                        "tooling": "标准锯条",
                    },
                    {
                        "sequence": 2,
                        "name": "粗车外圆",
                        "work_center": "车削中心",
                        "hours": 60,
                        "equipment": "CK6150数控车床",
                        "tooling": "外圆车刀",
                    },
                    {
                        "sequence": 3,
                        "name": "精车外圆",
                        "work_center": "车削中心",
                        "hours": 90,
                        "equipment": "CK6150数控车床",
                        "tooling": "精车刀片",
                    },
                    {
                        "sequence": 4,
                        "name": "铣键槽",
                        "work_center": "铣削中心",
                        "hours": 45,
                        "equipment": "VMC850加工中心",
                        "tooling": "键槽铣刀",
                    },
                    {
                        "sequence": 5,
                        "name": "磨削外圆",
                        "work_center": "磨削车间",
                        "hours": 60,
                        "equipment": "M1432B外圆磨床",
                        "tooling": "砂轮",
                    },
                    {
                        "sequence": 6,
                        "name": "质检包装",
                        "work_center": "质检中心",
                        "hours": 20,
                        "equipment": "三坐标测量仪",
                        "tooling": None,
                    },
                ],
            },
            {
                "name": "齿轮加工工艺",
                "part_type": "齿轮类零件",
                "status": "已发布",
                "description": "适用于模数1-8的精密齿轮加工",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "下料",
                        "work_center": "下料车间",
                        "hours": 25,
                        "equipment": "带锯床",
                        "tooling": "标准锯条",
                    },
                    {
                        "sequence": 2,
                        "name": "锻造毛坯",
                        "work_center": "锻造车间",
                        "hours": 120,
                        "equipment": "摩擦压力机",
                        "tooling": "锻模",
                    },
                    {
                        "sequence": 3,
                        "name": "粗车",
                        "work_center": "车削中心",
                        "hours": 60,
                        "equipment": "CK6150数控车床",
                        "tooling": "外圆车刀",
                    },
                    {
                        "sequence": 4,
                        "name": "精车",
                        "work_center": "车削中心",
                        "hours": 80,
                        "equipment": "CK6150数控车床",
                        "tooling": "精车刀片",
                    },
                    {
                        "sequence": 5,
                        "name": "滚齿",
                        "work_center": "齿轮加工中心",
                        "hours": 90,
                        "equipment": "Y3150滚齿机",
                        "tooling": "滚刀",
                    },
                    {
                        "sequence": 6,
                        "name": "剃齿",
                        "work_center": "齿轮加工中心",
                        "hours": 60,
                        "equipment": "Y4232剃齿机",
                        "tooling": "剃齿刀",
                    },
                    {
                        "sequence": 7,
                        "name": "热处理",
                        "work_center": "热处理车间",
                        "hours": 180,
                        "equipment": "井式渗碳炉",
                        "tooling": None,
                    },
                    {
                        "sequence": 8,
                        "name": "磨齿",
                        "work_center": "磨削车间",
                        "hours": 120,
                        "equipment": "YK7236数控磨齿机",
                        "tooling": "蜗杆砂轮",
                    },
                ],
            },
            {
                "name": "箱体加工工艺",
                "part_type": "箱体类零件",
                "status": "草稿",
                "description": "适用于中小型铸铁箱体零件加工",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "铸造毛坯",
                        "work_center": "铸造车间",
                        "hours": 240,
                        "equipment": "DISA造型线",
                        "tooling": "砂型模具",
                    },
                    {
                        "sequence": 2,
                        "name": "时效处理",
                        "work_center": "热处理车间",
                        "hours": 480,
                        "equipment": "时效炉",
                        "tooling": None,
                    },
                    {
                        "sequence": 3,
                        "name": "粗铣基准面",
                        "work_center": "铣削中心",
                        "hours": 90,
                        "equipment": "龙门加工中心",
                        "tooling": "面铣刀",
                    },
                    {
                        "sequence": 4,
                        "name": "镗孔",
                        "work_center": "镗削中心",
                        "hours": 120,
                        "equipment": "T68卧式镗床",
                        "tooling": "镗刀",
                    },
                    {
                        "sequence": 5,
                        "name": "精铣各面",
                        "work_center": "铣削中心",
                        "hours": 150,
                        "equipment": "VMC850加工中心",
                        "tooling": "精铣刀片",
                    },
                ],
            },
            {
                "name": "模具制造工艺",
                "part_type": "模具类零件",
                "status": "已发布",
                "description": "适用于精密注塑模具及冲压模具制造",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "设计评审",
                        "work_center": "技术中心",
                        "hours": 120,
                        "equipment": None,
                        "tooling": None,
                    },
                    {
                        "sequence": 2,
                        "name": "备料",
                        "work_center": "下料车间",
                        "hours": 30,
                        "equipment": "带锯床",
                        "tooling": "标准锯条",
                    },
                    {
                        "sequence": 3,
                        "name": "粗加工",
                        "work_center": "铣削中心",
                        "hours": 180,
                        "equipment": "DMG MORI DMU 50",
                        "tooling": "硬质合金铣刀",
                    },
                    {
                        "sequence": 4,
                        "name": "热处理",
                        "work_center": "热处理车间",
                        "hours": 240,
                        "equipment": "真空淬火炉",
                        "tooling": None,
                    },
                    {
                        "sequence": 5,
                        "name": "精加工",
                        "work_center": "高速加工中心",
                        "hours": 300,
                        "equipment": "DMG MORI DMU 50",
                        "tooling": "球头铣刀",
                    },
                    {
                        "sequence": 6,
                        "name": "电火花",
                        "work_center": "特种加工",
                        "hours": 180,
                        "equipment": "CNC电火花机",
                        "tooling": "铜电极",
                    },
                    {
                        "sequence": 7,
                        "name": "装配试模",
                        "work_center": "装配车间",
                        "hours": 240,
                        "equipment": "合模机",
                        "tooling": None,
                    },
                ],
            },
            {
                "name": "精密铸造工艺",
                "part_type": "铸造类零件",
                "status": "已归档",
                "description": "适用于熔模精密铸造工艺流程",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "蜡模制造",
                        "work_center": "蜡模车间",
                        "hours": 60,
                        "equipment": "注蜡机",
                        "tooling": "金属模具",
                    },
                    {
                        "sequence": 2,
                        "name": "制壳",
                        "work_center": "制壳车间",
                        "hours": 120,
                        "equipment": "沾浆机",
                        "tooling": None,
                    },
                    {
                        "sequence": 3,
                        "name": "熔炼浇注",
                        "work_center": "熔炼车间",
                        "hours": 90,
                        "equipment": "中频感应炉",
                        "tooling": None,
                    },
                    {
                        "sequence": 4,
                        "name": "后处理",
                        "work_center": "清理车间",
                        "hours": 60,
                        "equipment": "抛丸机",
                        "tooling": None,
                    },
                ],
            },
            {
                "name": "焊接组装工艺",
                "part_type": "焊接组件",
                "status": "已发布",
                "description": "适用于钢结构焊接组件的制造与组装",
                "steps": [
                    {
                        "sequence": 1,
                        "name": "零件下料",
                        "work_center": "下料车间",
                        "hours": 45,
                        "equipment": "激光切割机",
                        "tooling": None,
                    },
                    {
                        "sequence": 2,
                        "name": "坡口加工",
                        "work_center": "铣削中心",
                        "hours": 30,
                        "equipment": "坡口机",
                        "tooling": None,
                    },
                    {
                        "sequence": 3,
                        "name": "拼装定位",
                        "work_center": "焊接车间",
                        "hours": 60,
                        "equipment": "拼装平台",
                        "tooling": "夹具",
                    },
                    {
                        "sequence": 4,
                        "name": "焊接",
                        "work_center": "焊接车间",
                        "hours": 180,
                        "equipment": "焊接机器人",
                        "tooling": "焊枪",
                    },
                    {
                        "sequence": 5,
                        "name": "检验校正",
                        "work_center": "质检中心",
                        "hours": 40,
                        "equipment": "超声波探伤仪",
                        "tooling": None,
                    },
                ],
            },
        ]

        steps_count = 0
        try:
            for rs in routes_seed:
                steps = cast(list[dict[str, Any]], rs.pop("steps"))
                steps_count += len(steps)
                route = ProcessRoute(steps_count=len(steps), **rs)
                session.add(route)
                await session.flush()

                for sd in steps:
                    session.add(ProcessStep(route_id=route.id, **dict(sd)))

            await session.commit()
            return {
                "already_exists": False,
                "routes_count": len(routes_seed),
                "steps_count": steps_count,
            }
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充工艺路线演示数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充工艺路线演示数据失败: %s", e, exc_info=True)
            raise
