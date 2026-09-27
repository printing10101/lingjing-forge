"""工程域路由注册.

聚合：
- simulation        — 仿真（含 chatter / cutting_force）
- chatter           — 颤振仿真
- cutting_force     — 切削力仿真
- collision_check   — 碰撞检查（CAM API）
- tools             — 刀具管理（CAM API）
- project_routes    — 项目管理
- step_import_api   — STEP 文件导入
- rules_router      — 规则引擎
- dxf_pipeline_routes — DXF 流水线
- nl2cad_router     — NL-to-CAD 自然语言建模

注：``process_explainer`` / ``process_understanding`` / ``dynamic_adjustment`` /
``signal_fusion_kb`` / ``knowledge_graph`` 等 AI 驱动路由在 ``ai.py`` 中注册，
避免双重注册。本域仅含纯工程侧路由（仿真 / 项目 / STEP / 规则 / DXF / NL2CAD / CAM）。
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    collision_check,
    dxf_pipeline as dxf_pipeline_routes,
    postprocessor_dialects,
    tools,
    monitor_ws,
    optimizer_routes,
)
from app.api.v1.cutting_experience.routes import router as cutting_experience_router
from app.api.v1.nl2cad.routes import router as nl2cad_router
from app.projects import project_api as project_routes
from app.rules import router as rules_router
from app.simulation import api as simulation_api
from app.simulation.chatter import api as chatter_api
from app.simulation.cutting_force import api as cutting_force_api
from app.step_import import api as step_import_api


def register(app: FastAPI) -> None:
    """注册工程域路由."""
    # === 仿真（含 chatter / cutting_force）===
    app.include_router(simulation_api.router)
    app.include_router(chatter_api.router)
    app.include_router(cutting_force_api.router)

    # 项目 / STEP / 规则
    app.include_router(project_routes.router)
    app.include_router(step_import_api.router)
    app.include_router(rules_router)

    # DXF 流水线
    app.include_router(dxf_pipeline_routes.router)

    # CAM APIs
    app.include_router(collision_check.router)
    app.include_router(tools.router)

    # NL-to-CAD 自然语言建模
    app.include_router(nl2cad_router)

    # === 后处理器方言管理（P3）===
    app.include_router(postprocessor_dialects.router)

    # === 数据飞轮 / 参数优化 / 实时监控（功能缺口接线）===
    # P2-3：cutting_experience 统一采集 API（RBAC + 统一异常体系，前缀 /api/v1/experience）
    app.include_router(cutting_experience_router)
    app.include_router(optimizer_routes.router)
    app.include_router(monitor_ws.router)
