"""对齐器配置数据类（从 aligner 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TripletLossConfig:
    """Configuration for triplet loss-based cross-modal alignment.

    The triplet loss ensures that embeddings of the same manufacturing concept
    from different modalities are closer than embeddings of different concepts.

    Loss: L = max(0, d(anchor, positive) - d(anchor, negative) + margin)
    """

    margin: float = 0.3
    p: float = 2.0
    swap: bool = True
    reduction: str = "mean"
    hard_negative_mining: bool = True
    hard_negative_ratio: float = 0.3


@dataclass
class ContrastiveConfig:
    """Configuration for InfoNCE-based contrastive alignment.

    Loss: L = -log( exp(sim(a, p) / tau) / sum(exp(sim(a, n_i) / tau)) )

    where tau is the temperature parameter controlling the concentration
    of the similarity distribution.
    """

    temperature: float = 0.07
    temperature_trainable: bool = True
    temperature_min: float = 0.01
    temperature_max: float = 1.0
    normalize_embeddings: bool = True
    symmetric: bool = True


@dataclass
class AlignerConfig:
    """Full configuration for embedding alignment."""

    triplet: TripletLossConfig = field(default_factory=TripletLossConfig)
    contrastive: ContrastiveConfig = field(default_factory=ContrastiveConfig)
    alignment_weight: float = 0.6
    preservation_weight: float = 0.2
    regularization_weight: float = 0.2
    learning_rate: float = 1e-4
    batch_size: int = 128
    gradient_clip_norm: float = 1.0
    update_interval_steps: int = 100
    compatibility_threshold: float = 0.95
