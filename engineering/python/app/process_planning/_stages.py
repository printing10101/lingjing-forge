"""工艺规划流水线阶段数据类（从 pipeline 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.process_planning.gcode_generator import GCodeResult
from app.process_planning.hole_recognizer import HoleRecognitionResult
from app.process_planning.operation_sequencer import OperationPlan
from app.process_planning.tool_param_matcher import HoleProcessPlan


@dataclass
class PipelineStage:
    """单个流水线阶段的执行记录。

    Attributes:
        name: 阶段名称
        status: 执行状态 (success/failed/skipped)
        duration_ms: 执行耗时(毫秒)
        input_summary: 输入摘要
        output_summary: 输出摘要
        errors: 该阶段的错误列表
        warnings: 该阶段的警告列表
    """

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
class PipelineResult:
    """端到端流水线的完整执行结果。

    Attributes:
        success: 整体是否成功
        stages: 各阶段执行记录
        hole_recognition: 孔特征识别结果
        process_plans: 每个孔的加工工艺方案
        operation_plan: 整体工序规划
        gcode_result: G代码生成结果
        total_duration_ms: 总耗时(毫秒)
        summary: 流水线执行摘要
    """

    success: bool = False
    stages: list[PipelineStage] = field(default_factory=list)
    hole_recognition: HoleRecognitionResult | None = None
    process_plans: list[HoleProcessPlan] = field(default_factory=list)
    operation_plan: OperationPlan | None = None
    gcode_result: GCodeResult | None = None
    simulation: dict[str, Any] | None = None
    total_duration_ms: float = 0.0
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": self.success,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "summary": self.summary,
            "stages": [s.to_dict() for s in self.stages],
        }
        if self.hole_recognition:
            result["hole_recognition"] = self.hole_recognition.to_dict()
        if self.process_plans:
            result["process_plans"] = [p.to_dict() for p in self.process_plans]
        if self.operation_plan:
            result["operation_plan"] = self.operation_plan.to_dict()
        if self.gcode_result:
            result["gcode"] = {
                "controller_type": self.gcode_result.controller_type,
                "program_number": self.gcode_result.program_number,
                "total_lines": self.gcode_result.total_lines,
                "operations_count": self.gcode_result.operations_count,
                "tool_count": self.gcode_result.tool_count,
                "estimated_cycle_time_min": self.gcode_result.estimated_cycle_time_min,
                "is_valid": self.gcode_result.is_valid,
                "program_text": self.gcode_result.program_text,
                "warnings": self.gcode_result.warnings,
                "errors": self.gcode_result.errors,
            }
        # 始终包含仿真字段，确保接口一致性
        result["simulation"] = (
            self.simulation
            if self.simulation
            else {
                "status": "not_run",
                "score": 0.0,
                "passed": False,
                "recommendation": "not_recommended",
                "cutting_force": None,
                "chatter_stability": None,
                "duration_ms": 0.0,
                "error_message": "仿真未执行（流水线提前终止）",
            }
        )
        return result
