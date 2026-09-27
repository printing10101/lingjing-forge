"""工艺理解引擎单例（从 engine 拆出）。"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class _ProcessUnderstandingEngineHolder:
    """Thread-safe lazy holder for the :class:`ProcessUnderstandingEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: Any = None

    def get(self):
        from app.ai.process_understanding.engine import ProcessUnderstandingEngine

        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = ProcessUnderstandingEngine()
                logger.info("ProcessUnderstandingEngine initialized")
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _ProcessUnderstandingEngineHolder()


def get_process_understanding_engine():
    """获取共享的 :class:`ProcessUnderstandingEngine` 单例；首次访问时懒初始化。

    Returns:
        :class:`ProcessUnderstandingEngine` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_process_understanding_engine)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()
