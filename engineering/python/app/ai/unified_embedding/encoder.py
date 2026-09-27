"""Multi-modal embedding encoders for LLM, LNN, and JEPA models.

Each encoder projects its native representation into the unified 512-dimensional
manufacturing semantic embedding space.

LLMEncoder:   text embeddings (384-4096 dims) → [512 dims] via learned projection
LNNEncoder:   time-series features (18-128 dims) → [512 dims] via MLP projection
JEPAEncoder:  visual features (512-2048 dims) → [512 dims] via learned projection
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod


import numpy as np

from app.ai.unified_embedding.space import (
    TOTAL_DIMS,
    get_embedding_space,
)

logger = logging.getLogger(__name__)

DEFAULT_LLM_INPUT_DIM = 768
DEFAULT_LNN_INPUT_DIM = 18
DEFAULT_JEPA_INPUT_DIM = 1024

PROJECTION_INIT_SCALE = 0.02


class EmbeddingEncoder(ABC):
    """Abstract base class for embedding encoders that project into unified space."""

    def __init__(self, name: str, input_dim: int):
        self.name = name
        self.input_dim = input_dim
        self.output_dim = TOTAL_DIMS
        self._space = get_embedding_space()

    @abstractmethod
    def encode(self, inputs: np.ndarray) -> np.ndarray:
        pass

    @abstractmethod
    def encode_batch(self, inputs: np.ndarray) -> np.ndarray:
        pass

    def normalize(self, embeddings: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(embeddings, axis=-1, keepdims=True)
        norms = np.where(norms < 1e-10, 1.0, norms)
        return embeddings / norms

    def get_info(self) -> dict[str, object]:
        return {
            "name": self.name,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
        }


class LinearProjection:
    """Simple linear projection layer with optional bias and activation."""

    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        use_bias: bool = True,
        activation: str = "none",
        seed: int = 42,
    ):
        rng = np.random.RandomState(seed)
        self.weight = rng.randn(input_dim, output_dim).astype(np.float32) * PROJECTION_INIT_SCALE
        self.bias = np.zeros(output_dim, dtype=np.float32) if use_bias else None
        self.activation = activation

    def forward(self, x: np.ndarray) -> np.ndarray:
        y = np.dot(x, self.weight)
        if self.bias is not None:
            y = y + self.bias
        if self.activation == "tanh":
            y = np.tanh(y)
        elif self.activation == "relu":
            y = np.maximum(0, y)
        return y

    def get_weights(self) -> tuple[np.ndarray, np.ndarray | None]:
        return self.weight, self.bias

    def set_weights(self, weight: np.ndarray, bias: np.ndarray | None = None):
        self.weight = weight.astype(np.float32)
        if bias is not None and self.bias is not None:
            self.bias = bias.astype(np.float32)


class MLPProjection:
    """Two-layer MLP projection for more expressive encoding."""

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        output_dim: int,
        seed: int = 42,
    ):
        rng = np.random.RandomState(seed)
        self.w1 = rng.randn(input_dim, hidden_dim).astype(np.float32) * PROJECTION_INIT_SCALE
        self.b1 = np.zeros(hidden_dim, dtype=np.float32)
        self.w2 = rng.randn(hidden_dim, output_dim).astype(np.float32) * PROJECTION_INIT_SCALE
        self.b2 = np.zeros(output_dim, dtype=np.float32)

    def forward(self, x: np.ndarray) -> np.ndarray:
        h = np.maximum(0, np.dot(x, self.w1) + self.b1)
        return np.dot(h, self.w2) + self.b2


class LLMProjector(EmbeddingEncoder):
    """Projects LLM text embeddings into the unified manufacturing embedding space.

    The LLM component handles process knowledge understanding and natural language
    interaction. Its native text embeddings (typically 768-4096 dims) are projected
    via a two-layer MLP into the 512-dimensional unified space.
    """

    def __init__(
        self,
        input_dim: int = DEFAULT_LLM_INPUT_DIM,
        hidden_dim: int = 1024,
        seed: int = 42,
    ):
        super().__init__("LLMProjector", input_dim)
        self._projection = MLPProjection(input_dim, hidden_dim, TOTAL_DIMS, seed)

    def encode(self, text_embedding: np.ndarray) -> np.ndarray:
        if text_embedding.ndim == 1:
            text_embedding = text_embedding.reshape(1, -1)
        if text_embedding.shape[-1] != self.input_dim:
            raise ValueError(
                f"LLM编码器维度不匹配：期望输入维度 {self.input_dim}，"
                f"实际输入维度 {text_embedding.shape[-1]}。请检查LLM模型的输出维度配置。"
            )
        projected = self._projection.forward(text_embedding)
        return self.normalize(projected)

    def encode_batch(self, text_embeddings: np.ndarray) -> np.ndarray:
        return self.encode(text_embeddings)

    def project_from_tokens(
        self,
        tokens: np.ndarray,
        position_ids: np.ndarray | None = None,
    ) -> np.ndarray:
        aggregated = tokens.mean(axis=1) if tokens.ndim == 3 else tokens
        return self.encode(aggregated)

    def set_weights(
        self,
        w1: np.ndarray,
        b1: np.ndarray,
        w2: np.ndarray,
        b2: np.ndarray,
    ):
        self._projection.w1 = w1.astype(np.float32)
        self._projection.b1 = b1.astype(np.float32)
        self._projection.w2 = w2.astype(np.float32)
        self._projection.b2 = b2.astype(np.float32)


class LNNProjector(EmbeddingEncoder):
    """Projects LNN time-series features into the unified embedding space.

    The LNN component handles time-series prediction and real-time control. Its native
    state features (typically 18-dimensional sensor fusion) are projected via an MLP
    into the state and risk axes of the unified space, with optional alignment to
    material and process axes.
    """

    def __init__(
        self,
        input_dim: int = DEFAULT_LNN_INPUT_DIM,
        hidden_dim: int = 128,
        seed: int = 42,
    ):
        super().__init__("LNNProjector", input_dim)
        self._projection = MLPProjection(input_dim, hidden_dim, TOTAL_DIMS, seed)

    def encode(self, state_features: np.ndarray) -> np.ndarray:
        if state_features.ndim == 1:
            state_features = state_features.reshape(1, -1)
        if state_features.shape[-1] != self.input_dim:
            raise ValueError(
                f"LNN编码器维度不匹配：期望输入维度 {self.input_dim}，"
                f"实际输入维度 {state_features.shape[-1]}。请检查LNN模型的输出维度配置。"
            )
        projected = self._projection.forward(state_features)
        return self.normalize(projected)

    def encode_batch(self, state_features: np.ndarray) -> np.ndarray:
        return self.encode(state_features)

    def encode_timeseries(
        self,
        sequences: np.ndarray,
        window_size: int | None = None,
    ) -> np.ndarray:
        if sequences.ndim == 2:
            return self.encode(sequences)
        if sequences.ndim == 3:
            return self.encode(sequences.mean(axis=1))
        raise ValueError(
            f"LNN时序编码失败：输入维度为 {sequences.ndim}，期望2维或3维。"
            f"请确保输入形状为 (batch, features) 或 (batch, timesteps, features)。"
        )

    def set_weights(
        self,
        w1: np.ndarray,
        b1: np.ndarray,
        w2: np.ndarray,
        b2: np.ndarray,
    ):
        self._projection.w1 = w1.astype(np.float32)
        self._projection.b1 = b1.astype(np.float32)
        self._projection.w2 = w2.astype(np.float32)
        self._projection.b2 = b2.astype(np.float32)


class JEPAProjector(EmbeddingEncoder):
    """Projects JEPA visual/world model features into the unified embedding space.

    The JEPA component handles visual understanding and world modeling. Its native
    visual features (typically 1024-2048 dims from vision encoders) are projected
    via a linear layer with tanh activation into the material and process axes.
    """

    def __init__(
        self,
        input_dim: int = DEFAULT_JEPA_INPUT_DIM,
        seed: int = 42,
    ):
        super().__init__("JEPAProjector", input_dim)
        self._projection = LinearProjection(input_dim, TOTAL_DIMS, use_bias=True, activation="tanh", seed=seed)

    def encode(self, visual_features: np.ndarray) -> np.ndarray:
        if visual_features.ndim == 1:
            visual_features = visual_features.reshape(1, -1)
        if visual_features.shape[-1] != self.input_dim:
            raise ValueError(
                f"JEPA编码器维度不匹配：期望输入维度 {self.input_dim}，"
                f"实际输入维度 {visual_features.shape[-1]}。请检查JEPA模型的输出维度配置。"
            )
        projected = self._projection.forward(visual_features)
        return self.normalize(projected)

    def encode_batch(self, visual_features: np.ndarray) -> np.ndarray:
        return self.encode(visual_features)

    def encode_image_patch(
        self,
        patch_features: np.ndarray,
        spatial_positions: np.ndarray | None = None,
    ) -> np.ndarray:
        return self.encode(patch_features.mean(axis=1) if patch_features.ndim == 3 else patch_features)

    def set_weights(self, weight: np.ndarray, bias: np.ndarray | None = None):
        self._projection.weight = weight.astype(np.float32)
        if bias is not None and self._projection.bias is not None:
            self._projection.bias = bias.astype(np.float32)


class MultiModalEncoder:
    """Coordinates embeddings from all three modalities into the unified space.

    Provides batch encoding, modality-specific weighting, and weighted fusion
    of embeddings from different sources.

    Example:
        >>> encoder = MultiModalEncoder()
        >>> llm_emb = encoder.encode_llm(text_features)
        >>> lnn_emb = encoder.encode_lnn(state_features)
        >>> fused = encoder.fuse([llm_emb, lnn_emb], weights=[0.6, 0.4])
    """

    def __init__(
        self,
        llm_input_dim: int = DEFAULT_LLM_INPUT_DIM,
        lnn_input_dim: int = DEFAULT_LNN_INPUT_DIM,
        jepa_input_dim: int = DEFAULT_JEPA_INPUT_DIM,
        seed: int = 42,
    ):
        self.llm = LLMProjector(input_dim=llm_input_dim, seed=seed)
        self.lnn = LNNProjector(input_dim=lnn_input_dim, seed=seed + 1)
        self.jepa = JEPAProjector(input_dim=jepa_input_dim, seed=seed + 2)
        self._space = get_embedding_space()

    def encode_llm(self, text_features: np.ndarray) -> np.ndarray:
        return self.llm.encode(text_features)

    def encode_lnn(self, state_features: np.ndarray) -> np.ndarray:
        return self.lnn.encode(state_features)

    def encode_jepa(self, visual_features: np.ndarray) -> np.ndarray:
        return self.jepa.encode(visual_features)

    def fuse(
        self,
        embeddings: list[np.ndarray],
        weights: list[float] | None = None,
    ) -> np.ndarray:
        if weights is None:
            weights = [1.0 / len(embeddings)] * len(embeddings)
        total_weight = sum(weights)
        normalized_weights = [w / total_weight for w in weights]
        fused = np.zeros(TOTAL_DIMS, dtype=np.float32)
        for emb, w in zip(embeddings, normalized_weights):
            if emb.ndim == 2:
                emb = emb[0]
            fused = fused + (emb * w).astype(np.float32)
        return self._space.normalize(fused)

    def fuse_batch(
        self,
        llm_embeddings: np.ndarray | None = None,
        lnn_embeddings: np.ndarray | None = None,
        jepa_embeddings: np.ndarray | None = None,
        weights: list[float] | None = None,
    ) -> np.ndarray:
        active: list[np.ndarray] = []
        for emb in [llm_embeddings, lnn_embeddings, jepa_embeddings]:
            if emb is not None:
                active.append(emb)

        if len(active) == 1:
            return self._space.normalize(active[0])

        if weights is None:
            weights = [1.0 / len(active)] * len(active)
        total_weight = sum(weights)
        normalized_weights = [w / total_weight for w in weights]

        batch_size = active[0].shape[0]
        for emb in active:
            if emb.shape[0] != batch_size:
                raise ValueError(
                    f"多模态编码器批量融合失败：批次大小不一致。"
                    f"期望批次大小为 {batch_size}，实际批次大小为 {emb.shape[0]}。"
                )

        fused = np.zeros((batch_size, TOTAL_DIMS), dtype=np.float32)
        for emb, w in zip(active, normalized_weights):
            fused = fused + (emb * w).astype(np.float32)
        return self._space.normalize(fused)

    def encode_manufacturing_scene(
        self,
        text_description: np.ndarray | None = None,
        state_features: np.ndarray | None = None,
        visual_features: np.ndarray | None = None,
        modality_weights: dict[str, float] | None = None,
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        embeddings: dict[str, np.ndarray] = {}
        weight_list: list[float] = []
        emb_list: list[np.ndarray] = []

        if modality_weights is None:
            modality_weights = {}

        if text_description is not None:
            emb = self.encode_llm(text_description)
            embeddings["llm"] = emb
            w = modality_weights.get("llm", 0.4)
            weight_list.append(w)
            emb_list.append(emb)

        if state_features is not None:
            emb = self.encode_lnn(state_features)
            embeddings["lnn"] = emb
            w = modality_weights.get("lnn", 0.35)
            weight_list.append(w)
            emb_list.append(emb)

        if visual_features is not None:
            emb = self.encode_jepa(visual_features)
            embeddings["jepa"] = emb
            w = modality_weights.get("jepa", 0.25)
            weight_list.append(w)
            emb_list.append(emb)

        if not emb_list:
            raise ValueError(
                "制造场景编码失败：未提供任何输入模态。"
                "请至少提供 text_description、state_features 或 visual_features 中的一种。"
            )

        fused = self.fuse(emb_list, weight_list)
        return fused, embeddings

    def get_info(self) -> dict[str, object]:
        return {
            "llm": self.llm.get_info(),
            "lnn": self.lnn.get_info(),
            "jepa": self.jepa.get_info(),
        }
