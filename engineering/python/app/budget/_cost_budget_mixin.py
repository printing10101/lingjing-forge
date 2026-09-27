"""_CostBudgetMixin (split from MultiDimensionCostTracker)."""

from __future__ import annotations

import logging
import time
from typing import Any
from app.utils.sqlite_retry import sqlite_retry

from app.budget._cost_models import (  # noqa: F401
    CostDimension,
    CostType,
    ProviderType,
    ModelType,
    CostUnitPrice,
    CostEvent,
    CostSummary,
    BudgetEvent,
)

logger = logging.getLogger(__name__)


class _CostBudgetMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any

    @sqlite_retry()
    def record_budget_event(self, event: BudgetEvent) -> None:
        """记录预算事件（超限/警告）"""
        if event.recorded_at is None:
            event.recorded_at = time.time()

        self._conn.execute(
            """INSERT INTO budget_events
               (budget_level, scope_id, resource_type, current_usage,
                limit_value, usage_ratio, status, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.budget_level,
                event.scope_id,
                event.resource_type,
                event.current_usage,
                event.limit_value,
                event.usage_ratio,
                event.status,
                event.recorded_at,
            ),
        )
        self._conn.commit()

    def get_budget_events(
        self,
        budget_level: str | None = None,
        scope_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """获取预算事件列表"""
        conditions = []
        params = []

        if budget_level:
            conditions.append("budget_level = ?")
            params.append(budget_level)
        if scope_id:
            conditions.append("scope_id = ?")
            params.append(scope_id)
        if status:
            conditions.append("status = ?")
            params.append(status)

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = self._conn.execute(
            f"""SELECT * FROM budget_events
                WHERE {where}
                ORDER BY recorded_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(row) for row in rows]

    @sqlite_retry()
    def record_budget_adjustment(
        self,
        budget_level: str,
        scope_id: str,
        resource_type: str,
        old_limit: float,
        new_limit: float,
        reason: str = "",
        adjusted_by: str = "admin",
    ) -> None:
        """记录预算调整历史"""
        self._conn.execute(
            """INSERT INTO budget_adjustments
               (budget_level, scope_id, resource_type, old_limit, new_limit,
                reason, adjusted_by, adjusted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                budget_level,
                scope_id,
                resource_type,
                old_limit,
                new_limit,
                reason,
                adjusted_by,
                time.time(),
            ),
        )
        self._conn.commit()
        logger.info(
            "Budget adjusted: %s/%s/%s %.2f -> %.2f",
            budget_level,
            scope_id,
            resource_type,
            old_limit,
            new_limit,
        )

    def get_budget_adjustments(self, limit: int = 50) -> list[dict[str, Any]]:
        """获取预算调整历史"""
        rows = self._conn.execute(
            "SELECT * FROM budget_adjustments ORDER BY adjusted_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
