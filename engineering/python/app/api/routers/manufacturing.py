"""制造域路由注册.

聚合：
- materials  — 物料管理
- equipment  — 设备管理
- quality    — 质量管理
- production — 生产管理
- process_routes — 工艺路线
- documents  — 制造文档
- notifications — 通知 / 系统状态 / 活动简报聚合
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    documents,
    equipment,
    materials,
    notifications,
    process_routes,
    production,
    quality,
)


def register(app: FastAPI) -> None:
    """注册制造域路由（Manufacturing UI APIs）."""
    # Manufacturing UI APIs
    app.include_router(materials.router)
    app.include_router(equipment.router)
    app.include_router(quality.router)
    app.include_router(production.router)
    app.include_router(process_routes.router)
    app.include_router(documents.router)
    # 顶栏通知 / 首页系统状态与活动简报（聚合端点）
    app.include_router(notifications.router)
