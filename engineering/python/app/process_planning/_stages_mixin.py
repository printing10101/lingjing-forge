"""工艺规划流水线阶段实现 mixin（从 pipeline 拆出）。"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.process_planning.boss_recognizer import BossFeature
from app.process_planning.cavity_recognizer import CavityFeature
from app.process_planning.feature_dependency import MachiningFeature
from app.process_planning.hole_recognizer import HoleRecognitionResult
from app.process_planning.operation_sequencer import OperationPlan
from app.process_planning.plane_recognizer import PlaneFeature
from app.process_planning.sim_integration import SimulationIntegration
from app.process_planning.tool_param_matcher import HoleProcessPlan
from app.process_planning._stages import PipelineResult, PipelineStage

logger = logging.getLogger(__name__)

# 识别件（型腔/凸台/平面）统一转 MachiningFeature 字典的口径：
# 三类对象都有 to_machining_feature()；上游（如 DXF 轮廓桥）也可直接传字典。
_HAS_TO_MF = (CavityFeature, BossFeature, PlaneFeature)


def _as_feature_dict(item: Any) -> dict[str, Any] | None:
    """把识别件对象或特征字典归一为 MachiningFeature 构造入参字典。"""
    if isinstance(item, dict):
        return item
    if isinstance(item, _HAS_TO_MF):
        return item.to_machining_feature()
    logger.warning("[特征构建跳过] 无法识别的特征类型: %s。建议操作：传 dict 或识别件对象", type(item).__name__)
    return None


def _build_machining_feature(d: dict[str, Any]) -> MachiningFeature:
    """按统一口径构造 MachiningFeature（缺字段用保守默认，不抛 KeyError）。

    历史实现用 d["name"]/d["type"]… 硬取，上游少给一个键就整条流水线炸；
    这里改为带默认值读取，并把真实轮廓顶点 contour 一并带上。
    """
    return MachiningFeature(
        name=str(d.get("name", "")),
        type=str(d.get("type", "")),
        geometric_type=str(d.get("geometric_type", "")),
        tolerance_grade=str(d.get("tolerance_grade", "IT8")),
        surface_roughness_ra=float(d.get("surface_roughness_ra", 6.3)),
        is_datum_candidate=bool(d.get("is_datum_candidate", False)),
        priority=str(d.get("priority", "medium")),
        surface=str(d.get("surface", "A")),
        dimensions=dict(d.get("dimensions") or {}),
        parent_feature=str(d.get("parent_feature", "")),
        tolerances=dict(d.get("tolerances") or {}),
        contour=d.get("contour"),
    )


class _StagesMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _data_valid: Any

    def _validate_input(self, part_description: dict[str, Any]) -> PipelineStage:
        """验证输入数据的完整性和格式。

        校验项：
        1. part_description 不能为 None/空
        2. material 字段必须存在且非空
        3. holes 字段应为列表类型
        4. 知识库是否成功加载
        """
        stage = PipelineStage(name="输入验证")
        errors = []
        warnings = []

        if part_description is None:
            errors.append("零件描述数据为None")
            stage.status = "failed"
            stage.errors = errors
            return stage

        if not isinstance(part_description, dict):
            errors.append(f"零件描述数据类型无效: {type(part_description).__name__}, 应为dict")
            stage.status = "failed"
            stage.errors = errors
            return stage

        if not part_description:
            errors.append("零件描述数据为空字典")
            stage.status = "failed"
            stage.errors = errors
            return stage

        material = part_description.get("material", "")
        if not material:
            errors.append("缺少必需的'material'字段——请指定零件材料")
        else:
            stage.input_summary = f"材料: {material}"

        holes = part_description.get("holes", part_description.get("features", []))
        if not isinstance(holes, list):
            errors.append("'holes'字段应为数组类型")
        else:
            stage.input_summary += f", 孔数据: {len(holes)}条"

        if not self._data_valid:
            warnings.append("工艺知识库加载失败，将使用内置默认值进行刀具和参数匹配")

        stage.status = "success" if not errors else "failed"
        stage.errors = errors
        stage.warnings = warnings
        return stage

    def _consensus_tool_diameter(self, process_plans: list[HoleProcessPlan]) -> float | None:
        """从阶段 3 的刀具匹配结果推导共识刀具直径（mm）。

        规则（宁保守不乐观）：
        - 无任何匹配刀具 → None（下游回退配置默认值并如实标注）；
        - 全部一致（0.01mm 容差）→ 该值；
        - 混杂 → 取最大值：直径越大材料去除越多，对体素碰撞仿真越保守。

        Returns:
            共识直径，或 None（无可信来源）。
        """
        diameters = [
            float(t.tool.diameter_mm)
            for plan in process_plans
            for t in plan.tools
            if t.tool.diameter_mm and float(t.tool.diameter_mm) > 0
        ]
        if not diameters:
            return None
        if max(diameters) - min(diameters) <= 0.01:
            return round(diameters[0], 2)
        return round(max(diameters), 2)

    def _build_features(
        self,
        hole_result: HoleRecognitionResult,
        process_plans: list[HoleProcessPlan],
        part_description: dict[str, Any],
    ) -> list[MachiningFeature]:
        features: list[MachiningFeature] = []

        # 基准面（毛坯上表面）尺寸必须来自零件实际包络，不能写死。
        # 此前硬编码 length=200 / width=100，导致 face_raster 对任何零件都
        # 扫同一个 200×100 区域：小件扫到工件外、大件扫不到边界，
        # 且让「刀轨由真实几何生成」这句对外口径失真。
        # 缺 overall_dimensions 时回退旧值（保持既有调用方与测试的兼容口径）。
        overall = part_description.get("overall_dimensions") or {}
        try:
            blank_length = float(overall.get("length") or 0.0)
            blank_width = float(overall.get("width") or 0.0)
        except (TypeError, ValueError):
            blank_length = blank_width = 0.0
        if blank_length <= 0 or blank_width <= 0 or not (math.isfinite(blank_length) and math.isfinite(blank_width)):
            blank_length, blank_width = 200.0, 100.0
        try:
            blank_x0 = float(overall.get("min_x") or 0.0)
            blank_y0 = float(overall.get("min_y") or 0.0)
        except (TypeError, ValueError):
            blank_x0 = blank_y0 = 0.0
        if not (math.isfinite(blank_x0) and math.isfinite(blank_y0)):
            blank_x0 = blank_y0 = 0.0

        # 下游 `_extract_feature_geometry` 用 center_x/center_y 判定 anchor="center"，
        # 而刀轨引擎的 raster rect = 中心 ± length/2、width/2。所以这里必须传**中心**，
        # 传角点会把整张面铣往 −L/2,−W/2 方向平移半个零件（实测 case6 因此有 83%
        # 运动点跑到毛坯网格外，阶段 7 的碰撞检查对这样的程序几乎是在空转）。
        features.append(
            MachiningFeature(
                name="基准面A-上表面",
                type="plane_surface",
                geometric_type="plane",
                tolerance_grade="IT7",
                surface_roughness_ra=1.6,
                is_datum_candidate=True,
                priority="high",
                surface="A",
                dimensions={
                    "area": round(blank_length * blank_width, 3),
                    "length": blank_length,
                    "width": blank_width,
                    "center_x": blank_x0 + blank_length / 2.0,
                    "center_y": blank_y0 + blank_width / 2.0,
                },
            )
        )

        for hole, plan in zip(hole_result.holes, process_plans):
            hole_dict = hole.to_machining_feature()

            mf = MachiningFeature(
                name=hole.hole_id,
                type=hole_dict["type"],
                geometric_type="cylinder",
                tolerance_grade=hole_dict["tolerance_grade"],
                surface_roughness_ra=hole.surface_roughness_ra,
                is_datum_candidate=hole.type == "center_hole",
                priority=hole_dict["priority"],
                surface=hole.surface,
                dimensions={
                    "diameter": hole.diameter,
                    "depth": hole.depth,
                    "position_x": hole.position_x,
                    "position_y": hole.position_y,
                },
            )
            features.append(mf)

        cavity_features = part_description.get("cavities", [])
        for cav in cavity_features:
            d = _as_feature_dict(cav)
            if d is None:
                continue
            features.append(_build_machining_feature(d))

        boss_features = part_description.get("bosses", [])
        for boss in boss_features:
            d = _as_feature_dict(boss)
            if d is None:
                continue
            features.append(_build_machining_feature(d))

        # 零件外轮廓（DXF 最大闭合环）：把零件从板上切下来。与凸台同走刀族
        # （外轮廓偏置），但语义与工序命名分开，便于工序表/审计区分。
        outline_features = part_description.get("outlines", [])
        for outline in outline_features:
            d = _as_feature_dict(outline)
            if d is None:
                continue
            features.append(_build_machining_feature(d))

        plane_features = part_description.get("planes", [])
        for plane in plane_features:
            d = _as_feature_dict(plane)
            if d is None:
                continue
            features.append(_build_machining_feature(d))

        return features

    def _validate_pipeline_output(
        self,
        result: PipelineResult,
    ) -> tuple[list[str], list[str]]:
        """校验流水线输出的完整性和正确性。

        校验项：
        1. G代码程序非空
        2. G代码程序语法校验通过
        3. 孔识别结果可靠（准确率达标）
        4. 工序规划至少包含1个工序
        """
        errors: list[str] = []
        warnings: list[str] = []

        if result.hole_recognition:
            hr = result.hole_recognition
            if not hr.is_reliable:
                warnings.append(f"孔识别可靠性偏低: {hr.accuracy_metrics.get('overall', 0):.1%}")
            if hr.warnings:
                warnings.extend(hr.warnings)

        if result.operation_plan:
            op_plan = result.operation_plan
            if not op_plan.operations:
                errors.append("工序规划结果为空")
            if len(op_plan.setups) == 0:
                warnings.append("未生成装夹方案")
        else:
            errors.append("缺少工序规划结果")

        if result.gcode_result:
            gc = result.gcode_result
            if not gc.program_text or len(gc.program_text.strip()) < 10:
                errors.append("G代码程序过短（可能生成失败）")
            if gc.errors:
                errors.extend(gc.errors)
            if gc.warnings:
                warnings.extend(gc.warnings)
            if gc.tool_count == 0:
                warnings.append("未使用任何刀具（可能为仅回零程序）")
        else:
            errors.append("缺少G代码生成结果")

        return errors, warnings

    def _run_simulation(
        self,
        material: str,
        operation_plan: OperationPlan,
    ) -> dict[str, Any]:
        """运行仿真验证。

        调用仿真集成器对工序规划结果进行切削力和颤振稳定性分析。

        Args:
            material: 材料名称
            operation_plan: 工序规划结果

        Returns:
            包含仿真结果的字典，包括：
            - status: 仿真状态 ('success', 'timeout', 'failed', 'not_run')
            - score: 仿真评分 (0-100)
            - passed: 是否通过仿真
            - recommendation: 推荐级别 ('recommended', 'acceptable', 'not_recommended')
            - cutting_force: 切削力预测结果
            - chatter_stability: 颤振稳定性分析结果
            - duration_ms: 仿真耗时(毫秒)
        """
        try:
            simulator = SimulationIntegration(timeout_seconds=5.0)

            # 从工序规划中提取典型加工参数
            # 使用第一个工序的参数作为代表（如有多个工序，可考虑加权平均）
            if operation_plan.operations:
                first_op = operation_plan.operations[0]
                # 从工序中提取切削参数（如果存在）
                cutting_params = first_op.cutting_params
                spindle_rpm = cutting_params.get("spindle_rpm", 8000)
                feed_rate = cutting_params.get("feed_rate", 1200)
                depth_of_cut = cutting_params.get("depth_of_cut", 2.0)
                tool = first_op.tool_type or "endmill_d10"
            else:
                # 默认参数
                spindle_rpm = 8000
                feed_rate = 1200
                depth_of_cut = 2.0
                tool = "endmill_d10"

            # 运行仿真
            sim_result = simulator.run_simulation(
                material=material,
                tool=tool,
                spindle_rpm=spindle_rpm,
                feed_rate=feed_rate,
                depth_of_cut=depth_of_cut,
            )

            return sim_result.to_dict()

        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            # 仿真失败时返回降级结果，不阻断主流程
            logger.error("仿真服务调用失败: %s", e, exc_info=True)
            return {
                "status": "failed",
                "score": 0.0,
                "passed": False,
                "recommendation": "not_recommended",
                "cutting_force": None,
                "chatter_stability": None,
                "duration_ms": 0.0,
                "error_message": f"仿真服务调用失败: {type(e).__name__}",
            }
