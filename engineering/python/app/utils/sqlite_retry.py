"""
SQLite 操作重试包装器

在异步 + check_same_thread=False 的上下文中，
并发写操作可能触发 database is locked 错误。
本模块提供装饰器和上下文管理器，以指数退避 + jitter 自动重试。
"""

import asyncio
import functools
import logging
import random
import sqlite3
import time
from typing import Any, TypeVar
from collections.abc import Callable

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 0.05
DEFAULT_MAX_DELAY = 2.0


def _is_lock_error(error: Exception) -> bool:
    if isinstance(error, sqlite3.OperationalError):
        msg = str(error).lower()
        return "database is locked" in msg or "database is busy" in msg
    return False


def sqlite_retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    装饰器：自动重试 SQLite 数据库锁定错误

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if not _is_lock_error(e):
                        raise
                    last_error = e
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2**attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.5)
                    sleep_time = delay + jitter
                    logger.debug(
                        "SQLite lock retry %d/%d after %.3fs: %s",
                        attempt + 1,
                        max_retries,
                        sleep_time,
                        e,
                    )
                    time.sleep(sleep_time)
                except (OSError, RuntimeError) as sleep_err:
                    # sleep 被中断或系统错误，直接抛出
                    raise sleep_err

            logger.error(
                "SQLite operation failed after %d retries: %s",
                max_retries,
                last_error,
            )
            raise last_error  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


def async_sqlite_retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    异步装饰器：自动重试 SQLite 数据库锁定错误（用于 async 函数）

    使用场景：
        当被装饰的函数是 ``async def`` 且运行在事件循环中时使用本装饰器。
        重试退避使用 ``await asyncio.sleep()``，不会阻塞事件循环，
        适合 FastAPI 异步路由 / 后台任务中调用 SQLite 的场景。

    与 :func:`sqlite_retry` 的区别：
        - ``sqlite_retry`` 适用于同步函数，使用 ``time.sleep``（会阻塞事件循环）
        - ``async_sqlite_retry`` 适用于异步函数，使用 ``asyncio.sleep``（让出事件循环）

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟（秒）
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if not _is_lock_error(e):
                        raise
                    last_error = e
                    if attempt >= max_retries:
                        break
                    delay = min(base_delay * (2**attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.5)
                    sleep_time = delay + jitter
                    logger.debug(
                        "SQLite lock async retry %d/%d after %.3fs: %s",
                        attempt + 1,
                        max_retries,
                        sleep_time,
                        e,
                    )
                    # 使用 asyncio.sleep 让出事件循环，避免阻塞其他协程
                    await asyncio.sleep(sleep_time)
                except (OSError, RuntimeError) as sleep_err:
                    # sleep 被中断或系统错误，直接抛出
                    raise sleep_err

            logger.error(
                "SQLite async operation failed after %d retries: %s",
                max_retries,
                last_error,
            )
            raise last_error  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


# aretry: async_sqlite_retry 的短别名，便于在 async 上下文中使用。
# 内部使用 asyncio.sleep 进行退避，不会阻塞事件循环。
aretry = async_sqlite_retry


class SqliteTransaction:
    """带重试的 SQLite 事务上下文管理器"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ):
        self._conn = conn
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    def execute(self, sql: str, params: Any = None) -> sqlite3.Cursor:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                if params is not None:
                    return self._conn.execute(sql, params)
                return self._conn.execute(sql)
            except sqlite3.OperationalError as e:
                if not _is_lock_error(e):
                    raise
                last_error = e
                if attempt >= self._max_retries:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                time.sleep(delay + random.uniform(0, delay * 0.5))

        raise last_error  # type: ignore[misc]

    def commit(self) -> None:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                self._conn.commit()
                return
            except sqlite3.OperationalError as e:
                if not _is_lock_error(e):
                    raise
                last_error = e
                if attempt >= self._max_retries:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                time.sleep(delay + random.uniform(0, delay * 0.5))

        raise last_error  # type: ignore[misc]


class AsyncSqliteTransaction:
    """带重试的异步 SQLite 事务上下文管理器

    使用场景：
        在 async 上下文（FastAPI 异步路由、后台任务）中执行 SQLite 事务时使用。
        重试退避使用 ``await asyncio.sleep()``，不会阻塞事件循环。

    与 :class:`SqliteTransaction` 的区别：
        - :class:`SqliteTransaction` 适用于同步代码，使用 ``time.sleep``
        - :class:`AsyncSqliteTransaction` 适用于异步代码，使用 ``asyncio.sleep``

    Note:
        SQLite 操作本身是同步的（``sqlite3`` 模块为阻塞 IO），本类仅将
        重试退避部分改为非阻塞。若需完全避免阻塞事件循环，建议配合
        ``asyncio.to_thread`` / 线程池执行实际的 SQL 调用。
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_delay: float = DEFAULT_BASE_DELAY,
        max_delay: float = DEFAULT_MAX_DELAY,
    ):
        self._conn = conn
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay

    async def execute(self, sql: str, params: Any = None) -> sqlite3.Cursor:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                if params is not None:
                    return self._conn.execute(sql, params)
                return self._conn.execute(sql)
            except sqlite3.OperationalError as e:
                if not _is_lock_error(e):
                    raise
                last_error = e
                if attempt >= self._max_retries:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                # 使用 asyncio.sleep 让出事件循环，避免阻塞其他协程
                await asyncio.sleep(delay + random.uniform(0, delay * 0.5))

        raise last_error  # type: ignore[misc]

    async def commit(self) -> None:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                self._conn.commit()
                return
            except sqlite3.OperationalError as e:
                if not _is_lock_error(e):
                    raise
                last_error = e
                if attempt >= self._max_retries:
                    break
                delay = min(self._base_delay * (2**attempt), self._max_delay)
                # 使用 asyncio.sleep 让出事件循环，避免阻塞其他协程
                await asyncio.sleep(delay + random.uniform(0, delay * 0.5))

        raise last_error  # type: ignore[misc]
