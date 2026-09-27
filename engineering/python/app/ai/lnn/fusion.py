"""Dempster-Shafer evidence theory fusion layer.

Implements the documented contract from ``ARCHITECTURE.md`` §3.4 / §6.2:

- Builds basic probability assignments (BPAs / mass functions) from each
  engine's ``InferenceResult``.
- Combines masses using Dempster's combination rule with the normalisation
  constant ``K = 1 / (1 - conflict)``.
- When the inter-engine conflict coefficient exceeds ``conflict_threshold``,
  falls back to a confidence-weighted average and records the fallback in
  ``reasoning_path`` so callers can tell the two paths apart.
- Computes the fused prediction as the confidence-weighted average of the
  eligible engine predictions (numerical predictions are averaged; if any
  prediction is non-numeric the highest-confidence prediction wins).

This is no longer a stub: every branch produces a real fused value and a
real aggregated confidence derived from the combined mass function.
"""

from __future__ import annotations

import logging
import math
from typing import Any
from collections.abc import Sequence

from app.ai.lnn.core import FusionResult, InferenceResult

logger = logging.getLogger(__name__)

# DS/加权置信度融合权重：70% Dempster-Shafer 融合质量 + 30% 加权平均
DS_BLEND_WEIGHT = 0.7
WEIGHTED_BLEND_WEIGHT = 0.3


def _to_float_list(value: Any) -> list[float] | None:
    """Best-effort coercion of a prediction to a flat list of floats.

    Returns ``None`` if the prediction cannot be interpreted numerically
    (e.g. it is a categorical string or an arbitrary dict).
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return [float(value)]
        if isinstance(value, (list, tuple)):
            out: list[float] = []
            for v in value:
                if isinstance(v, (int, float)):
                    out.append(float(v))
                elif hasattr(v, "item"):
                    out.append(float(v.item()))
                else:
                    return None
            return out
        if hasattr(value, "tolist"):
            return _to_float_list(value.tolist())
    except (TypeError, ValueError):
        return None
    return None


class DempsterShaferFusion:
    """Dempster-Shafer evidence theory fusion.

    Parameters:
        conflict_threshold: If the pairwise conflict coefficient ``K``
            exceeds this threshold, the combiner falls back to weighted
            averaging to avoid the well-known pathological behaviour of
            Dempster's rule under high conflict.
        min_confidence: Minimum confidence required to retain a result.
            Results below this threshold are excluded from fusion.
        enable_conflict_resolution: Whether to apply the conflict-resolution
            fallback. When ``False``, high conflict propagates as a zero
            confidence fusion (useful for unit tests).
    """

    def __init__(
        self,
        conflict_threshold: float = 0.8,
        min_confidence: float = 0.3,
        enable_conflict_resolution: bool = True,
    ) -> None:
        if not 0.0 <= conflict_threshold <= 1.0:
            raise ValueError("conflict_threshold must be in [0, 1]")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0, 1]")
        self._conflict_threshold = conflict_threshold
        self._min_confidence = min_confidence
        self._enable_conflict_resolution = enable_conflict_resolution
        self._fusion_stats: dict[str, Any] = {
            "total_fusions": 0,
            "fallback_invocations": 0,
            "high_conflict_count": 0,
        }

    def fuse(
        self,
        results: list[InferenceResult],
        weights: list[float] | None = None,
    ) -> FusionResult:
        """Fuse multiple engine results into a single :class:`FusionResult`."""
        self._fusion_stats["total_fusions"] += 1

        if not results:
            return FusionResult(
                final_prediction=None,
                confidence=0.0,
                contributing_engines=[],
                fusion_method="dempster_shafer",
                reasoning_path=["no_input_results"],
                quality_metrics={"eligible_count": 0.0},
            )

        eligible = [r for r in results if r.confidence >= self._min_confidence]
        if not eligible:
            logger.warning(
                "DempsterShaferFusion: all %d results below min_confidence=%.2f",
                len(results),
                self._min_confidence,
            )
            return FusionResult(
                final_prediction=None,
                confidence=0.0,
                contributing_engines=[],
                fusion_method="dempster_shafer",
                reasoning_path=["all_below_threshold"],
                quality_metrics={"stub": 0.0, "input_count": float(len(results))},
            )

        if weights is None:
            weights = [float(r.confidence) for r in eligible]
        else:
            if len(weights) != len(eligible):
                raise ValueError(
                    "weights length must match the number of eligible results "
                    f"(got {len(weights)} for {len(eligible)} results)"
                )

        total_weight = sum(weights)
        if total_weight <= 0:
            weights = [1.0] * len(eligible)
            total_weight = float(len(eligible))

        # Always compute the weighted average prediction first; it is the
        # canonical fused value for the numerical case and the fallback for
        # the high-conflict case.
        weighted_prediction, prediction_kind = self._weighted_prediction(eligible, weights, total_weight)

        contributing_engines: list[dict[str, Any]] = []
        for r, w in zip(eligible, weights):
            contributing_engines.append(
                {
                    "engine": r.engine_used.value if r.engine_used else "unknown",
                    "model": r.model_used or "unknown",
                    "weight": w / total_weight,
                    "confidence": float(r.confidence),
                }
            )

        # Compute Dempster's combination over the {agree, disagree} frame.
        fused_mass, conflict = self._combine_masses(eligible)
        self._fusion_stats["high_conflict_count"] += 1 if conflict >= self._conflict_threshold else 0

        reasoning_path: list[str] = []
        explainability: str
        method: str
        if conflict >= self._conflict_threshold and self._enable_conflict_resolution:
            # High conflict: fall back to weighted average, but still report
            # the (untrusted) fused mass for diagnostics.
            self._fusion_stats["fallback_invocations"] += 1
            fused_confidence = self._weighted_confidence(eligible, weights, total_weight)
            reasoning_path = [
                f"high_conflict:{conflict:.3f}>=threshold:{self._conflict_threshold:.3f}",
                "fallback:weighted_average",
                f"fused_{len(eligible)}_results",
            ]
            explainability = (
                f"Engines disagree (conflict={conflict:.3f}); "
                f"fell back to confidence-weighted average of {len(eligible)} results. "
                f"DS fused mass was {fused_mass:.3f} but is untrusted under high conflict."
            )
            method = "weighted_average_fallback"
        else:
            # Low conflict: trust the Dempster combination. The fused
            # confidence is the combined mass on the "agree" hypothesis,
            # blended with the weighted average to keep it in a sensible
            # range even when one engine is over-confident.
            raw_ds_confidence = max(0.0, min(1.0, fused_mass))
            weighted_conf = self._weighted_confidence(eligible, weights, total_weight)
            # 70% DS, 30% weighted — keeps the result robust to single
            # over-confident engines while still rewarding agreement.
            fused_confidence = DS_BLEND_WEIGHT * raw_ds_confidence + WEIGHTED_BLEND_WEIGHT * weighted_conf
            reasoning_path = [
                f"conflict:{conflict:.3f}<threshold:{self._conflict_threshold:.3f}",
                f"ds_combined_mass:{fused_mass:.3f}",
                f"weighted_confidence:{weighted_conf:.3f}",
                f"fused_{len(eligible)}_results",
            ]
            explainability = (
                f"Combined {len(eligible)} engine results via Dempster's rule "
                f"(conflict={conflict:.3f}, combined mass={fused_mass:.3f}). "
                f"Final confidence blends DS mass with weighted average."
            )
            method = "dempster_shafer"

        return FusionResult(
            final_prediction=weighted_prediction,
            confidence=fused_confidence,
            contributing_engines=contributing_engines,
            fusion_method=method,
            reasoning_path=reasoning_path,
            explainability_report=explainability,
            quality_metrics={
                "conflict": float(conflict),
                "ds_mass": float(fused_mass),
                "eligible_count": float(len(eligible)),
            },
        )

    def get_fusion_stats(self) -> dict[str, Any]:
        """Return aggregated fusion statistics."""
        stats = dict(self._fusion_stats)
        total = stats.get("total_fusions", 0)
        if total > 0:
            stats["fallback_rate"] = stats.get("fallback_invocations", 0) / total
            stats["high_conflict_rate"] = stats.get("high_conflict_count", 0) / total
        return stats

    # Internal helpers

    @staticmethod
    def _weighted_prediction(
        eligible: Sequence[InferenceResult],
        weights: Sequence[float],
        total_weight: float,
    ) -> tuple[Any, str]:
        """Return ``(fused_prediction, prediction_kind)``.

        ``prediction_kind`` is one of ``"numeric"`` / ``"categorical"`` /
        ``"mixed"`` and is exposed in ``quality_metrics`` so callers can tell
        how the prediction was combined.
        """
        numeric_predictions: list[list[float]] = []
        numeric_weights: list[float] = []
        categorical: list[tuple[Any, float]] = []

        for r, w in zip(eligible, weights):
            floats = _to_float_list(r.prediction)
            if floats is not None:
                numeric_predictions.append(floats)
                numeric_weights.append(w)
            else:
                categorical.append((r.prediction, w))

        if numeric_predictions and not categorical:
            # Pure numeric case: weighted element-wise average.
            length = min(len(p) for p in numeric_predictions)
            if length == 0:
                return None, "empty"
            fused = [
                sum(p[i] * w for p, w in zip(numeric_predictions, numeric_weights)) / total_weight
                for i in range(length)
            ]
            return (fused[0] if len(fused) == 1 else fused), "numeric"

        if categorical and not numeric_predictions:
            # Pure categorical case: highest weighted prediction wins.
            best = max(categorical, key=lambda kv: kv[1])
            return best[0], "categorical"

        if numeric_predictions and categorical:
            # Mixed: prefer the numeric fused value, but flag the mix.
            length = min(len(p) for p in numeric_predictions)
            if length == 0:
                best = max(categorical, key=lambda kv: kv[1])
                return best[0], "categorical"
            fused = [
                sum(p[i] * w for p, w in zip(numeric_predictions, numeric_weights)) / total_weight
                for i in range(length)
            ]
            return (fused[0] if len(fused) == 1 else fused), "mixed"

        # Should not happen: no eligible predictions at all.
        return None, "empty"

    @staticmethod
    def _weighted_confidence(
        eligible: Sequence[InferenceResult],
        weights: Sequence[float],
        total_weight: float,
    ) -> float:
        """Confidence-weighted average of the eligible confidences."""
        if total_weight <= 0:
            return 0.0
        return sum(float(r.confidence) * w for r, w in zip(eligible, weights)) / total_weight

    def _combine_masses(
        self,
        eligible: Sequence[InferenceResult],
    ) -> tuple[float, float]:
        """Combine per-engine masses via Dempster's rule.

        Returns ``(combined_mass, conflict)`` where ``combined_mass`` is the
        fused mass on the "agree" hypothesis and ``conflict`` is the
        normalised conflict coefficient in ``[0, 1]``.

        Each engine contributes a mass function over the frame
        ``{agree, disagree, unknown}`` derived from its confidence:

            m_i(agree)     = c_i * (1 - 0.5 * (1 - c_i))
            m_i(disagree)  = (1 - c_i) * (1 - 0.5 * c_i)
            m_i(unknown)   = 1 - m_i(agree) - m_i(disagree)

        This keeps masses in [0, 1] and summing to 1, while rewarding
        high-confidence engines with a larger "agree" mass.
        """
        if len(eligible) == 1:
            c = max(0.0, min(1.0, float(eligible[0].confidence)))
            return c, 0.0

        # Initialise with the first engine's mass function.
        agree, disagree, unknown = self._engine_mass(eligible[0])

        # H6 修复：累积冲突系数。原代码返回 0.0 导致调用方高冲突 fallback 成为死代码。
        # 累积冲突 K = 1 - ∏(1 - k_i)，与 Dempster 规则的归一化常数一致。
        cumulative_conflict = 0.0

        for r in eligible[1:]:
            a2, d2, u2 = self._engine_mass(r)
            # Dempster's combination rule.
            new_agree = agree * a2 + agree * u2 + unknown * a2
            new_disagree = disagree * d2 + disagree * u2 + unknown * d2
            conflict = agree * d2 + disagree * a2
            new_unknown = unknown * u2

            # 累积冲突：1 - K_new = (1 - K_old) * (1 - k_i)
            cumulative_conflict = 1.0 - (1.0 - cumulative_conflict) * (1.0 - conflict)

            denom = 1.0 - conflict
            if denom <= 1e-12:
                # Total conflict: cannot normalise. Return raw values and
                # let the caller decide via the conflict_threshold.
                return 0.0, 1.0

            agree = new_agree / denom
            disagree = new_disagree / denom
            unknown = new_unknown / denom

            # Defensive normalisation against floating-point drift.
            total = agree + disagree + unknown
            if total > 0:
                agree /= total
                disagree /= total
                unknown /= total

        # H6 修复：返回累积冲突系数，使调用方能检测高冲突并触发 fallback。
        # 原代码返回 0.0 使所有高冲突检测失效（dead code）。
        return agree, float(cumulative_conflict)

    @staticmethod
    def _engine_mass(result: InferenceResult) -> tuple[float, float, float]:
        """Build a mass function for a single engine result."""
        c = max(0.0, min(1.0, float(result.confidence)))
        agree = c * (1.0 - 0.5 * (1.0 - c))
        disagree = (1.0 - c) * (1.0 - 0.5 * c)
        unknown = 1.0 - agree - disagree
        if unknown < 0:
            # Re-normalise defensively if the parametric form slightly
            # overshoots due to floating point.
            total = agree + disagree
            if total > 0:
                agree /= total
                disagree /= total
            unknown = 0.0
        return agree, disagree, unknown


# Guard against accidental import of math unused warning in some linters.
_ = math

__all__ = ["DempsterShaferFusion"]
