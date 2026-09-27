"""
配置管理模块 - 加载和管理数据管道配置

支持 YAML 配置文件加载、环境变量注入和动态配置。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


@dataclass
class ImageProcessorConfig:
    image_size: int = 256
    cnn_feature_dim: int = 512
    normalize_range: tuple = (0.0, 1.0)
    supported_bit_depths: list[int] = field(default_factory=lambda: [8, 16])
    pretrained_model: str = "resnet50"
    mean: list[float] = field(default_factory=lambda: [0.485, 0.456, 0.406])
    std: list[float] = field(default_factory=lambda: [0.229, 0.224, 0.225])


@dataclass
class TimeSeriesProcessorConfig:
    window_size: int = 256
    overlap_ratio: float = 0.5
    sample_rate: float = 1000.0
    sample_rate_min: float = 100.0
    sample_rate_max: float = 10000.0
    denoising_algorithm: str = "butterworth"
    ts_feature_count: int = 24
    denoise_params: dict[str, Any] = field(
        default_factory=lambda: {
            "order": 4,
            "cutoff_ratio": 0.1,
            "btype": "low",
        }
    )


@dataclass
class TextProcessorConfig:
    bge_embedding_dim: int = 512
    bge_model_name: str = "BAAI/bge-small-zh-v1.5"
    max_text_length: int = 512
    clean_special_chars: bool = True
    normalize_whitespace: bool = True
    vector_db_type: str = "faiss"


@dataclass
class ToolStateProcessorConfig:
    tool_state_dim: int = 32
    encoding_method: str = "one_hot"
    anomaly_detection_method: str = "iqr"
    anomaly_threshold: float = 3.0
    state_fields: list[str] = field(
        default_factory=lambda: [
            "wear_level",
            "cutting_time",
            "tool_life_remaining",
            "spindle_load",
            "temperature",
            "vibration_amplitude",
            "cutting_force_x",
            "cutting_force_y",
            "cutting_force_z",
        ]
    )


@dataclass
class GCodeProcessorConfig:
    gcode_embedding_dim: int = 256
    max_instructions_per_segment: int = 500
    segment_by_operation: bool = True
    supported_controllers: list[str] = field(
        default_factory=lambda: [
            "fanuc",
            "siemens",
            "heidenhain",
        ]
    )


@dataclass
class BatchConfig:
    image_inference: int = 32
    image_training: int = 16
    time_series_inference: int = 128
    time_series_training: int = 64
    num_worker_processes: int = 4
    prefetch_factor: int = 4
    cache_memory_limit: str = "4GB"
    pin_memory: bool = True


@dataclass
class FusionConfig:
    fusion_method: str = "cross_modal_attention"
    target_dim: int = 512
    attention_heads: int = 8
    dropout: float = 0.1
    modality_weights: dict[str, float] = field(
        default_factory=lambda: {
            "image": 0.25,
            "time_series": 0.25,
            "text": 0.20,
            "tool_state": 0.15,
            "gcode": 0.15,
        }
    )


@dataclass
class MonitoringConfig:
    enabled: bool = True
    log_interval_seconds: int = 60
    alert_threshold_latency_ms: float = 100.0
    alert_threshold_memory_pct: float = 85.0
    metrics_export: bool = True
    metrics_export_path: str = "logs/pipeline_metrics"


@dataclass
class PipelineConfig:
    image: ImageProcessorConfig = field(default_factory=ImageProcessorConfig)
    time_series: TimeSeriesProcessorConfig = field(default_factory=TimeSeriesProcessorConfig)
    text: TextProcessorConfig = field(default_factory=TextProcessorConfig)
    tool_state: ToolStateProcessorConfig = field(default_factory=ToolStateProcessorConfig)
    gcode: GCodeProcessorConfig = field(default_factory=GCodeProcessorConfig)
    batch: BatchConfig = field(default_factory=BatchConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    memory_limit: str = "8GB"
    performance_test_env_spec: str = "CPU: 8-core, RAM: 16GB, GPU: NVIDIA T4"
    test_data_path: str = "tests/data"
    enable_async: bool = True
    enable_cache: bool = True
    cache_ttl_seconds: int = 3600
    log_level: str = "INFO"

    def to_dict(self) -> dict[str, Any]:
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, (int, float, str, bool, list, dict, tuple)):
                result[key] = value
            elif hasattr(value, "__dataclass_fields__"):
                result[key] = {k: v for k, v in value.__dict__.items() if not k.startswith("_")}
        return result


def _parse_memory_to_bytes(mem_str: str) -> int:
    """解析内存字符串为字节数"""
    mem_str = mem_str.strip().upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
    for unit, mult in multipliers.items():
        if mem_str.endswith(unit):
            return int(float(mem_str.replace(unit, "")) * mult)
    if mem_str.isdigit():
        return int(mem_str)
    return 8 * 1024**3


def _env_override(config_dict: dict[str, Any]) -> dict[str, Any]:
    """环境变量注入覆盖配置"""
    env_map = {
        "PIPELINE_IMAGE_SIZE": ("image", "image_size", int),
        "PIPELINE_CNN_FEATURE_DIM": ("image", "cnn_feature_dim", int),
        "PIPELINE_WINDOW_SIZE": ("time_series", "window_size", int),
        "PIPELINE_SAMPLE_RATE": ("time_series", "sample_rate", float),
        "PIPELINE_BGE_EMBEDDING_DIM": ("text", "bge_embedding_dim", int),
        "PIPELINE_TOOL_STATE_DIM": ("tool_state", "tool_state_dim", int),
        "PIPELINE_GCODE_EMBEDDING_DIM": ("gcode", "gcode_embedding_dim", int),
        "PIPELINE_NUM_WORKERS": ("batch", "num_worker_processes", int),
        "PIPELINE_CACHE_LIMIT": ("batch", "cache_memory_limit", str),
        "PIPELINE_MEMORY_LIMIT": ("memory_limit", None, str),
        "PIPELINE_LOG_LEVEL": ("log_level", None, str),
    }

    for env_var, (section, key, cast_type) in env_map.items():
        env_value = os.environ.get(env_var)
        if env_value is not None:
            try:
                if key is None:
                    config_dict[section] = cast_type(env_value)
                elif section in config_dict:
                    config_dict[section][key] = cast_type(env_value)
            except (ValueError, TypeError) as cast_err:
                # 单个环境变量类型转换失败时不影响其他配置加载，记录以便排查
                logger.debug(
                    "Failed to cast env %s value %r to %s: %s",
                    env_var,
                    env_value,
                    getattr(cast_type, "__name__", cast_type),
                    cast_err,
                    exc_info=True,
                )

    return config_dict


def load_config(config_path: str) -> PipelineConfig:
    """从 YAML 文件加载配置，支持环境变量覆盖"""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {config_path}\n请确保配置文件路径正确，或调用 get_default_config() 获取默认配置。"
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    raw = _env_override(raw)

    sections = {
        "image": ImageProcessorConfig,
        "time_series": TimeSeriesProcessorConfig,
        "text": TextProcessorConfig,
        "tool_state": ToolStateProcessorConfig,
        "gcode": GCodeProcessorConfig,
        "batch": BatchConfig,
        "fusion": FusionConfig,
        "monitoring": MonitoringConfig,
    }

    kwargs = {}
    for name, cls in sections.items():
        if name in raw:
            kwargs[name] = cls(**raw[name])
        else:
            kwargs[name] = cls()

    for key in [
        "memory_limit",
        "performance_test_env_spec",
        "test_data_path",
        "enable_async",
        "enable_cache",
        "cache_ttl_seconds",
        "log_level",
    ]:
        if key in raw:
            kwargs[key] = raw[key]

    return PipelineConfig(**kwargs)


def get_default_config() -> PipelineConfig:
    """获取默认配置"""
    return PipelineConfig()


def save_config(config: PipelineConfig, config_path: str) -> None:
    """保存配置到 YAML 文件"""
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False, allow_unicode=True, indent=2)
