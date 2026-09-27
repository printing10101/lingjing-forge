"""DXF端到端处理流水线。

将DXF解析、特征提取、3D模型转换、工艺规划和G代码生成
整合为单一自动化流水线。

流水线阶段（6步）：
1. DXF解析：调用DxfParser提取几何实体和尺寸标注
2. 特征提取：调用FeatureExtractor识别加工特征
3. 3D模型转换：调用DxfToModelConverter生成CadQuery模型
4. 数据组装：将特征转换为process_planning兼容格式
5. 工艺规划：调用ProcessPlanningPipeline
6. 结果验证：校验输出完整性和语法正确性

数据流:
    DXF文件 ──▶ DxfParser ──▶ FeatureExtractor ──▶ DxfToModelConverter
                                                          │
                                              ProcessPlanningPipeline
                                                          │
                                                     G代码输出
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.safe_errors import safe_error_message
from app.dxf._pipeline_stages import (
    StageKey,
    stage_name,
)
from app.dxf.exceptions import DxfError
from app.dxf.dxf_parser import DxfParser, DxfParseResult
from app.dxf.feature_extractor import (
    FeatureExtractor,
    FeatureExtractionResult,
)
from app.dxf.dxf_to_model import (
    DxfToModelConverter,
    ModelConversionResult,
)
from app.process_planning.pipeline import (
    ProcessPlanningPipeline,
    PipelineResult as ProcessPipelineResult,
)

logger = logging.getLogger(__name__)


def _record_stage_error(exc: BaseException, *, context: str, generic_message: str) -> tuple[str, str]:
    """统一记录阶段错误的安全包装器。

    修复：原实现将 ``str(e)`` 直接存储到 ``DxfPipelineStage.errors`` 中，
    而 ``DxfPipelineResult.to_dict()`` 又会被 ``/api/dxf/pipeline`` 端点
    原样返回给前端，从而将 ezdxf / cadquery 内部的异常消息、文件路径、
    状态码等细节暴露给未授权用户。

    新实现：
    1. 服务端日志保留完整堆栈（通过 ``safe_error_message`` 内的 logger.exception）。
    2. 返回给前端的 ``errors`` 列表仅含通用描述 + ``error_id``，供报障关联。
    """
    safe = safe_error_message(exc, context=context)
    return generic_message, safe.get("error_id", "")


@dataclass
class DxfPipelineStage:
    """DXF流水线单个阶段的执行记录。"""

    name: str
    status: str = "pending"
    duration_ms: float = 0.0
    input_summary: str = ""
    output_summary: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class DxfPipelineResult:
    """DXF流水线完整执行结果。"""

    success: bool = False
    stages: list[DxfPipelineStage] = field(default_factory=list)
    parse_result: DxfParseResult | None = None
    feature_result: FeatureExtractionResult | None = None
    model_result: ModelConversionResult | None = None
    process_result: ProcessPipelineResult | None = None
    total_duration_ms: float = 0.0
    summary: str = ""
    # 修复：新增 error_id 字段以便客户端报错时与服务端日志关联，
    # 避免原 summary 字段中直接拼接 str(e) 泄露内部异常详情。
    error_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "summary": self.summary,
            "stages": [s.to_dict() for s in self.stages],
        }
        if self.error_id:
            result["error_id"] = self.error_id
        if self.parse_result:
            result["parse"] = self.parse_result.to_dict()
        if self.feature_result:
            result["features"] = self.feature_result.to_dict()
        if self.model_result:
            result["model"] = {
                "length": self.model_result.length,
                "width": self.model_result.width,
                "height": self.model_result.height,
                "hole_count": self.model_result.hole_count,
            }
        if self.process_result:
            result["process"] = self.process_result.to_dict()
        return result


class DxfProcessPipeline:
    """DXF端到端处理流水线。

    将DXF工程图解析、特征提取、3D模型转换、工艺规划和G代码生成
    集成到统一的自动化流水线中。

    使用方式:
        pipeline = DxfProcessPipeline()
        result = pipeline.run("path/to/part.dxf", material="45#钢")
        if result.success and result.process_result:
            gcode = result.process_result.gcode_result.program_text

    错误处理策略：
    - DXF解析失败 → 立即终止，返回详细错误
    - 特征提取警告 → 继续执行，警告记入结果
    - 模型转换失败 → 继续工艺规划（可降级运行）
    - 工艺规划失败 → 终止，无法生成后续工序
    - G代码语法错误 → 仍输出代码，错误记入结果
    """

    def __init__(self) -> None:
        self._dxf_parser = DxfParser()
        self._feature_extractor = FeatureExtractor()
        self._model_converter = DxfToModelConverter()
        self._process_pipeline = ProcessPlanningPipeline()
        logger.info("DxfProcessPipeline初始化完成")

    def run(
        self,
        file_path: str | Path,
        material: str = "45#钢",
        part_type: str = "general",
        controller_type: str = "fanuc_0i",
        safe_z: float = 50.0,
        program_number: int = 1000,
    ) -> DxfPipelineResult:
        """执行完整的DXF端到端处理流水线。

        Args:
            file_path: DXF文件路径
            material: 零件材料名称
            part_type: 零件类型
            controller_type: 目标数控系统
            safe_z: 安全Z高度(mm)
            program_number: 数控程序号

        Returns:
            DxfPipelineResult: 完整的流水线执行结果
        """
        pipeline_start = time.time()
        result = DxfPipelineResult()
        stages: list[DxfPipelineStage] = []

        # Stage 1: DXF解析
        stage1_start = time.time()
        try:
            parse_result = self._dxf_parser.parse(file_path)
            status = "success" if parse_result.success else "failed"
            stage1 = DxfPipelineStage(
                name=stage_name(StageKey.PARSE),
                status=status,
                duration_ms=(time.time() - stage1_start) * 1000,
                input_summary=f"文件: {Path(file_path).name}",
                output_summary=(
                    f"版本: {parse_result.dxf_version}, "
                    f"实体: {parse_result.total_entities}个 "
                    f"(L:{len(parse_result.lines)} C:{len(parse_result.circles)} "
                    f"A:{len(parse_result.arcs)} T:{len(parse_result.texts)} "
                    f"D:{len(parse_result.dimensions)})"
                ),
                errors=parse_result.errors,
                warnings=parse_result.warnings,
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, DxfError) as e:
            # DXF解析涉及文件I/O和数据解析
            err_msg, error_id = _record_stage_error(
                e,
                context="dxf.pipeline.parse",
                generic_message="DXF解析阶段失败",
            )
            stage1 = DxfPipelineStage(
                name=stage_name(StageKey.PARSE),
                status="failed",
                duration_ms=(time.time() - stage1_start) * 1000,
                errors=[err_msg],
            )
            stages.append(stage1)
            result.stages = stages
            result.summary = "流水线在DXF解析阶段失败"
            result.error_id = error_id
            return result

        stages.append(stage1)
        result.parse_result = parse_result

        if not parse_result.success:
            result.stages = stages
            result.summary = f"DXF解析存在错误: {'; '.join(parse_result.errors)}"
            return result

        # Stage 2: 特征提取
        stage2_start = time.time()
        try:
            feature_result = self._feature_extractor.extract(parse_result)
            status = "success" if feature_result.success else "failed"
            stage2 = DxfPipelineStage(
                name=stage_name(StageKey.FEATURES),
                status=status,
                duration_ms=(time.time() - stage2_start) * 1000,
                input_summary=f"实体: {parse_result.total_entities}个",
                output_summary=(
                    f"孔: {feature_result.hole_count}个, "
                    f"平面: {feature_result.plane_count}个, "
                    f"外形: {feature_result.overall_length:.1f}x"
                    f"{feature_result.overall_width:.1f}x"
                    f"{feature_result.overall_height:.1f}mm"
                ),
                errors=feature_result.errors,
                warnings=feature_result.warnings,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as e:
            # 特征提取涉及几何计算和数据解析
            err_msg, error_id = _record_stage_error(
                e,
                context="dxf.pipeline.features",
                generic_message="特征提取阶段失败",
            )
            stage2 = DxfPipelineStage(
                name=stage_name(StageKey.FEATURES),
                status="failed",
                duration_ms=(time.time() - stage2_start) * 1000,
                errors=[err_msg],
            )
            stages.append(stage2)
            result.stages = stages
            result.summary = "流水线在特征提取阶段失败"
            result.error_id = error_id
            return result

        stages.append(stage2)
        result.feature_result = feature_result

        # Stage 3: 3D模型转换
        stage3_start = time.time()
        try:
            model_result = self._model_converter.convert(feature_result)
            status = "success" if model_result.success else "failed"
            stage3 = DxfPipelineStage(
                name=stage_name(StageKey.MODEL_CONVERT),
                status=status,
                duration_ms=(time.time() - stage3_start) * 1000,
                input_summary=(
                    f"外形: {feature_result.overall_length:.1f}x"
                    f"{feature_result.overall_width:.1f}x"
                    f"{feature_result.overall_height:.1f}mm, "
                    f"孔: {feature_result.hole_count}个"
                ),
                output_summary=(
                    f"模型: {model_result.length:.1f}x"
                    f"{model_result.width:.1f}x"
                    f"{model_result.height:.1f}mm, "
                    f"孔: {model_result.hole_count}个创建成功"
                ),
                errors=model_result.errors,
                warnings=model_result.warnings,
            )
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OverflowError) as e:
            # 3D模型转换涉及几何计算，与文档约定保持一致——失败仅降级继续
            err_msg, error_id = _record_stage_error(
                e,
                context="dxf.pipeline.model_convert",
                generic_message="3D模型转换失败",
            )
            logger.warning("3D模型转换失败，流水线将降级继续: error_id=%s", error_id)
            model_result = None
            stage3 = DxfPipelineStage(
                name=stage_name(StageKey.MODEL_CONVERT),
                status="failed",
                duration_ms=(time.time() - stage3_start) * 1000,
                errors=[err_msg],
                warnings=["模型转换失败，将跳过3D模型环节继续工艺规划"],
            )
            # 注意：降级失败不覆盖 result.error_id，仅记录日志供排查

        stages.append(stage3)
        result.model_result = model_result

        # Stage 4: 数据组装(特征process_planning格式)
        stage4_start = time.time()
        try:
            part_description = self._build_part_description(feature_result, material, part_type)
            stage4 = DxfPipelineStage(
                name=stage_name(StageKey.DATA_ASSEMBLY),
                status="success",
                duration_ms=(time.time() - stage4_start) * 1000,
                output_summary=(
                    f"孔数据: {len(part_description.get('holes', []))}条, 材料: {material}, 类型: {part_type}"
                ),
            )
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            # 数据组装涉及字典访问和字符串格式化
            err_msg, error_id = _record_stage_error(
                e,
                context="dxf.pipeline.build_part_description",
                generic_message="数据组装阶段失败",
            )
            stage4 = DxfPipelineStage(
                name=stage_name(StageKey.DATA_ASSEMBLY),
                status="failed",
                duration_ms=(time.time() - stage4_start) * 1000,
                errors=[err_msg],
            )
            stages.append(stage4)
            result.stages = stages
            result.summary = "流水线在数据组装阶段失败"
            result.error_id = error_id
            return result

        stages.append(stage4)

        # Stage 5: 工艺规划 + G代码生成
        stage5_start = time.time()
        try:
            process_result = self._process_pipeline.run(
                part_description=part_description,
                controller_type=controller_type,
                safe_z=safe_z,
                program_number=program_number,
            )

            status = "success" if process_result.success else "completed_with_errors"
            output_summary_parts = []
            if process_result.operation_plan:
                op = process_result.operation_plan
                output_summary_parts.append(f"工序: {len(op.operations)}个, 工时: {op.estimated_time_min:.1f}min")
            if process_result.gcode_result:
                gc = process_result.gcode_result
                output_summary_parts.append(
                    f"G代码: {gc.total_lines}行, 刀具: {gc.tool_count}把, 周期: {gc.estimated_cycle_time_min:.1f}min"
                )

            stage5 = DxfPipelineStage(
                name="工艺规划与G代码生成",
                status=status,
                duration_ms=(time.time() - stage5_start) * 1000,
                input_summary=f"孔: {feature_result.hole_count}个, 材料: {material}",
                output_summary=" | ".join(output_summary_parts),
                errors=[e for s in process_result.stages for e in s.errors],
                warnings=[w for s in process_result.stages for w in s.warnings],
            )
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RuntimeError, TimeoutError) as e:
            # 工艺规划涉及流程控制和文件I/O
            err_msg, error_id = _record_stage_error(
                e,
                context="dxf.pipeline.process_planning",
                generic_message="工艺规划阶段失败",
            )
            stage5 = DxfPipelineStage(
                name="工艺规划与G代码生成",
                status="failed",
                duration_ms=(time.time() - stage5_start) * 1000,
                errors=[err_msg],
            )
            stages.append(stage5)
            result.stages = stages
            result.summary = "流水线在工艺规划阶段失败"
            result.error_id = error_id
            return result

        stages.append(stage5)
        result.process_result = process_result

        # 汇总
        result.stages = stages
        result.success = all(s.status in ("success", "completed_with_errors") for s in stages)
        result.total_duration_ms = (time.time() - pipeline_start) * 1000

        summary_parts = [
            f"DXF流水线{'成功' if result.success else '部分失败'} ({result.total_duration_ms:.0f}ms)",
            f"文件: {Path(file_path).name}",
            f"特征: {feature_result.hole_count}孔/{feature_result.plane_count}面, "
            f"外形{feature_result.overall_length:.0f}x"
            f"{feature_result.overall_width:.0f}x"
            f"{feature_result.overall_height:.0f}mm",
        ]
        if process_result and process_result.gcode_result:
            summary_parts.append(f"G代码: {process_result.gcode_result.total_lines}行")
        result.summary = " | ".join(summary_parts)

        logger.info("DXF流水线完成: %s", result.summary)
        return result

    def _build_part_description(
        self,
        feature_result: FeatureExtractionResult,
        material: str,
        part_type: str,
    ) -> dict[str, Any]:
        """将特征提取结果组装为ProcessPlanningPipeline兼容的格式。

        Args:
            feature_result: 特征提取结果
            material: 材料名称
            part_type: 零件类型

        Returns:
            兼容ProcessPlanningPipeline.run()的part_description字典
        """
        holes_data = []
        for hole in feature_result.holes:
            holes_data.append(
                {
                    "id": hole.hole_id,
                    "type": hole.hole_type,
                    "position": [hole.center_x, hole.center_y, 0.0],
                    "diameter": hole.diameter,
                    "depth": hole.depth,
                    "tolerance_grade": hole.tolerance_grade,
                    "surface": hole.surface,
                }
            )

        part_description: dict[str, Any] = {
            "material": material,
            "part_type": part_type,
            "holes": holes_data,
            "features": holes_data,
            "overall_dimensions": {
                "length": feature_result.overall_length,
                "width": feature_result.overall_width,
                "height": feature_result.overall_height,
            },
            "plane_features": [p.to_dict() for p in feature_result.planes],
        }

        return part_description


def run_dxf_pipeline(
    file_path: str,
    material: str = "45#钢",
    controller: str = "fanuc_0i",
) -> DxfPipelineResult:
    """便捷函数：一键执行DXF到G代码的完整转换。

    Args:
        file_path: DXF文件路径
        material: 材料名称
        controller: 目标数控系统

    Returns:
        DxfPipelineResult: 包含解析、特征、模型和G代码的完整结果
    """
    pipeline = DxfProcessPipeline()
    return pipeline.run(
        file_path=file_path,
        material=material,
        controller_type=controller,
    )
