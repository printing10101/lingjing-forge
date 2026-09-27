"""G 代码预览与语法校验 mixin（从 gcode_generator 拆出）。"""

from __future__ import annotations

from typing import Any

from app.process_planning._validation import build_dry_run_preview, validate_gcode_syntax
from app.process_planning.operation_sequencer import OperationPlan


class _PreviewMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    CONTROLLER_MAP: Any

    def _validate_syntax(
        self,
        program_text: str,
        controller_type: str,
    ) -> list[str]:
        """G代码语法校验（增强版）。委托 _validation.validate_gcode_syntax。"""
        return validate_gcode_syntax(program_text, controller_type)

    def dry_run_preview(
        self,
        operation_plan: OperationPlan,
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
        program_number: int = 1000,
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
    ) -> dict[str, Any]:
        """G代码 dry-run 预览模式。委托 _validation.build_dry_run_preview。"""
        return build_dry_run_preview(
            operation_plan,
            controller_type=controller_type,
            material_name=material_name,
            program_number=program_number,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
        )

    def list_available_controllers(self) -> list[str]:
        """列出所有可用的控制器类型"""
        return list(self.CONTROLLER_MAP.keys())
