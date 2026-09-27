"""_PublisherPublishMixin (split from ProgressivePublisher)."""

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
from app.dreaming.apply_rules import (
    ApplyResult,
)
from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming.rule_validator import ValidationResult
from datetime import timezone


logger = logging.getLogger(__name__)


class _PublisherPublishMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _check_demotion_thresholds: Callable[..., Any]
    _get_applicator: Callable[..., Any]
    _get_audit_recorder: Callable[..., Any]
    _get_or_create_record: Callable[..., Any]
    _save_record: Callable[..., Any]
    _lock: Any
    _records: Any
    _validator: Any

    def publish(
        self,
        rule: RuleDraft,
        stage: PublicationStage = PublicationStage.SHADOW,
        skip_validation: bool = False,
    ) -> PublicationResult:
        """将规则发布到指定灰度阶段。

        Args:
            rule: 待发布的规则草稿。
            stage: 目标灰度阶段。默认 SHADOW。
            skip_validation: 是否跳过沙箱校验（仅测试用，生产禁用）。

        Returns:
            PublicationResult 实例。
        """
        operated_at = datetime.now(timezone.utc).isoformat()
        validation_result: ValidationResult | None = None

        # 阶段 1：沙箱校验（FULL 阶段必须通过，其他阶段也建议通过）
        if not skip_validation:
            validation_result = self._validator.validate(rule)
            if not validation_result.passed and stage == PublicationStage.FULL:
                error_msg = f"FULL 阶段发布要求沙箱验证通过：{validation_result.errors}"
                logger.warning(error_msg)
                return PublicationResult(
                    success=False,
                    rule_id=rule.rule_id,
                    stage=stage,
                    operated_at=operated_at,
                    validation_result=validation_result,
                    error=error_msg,
                )

        # 阶段 2：更新灰度记录
        with self._lock:
            record = self._get_or_create_record(rule.rule_id)
            record.current_stage = stage
            record.entered_at = operated_at
            record.stage_history.append(
                {
                    "action": "publish",
                    "stage": stage.value,
                    "operated_at": operated_at,
                    "traffic_percentage": stage.traffic_percentage,
                }
            )
            if stage == PublicationStage.FULL:
                record.promoted_to_full = True
            self._save_record(record)

        # 阶段 3：FULL 阶段才真正应用到知识图谱
        audit_seq: int | None = None
        apply_result: ApplyResult | None = None
        if stage == PublicationStage.FULL:
            apply_result = self._get_applicator().apply(rule, skip_validation=skip_validation)
            if not apply_result.success:
                # 应用失败：降级回 SHADOW
                with self._lock:
                    record = self._records.get(rule.rule_id)
                    if record is not None:
                        record.current_stage = PublicationStage.SHADOW
                        record.promoted_to_full = False
                        record.demoted_count += 1
                        record.stage_history.append(
                            {
                                "action": "auto_demote_on_apply_failure",
                                "stage": PublicationStage.SHADOW.value,
                                "operated_at": datetime.now(timezone.utc).isoformat(),
                                "reason": apply_result.error or "apply failed",
                            }
                        )
                        self._save_record(record)
                return PublicationResult(
                    success=False,
                    rule_id=rule.rule_id,
                    stage=PublicationStage.SHADOW,
                    traffic_percentage=0.0,
                    operated_at=operated_at,
                    validation_result=validation_result,
                    error=apply_result.error or "FULL 应用失败",
                )
            audit_seq = apply_result.audit_entry_seq

        # 阶段 4：写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule.rule_id,
                rule_description=rule.description,
                validation_passed=(validation_result.passed if validation_result is not None else True),
                applied=(stage == PublicationStage.FULL),
                rollback_triggered=False,
            )
        except Exception as e:
            logger.error("灰度发布审计写入失败（不影响发布）：%s", e)

        logger.info(
            "规则 %s 已发布到 %s 阶段（流量 %.1f%%）",
            rule.rule_id,
            stage.value,
            stage.traffic_percentage * 100,
        )

        return PublicationResult(
            success=True,
            rule_id=rule.rule_id,
            stage=stage,
            traffic_percentage=stage.traffic_percentage,
            operated_at=operated_at,
            validation_result=validation_result,
            audit_entry_seq=audit_seq,
        )

    def check_auto_demotion(
        self,
        rule_id: str,
        metrics: dict[str, Any],
    ) -> str | None:
        """检查指标是否触发自动降级。

        Args:
            rule_id: 规则 ID。
            metrics: 当前效果指标。

        Returns:
            触发降级的原因字符串；None 表示无需降级。
        """
        with self._lock:
            record = self._records.get(rule_id)
            if record is None:
                return None
            # SHADOW 阶段不检查（无流量）
            if record.current_stage == PublicationStage.SHADOW:
                return None

        return self._check_demotion_thresholds(metrics)
