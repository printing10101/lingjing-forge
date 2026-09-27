"""任务域路由注册：agent_gateway / agent_state / jobs / heartbeat / task_checkout.

注册模块：
- Agent 网关（agent_gateway，SSE 流式推理）
- Agent 状态管理（agent_state，P0-8 修复：补齐 /api/agents/* 路由）
- 异步任务系统（jobs）
- 心跳检测（heartbeat）
- 任务签出（task_checkout，防止多实例重复执行）

设计约束：
- agent_state 路由必须注册（P0-8 修复：补齐 /api/agents/* 路由）
- agent_gateway 依赖 SSE 管理器
- task_checkout 用于多实例环境下的任务互斥

注：flywheel 在原 registry 中归属模板系统块，已迁移至 templates 领域
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    agent_state as agent_state_routes,
    heartbeat,
    jobs,
    task_checkout,
)
from app.api.v1.agent_gateway import router as agent_gateway_router


def register(app: FastAPI) -> None:
    """注册任务域路由."""
    # Agent 网关（SSE 流式推理）
    app.include_router(agent_gateway_router)
    # P0-8 修复：注册 agent_state 路由（/api/agents/*）
    app.include_router(agent_state_routes.router)
    app.include_router(jobs.router)
    app.include_router(heartbeat.router)
    app.include_router(task_checkout.router)
