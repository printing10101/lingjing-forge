"""Database connection pool management for PostgreSQL.

Provides async SQLAlchemy engine with connection pooling,
health check, and lifecycle management.

Refactored to use thread-safe lazy singleton instead of ``global _``
pattern.  All public helpers are also exposed as FastAPI dependency
factories (see :func:`get_db`, :func:`get_db_sessionmaker`,
:func:`get_db_engine`).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event, text
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# P1-2 修复：通过 contextvar 让 get_db() 在 yield 时知道当前请求方法，
# 从而对 GET / HEAD / OPTIONS 请求跳过 commit。
_current_request_method: contextvars.ContextVar[str] = contextvars.ContextVar("_current_request_method", default="")


def set_request_method(method: str) -> contextvars.Token:
    """由 RequestIdMiddleware / 最外层 ASGI 中间件调用，记录当前请求方法。

    返回 Token 以便在请求结束时 reset_contextvar。
    """
    return _current_request_method.set(method.upper() if method else "")


def _resolve_db_url() -> str:
    # 优先使用 DB_URL 环境变量以保持向后兼容，否则回退到 config.database.db_url
    # 这样配置统一由 config.py 管理（DATABASE_URL），main.py 不再需要设置 DB_URL
    url = os.environ.get("DB_URL")
    if url:
        return url
    from app.config import config

    return config.database.db_url


@dataclass
class DatabaseConfig:
    url: str = field(default_factory=_resolve_db_url)
    pool_size: int = field(default_factory=lambda: int(os.environ.get("DB_POOL_SIZE", "15")))
    max_overflow: int = field(default_factory=lambda: int(os.environ.get("DB_MAX_OVERFLOW", "10")))
    pool_timeout: int = field(default_factory=lambda: int(os.environ.get("DB_POOL_TIMEOUT", "30")))
    pool_recycle: int = field(default_factory=lambda: int(os.environ.get("DB_POOL_RECYCLE", "3600")))
    echo: bool = field(default_factory=lambda: os.environ.get("DB_ECHO", "false").lower() == "true")

    @property
    def async_url(self) -> str:
        url = self.url
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif url.startswith("sqlite://"):
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        return url

    @property
    def enabled(self) -> bool:
        return bool(self.url)


# Thread-safe lazy singletons (替代 ``global _`` 模式)
# 设计要点：
# - 使用 ``_SingletonHolder`` 内部类封装可变状态，避免在模块顶层暴露
# ``_db_engine = None`` 这种"模块级可写变量"。
# - 构造时使用 ``threading.Lock`` 保证多线程并发首次创建时仅产生一个实例。
# - 重置（``close_db``）时使用同一把锁，确保 dispose 与重新初始化互斥。


class _DatabaseSingletons:
    """Thread-safe lazy holder for engine & sessionmaker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker | None = None

    def get_engine(self) -> AsyncEngine | None:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            config = DatabaseConfig()
            if not config.enabled:
                logger.warning("DB_URL not configured, database persistence disabled")
                return None
            # Build engine kwargs (SQLite does not support pool_size/max_overflow)
            engine_kwargs: dict[str, Any] = {}
            if config.async_url.startswith("sqlite"):
                # SQLite: use StaticPool or NullPool, no pool params
                engine_kwargs["pool_pre_ping"] = False
            else:
                engine_kwargs.update(
                    {
                        "pool_size": config.pool_size,
                        "max_overflow": config.max_overflow,
                        "pool_timeout": config.pool_timeout,
                        "pool_recycle": config.pool_recycle,
                        "pool_pre_ping": True,
                    }
                )
            self._engine = create_async_engine(config.async_url, **engine_kwargs)
            # P1-3 修复：SQLite PRAGMA 优化（WAL 模式 + foreign_keys + busy_timeout）
            # - journal_mode=WAL：并发读不阻塞写，桌面单用户场景显著降低锁冲突
            # - foreign_keys=ON：默认开启外键约束（SQLAlchemy ORM 假设外键生效）
            # - busy_timeout=5000ms：写锁竞争时等待 5s 而非立即抛 SQLITE_BUSY
            if config.async_url.startswith("sqlite"):
                # 防御性：当 sync_engine 不是合法事件目标时（如测试 MagicMock）跳过注册
                try:

                    @event.listens_for(self._engine.sync_engine, "connect")
                    def _set_sqlite_pragma(dbapi_conn, _connection_record):
                        cursor = dbapi_conn.cursor()
                        try:
                            cursor.execute("PRAGMA journal_mode=WAL")
                            cursor.execute("PRAGMA foreign_keys=ON")
                            cursor.execute("PRAGMA busy_timeout=5000")
                        finally:
                            cursor.close()

                    logger.info("SQLite PRAGMA enabled: journal_mode=WAL, foreign_keys=ON, busy_timeout=5000")
                except (InvalidRequestError, AttributeError, TypeError):
                    # 测试环境或非标准 engine（如 MagicMock）无法注册事件，跳过
                    logger.debug("SQLite PRAGMA registration skipped (non-standard engine)")
            else:
                logger.info(
                    "Database engine created: pool_size=%d max_overflow=%d",
                    config.pool_size,
                    config.max_overflow,
                )
            return self._engine

    def get_sessionmaker(self) -> async_sessionmaker | None:
        if self._sessionmaker is not None:
            return self._sessionmaker
        # 在锁外获取 engine 引用，避免不可重入锁死锁：
        # 若在持锁状态下调用 get_engine()，后者再次尝试 acquire 同一把 Lock 会永久阻塞。
        # get_engine() 内部已有自己的锁保护，此处锁外调用是安全的。
        engine = self.get_engine()
        if engine is None:
            return None
        with self._lock:
            if self._sessionmaker is not None:
                return self._sessionmaker
            self._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            return self._sessionmaker

    async def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    await self._engine.dispose()
                except Exception as e:  # pragma: no cover - 资源释放失败仅记录
                    logger.warning("Error disposing DB engine: %s", e)
                self._engine = None
                self._sessionmaker = None
                logger.info("Database engine disposed")


_singletons = _DatabaseSingletons()


# Public helpers (保留重构前的同名 API，确保向后兼容)


def get_engine() -> AsyncEngine | None:
    """获取全局数据库引擎（首次访问时惰性创建）。

    Returns:
        异步 SQLAlchemy 引擎实例；如果未配置 ``DB_URL`` 则返回 ``None``。
    """
    return _singletons.get_engine()


def get_sessionmaker() -> async_sessionmaker | None:
    """获取全局 sessionmaker（首次访问时惰性创建）。

    Returns:
        :class:`async_sessionmaker` 实例；如果底层引擎不可用则返回 ``None``。
    """
    return _singletons.get_sessionmaker()


async def get_session() -> AsyncSession:
    """获取一个新的 :class:`AsyncSession`（非依赖注入风格，仅供内部调用）。

    Returns:
        已配置 ``expire_on_commit=False`` 的新会话。

    Raises:
        RuntimeError: 数据库未配置（``DB_URL`` 为空）。
    """
    sessionmaker = get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("Database not configured")
    return sessionmaker()


async def check_db_health() -> dict:
    engine = get_engine()
    if engine is None:
        return {"status": "disabled", "message": "DB_URL not configured"}
    try:
        # P5-1 优化：健康检查超时从 3s 增加到 5s，避免高负载下误判 unhealthy
        # 高负载场景下（并发查询多/磁盘 IO 慢），3s 可能不足以完成健康检查，
        # 导致负载均衡/容器编排误判服务不健康并重启，形成抖动。
        # 5s 符合健康检查端点的常规响应要求，同时避免长时间卡顿。
        async def _do_health_check() -> dict:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                pool = engine.pool
                return {
                    "status": "healthy",
                    "pool_size": pool.size() if hasattr(pool, "size") else "N/A",
                    "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else "N/A",
                }

        return await asyncio.wait_for(_do_health_check(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("数据库健康检查超时（5s，可能是高负载或网络延迟）")
        return {"status": "unhealthy", "error": "database: TimeoutError"}
    except Exception as e:
        logger.warning("数据库健康检查失败: %s", e, exc_info=True)
        return {"status": "unhealthy", "error": f"database: {type(e).__name__}"}


async def close_db() -> None:
    """关闭并释放数据库引擎（FastAPI shutdown 时调用）。"""
    await _singletons.close()


# FastAPI dependency factories
# 这些函数可直接用于 ``Depends(...)``。与原 ``get_session`` 不同的是：
# - 返回类型更精确（``Optional[...]`` 表示未配置时为 None）
# - ``get_db`` 是 yield-式依赖，FastAPI 会负责关闭会话


async def get_db_engine() -> AsyncEngine | None:
    """FastAPI 依赖：返回当前数据库引擎（可能为 ``None``）。

    用法::

        @router.get("/health")
        async def health(engine: AsyncEngine = Depends(get_db_engine)):
            ...
    """
    return _singletons.get_engine()


async def get_db_sessionmaker() -> async_sessionmaker | None:
    """FastAPI 依赖：返回当前 sessionmaker（可能为 ``None``）。"""
    return _singletons.get_sessionmaker()


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI 依赖：yield 一个 :class:`AsyncSession`，请求结束自动关闭。

    用法::

        @router.get("/items")
        async def list_items(session: AsyncSession = Depends(get_db)):
            result = await session.execute(...)

    P1-2 修复：GET / HEAD / OPTIONS 请求按语义应当只读，不自动 commit。
    - 只读请求：仅 rollback 释放事务（即使有 pending 修改也不持久化，符合最小惊讶原则）
    - 写请求（POST/PUT/PATCH/DELETE）：成功时 commit，失败时 rollback

    Raises:
        RuntimeError: 数据库未配置（``DB_URL`` 为空）。
    """
    sessionmaker = _singletons.get_sessionmaker()
    if sessionmaker is None:
        raise RuntimeError("Database not configured")
    # 通过 contextvar / 全局状态获取当前请求方法
    # FastAPI 中 request.method 可通过 inspect stack 获取，但更可靠的方式是
    # 在此处使用 contextvar。此处简化：直接判断是否在请求上下文中。
    request_method = _current_request_method.get()
    is_readonly = request_method in ("GET", "HEAD", "OPTIONS")
    async with sessionmaker() as session:
        try:
            yield session
        except HTTPException:
            # P0-14 修复：HTTP 异常由业务层主动抛出（如正常的 4xx/5xx 响应），
            # 为避免误持久化未完成的事务，统一 rollback 后重新抛出。
            await session.rollback()
            raise
        except SQLAlchemyError as e:
            # P0-14 修复：数据库异常（IntegrityError/OperationalError 等）必须 rollback，
            # 否则会进入 else 分支触发 commit 导致脏数据持久化。
            await session.rollback()
            logger.error("Database session error, rolled back: %s", e, exc_info=True)
            raise
        except (RuntimeError, OSError, ValueError) as e:
            await session.rollback()
            logger.error("Database session error, rolled back: %s", e, exc_info=True)
            raise
        else:
            if is_readonly:
                # P1-2：GET 请求不 commit，仅 rollback 释放事务
                await session.rollback()
            else:
                await session.commit()
