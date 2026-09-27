"""Cutting Parameter Data Models.

Defines Pydantic models for cutting process parameters, validation results,
and LNN prediction outputs. Used by the LNN engine to generate and validate
machining parameter recommendations.

Key components:
    - ParameterSource: Enum for the source of parameters (LNN, LLM, Hybrid, Rule).
    - CuttingParameters: Validated cutting parameters with range checks.
    - ValidationResult: Validation outcome with issues and warnings.
    - LNNPredictionResult: LNN prediction with confidence and metadata.

Example:
    >>> params = CuttingParameters(
    ...     cutting_speed=150.0,
    ...     feed_rate=0.2,
    ...     depth_of_cut=2.0,
    ...     spindle_speed=1200.0,
    ...     material="45钢",
    ...     source=ParameterSource.LNN,
    ... )
    >>> params.cutting_speed
    150.0
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


class ParameterSource(str, Enum):
    """参数来源枚举"""

    LNN = "lnn"
    LLM = "llm"
    HYBRID = "hybrid"
    RULE = "rule"


class CuttingParameters(BaseModel):
    """切削参数数据类"""

    cutting_speed: float = Field(..., description="切削速度，单位m/min", ge=0)
    feed_rate: float = Field(..., description="进给量，单位mm/r", ge=0)
    depth_of_cut: float = Field(..., description="背吃刀量，单位mm", ge=0)
    spindle_speed: float = Field(..., description="主轴转速，单位r/min", ge=0)
    material: str = Field(..., description="加工材料", min_length=1)
    tool_type: Optional[str] = Field(None, description="刀具类型")
    confidence: float = Field(1.0, description="参数置信度", ge=0, le=1)
    source: ParameterSource = Field(..., description="参数来源")

    @field_validator("cutting_speed")
    @classmethod
    def validate_cutting_speed(cls, v):
        if not (50 <= v <= 500):
            raise ValueError(f"切削速度必须在[50, 500] m/min区间内，当前值: {v}")
        return v

    @field_validator("feed_rate")
    @classmethod
    def validate_feed_rate(cls, v):
        if not (0.05 <= v <= 1.0):
            raise ValueError(
                f"切削参数验证失败：进给量（feed_rate）必须在 [0.05, 1.0] mm/r 有效区间内，当前输入值: {v} mm/r。超出此范围可能导致模型预测不准确。请调整进给量参数至有效范围，或参考切削工艺手册确认合理参数。"  # noqa: E501
            )
        return v


class ValidationResult(BaseModel):
    """验证结果数据类"""

    is_valid: bool = Field(..., description="参数是否有效")
    issues: List[str] = Field(
        default_factory=list, description="问题列表，存储验证失败的具体原因"
    )
    warnings: List[str] = Field(
        default_factory=list, description="警告列表，存储需要注意的潜在问题"
    )


class LNNResult(BaseModel):
    """LNN预测结果数据类"""

    parameters: CuttingParameters = Field(..., description="切削参数对象")
    confidence: float = Field(..., description="预测置信度", ge=0, le=1)
