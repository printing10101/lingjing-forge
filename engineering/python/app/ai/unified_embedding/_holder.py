"""嵌入空间单例（从 space 拆出）。"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class _EmbeddingSpaceHolder:
    """Thread-safe lazy holder for the :class:`EmbeddingSpace` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: Any = None

    def get(self):
        from app.ai.unified_embedding._space import EmbeddingSpace

        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = EmbeddingSpace()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _EmbeddingSpaceHolder()


def get_embedding_space():
    """获取共享的 :class:`EmbeddingSpace` 单例；首次访问时懒初始化。

    Returns:
        :class:`EmbeddingSpace` 实例（应用生命周期内同一实例）。
    """
    return _holder.get()
