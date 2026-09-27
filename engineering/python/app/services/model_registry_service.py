"""
Model Registry Service

Provides a thread-safe singleton service that unifies access to:
- LNNModelRegistry (model registration & metadata)
- ModelCache (predictor caching)
- training_tasks (training task state)

This eliminates the dual-instance problem where lnn.py and agent_gateway.py
each created their own LNNModelRegistry, causing data inconsistency.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.ai.lnn.inference.registry import LNNModelRegistry, ModelRegistry
from app.ai.lnn.inference.model_cache import get_model_cache, ModelCache
from app.services._shared.service_base import BaseSingletonService


def _default_registry_model_dir() -> str | None:
    """解析随包权重目录 engineering/python/models/lnn（CWD 无关）。

    注册表预定义模型的相对路径（models/lnn/*.npz）按进程 CWD 解析，
    服务可能从任意目录启动——这里显式锚定到随包目录，保证权重加载
    不受启动目录影响。目录不存在时返回 None（保持相对路径行为）。
    """
    model_dir = Path(__file__).resolve().parents[2] / "models" / "lnn"
    return str(model_dir) if model_dir.is_dir() else None


class ModelRegistryService(BaseSingletonService):
    """
    Thread-safe singleton service for unified model registry access.

    All modules must access model registry, model cache, and training tasks
    through this service instance — never by directly instantiating LNNModelRegistry
    or ModelCache.
    """

    def __init__(self):
        self._model_registry: LNNModelRegistry = LNNModelRegistry(model_dir=_default_registry_model_dir())
        self._pytorch_registry: ModelRegistry = ModelRegistry()
        self._model_cache: ModelCache = get_model_cache()
        self._training_tasks: dict[str, dict[str, Any]] = {}
        self._tasks_lock = threading.Lock()

    # ── Model Registry Delegation ──────────────────────────────────────

    @property
    def model_registry(self) -> LNNModelRegistry:
        """Get the shared LNNModelRegistry instance."""
        return self._model_registry

    @property
    def pytorch_registry(self) -> ModelRegistry:
        """Get the shared ModelRegistry (PyTorch) instance."""
        return self._pytorch_registry

    def register_model(self, model_info) -> bool:
        """Register a new model."""
        return self._model_registry.register_model(model_info)

    def register_quantized_model(self, *args, **kwargs) -> bool:
        """Register a quantized model."""
        return self._model_registry.register_quantized_model(*args, **kwargs)

    def get_model_entry(self, model_name: str):
        """Get a model entry by name. Returns None if not found (no exception)."""
        # 注意：不可在此再包一层 _lock —— registry 内部方法自带锁，
        # 外层重复获取非重入 Lock 会自死锁（曾导致 hot_update 测试死锁）。
        return self._model_registry.registry.get(model_name)

    def list_models(self, return_objects: bool = False):
        """List all registered models."""
        return self._model_registry.list_models(return_objects=return_objects)

    def validate_model(self, model_name: str) -> dict[str, Any]:
        """Validate a model."""
        return self._model_registry.validate_model(model_name)

    # ── Model Cache Delegation ─────────────────────────────────────────

    @property
    def model_cache(self) -> ModelCache:
        """Get the shared ModelCache instance."""
        return self._model_cache

    def get_cached_predictor(self, model_name: str):
        """Get a cached predictor by model name."""
        return self._model_cache.get(model_name)

    def cache_predictor(self, model_name: str, predictor, memory_size_bytes: int = 0) -> None:
        """Cache a predictor instance."""
        self._model_cache.put(model_name, predictor, memory_size_bytes)

    def clear_cache(self):
        """Clear all cached predictors."""
        return self._model_cache.clear()

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return self._model_cache.get_stats()

    # ── Training Task Management ───────────────────────────────────────

    def get_training_tasks(self) -> dict[str, dict[str, Any]]:
        """Get the shared training tasks dictionary."""
        return self._training_tasks

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a specific training task by ID."""
        return self._training_tasks.get(task_id)

    def create_task(self, task_id: str, task_data: dict[str, Any]) -> None:
        """Create a new training task entry."""
        with self._tasks_lock:
            self._training_tasks[task_id] = task_data

    def update_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Update an existing training task. Returns False if task not found."""
        with self._tasks_lock:
            if task_id in self._training_tasks:
                self._training_tasks[task_id].update(updates)
                return True
            return False

    def remove_task(self, task_id: str) -> bool:
        """Remove a training task. Returns False if not found."""
        with self._tasks_lock:
            if task_id in self._training_tasks:
                del self._training_tasks[task_id]
                return True
            return False


# Module-level singleton accessor — ensures global single point of access
def get_model_registry_service() -> ModelRegistryService:
    """
    Get the global ModelRegistryService singleton.

    This is the preferred way to access model registry functionality
    from any module. Do NOT directly instantiate LNNModelRegistry.
    """
    return ModelRegistryService.get_instance()  # type: ignore[return-value]
