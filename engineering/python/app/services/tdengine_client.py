"""TDengine time-series database client wrapper for CNC sensor data.

Provides a thread-safe lazy connection singleton and high-level helpers for
common time-series operations (database management, table creation, batch
insert, time-range query).  The implementation follows the same patterns as
:mod:`app.services.redis_client` and :mod:`app.database.connection`:

- A ``_TdengineHolder`` internal class encapsulates the connection state
  instead of leaking module-level globals.
- :func:`get_tdengine` is exposed as a FastAPI dependency factory.
- The public API is fully ``async`` even though the underlying ``taospy``
  driver is synchronous - blocking calls are offloaded via
  :func:`asyncio.to_thread` so the FastAPI event loop is never blocked.

Configuration is read from environment variables (with sensible defaults
suitable for local docker compose deployments):

- ``TDENGINE_URL``  - native-protocol URL, e.g. ``taos://root:<password>@tdengine:6030``
- ``TDENGINE_USER``  - user name (default ``root``)
- ``TDENGINE_PASSWORD`` - password (must be set via environment variable)
- ``TDENGINE_DB`` - default database name (default ``lnn_tsdb``)
- ``TDENGINE_CONNECT_TIMEOUT`` - connection timeout in seconds (default 10)
- ``TDENGINE_HEALTH_URL`` - REST health endpoint URL (optional)

Example:

    >>> from app.services.tdengine_client import get_tdengine
    >>> c = get_tdengine()
    >>> c.execute("SHOW DATABASES")
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Sequence

logger = logging.getLogger(__name__)


# Configuration


@dataclass
class TDengineConfig:
    """TDengine 连接配置，从环境变量惰性加载。"""

    url: str = field(default_factory=lambda: os.environ.get("TDENGINE_URL", ""))
    user: str = field(default_factory=lambda: os.environ.get("TDENGINE_USER", "root"))
    password: str = field(default_factory=lambda: os.environ.get("TDENGINE_PASSWORD", ""))
    database: str = field(default_factory=lambda: os.environ.get("TDENGINE_DB", "lnn_tsdb"))
    connect_timeout: int = field(default_factory=lambda: int(os.environ.get("TDENGINE_CONNECT_TIMEOUT", "10")))
    health_url: str = field(
        # 统一使用 127.0.0.1（项目约定），避免 localhost 解析差异
        default_factory=lambda: os.environ.get("TDENGINE_HEALTH_URL", "http://127.0.0.1:6041/api/health")
    )

    @property
    def enabled(self) -> bool:
        return bool(self.url)


# Internal holder (替代 ``global _`` 模式)
# 设计要点（与 ``redis_client._RedisHolder`` / ``connection._DatabaseSingletons``
# 保持一致）：
# - 将可变状态封装在 ``_TdengineHolder`` 内部，避免模块顶层出现可写变量。
# - 使用 ``threading.Lock`` 保证多线程首次创建时仅产生一个连接。
# - ``get()`` 快速路径不持锁，热路径性能零开销。


class _TdengineHolder:
    """线程安全的 TDengine 连接单例容器。"""

    # 连接失败后的"冷却时间"：在窗口期内不再尝试重连，避免反复触发慢 IO
    _COOLDOWN_SECONDS = 5.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._client: Any | None = None
        self._connect_attempts: int = 0
        self._last_failure_ts: float = 0.0

    def get(self) -> Any | None:
        # 快速路径：已有客户端则直接返回
        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is not None:
                return self._client

            # 冷却期短路：避免对持续不可用的服务做重复连接尝试
            if self._last_failure_ts > 0 and (time.monotonic() - self._last_failure_ts) < self._COOLDOWN_SECONDS:
                return None

            config = TDengineConfig()
            if not config.enabled:
                logger.warning("TDENGINE_URL not configured, TDengine disabled")
                return None

            try:
                import taos

                self._connect_attempts += 1
                client = taos.connect(
                    url=config.url,
                    user=config.user,
                    password=config.password,
                    timeout=config.connect_timeout,
                )
                # 立即验证连接：执行一条轻量级语句
                client.execute("SELECT SERVER_STATUS()")
                self._client = client
                self._last_failure_ts = 0.0
                logger.info("TDengine client connected: %s", config.url)
                return client
            except ImportError:
                logger.warning("taospy library not installed, TDengine client disabled")
                # 缺失原生库视为永久性错误：拉长冷却时间
                self._last_failure_ts = time.monotonic()
                return None
            except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError) as e:
                logger.error("Failed to connect to TDengine: %s", e)
                self._last_failure_ts = time.monotonic()
                return None

    def close(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            # 重置冷却状态：显式 close 后允许下一次 get 重新尝试连接
            self._last_failure_ts = 0.0
        if client is not None:
            try:
                client.close()
            except (ConnectionError, OSError, RuntimeError) as close_err:
                # 关闭失败不应阻塞主流程，仅记录以便排查
                logger.debug(
                    "TDengine client close failed, continuing shutdown: %s",
                    close_err,
                    exc_info=True,
                )
            logger.info("TDengine client closed")


_holder = _TdengineHolder()


# 异步包装工具
# ``taospy`` 驱动是同步阻塞 IO，所有调用必须放入线程池以避免阻塞事件循环。


async def _run_sync(func, /, *args, **kwargs):
    """在默认线程池中执行同步函数并返回结果。

    用 ``asyncio.to_thread`` 替代过时的 ``run_in_executor`` 写法；
    ``asyncio.to_thread`` 自 Python 3.9 起为推荐用法。
    """
    return await asyncio.to_thread(func, *args, **kwargs)


# 公共 API（兼容 FastAPI 依赖注入风格）


def get_tdengine() -> Any | None:
    """获取共享的 TDengine 客户端（同步）。

    Returns:
        ``taos.TaosConnection`` 实例；如果未配置 ``TDENGINE_URL`` 或连接失败则
        返回 ``None``。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_tdengine)``。
        公共 API 设计为同步是因为 ``taospy`` 驱动本身是同步的，且 FastAPI
        端点可通过 ``await asyncio.to_thread(...)`` 自行调度；保持同步返回
        类型有助于在同步代码（如脚本/Cron）中也直接复用。
    """
    return _holder.get()


async def get_tdengine_async() -> Any | None:
    """异步获取 TDengine 客户端，避免在事件循环中执行连接初始化。"""
    return await _run_sync(_holder.get)


async def close_tdengine() -> None:
    """关闭并释放 TDengine 客户端（FastAPI shutdown 时调用）。"""
    await _run_sync(_holder.close)


async def check_tdengine_health() -> dict:
    """健康检查：验证连接可用并返回基础状态信息。"""
    client = await get_tdengine_async()
    if client is None:
        return {"status": "disabled", "message": "TDENGINE_URL not configured"}
    try:
        result = await _run_sync(client.execute, "SELECT SERVER_STATUS()")
        rows = list(result) if result is not None else []
        return {
            "status": "healthy",
            "server_status": rows[0][0] if rows else "unknown",
            "database": TDengineConfig().database,
        }
    except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError) as e:
        logger.warning("TDengine健康检查失败: %s", e, exc_info=True)
        return {"status": "unhealthy", "error": f"tdengine: {type(e).__name__}"}


# 业务级辅助函数（高层 API）
#
# SQL 安全模型：
# TDengine Python 驱动 (taos) 的 DDL 语句（CREATE/USE/ALTER DATABASE|TABLE）
# 不支持 ``?`` 参数占位符，因此标识符必须经过白名单校验后拼接。
#
# 所有 SQL 构建必须遵循：
# 1. 标识符（库名/表名/列名） _safe_ident() 白名单校验后返回
# 2. 时间字面量 _validate_timestamp_literal() 校验后返回
# 3. 列表达式 正则白名单匹配后返回
# 4. 数值参数 int()/float() 强制转换
#
# 禁止：在未调用以上安全函数的情况下，将任何外部输入拼入 SQL 字符串。

import re as _re

# 标识符白名单：仅允许字母/下划线开头，后接字母/数字/下划线，长度 1-63
_IDENTIFIER_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _safe_ident(name: str, kind: str = "identifier") -> str:
    """安全包装器：校验 TDengine 标识符后原样返回。

    此函数将 ``_validate_identifier`` 的校验与返回值绑定，
    确保标识符在拼入 SQL 之前必须经过白名单校验，无法绕过。

    Args:
        name: 待校验的标识符。
        kind: 标识符类型（用于错误信息），如 "database" / "table"。

    Returns:
        校验通过的原始标识符字符串。

    Raises:
        ValueError: 当标识符不符合白名单规则时。
    """
    _validate_identifier(name, kind)
    return name


def _validate_identifier(name: str, kind: str = "identifier") -> None:
    """校验 TDengine 标识符（库名/表名/列名），防止 SQL 注入。

    Args:
        name: 待校验的标识符。
        kind: 标识符类型（用于错误信息），如 "database" / "table"。

    Raises:
        ValueError: 当标识符不符合白名单规则时。
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {kind} identifier: {name!r}. Must match ^[A-Za-z_][A-Za-z0-9_]{{0,62}}$")


async def ensure_database(database: str | None = None) -> bool:
    """确保指定数据库存在，不存在则创建。

    Args:
        database: 数据库名，默认使用 :data:`TDengineConfig.database`。

    Returns:
        ``True`` 表示数据库已存在或创建成功，``False`` 表示失败。
    """
    client = await get_tdengine_async()
    if client is None:
        return False
    db_name = _safe_ident(database or TDengineConfig().database, "database")

    def _ensure() -> None:
        # db_name 已通过 _safe_ident() 白名单校验
        client.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")

    try:
        await _run_sync(_ensure)
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        logger.error("Failed to ensure database %s: %s", db_name, e)
        return False


async def use_database(database: str | None = None) -> bool:
    """切换当前连接的活动数据库。"""
    client = await get_tdengine_async()
    if client is None:
        return False
    db_name = _safe_ident(database or TDengineConfig().database, "database")
    try:
        await _run_sync(client.execute, f"USE {db_name}")
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        logger.error("Failed to use database %s: %s", db_name, e)
        return False


async def create_table_if_not_exists(
    table_name: str,
    columns: Sequence[str],
    database: str | None = None,
) -> bool:
    """创建超级表/子表占位方法（按需扩展）。

    本任务范围内 ``columns`` 接受完整的 ``CREATE TABLE`` 列定义字符串
    （例如 ``"(ts TIMESTAMP, value DOUBLE)"``）。

    安全：列定义仅允许字母/数字/下划线/括号/逗号/空格及常见 SQL 类型
    关键字，禁止分号与注释，防止 SQL 注入。
    """
    client = await get_tdengine_async()
    if client is None:
        return False
    db_name = _safe_ident(database or TDengineConfig().database, "database")
    table_name = _safe_ident(table_name, "table")
    # 列定义白名单：允许字母/数字/下划线/空格/逗号/括号及常见 SQL 类型关键字
    # 禁止分号、注释（--、/* */）、引号转义等注入向量
    _COLUMN_DEF_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_(),\s]*$")
    for col_def in columns:
        if not isinstance(col_def, str) or not _COLUMN_DEF_RE.match(col_def.strip()):
            logger.error(
                "Invalid column definition rejected (SQL injection defense): %r",
                col_def,
            )
            return False
    cols = " ".join(columns).strip()
    sql = f"CREATE TABLE IF NOT EXISTS {db_name}.{table_name} {cols}"
    try:
        await _run_sync(client.execute, sql)
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        logger.error("Failed to create table %s.%s: %s", db_name, table_name, e)
        return False


async def insert_rows(
    table_name: str,
    rows: Sequence[Sequence[Any]],
    database: str | None = None,
) -> int:
    """批量插入时序数据到指定表。

    Args:
        table_name: 目标表名。
        rows: 数据行序列，每行对应一条记录（列顺序需与表定义一致）。
        database: 数据库名，默认使用 :data:`TDengineConfig.database`。

    Returns:
        成功插入的行数；失败时返回 ``-1``。
    """
    if not rows:
        return 0
    client = await get_tdengine_async()
    if client is None:
        return -1
    db_name = _safe_ident(database or TDengineConfig().database, "database")
    table_name = _safe_ident(table_name, "table")
    sql = f"INSERT INTO {db_name}.{table_name} VALUES"
    try:

        def _insert() -> int:
            # ``taos`` 驱动提供了 ``insert_lines`` 方法（更高吞吐），
            # 此处使用 ``execute`` 拼接方式，语义清晰且与 SQL 一致。
            values = " ".join("(" + ",".join(_format_value(v) for v in row) + ")" for row in rows)
            affected = client.execute(sql + " " + values)
            return int(affected) if affected is not None else len(rows)

        return await _run_sync(_insert)
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        logger.error("Failed to insert rows into %s.%s: %s", db_name, table_name, e)
        return -1


async def query_time_range(
    table_name: str,
    start_ts: Any,
    end_ts: Any,
    columns: str = "*",
    database: str | None = None,
    limit: int = 10000,
) -> list[list[Any]]:
    """按时间范围查询时序数据。

    Args:
        table_name: 表名。
        start_ts: 起始时间戳（任意 TDengine 支持的格式，常见为 ``datetime`` /
            ``pandas.Timestamp`` / 毫秒整数）。
        end_ts: 结束时间戳。
        columns: SELECT 列表达式，默认 ``*``。
        database: 数据库名，默认使用 :data:`TDengineConfig.database`。
        limit: 返回行数上限，默认 10000。

    Returns:
        行数据列表；查询失败时返回空列表。
    """
    client = await get_tdengine_async()
    if client is None:
        return []
    db_name = _safe_ident(database or TDengineConfig().database, "database")
    table_name = _safe_ident(table_name, "table")
    start = _format_value(start_ts)
    end = _format_value(end_ts)
    # P2-3-1 修复：对 _format_value 输出做二次校验，确保时间字面量仅为
    # 纯数字（毫秒时间戳）或符合 ISO 时间格式，防止通过时间戳参数注入 SQL。
    start = _validate_timestamp_literal(start)
    end = _validate_timestamp_literal(end)
    # columns 参数允许传入 "*" 或列名列表，但需限制为白名单字符
    # 防止通过 columns 参数注入
    if not _re.match(r"^[A-Za-z_*][A-Za-z0-9_*,\s]*$", columns):
        logger.error("Invalid columns expression: %r", columns)
        return []
    sql = (
        f"SELECT {columns} FROM {db_name}.{table_name} "
        f"WHERE ts >= {start} AND ts <= {end} "
        f"ORDER BY ts ASC LIMIT {int(limit)}"
    )
    try:

        def _query() -> list[list[Any]]:
            result = client.execute(sql)
            if result is None:
                return []
            rows: list[list[Any]] = []
            for row in result:
                rows.append([_coerce(v) for v in row])
            return rows

        return await _run_sync(_query)
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        logger.error("Failed to query time range on %s.%s: %s", db_name, table_name, e)
        return []


async def execute(sql: str) -> Any:
    """执行任意 SQL 语句（不返回结果集）。"""
    client = await get_tdengine_async()
    if client is None:
        return None
    try:
        return await _run_sync(client.execute, sql)
    except (ConnectionError, OSError, TimeoutError, ValueError, TypeError) as e:
        # [S-H2] SQL 入日志脱敏：替换引号内的字面量为 ***，防止敏感数据（密码/Token/PII）入日志
        # 仅保留 SQL 结构便于排查，截断到 200 字符避免日志膨胀
        safe_sql = _re.sub(r"'[^']*'", "'***'", sql)[:200]
        logger.error("TDengine execute failed: %s | sql=%s", e, safe_sql)
        return None


# 值格式化辅助函数


def _format_value(value: Any) -> str:
    """将 Python 值格式化为 TDengine SQL 字面量。

    - 字符串/字节 → 单引号包裹并转义
    - ``None`` → ``NULL``
    - ``datetime`` / ``pandas.Timestamp`` → ``'YYYY-MM-DD HH:MM:SS.ffffff'``
    - bool → 1/0
    - 数值（int / float / Decimal） → 原样
    - 其他 → str(value) 后用单引号包裹
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    # pandas Timestamp 与 datetime.datetime 都需要在导入前用 getattr 容错
    pd_ts = getattr(__import__("pandas", fromlist=["Timestamp"]), "Timestamp", None)
    if pd_ts is not None and isinstance(value, pd_ts):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S.%f')}'"
    # datetime / date
    from datetime import date as _date
    from datetime import datetime as _dt

    if isinstance(value, _dt):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S.%f')}'"
    if isinstance(value, _date):
        return f"'{value.isoformat()}'"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return "'" + value.decode("utf-8").replace("'", "''") + "'"
        except (UnicodeDecodeError, ValueError) as e:
            logger.debug("字节数据 UTF-8 解码失败，使用十六进制表示: %s", e)
            return "'" + value.hex() + "'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


# P2-3-1 修复：时间字面量白名单校验，防止通过时间戳参数注入 SQL。
# 合法时间字面量格式：
# - "NULL"（空值）
# - 纯数字（毫秒时间戳，如 "1700000000000"）
# - 单引号包裹的 ISO 时间字符串（如 "'2024-01-01 00:00:00.000000'"）
_TIMESTAMP_DIGIT_RE = _re.compile(r"^\d+$")
_TIMESTAMP_QUOTED_RE = _re.compile(r"^'\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2}(\.\d+)?)?'$")


def _validate_timestamp_literal(val: str) -> str:
    """校验 _format_value 输出是否为合法的时间字面量。

    防止通过 start_ts/end_ts 参数注入 SQL。仅接受以下格式：
    1. ``NULL``（空值）
    2. 纯数字（毫秒时间戳）
    3. 单引号包裹的 ``YYYY-MM-DD HH:MM:SS[.ffffff]`` 时间字符串

    Args:
        val: _format_value 的输出字符串。

    Returns:
        校验通过则原样返回 val。

    Raises:
        ValueError: 当 val 不匹配任何合法时间字面量格式时。
    """
    if val == "NULL":
        return val
    if _TIMESTAMP_DIGIT_RE.match(val):
        return val
    if _TIMESTAMP_QUOTED_RE.match(val):
        return val
    raise ValueError(f"Invalid timestamp literal (potential SQL injection): {val!r}")


def _coerce(value: Any) -> Any:
    """将 ``taos`` 驱动返回的字段值转换为更易处理的 Python 原生类型。"""
    # ``datetime`` / 数值 / 字符串 保持原样
    return value


# 模块导出


__all__ = [
    "TDengineConfig",
    "get_tdengine",
    "get_tdengine_async",
    "close_tdengine",
    "check_tdengine_health",
    "ensure_database",
    "use_database",
    "create_table_if_not_exists",
    "insert_rows",
    "query_time_range",
    "execute",
]
