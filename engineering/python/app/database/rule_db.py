"""
工艺规则 SQLite 数据库模块

提供规则的持久化存储，支持 CRUD 操作、分组管理、导入导出和数据备份。
所有数据存储在本地文件系统中，不依赖任何云端服务。

本模块为门面：实现已拆分至 _constants / _version / _models / _rule_crud_mixin / _group_crud_mixin / _transfer_mixin。
"""

from __future__ import annotations

import logging
import sqlite3


from app.utils.sqlite_pool import get_sqlite_manager

from app.database._constants import CURRENT_FORMAT_VERSION, DB_PATH  # noqa: F401
from app.database._group_crud_mixin import _GroupCrudMixin
from app.database._models import (  # noqa: F401
    ProcessRule,
    RuleCondition,
    RuleGroup,
    RuleResult,
)
from app.database._rule_crud_mixin import _RuleCrudMixin
from app.database._transfer_mixin import _TransferMixin
from app.database._version import (  # noqa: F401
    check_version_compatibility,
    get_project_version,
    parse_version,
)

logger = logging.getLogger(__name__)


class RuleDatabase(_RuleCrudMixin, _GroupCrudMixin, _TransferMixin):
    """工艺规则 SQLite 数据库操作类"""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or str(DB_PATH)
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("process_rules", db_path=self.db_path)
        self._conn: sqlite3.Connection | None = None
        self._closed = False  # P3 幂等性标志位
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = self._pool.get_connection()
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rule_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                group_id INTEGER,
                conditions_json TEXT NOT NULL,
                logic_operator TEXT NOT NULL DEFAULT 'AND',
                result_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                priority INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (group_id) REFERENCES rule_groups(id) ON DELETE SET NULL
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_rules_group_id ON rules(group_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_rules_status ON rules(status)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_rules_name ON rules(name)
        """)

        conn.commit()
        logger.info("工艺规则数据库初始化完成: %s", self.db_path)

    def close(self):
        """关闭数据库连接，归还连接到连接池并关闭池中所有连接

        .. note::
            测试场景中临时 db 文件需要在 teardown 时被删除，但连接池中
            可能仍持有该文件的空闲连接句柄，导致 Windows 上出现
            ``PermissionError [WinError 32]``。因此本方法在归还当前连接后，
            额外调用 ``close_all()`` 释放池中所有连接，确保文件句柄完全释放。

        幂等性：
            多次调用安全；已关闭后再次调用为 no-op。
        """
        if self._closed:
            return
        if self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None
        # 关闭池中所有空闲连接，避免临时文件被锁定
        try:
            self._pool.close_all()
        except Exception as e:
            logger.warning("Failed to close connection pool: %s", e)
        logger.info("RuleDatabase closed")
        self._closed = True

    def __del__(self):
        self.close()

    def _now(self) -> str:
        """返回当前时间的 ISO8601 带时区格式字符串（UTC）。

        统一时间戳格式为 ``YYYY-MM-DDTHH:MM:SSZ``（UTC 零时区），
        与 DDL 中的 ``DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))`` 一致。
        """
        from app.utils.time import utcnow_seconds_iso_z

        return utcnow_seconds_iso_z()


class _RuleDbHolder:
    """Thread-safe lazy holder for the :class:`RuleDatabase` singleton."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._instance: RuleDatabase | None = None

    def get(self) -> RuleDatabase:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            # 双重检查：可能在获取锁的过程中其他线程已创建实例
            if self._instance is not None:
                return self._instance
            self._instance = RuleDatabase()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _RuleDbHolder()

# 模块级全局变量，用于测试场景中临时替换全局 RuleDatabase 实例。
# 测试可通过 ``rule_db_module._global_db = temp_db`` 直接覆盖，
# ``get_rule_db()`` 会优先返回此变量（若非 None），否则回退到 holder 单例。
# 生产代码不应直接修改此变量。
_global_db: RuleDatabase | None = None


def get_rule_db() -> RuleDatabase:
    """获取共享的 :class:`RuleDatabase` 单例；首次访问时懒初始化。

    优先返回测试可覆盖的 ``_global_db``（若已设置），否则使用 holder 创建单例。

    Returns:
        :class:`RuleDatabase` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_rule_db)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    global _global_db
    if _global_db is not None:
        return _global_db
    return _holder.get()
