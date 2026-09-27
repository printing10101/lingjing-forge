"""决策动作方法组：晋级/降级/回滚/阶段推进。"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable

from app.dreaming._closed_loop_models import (
    ClosedLoopDecision,
)

logger = logging.getLogger(__name__)


class _ClosedLoopActionMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_publisher: Callable[..., Any]
    _get_rollback_manager: Callable[..., Any]
    _record_decision_to_audit: Callable[..., Any]

    def apply_decision(self, decision: ClosedLoopDecision) -> bool:
        """应用闭环决策。

        Args:
            decision: 决策结果。

        Returns:
            是否应用成功。
        """
        if decision.action == "keep":
            decision.applied = True
            return True

        if decision.action == "rollback":
            return self._apply_rollback(decision)

        if decision.action == "promote":
            return self._apply_promote(decision)

        if decision.action == "demote":
            return self._apply_demote(decision)

        logger.warning("ClosedLoop: 未知 action=%s", decision.action)
        return False

    def _apply_promote(self, decision: ClosedLoopDecision) -> bool:
        """执行晋级决策。"""
        publisher = self._get_publisher()
        if publisher is None:
            decision.apply_error = "ProgressivePublisher 不可用"
            return False
        try:
            from app.dreaming.progressive_publisher import PublicationStage

            target_stage = None
            if decision.target_stage:
                try:
                    target_stage = PublicationStage(decision.target_stage)
                except ValueError:
                    logger.warning(
                        "ClosedLoop: 未知 target_stage=%s，使用默认晋级",
                        decision.target_stage,
                    )
            result = publisher.promote(
                rule_id=decision.rule_id,
                target_stage=target_stage,
                metrics_snapshot={
                    "fused_confidence": decision.fused_confidence,
                    "conflict": decision.conflict,
                    "ds_mass": decision.ds_mass,
                    "sample_count": decision.sample_count,
                    "source": "closed_loop",
                },
            )
            decision.applied = bool(result.success)
            if not result.success:
                decision.apply_error = result.error
            self._record_decision_to_audit(decision)
            return decision.applied
        except Exception as e:
            decision.apply_error = f"{type(e).__name__}: {e}"
            logger.error(
                "ClosedLoop: 晋级失败 rule=%s：%s",
                decision.rule_id,
                e,
                exc_info=True,
            )
            return False

    def _apply_demote(self, decision: ClosedLoopDecision) -> bool:
        """执行降级决策。"""
        publisher = self._get_publisher()
        if publisher is None:
            decision.apply_error = "ProgressivePublisher 不可用"
            return False
        try:
            from app.dreaming.progressive_publisher import PublicationStage

            target_stage = None
            if decision.target_stage:
                try:
                    target_stage = PublicationStage(decision.target_stage)
                except ValueError:
                    logger.warning(
                        "ClosedLoop: 未知 target_stage=%s，使用默认降级",
                        decision.target_stage,
                    )
            result = publisher.demote(
                rule_id=decision.rule_id,
                reason=decision.reason,
                target_stage=target_stage,
                auto=False,
            )
            decision.applied = bool(result.success)
            if not result.success:
                decision.apply_error = result.error
            self._record_decision_to_audit(decision)
            return decision.applied
        except Exception as e:
            decision.apply_error = f"{type(e).__name__}: {e}"
            logger.error(
                "ClosedLoop: 降级失败 rule=%s：%s",
                decision.rule_id,
                e,
                exc_info=True,
            )
            return False

    def _apply_rollback(self, decision: ClosedLoopDecision) -> bool:
        """执行回滚决策（硬约束违反）。"""
        rollback_mgr = self._get_rollback_manager()
        if rollback_mgr is None:
            decision.apply_error = "RollbackManager 不可用"
            return False
        try:
            result = rollback_mgr.rollback_rule(
                rule_id=decision.rule_id,
                reason=decision.reason,
                severity="hard_constraint",
                fully_deprecate=True,
            )
            decision.applied = bool(result.success)
            if not result.success:
                decision.apply_error = result.error
            self._record_decision_to_audit(decision)
            return decision.applied
        except Exception as e:
            decision.apply_error = f"{type(e).__name__}: {e}"
            logger.error(
                "ClosedLoop: 回滚失败 rule=%s：%s",
                decision.rule_id,
                e,
                exc_info=True,
            )
            return False

    def _get_next_stage(self, rule_id: str) -> str | None:
        """获取规则的下一晋级阶段。"""
        publisher = self._get_publisher()
        if publisher is None:
            return None
        try:
            record = publisher.get_record(rule_id)
            if record is None:
                # 规则尚未发布，下一阶段为 shadow
                return "shadow"
            # record.current_stage 已是 PublicationStage 枚举
            current_stage = record.current_stage
            nxt = current_stage.next_stage
            return nxt.value if nxt else None
        except Exception as e:
            logger.debug("ClosedLoop: get_next_stage 失败：%s", e)
            return None

    def _get_previous_stage(self, rule_id: str) -> str | None:
        """获取规则的降级目标阶段。"""
        publisher = self._get_publisher()
        if publisher is None:
            return None
        try:
            record = publisher.get_record(rule_id)
            if record is None:
                return None
            current_stage = record.current_stage
            prev = current_stage.previous_stage
            return prev.value if prev else None
        except Exception as e:
            logger.debug("ClosedLoop: get_previous_stage 失败：%s", e)
            return None
