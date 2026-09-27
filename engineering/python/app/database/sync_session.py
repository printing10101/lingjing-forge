"""Sync SQLAlchemy engine/session factory (shared singleton).

This module centralises the **synchronous** database session management
that was previously duplicated in
``app/database/repository/machining_record_repo.py`` and
``app/knowledge_graph/repository.py``. Both modules now delegate to
:func:`get_sync_engine` / :func:`get_sync_sessionmaker` to avoid the DRY
violation and to ensure consistent pool configuration across all sync
consumers.

The async entry point remains :mod:`app.database.connection`; this module
only provides the sync counterparts required by pytest scripts and
synchronous repository helpers.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.database.connection import DatabaseConfig

logger = logging.getLogger(__name__)


def _build_sync_url(url: str) -> str:
    """将异步驱动 URL 规范化为同步驱动 URL。

    支持两种转换：
        - ``postgresql+asyncpg://`` → ``postgresql+psycopg2://``
        - ``sqlite+aiosqlite://`` → ``sqlite://``

    其他 URL（已是同步驱动或纯 ``sqlite://`` / ``postgresql://``）原样返回。
    """
    if not url:
        return url
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return url


class _SyncSingletons:
    """线程安全的懒加载同步引擎持有者。

    配置统一从 :class:`DatabaseConfig` 读取（与异步引擎一致），确保
    ``DB_POOL_SIZE`` / ``DB_MAX_OVERFLOW`` / ``DB_POOL_TIMEOUT`` /
    ``DB_POOL_RECYCLE`` 等环境变量在同步与异步路径下行为一致。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: Engine | None = None
        self._sessionmaker: sessionmaker | None = None

    def get_engine(self) -> Engine | None:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            config = DatabaseConfig()
            if not config.enabled:
                logger.warning("DB_URL not configured, sync repository in-memory only")
                return None
            sync_url = _build_sync_url(config.url)
            is_sqlite = sync_url.startswith("sqlite://")
            engine_kwargs: dict[str, Any] = {
                "future": True,
                "pool_pre_ping": True,
                "echo": config.echo,
            }
            if not is_sqlite:
                engine_kwargs.update(
                    pool_size=config.pool_size,
                    max_overflow=config.max_overflow,
                    pool_recycle=config.pool_recycle,
                    pool_timeout=config.pool_timeout,
                )
            self._engine = create_engine(sync_url, **engine_kwargs)
            return self._engine

    def get_sessionmaker(self) -> sessionmaker | None:
        if self._sessionmaker is not None:
            return self._sessionmaker
        # 修复：原实现在 self._lock 内调用 self.get_engine()，而
        # get_engine() 也会尝试获取同一把 threading.Lock——
        # threading.Lock 不可重入，会永久死锁。
        # 当前侥幸不死锁只是因为 get_engine 总是先被调用（engine 已存在
        # 的快速路径直接返回，不进入锁）。一旦调用顺序改变即死锁。
        # 现改为在锁外获取 engine 引用，与异步版本（connection.py:152-165）
        # 的修复方式保持一致。
        engine = self.get_engine()
        if engine is None:
            return None
        with self._lock:
            if self._sessionmaker is not None:
                return self._sessionmaker
            self._sessionmaker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
            return self._sessionmaker

    def close(self) -> None:
        """释放底层引擎（主要用于测试重置）。"""
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.dispose()
                except Exception as e:  # pragma: no cover - 资源释放失败仅记录
                    logger.warning("Error disposing sync DB engine: %s", e)
                self._engine = None
                self._sessionmaker = None


_singletons = _SyncSingletons()


def get_sync_engine() -> Engine | None:
    """获取全局同步数据库引擎（首次访问时惰性创建）。"""
    return _singletons.get_engine()


def get_sync_sessionmaker() -> sessionmaker | None:
    """获取全局同步 ``sessionmaker``（首次访问时惰性创建）。"""
    return _singletons.get_sessionmaker()


def close_sync_engine() -> None:
    """释放同步引擎（测试场景使用）。"""
    _singletons.close()


__all__ = [
    "get_sync_engine",
    "get_sync_sessionmaker",
    "close_sync_engine",
]
