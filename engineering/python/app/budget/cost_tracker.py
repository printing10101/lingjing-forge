"""
Multi-dimensional Cost Tracking System

Tracks costs across agent, project, goal, task, provider, and model dimensions.
Supports precise measurement of GPU time, GPU memory, API calls, and data transfer.
"""

import logging
import threading

from pathlib import Path

from app.utils.utils import get_output_dir
from app.utils.sqlite_pool import get_sqlite_manager
from app.budget._cost_price_mixin import _CostPriceMixin
from app.budget._cost_record_mixin import _CostRecordMixin
from app.budget._cost_query_mixin import _CostQueryMixin
from app.budget._cost_budget_mixin import _CostBudgetMixin
from app.budget._cost_models import (
    BudgetEvent,
    CostDimension,
    CostEvent,
    CostSummary,
    CostType,
    CostUnitPrice,
    ModelType,
    ProviderType,
)

# 数据类/类型经本模块再导出（cost_optimizer / api / tests 依赖），__all__ 防 ruff F401 误删
__all__ = [
    "MultiDimensionCostTracker",
    "CostDimension",
    "CostType",
    "ProviderType",
    "ModelType",
    "CostUnitPrice",
    "CostEvent",
    "CostSummary",
    "BudgetEvent",
]


logger = logging.getLogger(__name__)


class MultiDimensionCostTracker(_CostPriceMixin, _CostRecordMixin, _CostQueryMixin, _CostBudgetMixin):
    """多维度成本追踪器"""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(get_output_dir("data") / "cost_tracking.db")

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("cost_tracking", db_path=self.db_path)
        self._conn = self._pool.get_connection()
        self._unit_prices = CostUnitPrice()
        self._closed = False  # P3 幂等性标志位
        self._init_schema()

        logger.info("MultiDimensionCostTracker initialized at %s", db_path)

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS cost_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT 'default',
                goal_id TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT 'system_internal',
                model TEXT NOT NULL DEFAULT '',
                cost_type TEXT NOT NULL,
                resource_value REAL NOT NULL DEFAULT 0.0,
                cost_value REAL NOT NULL DEFAULT 0.0,
                start_time REAL,
                end_time REAL,
                metadata TEXT DEFAULT '{}',
                recorded_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_cost_task ON cost_events(task_id);
            CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_events(agent_id);
            CREATE INDEX IF NOT EXISTS idx_cost_project ON cost_events(project_id);
            CREATE INDEX IF NOT EXISTS idx_cost_goal ON cost_events(goal_id);
            CREATE INDEX IF NOT EXISTS idx_cost_provider ON cost_events(provider);
            CREATE INDEX IF NOT EXISTS idx_cost_model ON cost_events(model);
            CREATE INDEX IF NOT EXISTS idx_cost_type ON cost_events(cost_type);
            CREATE INDEX IF NOT EXISTS idx_cost_recorded ON cost_events(recorded_at);

            CREATE TABLE IF NOT EXISTS budget_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                budget_level TEXT NOT NULL DEFAULT 'global',
                scope_id TEXT NOT NULL DEFAULT 'default',
                resource_type TEXT NOT NULL,
                current_usage REAL NOT NULL DEFAULT 0.0,
                limit_value REAL NOT NULL DEFAULT 0.0,
                usage_ratio REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'ok',
                recorded_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_budget_level ON budget_events(budget_level);
            CREATE INDEX IF NOT EXISTS idx_budget_scope ON budget_events(scope_id);
            CREATE INDEX IF NOT EXISTS idx_budget_recorded ON budget_events(recorded_at);

            CREATE TABLE IF NOT EXISTS unit_price_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price_key TEXT NOT NULL UNIQUE,
                price_value REAL NOT NULL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS budget_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                budget_level TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                old_limit REAL NOT NULL,
                new_limit REAL NOT NULL,
                reason TEXT,
                adjusted_by TEXT DEFAULT 'admin',
                adjusted_at REAL
            );
        """)
        self._conn.commit()

        self._load_unit_prices()

    def close(self) -> None:
        """关闭追踪器，归还连接到连接池

        幂等性：
            多次调用安全；已关闭后再次调用为 no-op。

        Note:
            H3 bug 修复：原代码直接关闭连接，未归还到连接池，导致连接泄漏。
            改为：如果存在 _pool 则归还，否则才直接 close。
        """
        if self._closed:
            return
        if hasattr(self, "_pool") and self._pool is not None and hasattr(self, "_conn") and self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None
        elif hasattr(self, "_conn") and self._conn:
            self._conn.close()
            self._conn = None
        logger.info("MultiDimensionCostTracker closed")
        self._closed = True


class _CostTrackerHolder:
    """Thread-safe lazy holder for the :class:`MultiDimensionCostTracker` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: MultiDimensionCostTracker | None = None

    def get(self) -> MultiDimensionCostTracker:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = MultiDimensionCostTracker()
            return self._instance

    def init(self, db_path: str | None = None) -> MultiDimensionCostTracker:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = MultiDimensionCostTracker(db_path)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _CostTrackerHolder()


def get_cost_tracker() -> MultiDimensionCostTracker:
    """获取共享的 :class:`MultiDimensionCostTracker` 单例；首次访问时懒初始化。

    .. deprecated:: V3.0 (2026-08-02)
    """
    return _holder.get()


def init_cost_tracker(db_path: str | None = None) -> MultiDimensionCostTracker:
    """初始化成本追踪器，行为与重构前完全一致。"""
    return _holder.init(db_path)
