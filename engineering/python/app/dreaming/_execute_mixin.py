"""回滚执行与历史查询 mixin（从 rollback_manager 拆出）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Callable

from app.dreaming.apply_rules import RollbackResult
from app.dreaming.progressive_publisher import PublicationStage
from app.dreaming._rollback_models import RollbackExecutionResult

logger = logging.getLogger(__name__)


class _ExecuteMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_applicator: Callable[..., Any]
    _get_audit_recorder: Callable[..., Any]
    _get_publisher: Callable[..., Any]
    _save_cooldowns: Callable[..., Any]
    _save_history: Callable[..., Any]
    _consecutive_anomalies: Any
    _cooldowns: Any
    _lock: Any
    _rollback_history: Any
    cooldown_hours: Any

    def rollback_rule(
        self,
        rule_id: str,
        reason: str,
        severity: str = "manual",
        fully_deprecate: bool = False,
    ) -> RollbackExecutionResult:
        """执行规则回滚。

        回滚策略：
            - severity=hard_constraint 或 fully_deprecate=True：
              直接调用 RuleApplicator.rollback 并标记 DEPRECATED
            - 其他 severity：通过 ProgressivePublisher.demote 降级
              若已在 SHADOW 阶段，则触发 DEPRECATED

        Args:
            rule_id: 规则 ID。
            reason: 回滚原因。
            severity: 严重级别。
            fully_deprecate: 是否强制完全废弃。

        Returns:
            RollbackExecutionResult 实例。
        """
        operated_at = datetime.now(timezone.utc).isoformat()
        publisher = self._get_publisher()
        applicator = self._get_applicator()

        # 获取当前灰度阶段
        record = publisher.get_record(rule_id)
        previous_stage = record.current_stage.value if record else "unknown"

        rollback_result: RollbackResult | None = None
        current_stage = previous_stage
        fully_deprecated = False

        # 硬约束违反或强制废弃：直接 RuleApplicator.rollback
        if severity == "hard_constraint" or fully_deprecate:
            rollback_result = applicator.rollback(rule_id)
            current_stage = PublicationStage.DEPRECATED.value
            fully_deprecated = True
            if not rollback_result.success:
                logger.error(
                    "规则 %s 硬约束回滚失败：%s",
                    rule_id,
                    rollback_result.error,
                )
        else:
            # 其他 severity：通过 ProgressivePublisher.demote 降级
            demote_result = publisher.demote(
                rule_id=rule_id,
                reason=reason,
                auto=True,
            )
            if demote_result.success:
                current_stage = demote_result.stage.value
                fully_deprecated = demote_result.stage == PublicationStage.DEPRECATED
            else:
                # demote 失败：尝试直接 rollback
                logger.warning(
                    "规则 %s demote 失败，尝试直接 rollback：%s",
                    rule_id,
                    demote_result.error,
                )
                rollback_result = applicator.rollback(rule_id)
                current_stage = PublicationStage.DEPRECATED.value
                fully_deprecated = True

        # 设置冷却期
        cooldown_until = (datetime.now(timezone.utc) + timedelta(hours=self.cooldown_hours)).isoformat()
        with self._lock:
            self._cooldowns[rule_id] = cooldown_until
            # 重置连续异常计数
            self._consecutive_anomalies[rule_id] = 0
            # 记录历史
            self._rollback_history.append(
                {
                    "rule_id": rule_id,
                    "reason": reason,
                    "severity": severity,
                    "previous_stage": previous_stage,
                    "current_stage": current_stage,
                    "fully_deprecated": fully_deprecated,
                    "operated_at": operated_at,
                    "cooldown_until": cooldown_until,
                }
            )
            self._save_history()
            self._save_cooldowns()

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=(f"规则回滚：{reason}（severity={severity}）"),
                validation_passed=True,
                applied=False,
                rollback_triggered=True,
            )
        except Exception as e:
            logger.error("回滚审计写入失败（不影响回滚）：%s", e)

        logger.info(
            "规则 %s 回滚完成：%s → %s（reason=%s）",
            rule_id,
            previous_stage,
            current_stage,
            reason,
        )

        return RollbackExecutionResult(
            success=True,
            rule_id=rule_id,
            previous_stage=previous_stage,
            current_stage=current_stage,
            fully_deprecated=fully_deprecated,
            rollback_result=rollback_result,
            operated_at=operated_at,
            reason=reason,
        )

    def get_rollback_history(
        self,
        rule_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """查询回滚历史。

        Args:
            rule_id: 过滤指定规则。None 表示全部。
            limit: 返回条数上限。

        Returns:
            回滚历史列表（按时间倒序）。
        """
        with self._lock:
            history = list(self._rollback_history)
        if rule_id is not None:
            history = [h for h in history if h.get("rule_id") == rule_id]
        history.sort(key=lambda h: h.get("operated_at", ""), reverse=True)
        return history[:limit]

    def get_consecutive_anomaly_count(self, rule_id: str) -> int:
        """获取规则当前连续异常计数。"""
        with self._lock:
            return self._consecutive_anomalies.get(rule_id, 0)

    def reset_anomaly_count(self, rule_id: str) -> None:
        """重置规则连续异常计数（用于人工确认指标恢复后）。"""
        with self._lock:
            self._consecutive_anomalies[rule_id] = 0
