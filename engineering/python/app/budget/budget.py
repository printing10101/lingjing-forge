"""
Budget Check Pre-execution Module

Implements resource budget verification before task execution, including GPU memory
availability, inference quota validation, and multi-dimensional resource tracking.

本模块为门面：实现已拆分至 _budget_models / _tracker / _budget_config_mixin / _budget_check_mixin。
"""

from __future__ import annotations

import logging
import sqlite3
import threading

from pathlib import Path

from app.models.budget import (  # noqa: F401
    BudgetLevel,
    BudgetStatus,
    ResourceType,
)
from app.utils.utils import get_output_dir
from app.utils.sqlite_pool import get_sqlite_manager
from app.budget._budget_check_mixin import _BudgetCheckMixin
from app.budget._budget_config_mixin import _BudgetConfigMixin
from app.budget._budget_models import (  # noqa: F401
    BudgetCheckResult,
    BudgetLimit,
    BudgetUsage,
)
from app.budget._tracker import ResourceTracker  # noqa: F401

logger = logging.getLogger(__name__)


class BudgetManager(_BudgetConfigMixin, _BudgetCheckMixin):
    """预算管理器"""

    def __init__(self, db_path: str | None = None):
        """
        初始化预算管理器

        Args:
            db_path: SQLite数据库路径
        """
        if db_path is None:
            db_path = str(get_output_dir("data") / "budget.db")

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.tracker = ResourceTracker()
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("budget", db_path=self.db_path)
        self._conn = self._pool.get_connection()
        self._lock = threading.RLock()
        self._closed = False  # P3 幂等性标志位
        self._init_schema()
        self._load_default_budgets()

        logger.info("BudgetManager initialized at %s", db_path)

    def _init_schema(self) -> None:
        """初始化数据库模式"""
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS budget_limits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                resource_type TEXT NOT NULL,
                limit_value REAL NOT NULL,
                warning_threshold REAL DEFAULT 0.8,
                hard_stop_threshold REAL DEFAULT 1.0,
                budget_level TEXT DEFAULT 'global',
                scope_id TEXT DEFAULT 'default',
                reset_interval TEXT DEFAULT 'daily',
                created_at REAL DEFAULT (strftime('%s', 'now')),
                UNIQUE(resource_type, budget_level, scope_id)
            );

            CREATE TABLE IF NOT EXISTS budget_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                usage_value REAL NOT NULL,
                limit_value REAL NOT NULL,
                usage_ratio REAL NOT NULL,
                status TEXT NOT NULL,
                recorded_at REAL DEFAULT (strftime('%s', 'now'))
            );

            CREATE TABLE IF NOT EXISTS budget_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                message TEXT NOT NULL,
                resource_type TEXT,
                usage_ratio REAL,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            );
        """)
            self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池

        幂等性：
            多次调用安全；已关闭后再次调用为 no-op。
        """
        if self._closed:
            return
        if hasattr(self, "_conn") and self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None
            logger.info("BudgetManager closed")
        self._closed = True

    def __del__(self) -> None:
        try:
            self.close()
        except (sqlite3.ProgrammingError, AttributeError) as e:
            # 析构时数据库连接已关闭或对象处于无效状态属于正常 GC 路径
            logger.debug("Cleanup during deallocation skipped: %s", e)


class _BudgetManagerHolder:
    """Thread-safe lazy holder for the :class:`BudgetManager` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: BudgetManager | None = None

    def get(self) -> BudgetManager:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = BudgetManager()
            return self._instance

    def init(self, db_path: str | None = None) -> BudgetManager:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = BudgetManager(db_path)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _BudgetManagerHolder()


def get_budget_manager() -> BudgetManager:
    """获取共享的 :class:`BudgetManager` 单例；首次访问时懒初始化。

    .. deprecated:: V3.0 (2026-08-02)
    """
    return _holder.get()


def init_budget_manager(db_path: str | None = None) -> BudgetManager:
    """初始化预算管理器，行为与重构前完全一致。"""
    return _holder.init(db_path)
