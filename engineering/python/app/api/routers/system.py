"""系统域路由注册：健康检查 / 状态 / 版本 / 日志 / 管理 / SSE."""

from fastapi import FastAPI

from app.api.v1 import health, status, system, logs, admin


def register(app: FastAPI) -> None:
    """注册系统域路由."""
    app.include_router(health.router)
    app.include_router(health.simple_health_router)
    app.include_router(status.router)
    app.include_router(system.router)
    app.include_router(system.version_router)
    app.include_router(logs.router)
    app.include_router(admin.router)
