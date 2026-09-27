"""统一嵌入空间（从 space 拆出）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field


import numpy as np

from app.ai.unified_embedding._axes import (
    MATERIAL_DIMS,
    MATERIAL_OFFSET,
    PRECISION_DIMS,
    PRECISION_OFFSET,
    PROCESS_DIMS,
    PROCESS_OFFSET,
    RESERVED_DIMS,
    RESERVED_OFFSET,
    RISK_DIMS,
    RISK_OFFSET,
    STATE_DIMS,
    STATE_OFFSET,
    TOTAL_DIMS,
    MaterialAxis,
    PrecisionAxis,
    ProcessAxis,
    RiskAxis,
    StateAxis,
)

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingSpace:
    """The 512-dimensional unified manufacturing semantic embedding space.

    Attributes:
        total_dims: Total embedding dimensions (512).
        material: Material properties axis.
        process: Process methods axis.
        precision: Dimensional precision axis.
        state: Equipment/tool state axis.
        risk: Safety risk axis.
        reserved_offset: Start of reserved dimensions.
        reserved_dims: Number of reserved dimensions.

    Example:
        >>> space = EmbeddingSpace()
        >>> emb = space.create_empty()
        >>> emb.shape
        (512,)
    """

    total_dims: int = TOTAL_DIMS
    material: MaterialAxis = field(default_factory=MaterialAxis)
    process: ProcessAxis = field(default_factory=ProcessAxis)
    precision: PrecisionAxis = field(default_factory=PrecisionAxis)
    state: StateAxis = field(default_factory=StateAxis)
    risk: RiskAxis = field(default_factory=RiskAxis)
    reserved_offset: int = RESERVED_OFFSET
    reserved_dims: int = RESERVED_DIMS

    def create_empty(self) -> np.ndarray:
        return np.zeros(self.total_dims, dtype=np.float32)

    def compose(
        self,
        material_vec: np.ndarray | None = None,
        process_vec: np.ndarray | None = None,
        precision_vec: np.ndarray | None = None,
        state_vec: np.ndarray | None = None,
        risk_vec: np.ndarray | None = None,
        reserved_vec: np.ndarray | None = None,
    ) -> np.ndarray:
        embedding = self.create_empty()
        if material_vec is not None:
            embedding[MATERIAL_OFFSET : MATERIAL_OFFSET + MATERIAL_DIMS] = material_vec[:MATERIAL_DIMS]
        if process_vec is not None:
            embedding[PROCESS_OFFSET : PROCESS_OFFSET + PROCESS_DIMS] = process_vec[:PROCESS_DIMS]
        if precision_vec is not None:
            embedding[PRECISION_OFFSET : PRECISION_OFFSET + PRECISION_DIMS] = precision_vec[:PRECISION_DIMS]
        if state_vec is not None:
            embedding[STATE_OFFSET : STATE_OFFSET + STATE_DIMS] = state_vec[:STATE_DIMS]
        if risk_vec is not None:
            embedding[RISK_OFFSET : RISK_OFFSET + RISK_DIMS] = risk_vec[:RISK_DIMS]
        if reserved_vec is not None:
            embedding[RESERVED_OFFSET : RESERVED_OFFSET + RESERVED_DIMS] = reserved_vec[:RESERVED_DIMS]
        return embedding

    def decompose(self, embedding: np.ndarray) -> dict[str, np.ndarray]:
        return {
            "material": embedding[MATERIAL_OFFSET : MATERIAL_OFFSET + MATERIAL_DIMS].copy(),
            "process": embedding[PROCESS_OFFSET : PROCESS_OFFSET + PROCESS_DIMS].copy(),
            "precision": embedding[PRECISION_OFFSET : PRECISION_OFFSET + PRECISION_DIMS].copy(),
            "state": embedding[STATE_OFFSET : STATE_OFFSET + STATE_DIMS].copy(),
            "risk": embedding[RISK_OFFSET : RISK_OFFSET + RISK_DIMS].copy(),
            "reserved": embedding[RESERVED_OFFSET : RESERVED_OFFSET + RESERVED_DIMS].copy(),
        }

    def validate(self, embedding: np.ndarray) -> dict[str, float]:
        if embedding.shape[-1] != self.total_dims:
            raise ValueError(
                f"嵌入空间维度验证失败：期望维度为 {self.total_dims}，实际维度为 {embedding.shape[-1]}。"
                f"请确保编码器输出维度与 UnifiedEmbeddingSpace 的总维度一致。"
            )
        metrics = {}
        metrics.update(self.material.validate(embedding))
        metrics.update(self.process.validate(embedding))
        metrics.update(self.precision.validate(embedding))
        metrics.update(self.state.validate(embedding))
        metrics.update(self.risk.validate(embedding))
        reserved = embedding[RESERVED_OFFSET : RESERVED_OFFSET + RESERVED_DIMS]
        metrics["reserved_mean"] = float(np.mean(reserved))
        metrics["reserved_std"] = float(np.std(reserved))
        metrics["total_norm"] = float(np.linalg.norm(embedding))
        return metrics

    def normalize(self, embedding: np.ndarray) -> np.ndarray:
        if embedding.ndim == 1:
            norm = np.linalg.norm(embedding)
            if norm > 1e-10:
                return embedding / norm
            return embedding
        if embedding.ndim == 2:
            norms = np.linalg.norm(embedding, axis=1, keepdims=True)
            norms = np.where(norms < 1e-10, 1.0, norms)
            return embedding / norms
        raise ValueError(f"嵌入归一化失败：输入维度为 {embedding.ndim}，期望1维或2维。")

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        a_norm = a / (np.linalg.norm(a) + 1e-10)
        b_norm = b / (np.linalg.norm(b) + 1e-10)
        return float(np.dot(a_norm, b_norm))

    def axis_similarity(self, a: np.ndarray, b: np.ndarray) -> dict[str, float]:
        return {
            "material": self.similarity(
                a[MATERIAL_OFFSET : MATERIAL_OFFSET + MATERIAL_DIMS],
                b[MATERIAL_OFFSET : MATERIAL_OFFSET + MATERIAL_DIMS],
            ),
            "process": self.similarity(
                a[PROCESS_OFFSET : PROCESS_OFFSET + PROCESS_DIMS],
                b[PROCESS_OFFSET : PROCESS_OFFSET + PROCESS_DIMS],
            ),
            "precision": self.similarity(
                a[PRECISION_OFFSET : PRECISION_OFFSET + PRECISION_DIMS],
                b[PRECISION_OFFSET : PRECISION_OFFSET + PRECISION_DIMS],
            ),
            "state": self.similarity(
                a[STATE_OFFSET : STATE_OFFSET + STATE_DIMS],
                b[STATE_OFFSET : STATE_OFFSET + STATE_DIMS],
            ),
            "risk": self.similarity(
                a[RISK_OFFSET : RISK_OFFSET + RISK_DIMS],
                b[RISK_OFFSET : RISK_OFFSET + RISK_DIMS],
            ),
        }

    def to_schema(self) -> dict:
        axes = {}
        for axis in [self.material, self.process, self.precision, self.state, self.risk]:
            sub_axes = {}
            for name, (offset, length) in axis._sub_axes.items():
                sub_axes[name] = {"offset": offset, "length": length}
            axes[axis.name] = {
                "offset": axis.offset,
                "dims": axis.dims,
                "description": axis.description,
                "sub_axes": sub_axes,
            }
        return {
            "total_dims": self.total_dims,
            "axes": axes,
            "reserved": {
                "offset": self.reserved_offset,
                "dims": self.reserved_dims,
                "description": "Future extensions and system optimization",
            },
        }
