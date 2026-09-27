"""规则评估方法组：证据融合/HRC52 惩罚。"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

from app.dreaming._closed_loop_models import (
    ClosedLoopDecision,
    RuleOutcomeRecord,
)

logger = logging.getLogger(__name__)


class _ClosedLoopRuleMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_collector: Callable[..., Any]
    _get_fusion: Callable[..., Any]
    _get_next_stage: Callable[..., Any]
    _get_previous_stage: Callable[..., Any]
    _demote_confidence: Any
    _fusion_params: Any
    _hrc52_confidence_penalty: Any
    _lock: Any
    _max_conflict_for_promote: Any
    _min_samples_for_decision: Any
    _promote_confidence: Any
    _window_size: Any
    _windows: Any

    def evaluate_rule(self, rule_id: str) -> ClosedLoopDecision:
        """评估指定规则，返回决策建议。

        决策逻辑：
            1. 从滚动窗口取出最近样本
            2. 将成功/失败样本包装为 InferenceResult 列表
            3. 调用 DempsterShaferFusion.fuse() 得到 FusionResult
            4. 基于 confidence + conflict + ds_mass 决策：
                - 硬约束违反 → rollback
                - 样本不足 → keep
                - confidence ≥ promote_confidence 且 conflict ≤ max → promote
                - confidence ≤ demote_confidence 或 conflict > fusion_threshold → demote
                - 否则 → keep

        Args:
            rule_id: 规则 ID。

        Returns:
            ClosedLoopDecision 实例。
        """
        evaluated_at = datetime.now(timezone.utc).isoformat()

        with self._lock:
            window = self._windows.get(rule_id, deque(maxlen=self._window_size))
            samples = list(window)

        sample_count = len(samples)
        decision = ClosedLoopDecision(
            rule_id=rule_id,
            action="keep",
            reason="",
            sample_count=sample_count,
            evaluated_at=evaluated_at,
        )

        # 阶段 1：检查硬约束违反（通过 EffectivenessMetricsCollector）
        collector = self._get_collector()
        if collector is not None:
            try:
                metrics = collector.collect_metrics(rule_id)
                if metrics and metrics.hard_constraint_violations > 0:
                    decision.action = "rollback"
                    decision.reason = f"硬约束违反 {metrics.hard_constraint_violations} 次，触发自动回滚"
                    decision.fused_confidence = metrics.confidence
                    decision.conflict = 0.0
                    decision.ds_mass = 0.0
                    return decision
            except Exception as e:
                logger.debug("ClosedLoop: collect_metrics 失败（继续评估）：%s", e)

        # 阶段 2：样本不足直接 keep
        if sample_count < self._min_samples_for_decision:
            decision.action = "keep"
            decision.reason = f"样本数不足（{sample_count}/{self._min_samples_for_decision}），暂不决策"
            return decision

        # 阶段 3：Dempster-Shafer 融合
        fused_confidence, conflict, ds_mass = self._fuse_rule_evidence(samples)
        decision.fused_confidence = fused_confidence
        decision.conflict = conflict
        decision.ds_mass = ds_mass

        # 阶段 4：HRC52 pending_calibration 惩罚（强制降低置信度）
        fused_confidence = self._apply_hrc52_penalty(rule_id, fused_confidence)
        decision.fused_confidence = fused_confidence

        # 阶段 5：决策
        if fused_confidence >= self._promote_confidence and conflict <= self._max_conflict_for_promote:
            decision.action = "promote"
            decision.target_stage = self._get_next_stage(rule_id)
            decision.reason = (
                f"融合置信度 {fused_confidence:.3f} ≥ "
                f"{self._promote_confidence} 且冲突 {conflict:.3f} ≤ "
                f"{self._max_conflict_for_promote}，建议晋级"
            )
        elif fused_confidence <= self._demote_confidence or conflict > self._fusion_params["conflict_threshold"]:
            decision.action = "demote"
            decision.target_stage = self._get_previous_stage(rule_id)
            decision.reason = (
                f"融合置信度 {fused_confidence:.3f} ≤ {self._demote_confidence} 或冲突 {conflict:.3f} 过高，建议降级"
            )
        else:
            decision.action = "keep"
            decision.reason = f"融合置信度 {fused_confidence:.3f} 与冲突 {conflict:.3f} 均在容忍区间，保持现状"

        return decision

    def _fuse_rule_evidence(self, samples: list[RuleOutcomeRecord]) -> tuple:
        """将规则样本融合为单一置信度。

        将成功样本与失败样本分别包装为 InferenceResult：
            - 成功样本：prediction=1.0, confidence=record.confidence
            - 失败样本：prediction=0.0, confidence=1-record.confidence

        调用 DempsterShaferFusion.fuse() 得到 FusionResult，
        返回 (fused_confidence, conflict, ds_mass)。

        若 DempsterShaferFusion 不可用，退化为加权平均。

        Args:
            samples: 滚动窗口内的样本列表。

        Returns:
            (fused_confidence, conflict, ds_mass) 三元组。
        """
        fusion = self._get_fusion()

        if fusion is None or not samples:
            # 退化为加权平均
            total_weight = 0.0
            weighted_sum = 0.0
            for s in samples:
                w = max(0.1, min(1.0, s.confidence))
                weighted_sum += (1.0 if s.success else 0.0) * w
                total_weight += w
            fused = weighted_sum / total_weight if total_weight > 0 else 0.0
            return (fused, 0.0, fused)

        try:
            from app.ai.lnn.core import EngineType, InferenceResult

            results: list[InferenceResult] = []
            for s in samples:
                if s.success:
                    results.append(
                        InferenceResult(
                            prediction=1.0,
                            confidence=max(0.3, s.confidence),
                            engine_used=EngineType.RULE,
                            model_used=s.rule_id,
                            processing_time_ms=0.0,
                            metadata={"source": s.source, "outcome": "success"},
                        )
                    )
                else:
                    results.append(
                        InferenceResult(
                            prediction=0.0,
                            confidence=max(0.3, 1.0 - s.confidence),
                            engine_used=EngineType.RULE,
                            model_used=s.rule_id,
                            processing_time_ms=0.0,
                            metadata={"source": s.source, "outcome": "failure"},
                        )
                    )

            fusion_result = fusion.fuse(results)
            qm = fusion_result.quality_metrics or {}
            return (
                float(fusion_result.confidence),
                float(qm.get("conflict", 0.0)),
                float(qm.get("ds_mass", fusion_result.confidence)),
            )
        except Exception as e:
            logger.warning(
                "ClosedLoop: DempsterShaferFusion.fuse 失败，退化为加权平均：%s",
                e,
            )
            total_weight = 0.0
            weighted_sum = 0.0
            for s in samples:
                w = max(0.1, min(1.0, s.confidence))
                weighted_sum += (1.0 if s.success else 0.0) * w
                total_weight += w
            fused = weighted_sum / total_weight if total_weight > 0 else 0.0
            return (fused, 0.0, fused)

    def _apply_hrc52_penalty(self, rule_id: str, confidence: float) -> float:
        """对 HRC52 pending_calibration 规则强制降低置信度。

        硬约束：HRC52 pending_calibration 强制降低置信度，
        避免未校准数据流入高阶段灰度。

        Args:
            rule_id: 规则 ID。
            confidence: 原始融合置信度。

        Returns:
            惩罚后的置信度。
        """
        if "HRC52" in rule_id or "hrc52" in rule_id.lower():
            penalized = confidence * self._hrc52_confidence_penalty
            logger.debug(
                "ClosedLoop: HRC52 pending_calibration 惩罚 %s %.3f → %.3f",
                rule_id,
                confidence,
                penalized,
            )
            return penalized
        return confidence
