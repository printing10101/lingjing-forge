"""G代码生成结果数据类（从 gcode_generator 拆分，D5）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GCodeResult:
    """G代码生成结果。

    Attributes:
        program_text: 完整的G代码程序文本
        controller_type: 目标控制器类型(fanuc/siemens/heidenhain)
        program_number: 程序号
        total_lines: 总行数
        operations_count: 包含的工序数量
        tool_count: 使用的刀具数量
        estimated_cycle_time_min: 预估加工周期(分钟)
        warnings: 生成过程中的警告
        errors: 生成过程中的错误
        metadata: 附加元数据
        checkpoints: 断点续传标记点列表 [{"op_index": int, "line_number": int, "label": str}]
    """

    program_text: str
    controller_type: str
    program_number: int = 1000
    total_lines: int = 0
    operations_count: int = 0
    tool_count: int = 0
    estimated_cycle_time_min: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert the G-code result to a dictionary representation.

        Returns:
            A dictionary containing program text, controller type, program
            metadata, and any warnings or errors.
        """
        return {
            "program_text": self.program_text,
            "controller_type": self.controller_type,
            "program_number": self.program_number,
            "total_lines": self.total_lines,
            "operations_count": self.operations_count,
            "tool_count": self.tool_count,
            "estimated_cycle_time_min": self.estimated_cycle_time_min,
            "warnings": self.warnings,
            "errors": self.errors,
            "metadata": self.metadata,
            "checkpoints": self.checkpoints,
        }

    @property
    def is_valid(self) -> bool:
        """G代码是否有效（无语法错误）"""
        return len(self.errors) == 0
