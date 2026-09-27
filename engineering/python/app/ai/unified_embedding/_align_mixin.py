"""对比对齐执行/指标 mixin（从 aligner 拆出）。"""

from __future__ import annotations

import logging
import numpy as np
from typing import Any
from collections.abc import Callable

logger = logging.getLogger(__name__)


class _AlignMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    compute_preservation_loss: Callable[..., Any]
    compute_similarity_matrix: Callable[..., Any]
    compute_total_loss: Callable[..., Any]
    _compatibility_scores: Any
    _iteration: Any
    _loss_history: Any
    _space: Any
    _temperature: Any
    config: Any

    def align(
        self,
        source_modality: str,
        target_modality: str,
        source_embeddings: np.ndarray,
        target_embeddings: np.ndarray,
        learning_rate: float | None = None,
    ) -> dict[str, float]:
        """Perform one alignment step between two modalities.

        Uses gradient-based optimization to minimize the contrastive loss
        by adjusting embedding positions in the unified space.

        Args:
            source_modality: Name of source modality (e.g., "llm")
            target_modality: Name of target modality (e.g., "lnn")
            source_embeddings: (batch_size, 512) from source
            target_embeddings: (batch_size, 512) from target
            learning_rate: Step size for gradient update

        Returns:
            Metrics dictionary with loss, alignment_score, compatibility
        """
        lr = learning_rate or self.config.learning_rate
        batch_size = source_embeddings.shape[0]

        sim_matrix = self.compute_similarity_matrix(source_embeddings, target_embeddings)
        sim_matrix = sim_matrix / self._temperature

        # [H9] 梯度更新路径同样需要数值稳定的 exp，softmax 权重比例不变
        exp_sim = np.exp(sim_matrix - sim_matrix.max(axis=1, keepdims=True))

        grad_source = np.zeros_like(source_embeddings)
        grad_target = np.zeros_like(target_embeddings)

        source_norm = source_embeddings / (np.linalg.norm(source_embeddings, axis=1, keepdims=True) + 1e-10)
        target_norm = target_embeddings / (np.linalg.norm(target_embeddings, axis=1, keepdims=True) + 1e-10)

        for i in range(batch_size):
            pos_weight = 1.0 / self._temperature
            grad_source[i] = -pos_weight * target_norm[i]
            grad_target[i] = -pos_weight * source_norm[i]

            sum_exp = exp_sim[i].sum()
            for j in range(batch_size):
                weight = exp_sim[i, j] / (sum_exp * self._temperature)
                grad_source[i] = grad_source[i] + weight * target_norm[j]
                if self.config.contrastive.symmetric:
                    grad_target[j] = grad_target[j] + weight * source_norm[i]

        grad_source = grad_source / batch_size
        grad_target = grad_target / batch_size

        grad_norm_s = np.linalg.norm(grad_source)
        grad_norm_t = np.linalg.norm(grad_target)
        max_norm = self.config.gradient_clip_norm

        if grad_norm_s > max_norm:
            grad_source = grad_source * (max_norm / grad_norm_s)
        if grad_norm_t > max_norm:
            grad_target = grad_target * (max_norm / grad_norm_t)

        original_source = source_embeddings.copy()

        updated_source = source_embeddings - lr * grad_source
        updated_target = target_embeddings - lr * grad_target

        updated_source = self._space.normalize(updated_source)
        updated_target = self._space.normalize(updated_target)

        loss, metrics = self.compute_total_loss(
            updated_source,
            updated_target,
            original_embeddings=original_source.copy(),
            updated_embeddings=updated_source.copy(),
        )

        preserve_loss, compatibility = self.compute_preservation_loss(original_source, updated_source)
        metrics["compatibility"] = float(compatibility)

        self._iteration += 1
        self._update_temperature()

        logger.debug(
            "Align step %d: %s->%s, loss=%.4f, compat=%.4f, temp=%.4f",
            self._iteration,
            source_modality,
            target_modality,
            loss,
            compatibility,
            self._temperature,
        )

        return metrics

    def cross_modal_align(
        self,
        llm_embeddings: np.ndarray,
        lnn_embeddings: np.ndarray,
        jepa_embeddings: np.ndarray | None = None,
    ) -> dict[str, dict[str, float]]:
        """Perform full cross-modal alignment across all three modalities.

        Args:
            llm_embeddings: (batch_size, 512) LLM embeddings
            lnn_embeddings: (batch_size, 512) LNN embeddings
            jepa_embeddings: Optional (batch_size, 512) JEPA embeddings

        Returns:
            Dictionary mapping modality pairs to their alignment metrics
        """
        results = {}

        results["llm-lnn"] = self.align("llm", "lnn", llm_embeddings, lnn_embeddings)

        if jepa_embeddings is not None:
            results["llm-jepa"] = self.align("llm", "jepa", llm_embeddings, jepa_embeddings)
            results["lnn-jepa"] = self.align("lnn", "jepa", lnn_embeddings, jepa_embeddings)

        return results

    def _compute_alignment_score(self, a: np.ndarray, b: np.ndarray) -> float:
        sim = self.compute_similarity_matrix(a, b)
        batch_size = a.shape[0]
        if batch_size == 0:
            return 0.0
        diag_sim = np.diag(sim)
        return float(diag_sim.mean())

    def _compute_uniformity(
        self,
        a: np.ndarray,
        b: np.ndarray,
        t: float = 2.0,
    ) -> float:
        combined = np.concatenate([a, b], axis=0)
        n = combined.shape[0]
        if n <= 1:
            return 0.0
        norms = combined / (np.linalg.norm(combined, axis=1, keepdims=True) + 1e-10)
        sim = np.dot(norms, norms.T)
        uniformity = np.exp(-t * sim).sum() / (n * (n - 1))
        return float(-np.log(uniformity + 1e-10))

    def _update_temperature(self):
        if not self.config.contrastive.temperature_trainable:
            return
        if self._iteration % 10 == 0 and len(self._loss_history) >= 2:
            recent_loss = np.mean(self._loss_history[-10:])
            earlier_loss = np.mean(
                self._loss_history[max(0, len(self._loss_history) - 20) : len(self._loss_history) - 10]
            )
            if recent_loss < earlier_loss * 0.95:
                self._temperature = max(
                    self.config.contrastive.temperature_min,
                    self._temperature * 0.95,
                )

    def check_compatibility(self) -> bool:
        if len(self._compatibility_scores) < 10:
            return True
        recent_compat = np.mean(self._compatibility_scores[-10:])
        return recent_compat >= self.config.compatibility_threshold

    def get_stats(self) -> dict[str, float]:
        return {
            "total_iterations": self._iteration,
            "current_temperature": self._temperature,
            "mean_loss": float(np.mean(self._loss_history[-100:])) if self._loss_history else 0.0,
            "recent_compatibility": float(np.mean(self._compatibility_scores[-10:]))
            if self._compatibility_scores
            else 1.0,
            "compatibility_check": float(self.check_compatibility()),
        }
