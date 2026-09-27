"""工艺 / NC 代码解释模块（LLM 对话式）。

落地竞品分析中识别的 SolidWorks AURA 式对话解释补强点。

子模块：
    - prompts: 系统与用户 Prompt 模板
    - session_store: SQLite 持久化的多轮对话历史
    - explainer: 主解释引擎（LLM + 规则化降级）
"""

from __future__ import annotations

from app.ai.process_explainer.explainer import (
    ExplanationResult,
    ProcessExplainer,
    get_process_explainer,
)
from app.ai.process_explainer.session_store import (
    ChatMessage,
    SessionStore,
    get_session_store,
)

__all__ = [
    "ExplanationResult",
    "ProcessExplainer",
    "get_process_explainer",
    "ChatMessage",
    "SessionStore",
    "get_session_store",
]
