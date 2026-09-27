"""LNN 配置数据类（从 config_manager 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelConfig:
    """单个LNN模型的配置"""

    type: str = "cfc"
    path: str = ""
    enabled: bool = True
    device: str = "cpu"
    input_dim: int = 128
    output_dim: int = 10
    hidden_dim: int = 256
    num_layers: int = 2
    dropout_rate: float = 0.1
    temporal_horizon: int = 1000
    metadata: dict[str, Any] | None = None


@dataclass
class ThresholdConfig:
    """阈值配置"""

    quick: float = 0.85
    hybrid: float = 0.60
    complexity: int = 3
    fallback: float = 0.50
    confidence: float = 0.70


@dataclass
class LNNConfig:
    """LNN引擎配置"""

    enabled: bool = True
    models_dir: str = "models/lnn"
    default_device: str = "cpu"
    models: dict[str, ModelConfig] = field(default_factory=dict)
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    cache_size: int = 10
    enable_amp: bool = True
    max_retry_count: int = 3


@dataclass
class DatasetCacheConfig:
    """数据集缓存配置"""

    cache_directory: str = "~/.lingjing/cache/datasets/"
    max_cache_size: int = 5 * 1024 * 1024 * 1024
    memory_cache_size: int = 1024 * 1024 * 1024
    cache_eviction_policy: str = "lru"
    enabled: bool = True


@dataclass
class WorkflowConfig:
    """工作流配置"""

    enabled: bool = True
    max_steps: int = 10
    timeout_seconds: int = 300
    enable_fallback: bool = True
    fallback_engine: str = "Rule"
    log_enabled: bool = True
    log_dir: str = "logs/workflows"


@dataclass
class EnvironmentConfig:
    """环境配置"""

    name: str = "development"
    debug: bool = True
    device_override: str | None = None
    models_path_override: str | None = None


@dataclass
class AppConfig:
    """应用根配置"""

    lnn: LNNConfig = field(default_factory=LNNConfig)
    workflow: WorkflowConfig = field(default_factory=WorkflowConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    dataset_cache: DatasetCacheConfig = field(default_factory=DatasetCacheConfig)
