"""预算配置 mixin（从 budget 拆出）。"""

from __future__ import annotations

import logging
from typing import Any

import sqlite3

from app.budget._budget_models import BudgetLimit
from app.models.budget import BudgetLevel, ResourceType

logger = logging.getLogger(__name__)


class _BudgetConfigMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any
    _lock: Any

    def _load_default_budgets(self) -> None:
        """加载默认预算配置"""
        defaults = [
            BudgetLimit(
                resource_type=ResourceType.INFERENCE_COUNT,
                limit_value=10000,
                budget_level=BudgetLevel.GLOBAL,
            ),
            BudgetLimit(
                resource_type=ResourceType.GPU_HOURS,
                limit_value=24.0,
                budget_level=BudgetLevel.GLOBAL,
            ),
            BudgetLimit(
                resource_type=ResourceType.MEMORY_PEAK,
                limit_value=16384,
                budget_level=BudgetLevel.GLOBAL,
            ),
            BudgetLimit(
                resource_type=ResourceType.API_CALLS,
                limit_value=50000,
                budget_level=BudgetLevel.GLOBAL,
            ),
        ]

        with self._lock:
            for budget in defaults:
                try:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO budget_limits
                           (resource_type, limit_value, warning_threshold, hard_stop_threshold,
                            budget_level, scope_id, reset_interval)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            budget.resource_type.value,
                            budget.limit_value,
                            budget.warning_threshold,
                            budget.hard_stop_threshold,
                            budget.budget_level.value,
                            budget.scope_id,
                            budget.reset_interval,
                        ),
                    )
                except (sqlite3.IntegrityError, sqlite3.OperationalError) as e:
                    # 重复的预算条目插入（已存在）属于幂等性场景，记录后跳过
                    logger.debug("Budget already exists, skipping insert: %s", e)

            self._conn.commit()

    def set_budget_limit(self, budget: BudgetLimit) -> None:
        """设置预算限制"""
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO budget_limits
                   (resource_type, limit_value, warning_threshold, hard_stop_threshold,
                    budget_level, scope_id, reset_interval)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    budget.resource_type.value,
                    budget.limit_value,
                    budget.warning_threshold,
                    budget.hard_stop_threshold,
                    budget.budget_level.value,
                    budget.scope_id,
                    budget.reset_interval,
                ),
            )
            self._conn.commit()

        logger.info(
            "Budget limit set: %s for %s (level=%s, limit=%.2f)",
            budget.resource_type.value,
            budget.scope_id,
            budget.budget_level.value,
            budget.limit_value,
        )
