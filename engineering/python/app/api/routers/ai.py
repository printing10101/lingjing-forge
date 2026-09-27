"""AI 域路由注册：LNN / RAG / Ollama / LLM Provider / SHARP / 工艺理解 / 动态调参 / 信号融合 / 知识图谱 / 颤振磨损预测.

注册模块：
- LNN 不确定性量化（lnn_uncertain）
- 刀具磨损预测（wear_prediction）
- RAG 检索增强生成（rag.routes）
- Ollama 本地 LLM（条件注册，依赖 ollama 包 + config.hardware.skip_ollama）
- LLM Provider 网关（多后端 LLM 管理）
- SHARP 三元组验证智能体
- 工艺 / NC 代码对话式解释（LLM 驱动，含多轮会话）
- 工艺理解（process_understanding.routes）
- 刀路动态调参闭环
- 多源信号融合知识库
- 知识图谱

设计约束：
- Ollama 模块为条件注册：依赖可选库 ollama 且 config.hardware.skip_ollama=False
- _OLLAMA_AVAILABLE 标志位由 router_registry 定义并传递
"""

from __future__ import annotations

from fastapi import FastAPI

from app.api.v1 import (
    dynamic_adjustment as dynamic_adjustment_routes,
    embedding_retrieval,
    knowledge_graph as knowledge_graph_routes,
    llm_providers,
    llm_stream,
    lnn_uncertain,
    process_explainer as process_explainer_routes,
    sharp as sharp_routes,
    signal_fusion_kb as signal_fusion_kb_routes,
    wear_prediction,
)

# V2.7.0 子路由拆分后，LNN 主路由聚合器（health/models/predict/train/tasks 等
# 60+ 端点）位于 app.api.v1.lnn.routes；lnn_uncertain.py 是独立的不确定性端点。
# 修复：二者都注册，避免聚合器路由从未挂载（重构遗漏）。
from app.api.v1.lnn.routes import router as lnn_routes_router
from app.ai.process_understanding import routes as process_understanding_routes
from app.rag import routes as rag_routes


def register(app: FastAPI, *, ollama_available: bool = False) -> None:
    """注册 AI 域路由.

    Args:
        app: FastAPI 应用实例
        ollama_available: Ollama 模块是否可用（由 router_registry 传入）
    """
    # LNN 不确定性 / 磨损预测
    app.include_router(lnn_routes_router)  # LNN 主聚合器（60+ 端点）
    app.include_router(lnn_uncertain.router)  # 独立不确定性端点
    app.include_router(wear_prediction.router)

    # RAG
    app.include_router(rag_routes.router)

    # Ollama（条件注册）
    if ollama_available:
        from app.ai import ollama_routes

        app.include_router(ollama_routes.router)

    # LLM Provider 网关
    app.include_router(llm_providers.router)

    # LLM 流式对话（SSE，2026-09 全量升格）
    app.include_router(llm_stream.router)

    # 统一制造语义嵌入检索（W8 出口，2026-09 全量升格）
    app.include_router(embedding_retrieval.router)

    # SHARP 三元组验证智能体
    app.include_router(sharp_routes.router)

    # 工艺 / NC 代码对话式解释
    app.include_router(process_explainer_routes.router)

    # 工艺理解
    app.include_router(process_understanding_routes.router)

    # 刀路动态调参闭环
    app.include_router(dynamic_adjustment_routes.router)

    # 多源信号融合知识库
    app.include_router(signal_fusion_kb_routes.router)

    # 知识图谱
    app.include_router(knowledge_graph_routes.router)
