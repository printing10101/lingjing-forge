"""
知识检索模块

v2 架构升级：作为 RagRetrievalEngine 的统一入口适配层。
- 保留原有 HybridRetrievalResult / RetrievalDocument 接口，向后兼容
- 将 TaskType 映射为 QueryIntent + pipeline_level，委托给 RagRetrievalEngine
- 启用查询改写 / HyDE / 混合检索 / Cross-Encoder 重排序等增强能力
- 同步 RagRetrievalEngine 调用通过 asyncio.to_thread 放入线程池，避免阻塞事件循环
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ai.process_understanding.task_classifier import TaskType

logger = logging.getLogger(__name__)

# RAG 检索 top_k 兜底默认值（当 task_type 未命中 TASK_TYPE_DEFAULT_TOP_K 时使用）
DEFAULT_RAG_TOP_K = 5


class RetrievalStrategy(Enum):
    VECTOR = "vector"
    KEYWORD = "keyword"
    HYBRID = "hybrid"


@dataclass
class RetrievalDocument:
    """检索文档"""

    id: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    vector_score: float = 0.0
    keyword_score: float = 0.0
    final_score: float = 0.0
    source: str = "unknown"


@dataclass
class HybridRetrievalResult:
    """混合检索结果"""

    query: str
    task_type: TaskType
    strategy: RetrievalStrategy
    documents: list[RetrievalDocument] = field(default_factory=list)
    total_candidates: int = 0
    latency_ms: float = 0.0


# TaskType QueryIntent 映射（统一两套检索系统）
# A-工艺咨询：聚焦切削参数/刀具选择 CUTTING_PARAMS
# B-故障诊断：聚焦振动/磨损/异常 VIBRATION_WEAR
# C-方案生成：需要跨源综合数据 CROSS_SOURCE
# D-知识查询：通用知识检索 GENERAL
# E-闲聊：无需增强 pipeline GENERAL (fast)
TASK_TYPE_TO_QUERY_INTENT: dict[TaskType, str] = {
    TaskType.PROCESS_CONSULT: "cutting_params",
    TaskType.FAULT_DIAGNOSIS: "vibration_wear",
    TaskType.SOLUTION_GENERATION: "cross_source",
    TaskType.KNOWLEDGE_QUERY: "general",
    TaskType.CHITCHAT: "general",
}

# pipeline 分级：fast 跳过 reranker/HyDE；standard 启用混合检索+reranker；full 启用全部
TASK_TYPE_TO_PIPELINE_LEVEL: dict[TaskType, str] = {
    TaskType.CHITCHAT: "fast",
    TaskType.PROCESS_CONSULT: "standard",
    TaskType.KNOWLEDGE_QUERY: "standard",
    TaskType.FAULT_DIAGNOSIS: "full",
    TaskType.SOLUTION_GENERATION: "full",
}

# TaskType top_k 默认值（保留原 RETRIEVAL_WEIGHTS 语义）
TASK_TYPE_DEFAULT_TOP_K: dict[TaskType, int] = {
    TaskType.PROCESS_CONSULT: 5,
    TaskType.FAULT_DIAGNOSIS: 5,
    TaskType.SOLUTION_GENERATION: 8,
    TaskType.KNOWLEDGE_QUERY: 5,
    TaskType.CHITCHAT: 3,
}


# RagRetrievalEngine 懒加载单例（线程安全）

_rag_engine_instance: Any = None
_rag_engine_lock = threading.Lock()


def _get_rag_engine() -> Any:
    """懒加载 RagRetrievalEngine 单例。

    避免在模块导入时初始化重型组件（reranker / hybrid_search / query_rewriter）。
    """
    global _rag_engine_instance
    if _rag_engine_instance is not None:
        return _rag_engine_instance
    with _rag_engine_lock:
        if _rag_engine_instance is not None:
            return _rag_engine_instance
        from app.dependencies import get_knowledge_base
        from app.rag.rag_retrieval import RagRetrievalEngine
        from app.rag.signal_fusion_kb import get_signal_fusion_kb

        kb = get_knowledge_base()
        # 集成点 2：显式注入 signal_fusion_kb（与 app/rag/routes.py 保持一致）
        _rag_engine_instance = RagRetrievalEngine(
            knowledge_base=kb,
            signal_fusion_kb=get_signal_fusion_kb(),
        )
        logger.info("KnowledgeRetriever: RagRetrievalEngine singleton initialized (signal_fusion_kb injected)")
    return _rag_engine_instance


class KnowledgeRetriever:
    """混合检索器（v2：委托给 RagRetrievalEngine）。

    架构升级说明：
    - 原 v1 实现：独立的 vector/keyword/reranking pipeline，与 RagRetrievalEngine 并行存在
    - v2 实现：委托给 RagRetrievalEngine，统一启用查询改写、混合检索、Cross-Encoder 重排序
    - 保留 async retrieve() 接口，向后兼容 process_understanding/engine.py
    - 通过 asyncio.to_thread 将同步 ChromaDB 调用放入线程池，不阻塞事件循环
    - pipeline_level 控制：CHITCHAT 走 fast 路径，跳过增强模块以降低延迟
    """

    def __init__(self):
        self._rag_engine: Any = None
        self._total_queries = 0
        self._total_latency_ms = 0.0
        self._delegation_success = 0
        self._delegation_fallback = 0

    def _ensure_engine(self) -> Any:
        """懒加载 RagRetrievalEngine。"""
        if self._rag_engine is None:
            self._rag_engine = _get_rag_engine()
        return self._rag_engine

    async def retrieve(
        self,
        query: str,
        task_type: TaskType = TaskType.KNOWLEDGE_QUERY,
        top_k: int | None = None,
    ) -> HybridRetrievalResult:
        """执行混合检索（委托给 RagRetrievalEngine）。

        Args:
            query: 检索查询文本
            task_type: 任务类型（用于映射到 QueryIntent 和 pipeline_level）
            top_k: 返回文档数量上限

        Returns:
            HybridRetrievalResult 包含检索到的文档列表
        """
        start_time = time.perf_counter()
        self._total_queries += 1

        actual_top_k = top_k or TASK_TYPE_DEFAULT_TOP_K.get(task_type, DEFAULT_RAG_TOP_K)
        intent_value = TASK_TYPE_TO_QUERY_INTENT.get(task_type, "general")
        pipeline_level = TASK_TYPE_TO_PIPELINE_LEVEL.get(task_type, "standard")

        try:
            engine = self._ensure_engine()

            # 将同步 retrieve() 调用放入线程池，避免阻塞事件循环
            # fast pipeline：临时关闭增强模块以降低延迟
            result_dict = await asyncio.to_thread(
                self._invoke_engine,
                engine,
                query,
                intent_value,
                actual_top_k,
                pipeline_level,
            )

            self._delegation_success += 1
            hybrid_result = self._convert_to_hybrid_result(result_dict, query, task_type)

        except (RuntimeError, OSError, ValueError, KeyError, ImportError) as e:
            # 委托失败时降级为直接 kb.query()，保证可用性
            self._delegation_fallback += 1
            logger.warning(
                "KnowledgeRetriever delegation failed, fallback to direct kb query: %s",
                e,
                exc_info=True,
            )
            hybrid_result = await self._fallback_retrieve(query, task_type, actual_top_k)

        elapsed = (time.perf_counter() - start_time) * 1000
        self._total_latency_ms += elapsed
        hybrid_result.latency_ms = elapsed

        logger.info(
            "检索完成: query='%s', task=%s, intent=%s, pipeline=%s, candidates=%d, results=%d, %.1fms",
            query[:50],
            task_type.label,
            intent_value,
            pipeline_level,
            hybrid_result.total_candidates,
            len(hybrid_result.documents),
            elapsed,
        )

        return hybrid_result

    @staticmethod
    def _invoke_engine(
        engine: Any,
        query: str,
        intent_value: str,
        n_results: int,
        pipeline_level: str,
    ) -> dict:
        """在线程池中执行 RagRetrievalEngine.retrieve()。

        fast pipeline：临时禁用 query_rewrite / hyde / reranker 以降低延迟。
        """
        from app.rag.rag_retrieval import QueryIntent

        # 解析 intent 字符串到枚举
        try:
            intent_enum = QueryIntent(intent_value)
        except ValueError:
            intent_enum = QueryIntent.GENERAL

        # fast pipeline：临时关闭增强模块
        if pipeline_level == "fast":
            import app.rag.rag_retrieval as rag_mod

            original_flags = {
                "rewrite": rag_mod.ENABLE_QUERY_REWRITE,
                "hyde": rag_mod.ENABLE_HYDE,
                "reranker": rag_mod.ENABLE_RERANKER,
            }
            rag_mod.ENABLE_QUERY_REWRITE = False
            rag_mod.ENABLE_HYDE = False
            rag_mod.ENABLE_RERANKER = False
            try:
                return engine.retrieve(query=query, intent=intent_enum, n_results=n_results)
            finally:
                rag_mod.ENABLE_QUERY_REWRITE = original_flags["rewrite"]
                rag_mod.ENABLE_HYDE = original_flags["hyde"]
                rag_mod.ENABLE_RERANKER = original_flags["reranker"]

        return engine.retrieve(query=query, intent=intent_enum, n_results=n_results)

    @staticmethod
    def _convert_to_hybrid_result(
        result_dict: dict,
        query: str,
        task_type: TaskType,
    ) -> HybridRetrievalResult:
        """将 RagRetrievalEngine 的 dict 结果转换为 HybridRetrievalResult。

        保留 rerank_score / rrf_score 作为 final_score，
        向量距离转换为 vector_score。
        """
        documents: list[RetrievalDocument] = []
        results_list = result_dict.get("results", []) or []

        for item in results_list:
            if not isinstance(item, dict):
                continue

            doc_id = str(item.get("id", "") or "")
            content = item.get("document", "") or ""
            metadata = item.get("metadata", {}) or {}
            distance = item.get("distance")
            source = metadata.get("source", "unknown")

            # 向量得分：距离越小越好
            vector_score = 0.0
            if distance is not None:
                try:
                    # 学术诚信修复：ChromaDB 使用 cosine 距离（distance ∈ [0,2]），
                    # 原 1.0 - min(distance, 1.0) 在 distance>1 时截断为 0，丢失区分度；
                    # 改为标准 cosine 归一化 1.0 - distance/2.0
                    vector_score = 1.0 - float(distance) / 2.0
                except (TypeError, ValueError):
                    vector_score = 0.0

            # final_score：优先 reranker > rrf > vector
            final_score = 0.0
            if "rerank_score" in item:
                try:
                    final_score = float(item["rerank_score"])
                except (TypeError, ValueError):
                    final_score = vector_score
            elif "rrf_score" in item:
                try:
                    final_score = float(item["rrf_score"])
                except (TypeError, ValueError):
                    final_score = vector_score
            else:
                final_score = vector_score

            documents.append(
                RetrievalDocument(
                    id=doc_id,
                    content=content,
                    metadata=metadata,
                    vector_score=vector_score,
                    keyword_score=0.0,
                    final_score=final_score,
                    source=source,
                )
            )

        return HybridRetrievalResult(
            query=query,
            task_type=task_type,
            strategy=RetrievalStrategy.HYBRID,
            documents=documents,
            total_candidates=result_dict.get("total_found", len(documents)),
            latency_ms=0.0,  # 由调用方设置
        )

    async def _fallback_retrieve(
        self,
        query: str,
        task_type: TaskType,
        top_k: int,
    ) -> HybridRetrievalResult:
        """降级路径：直接调用 kb.query()，保证委托失败时仍可用。"""
        try:
            from app.dependencies import get_knowledge_base

            kb = get_knowledge_base()
            raw = await asyncio.to_thread(kb.query, query_text=query, n_results=top_k)
        except (RuntimeError, OSError, ValueError, ImportError) as e:
            logger.error("Fallback retrieve also failed: %s", e, exc_info=True)
            return HybridRetrievalResult(
                query=query,
                task_type=task_type,
                strategy=RetrievalStrategy.HYBRID,
                documents=[],
                total_candidates=0,
                latency_ms=0.0,
            )

        documents: list[RetrievalDocument] = []
        docs_raw = raw.get("documents", []) if isinstance(raw, dict) else []
        for doc in docs_raw:
            if isinstance(doc, dict):
                content = doc.get("document", "")
                metadata = doc.get("metadata", {}) or {}
                doc_id = str(doc.get("id", "") or "")
                distance = doc.get("distance", 0.5)
            else:
                content = str(doc)
                metadata = {}
                doc_id = ""
                distance = 0.5

            try:
                vector_score = 1.0 - min(float(distance), 1.0)
            except (TypeError, ValueError):
                vector_score = 0.0

            documents.append(
                RetrievalDocument(
                    id=doc_id,
                    content=content,
                    metadata=metadata,
                    vector_score=vector_score,
                    final_score=vector_score,
                    source=metadata.get("source", "unknown"),
                )
            )

        return HybridRetrievalResult(
            query=query,
            task_type=task_type,
            strategy=RetrievalStrategy.VECTOR,
            documents=documents,
            total_candidates=len(documents),
            latency_ms=0.0,
        )

    def get_stats(self) -> dict[str, Any]:
        """获取检索器性能统计。"""
        return {
            "total_queries": self._total_queries,
            "avg_latency_ms": (self._total_latency_ms / self._total_queries if self._total_queries > 0 else 0.0),
            "delegation_success": self._delegation_success,
            "delegation_fallback": self._delegation_fallback,
            "delegation_success_rate": (
                self._delegation_success / self._total_queries if self._total_queries > 0 else 0.0
            ),
        }
