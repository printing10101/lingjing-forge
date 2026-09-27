"""G 代码生成适配器（阶段 6）：封装现有 GCodeGenerator，注入 ChatterReport 安全裕度。

职责
====
1. 组合（has-a）``app.process_planning.gcode_generator.GCodeGenerator``，不继承
   —— 保护其 212 个测试用例不受阶段 6 改动影响。
2. 调用 ``GCodeGenerator.generate()`` 生成基础 G 代码（复用现有后处理器 + 语法校验）。
3. 遍历阶段 5 ``ChatterReport.feature_results``，为每个特征：
   - 计算 ``safety_margin_ratio = axial_depth_mm / limit_depth_mm``
   - 若 ``axial_depth_mm > SAFETY_MARGIN_RATIO × limit_depth_mm``（0.8）→ 写入 warning
   - 若 ``stable == False`` → 在 ``GCodeResult.errors`` 追加「不稳定特征」错误
     （使 ``GCodeResult.is_valid == False``，禁止导出，强制工程师回阶段 5 降低切深）
4. 利用 ``GCodeResult.checkpoints`` + ``operation.feature_name`` 匹配 ``feature_id``，
   抽取每个特征对应的 G 代码行片段（``gcode_lines`` / ``line_range``）。
5. 提供 ``load_operation_plan()`` 静态方法，从 JSON 反序列化 ``OperationPlan``
   （``OperationPlan`` 无 ``from_dict()`` / ``from_json()``，需自行实现）。

项目记忆硬约束
==============
- 系统定位「工程师助手」，非「全自动 G 代码生成器」
- 复用现有 GCodeGenerator（212 个测试用例覆盖），不重写
- SAFETY_MARGIN_RATIO=0.8，实际切深超过极限切深 80% 时发出警告
- stable == False 的特征禁止生成 G 代码（在 errors 中追加错误，使 is_valid=False）
- cam_validation_required 始终 True，不可由环境变量关闭
- K_s（cutting_force_coeff）直接来自阶段 4，不二次拟合（阶段 6 不涉及拟合）
- HRC52 pending_calibration 标注由阶段 5 完成，阶段 6 仅继承
- 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验，绝不直接接口 CNC 控制器

精度继承链
==========
阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5 → 阶段 6（本模块）
本模块不引入新的精度档位，全程继承上游告知。
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

from app.chatter_prediction.chatter_store import FeatureChatterResult
from app.gcode_generation.gcode_store import (
    GCodeGenerationError,
    FeatureGCodeResult,
    OperationPlanLoadError,
    SAFETY_MARGIN_RATIO,
)
from app.process_planning.feature_dependency import Setup
from app.process_planning.gcode_generator import GCodeGenerator, GCodeResult
from app.process_planning.operation_sequencer import Operation, OperationPlan

logger = logging.getLogger(__name__)


__all__ = [
    "GeneratorAdapter",
    "GeneratorAdapterError",
    "load_operation_plan",
]


# 异常


class GeneratorAdapterError(GCodeGenerationError):
    """GeneratorAdapter 适配异常。"""


# OperationPlan JSON 反序列化


# OperationPlan.to_dict() 输出的必填字段
_REQUIRED_OPERATION_FIELDS = {"seq", "name", "feature_name", "machining_method", "surface", "tolerance_grade"}
_REQUIRED_SETUP_FIELDS = {"name", "surface"}


def load_operation_plan(json_path: str) -> OperationPlan:
    """从 JSON 文件反序列化 OperationPlan。

    ``OperationPlan`` 有 ``to_dict()`` 但无 ``from_dict()`` / ``from_json()``，
    此函数补齐反序列化能力，仅供阶段 6 GeneratorAdapter 使用。

    反序列化策略：
    - ``operations``: 完整恢复为 ``Operation`` 对象（GCodeGenerator.generate() 依赖）
    - ``setups``: 恢复为 ``Setup`` 对象（generate() 仅用 len() + name/surface/fixture_type）
    - ``estimated_time_min`` / ``face_change_count``: 直接读取
    - ``fixture_recommendations``: 留空（generate() 不使用此字段）

    Args:
        json_path: OperationPlan JSON 文件路径

    Returns:
        OperationPlan 对象

    Raises:
        OperationPlanLoadError: 文件不存在 / JSON 格式错误 / 必填字段缺失
    """
    path = Path(json_path)
    if not path.exists():
        raise OperationPlanLoadError(f"阶段 3 OperationPlan 不存在: {json_path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise OperationPlanLoadError(f"OperationPlan JSON 格式错误: {e}") from e

    if not isinstance(raw, dict):
        raise OperationPlanLoadError("OperationPlan JSON 顶层必须是 dict")

    # 反序列化 operations
    raw_operations = raw.get("operations", [])
    if not isinstance(raw_operations, list):
        raise OperationPlanLoadError("OperationPlan operations 必须是列表")

    operations: list[Operation] = []
    for i, raw_op in enumerate(raw_operations):
        if not isinstance(raw_op, dict):
            raise OperationPlanLoadError(f"operations[{i}] 必须是 dict")
        missing = _REQUIRED_OPERATION_FIELDS - set(raw_op.keys())
        if missing:
            raise OperationPlanLoadError(f"operations[{i}] 缺少必填字段: {missing}")
        operations.append(
            Operation(
                seq=int(raw_op["seq"]),
                name=str(raw_op["name"]),
                feature_name=str(raw_op["feature_name"]),
                machining_method=str(raw_op["machining_method"]),
                surface=str(raw_op["surface"]),
                tolerance_grade=str(raw_op["tolerance_grade"]),
                tool_type=str(raw_op.get("tool_type", "")),
                cutting_params=dict(raw_op.get("cutting_params", {})),
                estimated_time_min=float(raw_op.get("estimated_time_min", 0.0)),
                notes=str(raw_op.get("notes", "")),
            )
        )

    if not operations:
        raise OperationPlanLoadError("OperationPlan operations 为空，无法生成 G 代码")

    # 反序列化 setups（generate() 仅用 len() + name/surface/fixture_type）
    raw_setups = raw.get("setups", [])
    setups: list[Setup] = []
    if isinstance(raw_setups, list):
        for i, raw_setup in enumerate(raw_setups):
            if not isinstance(raw_setup, dict):
                continue
            missing = _REQUIRED_SETUP_FIELDS - set(raw_setup.keys())
            if missing:
                logger.warning(
                    "setups[%d] 缺少字段 %s，跳过",
                    i,
                    missing,
                )
                continue
            setups.append(
                Setup(
                    name=str(raw_setup["name"]),
                    surface=str(raw_setup["surface"]),
                    datum_features=list(raw_setup.get("datum_features", [])),
                    fixture_type=str(raw_setup.get("fixture_type", "")),
                    clamped_features=list(raw_setup.get("clamped_features", [])),
                )
            )

    # 可选刀具实参（阶段 3 新版 JSON 携带；旧版缺省 → None，
    # 阶段 7 体素仿真回退配置默认值并如实标注 tool_source）
    tool_diameter_mm: float | None = None
    raw_diameter = raw.get("tool_diameter_mm")
    if raw_diameter is not None:
        try:
            parsed = float(raw_diameter)
            if math.isfinite(parsed) and parsed > 0:
                tool_diameter_mm = parsed
            else:
                logger.warning("OperationPlan tool_diameter_mm 非法（%s），按未提供处理", raw_diameter)
        except (TypeError, ValueError):
            logger.warning("OperationPlan tool_diameter_mm 非法（%s），按未提供处理", raw_diameter)

    return OperationPlan(
        operations=operations,
        setups=setups,
        estimated_time_min=float(raw.get("estimated_time_min", 0.0)),
        face_change_count=int(raw.get("face_change_count", 0)),
        fixture_recommendations=[],  # generate() 不使用此字段
        tool_diameter_mm=tool_diameter_mm,
    )


# GeneratorAdapter：组合 GCodeGenerator


class GeneratorAdapter:
    """G 代码生成适配器：组合 GCodeGenerator，注入 ChatterReport 安全裕度。

    设计原则（ADR-014）：
        组合（has-a）而非继承，避免破坏 GCodeGenerator 的 212 个测试用例。
        本类只负责「安全裕度适配」和「特征级 G 代码段切分」，
        不修改 GCodeGenerator 的核心生成逻辑。

    使用方式：
        adapter = GeneratorAdapter()
        base_result, feature_results = adapter.adapt(
            operation_plan=plan,
            chatter_results=chatter_report.feature_results,
            controller_type="fanuc_0i",
            material_name="45#钢",
        )
        if not base_result.is_valid:
            # 含 unstable 特征或语法错误，禁止导出
            ...
        for fr in feature_results:
            logger.info("特征 %s: 安全裕度 %.2f, 警告 %s", fr.feature_id, fr.safety_margin_ratio, fr.warning)
    """

    def __init__(self, machine_config: dict[str, Any] | None = None) -> None:
        """初始化适配器。

        Args:
            machine_config: 机床配置参数（透传给 GCodeGenerator，用于 ConfigLimiter 验证）
        """
        self._generator = GCodeGenerator(machine_config=machine_config)

    # 主入口：adapt()

    def adapt(
        self,
        operation_plan: OperationPlan,
        chatter_results: list[FeatureChatterResult],
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
        program_number: int = 1000,
        safe_z: float = 80.0,
        tool_radius_compensation: str = "G41",
        use_coolant: bool = True,
        stock_top_z: float = 50.0,
    ) -> tuple[GCodeResult, list[FeatureGCodeResult]]:
        """生成 G 代码 + 特征级安全裕度结果。

        执行流程：
            1. 调用 ``GCodeGenerator.generate()`` 生成基础 G 代码
            2. 遍历 ``chatter_results``，为每个特征计算 ``safety_margin_ratio``
            3. 若 ``axial_depth > 0.8 × limit_depth`` → 写入 ``FeatureGCodeResult.warning``
            4. 若 ``stable == False`` → 在 ``GCodeResult.errors`` 追加错误
               （使 ``is_valid == False``，禁止导出，强制工程师回阶段 5 降低切深）
            5. 利用 ``checkpoints`` 匹配 ``feature_id ↔ feature_name``，
               切分每个特征的 ``gcode_lines`` 和 ``line_range``

        Args:
            operation_plan: 阶段 3 工序规划结果
            chatter_results: 阶段 5 ChatterReport 中的特征颤振预测结果列表
            controller_type: 控制器类型（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
            material_name: 材料名称（用于 G 代码注释）
            program_number: 程序号
            safe_z: 安全平面 Z 高度（mm）
            tool_radius_compensation: 刀具半径补偿模式（G41/G42/G40）
            use_coolant: 是否开启冷却液
            stock_top_z: 毛坯顶面 Z 坐标（mm）

        Returns:
            (GCodeResult, list[FeatureGCodeResult]) 二元组：
            - GCodeResult: 基础 G 代码生成结果（可能含 unstable 特征错误）
            - list[FeatureGCodeResult]: 每个特征的安全裕度 + G 代码片段

        Raises:
            GeneratorAdapterError: chatter_results 为空 / GCodeGenerator.generate() 抛错
        """
        if not chatter_results:
            raise GeneratorAdapterError(
                "chatter_results 为空，无法执行安全裕度适配（阶段 5 ChatterReport 必须包含至少一个特征）"
            )

        # 1. 调用 GCodeGenerator.generate() 生成基础 G 代码
        try:
            base_result = self._generator.generate(
                operation_plan=operation_plan,
                controller_type=controller_type,
                material_name=material_name,
                program_number=program_number,
                safe_z=safe_z,
                tool_radius_compensation=tool_radius_compensation,
                use_coolant=use_coolant,
                stock_top_z=stock_top_z,
            )
        except ValueError as e:
            raise GeneratorAdapterError(f"GCodeGenerator.generate() 失败: {e}") from e

        # 2. 追加 unstable 特征错误（使 is_valid=False，禁止导出）
        unstable_features = [f for f in chatter_results if not f.stable]
        for f in unstable_features:
            error_msg = (
                f"特征 {f.feature_id}({f.feature_type}) 颤振预测结果为不稳定"
                f"（axial_depth={f.axial_depth_mm}mm, limit_depth={f.limit_depth_mm}mm, "
                f"method={f.method}），禁止生成 G 代码。"
                "请回到阶段 5 降低切深或主轴转速后重新审核。"
            )
            base_result.errors.append(error_msg)
            logger.warning(
                "特征 %s 不稳定，已追加 error 禁止导出 G 代码",
                f.feature_id,
            )

        # 3. 构建 feature_id (start_line, end_line) 映射（基于 checkpoints）
        feature_line_ranges = self._build_feature_line_ranges(base_result, operation_plan)

        # 5. 切分 G 代码行（program_text list[str]）
        program_lines = base_result.program_text.split("\n") if base_result.program_text else []

        # 6. 遍历 chatter_results，生成 FeatureGCodeResult
        feature_gcode_results: list[FeatureGCodeResult] = []
        for f in chatter_results:
            safety_margin_ratio = self._compute_safety_margin(f.axial_depth_mm, f.limit_depth_mm)

            # 安全裕度警告
            warning = ""
            if f.limit_depth_mm > 0 and f.axial_depth_mm > SAFETY_MARGIN_RATIO * f.limit_depth_mm:
                warning = (
                    f"安全裕度不足：实际切深 {f.axial_depth_mm:.3f}mm > "
                    f"极限切深 × {SAFETY_MARGIN_RATIO} = "
                    f"{f.limit_depth_mm * SAFETY_MARGIN_RATIO:.3f}mm"
                )
                if not f.stable:
                    warning += "（且颤振预测为不稳定）"
                logger.info(
                    "特征 %s 安全裕度不足 ratio=%.3f axial=%.3f limit=%.3f",
                    f.feature_id,
                    safety_margin_ratio,
                    f.axial_depth_mm,
                    f.limit_depth_mm,
                )

            # 提取特征 G 代码片段
            line_range = feature_line_ranges.get(f.feature_id, (0, 0))
            gcode_lines = self._extract_feature_gcode_lines(program_lines, line_range)

            feature_gcode_results.append(
                FeatureGCodeResult(
                    feature_id=f.feature_id,
                    feature_type=f.feature_type,
                    material_id=f.material_id,
                    spindle_rpm=f.spindle_rpm,
                    axial_depth_mm=f.axial_depth_mm,
                    limit_depth_mm=f.limit_depth_mm,
                    stable=f.stable,
                    safety_margin_ratio=safety_margin_ratio,
                    gcode_lines=gcode_lines,
                    line_range=line_range,
                    warning=warning,
                )
            )

        logger.info(
            "GeneratorAdapter.adapt() 完成 controller=%s features=%d stable=%d unstable=%d "
            "warnings=%d errors=%d total_lines=%d",
            controller_type,
            len(feature_gcode_results),
            sum(1 for r in feature_gcode_results if r.stable),
            len(unstable_features),
            sum(1 for r in feature_gcode_results if r.warning),
            len(base_result.errors),
            base_result.total_lines,
        )

        return base_result, feature_gcode_results

    # 辅助方法

    @staticmethod
    def _compute_safety_margin(axial_depth_mm: float, limit_depth_mm: float) -> float:
        """计算安全裕度比例 = axial_depth / limit_depth。

        Args:
            axial_depth_mm: 实际切深（mm）
            limit_depth_mm: 极限切深（mm）

        Returns:
            safety_margin_ratio: 无量纲比例
            - limit_depth_mm > 0 时：axial / limit
            - limit_depth_mm == 0 时：返回 -1.0（无法计算，表示极限切深为 0）
            - limit_depth_mm < 0 时：返回 -1.0（异常值）
        """
        if limit_depth_mm <= 0:
            return -1.0
        return axial_depth_mm / limit_depth_mm

    @staticmethod
    def _build_feature_operation_map(
        operation_plan: OperationPlan,
    ) -> dict[str, list[int]]:
        """构建 feature_id → [op_index, ...] 映射。

        用于将 ChatterReport 中的 feature_id 匹配到 OperationPlan 中的 operation。
        匹配规则：``operation.feature_name == feature_id``（阶段 2 特征提取保证一致性）。

        一个 feature_id 可能对应多个 operation（如同一特征有粗加工 + 精加工两道工序）。
        """
        mapping: dict[str, list[int]] = {}
        for op_index, op in enumerate(operation_plan.operations):
            if op.feature_name not in mapping:
                mapping[op.feature_name] = []
            mapping[op.feature_name].append(op_index)
        return mapping

    @staticmethod
    def _build_feature_line_ranges(
        base_result: GCodeResult,
        operation_plan: OperationPlan,
    ) -> dict[str, tuple[int, int]]:
        """基于 checkpoints 构建 feature_id → (start_line, end_line) 映射。

        GCodeGenerator.generate() 在每个工序开始前插入 BREAKPOINT checkpoint，
        记录了 ``op_index`` / ``feature_name`` / ``line_number``。

        匹配策略：
            1. 按 checkpoints 顺序遍历，每个 checkpoint 对应一个 operation
            2. ``start_line = checkpoint.line_number``
            3. ``end_line = 下一个 checkpoint.line_number - 1``（最后一个 checkpoint 的
               end_line = total_lines - 1）
            4. ``feature_name`` 作为 key（与 ChatterReport 的 feature_id 匹配）

        若同一 feature_name 对应多个 checkpoint（粗加工 + 精加工），
        合并为最大的行范围 [first_start, last_end]。
        """
        if not base_result.checkpoints:
            return {}

        # checkpoints 按 op_index 排序（保险起见）
        sorted_cps = sorted(
            base_result.checkpoints,
            key=lambda cp: cp.get("op_index", 0),
        )

        # 先按 feature_name 聚合所有 checkpoint 的 line_number
        feature_lines: dict[str, list[int]] = {}
        for cp in sorted_cps:
            feature_name = cp.get("feature_name", "")
            line_number = cp.get("line_number", 0)
            if not feature_name:
                continue
            if feature_name not in feature_lines:
                feature_lines[feature_name] = []
            feature_lines[feature_name].append(line_number)

        # 计算每个 feature 的 [start, end] 范围
        # end = 下一个 checkpoint 的 line_number - 1（或 total_lines - 1）
        total_lines = base_result.total_lines
        all_line_numbers = [cp.get("line_number", 0) for cp in sorted_cps]

        result: dict[str, tuple[int, int]] = {}
        for feature_name, line_nums in feature_lines.items():
            start_line = min(line_nums)
            # 找到该 feature 最后一个 checkpoint 在 all_line_numbers 中的位置
            last_line_num = max(line_nums)
            last_idx = all_line_numbers.index(last_line_num)
            if last_idx + 1 < len(all_line_numbers):
                end_line = all_line_numbers[last_idx + 1] - 1
            else:
                end_line = total_lines - 1
            # 确保 end_line >= start_line
            if end_line < start_line:
                end_line = start_line
            result[feature_name] = (start_line, end_line)

        return result

    @staticmethod
    def _extract_feature_gcode_lines(
        program_lines: list[str],
        line_range: tuple[int, int],
    ) -> list[str]:
        """从完整 G 代码行列表中提取指定行范围的子片段。

        Args:
            program_lines: 完整 G 代码行列表（按 \\n 切分）
            line_range: (start, end) 行号范围（含两端，0-based）

        Returns:
            该行范围内的 G 代码行列表。若 line_range == (0, 0) 返回空列表。
        """
        start, end = line_range
        if start == 0 and end == 0:
            return []
        if not program_lines:
            return []
        # 边界保护
        start = max(0, min(start, len(program_lines) - 1))
        end = max(start, min(end, len(program_lines) - 1))
        return program_lines[start : end + 1]
