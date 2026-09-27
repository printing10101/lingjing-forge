"""工作流域路由注册.

聚合 ADR-005 / ADR-010 / ADR-011 / ADR-012 / ADR-015 / ADR-016 / ADR-017 全部产物：
- workflows          — ADR-005 阶段 1：DAG 工作流编排 API
- datasets           — ADR-005 阶段 2：数据集 / 版本 / 血缘 API
- snapshots          — ADR-005 阶段 2：实验快照 / 一键复现 API
- workflow_templates — ADR-010 阶段 6 p6-1：工作流模板市场 API
- project_sync       — ADR-011 阶段 6 p6-2：项目级 Git 同步 API
- resource_cards     — ADR-012 阶段 6 p6-3：资源卡片 API
- project_packages   — ADR-015 阶段 6 p6-4：项目导入导出 API
- explainability     — ADR-016 阶段 7 p7：可解释性可视化 API
- world_model        — ADR-017 阶段 8 p8：世界模型 API
- rl_agent           — ADR-017 阶段 8 p8：RL Agent API
- dreaming           — ADR-021：离线反思 / 规则灰度发布 API（W7.2）
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    datasets,
    dreaming,
    explainability,
    project_packages,
    project_sync,
    resource_cards,
    rl_agent,
    snapshots,
    workflow_templates,
    workflows,
    world_model,
)


def register(app: FastAPI) -> None:
    """注册工作流域路由."""
    # ADR-005 阶段 1：DAG 工作流编排
    app.include_router(workflows.router)

    # ADR-005 阶段 2：数据集 / 版本 / 血缘
    app.include_router(datasets.router)

    # ADR-005 阶段 2：实验快照 / 一键复现
    app.include_router(snapshots.router)

    # ADR-010 阶段 6 p6-1：工作流模板市场
    app.include_router(workflow_templates.router)

    # ADR-011 阶段 6 p6-2：项目级 Git 同步
    app.include_router(project_sync.router)

    # ADR-012 阶段 6 p6-3：资源卡片
    app.include_router(resource_cards.router)

    # ADR-015 阶段 6 p6-4：项目导入导出
    app.include_router(project_packages.router)

    # ADR-016 阶段 7 p7：可解释性可视化
    app.include_router(explainability.router)

    # ADR-017 阶段 8 p8：世界模型 / RL Agent
    app.include_router(world_model.router)
    app.include_router(rl_agent.router)

    # ADR-021：离线反思 / 规则灰度发布（W7.2 最小 API 面）
    app.include_router(dreaming.router)
