"""DXF工程图解析与处理系统。

提供完整的DXF文件处理流水线，包括：
- dxf_parser: DXF文件解析，提取几何实体和尺寸标注
- feature_extractor: 基于规则的加工特征识别算法
- dxf_to_model: 基于CadQuery的2D→3D模型转换
- pipeline: 端到端流水线，集成DXF解析、特征提取、模型转换、工艺规划和G代码生成
- api: RESTful API端点

模块依赖关系：
    dxf_parser ───────┐
    feature_extractor ─┤── pipeline.py ── ProcessPlanningPipeline ── G代码
    dxf_to_model ─────┘
         │
         └── cadquery (外部库)
"""

from __future__ import annotations

from app.dxf.exceptions import DxfError, DxfParseError, DxfFeatureError, DxfModelError
from app.dxf.dxf_parser import (
    DxfParser,
    DxfLine,
    DxfCircle,
    DxfArc,
    DxfText,
    DxfDimension,
    DxfParseResult,
)
from app.dxf.feature_extractor import (
    FeatureExtractor,
    HoleFeatureInfo,
    PlaneFeatureInfo,
    FeatureExtractionResult,
)
from app.dxf.dxf_to_model import (
    DxfToModelConverter,
    ModelConversionResult,
)
from app.dxf.pipeline import (
    DxfProcessPipeline,
    DxfPipelineResult,
    DxfPipelineStage,
)

__all__ = [
    "DxfError",
    "DxfParseError",
    "DxfFeatureError",
    "DxfModelError",
    "DxfParser",
    "DxfLine",
    "DxfCircle",
    "DxfArc",
    "DxfText",
    "DxfDimension",
    "DxfParseResult",
    "FeatureExtractor",
    "HoleFeatureInfo",
    "PlaneFeatureInfo",
    "FeatureExtractionResult",
    "DxfToModelConverter",
    "ModelConversionResult",
    "DxfProcessPipeline",
    "DxfPipelineResult",
    "DxfPipelineStage",
]

from app.dxf._pipeline_stages import (
    STAGES,
    StageKey,
    StageStatus,
    stage_name,
    stage_failure_is_fatal,
    should_abort_after,
    progress_of,
    summarize_pipeline,
)
