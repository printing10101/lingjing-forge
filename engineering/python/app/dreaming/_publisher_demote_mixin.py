"""_PublisherDemoteMixin (split from ProgressivePublisher)."""

from __future__ import annotations

from __future__ import annotations
from app.dreaming._publisher_models import (  # noqa: F401
    PublicationStage,
    PublicationRecord,
    PublicationResult,
)

import logging
from datetime import datetime
from typing import Any
from collections.abc import Callable
from datetime import timezone


logger = logging.getLogger(__name__)


class _PublisherDemoteMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_applicator: Callable[..., Any]
    _get_audit_recorder: Callable[..., Any]
    _save_record: Callable[..., Any]
    _demotion_thresholds: Any
    _lock: Any
    _records: Any

    def demote(
        self,
        rule_id: str,
        reason: str,
        target_stage: PublicationStage | None = None,
        auto: bool = False,
    ) -> PublicationResult:
        """将规则降级到上一阶段（或指定阶段）。

        降级方向：FULL → ROLLING_50 → ROLLING_10 → CANARY → SHADOW
        若 SHADOW 阶段再次降级，则触发回滚（状态改为 DEPRECATED）。

        Args:
            rule_id: 规则 ID。
            reason: 降级原因（写入审计日志）。
            target_stage: 目标阶段。None 表示降级到上一阶段。
            auto: 是否为自动降级（指标恶化触发）。

        Returns:
            PublicationResult 实例。
        """
        operated_at = datetime.now(timezone.utc).isoformat()

        with self._lock:
            record = self._records.get(rule_id)
            if record is None:
                return PublicationResult(
                    success=False,
                    rule_id=rule_id,
                    stage=PublicationStage.SHADOW,
                    operated_at=operated_at,
                    error=f"规则 {rule_id} 未发布，无法降级",
                )

            current_stage = record.current_stage

            # SHADOW 阶段降级 = 回滚
            if current_stage == PublicationStage.SHADOW:
                if target_stage is None or target_stage == PublicationStage.DEPRECATED:
                    return self._rollback_to_deprecated(
                        rule_id=rule_id,
                        reason=reason,
                        operated_at=operated_at,
                        record=record,
                    )
                return PublicationResult(
                    success=False,
                    rule_id=rule_id,
                    stage=current_stage,
                    operated_at=operated_at,
                    error="SHADOW 已是最低发布阶段，无法继续降级",
                )

            if target_stage is None:
                prev_stage = current_stage.previous_stage
            else:
                prev_stage = target_stage

            # FULL 阶段降级：调用 RuleApplicator.rollback 真正回滚知识图谱
            if current_stage == PublicationStage.FULL:
                rollback_result = self._get_applicator().rollback(rule_id)
                if not rollback_result.success:
                    logger.warning(
                        "FULL 阶段知识图谱回滚失败：%s",
                        rollback_result.error,
                    )

            record.current_stage = prev_stage
            record.entered_at = operated_at
            record.promoted_to_full = False
            record.demoted_count += 1
            record.stage_history.append(
                {
                    "action": "auto_demote" if auto else "demote",
                    "from": current_stage.value,
                    "to": prev_stage.value,
                    "operated_at": operated_at,
                    "reason": reason,
                }
            )
            self._save_record(record)

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=f"规则降级：{reason}",
                validation_passed=True,
                applied=False,
                rollback_triggered=(current_stage == PublicationStage.FULL),
            )
        except Exception as e:
            logger.error("降级审计写入失败（不影响降级）：%s", e)

        logger.info(
            "规则 %s 从 %s 降级到 %s（原因：%s）",
            rule_id,
            current_stage.value,
            prev_stage.value,
            reason,
        )

        return PublicationResult(
            success=True,
            rule_id=rule_id,
            stage=prev_stage,
            traffic_percentage=prev_stage.traffic_percentage,
            operated_at=operated_at,
        )

    def _check_demotion_thresholds(self, metrics: dict[str, Any]) -> str | None:
        """检查指标是否触发降级。

        Returns:
            触发降级的原因；None 表示无需降级。
        """
        accuracy = float(metrics.get("accuracy", 1.0))
        fpr = float(metrics.get("false_positive_rate", 0.0))
        err_rate = float(metrics.get("error_rate", 0.0))

        max_fpr = self._demotion_thresholds["max_false_positive_rate"]
        max_err = self._demotion_thresholds["max_error_rate"]
        min_acc = self._demotion_thresholds["min_accuracy"]

        if fpr > max_fpr:
            return f"误报率 {fpr:.3f} 超过降级阈值 {max_fpr}"
        if err_rate > max_err:
            return f"错误率 {err_rate:.3f} 超过降级阈值 {max_err}"
        if accuracy < min_acc:
            return f"准确率 {accuracy:.3f} 低于降级阈值 {min_acc}"
        return None

    def _rollback_to_deprecated(
        self,
        rule_id: str,
        reason: str,
        operated_at: str,
        record: PublicationRecord,
    ) -> PublicationResult:
        """SHADOW 阶段再次降级时，将规则标记为 DEPRECATED（等价回滚）。"""
        record.current_stage = PublicationStage.DEPRECATED
        record.entered_at = operated_at
        record.auto_rollback_triggered = True
        record.stage_history.append(
            {
                "action": "auto_rollback",
                "from": PublicationStage.SHADOW.value,
                "to": PublicationStage.DEPRECATED.value,
                "operated_at": operated_at,
                "reason": reason,
            }
        )
        self._save_record(record)

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=f"规则自动回滚：{reason}",
                validation_passed=True,
                applied=False,
                rollback_triggered=True,
            )
        except Exception as e:
            logger.error("自动回滚审计写入失败：%s", e)

        logger.warning(
            "规则 %s 触发自动回滚（SHADOW → DEPRECATED），原因：%s",
            rule_id,
            reason,
        )

        return PublicationResult(
            success=True,
            rule_id=rule_id,
            stage=PublicationStage.DEPRECATED,
            traffic_percentage=0.0,
            operated_at=operated_at,
        )
