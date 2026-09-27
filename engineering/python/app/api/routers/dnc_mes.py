"""通信域路由注册.

聚合：
- DNC 机床通信（dnc）
- MES / ERP 集成（app.integrations.mes.api）
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import dnc as dnc_routes
from app.integrations.mes import api as mes_api


def register(app: FastAPI) -> None:
    """注册通信域路由."""
    # DNC 机床通信
    app.include_router(dnc_routes.router)

    # MES/ERP 集成
    app.include_router(mes_api.router)
