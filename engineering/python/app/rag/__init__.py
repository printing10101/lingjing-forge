"""RAG 检索增强生成模块。

统一导出 RAG pipeline 各核心组件，便于外部模块以
``from app.rag import RagRetrievalEngine, RetrievalEvaluator`` 的方式引用，
避免散落的 ``from app.rag.xxx import Yyy`` 语句导致依赖耦合。

子模块概览：
- ``rag_retrieval``  : 多源分层检索主引擎（v2 pipeline + 并行检索 + LRU 缓存）
- ``knowledge_base`` : ChromaDB 向量知识库封装
- ``embeddings``     : BGE embedding 模型管理（支持 small/base/large 预设）
- ``reranker``       : 三级重排序服务（Cross-Encoder → BM25 → 词重叠）
- ``hybrid_search``  : BM25 + Vector RRF 混合检索融合
- ``query_rewriter`` : LLM 查询改写 + HyDE 文档生成
- ``evaluation``     : 检索质量评估体系（含 ablation study 与 A/B 对比报告）
- ``routes``         : FastAPI 路由（v1 基础 CRUD + v2 增强 API）
- ``document_importer`` : 多格式文档导入服务
- ``vector_store``   : ChromaDB 底层存储与备份
"""

from __future__ import annotations

# 主引擎与评估器（最常用，直接导出）
from app.rag.rag_retrieval import (
    RagRetrievalEngine,
    QueryIntent,
    RetrievalRule,
    RETRIEVAL_RULES,
    INTENT_KEYWORDS,
)
from app.rag.evaluation import (
    RetrievalEvaluator,
    EvaluationDataset,
    EvaluationQuery,
    EvaluationResult,
    EvaluationReport,
    AblationResult,
    ComparisonReport,
)

# 增强组件（懒加载，仅在显式引用时才触发模型加载）
from app.dependencies import get_embedding_service
from app.rag.embeddings import EmbeddingService
from app.rag.reranker import RerankerService, get_reranker_service
from app.rag.hybrid_search import (
    BM25Index,
    reciprocal_rank_fusion,
    HybridSearchEngine,
    get_hybrid_search_engine,
)
from app.dependencies import get_knowledge_base
from app.rag.knowledge_base import KnowledgeBase

# 查询改写器（导入即触发 LLM client 懒加载，不阻塞 __init__）
try:
    from app.rag.query_rewriter import (
        QueryRewriter,
        get_query_rewriter,
    )
except ImportError:  # pragma: no cover - 仅在 LLM 依赖缺失时发生
    QueryRewriter = None  # type: ignore[assignment,misc]
    get_query_rewriter = None  # type: ignore[assignment]


__all__ = [
    # 主引擎
    "RagRetrievalEngine",
    "QueryIntent",
    "RetrievalRule",
    "RETRIEVAL_RULES",
    "INTENT_KEYWORDS",
    # 评估
    "RetrievalEvaluator",
    "EvaluationDataset",
    "EvaluationQuery",
    "EvaluationResult",
    "EvaluationReport",
    "AblationResult",
    "ComparisonReport",
    # Embedding
    "EmbeddingService",
    "get_embedding_service",
    # Reranker
    "RerankerService",
    "get_reranker_service",
    # Hybrid Search
    "BM25Index",
    "reciprocal_rank_fusion",
    "HybridSearchEngine",
    "get_hybrid_search_engine",
    # Knowledge Base
    "KnowledgeBase",
    "get_knowledge_base",
    # Query Rewriter（可能为 None，依赖未安装时降级）
    "QueryRewriter",
    "get_query_rewriter",
]
