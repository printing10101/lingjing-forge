"""预算核心检查/执行/重置方法组。"""

from __future__ import annotations

import logging
import time
from typing import Any
from collections.abc import Callable
from app.budget.models import EnforcementAction, EnforcementResult
from app.models.budget import (
    BudgetCheckResult,
    BudgetLevel,
    BudgetPeriod,
    BudgetPolicy,
    BudgetStatus,
    ResourceType,
)


logger = logging.getLogger(__name__)


class _BudgetCoreMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _agent_suspender: Any
    _cancel_pending_tasks: Callable[..., Any]
    _create_alert: Callable[..., Any]
    _log_enforcement: Callable[..., Any]
    _policy_key: Callable[..., Any]
    get_policy: Callable[..., Any]
    set_policy: Callable[..., Any]
    _conn: Any
    _cost_tracker_ref: Any
    _policies: Any
    _task_canceller: Any

    def adjust_budget(
        self,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
        new_limit: float,
        reason: str = "",
        adjusted_by: str = "admin",
    ) -> BudgetPolicy | None:
        key = self._policy_key(level.value, scope_id, resource_type.value)
        old_policy = self._policies.get(key)

        old_limit = old_policy.limit if old_policy else 0.0

        if old_policy:
            old_policy.limit = new_limit
            old_policy._adjustment_reason = reason
            self.set_policy(old_policy)
        else:
            new_policy = BudgetPolicy(
                level=level,
                scope_id=scope_id,
                resource_type=resource_type,
                limit=new_limit,
            )
            self.set_policy(new_policy)

        if hasattr(self, "_cost_tracker_ref") and self._cost_tracker_ref:
            self._cost_tracker_ref.record_budget_adjustment(
                budget_level=level.value,
                scope_id=scope_id,
                resource_type=resource_type.value,
                old_limit=old_limit,
                new_limit=new_limit,
                reason=reason,
                adjusted_by=adjusted_by,
            )

        logger.info(
            "Budget adjusted: %s/%s/%s %.2f → %.2f by %s",
            level.value,
            scope_id,
            resource_type.value,
            old_limit,
            new_limit,
            adjusted_by,
        )
        return self._policies.get(key)

    def check_budget(
        self,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
        planned_usage: float = 0.0,
    ) -> BudgetCheckResult:
        policy = self.get_policy(level, scope_id, resource_type)

        if policy is None:
            return BudgetCheckResult(
                passed=True,
                status=BudgetStatus.OK,
                usage_ratio=0.0,
                remaining=float("inf"),
                limit=0.0,
                checked_at=time.time(),
            )

        self._check_and_reset_period(policy)

        projected_usage = policy.current_usage + planned_usage
        projected_ratio = projected_usage / policy.limit if policy.limit > 0 else 0.0

        result = BudgetCheckResult(
            policy=policy,
            usage_ratio=projected_ratio,
            remaining=max(policy.limit - projected_usage, 0),
            limit=policy.limit,
            checked_at=time.time(),
        )

        if not policy.enabled:
            result.status = BudgetStatus.DISABLED
            result.passed = False
            result.block_reason = f"Budget policy disabled: {scope_id}/{resource_type.value}"
        elif projected_ratio >= 1.0:
            result.status = BudgetStatus.EXCEEDED
            if policy.hard_stop:
                result.passed = False
                result.block_reason = (
                    f"Budget EXCEEDED: {scope_id}/{resource_type.value} "
                    f"({projected_usage:.2f}/{policy.limit:.2f}, "
                    f"{projected_ratio:.1%})"
                )
            else:
                result.passed = True
                result.warnings.append(
                    f"Budget exceeded but hard_stop is disabled: {projected_usage:.2f}/{policy.limit:.2f}"
                )
        elif projected_ratio >= policy.warning_threshold:
            result.status = BudgetStatus.WARNING
            result.passed = True
            result.warnings.append(
                f"Budget WARNING: {scope_id}/{resource_type.value} "
                f"({projected_usage:.2f}/{policy.limit:.2f}, "
                f"{projected_ratio:.1%})"
            )
        else:
            result.status = BudgetStatus.OK
            result.passed = True

        return result

    def check_budget_cascade(
        self,
        agent_id: str,
        project_id: str = "default",
        resource_type: ResourceType = ResourceType.TOTAL_COST,
        planned_usage: float = 0.0,
    ) -> BudgetCheckResult:
        """级联预算检查：任务级 → 代理级 → 项目级 → 全局级"""
        cascade = [
            (BudgetLevel.TASK, agent_id),
            (BudgetLevel.AGENT, agent_id),
            (BudgetLevel.PROJECT, project_id),
            (BudgetLevel.GLOBAL, "default"),
        ]

        for level, scope_id in cascade:
            policy = self.get_policy(level, scope_id, resource_type)
            if policy is None:
                continue

            result = self.check_budget(level, scope_id, resource_type, planned_usage)
            if not result.passed:
                return result
            if result.warnings:
                return result

        return BudgetCheckResult(
            passed=True,
            status=BudgetStatus.OK,
            usage_ratio=0.0,
            remaining=float("inf"),
            limit=0.0,
            checked_at=time.time(),
        )

    def record_usage(
        self,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
        usage: float,
    ) -> None:
        """记录资源用量并更新策略"""
        key = self._policy_key(level.value, scope_id, resource_type.value)
        policy = self._policies.get(key)

        if policy is None:
            return

        policy.current_usage += usage

        self._conn.execute(
            """UPDATE budget_policies SET current_usage = ?, updated_at = ?
               WHERE level = ? AND scope_id = ? AND resource_type = ?""",
            (
                policy.current_usage,
                time.time(),
                level.value,
                scope_id,
                resource_type.value,
            ),
        )
        self._conn.commit()

    def enforce(
        self,
        level: BudgetLevel,
        scope_id: str,
        resource_type: ResourceType,
        planned_usage: float = 0.0,
    ) -> EnforcementResult:
        """执行预算强制执行"""
        result = EnforcementResult()
        check = self.check_budget(level, scope_id, resource_type, planned_usage)
        result.check_result = check

        if check.status == BudgetStatus.DISABLED:
            result.actions_taken.append(EnforcementAction.BLOCK)
            return result

        if check.passed:
            result.actions_taken.append(EnforcementAction.ALLOW)
            if check.status == BudgetStatus.WARNING:
                result.actions_taken.append(EnforcementAction.WARN)

                alert = self._create_alert(level, scope_id, resource_type, check, "warning")
                result.alerts_generated.append(alert)
            return result

        result.actions_taken.append(EnforcementAction.BLOCK)

        alert = self._create_alert(level, scope_id, resource_type, check, "exceeded")
        result.alerts_generated.append(alert)

        policy = check.policy
        if policy and policy.hard_stop:
            result.actions_taken.append(EnforcementAction.CANCEL_PENDING)

            if self._task_canceller and policy:
                cancelled = self._cancel_pending_tasks(level, scope_id, resource_type)
                result.cancelled_tasks = cancelled

            if level == BudgetLevel.AGENT:
                result.actions_taken.append(EnforcementAction.SUSPEND_AGENT)
                result.suspended_agents.append(scope_id)

                if self._agent_suspender is not None:
                    self._agent_suspender(
                        scope_id,
                        f"Budget exceeded: {resource_type.value} ({check.usage_ratio:.1%})",
                    )

        if policy and policy.auto_notify:
            result.actions_taken.append(EnforcementAction.NOTIFY_ADMIN)
            result.notifications_sent = True

        self._log_enforcement(result, level, scope_id, resource_type)

        return result

    def enforce_cascade(
        self,
        agent_id: str,
        project_id: str = "default",
        resource_type: ResourceType = ResourceType.TOTAL_COST,
        planned_usage: float = 0.0,
    ) -> EnforcementResult:
        """级联预算强制执行"""
        cascade = [
            (BudgetLevel.TASK, agent_id),
            (BudgetLevel.AGENT, agent_id),
            (BudgetLevel.PROJECT, project_id),
            (BudgetLevel.GLOBAL, "default"),
        ]

        for level, scope_id in cascade:
            policy = self.get_policy(level, scope_id, resource_type)
            if policy is None:
                continue

            result = self.enforce(level, scope_id, resource_type, planned_usage)
            if EnforcementAction.BLOCK in result.actions_taken:
                return result

        return EnforcementResult(
            actions_taken=[EnforcementAction.ALLOW],
            check_result=BudgetCheckResult(passed=True, status=BudgetStatus.OK, checked_at=time.time()),
        )

    def reset_period(self, level: BudgetLevel, scope_id: str, resource_type: ResourceType) -> None:
        """手动重置预算周期"""
        policy = self.get_policy(level, scope_id, resource_type)
        if policy is None:
            return

        old_usage = policy.current_usage

        self._conn.execute(
            """INSERT INTO budget_reset_log
               (level, scope_id, resource_type, period, usage_before_reset,
                limit_at_reset, reset_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                level.value,
                scope_id,
                resource_type.value,
                policy.period.value,
                old_usage,
                policy.limit,
                time.time(),
            ),
        )

        policy.current_usage = 0.0
        policy.last_reset_at = time.time()

        self._conn.execute(
            """UPDATE budget_policies
               SET current_usage = 0.0, last_reset_at = ?, updated_at = ?
               WHERE level = ? AND scope_id = ? AND resource_type = ?""",
            (
                policy.last_reset_at,
                time.time(),
                level.value,
                scope_id,
                resource_type.value,
            ),
        )
        self._conn.commit()

        logger.info(
            "Budget reset: %s/%s/%s, previous usage: %.2f",
            level.value,
            scope_id,
            resource_type.value,
            old_usage,
        )

    def auto_reset_periods(self) -> int:
        """自动检查并重置所有到期周期，返回重置数量"""
        now = time.time()
        reset_count = 0

        for key, policy in list(self._policies.items()):
            if self._should_reset(policy, now):
                old_usage = policy.current_usage
                policy.current_usage = 0.0
                policy.last_reset_at = now

                self._conn.execute(
                    """UPDATE budget_policies
                       SET current_usage = 0.0, last_reset_at = ?, updated_at = ?
                       WHERE level = ? AND scope_id = ? AND resource_type = ?""",
                    (
                        now,
                        now,
                        policy.level.value,
                        policy.scope_id,
                        policy.resource_type.value,
                    ),
                )

                self._conn.execute(
                    """INSERT INTO budget_reset_log
                       (level, scope_id, resource_type, period,
                        usage_before_reset, limit_at_reset, reset_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        policy.level.value,
                        policy.scope_id,
                        policy.resource_type.value,
                        policy.period.value,
                        old_usage,
                        policy.limit,
                        now,
                    ),
                )
                reset_count += 1

        if reset_count > 0:
            self._conn.commit()
            logger.info("Auto-reset %d budget periods", reset_count)

        return reset_count

    def _should_reset(self, policy: BudgetPolicy, now: float) -> bool:
        if policy.last_reset_at is None:
            return False

        import datetime

        last_dt = datetime.datetime.fromtimestamp(policy.last_reset_at)
        now_dt = datetime.datetime.fromtimestamp(now)

        if policy.period == BudgetPeriod.DAILY:
            return last_dt.date() < now_dt.date()
        elif policy.period == BudgetPeriod.WEEKLY:
            last_monday = last_dt - datetime.timedelta(days=last_dt.weekday())
            now_monday = now_dt - datetime.timedelta(days=now_dt.weekday())
            return last_monday.date() < now_monday.date()
        elif policy.period == BudgetPeriod.MONTHLY:
            return (last_dt.year, last_dt.month) < (now_dt.year, now_dt.month)

        return False

    def _check_and_reset_period(self, policy: BudgetPolicy) -> None:
        now = time.time()
        if policy.last_reset_at is None:
            policy.last_reset_at = now
            self._conn.execute(
                """UPDATE budget_policies SET last_reset_at = ?
                   WHERE level = ? AND scope_id = ? AND resource_type = ?""",
                (now, policy.level.value, policy.scope_id, policy.resource_type.value),
            )
            self._conn.commit()
            return

        if self._should_reset(policy, now):
            old_usage = policy.current_usage
            policy.current_usage = 0.0
            policy.last_reset_at = now

            self._conn.execute(
                """UPDATE budget_policies
                   SET current_usage = 0.0, last_reset_at = ?, updated_at = ?
                   WHERE level = ? AND scope_id = ? AND resource_type = ?""",
                (
                    now,
                    now,
                    policy.level.value,
                    policy.scope_id,
                    policy.resource_type.value,
                ),
            )
            self._conn.execute(
                """INSERT INTO budget_reset_log
                   (level, scope_id, resource_type, period,
                    usage_before_reset, limit_at_reset, reset_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    policy.level.value,
                    policy.scope_id,
                    policy.resource_type.value,
                    policy.period.value,
                    old_usage,
                    policy.limit,
                    now,
                ),
            )
            self._conn.commit()
            logger.info(
                "Period reset triggered: %s/%s/%s, previous: %.2f",
                policy.level.value,
                policy.scope_id,
                policy.resource_type.value,
                old_usage,
            )
