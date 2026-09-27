"""Embedding model wrapper with caching for semantic search.

Supports local BGE series models (small/base/large) with optional API fallback.
Uses HuggingFace Chinese mirror (hf-mirror.com) for faster model downloads in China.

Model selection via environment variables:
- EMBEDDING_MODEL: HuggingFace model name (default: BAAI/bge-small-zh-v1.5)
- EMBEDDING_DIM: embedding dimension override (auto-detected if not set)

Preset shortcuts:
- EMBEDDING_MODEL=small  -> BAAI/bge-small-zh-v1.5  (512 dim,  ~95MB)
- EMBEDDING_MODEL=base   -> BAAI/bge-base-zh-v1.5    (768 dim,  ~400MB)
- EMBEDDING_MODEL=large  -> BAAI/bge-large-zh-v1.5   (1024 dim, ~1.2GB)
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Any
from collections.abc import Sequence

# 设置 HuggingFace 中国镜像，加速模型下载
# 可通过环境变量 HF_ENDPOINT 覆盖，默认为 https://hf-mirror.com
if "HF_ENDPOINT" not in os.environ:
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

logger = logging.getLogger(__name__)

# 预置模型映射表：别名 -> (完整模型名, 默认维度)
_MODEL_PRESETS: dict[str, tuple[str, int]] = {
    "small": ("BAAI/bge-small-zh-v1.5", 512),
    "base": ("BAAI/bge-base-zh-v1.5", 768),
    "large": ("BAAI/bge-large-zh-v1.5", 1024),
    # 英文场景可选：
    "small-en": ("BAAI/bge-small-en-v1.5", 384),
    "base-en": ("BAAI/bge-base-en-v1.5", 768),
    "large-en": ("BAAI/bge-large-en-v1.5", 1024),
}

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_DIM = 512
CACHE_MAX_SIZE = 10000


def _resolve_model_config() -> tuple[str, int]:
    """从环境变量解析模型配置，支持预设别名。"""
    raw = os.getenv("EMBEDDING_MODEL", "").strip()
    if not raw:
        return DEFAULT_MODEL, DEFAULT_DIM

    # 检查是否为预设别名
    preset = _MODEL_PRESETS.get(raw.lower())
    if preset is not None:
        model_name, default_dim = preset
    else:
        model_name = raw
        # 尝试从预设表中匹配完整模型名以获取默认维度
        default_dim = DEFAULT_DIM
        for _alias, (full_name, dim) in _MODEL_PRESETS.items():
            if full_name == model_name:
                default_dim = dim
                break

    # 环境变量显式指定维度优先
    dim_str = os.getenv("EMBEDDING_DIM", "").strip()
    if dim_str:
        try:
            return model_name, int(dim_str)
        except ValueError:
            logger.warning(
                "Invalid EMBEDDING_DIM value '%s', using default %d",
                dim_str,
                default_dim,
            )
    return model_name, default_dim


# 启动时解析一次
_RESOLVED_MODEL, _RESOLVED_DIM = _resolve_model_config()


class EmbeddingService:
    """Thread-safe embedding service with LRU cache and lazy model loading."""

    # 批量推理时单批最大文本数，避免大 batch 导致内存峰值
    # 太小会浪费 GPU/CPU 并行能力，太大可能 OOM；32 是 CPU 场景的合理折中
    _EMBED_BATCH_CHUNK = 32

    def __init__(
        self,
        model_name: str | None = None,
        vector_dim: int | None = None,
        cache_size: int = CACHE_MAX_SIZE,
    ):
        self._model_name = model_name or _RESOLVED_MODEL
        self._vector_dim = vector_dim or _RESOLVED_DIM
        self._model: Any = None
        self._lock = threading.Lock()
        self._cache: dict[str, list[float]] = {}
        self._cache_keys: list[str] = []
        self._cache_size = cache_size
        # 命中统计（用于诊断与性能监控）
        self._cache_hits = 0
        self._cache_misses = 0
        # 批量推理总文本计数（用于评估 batch chunk 策略效果）
        self._total_embed_calls = 0

    def _ensure_model(self):
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            logger.info("Loading embedding model: %s", self._model_name)
            from sentence_transformers import SentenceTransformer

            # 设备选择：默认 auto（cuda:0 优先），可通过 EMBEDDING_DEVICE 环境变量覆盖
            # 设为 "cpu" 时强制使用 CPU，避免与 Ollama 等 GPU 进程内存竞争导致 segfault
            # （案例：qwen3:14b 占用 ~9GB 显存后，bge-small-zh 在 cuda:0 加载时 segfault）
            device = os.getenv("EMBEDDING_DEVICE", "").strip() or None
            logger.info("Embedding device: %s (EMBEDDING_DEVICE=%r)", device or "auto", os.getenv("EMBEDDING_DEVICE"))

            try:
                # 优先从 HuggingFace 镜像站下载（通过 HF_ENDPOINT 环境变量控制）
                if device:
                    self._model = SentenceTransformer(self._model_name, device=device)
                else:
                    self._model = SentenceTransformer(self._model_name)
            except Exception as download_err:
                # 下载失败时尝试从本地缓存加载
                logger.warning(
                    "Failed to download model from remote (%s), attempting to load from local cache...",
                    download_err,
                )
                try:
                    cache_kwargs: dict[str, Any] = {
                        "cache_folder": "./models/embedding_cache",
                    }
                    if device:
                        cache_kwargs["device"] = device
                    self._model = SentenceTransformer(self._model_name, **cache_kwargs)
                except Exception as cache_err:
                    logger.error(
                        "Failed to load embedding model from local cache: %s",
                        cache_err,
                    )
                    raise RuntimeError(
                        f"无法加载 Embedding 模型 '{self._model_name}'。"
                        f"远程下载失败: {download_err}；"
                        f"本地缓存加载也失败: {cache_err}。"
                        f"请检查网络连接或手动将模型放置到 ./models/embedding_cache 目录。"
                    ) from cache_err

            actual_dim = self._model.get_sentence_embedding_dimension()
            if actual_dim:
                self._vector_dim = actual_dim
            logger.info("Embedding model loaded: %s, dim=%d", self._model_name, self._vector_dim)

    def _cache_key(self, text: str) -> str:
        # 安全修复：使用 SHA256 替代 MD5，避免碰撞导致的缓存投毒
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> list[float] | None:
        """LRU 读：命中时把 key 移到末尾，并累计命中统计。"""
        # H13 修复：_cache/_cache_keys/_cache_hits/_cache_misses 并发读改写需加锁，
        # 原 list.remove(key) 在并发下会抛 ValueError（key 已被其他线程移除）。
        with self._lock:
            if key in self._cache:
                self._cache_hits += 1
                # LRU：移动到末尾表示最近使用
                self._cache_keys.remove(key)
                self._cache_keys.append(key)
                return self._cache[key]
            self._cache_misses += 1
            return None

    def _cache_set(self, key: str, vector: list[float]):
        """LRU 写：已存在则更新值，否则淘汰最旧后插入。"""
        # H13 修复：与 _cache_get 共用锁，避免并发淘汰/插入丢失更新。
        with self._lock:
            if key in self._cache:
                # 已存在则更新值（不需要重复添加到 keys）
                self._cache[key] = vector
                return
            if len(self._cache_keys) >= self._cache_size:
                oldest = self._cache_keys.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = vector
            self._cache_keys.append(key)

    def embed(self, text: str) -> list[float]:
        key = self._cache_key(text)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        self._ensure_model()
        vector = self._model.encode(text, normalize_embeddings=True).tolist()
        self._cache_set(key, vector)
        return vector

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """批量生成 embedding，命中缓存的部分直接复用。

        优化：
        - 缓存命中/未命中计数，便于诊断命中率
        - 未命中文本按 ``_EMBED_BATCH_CHUNK`` 分批推理，避免大 batch OOM
        """
        results: list[list[float]] = [[] for _ in texts]
        uncached_texts: list[str] = []
        uncached_indices: list[int] = []
        uncached_keys: list[str] = []

        for i, text in enumerate(texts):
            key = self._cache_key(text)
            cached = self._cache_get(key)
            if cached is not None:
                results[i] = cached
            else:
                uncached_texts.append(text)
                uncached_indices.append(i)
                uncached_keys.append(key)

        if uncached_texts:
            self._ensure_model()
            self._total_embed_calls += len(uncached_texts)
            # 分块推理：避免一次性把大量文本塞给模型导致内存峰值
            chunk_size = self._EMBED_BATCH_CHUNK
            for start in range(0, len(uncached_texts), chunk_size):
                end = start + chunk_size
                chunk_texts = uncached_texts[start:end]
                vectors = self._model.encode(chunk_texts, normalize_embeddings=True, show_progress_bar=False).tolist()
                for offset, vector in enumerate(vectors):
                    idx = uncached_indices[start + offset]
                    key = uncached_keys[start + offset]
                    results[idx] = vector
                    self._cache_set(key, vector)

        return results

    def get_cache_stats(self) -> dict[str, Any]:
        """获取缓存命中统计（用于 /api/rag/stats 诊断端点）。"""
        total = self._cache_hits + self._cache_misses
        return {
            "cache_size": len(self._cache),
            "cache_capacity": self._cache_size,
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "cache_hit_rate": round(self._cache_hits / total, 4) if total > 0 else 0.0,
            "total_embed_calls": self._total_embed_calls,
            "model_loaded": self._model is not None,
            "model_name": self._model_name,
            "vector_dim": self._vector_dim,
        }

    @property
    def dimension(self) -> int:
        self._ensure_model()
        return self._vector_dim


class _EmbeddingServiceHolder:
    """Thread-safe lazy holder for the :class:`EmbeddingService` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: EmbeddingService | None = None

    def get(self) -> EmbeddingService:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = EmbeddingService()
            logger.info("Initialized embedding service")
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _EmbeddingServiceHolder()


def get_embedding_service() -> EmbeddingService:
    """获取共享的 :class:`EmbeddingService` 单例；首次访问时懒初始化。

    Returns:
        :class:`EmbeddingService` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_embedding_service)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


__all__ = [
    "EmbeddingService",
    "get_embedding_service",
    "DEFAULT_MODEL",
    "DEFAULT_DIM",
    "CACHE_MAX_SIZE",
    "get_model_info",
]


def get_model_info() -> dict[str, object]:
    """获取当前生效的嵌入模型配置信息（用于 /api/rag/stats 等诊断端点）。"""
    service = get_embedding_service()
    cache_stats = service.get_cache_stats()
    return {
        "configured_model": _RESOLVED_MODEL,
        "configured_dim": _RESOLVED_DIM,
        "actual_dim": service._vector_dim,
        "model_loaded": service._model is not None,
        "cache_size": len(service._cache),
        "cache_capacity": service._cache_size,
        "presets_available": list(_MODEL_PRESETS.keys()),
        # 详细缓存命中统计
        "cache_hits": cache_stats["cache_hits"],
        "cache_misses": cache_stats["cache_misses"],
        "cache_hit_rate": cache_stats["cache_hit_rate"],
        "total_embed_calls": cache_stats["total_embed_calls"],
    }
