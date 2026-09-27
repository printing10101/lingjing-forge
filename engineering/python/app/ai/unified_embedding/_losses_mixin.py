"""对比对齐损失计算 mixin（从 aligner 拆出）。"""

from __future__ import annotations

import numpy as np
from typing import Any
from collections.abc import Callable


class _LossesMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _compute_alignment_score: Callable[..., Any]
    _compute_uniformity: Callable[..., Any]
    _compatibility_scores: Any
    _loss_history: Any
    _temperature: Any
    config: Any

    def compute_similarity_matrix(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        a_norm = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-10)
        b_norm = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-10)
        return np.dot(a_norm, b_norm.T)

    def compute_infonce_loss(
        self,
        embeddings_a: np.ndarray,
        embeddings_b: np.ndarray,
    ) -> tuple[float, dict[str, float]]:
        """Compute symmetric InfoNCE contrastive loss.

        Args:
            embeddings_a: (batch_size, 512) embeddings from modality A
            embeddings_b: (batch_size, 512) embeddings from modality B

        Returns:
            (loss, metrics) where metrics includes alignment_score and uniformity
        """
        batch_size = embeddings_a.shape[0]

        sim_matrix = self.compute_similarity_matrix(embeddings_a, embeddings_b)
        sim_matrix = sim_matrix / self._temperature

        # [H9] InfoNCE 数值稳定性：减去每行 max 防止 exp 溢出。
        # softmax 形式对 exp(x - max) 与 exp(x) 等价（分子分母同比例缩放），
        # 但 log(sum(exp(x - max))) 数值稳定，避免大 batch + 高温度时溢出。
        exp_sim = np.exp(sim_matrix - sim_matrix.max(axis=1, keepdims=True))

        loss_a_to_b = 0.0
        for i in range(batch_size):
            pos_sim = sim_matrix[i, i]
            all_sim_sum = exp_sim[i].sum()
            loss_a_to_b += -pos_sim + np.log(all_sim_sum)
        loss_a_to_b = loss_a_to_b / batch_size

        if self.config.contrastive.symmetric:
            sim_matrix_t = sim_matrix.T
            # [H9] 对称分支同样减去每行 max
            exp_sim_t = np.exp(sim_matrix_t - sim_matrix_t.max(axis=1, keepdims=True))
            loss_b_to_a = 0.0
            for i in range(batch_size):
                pos_sim = sim_matrix_t[i, i]
                all_sim_sum = exp_sim_t[i].sum()
                loss_b_to_a += -pos_sim + np.log(all_sim_sum)
            loss_b_to_a = loss_b_to_a / batch_size
            total_loss = (loss_a_to_b + loss_b_to_a) / 2.0
        else:
            total_loss = loss_a_to_b

        alignment_score = self._compute_alignment_score(embeddings_a, embeddings_b)
        uniformity = self._compute_uniformity(embeddings_a, embeddings_b)

        metrics = {
            "infonce_loss": float(total_loss),
            "alignment_score": float(alignment_score),
            "uniformity": float(uniformity),
            "temperature": float(self._temperature),
        }
        self._loss_history.append(float(total_loss))

        return float(total_loss), metrics

    def compute_triplet_loss(
        self,
        anchor: np.ndarray,
        positive: np.ndarray,
        negative: np.ndarray,
    ) -> tuple[float, dict[str, float]]:
        """Compute cross-modal triplet loss with optional hard negative mining.

        L_triplet(a, p, n) = max(0, ||a - p||_2 - ||a - n||_2 + margin)

        Args:
            anchor: (batch_size, 512) anchor embeddings
            positive: (batch_size, 512) positive pair embeddings
            negative: (batch_size, 512) or (batch_size, num_negatives, 512)

        Returns:
            (loss, metrics) with positive_distance, negative_distance, margin
        """
        cfg = self.config.triplet
        batch_size = anchor.shape[0]

        d_pos = np.linalg.norm(anchor - positive, axis=1)

        if negative.ndim == 3:
            num_negatives = negative.shape[1]
            d_neg_all = np.zeros((batch_size, num_negatives))
            for j in range(num_negatives):
                d_neg_all[:, j] = np.linalg.norm(anchor - negative[:, j, :], axis=1)

            if cfg.hard_negative_mining:
                n_hard = max(1, int(num_negatives * cfg.hard_negative_ratio))
                d_neg = np.partition(d_neg_all, n_hard - 1, axis=1)[:, :n_hard].mean(axis=1)
            else:
                d_neg = d_neg_all.min(axis=1)
        else:
            d_neg = np.linalg.norm(anchor - negative, axis=1)

        losses = np.maximum(0, d_pos - d_neg + cfg.margin)

        if cfg.reduction == "mean":
            loss = float(losses.mean())
        elif cfg.reduction == "sum":
            loss = float(losses.sum())
        else:
            loss = float(losses.mean())

        metrics = {
            "triplet_loss": loss,
            "mean_positive_distance": float(d_pos.mean()),
            "mean_negative_distance": float(d_neg.mean()),
            "margin": cfg.margin,
            "hard_triplet_ratio": float((losses > 0).mean()),
        }
        self._loss_history.append(loss)

        return loss, metrics

    def compute_preservation_loss(
        self,
        original_embeddings: np.ndarray,
        updated_embeddings: np.ndarray,
    ) -> tuple[float, float]:
        """Compute space preservation loss for incremental updates.

        Ensures updated embeddings remain compatible with the original space.
        L_preservation = 1 - cosine_similarity(original, updated)
        """
        original_norm = original_embeddings / (np.linalg.norm(original_embeddings, axis=1, keepdims=True) + 1e-10)
        updated_norm = updated_embeddings / (np.linalg.norm(updated_embeddings, axis=1, keepdims=True) + 1e-10)
        similarities = np.sum(original_norm * updated_norm, axis=1)
        loss = float(1.0 - similarities.mean())
        compatibility = float(similarities.mean())
        self._compatibility_scores.append(compatibility)
        return loss, compatibility

    def compute_total_loss(
        self,
        embeddings_a: np.ndarray,
        embeddings_b: np.ndarray,
        negative_embeddings: np.ndarray | None = None,
        original_embeddings: np.ndarray | None = None,
        updated_embeddings: np.ndarray | None = None,
    ) -> tuple[float, dict[str, float]]:
        """Compute combined alignment loss with all components.

        L_total = w_align * L_infoNCE + w_preserve * L_preservation + w_reg * L_reg

        Args:
            embeddings_a: (batch_size, 512) from modality A
            embeddings_b: (batch_size, 512) from modality B
            negative_embeddings: Optional negative samples for triplet loss
            original_embeddings: Original embeddings before update
            updated_embeddings: Updated embeddings after alignment

        Returns:
            (total_loss, component_metrics)
        """
        cfg = self.config

        infonce_loss, infonce_metrics = self.compute_infonce_loss(embeddings_a, embeddings_b)
        total_loss = cfg.alignment_weight * infonce_loss
        metrics = {f"infonce_{k}": v for k, v in infonce_metrics.items()}

        if negative_embeddings is not None:
            triplet_loss, triplet_metrics = self.compute_triplet_loss(embeddings_a, embeddings_b, negative_embeddings)
            total_loss += cfg.alignment_weight * 0.3 * triplet_loss
            metrics.update({f"triplet_{k}": v for k, v in triplet_metrics.items()})

        if original_embeddings is not None and updated_embeddings is not None:
            preserve_loss, compatibility = self.compute_preservation_loss(original_embeddings, updated_embeddings)
            total_loss += cfg.preservation_weight * preserve_loss
            metrics.update(
                {
                    "preservation_loss": preserve_loss,
                    "compatibility": compatibility,
                }
            )

        metrics["total_loss"] = float(total_loss)

        return float(total_loss), metrics
