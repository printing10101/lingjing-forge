"""告警生成与查询方法组。"""

from __future__ import annotations

import logging
import time
from typing import Any
from collections.abc import Callable
from app.models.budget import (
    BudgetAlert,
    BudgetCheckResult,
    BudgetLevel,
    BudgetStatus,
    ResourceType,
)


logger = logging.getLogger(__name__)


class _BudgetAlertMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _alert_callbacks: Any
    _conn: Any

    def _create_alert(
        self,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
        check: BudgetCheckResult,
        alert_type: str,
    ) -> BudgetAlert:
        now = time.time()
        message = (
            f"[{alert_type.upper()}] {level.value}:{scope_id} "
            f"{resource_type.value}: {check.usage_ratio:.1%} used "
            f"(limit: {check.limit:.2f})"
        )

        alert = BudgetAlert(
            level=level,
            scope_id=scope_id,
            resource_type=resource_type,
            status=BudgetStatus.WARNING if alert_type == "warning" else BudgetStatus.EXCEEDED,
            current_usage=check.limit * check.usage_ratio,
            limit=check.limit,
            usage_ratio=check.usage_ratio,
            message=message,
            created_at=now,
        )

        self._conn.execute(
            """INSERT INTO budget_alerts
               (level, scope_id, resource_type, status, current_usage,
                limit_value, usage_ratio, message, is_read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                level.value,
                scope_id,
                resource_type.value,
                alert.status.value,
                alert.current_usage,
                alert.limit,
                alert.usage_ratio,
                alert.message,
                now,
            ),
        )
        self._conn.commit()

        for callback in self._alert_callbacks:
            try:
                callback(alert)
            except (RuntimeError, ValueError, TypeError, AttributeError):
                logger.error("Alert callback error", exc_info=True)

        return alert

    def get_alerts(
        self,
        status: str | None = None,
        unread_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions = []
        params = []

        if status:
            conditions.append("status = ?")
            params.append(status)
        if unread_only:
            conditions.append("is_read = 0")

        where = " AND ".join(conditions) if conditions else "1=1"

        rows = self._conn.execute(
            f"""SELECT * FROM budget_alerts
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()

        return [dict(row) for row in rows]

    def mark_alert_read(self, alert_id: int) -> None:
        self._conn.execute("UPDATE budget_alerts SET is_read = 1 WHERE id = ?", (alert_id,))
        self._conn.commit()

    def mark_all_alerts_read(self) -> None:
        self._conn.execute("UPDATE budget_alerts SET is_read = 1 WHERE is_read = 0")
        self._conn.commit()

    def delete_alert(self, alert_id: int) -> None:
        self._conn.execute("DELETE FROM budget_alerts WHERE id = ?", (alert_id,))
        self._conn.commit()

    def register_alert_callback(self, callback: Callable[[BudgetAlert], None]) -> None:
        self._alert_callbacks.append(callback)
