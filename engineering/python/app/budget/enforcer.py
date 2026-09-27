"""预算强制执行器.

从原 ``app/budget/budget_enforcer.py`` 拆分而来，聚焦于预算执行职责：
预执行原子检查、级联预算状态处理、周期性自动重置、告警生成与执行日志。

向后兼容：``app/budget/budget_enforcer.py`` 仍作为 re-export shim 暴露
本模块的全部公开符号。
"""

import logging
import sqlite3
import threading
from pathlib import Path
from typing import ClassVar, cast
from collections.abc import Callable

from app.models.budget import (
    BudgetAlert,
    BudgetPolicy,
)
from app.services._shared.service_base import BaseSingletonService
from app.utils.sqlite_pool import get_sqlite_manager
from app.utils.utils import get_output_dir
from app.budget._budget_policy_mixin import _BudgetPolicyMixin
from app.budget._budget_core_mixin import _BudgetCoreMixin
from app.budget._budget_alert_mixin import _BudgetAlertMixin
from app.budget._budget_action_mixin import _BudgetActionMixin

logger = logging.getLogger(__name__)


class BudgetEnforcer(_BudgetPolicyMixin, _BudgetCoreMixin, _BudgetAlertMixin, _BudgetActionMixin, BaseSingletonService):
    """预算强制执行器.

    单例管理由 ``BaseSingletonService`` 提供（``get_instance`` / ``reset_instance``）。
    需要「强制重新创建并指定 db_path」时使用 :meth:`init` 类方法。
    """

    # 类变量：``init(db_path)`` 写入此变量，``__init__`` 在无显式参数时读取它。
    # 这样既兼容 ``BaseSingletonService.get_instance()`` 的无参构造，又保留了
    # 原 ``init_budget_enforcer(db_path)`` 接口的「指定路径」能力。
    _db_path: ClassVar[str | None] = None

    def __init__(self, db_path: str | None = None):
        # 优先使用显式传入的 db_path，其次回退到类变量（由 init() 设置），
        # 最后回退到默认路径。保持与重构前 holder 行为一致。
        if db_path is not None:
            actual_path = db_path
        elif type(self)._db_path is not None:
            actual_path = cast(str, type(self)._db_path)
        else:
            actual_path = str(get_output_dir("data") / "budget_enforcer.db")

        db_dir = Path(actual_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = actual_path
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("budget_enforcer", db_path=self.db_path)
        self._conn = self._pool.get_connection()
        self._policies: dict[str, BudgetPolicy] = {}
        self._alert_callbacks: list[Callable[[BudgetAlert], None]] = []
        self._task_canceller: Callable[[str], None] | None = None
        self._agent_suspender: Callable[[str, str], None] | None = None
        self._init_schema()
        self._load_policies()
        self._load_default_policies()

        logger.info("BudgetEnforcer initialized at %s", self.db_path)

    def close(self) -> None:
        """关闭执行器，归还连接到连接池"""
        if hasattr(self, "_conn") and self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None
            logger.info("BudgetEnforcer closed")

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS budget_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT 'default',
                resource_type TEXT NOT NULL,
                limit_value REAL NOT NULL DEFAULT 100.0,
                period TEXT NOT NULL DEFAULT 'daily',
                warning_threshold REAL NOT NULL DEFAULT 0.8,
                hard_stop INTEGER NOT NULL DEFAULT 1,
                auto_notify INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                current_usage REAL NOT NULL DEFAULT 0.0,
                last_reset_at REAL,
                created_at REAL,
                updated_at REAL,
                UNIQUE(level, scope_id, resource_type)
            );

            CREATE INDEX IF NOT EXISTS idx_policy_level ON budget_policies(level);
            CREATE INDEX IF NOT EXISTS idx_policy_scope ON budget_policies(scope_id);

            CREATE TABLE IF NOT EXISTS budget_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT 'default',
                resource_type TEXT NOT NULL,
                status TEXT NOT NULL,
                current_usage REAL NOT NULL DEFAULT 0.0,
                limit_value REAL NOT NULL DEFAULT 0.0,
                usage_ratio REAL NOT NULL DEFAULT 0.0,
                message TEXT DEFAULT '',
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_alert_created ON budget_alerts(created_at);
            CREATE INDEX IF NOT EXISTS idx_alert_status ON budget_alerts(status);
            CREATE INDEX IF NOT EXISTS idx_alert_read ON budget_alerts(is_read);

            CREATE TABLE IF NOT EXISTS budget_reset_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT 'default',
                resource_type TEXT NOT NULL,
                period TEXT NOT NULL,
                usage_before_reset REAL NOT NULL DEFAULT 0.0,
                limit_at_reset REAL NOT NULL DEFAULT 0.0,
                reset_at REAL
            );

            CREATE TABLE IF NOT EXISTS enforcement_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                level TEXT NOT NULL,
                scope_id TEXT NOT NULL DEFAULT 'default',
                resource_type TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                executed_at REAL
            );
        """)
        self._conn.commit()

    # H2 bug 修复：删除重复定义的 close 方法。
    # 原代码在第 83-88 行已定义 close（正确：归还连接到池），
    # 此处又定义了 close（错误：直接 close 连接，导致连接池泄漏）。
    # Python 类中后定义的方法会覆盖前者，使正确的版本永不生效。
    # 此处不再重复定义，由第 83 行的版本统一处理 close 逻辑。

    def __del__(self) -> None:
        try:
            self.close()
        except (sqlite3.ProgrammingError, AttributeError) as e:
            # 析构时数据库连接已关闭或对象处于无效状态属于正常 GC 路径
            logger.debug("Cleanup during deallocation skipped: %s", e)

    # 单例生命周期扩展

    @classmethod
    def init(cls, db_path: str | None = None) -> "BudgetEnforcer":
        """强制重新创建单例实例（用于启动时指定 db_path 的场景）。

        与 :meth:`get_instance` 的「懒初始化」不同，``init`` 总是创建新实例并
        覆盖已有的单例。行为与重构前 ``_BudgetEnforcerHolder.init`` 一致。

        Parameters
        ----------
        db_path:
            SQLite 数据库路径。``None`` 表示使用默认路径
            (``<output>/data/budget_enforcer.db``)。
        """
        with cls._service_lock:
            cls._db_path = db_path
            cls._service_singleton = cls()
            return cls._service_singleton

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例并清除缓存的 db_path。

        扩展了 ``BaseSingletonService.reset_instance``：同时清除 ``_db_path``，
        以保持与原 ``_BudgetEnforcerHolder.reset`` 的行为一致——即 reset 后再次
        ``get_instance`` 会使用默认路径，而非上次 ``init`` 设置的路径。
        """
        with cls._service_lock:
            cls._service_singleton = None
            cls._db_path = None


class _BudgetEnforcerHolder:
    """[Deprecated] 已被 :class:`BaseSingletonService` 单例机制取代.

    本类仅作为占位符保留，避免破坏 ``app/budget/budget_enforcer.py`` re-export
    shim 的导入。新代码应直接使用 :meth:`BudgetEnforcer.get_instance` /
    :meth:`BudgetEnforcer.init` / :meth:`BudgetEnforcer.reset_instance`。
    """

    def __init__(self) -> None:
        # 保留原属性名以兼容可能的外部反射访问
        self._lock = threading.Lock()
        self._instance: BudgetEnforcer | None = None

    def get(self) -> BudgetEnforcer:
        return BudgetEnforcer.get_instance()  # type: ignore[return-value]

    def init(self, db_path: str | None = None) -> BudgetEnforcer:
        return BudgetEnforcer.init(db_path)

    def reset(self) -> None:
        BudgetEnforcer.reset_instance()


_budget_holder = _BudgetEnforcerHolder()


def get_budget_enforcer() -> BudgetEnforcer:
    """获取共享的 :class:`BudgetEnforcer` 单例；首次访问时懒初始化。

    .. deprecated:: V3.0 (2026-08-02)
        本函数保留向后兼容。

    Returns:
        :class:`BudgetEnforcer` 实例（应用生命周期内同一实例）。

    Note:
        同时是 FastAPI 依赖工厂，可直接用于 ``Depends(get_budget_enforcer)``。
        实现是线程安全的，行为与重构前完全一致——内部委托给
        :meth:`BudgetEnforcer.get_instance`。
    """
    return BudgetEnforcer.get_instance()  # type: ignore[return-value]


def init_budget_enforcer(db_path: str | None = None) -> BudgetEnforcer:
    """初始化预算执行器，行为与重构前完全一致。

    内部委托给 :meth:`BudgetEnforcer.init`：强制重新创建单例并指定 db_path。
    """
    return BudgetEnforcer.init(db_path)


__all__ = [
    "BudgetEnforcer",
    "_BudgetEnforcerHolder",
    "_budget_holder",
    "get_budget_enforcer",
    "init_budget_enforcer",
]
