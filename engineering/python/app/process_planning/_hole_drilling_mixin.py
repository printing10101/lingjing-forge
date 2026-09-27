"""钻孔专用 G 代码生成 mixin（从 gcode_generator 拆出）。"""

from __future__ import annotations

from typing import Any

from app.process_planning._schemas import GCodeResult


class _HoleDrillingMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    CONTROLLER_MAP: Any
    _registry: Any

    def generate_hole_drilling_only(
        self,
        hole_positions: list[dict[str, float]],
        hole_depth: float,
        safe_z: float = 80.0,
        retract_plane: float = 5.0,
        controller_type: str = "fanuc_0i",
        tool_number: int = 1,
        spindle_speed: int = 1500,
        feed_rate: float = 150.0,
        material_name: str = "45#钢",
        stock_top_z: float = 50.0,
    ) -> GCodeResult:
        """仅生成钻孔G代码（简化API）。

        适用于已有孔位置数据、只需钻孔加工的场景。

        Args:
            hole_positions: 孔位置列表 [{"x": ..., "y": ..., "z": ...}, ...]
            hole_depth: 钻孔深度 (mm)，正值表示Z负方向钻进
            safe_z: 安全Z高度 (mm)
            retract_plane: 退刀平面R点高度 (mm)
            controller_type: 控制器类型
            tool_number: 刀具号
            spindle_speed: 主轴转速 (rpm)
            feed_rate: 进给速度 (mm/min)
            material_name: 材料名称

        Returns:
            GCodeResult: G代码生成结果
        """
        if not hole_positions:
            raise ValueError("孔位置列表不能为空")

        if controller_type not in self.CONTROLLER_MAP:
            raise ValueError(f"不支持的控制器类型: '{controller_type}'")

        postprocessor = self._registry.get_processor(controller_type)
        lines: list[str] = []

        # 程序头
        lines.append(postprocessor.format_header(1000))
        lines.append(postprocessor._comment(f"钻孔程序 - {material_name} - {len(hole_positions)}个孔"))
        lines.append(postprocessor._comment(f"深度: {hole_depth}mm | 安全高度: {safe_z}mm"))
        lines.append("G17 G21 G40 G49 G80 G90")
        lines.append(f"G00 Z{safe_z:.3f}")

        # 单刀具设置
        lines.append(
            postprocessor.format_tool_change(
                tool_id=tool_number,
                length_comp=float(tool_number),
                radius_comp=float(tool_number),
            )
        )
        lines.append(f"S{spindle_speed} M03")
        lines.append(postprocessor.format_coolant("on"))

        # 钻孔固定循环 - 使用后处理器的固定循环格式
        for i, pos in enumerate(hole_positions):
            x = pos.get("x", 0.0)
            y = pos.get("y", 0.0)
            z_surface = pos.get("z", stock_top_z)  # 默认从毛坯顶面开始
            actual_depth = z_surface - abs(hole_depth)  # Z负方向钻进

            cycle_code = postprocessor.format_cycle_drill(
                x=x,
                y=y,
                z=actual_depth,
                depth=hole_depth,
                dwell=0.5 if hole_depth > 15 else 0.0,
            )
            lines.append(postprocessor._comment(f"孔{i + 1}: X{x:.2f} Y{y:.2f}"))
            lines.append(cycle_code)

        # 取消固定循环
        lines.append("G80")

        # 程序尾
        lines.append(postprocessor.format_coolant("off"))
        lines.append(postprocessor.format_footer())

        program_text = "\n".join(lines)

        return GCodeResult(
            program_text=program_text,
            controller_type=controller_type,
            program_number=1000,
            total_lines=len(lines),
            operations_count=len(hole_positions),
            tool_count=1,
            estimated_cycle_time_min=round(len(hole_positions) * 0.5, 2),
            warnings=[],
            errors=[],
            metadata={"material": material_name},
        )
