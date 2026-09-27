"""回滚异常检测 mixin（从 rollback_manager 拆出）。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from app.dreaming.apply_rules import RuleApplicator
from app.dreaming.effectiveness_metrics import (
    EffectivenessMetrics,
    EffectivenessMetricsCollector,
    OutcomeSample,
)
from app.dreaming.progressive_publisher import (
    PublicationStage,
    ProgressivePublisher,
)
from app.dreaming._rollback_models import RollbackDecision, RollbackExecutionResult

logger = logging.getLogger(__name__)


class _DetectMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _is_in_cooldown: Callable[..., Any]
    rollback_rule: Callable[..., Any]
    _applicator: Any
    _consecutive_anomalies: Any
    _lock: Any
    _metrics_collector: Any
    _publisher: Any
    consecutive_anomaly_threshold: Any
    production_error_rate_threshold: Any

    def _get_publisher(self) -> ProgressivePublisher:
        if self._publisher is None:
            self._publisher = ProgressivePublisher()
        return self._publisher

    def _get_applicator(self) -> RuleApplicator:
        if self._applicator is None:
            self._applicator = RuleApplicator()
        return self._applicator

    def _get_metrics_collector(self) -> EffectivenessMetricsCollector:
        if self._metrics_collector is None:
            self._metrics_collector = EffectivenessMetricsCollector()
        return self._metrics_collector

    def _get_audit_recorder(self):
        from app.dreaming.audit_integration import get_audit_recorder

        return get_audit_recorder()

    def detect_anomaly(
        self,
        rule_id: str,
        metrics: EffectivenessMetrics,
        samples: list[OutcomeSample] | None = None,
    ) -> RollbackDecision:
        """检测规则是否存在异常需要回滚。

        检测顺序（优先级从高到低）：
            1. 硬约束违反（立即回滚）
            2. 生产异常率超限（立即降级）
            3. 指标连续恶化（累计计数，达阈值回滚）

        Args:
            rule_id: 规则 ID。
            metrics: 当前效果度量。
            samples: 触发样本列表（用于检测硬约束违反）。

        Returns:
            RollbackDecision 实例。
        """
        detected_at = datetime.now(timezone.utc).isoformat()
        metrics_snapshot = metrics.to_publisher_snapshot()

        # 优先级 1：硬约束违反
        if metrics.hard_constraint_violations > 0:
            reason = f"硬约束违反 {metrics.hard_constraint_violations} 次（CAM 绕过 / SUCCEEDED 解锁）"
            logger.warning(
                "规则 %s 触发硬约束违反回滚：%s",
                rule_id,
                reason,
            )
            return RollbackDecision(
                rule_id=rule_id,
                should_rollback=True,
                reason=reason,
                severity="hard_constraint",
                detected_at=detected_at,
                metrics_snapshot=metrics_snapshot,
            )

        # 优先级 2：生产异常率超限
        if metrics.error_rate > self.production_error_rate_threshold and metrics.sample_size > 0:
            reason = f"生产异常率 {metrics.error_rate:.3f} 超过阈值 {self.production_error_rate_threshold}"
            logger.warning("规则 %s 触发生产异常回滚：%s", rule_id, reason)
            return RollbackDecision(
                rule_id=rule_id,
                should_rollback=True,
                reason=reason,
                severity="production_error",
                detected_at=detected_at,
                metrics_snapshot=metrics_snapshot,
            )

        # 优先级 3：指标连续恶化
        with self._lock:
            anomaly_count = self._consecutive_anomalies.get(rule_id, 0)
            # 指标恶化判定：准确率低于 0.5 或误报率高于 0.4
            if (metrics.sample_size > 0 and metrics.accuracy < 0.5) or (
                metrics.sample_size > 0 and metrics.false_positive_rate > 0.4
            ):
                anomaly_count += 1
                self._consecutive_anomalies[rule_id] = anomaly_count
            else:
                # 指标恢复，重置计数
                if anomaly_count > 0:
                    self._consecutive_anomalies[rule_id] = 0
                    anomaly_count = 0

            if anomaly_count >= self.consecutive_anomaly_threshold:
                reason = (
                    f"连续 {anomaly_count} 次指标低于阈值"
                    f"（accuracy={metrics.accuracy:.3f}, "
                    f"fpr={metrics.false_positive_rate:.3f}）"
                )
                logger.warning("规则 %s 触发指标恶化回滚：%s", rule_id, reason)
                return RollbackDecision(
                    rule_id=rule_id,
                    should_rollback=True,
                    reason=reason,
                    severity="metrics_degradation",
                    detected_at=detected_at,
                    metrics_snapshot=metrics_snapshot,
                )

        # 样本不足时标记但不回滚
        if metrics.insufficient_data:
            logger.info(
                "规则 %s 样本不足（n=%d），暂不判定回滚",
                rule_id,
                metrics.sample_size,
            )

        return RollbackDecision(
            rule_id=rule_id,
            should_rollback=False,
            severity="none",
            detected_at=detected_at,
            metrics_snapshot=metrics_snapshot,
        )

    def monitor_and_rollback(self) -> list[RollbackExecutionResult]:
        """扫描所有灰度发布中的规则，检测异常并自动回滚。

        Returns:
            触发回滚的规则列表。
        """
        publisher = self._get_publisher()
        collector = self._get_metrics_collector()

        # 获取所有灰度发布中的规则
        publications = publisher.list_publications()
        results: list[RollbackExecutionResult] = []

        for record in publications:
            rule_id = record.rule_id

            # 跳过已废弃的规则
            if record.current_stage == PublicationStage.DEPRECATED:
                continue

            # 跳过 SHADOW 阶段（无流量，不收集指标）
            if record.current_stage == PublicationStage.SHADOW:
                continue

            # 跳过冷却期内的规则
            if self._is_in_cooldown(rule_id):
                logger.debug("规则 %s 在冷却期内，跳过检测", rule_id)
                continue

            # 收集指标
            try:
                metrics = collector.collect_metrics(rule_id)
            except Exception as e:
                logger.error("规则 %s 指标收集失败：%s", rule_id, e)
                continue

            # 检测异常
            decision = self.detect_anomaly(rule_id, metrics)
            if not decision.should_rollback:
                continue

            # 执行回滚
            result = self.rollback_rule(
                rule_id=rule_id,
                reason=decision.reason,
                severity=decision.severity,
                fully_deprecate=(decision.severity == "hard_constraint"),
            )
            results.append(result)

        if results:
            logger.info("本轮监控触发了 %d 条规则回滚", len(results))
        return results
