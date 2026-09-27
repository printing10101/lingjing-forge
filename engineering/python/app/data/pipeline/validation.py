"""
数据验证模块

提供数据质量验证和质量检查功能，支持：
- 数据完整性检查
- 格式兼容性验证
- 特征维度一致性验证
- 异常值检测
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.data.pipeline.datatypes import (
    DataSourceType,
    ProcessedData,
    PipelineResult,
    DataQualityMetrics,
)
from app.data.pipeline.config import PipelineConfig

logger = logging.getLogger(__name__)


class DataValidator:
    """
    数据验证器

    对管道各阶段的数据进行验证，确保数据质量和一致性。
    """

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.validation_history: list[dict[str, Any]] = []

    def validate_completeness(self, data: ProcessedData) -> DataQualityMetrics:
        """
        验证数据完整性

        检查: 字段缺失、NaN值、空值、维度
        """
        metrics = DataQualityMetrics()

        processed = data.processed_data

        if processed is None:
            metrics.completeness = 0.0
            metrics.validation_errors.append("处理后数据为None")
            return metrics

        if processed.size == 0:
            metrics.completeness = 0.0
            metrics.validation_errors.append("处理后数据为空")
            return metrics

        total_elements = processed.size
        nan_count = int(np.sum(np.isnan(processed)))
        inf_count = int(np.sum(np.isinf(processed)))

        metrics.missing_ratio = nan_count / total_elements if total_elements > 0 else 0
        metrics.completeness = 1.0 - metrics.missing_ratio

        if nan_count > 0:
            metrics.validation_errors.append(f"检测到{nan_count}个NaN值")
        if inf_count > 0:
            metrics.validation_errors.append(f"检测到{inf_count}个Inf值")
            metrics.completeness = min(metrics.completeness, 0.5)

        self.validation_history.append(
            {
                "type": "completeness",
                "source_type": data.source_type.value,
                "completeness": metrics.completeness,
                "errors": metrics.validation_errors,
            }
        )

        return metrics

    def validate_dimension_consistency(
        self,
        data: ProcessedData,
        expected_dim: int,
    ) -> DataQualityMetrics:
        """
        验证特征维度一致性
        """
        metrics = DataQualityMetrics(
            feature_dim_expected=expected_dim,
            feature_dim_actual=data.feature_dim,
        )

        if data.feature_dim != expected_dim:
            metrics.validation_errors.append(f"特征维度不一致: 期望{expected_dim}, 实际{data.feature_dim}")
            metrics.consistency = 0.0

        self.validation_history.append(
            {
                "type": "dimension",
                "expected": expected_dim,
                "actual": data.feature_dim,
                "consistent": data.feature_dim == expected_dim,
            }
        )

        return metrics

    def validate_value_range(
        self,
        data: ProcessedData,
        expected_min: float = 0.0,
        expected_max: float = 1.0,
    ) -> DataQualityMetrics:
        """
        验证数据值范围
        """
        metrics = DataQualityMetrics(
            value_range=(float(np.min(data.processed_data)), float(np.max(data.processed_data))),
        )

        actual_min = np.min(data.processed_data)
        actual_max = np.max(data.processed_data)

        tolerance = 0.01
        if actual_min < expected_min - tolerance:
            metrics.validation_errors.append(f"值超出下限: {actual_min:.4f} < {expected_min}")
        if actual_max > expected_max + tolerance:
            metrics.validation_errors.append(f"值超出上限: {actual_max:.4f} > {expected_max}")

        return metrics

    def validate_pipeline_result(self, result: PipelineResult) -> bool:
        """
        验证管道最终结果
        """
        if result.fused_features is None:
            logger.error("管道结果: fused_features为None")
            return False

        if np.any(np.isnan(result.fused_features)):
            logger.error("管道结果: fused_features包含NaN")
            return False

        if np.any(np.isinf(result.fused_features)):
            logger.error("管道结果: fused_features包含Inf")
            return False

        for modality, metrics in result.quality_metrics.items():
            if not metrics.is_valid:
                logger.warning("管道结果: %s 质量验证失败", modality)

        return True


class QualityChecker:
    """数据质量检查器"""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.validator = DataValidator(config)

    def check_all(
        self,
        processed_data: dict[str, ProcessedData],
        expected_dims: dict[str, int],
    ) -> dict[str, DataQualityMetrics]:
        """
        对所有预处理数据进行质量检查

        Args:
            processed_data: {modality: ProcessedData} 字典
            expected_dims: {modality: expected_dim} 字典

        Returns:
            {modality: DataQualityMetrics} 质量指标字典
        """
        results = {}

        for modality, data in processed_data.items():
            metrics = self.validator.validate_completeness(data)

            if modality in expected_dims:
                dim_metrics = self.validator.validate_dimension_consistency(data, expected_dims[modality])
                metrics.feature_dim_expected = dim_metrics.feature_dim_expected
                metrics.feature_dim_actual = dim_metrics.feature_dim_actual
                metrics.consistency = dim_metrics.consistency
                metrics.validation_errors.extend(dim_metrics.validation_errors)

            results[modality] = metrics

        return results

    def check_edge_cases(
        self,
        data: Any,
        source_type: DataSourceType,
    ) -> tuple[bool, str]:
        """
        边缘情况检查

        Args:
            data: 待检查数据
            source_type: 数据源类型

        Returns:
            (is_valid, error_message)
        """
        if data is None:
            return False, f"{source_type.value}: 数据为None"

        if source_type == DataSourceType.IMAGE:
            if isinstance(data, np.ndarray):
                if data.ndim < 2 or data.ndim > 4:
                    return False, f"图像维度异常: ndim={data.ndim}"
                if data.size == 0:
                    return False, "图像数据为空"
                if np.all(data == 0):
                    return False, "图像全为0"
                if np.all(data == data.flat[0]):
                    return False, "图像全为相同值"

        elif source_type == DataSourceType.TIME_SERIES:
            if isinstance(data, np.ndarray):
                if data.size == 0:
                    return False, "时序数据为空"
                if data.size < 2:
                    return False, "时序数据太短"
                if np.all(data == data.flat[0]):
                    return False, "时序数据为常数"

        elif source_type == DataSourceType.TEXT:
            if isinstance(data, str):
                if len(data.strip()) == 0:
                    return False, "文本为空"
                if len(data) > 1000000:
                    return False, "文本过长"

        elif source_type == DataSourceType.GCODE:
            if isinstance(data, str):
                if len(data.strip()) == 0:
                    return False, "G代码为空"
                if not any(c in data.upper() for c in ("G", "M", "T", "S", "F")):
                    return False, "G代码未包含有效指令"

        return True, ""
