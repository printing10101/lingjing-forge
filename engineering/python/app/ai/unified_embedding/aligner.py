"""Cross-modal embedding alignment via contrastive learning.

Implements the contrastive alignment strategy that ensures different modalities
(LLM text, LNN states, JEPA visual) map to semantically consistent positions
in the unified 512-dimensional embedding space.

Core algorithms:
    1. Cross-modal triplet loss with hard negative mining
    2. InfoNCE-based contrastive loss for batch-level alignment
    3. Dynamic temperature scaling for adaptive gradient flow
    4. Incremental embedding update with space compatibility preservation

本模块为门面：实现已拆分至 _aligner_models / _losses_mixin / _align_mixin。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod


import numpy as np

from app.ai.unified_embedding.space import get_embedding_space
from app.ai.unified_embedding._aligner_models import (  # noqa: F401
    AlignerConfig,
    ContrastiveConfig,
    TripletLossConfig,
)
from app.ai.unified_embedding._align_mixin import _AlignMixin
from app.ai.unified_embedding._losses_mixin import _LossesMixin

logger = logging.getLogger(__name__)


class EmbeddingAligner(ABC):
    """Abstract base class for embedding alignment strategies."""

    def __init__(self, config: AlignerConfig):
        self.config = config
        self._space = get_embedding_space()
        self._iteration: int = 0
        self._loss_history: list[float] = []

    @abstractmethod
    def compute_loss(
        self,
        anchor_embeddings: np.ndarray,
        positive_embeddings: np.ndarray,
        negative_embeddings: np.ndarray | None = None,
    ) -> tuple[float, dict[str, float]]:
        pass

    @abstractmethod
    def align(
        self,
        source_modality: str,
        target_modality: str,
        source_embeddings: np.ndarray,
        target_embeddings: np.ndarray,
    ) -> dict[str, float]:
        pass

    def get_loss_history(self) -> list[float]:
        return self._loss_history


class ContrastiveAligner(_LossesMixin, _AlignMixin):
    """Contrastive learning-based cross-modal alignment with InfoNCE loss.

    Implements:

    L_InfoNCE = -1/(2N) * sum_i [ log(exp(sim(z_i^A, z_i^B)/τ) / Σ_j exp(sim(z_i^A, z_j^B)/τ))
                                 + log(exp(sim(z_i^B, z_i^A)/τ) / Σ_j exp(sim(z_i^B, z_j^A)/τ)) ]

    where:
        z_i^A, z_i^B are embeddings of the same concept from modalities A and B
        τ is the temperature parameter
        N is the batch size

    Additionally implements:
        - Cross-modal triplet loss for fine-grained alignment
        - Space preservation regularization
        - Dynamic temperature adjustment
        - Incremental update compatibility check
    """

    def __init__(self, config: AlignerConfig | None = None):
        self.config = config or AlignerConfig()
        self._space = get_embedding_space()
        self._iteration: int = 0
        self._loss_history: list[float] = []
        self._temperature = self.config.contrastive.temperature
        self._compatibility_scores: list[float] = []
