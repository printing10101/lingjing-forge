"""物料管理 Service 层。

封装物料的 CRUD、统计汇总与种子数据填充逻辑，供
``app.api.v1.materials`` 路由调用。所有函数返回原始数据（dict / None），
不构造 HTTP 响应。未找到返回 ``None``；数据库未配置抛出 ``RuntimeError``
（与原路由行为一致，由上层 FastAPI 异常处理器接管）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_sessionmaker
from app.database.models import Material

logger = logging.getLogger(__name__)


# 物料种子数据（10 条）
SEED_MATERIALS = [
    {
        "code": "MAT-001",
        "name": "45号钢",
        "spec": "φ50×200mm",
        "category": "原材料",
        "quantity": 1500,
        "safe_quantity": 200,
        "status": "正常",
        "location": "A-01-03",
        "unit": "根",
        "supplier": "宝钢集团",
    },
    {
        "code": "MAT-002",
        "name": "铝合金6061",
        "spec": "100×50×300mm",
        "category": "原材料",
        "quantity": 800,
        "safe_quantity": 300,
        "status": "正常",
        "location": "A-01-05",
        "unit": "根",
        "supplier": "忠旺集团",
    },
    {
        "code": "MAT-003",
        "name": "不锈钢304",
        "spec": "φ30×150mm",
        "category": "原材料",
        "quantity": 200,
        "safe_quantity": 150,
        "status": "低库存",
        "location": "A-02-01",
        "unit": "根",
        "supplier": "太钢集团",
    },
    {
        "code": "MAT-004",
        "name": "铜棒H59",
        "spec": "φ25×100mm",
        "category": "原材料",
        "quantity": 50,
        "safe_quantity": 100,
        "status": "缺货",
        "location": "A-02-03",
        "unit": "根",
        "supplier": "海亮股份",
    },
    {
        "code": "MAT-005",
        "name": "齿轮半成品",
        "spec": "GZ-50T",
        "category": "半成品",
        "quantity": 120,
        "safe_quantity": 50,
        "status": "正常",
        "location": "B-01-01",
        "unit": "件",
        "supplier": "自制",
    },
    {
        "code": "MAT-006",
        "name": "轴承座半成品",
        "spec": "ZC-30B",
        "category": "半成品",
        "quantity": 85,
        "safe_quantity": 30,
        "status": "正常",
        "location": "B-01-02",
        "unit": "件",
        "supplier": "自制",
    },
    {
        "code": "MAT-007",
        "name": "减速机总成",
        "spec": "JS-50W",
        "category": "成品",
        "quantity": 30,
        "safe_quantity": 20,
        "status": "正常",
        "location": "C-01-01",
        "unit": "台",
        "supplier": "自制",
    },
    {
        "code": "MAT-008",
        "name": "伺服电机",
        "spec": "SM-750W",
        "category": "成品",
        "quantity": 15,
        "safe_quantity": 10,
        "status": "低库存",
        "location": "C-01-02",
        "unit": "台",
        "supplier": "安川电机",
    },
    {
        "code": "MAT-009",
        "name": "数控刀具",
        "spec": "D10-200",
        "category": "原材料",
        "quantity": 45,
        "safe_quantity": 20,
        "status": "正常",
        "location": "A-03-01",
        "unit": "把",
        "supplier": "山特维克",
    },
    {
        "code": "MAT-010",
        "name": "润滑油",
        "spec": "HM-46",
        "category": "原材料",
        "quantity": 200,
        "safe_quantity": 50,
        "status": "正常",
        "location": "A-03-05",
        "unit": "升",
        "supplier": "壳牌",
    },
]


def _get_session():
    """获取异步 sessionmaker 工厂，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置，请设置 DB_URL 环境变量")
    return sessionmaker


def _row_to_dict(m: Material) -> dict:
    """将 Material ORM 行转为字典（保持与原路由一致的键序列）。"""
    return {
        "id": m.id,
        "code": m.code,
        "name": m.name,
        "spec": m.spec,
        "category": m.category,
        "quantity": m.quantity,
        "safe_quantity": m.safe_quantity,
        "status": m.status,
        "location": m.location,
        "unit": m.unit,
        "supplier": m.supplier,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


async def list_materials(
    category: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """返回物料列表（分页 + 多条件过滤）。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        base = select(Material)
        if category:
            base = base.where(Material.category == category)
        if status:
            base = base.where(Material.status == status)
        if keyword:
            pattern = f"%{keyword}%"
            base = base.where(
                or_(
                    Material.name.ilike(pattern),
                    Material.code.ilike(pattern),
                )
            )

        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = base.order_by(Material.code).limit(page_size).offset(offset)
        materials = (await session.execute(stmt)).scalars().all()

    return {
        "items": [_row_to_dict(m) for m in materials],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


async def stats_summary() -> dict:
    """返回物料统计汇总：总数、低库存数、缺货数。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(
            func.count(Material.id).label("total"),
            func.sum(case((Material.status == "低库存", 1), else_=0)).label("low_stock"),
            func.sum(case((Material.status == "缺货", 1), else_=0)).label("out_of_stock"),
        )
        row = (await session.execute(stmt)).one()

    return {
        "total": row.total or 0,
        "low_stock": int(row.low_stock or 0),
        "out_of_stock": int(row.out_of_stock or 0),
    }


async def get_material(material_id: str) -> dict | None:
    """根据 ID 获取物料详情，未找到返回 None。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        m = (await session.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
        if m is None:
            return None
        return _row_to_dict(m)


def _recalc_status(m: Material) -> None:
    """根据库存数量与安全库存重算物料状态（缺货/低库存/正常）。"""
    if m.quantity <= 0:
        m.status = "缺货"  # type: ignore[assignment]
    elif m.quantity < m.safe_quantity:
        m.status = "低库存"  # type: ignore[assignment]
    else:
        m.status = "正常"  # type: ignore[assignment]


async def stock_in_material(material_id: str, quantity: int, remark: str | None = None) -> dict | None:
    """物料入库：库存数量增加并重算状态。

    Args:
        material_id: 物料 ID
        quantity: 入库数量（>0）
        remark: 入库备注（当前模型无流水表，仅记录日志）

    Returns:
        更新后的物料 dict；未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            m = (await session.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
            if m is None:
                return None

            m.quantity = (m.quantity or 0) + quantity
            _recalc_status(m)
            m.updated_at = datetime.now(timezone.utc)
            await session.commit()
            if remark:
                logger.info("物料 %s 入库 %d（备注：%s）", material_id, quantity, remark)
            return _row_to_dict(m)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("物料入库失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("物料入库失败: %s", e, exc_info=True)
            raise


async def purchase_material(material_id: str, quantity: int, supplier: str | None = None) -> dict | None:
    """物料采购：更新供应商信息并增加库存数量，重算状态。

    Args:
        material_id: 物料 ID
        quantity: 采购数量（>0）
        supplier: 供应商（可选，提供时更新）

    Returns:
        更新后的物料 dict；未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            m = (await session.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
            if m is None:
                return None

            if supplier:
                m.supplier = supplier
            m.quantity = (m.quantity or 0) + quantity
            _recalc_status(m)
            m.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _row_to_dict(m)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("物料采购失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("物料采购失败: %s", e, exc_info=True)
            raise


async def create_material(data: dict[str, Any]) -> dict[str, Any]:
    """创建新物料，返回新建物料 dict。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            m = Material(**data)
            session.add(m)
            await session.commit()
            return _row_to_dict(m)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("创建物料失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("创建物料失败: %s", e, exc_info=True)
            raise


async def update_material(material_id: str, update_data: dict[str, Any]) -> dict[str, Any] | None:
    """更新物料字段。

    Args:
        material_id: 物料 ID
        update_data: 已 ``exclude_unset`` 的更新字段 dict

    Returns:
        更新后的物料 dict；未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            m = (await session.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
            if m is None:
                return None

            for key, value in update_data.items():
                setattr(m, key, value)
            m.updated_at = datetime.now(timezone.utc)

            await session.commit()
            return _row_to_dict(m)
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新物料失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新物料失败: %s", e, exc_info=True)
            raise


async def delete_material(material_id: str) -> bool | None:
    """删除物料。返回 True；未找到返回 None。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            m = (await session.execute(select(Material).where(Material.id == material_id))).scalar_one_or_none()
            if m is None:
                return None

            await session.delete(m)
            await session.commit()
            return True
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("删除物料失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("删除物料失败: %s", e, exc_info=True)
            raise


async def seed_materials() -> dict:
    """填充物料种子数据。

    Returns:
        {"already_exists": bool, "existing_count": int, "inserted_count": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        existing_count = (await session.execute(select(func.count(Material.id)))).scalar() or 0
        if existing_count > 0:
            return {
                "already_exists": True,
                "existing_count": existing_count,
                "inserted_count": 0,
            }

        try:
            for item in SEED_MATERIALS:
                session.add(Material(**item))
            await session.commit()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充物料种子数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充物料种子数据失败: %s", e, exc_info=True)
            raise

    return {
        "already_exists": False,
        "existing_count": 0,
        "inserted_count": len(SEED_MATERIALS),
    }
