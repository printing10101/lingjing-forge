from __future__ import annotations

import logging
import threading

from collections.abc import Callable

from app.tasks.execution_lock import (
    ExecutionLockStore,
    get_execution_lock_store,
)

# 模型/常量经本模块再导出（dependencies / api 导入方依赖），__all__ 声明避免 ruff F401
from app.tasks._checkout_models import (
    BUDGET_RETRY_DELAY_MINUTES,
    CONFLICT_RETRY_DELAY_MINUTES,
    GPU_RETRY_DELAY_MINUTES,
    MAX_RETRY_COUNT,
    TaskRecord,
)

from app.tasks._checkout_models import (
    AgentMode,
    CheckoutFailureReason,
    CheckoutPriority,
    CheckoutQueueEntry,
    CheckoutRequest,
    CheckoutResult,
    CheckoutStatus,
    TaskStatus,
)

# 模型/常量经本模块再导出（api/v1/task_checkout.py、dependencies 等导入方依赖），
# __all__ 声明避免 ruff F401 误删。
__all__ = [
    "TaskCheckoutManager",
    "get_checkout_manager",
    "init_checkout_manager",
    "MAX_RETRY_COUNT",
    "BUDGET_RETRY_DELAY_MINUTES",
    "GPU_RETRY_DELAY_MINUTES",
    "CONFLICT_RETRY_DELAY_MINUTES",
    "AgentMode",
    "CheckoutFailureReason",
    "CheckoutPriority",
    "CheckoutQueueEntry",
    "CheckoutRequest",
    "CheckoutResult",
    "CheckoutStatus",
    "TaskRecord",
    "TaskStatus",
]

from app.utils.utils import get_output_dir
from app.utils.sqlite_pool import get_sqlite_manager

from app.tasks._task_checkout_locks_mixin import _TaskCheckoutLocksMixin
from app.tasks._task_checkout_ops_mixin import _TaskCheckoutOpsMixin
from app.tasks._task_checkout_queue_mixin import _TaskCheckoutQueueMixin

logger = logging.getLogger(__name__)


class TaskCheckoutManager(_TaskCheckoutLocksMixin, _TaskCheckoutQueueMixin, _TaskCheckoutOpsMixin):
    def __init__(self, lock_store: ExecutionLockStore, db_path: str | None = None):
        self._lock_store = lock_store
        if db_path is None:
            db_path = str(get_output_dir("data") / "task_checkout.db")
        self._db_path = db_path
        self._queue_lock = threading.Lock()
        self._checkout_lock = threading.Lock()
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("task_checkout", db_path=self._db_path)
        self._conn = self._pool.get_connection()
        self._budget_checker: Callable[[str, str | None], bool] | None = None
        self._gpu_checker: Callable[[float], bool] | None = None
        self._ensure_tables()

    def set_budget_checker(self, checker: Callable[[str, str | None], bool]):
        self._budget_checker = checker

    def set_gpu_checker(self, checker: Callable[[float], bool]):
        self._gpu_checker = checker

    def _row_to_task(self, row) -> TaskRecord:
        import json

        blockers = []
        try:
            blockers = json.loads(row["blockers"] or "[]")
        except (json.JSONDecodeError, TypeError):
            blockers = []

        return TaskRecord(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            task_type=row["task_type"],
            status=row["status"],
            assigned_to=row["assigned_to"],
            parent_goal_id=row["parent_goal_id"],
            project_id=row["project_id"],
            required_gpu_memory=row["required_gpu_memory"] or 0.0,
            blockers=blockers,
            priority=row["priority"] or 3,
            checked_out_at=row["checked_out_at"],
            checkout_expires_at=row["checkout_expires_at"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _serialize_blockers(blockers: list[str]) -> str:
        import json

        return json.dumps(blockers, ensure_ascii=False)


class _CheckoutManagerHolder:
    """Thread-safe lazy holder for the :class:`TaskCheckoutManager` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: TaskCheckoutManager | None = None

    def get(self) -> TaskCheckoutManager:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = TaskCheckoutManager(
                    lock_store=get_execution_lock_store(),
                    db_path="task_checkout.db",
                )
            return self._instance

    def init(self, db_path: str = "task_checkout.db") -> TaskCheckoutManager:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = TaskCheckoutManager(
                lock_store=get_execution_lock_store(),
                db_path=db_path,
            )
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _CheckoutManagerHolder()


def get_checkout_manager() -> TaskCheckoutManager:
    """获取共享的 :class:`TaskCheckoutManager` 单例；首次访问时懒初始化。

    Returns:
        :class:`TaskCheckoutManager` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_checkout_manager)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_checkout_manager(db_path: str = "task_checkout.db") -> TaskCheckoutManager:
    """初始化任务签出管理器，行为与重构前完全一致。"""
    return _holder.init(db_path)
