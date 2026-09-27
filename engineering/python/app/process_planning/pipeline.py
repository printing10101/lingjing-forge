"""端到端工艺规划流水线。

将孔特征识别、知识库匹配、工序规划、G代码生成整合为单一自动化流水线。
实现从零件参数输入到可执行G代码输出的全流程自动化处理。

流水线阶段（6步）：
1. 输入验证：校验零件描述数据的完整性和有效性
2. 孔特征识别：调用HoleFeatureRecognizer
3. 知识库查询：调用ToolParamMatcher匹配刀具和参数
4. 工艺规划：调用OperationSequencer生成工序序列
5. G代码生成：调用GCodeGenerator输出数控程序
6. 结果验证：校验输出完整性和语法正确性

质量标准：
- 端到端流程无人工干预即可完成
- 输出的G代码可直接用于实际数控加工
- 特征识别准确率 ≥ 99%
- G代码符合目标数控系统语法规范

本模块为门面：实现已拆分至 _stages / _stages_mixin。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.process_planning.hole_recognizer import HoleFeatureRecognizer
from app.process_planning.tool_param_matcher import ToolParamMatcher, HoleProcessPlan
from app.process_planning.operation_sequencer import OperationSequencer
from app.process_planning.gcode_generator import GCodeGenerator
from app.data.process_data_manager import ProcessPlanningDataManager, DataLoadError, QueryError
from app.process_planning._stages import (  # noqa: F401
    PipelineResult,
    PipelineStage,
)
from app.process_planning._stages_mixin import _StagesMixin

logger = logging.getLogger(__name__)


class ProcessPlanningPipeline(_StagesMixin):
    """端到端工艺规划流水线。

    将孔特征识别、知识库查询、工序规划和G代码生成集成到统一的
    自动化流水线中，实现从零件参数输入到可执行G代码输出的全流程。

    使用方式:
        pipeline = ProcessPlanningPipeline()
        result = pipeline.run(part_description)
        if result.success:
            gcode = result.gcode_result.program_text

    错误处理策略：
    - 输入验证失败 → 立即终止，返回详细错误
    - 孔识别警告 → 继续执行，警告记入结果
    - 知识库查询失败 → 使用默认参数继续
    - 工序规划失败 → 终止，无法生成后续工序
    - G代码语法错误 → 仍输出代码，错误记入结果
    """

    def __init__(self) -> None:
        """初始化流水线的所有子模块。

        初始化顺序：
        1. 加载工艺知识库（materials/tools/cutting_params/rules）
        2. 创建孔特征识别器
        3. 创建刀具参数匹配器（注入知识库）
        4. 创建工序排序器
        5. 创建G代码生成器
        """
        try:
            self._data_manager: Any = ProcessPlanningDataManager()
            self._data_valid = True
        except (DataLoadError, Exception):
            self._data_manager = None
            self._data_valid = False

        self._hole_recognizer = HoleFeatureRecognizer()
        self._tool_matcher = ToolParamMatcher(self._data_manager)
        self._operation_sequencer = OperationSequencer()
        self._gcode_generator = GCodeGenerator()

    def run(
        self,
        part_description: dict[str, Any],
        controller_type: str = "fanuc_0i",
        safe_z: float = 50.0,
        program_number: int = 1000,
    ) -> PipelineResult:
        """执行完整的端到端工艺规划流水线。

        Args:
            part_description: 零件描述数据，必须至少包含:
                - holes (list): 孔列表 [{id, type, position, diameter, depth}, ...]
                - material (str): 材料名称，如 '45#钢'
                - part_type (str, optional): 零件类型，默认'general'
            controller_type: 目标数控系统类型
            safe_z: 安全Z高度 (mm)
            program_number: 数控程序号

        Returns:
            PipelineResult: 完整的流水线执行结果

        输入验证要求：
        - part_description 不能为 None 或空
        - 必须包含 material 字段
        - holes 字段应为列表（可以为空）
        """
        pipeline_start = time.time()

        result = PipelineResult()
        stages: list[PipelineStage] = []

        # Stage 1: 输入验证
        stage1 = self._validate_input(part_description)
        stages.append(stage1)
        if stage1.status == "failed":
            result.stages = stages
            result.summary = f"流水线在输入验证阶段失败: {'; '.join(stage1.errors)}"
            return result

        material_name = part_description.get("material", "45#钢")
        part_type = part_description.get("part_type", "general")

        # Stage 2: 孔特征识别
        stage2_start = time.time()
        hole_result = self._hole_recognizer.recognize_holes(part_description)

        stage2 = PipelineStage(
            name="孔特征识别",
            status="success" if not hole_result.errors else "failed",
            duration_ms=(time.time() - stage2_start) * 1000,
            input_summary=f"零件材料: {material_name}",
            output_summary=(f"识别孔数: {hole_result.total_count}, 类型分布: {hole_result.type_summary}"),
            errors=hole_result.errors,
            warnings=hole_result.warnings,
        )

        # 孔识别失败 终止流水线
        if hole_result.errors:
            stage2.status = "failed"
            stages.extend([stage1, stage2])
            result.hole_recognition = hole_result
            result.stages = stages
            result.summary = f"流水线在孔识别阶段失败: {'; '.join(hole_result.errors)}"
            return result

        stages.append(stage2)
        result.hole_recognition = hole_result

        # Stage 3: 知识库查询 (材料+刀具+参数匹配)
        stage3_start = time.time()
        process_plans: list[HoleProcessPlan] = []

        # 查询材料信息
        material_info = self._tool_matcher.get_material_info(material_name)
        if not material_info:
            stage3 = PipelineStage(
                name="知识库查询",
                status="failed",
                duration_ms=(time.time() - stage3_start) * 1000,
                input_summary=f"材料查询: '{material_name}'",
                errors=[f"材料 '{material_name}' 在知识库中不存在"],
            )
            stages.extend([stage1, stage2, stage3])
            result.hole_recognition = hole_result
            result.stages = stages
            result.summary = f"流水线在知识库查询阶段失败: 材料 '{material_name}' 未找到"
            return result

        material_id = material_info.id
        material_category = material_info.category

        # 为每个孔匹配刀具和切削参数
        unmatched_holes = 0
        for hole in hole_result.holes:
            try:
                plan = self._tool_matcher.plan_for_hole(
                    material_id=material_id,
                    material_category=material_category,
                    hole_diameter=hole.diameter,
                    hole_type=hole.type,
                    tolerance_grade=hole.tolerance_grade,
                )
                plan.hole_id = hole.hole_id
                process_plans.append(plan)
            except QueryError as e:
                unmatched_holes += 1
                stage2.warnings.append(f"孔{hole.hole_id}刀具匹配失败: {e}")
                # 使用默认参数创建方案
                process_plans.append(
                    HoleProcessPlan(
                        hole_id=hole.hole_id,
                        hole_type=hole.type,
                        operations=["钻中心孔", "钻孔"],
                        tools=[],
                        estimated_time_min=2.0,
                    )
                )

        stage3 = PipelineStage(
            name="知识库查询",
            status="success",
            duration_ms=(time.time() - stage3_start) * 1000,
            input_summary=f"材料: {material_id}({material_category}), 孔数: {hole_result.total_count}",
            output_summary=(
                f"匹配方案: {len(process_plans)}个, "
                f"刀具总数: {sum(len(p.tools) for p in process_plans)}, "
                f"匹配失败: {unmatched_holes}"
            ),
            warnings=[f"{unmatched_holes}个孔使用默认刀具"] if unmatched_holes else [],
        )
        stages.append(stage3)
        result.process_plans = process_plans

        # Stage 4: 工序规划
        stage4_start = time.time()

        features = self._build_features(
            hole_result,
            process_plans,
            part_description,
        )

        if not features:
            stage4 = PipelineStage(
                name="工序规划",
                status="failed",
                duration_ms=(time.time() - stage4_start) * 1000,
                errors=["无法构建加工特征列表——无有效特征"],
            )
            stages.append(stage4)
            result.stages = stages
            result.summary = "流水线在工序规划阶段失败: 无法构建特征列表"
            return result

        operation_plan = self._operation_sequencer.plan_operations(
            features=features,
            material=material_name,
            part_type=part_type,
        )
        # 匹配刀具共识直径随 OperationPlan 序列化导出（→ 阶段 6 report.json
        # → 阶段 7 体素仿真），替代"仿真刀具直径只能用配置默认值"的旧口径
        operation_plan.tool_diameter_mm = self._consensus_tool_diameter(process_plans)

        stage4 = PipelineStage(
            name="工序规划",
            status="success",
            duration_ms=(time.time() - stage4_start) * 1000,
            input_summary=f"特征数: {len(features)}, 零件类型: {part_type}",
            output_summary=(
                f"规划工序: {len(operation_plan.operations)}个, "
                f"装夹方案: {len(operation_plan.setups)}个, "
                f"预估总工时: {operation_plan.estimated_time_min}min"
            ),
        )
        stages.append(stage4)
        result.operation_plan = operation_plan

        # Stage 4.5: 仿真验证
        # 传 KB 解析出的 material_id（如 steel_45 / al_6061）而非原始输入：
        # 仿真侧 Kienzle 系数别名表按库 ID 覆盖，原始输入（"steel"、
        # "45#钢" 等任意形态）不再依赖仿真层逐个认识。
        simulation_result = self._run_simulation(
            material=material_id,
            operation_plan=operation_plan,
        )

        stage4_5 = PipelineStage(
            name="仿真验证",
            status=simulation_result.get("status", "failed"),
            duration_ms=simulation_result.get("duration_ms", 0),
            input_summary=f"材料: {material_name}, 工序数: {len(operation_plan.operations)}",
            output_summary=(
                f"仿真评分: {simulation_result.get('score', 0):.1f}, "
                f"推荐级别: {simulation_result.get('recommendation', 'unknown')}"
            ),
            errors=[str(simulation_result.get("error_message"))] if simulation_result.get("error_message") else [],
        )
        stages.append(stage4_5)
        result.simulation = simulation_result

        # Stage 5: G代码生成
        stage5_start = time.time()

        try:
            gcode_result = self._gcode_generator.generate(
                operation_plan=operation_plan,
                controller_type=controller_type,
                material_name=material_name,
                program_number=program_number,
                safe_z=safe_z,
            )

            stage5 = PipelineStage(
                name="G代码生成",
                status="success" if gcode_result.is_valid else "completed_with_errors",
                duration_ms=(time.time() - stage5_start) * 1000,
                input_summary=f"工序数: {len(operation_plan.operations)}",
                output_summary=(
                    f"代码行数: {gcode_result.total_lines}, "
                    f"刀具数: {gcode_result.tool_count}, "
                    f"预估周期: {gcode_result.estimated_cycle_time_min}min"
                ),
                errors=gcode_result.errors,
                warnings=gcode_result.warnings,
            )
        except (OSError, ValueError, TypeError, KeyError, RuntimeError) as e:
            stage5 = PipelineStage(
                name="G代码生成",
                status="failed",
                duration_ms=(time.time() - stage5_start) * 1000,
                errors=[f"G代码生成异常: {type(e).__name__}"],
            )
            stages.append(stage5)
            result.stages = stages
            result.summary = f"流水线在G代码生成阶段失败: {type(e).__name__}"
            logger.error("G代码生成阶段失败: %s", e, exc_info=True)
            return result

        stages.append(stage5)
        result.gcode_result = gcode_result

        # Stage 6: 结果验证
        stage6_start = time.time()

        validation_errors, validation_warnings = self._validate_pipeline_output(result)

        stage6 = PipelineStage(
            name="结果验证",
            status="success" if not validation_errors else "failed",
            duration_ms=(time.time() - stage6_start) * 1000,
            output_summary=("验证通过" if not validation_errors else f"发现{len(validation_errors)}个错误"),
            errors=validation_errors,
            warnings=validation_warnings,
        )
        stages.append(stage6)

        # 汇总
        result.stages = stages
        result.success = all(s.status == "success" for s in stages)
        result.total_duration_ms = (time.time() - pipeline_start) * 1000

        # 构建摘要
        summary_parts = [
            f"流水线执行{'成功' if result.success else '部分失败'} ({result.total_duration_ms:.0f}ms)",
            f"零件: {material_name}({part_type})",
            f"孔识别: {hole_result.total_count}个孔 ({hole_result.type_summary})",
            f"工序规划: {len(operation_plan.operations)}个工序, {operation_plan.estimated_time_min:.1f}min预估工时",
        ]
        if gcode_result:
            summary_parts.append(
                f"G代码: {gcode_result.total_lines}行 "
                f"({gcode_result.tool_count}把刀具, "
                f"{gcode_result.estimated_cycle_time_min:.1f}min预估周期)"
            )
        result.summary = " | ".join(summary_parts)

        return result


def plan_process(
    feature: str = "pocket_cavity",
    material: str = "45steel",
    tool: str = "endmill_d10",
    **kwargs,
) -> dict[str, Any]:
    """工艺规划便捷函数。

    提供简化的工艺规划接口，支持端到端测试和快速调用。

    Args:
        feature: 加工特征类型 (如 'pocket_cavity', 'hole', 'slot')
        material: 材料名称 (如 '45steel', 'aluminum_6061')
        tool: 刀具标识 (如 'endmill_d10', 'drill_d8')
        **kwargs: 其他可选参数

    Returns:
        工艺规划结果字典，包含：
        - success: 是否成功
        - simulation: 仿真结果（包含 score, passed, recommendation 等）
        - operation_plan: 工序规划结果
        - gcode: G代码生成结果
        - 其他流水线输出字段
    """
    pipeline = ProcessPlanningPipeline()

    # 构建零件描述
    part_description = {
        "material": material,
        "part_type": kwargs.get("part_type", "general"),
        "holes": kwargs.get("holes", []),
        "features": kwargs.get("features", []),
    }

    # 如果指定了特征类型，添加到 features
    if feature:
        part_description["features"].append(
            {
                "type": feature,
                "name": f"{feature}_001",
            }
        )

    # 执行工艺规划流水线
    result = pipeline.run(
        part_description=part_description,
        controller_type=kwargs.get("controller_type", "fanuc_0i"),
        safe_z=kwargs.get("safe_z", 50.0),
        program_number=kwargs.get("program_number", 1000),
    )

    # 转换为字典格式返回
    return result.to_dict()
