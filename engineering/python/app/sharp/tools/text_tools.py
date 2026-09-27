"""SHARP 文本工具集（M2.3）。

封装 `RagRetrievalEngine`，提供 2 个文本检索工具供 ReAct 循环调用。

工具
----
- `text.retrieve`      文档检索（基于 RAG 引擎完整 pipeline）
- `text.entity_lookup` 实体倒排索引查询（基于查询实体提取）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.sharp.tools.base import BaseTool

logger = logging.getLogger(__name__)


# 工具实现


class TextRetrieveTool(BaseTool):
    """文档检索工具。

    工具名：`text.retrieve`
    调用 `RagRetrievalEngine.retrieve(query, n_results)` 检索相关文档片段。
    """

    def __init__(self, rag_engine) -> None:
        """Args:
        rag_engine: `RagRetrievalEngine` 实例
        """
        self._engine = rag_engine

    @property
    def name(self) -> str:
        return "text.retrieve"

    @property
    def description(self) -> str:
        return (
            "从知识库检索与查询相关的文档片段，支持多源混合检索与重排序。"
            "用于获取支撑三元组验证的外部文本证据（如工艺手册、论文、实测记录）"
        )

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "query": "检索查询文本，建议包含实体名与关系语义",
            "n_results": "返回结果数量，默认 5",
            "override_source": "覆盖检索源，如 'uniwear-phm2010' / 'bosch_cnc'（可选）",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        query = arguments.get("query")
        if not query:
            raise ValueError("query 参数不能为空")
        n_results = int(arguments.get("n_results", 5))
        override_source = arguments.get("override_source")

        # 调用 RAG 引擎（同步接口，用 to_thread 包装）
        result = await asyncio.to_thread(
            self._engine.retrieve,
            query,
            None,  # intent=None 让引擎自动检测
            n_results,
            override_source,
        )
        # 提取关键字段，避免返回过大对象
        results = result.get("results", []) if isinstance(result, dict) else []
        simplified = []
        for r in results[:n_results]:
            simplified.append(
                {
                    "content": (r.get("content", "") or "")[:500],  # 截断长文本
                    "source": r.get("source", r.get("_retrieval_source_filter", "")),
                    "score": r.get("score", r.get("rerank_score", 0.0)),
                    "metadata": {
                        k: v
                        for k, v in r.items()
                        if k not in ("content", "source", "score", "rerank_score")
                        and isinstance(v, (str, int, float, bool))
                    },
                }
            )
        return {
            "query": query,
            "results": simplified,
            "count": len(simplified),
            "intent": result.get("intent") if isinstance(result, dict) else None,
            "cache_hit": result.get("_cache_hit", False) if isinstance(result, dict) else False,
        }


class TextEntityLookupTool(BaseTool):
    """实体倒排索引查询工具。

    工具名：`text.entity_lookup`
    从查询文本中提取制造领域实体（TC4/HRC52/振动等），
    返回实体列表供 ReAct 循环判断实体识别准确性。
    """

    def __init__(self, rag_engine) -> None:
        self._engine = rag_engine

    @property
    def name(self) -> str:
        return "text.entity_lookup"

    @property
    def description(self) -> str:
        return (
            "从查询文本中提取制造领域实体（材料牌号、刀具型号、信号类型等），用于校验三元组中的实体是否为领域标准术语"
        )

    @property
    def arguments_schema(self) -> dict[str, str]:
        return {
            "query": "待提取实体的文本，通常是三元组的自然语言描述",
        }

    async def _execute(self, arguments: dict[str, Any]) -> Any:
        query = arguments.get("query")
        if not query:
            raise ValueError("query 参数不能为空")

        # 复用 RAG 引擎的实体提取函数（懒导入）
        try:
            from app.rag.rag_retrieval import _extract_query_entities
        except ImportError as e:
            raise RuntimeError(f"无法导入实体提取函数: {e}") from e

        entities = await asyncio.to_thread(_extract_query_entities, query)
        return {
            "query": query,
            "entities": entities,
            "count": len(entities),
        }


__all__ = ["TextRetrieveTool", "TextEntityLookupTool"]
