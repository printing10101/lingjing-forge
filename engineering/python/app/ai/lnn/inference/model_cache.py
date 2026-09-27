"""
Model Cache Implementation

Implements a thread-safe LRU (Least Recently Used) cache for LNN model instances.
Caches loaded models in memory to avoid repeated disk loading, reducing cold start latency.
"""

import time
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ModelCache:
    """
    Thread-safe LRU cache for model instances.

    Features:
    - LRU eviction strategy using OrderedDict
    - Configurable maximum cache size (default: 3 models)
    - Complete cache hit/miss statistics tracking
    - Memory usage tracking for cached models
    - Singleton pattern via ``__new__`` (first call's ``max_size`` wins;
      subsequent ``ModelCache(...)`` calls return the same instance)

    Usage:
        cache = get_model_cache(max_size=3)
        cache.put("model_name", model, memory_bytes)
        model = cache.get("model_name")

    Note:
        ``ModelCache()`` 直接实例化也返回单例（通过 ``__new__`` 实现），
        与 ``get_model_cache()`` 行为一致。测试中可用 ``reset_instance()``
        重置单例以实现测试间隔离。
    """

    _instance: Optional["ModelCache"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, max_size: int = 5):
        # 快速路径：已存在则直接返回，避免持锁开销
        if cls._instance is not None:
            return cls._instance
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, max_size: int = 5):
        # 单例模式：__init__ 会被每次调用，但只应在首次创建时执行初始化
        if getattr(self, "_initialized", False):
            return
        if max_size < 1:
            raise ValueError(f"max_size must be >= 1, got {max_size}")

        self._cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()
        self._total_requests = 0
        self._cache_hits = 0
        self._cache_misses = 0
        self._initialized = True

        logger.info("ModelCache initialized with max_size=%s", max_size)

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例（主要用于测试场景）。

        清除缓存的 ``ModelCache`` 实例，下次调用 ``ModelCache(...)`` 或
        ``get_model_cache()`` 时将创建新实例。
        """
        with cls._instance_lock:
            cls._instance = None

    def get(self, model_name: str) -> Any | None:
        """
        Get a cached model instance by name.

        Args:
            model_name: Unique model identifier

        Returns:
            Cached model instance if found, None otherwise
        """
        with self._lock:
            self._total_requests += 1

            if model_name in self._cache:
                self._cache_hits += 1
                entry = self._cache.pop(model_name)
                self._cache[model_name] = entry
                logger.debug(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=get status=CACHE_HIT"
                )
                return entry["model"]

            self._cache_misses += 1
            logger.debug(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=get status=CACHE_MISS")
            return None

    def put(self, model_name: str, model: Any, memory_size_bytes: int = 0) -> None:
        """
        Cache a model instance with its memory size.

        Args:
            model_name: Unique model identifier
            model: Model instance to cache
            memory_size_bytes: Memory size of the model in bytes

        Raises:
            ValueError: If memory_size_bytes is negative
        """
        if memory_size_bytes < 0:
            raise ValueError(
                "模型缓存内存估算失败：内存大小（memory_size）必须为非负数。当前值为负数，这通常表示模型参数计算出现异常。请检查模型架构定义。"
            )

        with self._lock:
            if model_name in self._cache:
                self._cache.pop(model_name)

            while len(self._cache) >= self._max_size:
                evicted_name, evicted_entry = self._cache.popitem(last=False)
                logger.info(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={evicted_name} "
                    f"operation=evict status=EVICTED (LRU policy)"
                )

            self._cache[model_name] = {
                "model": model,
                "memory_size_bytes": memory_size_bytes,
                "cached_at": time.time(),
            }
            logger.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} "
                f"operation=put status=CACHED memory={memory_size_bytes} bytes"
            )

    def remove(self, model_name: str) -> bool:
        """
        Remove a specific model from cache.

        Args:
            model_name: Model identifier to remove

        Returns:
            True if model was removed, False if not found
        """
        with self._lock:
            if model_name in self._cache:
                del self._cache[model_name]
                logger.info(
                    f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] model={model_name} operation=remove status=REMOVED"
                )
                return True
            return False

    def clear(self) -> tuple[int, int]:
        """
        Clear all cached models.

        Returns:
            Tuple of (number of models cleared, total memory freed in bytes)
        """
        with self._lock:
            count = len(self._cache)
            total_memory = sum(entry["memory_size_bytes"] for entry in self._cache.values())
            self._cache.clear()
            logger.info(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] operation=clear "
                f"status=CACHE_CLEARED models_cleared={count} memory_freed={total_memory} bytes"
            )
            return count, total_memory

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics and model information.

        Returns:
            Dictionary containing:
            - total_requests: Total number of cache requests
            - cache_hits: Number of cache hits
            - cache_misses: Number of cache misses
            - hit_rate: Cache hit rate (0.0 to 1.0)
            - cached_models: List of cached model names
            - total_cache_size_bytes: Total memory used by cached models
            - max_size: Maximum cache capacity
            - model_details: Dict mapping model names to their metadata
        """
        with self._lock:
            hit_rate = self._cache_hits / self._total_requests if self._total_requests > 0 else 0.0

            model_details = {}
            total_size = 0
            for name, entry in self._cache.items():
                memory_mb = entry["memory_size_bytes"] / (1024 * 1024)
                model_details[name] = {
                    "memory_size_bytes": entry["memory_size_bytes"],
                    "memory_size_mb": round(memory_mb, 2),
                    "cached_at": entry["cached_at"],
                }
                total_size += entry["memory_size_bytes"]

            return {
                "total_requests": self._total_requests,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "hit_rate": round(hit_rate, 4),
                "cached_models": list(self._cache.keys()),
                "total_cache_size_bytes": total_size,
                "total_cache_size_mb": round(total_size / (1024 * 1024), 2),
                "max_size": self._max_size,
                "model_details": model_details,
            }

    def contains(self, model_name: str) -> bool:
        """
        Check if a model is in cache without updating access order.

        Args:
            model_name: Model identifier to check

        Returns:
            True if model is cached, False otherwise
        """
        with self._lock:
            return model_name in self._cache

    def size(self) -> int:
        """Get current number of cached models."""
        with self._lock:
            return len(self._cache)

    def is_full(self) -> bool:
        """Check if cache has reached maximum capacity."""
        with self._lock:
            return len(self._cache) >= self._max_size


def get_model_cache(max_size: int = 3) -> ModelCache:
    """获取共享的 :class:`ModelCache` 单例；首次访问时懒初始化。

    单例逻辑现在直接由 :class:`ModelCache` 的 ``__new__`` 实现，
    本函数保留为向后兼容的入口，同时也是 FastAPI 依赖工厂，
    可直接用于 ``Depends(get_model_cache)``。

    Args:
        max_size: Maximum number of models to cache (only used on first call)

    Returns:
        Singleton ModelCache instance
    """
    return ModelCache(max_size=max_size)


def reset_model_cache() -> None:
    """Reset the singleton instance (mainly for testing purposes).

    委托给 :meth:`ModelCache.reset_instance`。
    """
    ModelCache.reset_instance()
