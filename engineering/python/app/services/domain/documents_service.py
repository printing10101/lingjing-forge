"""知识库文档 Service 层。

封装文档管理的业务逻辑与数据库操作，供 ``app.api.v1.documents`` 路由调用。
所有函数返回原始数据（dict / None），不构造 HTTP 响应。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, func, delete, or_
from sqlalchemy.exc import SQLAlchemyError

from app.database.connection import get_sessionmaker
from app.database.models import Document

logger = logging.getLogger(__name__)


# 所有合法的文档分类（用于 list_categories 确保零计数分类也返回）
VALID_CATEGORIES = ["工艺规范", "SOP标准", "设备手册", "质量标准", "材料参数"]


def _get_session():
    """获取异步 sessionmaker，若数据库未配置则抛出 RuntimeError。"""
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("数据库未配置")
    return sessionmaker


async def list_documents(
    category: str | None = None,
    status: str | None = None,
    keyword: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """返回文档列表。

    Returns:
        {"documents": [...], "total": int, "limit": int, "offset": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = select(Document).order_by(Document.created_at.desc())
        if category:
            stmt = stmt.where(Document.category == category)
        if status:
            stmt = stmt.where(Document.status == status)
        if keyword:
            stmt = stmt.where(
                or_(
                    Document.title.ilike(f"%{keyword}%"),
                    Document.content.ilike(f"%{keyword}%"),
                    Document.author.ilike(f"%{keyword}%"),
                )
            )

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await session.execute(count_stmt)).scalar() or 0

        stmt = stmt.offset(offset).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()

    return {
        "documents": [d.to_dict() for d in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def list_categories() -> dict:
    """返回所有分类及其文档数量（确保零计数分类也返回）。

    Returns:
        {"categories": [{"name": str, "count": int}, ...]}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        stmt = (
            select(Document.category, func.count().label("count"))
            .group_by(Document.category)
            .order_by(func.count().desc())
        )
        rows = (await session.execute(stmt)).all()

        # 确保所有分类都有返回，即使数量为0
        cat_map = {row.category: row.count for row in rows}
        categories = [{"name": cat, "count": cat_map.get(cat, 0)} for cat in VALID_CATEGORIES]

    return {"categories": categories}


async def get_document(doc_id: str) -> dict | None:
    """获取单个文档详情并增加浏览量。

    Returns:
        文档 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            stmt = select(Document).where(Document.id == doc_id)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            if not doc:
                return None

            # 增加浏览量
            doc.view_count = (doc.view_count or 0) + 1
            await session.commit()

            return doc.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("获取文档失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("获取文档失败: %s", e, exc_info=True)
            raise


async def create_document(
    title: str,
    category: str,
    version: str = "v1.0",
    author: str = "",
    content: str | None = None,
    tags: list[str] | None = None,
    status: str = "待审核",
) -> dict:
    """创建文档，返回新建文档 dict。"""
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            doc = Document(
                title=title,
                category=category,
                version=version,
                author=author,
                content=content,
                tags=tags or [],
                status=status,
            )
            session.add(doc)
            await session.commit()

            return doc.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("创建文档失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("创建文档失败: %s", e, exc_info=True)
            raise


async def update_document(doc_id: str, update_data: dict[str, Any]) -> dict[str, Any] | None:
    """更新文档字段。

    Args:
        doc_id: 文档 ID
        update_data: 仅包含待更新字段的 dict（已 exclude_unset）

    Returns:
        更新后的文档 dict；若未找到返回 None。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            stmt = select(Document).where(Document.id == doc_id)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            if not doc:
                return None

            for key, value in update_data.items():
                setattr(doc, key, value)

            await session.commit()

            return doc.to_dict()
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("更新文档失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("更新文档失败: %s", e, exc_info=True)
            raise


async def delete_document(doc_id: str) -> bool | None:
    """删除文档。

    Returns:
        True 表示删除成功；None 表示文档未找到。
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        try:
            stmt = select(Document).where(Document.id == doc_id)
            doc = (await session.execute(stmt)).scalar_one_or_none()
            if not doc:
                return None

            del_stmt = delete(Document).where(Document.id == doc_id)
            await session.execute(del_stmt)
            await session.commit()

            return True
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("删除文档失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("删除文档失败: %s", e, exc_info=True)
            raise


async def seed_documents() -> dict:
    """填充知识库文档演示数据。

    Returns:
        {"already_exists": bool, "count": int}
    """
    sessionmaker = _get_session()
    async with sessionmaker() as session:
        existing = (await session.execute(select(func.count()).select_from(Document))).scalar()
        if existing and existing > 0:
            return {"already_exists": True, "count": 0}

        docs_seed = [
            {
                "title": "五轴加工中心操作规范",
                "category": "工艺规范",
                "version": "v2.1",
                "author": "工艺部-张工",
                "content": "本规范规定了五轴联动加工中心的操作流程、安全要求及加工参数设置标准，适用于DMG MORI DMU 50系列设备的日常操作。",
                "tags": ["五轴加工", "操作规范", "DMG MORI", "数控"],
                "status": "已发布",
                "view_count": 156,
            },
            {
                "title": "CNC日常维护SOP",
                "category": "SOP标准",
                "version": "v1.0",
                "author": "设备部-李工",
                "content": "本SOP规定了CNC数控机床的日常维护保养流程，包括每日点检、每周保养、月度检查及年度大修的具体操作步骤和标准。",
                "tags": ["CNC", "维护保养", "SOP", "设备管理"],
                "status": "已发布",
                "view_count": 203,
            },
            {
                "title": "DMG MORI DMU 50 设备手册",
                "category": "设备手册",
                "version": "v3.2",
                "author": "DMG MORI技术支持",
                "content": "DMG MORI DMU 50 五轴万能铣削中心完整技术手册，包含设备规格、操作界面说明、报警代码及故障排除指南。",
                "tags": ["DMG MORI", "DMU 50", "设备手册", "五轴"],
                "status": "已发布",
                "view_count": 89,
            },
            {
                "title": "ISO 9001质量检验标准",
                "category": "质量标准",
                "version": "v4.0",
                "author": "质量部-王工",
                "content": "基于ISO 9001:2015标准制定的质量检验规范，涵盖进料检验、过程检验、成品检验的抽样方案、判定标准及记录要求。",
                "tags": ["ISO 9001", "质量标准", "检验规范"],
                "status": "已发布",
                "view_count": 134,
            },
            {
                "title": "45号钢材料参数手册",
                "category": "材料参数",
                "version": "v1.5",
                "author": "技术部-赵工",
                "content": "45号钢（45#）完整材料参数手册，包含化学成分、力学性能、热处理工艺参数及切削加工推荐参数。",
                "tags": ["45号钢", "材料参数", "热处理", "切削参数"],
                "status": "已发布",
                "view_count": 178,
            },
            {
                "title": "铝合金加工工艺指南",
                "category": "工艺规范",
                "version": "v2.0",
                "author": "工艺部-陈工",
                "content": "铝合金（6061-T6/7075-T6）精密加工工艺指南，涵盖刀具选择、切削参数、冷却方案及变形控制措施。",
                "tags": ["铝合金", "加工工艺", "6061", "7075"],
                "status": "待审核",
                "view_count": 45,
            },
            {
                "title": "三坐标测量操作流程",
                "category": "SOP标准",
                "version": "v1.0",
                "author": "质检部-孙工",
                "content": "三坐标测量机（CMM）标准操作流程，包括测头校准、坐标系建立、测量程序编制及测量报告生成。",
                "tags": ["三坐标", "CMM", "测量", "SOP"],
                "status": "已发布",
                "view_count": 112,
            },
            {
                "title": "刀具寿命管理规范",
                "category": "工艺规范",
                "version": "v1.2",
                "author": "工艺部-周工",
                "content": "数控加工刀具寿命管理规范，规定了各类刀具的寿命标准、监测方法、更换流程及寿命数据分析方法。",
                "tags": ["刀具管理", "寿命管理", "加工工艺"],
                "status": "待审核",
                "view_count": 67,
            },
        ]

        try:
            for dd in docs_seed:
                session.add(Document(**dd))

            await session.commit()

            return {"already_exists": False, "count": len(docs_seed)}
        except SQLAlchemyError as e:
            await session.rollback()
            logger.error("填充知识库文档演示数据失败: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("填充知识库文档演示数据失败: %s", e, exc_info=True)
            raise
