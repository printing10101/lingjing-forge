"""LNN 核心数据模型与类型定义。

提供推理管线所需的枚举、数据类和类型别名。
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Any
import numpy as np


class EngineType(str, Enum):
    """推理引擎类型枚举。"""

    LNN = "LNN"
    LLM = "LLM"
    HYBRID = "Hybrid"
    RULE = "Rule"


class ModelType(str, Enum):
    """LNN 模型架构枚举。"""

    CFC = "CFC"
    LTC = "LTC"
    HYBRID_LNN = "HybridLNN"


class DataType(str, Enum):
    """数据类型枚举。"""

    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"
    SEMI_STRUCTURED = "semi_structured"
    MULTIMODAL = "multimodal"


class TaskCategory(str, Enum):
    """任务类别枚举，用于路由和模型选择。"""

    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    TIME_SERIES = "time_series"
    NLP = "nlp"
    VISION = "vision"
    LOGIC_REASONING = "logic_reasoning"
    RULE_BASED = "rule_based"


@dataclass
class TaskInput:
    """标准化任务输入，封装推理管线所需的全部信息。

    Attributes:
        task_description: 任务描述。
        input_data: 原始输入数据。
        context: 可选上下文信息。
        task_category: 任务类别（None 时自动检测）。
        data_type: 数据类型（None 时自动检测）。
        precision_requirement: 最低精度要求 (0.0-1.0)。
        time_sensitivity: 时间敏感度 (0.0-1.0)。
        max_latency_ms: 最大可接受延迟（毫秒）。
        metadata: 附加元数据。
    """

    task_description: str
    input_data: Any
    context: dict[str, Any] | None = None
    task_category: TaskCategory | None = None
    data_type: DataType | None = None
    precision_requirement: float = 0.9
    time_sensitivity: float = 0.5
    max_latency_ms: int = 1000
    metadata: dict[str, Any] | None = None


@dataclass
class RoutingDecision:
    """任务路由决策结果。

    Attributes:
        selected_engine: 选中的引擎类型。
        selected_model: 选中的具体模型名称。
        confidence: 路由决策置信度 (0.0-1.0)。
        reasoning: 决策原因说明。
        decision_factors: 各因素的评分。
        alternatives: 备选方案列表。
        timestamp: 决策时间戳。
    """

    selected_engine: EngineType
    selected_model: str | None = None
    confidence: float = 0.0
    reasoning: str = ""
    decision_factors: dict[str, float] | None = None
    alternatives: list[dict[str, Any]] | None = None
    timestamp: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典（枚举值转为字符串）。"""
        return {
            "selected_engine": self.selected_engine.value,
            "selected_model": self.selected_model,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "decision_factors": self.decision_factors or {},
            "alternatives": self.alternatives or [],
            "timestamp": self.timestamp,
        }


@dataclass
class InferenceResult:
    """推理引擎输出结果，封装预测值及元数据。

    Attributes:
        prediction: 模型预测结果。
        confidence: 置信度 (0.0-1.0)。
        engine_used: 使用的引擎。
        model_used: 使用的模型名称。
        processing_time_ms: 处理耗时（毫秒）。
        metadata: 附加元数据。
        evidence: 支持证据列表。
        uncertainty: 不确定性指标（熵、方差等）。
    """

    prediction: Any
    confidence: float = 0.0
    engine_used: EngineType | None = None
    model_used: str | None = None
    processing_time_ms: float = 0.0
    metadata: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] | None = None
    uncertainty: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "prediction": self.prediction,
            "confidence": self.confidence,
            "engine_used": self.engine_used.value if self.engine_used else None,
            "model_used": self.model_used,
            "processing_time_ms": self.processing_time_ms,
            "metadata": self.metadata or {},
            "evidence": self.evidence or [],
            "uncertainty": self.uncertainty or {},
        }


@dataclass
class FusionResult:
    """多引擎推理结果融合输出（Dempster-Shafer 融合层产物）。

    Attributes:
        final_prediction: 融合后的最终预测。
        confidence: 聚合置信度 (0.0-1.0)。
        contributing_engines: 参与融合的引擎列表。
        fusion_method: 融合方法名称。
        reasoning_path: 融合推理步骤追踪。
        explainability_report: 可读的融合解释报告。
        quality_metrics: 质量评估指标。
    """

    final_prediction: Any
    confidence: float = 0.0
    contributing_engines: list[dict[str, Any]] = field(default_factory=list)
    fusion_method: str = "dempster_shafer"
    reasoning_path: list[str] | None = None
    explainability_report: str | None = None
    quality_metrics: dict[str, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为可序列化字典。"""
        return {
            "final_prediction": self.final_prediction,
            "confidence": self.confidence,
            "contributing_engines": self.contributing_engines,
            "fusion_method": self.fusion_method,
            "reasoning_path": self.reasoning_path or [],
            "explainability_report": self.explainability_report,
            "quality_metrics": self.quality_metrics or {},
        }


@dataclass
class ModelConfig:
    """模型加载与运行配置。

    Attributes:
        model_type: 架构类型 (CFC, LTC, HybridLNN)。
        model_name: 模型名称。
        model_path: 模型权重文件路径（新建模型为 None）。
        hyperparameters: 模型超参数字典。
        device: 计算设备 ("cpu", "cuda")。
        batch_size: 默认推理批次大小。
        version: 语义版本号。
        metadata: 附加元数据（训练日期、数据集等）。
    """

    model_type: ModelType
    model_name: str
    model_path: str | None = None
    hyperparameters: dict[str, Any] | None = None
    device: str = "cpu"
    batch_size: int = 32
    version: str = "1.0.0"
    metadata: dict[str, Any] | None = None


@dataclass
class PreprocessingResult:
    """数据预处理管线输出。

    Attributes:
        features: 预处理后的特征矩阵 (n_samples, n_features)。
        feature_names: 特征列名列表。
        normalization_method: 归一化方法 ("z_score", "min_max")。
        outliers_detected: 检测到的异常值数量。
        missing_values_filled: 填充的缺失值数量。
        metadata: 附加元数据（均值、标准差、原始形状等）。
    """

    features: np.ndarray
    feature_names: list[str] | None = None
    normalization_method: str = "z_score"
    outliers_detected: int = 0
    missing_values_filled: int = 0
    metadata: dict[str, Any] | None = None
