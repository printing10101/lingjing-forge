"""自进化域路由注册.

演化循环（统计/提案/门控发布）的人工操作面，与心跳 cron、
workflow 模板共用同一 EvolutionEngine。
"""

from __future__ import annotations

from fastapi import FastAPI


def register(app: FastAPI) -> None:
    """注册自进化域路由."""
    from app.api.v1 import evolution

    app.include_router(evolution.router)
