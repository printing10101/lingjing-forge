"""Common validation and data models shared across services."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WearDataPoint:
    """Single point on a tool wear curve.

    Compatible with both services/tool_wear_predictor.py and models/validation.py usage.
    """

    time: float = 0.0
    wear: float = 0.0
    wear_rate: float = 0.0
    confidence: float = 1.0
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "wear": self.wear,
            "wear_rate": self.wear_rate,
            "confidence": self.confidence,
        }


@dataclass
class WearCurve:
    """Full tool wear prediction curve."""

    data_points: list[WearDataPoint] = field(default_factory=list)
    material: str = ""
    tool_type: str = ""
    cutting_speed: float = 0.0
    feed_rate: float = 0.0
    depth_of_cut: float = 0.0
    total_time: float = 0.0
    max_wear: float = 0.0
    confidence: float = 0.0
    model_info: dict[str, Any] | None = None

    def add_point(self, point: WearDataPoint):
        self.data_points.append(point)
        if point.time > self.total_time:
            self.total_time = point.time
        if point.wear > self.max_wear:
            self.max_wear = point.wear

    def to_dict(self) -> dict[str, Any]:
        return {
            "data_points": [p.to_dict() for p in self.data_points],
            "material": self.material,
            "tool_type": self.tool_type,
            "cutting_speed": self.cutting_speed,
            "feed_rate": self.feed_rate,
            "depth_of_cut": self.depth_of_cut,
            "total_time": self.total_time,
            "max_wear": self.max_wear,
            "confidence": self.confidence,
        }


class WearPhase:
    INITIAL = "initial"
    STEADY = "steady"
    ACCELERATED = "accelerated"


class UrgencyLevel:
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    IMMINENT = "imminent"


@dataclass
class AdjustmentSuggestionItem:
    parameter: str
    current_value: float
    suggested_value: float
    change_percent: float
    reason: str
    confidence: float = 0.8
    priority: str = "medium"


@dataclass
class AdjustmentSuggestion:
    suggestions: list[AdjustmentSuggestionItem] = field(default_factory=list)
    summary: str = ""
    expected_improvement: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestions": [
                {
                    "parameter": s.parameter,
                    "current_value": s.current_value,
                    "suggested_value": s.suggested_value,
                    "change_percent": s.change_percent,
                    "reason": s.reason,
                    "confidence": s.confidence,
                    "priority": s.priority,
                }
                for s in self.suggestions
            ],
            "summary": self.summary,
            "expected_improvement": self.expected_improvement,
        }


@dataclass
class ValidationResult:
    """Result of a validation check."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def merge(self, other: ValidationResult) -> ValidationResult:
        return ValidationResult(
            is_valid=self.is_valid and other.is_valid,
            errors=self.errors + other.errors,
            warnings=self.warnings + other.warnings,
        )


@dataclass
class PredictionRequest:
    """Common prediction request model for validation."""

    material: str = ""
    tool_type: str = ""
    cutting_speed: float = 0.0
    feed_rate: float = 0.0
    depth_of_cut: float = 0.0
    current_wear: float | None = None
    prediction_horizon: float = 60.0

    def validate(self) -> ValidationResult:
        errors = []
        warnings = []
        if self.cutting_speed <= 0:
            errors.append("切削速度必须大于0")
        if self.feed_rate <= 0:
            errors.append("进给量必须大于0")
        if self.depth_of_cut <= 0:
            errors.append("切削深度必须大于0")
        if self.prediction_horizon <= 0:
            errors.append("预测时长必须大于0")
        if self.prediction_horizon > 3600:
            warnings.append("预测时长超过1小时，精度可能下降")
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )


@dataclass
class TrainingRequest:
    """Common training request model for validation."""

    model_name: str = ""
    model_type: str = "CFC"
    dataset_path: str = ""
    epochs: int = 100
    batch_size: int = 32
    learning_rate: float = 0.001
    validation_split: float = 0.2
    device: str = "auto"

    def validate(self) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        if not self.model_name:
            errors.append("模型名称不能为空")
        if not self.dataset_path:
            errors.append("数据集路径不能为空")
        if self.epochs <= 0 or self.epochs > 10000:
            errors.append("训练轮数必须在1-10000之间")
        if self.batch_size <= 0 or self.batch_size > 512:
            errors.append("批量大小必须在1-512之间")
        if self.learning_rate <= 0 or self.learning_rate > 1.0:
            errors.append("学习率必须在0-1之间")
        if not 0.0 <= self.validation_split <= 0.5:
            errors.append("验证集比例必须在0.0-0.5之间")
        if self.model_type not in ("CFC", "LTC", "HYBRID", "cnn"):
            errors.append(f"不支持的模型类型: {self.model_type}")
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )
