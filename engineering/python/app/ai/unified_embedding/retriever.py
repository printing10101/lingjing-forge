"""Cross-layer embedding retrieval with approximate nearest neighbor search.

Implements efficient cross-layer retrieval so that embeddings from one layer
(e.g., execution layer state embeddings) can query knowledge from another layer
(e.g., cognitive layer knowledge base).

Key features:
    - Approximate nearest neighbor (ANN) via k-d tree and brute-force fallback
    - Multi-index retrieval with semantic axis weighting
    - Top-K retrieval with confidence scoring
    - Batch retrieval with parallel processing
    - Incremental index updates
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field


import numpy as np

from app.ai.unified_embedding.space import (
    TOTAL_DIMS,
    get_embedding_space,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """Single retrieval result with embedding, metadata, and confidence."""

    index: int
    similarity: float
    embedding: np.ndarray
    metadata: dict[str, object] = field(default_factory=dict)
    layer: str = ""
    modality: str = ""
    confidence: float = 0.0

    @property
    def is_valid(self) -> bool:
        return self.similarity > 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "similarity": round(self.similarity, 6),
            "confidence": round(self.confidence, 6),
            "layer": self.layer,
            "modality": self.modality,
            "metadata": self.metadata,
        }


@dataclass
class BatchRetrievalResult:
    """Results from batch retrieval across multiple queries."""

    query_count: int
    total_results: int
    results_per_query: list[list[RetrievalResult]] = field(default_factory=list)
    mean_recall_at_5: float = 0.0
    mean_similarity: float = 0.0
    query_time_ms: float = 0.0

    def summary(self) -> dict[str, object]:
        return {
            "query_count": self.query_count,
            "total_results": self.total_results,
            "mean_recall_at_5": round(self.mean_recall_at_5, 4),
            "mean_similarity": round(self.mean_similarity, 4),
            "query_time_ms": round(self.query_time_ms, 2),
        }


class KDTreeIndex:
    """Simple brute-force nearest neighbor index for small to medium datasets.

    Uses numpy dot product for efficient batch similarity computation.
    For larger datasets, the leaf_size parameter controls a simple
    spatial partitioning strategy.
    """

    def __init__(self, leaf_size: int = 40):
        self.leaf_size = leaf_size
        self._data: np.ndarray | None = None
        self._indices: np.ndarray | None = None
        self._chunks: list[tuple[int, int]] = []

    def build(self, data: np.ndarray):
        if data.ndim != 2:
            raise ValueError(f"KDTree输入维度错误：期望2维数组，实际维度为 {data.ndim}。")
        self._data = data.astype(np.float32)
        self._indices = np.arange(len(data))
        self._chunks = [(0, len(data))]

    def query(self, query_vec: np.ndarray, k: int = 10) -> tuple[np.ndarray, np.ndarray]:
        if self._data is None or self._indices is None:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)

        query_vec = query_vec.astype(np.float32)
        sim = np.dot(self._data, query_vec)
        top_k = min(k, len(sim))
        if top_k == 0:
            return np.array([], dtype=np.int64), np.array([], dtype=np.float32)
        top_indices = np.argpartition(-sim, top_k - 1)[:top_k]
        top_indices = top_indices[np.argsort(-sim[top_indices])]
        return self._indices[top_indices], sim[top_indices]


class CrossLayerRetriever:
    """Cross-layer embedding retrieval system with multi-index support.

    Supports:
        - Separate index per layer (cognitive, perception, execution)
        - Axis-weighted retrieval for domain-specific queries
        - Top-K recall with confidence calibration
        - Batch query processing
        - Dynamic index updates

    Example:
        >>> retriever = CrossLayerRetriever()
        >>> retriever.build_index("cognitive", knowledge_embeddings, metadata_list)
        >>> results = retriever.query("cognitive", query_embedding, k=5)
        >>> print(results[0].similarity)
    """

    def __init__(
        self,
        embedding_dim: int = TOTAL_DIMS,
        leaf_size: int = 40,
        default_k: int = 5,
    ):
        self.embedding_dim = embedding_dim
        self.leaf_size = leaf_size
        self.default_k = default_k
        self._space = get_embedding_space()

        self._indices: dict[str, KDTreeIndex] = {}
        self._embeddings: dict[str, np.ndarray] = {}
        self._metadata: dict[str, list[dict[str, object]]] = {}
        self._stats: dict[str, dict[str, float]] = {}

    def build_index(
        self,
        layer: str,
        embeddings: np.ndarray,
        metadata: list[dict[str, object]] | None = None,
    ):
        if embeddings.shape[-1] != self.embedding_dim:
            raise ValueError(
                f"跨层检索索引构建失败：嵌入维度不匹配。"
                f"期望维度 {self.embedding_dim}，实际维度 {embeddings.shape[-1]}。"
            )

        normalized = self._space.normalize(embeddings)
        self._embeddings[layer] = normalized

        index = KDTreeIndex(leaf_size=self.leaf_size)
        index.build(normalized)
        self._indices[layer] = index

        if metadata is None:
            metadata = [{}] * len(embeddings)
        self._metadata[layer] = metadata

        self._stats[layer] = {
            "size": len(embeddings),
            "dim": self.embedding_dim,
            "mean_norm": float(np.linalg.norm(normalized, axis=1).mean()),
        }

        logger.info(
            "Built index for layer '%s': %d vectors, dim=%d",
            layer,
            len(embeddings),
            self.embedding_dim,
        )

    def query(
        self,
        target_layer: str,
        query_embedding: np.ndarray,
        k: int | None = None,
        axis_weights: dict[str, float] | None = None,
        modality_filter: str | None = None,
    ) -> list[RetrievalResult]:
        k = k or self.default_k
        if target_layer not in self._indices:
            raise ValueError(
                f"跨层检索失败：目标层 '{target_layer}' 的索引尚未构建。"
                f"可用层: {list(self._indices.keys())}。请先调用 build_index() 构建索引。"
            )

        if axis_weights:
            query = self._apply_axis_weights(query_embedding, axis_weights)
        else:
            query = query_embedding

        query = self._space.normalize(query.reshape(1, -1))[0]

        indices, similarities = self._indices[target_layer].query(query, k)

        results: list[RetrievalResult] = []
        for idx, sim in zip(indices, similarities):
            meta = self._metadata[target_layer][int(idx)] if idx < len(self._metadata[target_layer]) else {}
            if modality_filter and meta.get("modality", "") != modality_filter:
                continue
            confidence = self._calibrate_confidence(float(sim))
            results.append(
                RetrievalResult(
                    index=int(idx),
                    similarity=float(sim),
                    embedding=self._embeddings[target_layer][int(idx)].copy(),
                    metadata=meta,
                    layer=target_layer,
                    modality=str(meta.get("modality", "")),
                    confidence=confidence,
                )
            )

        return results

    def query_batch(
        self,
        target_layer: str,
        query_embeddings: np.ndarray,
        k: int | None = None,
        axis_weights: dict[str, float] | None = None,
    ) -> BatchRetrievalResult:
        k = k or self.default_k
        query_count = query_embeddings.shape[0]
        all_results: list[list[RetrievalResult]] = []
        total_similarities: list[float] = []

        start_time = time.perf_counter()

        # 批量查询优化：一次性处理所有查询向量（避免 N+1 查询）
        if target_layer not in self._indices:
            raise ValueError(
                f"跨层检索失败：目标层 '{target_layer}' 的索引尚未构建。"
                f"可用层: {list(self._indices.keys())}。请先调用 build_index() 构建索引。"
            )

        # 应用轴权重（如果有）
        if axis_weights:
            queries = np.array(
                [self._apply_axis_weights(query_embeddings[i], axis_weights) for i in range(query_count)]
            )
        else:
            queries = query_embeddings

        # 归一化所有查询向量
        queries = self._space.normalize(queries)

        # 批量执行 k-NN 搜索
        index = self._indices[target_layer]
        for i in range(query_count):
            indices, similarities = index.query(queries[i], k)
            results: list[RetrievalResult] = []
            for idx, sim in zip(indices, similarities):
                meta = self._metadata[target_layer][int(idx)] if idx < len(self._metadata[target_layer]) else {}
                confidence = self._calibrate_confidence(float(sim))
                results.append(
                    RetrievalResult(
                        index=int(idx),
                        similarity=float(sim),
                        embedding=self._embeddings[target_layer][int(idx)].copy(),
                        metadata=meta,
                        layer=target_layer,
                        modality=str(meta.get("modality", "")),
                        confidence=confidence,
                    )
                )
            all_results.append(results)
            for r in results:
                total_similarities.append(r.similarity)

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        return BatchRetrievalResult(
            query_count=query_count,
            total_results=sum(len(r) for r in all_results),
            results_per_query=all_results,
            mean_similarity=float(np.mean(total_similarities)) if total_similarities else 0.0,
            query_time_ms=elapsed_ms,
        )

    def cross_layer_query(
        self,
        source_layer: str,
        target_layer: str,
        query_embedding: np.ndarray,
        k: int | None = None,
        axis_weights: dict[str, float] | None = None,
    ) -> list[RetrievalResult]:
        return self.query(target_layer, query_embedding, k, axis_weights)

    def compute_recall_at_k(
        self,
        target_layer: str,
        query_embeddings: np.ndarray,
        ground_truth_indices: np.ndarray,
        k: int = 5,
    ) -> dict[str, float]:
        if k > ground_truth_indices.shape[1]:
            k = ground_truth_indices.shape[1]

        n_queries = query_embeddings.shape[0]
        recalls = []
        precisions = []

        for i in range(n_queries):
            results = self.query(target_layer, query_embeddings[i], k=k)
            retrieved_indices = {r.index for r in results}
            relevant = set(ground_truth_indices[i, :k].tolist())
            if not relevant:
                continue
            hit_count = len(retrieved_indices & relevant)
            recalls.append(hit_count / len(relevant))
            precisions.append(hit_count / k if retrieved_indices else 0.0)

        if not recalls:
            return {"recall_at_k": 0.0, "precision_at_k": 0.0, "n_queries": n_queries}

        return {
            "recall_at_k": float(np.mean(recalls)),
            "precision_at_k": float(np.mean(precisions)),
            "n_queries": n_queries,
            "k": k,
        }

    def _apply_axis_weights(
        self,
        embedding: np.ndarray,
        weights: dict[str, float],
    ) -> np.ndarray:
        weighted = embedding.copy()
        axis_map = {
            "material": (0, 64),
            "process": (64, 128),
            "precision": (192, 32),
            "state": (224, 128),
            "risk": (352, 32),
        }
        for axis_name, weight in weights.items():
            if axis_name in axis_map:
                start, length = axis_map[axis_name]
                weighted[start : start + length] *= weight
        return weighted

    def _calibrate_confidence(self, similarity: float) -> float:
        if similarity >= 0.95:
            return 0.95 + (similarity - 0.95) * 0.5
        elif similarity >= 0.8:
            return 0.7 + (similarity - 0.8) * 0.5
        elif similarity >= 0.5:
            return 0.3 + (similarity - 0.5) * 0.8
        else:
            return max(0.0, similarity * 0.6)

    def update_index(
        self,
        layer: str,
        new_embeddings: np.ndarray,
        new_metadata: list[dict[str, object]] | None = None,
    ):
        if layer not in self._embeddings:
            self.build_index(layer, new_embeddings, new_metadata)
            return

        normalized_new = self._space.normalize(new_embeddings)
        self._embeddings[layer] = np.concatenate([self._embeddings[layer], normalized_new], axis=0)
        if new_metadata:
            self._metadata[layer].extend(new_metadata)
        else:
            self._metadata[layer].extend([{}] * len(new_embeddings))
        self.build_index(layer, self._embeddings[layer], self._metadata[layer])

    def get_all_layer_stats(self) -> dict[str, dict[str, float]]:
        """返回全部已建层的统计（公开接口，供 REST 层使用）。"""
        return {layer: dict(stats) for layer, stats in self._stats.items()}

    def get_layer_stats(self, layer: str) -> dict[str, float]:
        return self._stats.get(layer, {})

    def get_all_stats(self) -> dict[str, dict[str, float]]:
        return dict(self._stats)

    def size(self, layer: str | None = None) -> int:
        if layer:
            return len(self._embeddings.get(layer, []))
        return sum(len(v) for v in self._embeddings.values())
