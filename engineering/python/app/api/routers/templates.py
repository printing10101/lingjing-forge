"""模板域路由注册.

聚合：
- template_ab_testing_routes   — 模板 A/B 测试
- template_branching_routes    — 模板分支
- template_evolution_routes    — 模板演化
- template_update_routes       — 模板更新
- pattern_engine_routes        — 模式引擎
- flywheel                     — 飞轮（模板系统产物，已从 tasks 域迁移）
- template_market              — 模板市场
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    flywheel,
    pattern_engine_routes as pattern_engine,
    template_ab_testing_routes as template_ab,
    template_branching_routes as template_branches,
    template_evolution_routes as template_evolution,
    template_market,
    template_update_routes as template_updates,
)


def register(app: FastAPI) -> None:
    """注册模板域路由."""
    # 模板系统
    app.include_router(template_ab.router)
    app.include_router(template_branches.router)
    app.include_router(template_evolution.router)
    app.include_router(template_updates.router)
    app.include_router(pattern_engine.router)
    app.include_router(flywheel.router)

    # 模板市场
    app.include_router(template_market.router)
