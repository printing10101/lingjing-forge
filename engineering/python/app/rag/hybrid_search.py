"""混合检索引擎：BM25 关键词检索 + 向量语义检索融合。

使用 Reciprocal Rank Fusion (RRF) 算法融合两路检索结果，
在保持精确匹配能力的同时兼顾语义相似性。

优化要点：
- 共享 jieba 分词器（reranker 与 hybrid_search 使用同一分词行为）
- BM25 索引懒构建 + 增量更新标记，避免每次查询都重建
- RRF k 参数从 60 调整为 40（更强调头部结果，对 top-k 检索更敏感）
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# RRF 超参数：k 越大，排名差异对融合分数的影响越小
# 优化：从 60 调整为 40，使头部排名差异更显著（更适合 top-k 检索）
DEFAULT_RRF_K = 40
# 默认权重：向量检索 0.6，BM25 检索 0.4
DEFAULT_VECTOR_WEIGHT = 0.6
DEFAULT_BM25_WEIGHT = 0.4


class BM25Index:
    """基于 rank_bm25 的内存 BM25 索引。

    支持增量更新和按 source 过滤查询。

    优化：
    - 使用共享 jieba 分词器，与 reranker 保持分词一致性
    - 索引增量构建标记，仅在文档变更时重建
    """

    def __init__(self):
        self._docs: list[dict[str, Any]] = []
        self._tokenized_docs: list[list[str]] = []
        self._bm25 = None
        self._lock = threading.Lock()
        self._dirty = True
        # 性能统计
        self._query_count = 0
        self._index_rebuild_count = 0

    def add_documents(self, documents: list[dict[str, Any]]):
        """批量添加文档到 BM25 索引。"""
        from app.rag.tokenizer import tokenize

        with self._lock:
            for doc in documents:
                self._docs.append(doc)
                # 使用共享 jieba 分词器
                self._tokenized_docs.append(tokenize(doc.get("document", "")))
            self._dirty = True

    def clear(self):
        """清空索引。"""
        with self._lock:
            self._docs.clear()
            self._tokenized_docs.clear()
            self._bm25 = None
            self._dirty = True

    def search(
        self,
        query: str,
        top_k: int = 10,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 关键词检索。

        Args:
            query: 查询文本
            top_k: 返回结果数
            source_filter: 按 source 过滤

        Returns:
            排序后的结果列表，每项增加 'bm25_score' 字段
        """
        if not self._docs:
            return []

        self._ensure_index()
        if self._bm25 is None:
            return []

        from app.rag.tokenizer import tokenize

        # 使用共享 jieba 分词器
        tokenized_query = tokenize(query)
        if not tokenized_query:
            return []

        self._query_count += 1
        scores = self._bm25.get_scores(tokenized_query)

        # 构建 (index, score) 并过滤
        scored: list[tuple[int, float]] = []
        for i, score in enumerate(scores):
            if score <= 0:
                continue
            if source_filter:
                doc_source = self._docs[i].get("metadata", {}).get("source")
                if doc_source != source_filter:
                    continue
            scored.append((i, float(score)))

        scored.sort(key=lambda x: x[1], reverse=True)

        results = []
        for rank, (idx, score) in enumerate(scored[:top_k]):
            result = dict(self._docs[idx])
            result["bm25_score"] = score
            result["bm25_rank"] = rank
            results.append(result)
        return results

    def _ensure_index(self):
        """懒构建 BM25 索引（仅在 _dirty 标记为 True 时重建）。"""
        if not self._dirty and self._bm25 is not None:
            return
        with self._lock:
            if not self._dirty and self._bm25 is not None:
                return
            if not self._tokenized_docs:
                self._bm25 = None
                self._dirty = False
                return
            try:
                from rank_bm25 import BM25Okapi

                self._bm25 = BM25Okapi(self._tokenized_docs)
                self._index_rebuild_count += 1
                logger.info(
                    "BM25 index built with %d documents (rebuild #%d)",
                    len(self._tokenized_docs),
                    self._index_rebuild_count,
                )
            except ImportError:
                logger.warning("rank_bm25 not installed, BM25 search disabled. Install with: pip install rank_bm25")
                self._bm25 = None
            except (ValueError, RuntimeError) as e:
                logger.warning("BM25 index build failed: %s", e, exc_info=True)
                self._bm25 = None
            self._dirty = False

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文分词（兼容旧调用入口）。

        委托到共享 tokenizer 模块（jieba 优先，字符级 fallback）。
        """
        from app.rag.tokenizer import tokenize

        return tokenize(text)

    @property
    def size(self) -> int:
        return len(self._docs)


def reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    k: int = DEFAULT_RRF_K,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    bm25_weight: float = DEFAULT_BM25_WEIGHT,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Reciprocal Rank Fusion (RRF) 融合两路检索结果。

    RRF 公式: score(d) = Σ w_i / (k + rank_i(d))

    Args:
        vector_results: 向量检索结果
        bm25_results: BM25 检索结果
        k: RRF 平滑参数
        vector_weight: 向量检索权重
        bm25_weight: BM25 检索权重
        top_k: 返回前 K 条

    Returns:
        融合后的结果列表
    """
    # 构建 doc_id -> 排名 映射
    vector_rank: dict[str, int] = {}
    for rank, result in enumerate(vector_results):
        doc_id = result.get("id") or result.get("document", "")[:100]
        vector_rank[doc_id] = rank

    bm25_rank: dict[str, int] = {}
    for rank, result in enumerate(bm25_results):
        doc_id = result.get("id") or result.get("document", "")[:100]
        bm25_rank[doc_id] = rank

    # 收集所有文档
    all_docs: dict[str, dict[str, Any]] = {}
    for result in vector_results:
        doc_id = result.get("id") or result.get("document", "")[:100]
        all_docs[doc_id] = result
    for result in bm25_results:
        doc_id = result.get("id") or result.get("document", "")[:100]
        if doc_id not in all_docs:
            all_docs[doc_id] = result

    # 计算 RRF 分数
    scored: list[tuple[str, float]] = []
    for doc_id, doc in all_docs.items():
        score = 0.0
        if doc_id in vector_rank:
            score += vector_weight / (k + vector_rank[doc_id] + 1)
        if doc_id in bm25_rank:
            score += bm25_weight / (k + bm25_rank[doc_id] + 1)
        scored.append((doc_id, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    results = []
    for doc_id, rrf_score in scored[:top_k]:
        result = dict(all_docs[doc_id])
        result["rrf_score"] = round(rrf_score, 6)
        result["in_vector"] = doc_id in vector_rank
        result["in_bm25"] = doc_id in bm25_rank
        results.append(result)

    return results


class HybridSearchEngine:
    """混合检索引擎：BM25 + 向量检索融合。

    自动管理 BM25 索引，并提供 RRF 融合接口。
    """

    def __init__(
        self,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        bm25_weight: float = DEFAULT_BM25_WEIGHT,
        rrf_k: int = DEFAULT_RRF_K,
    ):
        self._bm25_index = BM25Index()
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight
        self._rrf_k = rrf_k
        self._initialized = False

    def index_documents(self, documents: list[dict[str, Any]]):
        """索引文档（批量）。"""
        self._bm25_index.add_documents(documents)
        self._initialized = True
        logger.info("Hybrid search engine indexed %d documents", len(documents))

    def search(
        self,
        query: str,
        vector_results: list[dict[str, Any]],
        top_k: int = 10,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """执行混合检索。

        Args:
            query: 查询文本
            vector_results: 已完成的向量检索结果
            top_k: 返回结果数
            source_filter: 按 source 过滤 BM25 结果

        Returns:
            融合后的结果列表
        """
        # BM25 检索
        bm25_results = self._bm25_index.search(query, top_k=top_k * 2, source_filter=source_filter)

        if not bm25_results:
            # 无 BM25 结果时直接返回向量结果
            return vector_results[:top_k]

        if not vector_results:
            return bm25_results[:top_k]

        # RRF 融合
        return reciprocal_rank_fusion(
            vector_results=vector_results,
            bm25_results=bm25_results,
            k=self._rrf_k,
            vector_weight=self._vector_weight,
            bm25_weight=self._bm25_weight,
            top_k=top_k,
        )

    def rebuild_index(self, documents: list[dict[str, Any]]):
        """重建 BM25 索引。"""
        self._bm25_index.clear()
        self._bm25_index.add_documents(documents)
        logger.info("BM25 index rebuilt with %d documents", len(documents))

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def bm25_index_size(self) -> int:
        return self._bm25_index.size

    def get_stats(self) -> dict[str, Any]:
        from app.rag.tokenizer import get_tokenizer_info

        return {
            "bm25_index_size": self._bm25_index.size,
            "vector_weight": self._vector_weight,
            "bm25_weight": self._bm25_weight,
            "rrf_k": self._rrf_k,
            "initialized": self._initialized,
            "bm25_query_count": self._bm25_index._query_count,
            "bm25_rebuild_count": self._bm25_index._index_rebuild_count,
            "tokenizer": get_tokenizer_info(),
        }


# 线程安全懒加载单例


class _HybridSearchHolder:
    """Thread-safe lazy holder for the :class:`HybridSearchEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: HybridSearchEngine | None = None

    def get(self) -> HybridSearchEngine:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is not None:
                return self._instance
            self._instance = HybridSearchEngine()
            logger.info("Initialized hybrid search engine")
            return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None


_holder = _HybridSearchHolder()


def get_hybrid_search_engine() -> HybridSearchEngine:
    """获取共享的 :class:`HybridSearchEngine` 单例。"""
    return _holder.get()


__all__ = [
    "BM25Index",
    "HybridSearchEngine",
    "reciprocal_rank_fusion",
    "get_hybrid_search_engine",
    "DEFAULT_RRF_K",
    "DEFAULT_VECTOR_WEIGHT",
    "DEFAULT_BM25_WEIGHT",
]
