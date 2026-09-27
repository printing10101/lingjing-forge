"""G代码生成模块。

基于工序规划结果生成符合数控系统语法的G代码。
支持Fanuc 0i-MF、Siemens 840D、Heidenhain TNC三种主流CNC系统。

G代码生成的设计原则：
1. 语法严格合规：每个控制器输出必须通过对应系统的语法校验
2. 工艺意图完整：G代码必须准确表达工艺规划的所有意图（刀具选择、切削参数、加工顺序）
3. 可读性优先：包含清晰的行号、注释和分段标记
4. 安全第一：包括安全回零、冷却液控制、碰撞避免等措施
5. 生产可用：生成的代码可以直接加载到CNC机床执行

处理流程：
工序规划结果 → 指令序列生成 → 后处理器格式化 → 语法校验 → 最终G代码文本
"""

from __future__ import annotations

import logging
from typing import Any

from app.process_planning.operation_sequencer import OperationPlan
from app.postprocessor.base import BasePostProcessor
from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.xmachine import XMachineXM100PostProcessor
from app.postprocessor.registry import PostProcessorRegistry
from app.toolpath.five_axis_planner import FiveAxisToolpathPlanner
from app.postprocessor.config_loader import ConfigLimiter

logger = logging.getLogger(__name__)

from app.process_planning._feature_code_mixin import _FeatureCodeMixin
from app.process_planning._hole_drilling_mixin import _HoleDrillingMixin
from app.process_planning._preview_mixin import _PreviewMixin

from app.process_planning._schemas import GCodeResult
from app.process_planning._validation import (  # noqa: F401
    validate_gcode,  # re-export：兼容历史导入路径（拆分前位于本模块）
    validate_gcode_syntax,
    build_dry_run_preview,
)

logger = logging.getLogger(__name__)


class GCodeGenerator(_FeatureCodeMixin, _HoleDrillingMixin, _PreviewMixin):
    """G代码生成器。

    从工序规划结果生成完整的数控加工程序。

    调用方式：
        generator = GCodeGenerator()
        result = generator.generate(
            operation_plan=plan,
            controller_type="fanuc_0i",
            material_name="45#钢",
            safe_z=50.0,
        )
        # result.program_text 即为完整的G代码程序
    """

    # 控制器类型到后处理器的映射
    CONTROLLER_MAP: dict[str, type[BasePostProcessor]] = {
        "fanuc_0i": FanucPostProcessor,
        "siemens_840d": SiemensPostProcessor,
        "heidenhain_tnc": HeidenhainPostProcessor,
        "xmachine_xm100": XMachineXM100PostProcessor,
    }

    def __init__(self, machine_config: dict[str, Any] | None = None) -> None:
        """初始化G代码生成器。

        创建后处理器注册表并预加载三种控制器。

        Args:
            machine_config: 机床配置参数（用于ConfigLimiter验证）
        """
        self._registry = PostProcessorRegistry()
        self._five_axis_planner = FiveAxisToolpathPlanner()
        self._config_limiter = ConfigLimiter(machine_config) if machine_config else None

    def generate(
        self,
        operation_plan: OperationPlan,
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
        program_number: int = 1000,
        safe_z: float = 80.0,
        tool_radius_compensation: str = "G41",
        use_coolant: bool = True,
        stock_top_z: float = 50.0,
    ) -> GCodeResult:
        """从工序规划结果生成完整G代码程序。

        生成步骤：
        1. 初始化后处理器（根据controller_type选择）
        2. 生成程序头（程序号、注释、安全设置）
        3. 逐工序解析为加工指令（直线/圆弧/钻孔循环）
        4. 调用后处理器格式化每条指令
        5. 生成程序尾（回零、关主轴、关冷却液）
        6. 执行语法校验

        Args:
            operation_plan: 工序规划结果
            controller_type: 控制器类型标识符
            material_name: 材料名称（用于注释）
            program_number: 程序号 (O号)
            safe_z: 安全平面Z高度 (mm)，必须高于 stock_top_z
            tool_radius_compensation: 刀具半径补偿模式 "G41"/"G42"/"G40"
            use_coolant: 是否开启冷却液
            stock_top_z: 毛坯顶面Z坐标 (mm)，切削深度以此为基准向下计算

        Returns:
            GCodeResult: 生成的G代码程序及元信息

        Raises:
            ValueError: 当controller_type无效或operation_plan为空时
        """
        if safe_z <= stock_top_z:
            # 安全平面必须高于毛坯顶面，否则快速定位时会触发碰撞
            safe_z = stock_top_z + 30.0
        if not operation_plan or not operation_plan.operations:
            raise ValueError("工序规划结果为空，无法生成G代码")

        if controller_type not in self.CONTROLLER_MAP:
            available = list(self.CONTROLLER_MAP.keys())
            raise ValueError(f"不支持的控制器类型: '{controller_type}'。可用类型: {available}")

        postprocessor = self._registry.get_processor(controller_type)
        warnings: list[str] = []
        errors: list[str] = []

        # 五轴模式检测
        is_five_axis = controller_type == "xmachine_xm100"

        # 五轴机床 Z 行程通常较小（XM-100 仅 ±50mm），
        # 默认 safe_z=80.0 会超出物理限制导致后处理器校验失败。
        # 此处自动 clamp 到机床行程上限的 90%（留 10% 余量）。
        if is_five_axis:
            travel_z = getattr(postprocessor, "XM100_TRAVEL_Z", None)
            if travel_z is not None:
                max_safe_z = travel_z / 2 * 0.9
                if safe_z > max_safe_z:
                    logger.warning(
                        "safe_z=%.1f 超过 XM-100 Z 行程上限 ±%.0fmm，自动 clamp 到 %.1fmm",
                        safe_z,
                        travel_z / 2,
                        max_safe_z,
                    )
                    safe_z = max_safe_z

        lines: list[str] = []

        # 程序头
        header = postprocessor.format_header(program_number)
        lines.append(header)

        # 程序注释
        lines.append(postprocessor._comment(f"材料: {material_name}"))
        lines.append(
            postprocessor._comment(f"工序数: {len(operation_plan.operations)} | 装夹次数: {len(operation_plan.setups)}")
        )
        lines.append(postprocessor._comment(f"控制器: {controller_type} | 生成日期: {postprocessor._date_string()}"))
        if is_five_axis:
            lines.append(postprocessor._comment("五轴加工模式: A/C轴联动"))
        lines.append("")

        # 安全设置
        lines.append(postprocessor._comment("安全设置"))
        lines.append("G17 G21 G40 G49 G80 G90")  # XY平面, 公制, 取消补偿, 取消固定循环
        lines.append(f"G00 Z{safe_z:.3f}")  # Z轴抬至安全高度

        # 控制器特性方言接入
        # 根据控制器类型启用对应的特殊功能模式
        if controller_type == "fanuc_0i":
            # Fanuc: 启用高精度加工模式 (G05.1 Q1)
            if hasattr(postprocessor, "format_high_precision_mode"):
                lines.append(postprocessor._comment("启用AI高精度轮廓控制"))
                lines.append(postprocessor.format_high_precision_mode(enable=True, mode=1))
        elif controller_type == "siemens_840d":
            # Siemens: 五轴模式时启用 TRAORI
            if is_five_axis and hasattr(postprocessor, "format_five_axis_mode"):
                lines.append(postprocessor._comment("启用五轴联动模式 TRAORI"))
                lines.append(postprocessor.format_five_axis_mode(enable=True))
        elif controller_type == "heidenhain_tnc":
            # Heidenhain: 启用高精度模式 (M128)
            if hasattr(postprocessor, "format_high_precision_mode"):
                lines.append(postprocessor._comment("启用高精度加工模式 M128"))
                lines.append(postprocessor.format_high_precision_mode(enable=True))

        # 刀具清单汇总表
        # 收集刀具信息：刀具类型 -> {直径, 加工方法, 关联工序}
        tool_info: dict[str, dict[str, Any]] = {}
        for op in operation_plan.operations:
            tool_key = op.tool_type or "UNKNOWN"
            if tool_key not in tool_info:
                # 从 cutting_params 提取刀具直径
                cut_params = op.cutting_params or {}
                tool_diameter = cut_params.get("tool_diameter", 0.0)
                tool_info[tool_key] = {
                    "diameter": tool_diameter,
                    "methods": set(),
                    "features": set(),
                    "op_count": 0,
                }
            tool_info[tool_key]["methods"].add(op.machining_method)
            tool_info[tool_key]["features"].add(op.feature_name)
            tool_info[tool_key]["op_count"] += 1

        # 生成结构化刀具清单
        lines.append(postprocessor._comment("=" * 50))
        lines.append(postprocessor._comment("刀具清单汇总表 (TOOL LIST SUMMARY)"))
        lines.append(postprocessor._comment("=" * 50))
        tool_count = 0
        for tool_key, info in tool_info.items():
            tool_count += 1
            diameter_str = f"Φ{info['diameter']:.1f}" if info["diameter"] > 0 else "N/A"
            methods_str = "/".join(sorted(info["methods"]))
            features_str = ", ".join(sorted(info["features"])[:3])  # 最多显示3个特征
            if len(info["features"]) > 3:
                features_str += f"...等{len(info['features'])}个"
            lines.append(
                postprocessor._comment(
                    f"T{tool_count:02d} | {tool_key:<20} | {diameter_str:<8} | "
                    f"{methods_str:<15} | {info['op_count']}次 | {features_str}"
                )
            )
        lines.append(postprocessor._comment(f"总计: {tool_count} 把刀具"))
        lines.append(postprocessor._comment("=" * 50))
        lines.append("")

        # 逐工序生成G代码
        _current_tool = ""
        tool_index = 0
        tool_registry: dict[str, int] = {}
        checkpoints: list[dict[str, Any]] = []
        checkpoint_counter = 0

        for op_index, op in enumerate(operation_plan.operations):
            tool_key = op.tool_type or f"TOOL_{op_index}"

            # 换刀逻辑
            if tool_key not in tool_registry:
                tool_index += 1
                tool_registry[tool_key] = tool_index
                _current_tool = tool_key

                lines.append("")
                lines.append(postprocessor._comment(f"---- OP{op.seq:02d} {op.name} - {op.machining_method} ----"))

                # 生成换刀指令 - 传递整数编号
                tool_change_code = postprocessor.format_tool_change(
                    tool_id=tool_index,
                    length_comp=float(tool_index),
                    radius_comp=float(tool_index),
                )
                lines.append(tool_change_code)
            else:
                lines.append("")
                lines.append(postprocessor._comment(f"---- OP{op.seq:02d} {op.name} (复用{tool_key}) ----"))

            # 插入断点标记（每道工序开始前）
            checkpoint_counter += 1
            checkpoint_label = f"CP{checkpoint_counter:03d}"
            checkpoint_line = len(lines)

            # 根据控制器类型生成断点标记
            if controller_type == "fanuc_0i":
                # Fanuc 使用 N 行号 + 注释
                lines.append(f"N{checkpoint_counter * 100:05d} (BREAKPOINT: {checkpoint_label})")
            elif controller_type == "siemens_840d":
                # Siemens 使用 ; 注释
                lines.append(f"N{checkpoint_counter * 100:05d} ; BREAKPOINT: {checkpoint_label}")
            elif controller_type == "heidenhain_tnc":
                # Heidenhain 使用 ; 注释
                lines.append(f"{checkpoint_counter * 100}  ; BREAKPOINT: {checkpoint_label}")
            else:
                # 默认使用注释格式
                lines.append(postprocessor._comment(f"BREAKPOINT: {checkpoint_label}"))

            # 记录断点信息
            checkpoints.append(
                {
                    "checkpoint_id": checkpoint_label,
                    "op_index": op_index,
                    "op_name": op.name,
                    "feature_name": op.feature_name,
                    "line_number": checkpoint_line,
                    "tool_key": tool_key,
                    "tool_index": tool_index,
                }
            )

            # 冷却液控制
            if use_coolant:
                coolant_code = postprocessor.format_coolant("on")
                lines.append(coolant_code)

            # 根据加工类型生成对应的加工指令
            # 从工序参数中提取材料和刀具信息
            cut_params = op.cutting_params or {}
            material = cut_params.get("material", "steel")
            tool_diameter = cut_params.get("tool_diameter", 10.0)
            radius_comp = cut_params.get("radius_comp", "G41")

            feature_lines = self._generate_feature_code(
                op=op,
                postprocessor=postprocessor,
                safe_z=safe_z,
                controller_type=controller_type,
                material=material,
                tool_diameter=tool_diameter,
                radius_comp=radius_comp,
                stock_top_z=stock_top_z,
            )
            lines.extend(feature_lines)

            # 冷却液关闭（工序完成）
            if use_coolant:
                coolant_off = postprocessor.format_coolant("off")
                lines.append(coolant_off)

        # 程序尾
        lines.append("")
        lines.append(postprocessor._comment("程序结束"))
        footer = postprocessor.format_footer()
        lines.append(footer)

        # 组装完整程序
        program_text = "\n".join(lines)

        # 计算总行数
        total_lines = len(lines)

        # 语法校验
        syntax_errors = self._validate_syntax(program_text, controller_type)
        if syntax_errors:
            errors.extend(syntax_errors)

        # 预估加工周期（包含换刀时间）
        cycle_time = operation_plan.estimated_time_min + tool_count * 1.5

        return GCodeResult(
            program_text=program_text,
            controller_type=controller_type,
            program_number=program_number,
            total_lines=total_lines,
            operations_count=len(operation_plan.operations),
            tool_count=tool_count,
            estimated_cycle_time_min=round(cycle_time, 2),
            warnings=warnings,
            errors=errors,
            metadata={
                "material": material_name,
                "setups": [
                    {"name": s.name, "surface": s.surface, "fixture": s.fixture_type} for s in operation_plan.setups
                ],
                "cutting_parameter_count": sum(1 for op in operation_plan.operations if op.cutting_params),
            },
            checkpoints=checkpoints,
        )
