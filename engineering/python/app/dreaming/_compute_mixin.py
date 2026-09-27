"""效果度量计算 mixin（从 effectiveness_metrics 拆出）。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import Callable

from app.dreaming._metrics_models import (
    CONFIDENT_HIGH_SAMPLES,
    CONFIDENT_MID_SAMPLES,
    EffectivenessMetrics,
    OutcomeSample,
)

logger = logging.getLogger(__name__)


class _ComputeMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_audit_recorder: Callable[..., Any]
    _lock: Any
    _samples: Any
    min_sample_size: Any
    window_days: Any

    def collect_metrics(
        self,
        rule_id: str,
        window_days: int | None = None,
    ) -> EffectivenessMetrics:
        """收集指定规则的效果度量。

        Args:
            rule_id: 规则 ID。
            window_days: 度量窗口天数。None 表示使用默认值。

        Returns:
            EffectivenessMetrics 实例。
        """
        window = window_days or self.window_days
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=window)

        with self._lock:
            all_samples = list(self._samples.get(rule_id, []))

        # 按窗口过滤
        window_samples: list[OutcomeSample] = []
        for s in all_samples:
            try:
                triggered = datetime.fromisoformat(s.triggered_at)
                if window_start <= triggered <= window_end:
                    window_samples.append(s)
            except (ValueError, TypeError):
                continue

        metrics = self._compute_metrics(
            rule_id=rule_id,
            samples=window_samples,
            window_start=window_start.isoformat(),
            window_end=window_end.isoformat(),
        )

        # 写入审计日志
        try:
            self._get_audit_recorder().record_rule_application(
                rule_id=rule_id,
                rule_description=(
                    f"效果度量：acc={metrics.accuracy:.3f}, "
                    f"fpr={metrics.false_positive_rate:.3f}, "
                    f"n={metrics.sample_size}"
                ),
                validation_passed=True,
                applied=False,
                rollback_triggered=False,
            )
        except Exception as e:
            logger.error("度量审计写入失败（不影响度量）：%s", e)

        return metrics

    def collect_all_metrics(
        self,
        window_days: int | None = None,
    ) -> dict[str, EffectivenessMetrics]:
        """收集所有已记录规则的效果度量。

        Args:
            window_days: 度量窗口天数。

        Returns:
            rule_id -> EffectivenessMetrics 映射。
        """
        with self._lock:
            rule_ids = list(self._samples.keys())

        return {rule_id: self.collect_metrics(rule_id, window_days) for rule_id in rule_ids}

    def get_samples(
        self,
        rule_id: str,
        window_days: int | None = None,
    ) -> list[OutcomeSample]:
        """获取指定规则在窗口内的样本列表。"""
        window = window_days or self.window_days
        window_end = datetime.now(timezone.utc)
        window_start = window_end - timedelta(days=window)

        with self._lock:
            all_samples = list(self._samples.get(rule_id, []))

        result: list[OutcomeSample] = []
        for s in all_samples:
            try:
                triggered = datetime.fromisoformat(s.triggered_at)
                if window_start <= triggered <= window_end:
                    result.append(s)
            except (ValueError, TypeError):
                continue
        return result

    # 内部度量计算

    def _compute_metrics(
        self,
        rule_id: str,
        samples: list[OutcomeSample],
        window_start: str,
        window_end: str,
    ) -> EffectivenessMetrics:
        """从样本列表计算度量指标。"""
        sample_size = len(samples)

        if sample_size == 0:
            return EffectivenessMetrics(
                rule_id=rule_id,
                sample_size=0,
                window_start=window_start,
                window_end=window_end,
                insufficient_data=True,
                confidence=0.0,
            )

        correct_count = sum(1 for s in samples if s.correct)
        fp_count = sum(1 for s in samples if s.false_positive)
        fn_count = sum(1 for s in samples if s.false_negative)
        err_count = sum(1 for s in samples if s.production_error)
        hc_violations = sum(1 for s in samples if s.cam_validation_bypassed or s.succeeded_lock_violated)

        accuracy = correct_count / sample_size
        false_positive_rate = fp_count / sample_size
        error_rate = err_count / sample_size

        # 召回率：triggered / (triggered + missed)
        # 触发数 = 样本数 - 漏报数（漏报样本来源有限，这里近似）
        triggered_count = sample_size - fn_count
        total_should_trigger = triggered_count + fn_count
        recall = triggered_count / total_should_trigger if total_should_trigger > 0 else 1.0

        # 置信度：基于样本数
        if sample_size >= CONFIDENT_HIGH_SAMPLES:
            confidence = 0.9
        elif sample_size >= CONFIDENT_MID_SAMPLES:
            confidence = 0.7
        elif sample_size >= self.min_sample_size:
            confidence = 0.5
        else:
            confidence = 0.2

        # 硬约束违反会降低置信度
        if hc_violations > 0:
            confidence *= 0.5

        insufficient = sample_size < self.min_sample_size

        return EffectivenessMetrics(
            rule_id=rule_id,
            accuracy=accuracy,
            recall=recall,
            false_positive_rate=false_positive_rate,
            error_rate=error_rate,
            sample_size=sample_size,
            conflict=None,  # 由 ClosedLoop 融合后填充
            confidence=confidence,
            window_start=window_start,
            window_end=window_end,
            insufficient_data=insufficient,
            hard_constraint_violations=hc_violations,
        )


# 便捷函数
