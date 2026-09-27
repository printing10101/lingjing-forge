"""预算策略管理方法组。"""

from __future__ import annotations

import logging
import time
from typing import Any
from app.models.budget import (
    BudgetLevel,
    BudgetPeriod,
    BudgetPolicy,
    DEFAULT_GLOBAL_BUDGETS,
    ResourceType,
)


logger = logging.getLogger(__name__)


class _BudgetPolicyMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any
    _policies: Any

    def _load_policies(self) -> None:
        rows = self._conn.execute("SELECT * FROM budget_policies ORDER BY level, scope_id").fetchall()

        for row in rows:
            key = self._policy_key(row["level"], row["scope_id"], row["resource_type"])
            self._policies[key] = BudgetPolicy(
                level=BudgetLevel(row["level"]),
                scope_id=row["scope_id"],
                resource_type=ResourceType(row["resource_type"]),
                limit=row["limit_value"],
                period=BudgetPeriod(row["period"]),
                warning_threshold=row["warning_threshold"],
                hard_stop=bool(row["hard_stop"]),
                auto_notify=bool(row["auto_notify"]),
                enabled=bool(row["enabled"]),
                current_usage=row["current_usage"],
                last_reset_at=row["last_reset_at"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

        logger.info("Loaded %d budget policies", len(self._policies))

    def _load_default_policies(self) -> None:
        for policy in DEFAULT_GLOBAL_BUDGETS:
            key = self._policy_key(policy.level.value, policy.scope_id, policy.resource_type.value)
            if key not in self._policies:
                self.set_policy(policy)

    @staticmethod
    def _policy_key(level: str, scope_id: str, resource_type: str) -> str:
        return f"{level}:{scope_id}:{resource_type}"

    def set_policy(self, policy: BudgetPolicy) -> None:
        now = time.time()
        if policy.created_at is None:
            policy.created_at = now
        policy.updated_at = now

        key = self._policy_key(policy.level.value, policy.scope_id, policy.resource_type.value)

        self._conn.execute(
            """INSERT OR REPLACE INTO budget_policies
               (level, scope_id, resource_type, limit_value, period,
                warning_threshold, hard_stop, auto_notify, enabled,
                current_usage, last_reset_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                policy.level.value,
                policy.scope_id,
                policy.resource_type.value,
                policy.limit,
                policy.period.value,
                policy.warning_threshold,
                int(policy.hard_stop),
                int(policy.auto_notify),
                int(policy.enabled),
                policy.current_usage,
                policy.last_reset_at,
                policy.created_at,
                policy.updated_at,
            ),
        )
        self._conn.commit()
        self._policies[key] = policy
        logger.info(
            "Budget policy set: %s limit=%.2f period=%s",
            key,
            policy.limit,
            policy.period.value,
        )

    def get_policy(self, level: BudgetLevel, scope_id: str, resource_type: ResourceType) -> BudgetPolicy | None:
        key = self._policy_key(level.value, scope_id, resource_type.value)
        return self._policies.get(key)

    def get_all_policies(self, level: BudgetLevel | None = None, scope_id: str | None = None) -> list[BudgetPolicy]:
        result = []
        for policy in self._policies.values():
            if level and policy.level != level:
                continue
            if scope_id and policy.scope_id != scope_id:
                continue
            result.append(policy)
        return sorted(result, key=lambda p: (p.level.value, p.scope_id))
