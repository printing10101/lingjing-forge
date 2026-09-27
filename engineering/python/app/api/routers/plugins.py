"""插件域路由注册.

聚合：
- plugins — 插件系统统一入口
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import plugins


def register(app: FastAPI) -> None:
    """注册插件域路由."""
    # 插件系统
    app.include_router(plugins.router)
