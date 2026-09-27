"""Reranker service for knowledge retrieval.

提供多级重排序策略：
1. Cross-Encoder 重排序（默认，使用 BGE-reranker 模型）
2. BM25 关键词重排序（fallback）
3. 词重叠重排序（最低级 fallback）

通过环境变量 RERANKER_MODEL 可配置模型名称。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# 默认 Cross-Encoder 模型（中文优化）
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-base"
# 环境变量覆盖
RERANKER_MODEL = os.getenv("RERANKER_MODEL", DEFAULT_RERANKER_MODEL)
# 是否禁用 Cross-Encoder（设为 "1" 时仅使用词重叠）
DISABLE_CROSS_ENCODER = os.getenv("DISABLE_CROSS_ENCODER", "0") == "1"

# 重排序结果缓存大小
RERANK_CACHE_SIZE = 500


class RerankerService:
    """多级重排序服务。

    优先使用 Cross-Encoder 模型进行语义重排序；
    模型不可用时自动降级为 BM25 关键词重排序；
    最终 fallback 为词重叠评分。

    Attributes:
        enable_cross_encoder: 是否启用 Cross-Encoder。
        _model: 懒加载的 Cross-Encoder 模型实例。
        _cache: 查询结果 LRU 缓存。
    """

    def __init__(self, enable_cross_encoder: bool | None = None):
        if enable_cross_encoder is None:
            self.enable_cross_encoder = not DISABLE_CROSS_ENCODER
        else:
            self.enable_cross_encoder = enable_cross_encoder
        self._model = None
        self._model_lock = threading.Lock()
        self._total_requests = 0
        self._total_time_ms = 0.0
        # LRU 缓存：query+doc_hash -> score
        self._cache: dict[str, float] = {}
        self._cache_keys: list[str] = []
        # 缓存命中统计（用于诊断与调优）
        self._cache_hits = 0
        self._cache_misses = 0

    def _ensure_model(self):
        """懒加载 Cross-Encoder 模型。"""
        if self._model is not None or not self.enable_cross_encoder:
            return
        with self._model_lock:
            if self._model is not None:
                return
            try:
                from sentence_transformers import CrossEncoder

                logger.info("Loading reranker model: %s", RERANKER_MODEL)
                self._model = CrossEncoder(RERANKER_MODEL, max_length=512)
                logger.info("Reranker model loaded: %s", RERANKER_MODEL)
            except Exception as e:
                logger.warning(
                    "Failed to load Cross-Encoder model (%s): %s. Falling back to keyword-based reranking.",
                    RERANKER_MODEL,
                    e,
                    exc_info=True,
                )
                self.enable_cross_encoder = False

    def _cache_key(self, query: str, doc: str) -> str:
        """生成缓存键。"""
        content = f"{query}||{doc[:500]}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> float | None:
        # H13 修复：_cache/_cache_keys/_cache_hits/_cache_misses 并发读改写需加锁，
        # 原 list.remove(key) 在并发下会抛 ValueError（key 已被其他线程移除）。
        with self._model_lock:
            if key in self._cache:
                self._cache_hits += 1
                # LRU: 移动到末尾表示最近使用
                self._cache_keys.remove(key)
                self._cache_keys.append(key)
                return self._cache[key]
            self._cache_misses += 1
            return None

    def _cache_set(self, key: str, score: float):
        # H13 修复：与 _cache_get 共用锁，避免并发淘汰/插入丢失更新。
        with self._model_lock:
            if key in self._cache:
                # 已存在则更新值（不需要重复添加到 keys）
                self._cache[key] = score
                return
            if len(self._cache_keys) >= RERANK_CACHE_SIZE:
                oldest = self._cache_keys.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = score
            self._cache_keys.append(key)

    def rerank(
        self,
        query: str,
        results: list[dict[str, Any]],
        user_id: str | None = None,
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        """对检索结果进行重排序。

        Args:
            query: 查询文本
            results: 检索结果列表，每项包含 'document', 'id', 'metadata', 'distance'
            user_id: 可选的用户标识（预留）
            top_k: 返回前 K 条结果

        Returns:
            重排序后的结果列表，每项增加 'rerank_score' 字段
        """
        start_time = time.perf_counter()
        self._total_requests += 1

        if not results:
            return []

        # 尝试 Cross-Encoder 重排序
        if self.enable_cross_encoder:
            try:
                reranked = self._rerank_cross_encoder(query, results)
            except (RuntimeError, ValueError, OSError) as e:
                logger.warning(
                    "Cross-Encoder reranking failed, falling back to BM25: %s",
                    e,
                    exc_info=True,
                )
                reranked = self._rerank_bm25(query, results)
        else:
            reranked = self._rerank_bm25(query, results)

        if top_k:
            reranked = reranked[:top_k]

        elapsed = (time.perf_counter() - start_time) * 1000
        self._total_time_ms += elapsed

        return reranked

    def _rerank_cross_encoder(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """使用 Cross-Encoder 模型进行语义重排序。"""
        self._ensure_model()
        if self._model is None:
            return self._rerank_bm25(query, results)

        # 构建 query-document pairs
        pairs: list[tuple[str, str]] = []
        indices_uncached: list[int] = []
        scores: list[float | None] = [None] * len(results)

        for i, result in enumerate(results):
            doc = result.get("document", "")
            cache_key = self._cache_key(query, doc)
            cached = self._cache_get(cache_key)
            if cached is not None:
                scores[i] = cached
            else:
                pairs.append((query, doc))
                indices_uncached.append(i)

        # 批量推理未缓存的结果
        if pairs:
            import numpy as np

            raw_scores = self._model.predict(pairs, show_progress_bar=False, batch_size=32)
            # Cross-Encoder 输出为 logit，用 sigmoid 归一化到 [0, 1]
            raw_scores = np.array(raw_scores)
            normalized = 1.0 / (1.0 + np.exp(-raw_scores))

            for idx, score in zip(indices_uncached, normalized):
                scores[idx] = float(score)
                doc = results[idx].get("document", "")
                self._cache_set(self._cache_key(query, doc), float(score))

        # 构建结果
        reranked = []
        for i, result in enumerate(results):
            score = scores[i] if scores[i] is not None else 0.0
            reranked.append({**result, "rerank_score": round(score, 4)})

        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked

    def _rerank_bm25(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """使用 BM25 关键词匹配进行重排序（Cross-Encoder 不可用时的 fallback）。

        基于 BM25 算法计算 query 与 document 的关键词匹配分数。
        使用共享的 jieba 分词器获得比字符级更精准的分词结果。
        """
        try:
            from rank_bm25 import BM25Okapi
            from app.rag.tokenizer import tokenize_batch, tokenize

            # 使用共享 jieba 分词器（reranker 与 hybrid_search 一致）
            tokenized_query = tokenize(query)
            tokenized_docs = tokenize_batch([r.get("document", "") for r in results])

            if not tokenized_docs or not tokenized_query:
                return self._rerank_keyword_overlap(query, results)

            bm25 = BM25Okapi(tokenized_docs)
            bm25_scores = bm25.get_scores(tokenized_query)

            # 动态归一化：使用本批最大分做归一化基准，避免硬编码阈值
            max_score = max(bm25_scores) if bm25_scores.size else 0.0
            reranked = []
            for i, result in enumerate(results):
                score = float(bm25_scores[i]) if i < len(bm25_scores) else 0.0
                # 动态归一化到 [0, 1]，避免硬编码 10.0 阈值导致小数据集分数偏低
                normalized = min(score / max_score, 1.0) if max_score > 0 else 0.0
                reranked.append({**result, "rerank_score": round(normalized, 4)})
            reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
            return reranked

        except ImportError:
            logger.debug("rank_bm25 not installed, using keyword overlap")
            return self._rerank_keyword_overlap(query, results)

    def _rerank_keyword_overlap(self, query: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """词重叠重排序（最低级 fallback）。"""
        query_terms = set(query.lower().split())

        reranked = []
        for result in results:
            doc_text = result.get("document", "").lower()
            doc_terms = set(doc_text.split())
            overlap = len(query_terms & doc_terms)
            score = overlap / max(len(query_terms), 1)
            reranked.append({**result, "rerank_score": round(score, 4)})

        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        return reranked

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中文分词（兼容旧调用入口）。

        委托到共享 tokenizer 模块：
        - 优先使用 jieba（制造领域专用词典已注册）
        - jieba 不可用时降级为字符级 + 英文空格分词
        """
        from app.rag.tokenizer import tokenize

        return tokenize(text)

    def get_performance_metrics(self) -> dict[str, Any]:
        """获取重排序服务性能指标。"""
        avg_time = self._total_time_ms / self._total_requests if self._total_requests > 0 else 0.0
        cache_total = self._cache_hits + self._cache_misses
        return {
            "total_requests": self._total_requests,
            "avg_response_time_ms": round(avg_time, 2),
            "cross_encoder_enabled": self.enable_cross_encoder,
            "reranker_model": RERANKER_MODEL if self.enable_cross_encoder else None,
            "cache_size": len(self._cache),
            "cache_capacity": RERANK_CACHE_SIZE,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_rate": round(self._cache_hits / cache_total, 4) if cache_total > 0 else 0.0,
        }


# 线程安全懒加载单例


class _RerankerServiceHolder:
    """Thread-safe lazy holder for the :class:`RerankerService` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: RerankerService | None = None

    def get(self) -> RerankerService:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is not None:
                return self._instance
            self._instance = RerankerService()
            logger.info("Initialized reranker service")
            return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None


_holder = _RerankerServiceHolder()


def get_reranker_service() -> RerankerService:
    """获取共享的 :class:`RerankerService` 单例。"""
    return _holder.get()


__all__ = [
    "RerankerService",
    "get_reranker_service",
    "DEFAULT_RERANKER_MODEL",
    "RERANKER_MODEL",
]
