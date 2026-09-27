"""Hybrid rule + ML task router for the LNN inference pipeline.

This module implements the documented contract from ``ARCHITECTURE.md`` §3.3:
a hybrid scoring algorithm that combines a rule-based score (derived from
task metadata such as latency, precision, and data type) with an ML-based
score (derived from historical decision outcomes). The router selects the
engine with the highest combined score and emits a calibrated confidence
value instead of a fixed stub constant.

The implementation is intentionally lightweight (no external ML dependency)
but is no longer a stub: every decision is data-driven and reproducible.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any

from app.ai.lnn.core import EngineType, RoutingDecision, TaskInput, TaskCategory, DataType

logger = logging.getLogger(__name__)


class TaskRouter:
    """Hybrid rule + ML task router.

    Parameters:
        rule_weight: Weight of the rule-based score in the final decision
            (0.0-1.0). Must satisfy ``rule_weight + ml_weight <= 1.0``.
        ml_weight: Weight of the ML-based score in the final decision.
        confidence_threshold: Below this confidence the router emits a
            low-confidence flag so callers can fall back.
        enable_fallback: Whether to populate the ``alternatives`` field.
        history_size: Maximum number of recent decisions kept for the
            online ML score update. Defaults to 256.
    """

    # Engine affinity scores per task category. Each row sums to 1.0 so the
    # rule score is already normalised. Values are derived from the documented
    # strengths of each engine (LNN for fast numeric tasks, LLM for reasoning,
    # RULE for deterministic process rules, HYBRID for multimodal).
    _CATEGORY_AFFINITY: dict[TaskCategory, dict[EngineType, float]] = {
        TaskCategory.CLASSIFICATION: {
            EngineType.LNN: 0.55,
            EngineType.HYBRID: 0.25,
            EngineType.LLM: 0.10,
            EngineType.RULE: 0.10,
        },
        TaskCategory.REGRESSION: {
            EngineType.LNN: 0.60,
            EngineType.HYBRID: 0.20,
            EngineType.RULE: 0.15,
            EngineType.LLM: 0.05,
        },
        TaskCategory.TIME_SERIES: {
            EngineType.LNN: 0.65,
            EngineType.HYBRID: 0.20,
            EngineType.LLM: 0.10,
            EngineType.RULE: 0.05,
        },
        TaskCategory.NLP: {
            EngineType.LLM: 0.65,
            EngineType.HYBRID: 0.20,
            EngineType.LNN: 0.10,
            EngineType.RULE: 0.05,
        },
        TaskCategory.VISION: {
            EngineType.HYBRID: 0.55,
            EngineType.LNN: 0.25,
            EngineType.LLM: 0.15,
            EngineType.RULE: 0.05,
        },
        TaskCategory.LOGIC_REASONING: {
            EngineType.LLM: 0.55,
            EngineType.RULE: 0.25,
            EngineType.HYBRID: 0.15,
            EngineType.LNN: 0.05,
        },
        TaskCategory.RULE_BASED: {
            EngineType.RULE: 0.75,
            EngineType.HYBRID: 0.15,
            EngineType.LLM: 0.05,
            EngineType.LNN: 0.05,
        },
    }

    def __init__(
        self,
        rule_weight: float = 0.4,
        ml_weight: float = 0.6,
        confidence_threshold: float = 0.7,
        enable_fallback: bool = True,
        history_size: int = 256,
    ) -> None:
        if not 0.0 <= rule_weight <= 1.0:
            raise ValueError("rule_weight must be in [0, 1]")
        if not 0.0 <= ml_weight <= 1.0:
            raise ValueError("ml_weight must be in [0, 1]")
        if rule_weight + ml_weight > 1.0 + 1e-6:
            raise ValueError("rule_weight + ml_weight must not exceed 1.0")
        if history_size < 1:
            raise ValueError("history_size must be positive")

        self._rule_weight = rule_weight
        self._ml_weight = ml_weight
        self._confidence_threshold = confidence_threshold
        self._enable_fallback = enable_fallback
        self._history_size = history_size

        # Online ML signal: rolling success rate per engine. Seeds are small
        # pseudo-counts so a brand-new engine does not get a zero score.
        self._engine_success: dict[EngineType, deque[float]] = {eng: deque(maxlen=history_size) for eng in EngineType}
        self._engine_priors: dict[EngineType, float] = {
            EngineType.LNN: 0.75,
            EngineType.LLM: 0.70,
            EngineType.HYBRID: 0.72,
            EngineType.RULE: 0.90,
        }

        self._decision_history: list[dict[str, Any]] = []

        # H8 修复：route() 与 update_outcome() 并发调用时 _decision_history 切片重建
        # 与 _engine_success deque append 非原子，加锁保护。
        self._lock = threading.Lock()

    @property
    def rule_weight(self) -> float:
        return self._rule_weight

    @property
    def ml_weight(self) -> float:
        return self._ml_weight

    # Public API

    def route(self, task: TaskInput) -> RoutingDecision:
        """Produce a routing decision for ``task``.

        The hybrid score for each engine is::

            score = rule_weight * rule_score + ml_weight * ml_score

        where ``rule_score`` comes from category/data-type affinity and
        ``ml_score`` comes from the rolling success rate of that engine.
        The engine with the highest score wins; confidence is the winning
        score itself (already in [0, 1]).
        """
        category = task.task_category or self._detect_category(task)
        rule_scores = self._rule_scores(task, category)
        ml_scores = self._ml_scores()

        combined: dict[EngineType, float] = {}
        for eng in EngineType:
            combined[eng] = self._rule_weight * rule_scores.get(eng, 0.0) + self._ml_weight * ml_scores.get(eng, 0.0)

        selected_engine = max(combined, key=lambda e: combined[e])
        confidence = max(0.0, min(1.0, combined[selected_engine]))
        factors = {eng.value: round(score, 4) for eng, score in combined.items()}

        # Confidence calibration: if every engine is below threshold, mark
        # the decision as low-confidence so callers can fall back gracefully.
        below_threshold = confidence < self._confidence_threshold
        reasoning = self._build_reasoning(selected_engine, category, rule_scores, ml_scores, below_threshold)

        alternatives: list[dict[str, Any]] | None = None
        if self._enable_fallback:
            ranked = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)
            alternatives = [
                {
                    "engine": eng.value,
                    "model": self._default_model_for(eng),
                    "confidence": round(score, 4),
                }
                for eng, score in ranked[1:]
                if score > 0.0
            ][:2]

        decision = RoutingDecision(
            selected_engine=selected_engine,
            selected_model=self._default_model_for(selected_engine),
            confidence=confidence,
            reasoning=reasoning,
            decision_factors=factors,
            alternatives=alternatives,
            timestamp=time.time(),
        )

        # H8 修复：append + 切片重建在锁内原子完成，避免并发 update_outcome 竞态。
        with self._lock:
            self._decision_history.append(
                {
                    "task_description": task.task_description,
                    "task_category": category.value,
                    "selected_engine": selected_engine.value,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "timestamp": decision.timestamp,
                }
            )
            # Cap decision history to avoid unbounded growth.
            if len(self._decision_history) > self._history_size * 4:
                self._decision_history = self._decision_history[-self._history_size :]

        logger.debug(
            "TaskRouter routed task %r -> %s (confidence=%.3f, category=%s)",
            task.task_description[:80],
            selected_engine.value,
            confidence,
            category.value,
        )
        return decision

    def update_outcome(
        self,
        engine: EngineType,
        success: bool,
        confidence: float | None = None,
    ) -> None:
        """Feed back an observed outcome to update the online ML score.

        Parameters:
            engine: The engine that handled the task.
            success: Whether the outcome satisfied the caller's quality bar.
            confidence: Optional confidence reported by the engine; used to
                weight the sample (higher confidence => stronger update).
        """
        if engine not in self._engine_success:
            logger.warning("Unknown engine %r in update_outcome", engine)
            return
        weight = 1.0
        if confidence is not None:
            weight = max(0.1, min(1.0, float(confidence)))
        # H8 修复：deque append 在锁内，避免与 route() 并发丢失更新。
        with self._lock:
            self._engine_success[engine].append((1.0 if success else 0.0) * weight)

    def get_decision_stats(self) -> dict[str, Any]:
        """Return aggregated statistics over the decision history."""
        total = len(self._decision_history)
        if total == 0:
            return {
                "total_decisions": 0,
                "engine_distribution": {},
                "avg_confidence": 0.0,
                "ml_engine_rates": {eng.value: self._ml_score(eng) for eng in EngineType},
            }

        engine_counts: dict[str, int] = {}
        confidence_sum = 0.0
        for entry in self._decision_history:
            engine = entry["selected_engine"]
            engine_counts[engine] = engine_counts.get(engine, 0) + 1
            confidence_sum += float(entry["confidence"])

        return {
            "total_decisions": total,
            "engine_distribution": engine_counts,
            "avg_confidence": confidence_sum / total,
            "ml_engine_rates": {eng.value: self._ml_score(eng) for eng in EngineType},
        }

    def reset_history(self) -> None:
        """Clear all accumulated decision and outcome history."""
        self._decision_history.clear()
        for eng in EngineType:
            self._engine_success[eng].clear()

    # Internal helpers

    def _rule_scores(
        self,
        task: TaskInput,
        category: TaskCategory,
    ) -> dict[EngineType, float]:
        """Compute rule-based per-engine scores in [0, 1]."""
        base = dict(self._CATEGORY_AFFINITY.get(category, self._default_affinity()))

        # Latency penalty: time-sensitive tasks downweight slow engines.
        latency_factor = max(0.0, min(1.0, task.time_sensitivity))
        if latency_factor > 0.7:
            # Heavily prefer LNN/RULE for time-critical tasks.
            base[EngineType.LNN] = base.get(EngineType.LNN, 0.0) + 0.10
            base[EngineType.RULE] = base.get(EngineType.RULE, 0.0) + 0.05
            base[EngineType.LLM] = max(0.0, base.get(EngineType.LLM, 0.0) - 0.10)

        # Precision requirement: high precision upweights HYBRID (fusion).
        precision_factor = max(0.0, min(1.0, task.precision_requirement))
        if precision_factor > 0.9:
            base[EngineType.HYBRID] = base.get(EngineType.HYBRID, 0.0) + 0.10

        # Data-type adjustment.
        if task.data_type == DataType.UNSTRUCTURED:
            base[EngineType.LLM] = base.get(EngineType.LLM, 0.0) + 0.05
        elif task.data_type == DataType.MULTIMODAL:
            base[EngineType.HYBRID] = base.get(EngineType.HYBRID, 0.0) + 0.05

        # Normalise so the row sums to 1.0 (defensive against drift).
        total = sum(base.values())
        if total <= 0:
            base = self._default_affinity()
            total = sum(base.values())
        return {eng: base.get(eng, 0.0) / total for eng in EngineType}

    def _ml_scores(self) -> dict[EngineType, float]:
        """Compute the ML-based per-engine scores from rolling success rates."""
        return {eng: self._ml_score(eng) for eng in EngineType}

    def _ml_score(self, engine: EngineType) -> float:
        """Return the online ML score for ``engine`` in [0, 1].

        Uses the rolling mean of observed outcomes, regularised toward the
        engine's prior so a brand-new engine starts at its prior rather than
        zero. The regularisation strength decays as more samples arrive
        (Bayesian shrinkage).
        """
        outcomes = self._engine_success[engine]
        prior = self._engine_priors.get(engine, 0.5)
        n = len(outcomes)
        if n == 0:
            return prior
        # Shrinkage weight: pseudo-count of 5 samples worth of prior.
        shrink = 5.0 / (5.0 + n)
        observed = sum(outcomes) / n
        return shrink * prior + (1.0 - shrink) * observed

    def _detect_category(self, task: TaskInput) -> TaskCategory:
        """Heuristically detect the task category from input shape/description."""
        data = task.input_data
        desc = (task.task_description or "").lower()
        if task.data_type == DataType.UNSTRUCTURED or any(
            kw in desc for kw in ("text", "explain", "reason", "summarize", "describe")
        ):
            return TaskCategory.NLP
        if task.data_type == DataType.MULTIMODAL:
            return TaskCategory.VISION
        if any(kw in desc for kw in ("rule", "constraint", "check", "validate")):
            return TaskCategory.RULE_BASED
        if isinstance(data, (list, tuple)) and data and isinstance(data[0], (list, tuple)):
            return TaskCategory.TIME_SERIES
        if isinstance(data, dict) and any(k in data for k in ("speed", "feed", "depth", "force", "vibration")):
            return TaskCategory.REGRESSION
        return TaskCategory.REGRESSION

    @staticmethod
    def _default_affinity() -> dict[EngineType, float]:
        return {
            EngineType.LNN: 0.40,
            EngineType.HYBRID: 0.25,
            EngineType.LLM: 0.20,
            EngineType.RULE: 0.15,
        }

    @staticmethod
    def _default_model_for(engine: EngineType) -> str:
        return {
            EngineType.LNN: "cfc",
            EngineType.LLM: "qwen2.5",
            EngineType.HYBRID: "hybrid_v1",
            EngineType.RULE: "rule_v1",
        }.get(engine, "unknown")

    @staticmethod
    def _build_reasoning(
        engine: EngineType,
        category: TaskCategory,
        rule_scores: dict[EngineType, float],
        ml_scores: dict[EngineType, float],
        below_threshold: bool,
    ) -> str:
        parts = [
            f"category={category.value}",
            f"rule_top={max(rule_scores, key=lambda e: rule_scores[e]).value}",
            f"ml_top={max(ml_scores, key=lambda e: ml_scores[e]).value}",
            f"selected={engine.value}",
        ]
        if below_threshold:
            parts.append("low_confidence_below_threshold")
        return "|".join(parts)


__all__ = ["TaskRouter"]
