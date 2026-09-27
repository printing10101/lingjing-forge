"""治理域路由注册.

聚合：
- skills       — 技能注册与发现
- cost_budget  — 成本预算与配额
- governance   — 通用治理策略
- goal_alignment — 目标对齐

注：``explainability`` 属于 ADR-016 阶段 7 产物，归 ``workflows`` 域。
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    cost_budget,
    goal_alignment,
    governance,
    skills,
)


def register(app: FastAPI) -> None:
    """注册治理域路由."""
    # 技能注册 / 成本预算 / 通用治理 / 目标对齐
    app.include_router(skills.router)
    app.include_router(cost_budget.router)
    app.include_router(governance.router)
    app.include_router(goal_alignment.router)
