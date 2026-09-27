"""_PublisherPromoteMixin (split from ProgressivePublisher)."""

from __future__ import annotations

from app.dreaming._publisher_models import (  # noqa: F401
    PublicationStage,
    PublicationRecord,
    PublicationResult,
    _STAGE_ORDER,
)

import logging
from datetime import datetime
from typing import Any
from collections.abc import Callable
from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming.rule_validator import ValidationResult
from datetime import timezone


logger = logging.getLogger(__name__)


class _PublisherPromoteMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _save_record: Callable[..., Any]
    _update_stage_only: Callable[..., Any]
    publish: Callable[..., Any]
    _lock: Any
    _promotion_thresholds: Any
    _records: Any
    _validator: Any

    def promote(
        self,
        rule_id: str,
        target_stage: PublicationStage | None = None,
        metrics_snapshot: dict[str, Any] | None = None,
        rule: RuleDraft | None = None,
    ) -> PublicationResult:
        """将规则晋级到下一阶段（或指定阶段）。

        晋级前会检查效果指标是否达标（如果提供了 metrics_snapshot）。
        FULL 阶段晋级前会重新调用 RuleValidator 双重校验。

        Args:
            rule_id: 规则 ID。
            target_stage: 目标阶段。None 表示晋级到下一阶段。
            metrics_snapshot: 当前效果指标快照（用于阈值检查）。
            rule: 规则草稿（FULL 阶段晋级必须提供，用于重新校验）。

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
                    error=f"规则 {rule_id} 未发布，无法晋级",
                )

            current_stage = record.current_stage
            if target_stage is None:
                next_stage = current_stage.next_stage
                if next_stage is None:
                    return PublicationResult(
                        success=False,
                        rule_id=rule_id,
                        stage=current_stage,
                        traffic_percentage=current_stage.traffic_percentage,
                        operated_at=operated_at,
                        error=f"已在最高阶段 {current_stage.value}，无法晋级",
                    )
            else:
                next_stage = target_stage

            # 阶段顺序校验：不允许跨级晋级（必须 1%10%50%100%）
            if target_stage is not None:
                current_idx = _STAGE_ORDER.index(current_stage)
                target_idx = _STAGE_ORDER.index(target_stage)
                if target_idx != current_idx + 1:
                    return PublicationResult(
                        success=False,
                        rule_id=rule_id,
                        stage=current_stage,
                        traffic_percentage=current_stage.traffic_percentage,
                        operated_at=operated_at,
                        error=(f"不允许跨级晋级：{current_stage.value} → {target_stage.value}（必须逐级晋级）"),
                    )

            # 指标阈值检查
            if metrics_snapshot:
                metrics_ok, fail_reason = self._check_promotion_thresholds(metrics_snapshot)
                if not metrics_ok:
                    return PublicationResult(
                        success=False,
                        rule_id=rule_id,
                        stage=current_stage,
                        traffic_percentage=current_stage.traffic_percentage,
                        operated_at=operated_at,
                        error=f"效果指标未达晋级阈值：{fail_reason}",
                    )
                record.last_metrics = dict(metrics_snapshot)

        # FULL 阶段必须重新沙箱校验
        validation_result: ValidationResult | None = None
        if next_stage == PublicationStage.FULL:
            if rule is None:
                return PublicationResult(
                    success=False,
                    rule_id=rule_id,
                    stage=current_stage,
                    traffic_percentage=current_stage.traffic_percentage,
                    operated_at=operated_at,
                    error="FULL 阶段晋级必须提供 rule 参数用于沙箱校验",
                )
            validation_result = self._validator.validate(rule)
            if not validation_result.passed:
                return PublicationResult(
                    success=False,
                    rule_id=rule_id,
                    stage=current_stage,
                    traffic_percentage=current_stage.traffic_percentage,
                    operated_at=operated_at,
                    validation_result=validation_result,
                    error=f"FULL 阶段沙箱校验失败：{validation_result.errors}",
                )

        # 调用 publish 切换阶段
        if rule is not None:
            result = self.publish(
                rule=rule,
                stage=next_stage,
                skip_validation=(validation_result is not None),
            )
        else:
            # 非 FULL 阶段晋级：仅更新灰度记录，不真正 apply
            result = self._update_stage_only(
                rule_id=rule_id,
                target_stage=next_stage,
                operated_at=operated_at,
                validation_result=validation_result,
            )

        # 更新晋级计数
        if result.success:
            with self._lock:
                rec = self._records.get(rule_id)
                if rec is not None:
                    rec.promoted_count += 1
                    rec.stage_history.append(
                        {
                            "action": "promote",
                            "from": current_stage.value,
                            "to": next_stage.value,
                            "operated_at": operated_at,
                        }
                    )
                    self._save_record(rec)

        return result

    def _check_promotion_thresholds(self, metrics: dict[str, Any]) -> tuple[bool, str | None]:
        """检查指标是否达到晋级阈值。

        Returns:
            (是否通过, 失败原因) 元组。
        """
        accuracy = float(metrics.get("accuracy", 0.0))
        fpr = float(metrics.get("false_positive_rate", 1.0))
        sample = int(metrics.get("sample_size", 0))
        err_rate = float(metrics.get("error_rate", 1.0))

        min_acc = self._promotion_thresholds["min_accuracy"]
        max_fpr = self._promotion_thresholds["max_false_positive_rate"]
        min_sample = self._promotion_thresholds["min_sample_size"]
        max_err = self._promotion_thresholds["max_error_rate"]

        if accuracy < min_acc:
            return False, f"accuracy={accuracy:.3f} < {min_acc}"
        if fpr > max_fpr:
            return False, f"false_positive_rate={fpr:.3f} > {max_fpr}"
        if sample < min_sample:
            return False, f"sample_size={sample} < {min_sample}"
        if err_rate > max_err:
            return False, f"error_rate={err_rate:.3f} > {max_err}"
        return True, None
