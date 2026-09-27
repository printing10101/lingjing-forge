"""3D重建几何精度验证模块。

核心导出：
- GeometricValidator: 几何精度验证引擎
- BenchmarkDataset: 基准测试数据集管理器
- MetricsResult: 指标计算结果容器
- ValidationReport: 验证报告数据模型
- 各项指标计算函数
"""

from __future__ import annotations

from app.validation.benchmark_dataset import BenchmarkDataset, PartMetadata
from app.validation.geometric_validator import GeometricValidator, ValidationReport
from app.validation.metrics import (
    DimensionResult,
    MetricsResult,
    TopologyEdge,
    compute_dimension_accuracy,
    compute_feature_iou,
    compute_feature_precision,
    compute_feature_recall,
    compute_tolerance_compliance,
    compute_topology_correctness,
)

__all__ = [
    "GeometricValidator",
    "ValidationReport",
    "BenchmarkDataset",
    "PartMetadata",
    "MetricsResult",
    "DimensionResult",
    "TopologyEdge",
    "compute_dimension_accuracy",
    "compute_feature_iou",
    "compute_feature_recall",
    "compute_feature_precision",
    "compute_topology_correctness",
    "compute_tolerance_compliance",
]
