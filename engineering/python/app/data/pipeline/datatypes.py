"""
数据类型定义 - 混合架构数据管道系统

定义所有输入/输出数据类型、处理中间结果和管道元数据。
"""

from __future__ import annotations

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Union

import numpy as np


class DataSourceType(str, Enum):
    IMAGE = "image"
    TIME_SERIES = "time_series"
    TEXT = "text"
    TOOL_STATE = "tool_state"
    GCODE = "gcode"
    UNKNOWN = "unknown"


class PipelineStage(str, Enum):
    INGEST = "ingest"
    PREPROCESS = "preprocess"
    FEATURE_EXTRACT = "feature_extract"
    FUSION = "fusion"
    OUTPUT = "output"


@dataclass
class RawInput:
    """原始输入数据容器"""

    source_type: DataSourceType = DataSourceType.UNKNOWN
    data: Any = None
    source_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class ImageInput(RawInput):
    """三视图图像输入"""

    def __init__(
        self,
        data: np.ndarray,
        bit_depth: int = 8,
        channels: int = 3,
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(
            source_type=DataSourceType.IMAGE,
            data=data,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.bit_depth = bit_depth
        self.channels = channels
        self.source_type = DataSourceType.IMAGE


class TimeSeriesInput(RawInput):
    """传感器时序数据输入"""

    def __init__(
        self,
        data: np.ndarray,
        sample_rate: float = 1000.0,
        channels: int = 1,
        channel_names: list[str] | None = None,
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(
            source_type=DataSourceType.TIME_SERIES,
            data=data,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.sample_rate = sample_rate
        self.channels = channels
        self.channel_names = channel_names
        self.source_type = DataSourceType.TIME_SERIES


class TextInput(RawInput):
    """工艺知识文本输入"""

    def __init__(
        self,
        data: Union[str, dict[str, Any]],
        text_format: str = "json",
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(
            source_type=DataSourceType.TEXT,
            data=data,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.text_format = text_format
        self.source_type = DataSourceType.TEXT


class ToolStateInput(RawInput):
    """刀具状态输入"""

    def __init__(
        self,
        data: dict[str, Any],
        state_fields: list[str] | None = None,
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(
            source_type=DataSourceType.TOOL_STATE,
            data=data,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.state_fields = state_fields or []
        self.source_type = DataSourceType.TOOL_STATE


class GCodeInput(RawInput):
    """G代码输入"""

    def __init__(
        self,
        data: str,
        controller_type: str = "fanuc",
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(
            source_type=DataSourceType.GCODE,
            data=data,
            source_id=source_id,
            metadata=metadata or {},
        )
        self.controller_type = controller_type
        self.source_type = DataSourceType.GCODE


@dataclass
class ProcessedData:
    """预处理后的数据容器"""

    source_type: DataSourceType
    original_data: Any
    processed_data: np.ndarray
    features: np.ndarray | None = None
    feature_dim: int = 0
    processing_time_ms: float = 0.0
    quality_score: float = 1.0
    anomaly_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataQualityMetrics:
    """数据质量指标"""

    completeness: float = 1.0
    consistency: float = 1.0
    outlier_ratio: float = 0.0
    missing_ratio: float = 0.0
    feature_dim_expected: int | None = None
    feature_dim_actual: int | None = None
    value_range: tuple | None = None
    validation_errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """数据是否通过质量检查"""
        return self.completeness >= 0.999 and self.missing_ratio < 0.001 and len(self.validation_errors) == 0

    @property
    def dim_consistency(self) -> bool:
        """特征维度一致性"""
        if self.feature_dim_expected is None or self.feature_dim_actual is None:
            return True
        return self.feature_dim_expected == self.feature_dim_actual


@dataclass
class PipelineResult:
    """管道最终输出结果"""

    fused_features: np.ndarray
    individual_features: dict[str, np.ndarray]
    quality_metrics: dict[str, DataQualityMetrics]
    total_processing_time_ms: float
    stage_timings: dict[str, float]
    fusion_weights: dict[str, float]
    error_log: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return len(self.error_log) == 0
