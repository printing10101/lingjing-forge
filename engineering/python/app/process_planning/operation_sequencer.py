"""Operation sequencing engine.

Generates optimized machining operation sequences based on feature
dependency relationships and manufacturing rules. Implements complete
operation planning, fixture suggestions, and time estimation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.process_planning.feature_dependency import (
    FeatureDependencyGraph,
    MachiningFeature,
    Setup,
)
from app.process_planning.datum_selector import DatumSelector
from app.process_planning.fixture_analyzer import (
    FixtureAnalyzer,
    FixtureRecommendation,
)

# 一次钻削的直径上限（mm）。超过此直径的孔规范做法是分级扩孔后镗孔或螺旋铣，
# 不能指望一把小钻头"钻一刀就完成"。超过上限的通孔在工序层自动改走螺旋铣孔
# （见 ``_oversized_hole_diameter``）；若下游仍以钻孔方式调用，则标记
# ``tool_feasible=False``，由 G 代码层拒绝生成。
MAX_DRILL_DIAMETER_MM = 20.0

# 铣削默认刀具直径（mm）。对应知识库中已入库的 φ10 硬质合金平底立铣刀
# （app/database/data/tools.json: endmill_wc_flat_d10），不是凭空的兜底值。
DEFAULT_MILL_DIAMETER_MM = 10.0
_STANDARD_MILL_DIAMETERS_MM: tuple[float, ...] = (10.0, 6.0, 4.0, 3.0, 2.0)


def _circle_contour(
    center_x: float,
    center_y: float,
    radius: float,
    segments: int = 48,
) -> list[list[float]]:
    """生成圆的闭合多段线（供平面刀轨引擎按挖槽环切铣孔）。"""
    if radius <= 0:
        return []
    return [
        [
            round(center_x + radius * math.cos(2.0 * math.pi * i / segments), 6),
            round(center_y + radius * math.sin(2.0 * math.pi * i / segments), 6),
        ]
        for i in range(segments)
    ]


@dataclass
class Operation:
    """A single machining operation in the process plan.

    Attributes:
        seq: Operation sequence number.
        name: Operation name, e.g. 'OP01-Surface A'.
        feature_name: Name of the machining feature this operation processes.
        machining_method: Machining method, e.g. '钻孔', '精铣平面'.
        surface: Surface identifier this operation works on.
        tolerance_grade: Required tolerance grade.
        tool_type: Tool type description.
        cutting_params: Cutting parameters dictionary.
        estimated_time_min: Estimated operation time (minutes).
        notes: Additional operation notes.
    """

    seq: int
    name: str
    feature_name: str
    machining_method: str
    surface: str
    tolerance_grade: str
    tool_type: str = ""
    cutting_params: dict[str, Any] = field(default_factory=dict)
    estimated_time_min: float = 0.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert the operation to a dictionary representation.

        Returns:
            A dictionary containing all operation properties suitable
            for serialization.
        """
        return {
            "seq": self.seq,
            "name": self.name,
            "feature_name": self.feature_name,
            "machining_method": self.machining_method,
            "surface": self.surface,
            "tolerance_grade": self.tolerance_grade,
            "tool_type": self.tool_type,
            "cutting_params": self.cutting_params,
            "estimated_time_min": round(self.estimated_time_min, 2),
            "notes": self.notes,
        }


@dataclass
class OperationPlan:
    """Complete operation plan for a part.

    Attributes:
        operations: Ordered list of machining operations.
        setups: List of fixture setups (one per surface/group).
        estimated_time_min: Total estimated machining time (minutes).
        face_change_count: Number of face changes (setup transitions).
        fixture_recommendations: Recommended fixtures for each setup.
        tool_diameter_mm: 匹配刀具的共识直径（mm，可选）。由管线从阶段 3
            的 MatchedTool 结果推导（全部一致取该值，混杂取最大值——
            直径越大去除越多，对下游体素碰撞仿真越保守）；None 表示
            上游无刀具匹配结果，随 to_dict 导出供阶段 6/7 仿真使用。
    """

    operations: list[Operation] = field(default_factory=list)
    setups: list[Setup] = field(default_factory=list)
    estimated_time_min: float = 0.0
    face_change_count: int = 0
    fixture_recommendations: list[FixtureRecommendation] = field(default_factory=list)
    tool_diameter_mm: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert the operation plan to a dictionary representation.

        Returns:
            A dictionary containing operations, setups, estimated time,
            face change count, and fixture recommendations.
        """
        result: dict[str, Any] = {
            "operations": [op.to_dict() for op in self.operations],
            "setups": [
                {
                    "name": s.name,
                    "surface": s.surface,
                    "fixture_type": s.fixture_type,
                    "datum_features": s.datum_features,
                }
                for s in self.setups
            ],
            "estimated_time_min": round(self.estimated_time_min, 2),
            "face_change_count": self.face_change_count,
            "fixture_recommendations": [fr.to_dict() for fr in self.fixture_recommendations],
        }
        if self.tool_diameter_mm is not None:
            result["tool_diameter_mm"] = self.tool_diameter_mm
        return result


class OperationSequencer:
    """Operation sequencer that generates machining operation plans.

    Uses feature dependency graphs, datum selection, and fixture analysis
    to produce an optimized sequence of machining operations.
    """

    def __init__(self) -> None:
        self._dep_graph = FeatureDependencyGraph()
        self._datum_selector = DatumSelector()
        self._fixture_analyzer = FixtureAnalyzer()

    def plan_operations(
        self,
        features: list[MachiningFeature],
        material: str = "",
        blank_type: str = "",
        part_type: str = "general",
    ) -> OperationPlan:
        """Generate an optimized machining operation plan from a list of features.

        Args:
            features: List of machining features to plan operations for.
            material: Material name for feed rate adjustment.
            blank_type: Blank/stock type.
            part_type: Part type category (general/shaft/plate etc.).

        Returns:
            OperationPlan with ordered operations, setups, and time estimates.
        """
        self._dep_graph.build_graph(features)
        sequence = self._dep_graph.get_machining_sequence()

        operations: list[Operation] = []
        setups: dict[str, Setup] = {}
        seq = 0
        feed_rate_factor = self._get_feed_rate_factor(material)

        for fe in sequence:
            seq += 1
            surface = fe.surface
            if surface not in setups:
                setups[surface] = Setup(
                    name=f"装夹{len(setups) + 1}-面{surface}",
                    surface=surface,
                    fixture_type="",
                )

            method = self._select_machining_method(fe, blank_type)

            # 孔径超过一次钻削上限 → 改走螺旋铣孔（立铣刀环切），
            # 而不是用一把小钻头"钻一刀"就算完成大孔。
            bore_diameter = self._oversized_hole_diameter(fe, method)
            if bore_diameter is not None:
                method = "螺旋铣孔"

            tool = self._select_tool(fe, method)

            # 刀具直径必须来自特征本身（孔径），不能靠下游默认值兜底：
            # 此前这里不写 tool_diameter，G 代码层读到 fallback 10.0，
            # 于是 Ø38.2 / Ø5.5 / Ø6.6 的孔全部用同一把 Ø10 钻头"完成"。
            tool_diameter, tool_feasible, tool_note = self._resolve_hole_tooling(fe, method)
            if tool_diameter is not None:
                # 直径进刀具名 → 不同孔径自动成为不同刀具（不同 T 号）
                tool = f"{tool} Ø{tool_diameter:g}"

            geometry = self._extract_feature_geometry(fe)
            if bore_diameter is not None:
                # 挖槽环切按圆轮廓走刀，刀心轨迹由引擎按刀具半径偏置
                geometry["contour"] = _circle_contour(
                    float(geometry.get("x", 0.0)),
                    float(geometry.get("y", 0.0)),
                    bore_diameter / 2.0,
                )
                geometry["diameter"] = bore_diameter

            cutting_params: dict[str, Any] = {
                "feed_rate_factor": feed_rate_factor,
                "recommended_feed": self._recommend_feed(fe, material),
                "recommended_speed": self._recommend_speed(fe, material),
                "geometry": geometry,
            }
            # 材料随工序下传：G 代码层的参数查库与注释必须用同一份材料名，
            # 此前这里不写，G 代码层一律按 "steel" 查表，与图纸材料脱节。
            if material:
                cutting_params["material"] = material
            if tool_diameter is not None:
                cutting_params["tool_diameter"] = tool_diameter
            # 上游（如 CuttingParamRecommender）已计算并限幅的 S/F 直接下传 G 代码层
            for _k in ("spindle_speed", "feed_rate", "params_source"):
                _val = fe.dimensions.get(_k)
                if _val is not None:
                    cutting_params[_k] = _val
            if not tool_feasible:
                cutting_params["tool_feasible"] = False
                cutting_params["tool_note"] = tool_note

            estimated_time = self._estimate_operation_time(
                fe,
                method,
                feed_rate_factor,
            )

            op = Operation(
                seq=seq,
                name=f"OP{seq:02d}-{fe.name}",
                feature_name=fe.name,
                machining_method=method,
                surface=fe.surface,
                tolerance_grade=fe.tolerance_grade,
                tool_type=tool,
                cutting_params=cutting_params,
                estimated_time_min=estimated_time,
                notes=self._generate_operation_notes(fe, method),
            )
            operations.append(op)

        setup_list = list(setups.values())
        fixture_recommendations = []
        for s in setup_list:
            fr = self._fixture_analyzer.suggest_fixture(s, part_type)
            s.fixture_type = fr.fixture_name
            fixture_recommendations.append(fr)

        total_time = sum(op.estimated_time_min for op in operations)

        return OperationPlan(
            operations=operations,
            setups=setup_list,
            estimated_time_min=total_time,
            face_change_count=len(setups) - 1 if len(setups) > 1 else 0,
            fixture_recommendations=fixture_recommendations,
        )

    def suggest_fixture(
        self,
        setup: Setup,
        part_type: str = "general",
    ) -> FixtureRecommendation:
        """Suggest a fixture for a given setup.

        Args:
            setup: Setup to suggest a fixture for.
            part_type: Part type category.

        Returns:
            FixtureRecommendation with fixture details and suitability score.
        """
        return self._fixture_analyzer.suggest_fixture(setup, part_type)

    def _select_machining_method(
        self,
        fe: MachiningFeature,
        blank_type: str,
    ) -> str:
        is_rough = fe.tolerance_grade in ("IT9", "IT10", "IT11", "IT12", "IT13", "IT14")

        if fe.type in ("end_face",):
            return "粗车端面" if is_rough else "精车端面"
        if fe.type in ("outer_cylinder",):
            return "粗车外圆" if is_rough else "精车外圆"
        if fe.type in ("inner_bore",):
            return "粗镗内孔" if is_rough else "精镗内孔"
        if fe.type in ("through_hole",):
            return "钻孔"
        if fe.type in ("center_hole",):
            return "钻中心孔"
        if fe.type in ("groove",):
            return "切槽"
        if fe.type in ("chamfer",):
            return "倒角"
        if fe.type in ("plane_surface",):
            return "粗铣平面" if is_rough else "精铣平面"
        if fe.type in ("through_pocket", "blind_pocket", "pocket"):
            # 挖槽特征必须走铣削分支（此前落入"精加工-{type}"通用分支，不生成 G 代码）
            return "粗铣挖槽" if is_rough else "精铣挖槽"
        if fe.type in ("rectangular_boss", "circular_boss", "boss"):
            # 凸台特征走外形铣削分支（外轮廓偏置刀轨）
            return "粗铣外形" if is_rough else "精铣外形"
        if fe.type in ("outer_contour", "part_outline", "profile"):
            # 零件外轮廓（DXF/STEP 的最大闭合环）：沿边界偏置走刀把零件切出来。
            # 与凸台同一走刀族（都按外轮廓偏移），但语义不同，单独一档，
            # 便于工序表/审计里区分「外形轮廓铣」和「凸台铣」。
            return "粗铣外形" if is_rough else "精铣外形"
        if fe.type in ("keyway",):
            return "铣键槽"
        if fe.type in ("slot",):
            return "铣槽"
        if fe.type in ("step",):
            return "车台阶"
        if fe.type in ("counterbore",):
            return "钻沉头孔"

        if is_rough:
            return f"粗加工-{fe.type}"
        return f"精加工-{fe.type}"

    def _extract_feature_geometry(self, fe: MachiningFeature) -> dict[str, Any]:
        """从特征尺寸提取铣削几何，供平面刀轨引擎构造刀心轨迹。

        键语义：
        - x/y: 轮廓中心或角点坐标（由 anchor 区分）
        - anchor: "center"（x/y 为中心，挖槽/凸台）或 "corner"（x/y 为角点，平面）
        - length/width: 包络尺寸 (mm)
        - depth: 加工深度，正值向下 (mm)
        - orientation: 旋转角（度，绕中心，可选）
        - diameter/height: 凸台类特征的直径/高度（透传，供参考）

        钻孔类特征（diameter/depth/position_*）不在此注入，保持钻孔分支
        现有坐标来源不变。
        """
        dims = fe.dimensions or {}

        # 真实闭合轮廓优先：有 contour 时刀轨引擎按多边形做偏置（挖槽）或
        # 外形走刀，length/width 退化为包络参考值。即使 dims 为空也要带上。
        contour = getattr(fe, "contour", None)
        pts: list[list[float]] = []
        if contour:
            try:
                pts = [[float(p[0]), float(p[1])] for p in contour]
            except (TypeError, ValueError, IndexError):
                pts = []
            if len(pts) < 3:
                pts = []

        if not dims:
            return {"contour": pts} if pts else {}

        geom: dict[str, Any] = {}
        for key in ("length", "width", "depth", "orientation", "diameter", "height"):
            if key in dims:
                try:
                    geom[key] = float(dims[key])
                except (TypeError, ValueError):
                    continue

        # 毛坯余量透传给刀轨层：决定基准面光刀切多深（0 = 不切）。
        if "face_allowance" in dims:
            try:
                geom["face_allowance_mm"] = float(dims["face_allowance"])
            except (TypeError, ValueError):
                pass

        center_x = dims.get("center_x", dims.get("position_x"))
        center_y = dims.get("center_y", dims.get("position_y"))
        if center_x is not None and center_y is not None:
            geom["x"] = float(center_x)
            geom["y"] = float(center_y)
            geom["anchor"] = "center"
        else:
            geom.setdefault("x", 0.0)
            geom.setdefault("y", 0.0)
            geom["anchor"] = "corner"

        # 真实闭合轮廓优先：有 contour 时刀轨引擎按多边形做偏置（挖槽）或
        # 外形走刀，length/width 退化为包络参考值。
        if pts:
            geom["contour"] = pts
        return geom

    def _oversized_hole_diameter(
        self,
        fe: MachiningFeature,
        method: str,
    ) -> float | None:
        """孔径超过一次钻削上限时返回孔径，否则 None。

        对所有打算用钻/镗单刃加工的大孔（Ø>20mm，含沉头孔）统一改走螺旋铣孔，
        仅排除中心孔（中心钻规范最大 Ø6mm）；避免大孔被误判拒绝。
        """
        if ("钻" not in method and "镗" not in method) or "中心" in method:
            return None
        try:
            diameter = float(fe.dimensions.get("diameter") or 0.0)
        except (TypeError, ValueError):
            return None
        if diameter > MAX_DRILL_DIAMETER_MM:
            return diameter
        return None

    @staticmethod
    def _select_mill_diameter(fe: MachiningFeature) -> float:
        """为铣削/挖槽工序匹配能放入几何内部的最大标准立铣刀直径。"""
        dims = fe.dimensions or {}
        contour = dims.get("contour")
        # 优先按真实闭合多边形内缩测试（仅针对内腔/槽，外轮廓不需要内切限制）
        if isinstance(contour, list) and len(contour) >= 3 and ("槽" in fe.type or "腔" in fe.type or "pocket" in fe.type.lower()):
            try:
                from shapely.geometry import Polygon

                poly = Polygon([(float(p[0]), float(p[1])) for p in contour])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if not poly.is_empty and poly.area > 0:
                    for dia in _STANDARD_MILL_DIAMETERS_MM:
                        eroded = poly.buffer(-dia / 2.0 * 1.02)
                        if not eroded.is_empty:
                            return dia
                    return _STANDARD_MILL_DIAMETERS_MM[-1]
            except Exception:
                pass
        # 无多边形或非型腔：若有显式窄宽尺寸且 < 10mm，按短边降级
        try:
            width_val = float(dims.get("width") or 0.0)
            length_val = float(dims.get("length") or 0.0)
            span = min(v for v in (width_val, length_val) if v > 0) if (width_val > 0 or length_val > 0) else 0.0
            if 0 < span < DEFAULT_MILL_DIAMETER_MM and ("槽" in fe.type or "腔" in fe.type or "pocket" in fe.type.lower()):
                for dia in _STANDARD_MILL_DIAMETERS_MM:
                    if dia <= span * 0.95:
                        return dia
                return _STANDARD_MILL_DIAMETERS_MM[-1]
        except (TypeError, ValueError):
            pass
        return DEFAULT_MILL_DIAMETER_MM

    def _resolve_hole_tooling(
        self,
        fe: MachiningFeature,
        method: str,
    ) -> tuple[float | None, bool, str]:
        """确定孔加工的刀具直径与可行性。

        直径取自特征自身的 ``dimensions["diameter"]``（即图纸孔径），因为这正是
        钻头必须匹配的量；知识库里的刀具族只给区间，无法当成具体规格。

        Args:
            fe: 加工特征。
            method: 加工方法名。

        Returns:
            ``(tool_diameter_mm, feasible, note)``。铣削工序按几何宽窄选标准立铣刀；
            车削等不参与直径约束的工序返回 ``(None, True, "")``。
        """
        if "钻" not in method and "镗" not in method:
            if "铣" in method or "槽" in method:
                return self._select_mill_diameter(fe), True, ""
            return None, True, ""

        try:
            hole_diameter = float(fe.dimensions.get("diameter") or 0.0)
        except (TypeError, ValueError):
            hole_diameter = 0.0
        if not (hole_diameter > 0):
            return None, False, "孔直径缺失，无法确定刀具规格"

        if "中心" in method:
            # 中心钻常用 A 型规格上限 6mm，孔径小于该值时按孔径取
            return round(min(hole_diameter, 6.0), 2), True, ""

        if hole_diameter > MAX_DRILL_DIAMETER_MM:
            return None, False, (
                f"孔径 Ø{hole_diameter:.1f} 超过一次钻削上限 Ø{MAX_DRILL_DIAMETER_MM:.1f}"
                "（规范做法为分级扩孔后镗孔/螺旋铣，当前未实现）"
            )
        return round(hole_diameter, 2), True, ""

    def _select_tool(self, fe: MachiningFeature, method: str) -> str:
        if "车" in method:
            if "粗" in method:
                return "外圆车刀(粗)"
            return "外圆车刀(精)"
        if "镗" in method:
            return "镗刀"
        if "钻" in method:
            if "中心" in method:
                return "中心钻"
            return "麻花钻"
        if "切槽" in method:
            return "切槽刀"
        if "倒角" in method:
            return "倒角刀"
        if "铣" in method:
            return "立铣刀"
        return "通用刀具"

    def _estimate_operation_time(
        self,
        fe: MachiningFeature,
        method: str,
        feed_factor: float = 1.0,
    ) -> float:
        base_time = 2.0
        if "粗" in method:
            base_time = 4.0
        elif "精" in method:
            base_time = 3.0
        if "车" in method:
            base_time *= 1.0
        elif "钻" in method:
            base_time *= 0.8
        elif "铣" in method:
            base_time *= 1.3

        dim_factor = 1.0
        dia = fe.dimensions.get("diameter", 50)
        if dia > 100:
            dim_factor = 1.5
        elif dia > 50:
            dim_factor = 1.2

        return round(base_time * dim_factor * feed_factor, 2)

    def _get_feed_rate_factor(self, material: str) -> float:
        if not material:
            return 1.0
        ml = material.lower()
        if "aluminum" in ml or "铝合金" in ml:
            return 0.7
        if "titanium" in ml or "钛合金" in ml:
            return 1.5
        if "stainless" in ml or "不锈钢" in ml:
            return 1.3
        if "cast_iron" in ml or "铸铁" in ml:
            return 0.8
        return 1.0

    def _recommend_feed(self, fe: MachiningFeature, material: str) -> str:
        if fe.type in ("outer_cylinder", "end_face"):
            if fe.is_finish():
                return "0.05-0.15 mm/rev"
            return "0.15-0.35 mm/rev"
        if fe.is_hole():
            return "0.05-0.15 mm/rev"
        if fe.is_face():
            return "0.05-0.20 mm/tooth"
        return "0.10-0.25 mm/rev"

    def _recommend_speed(self, fe: MachiningFeature, material: str) -> str:
        if not material:
            return "80-200 m/min"
        ml = material.lower()
        if "aluminum" in ml or "铝合金" in ml:
            return "300-800 m/min"
        if "titanium" in ml or "钛合金" in ml:
            return "30-60 m/min"
        if "stainless" in ml or "不锈钢" in ml:
            return "50-100 m/min"
        if "cast_iron" in ml or "铸铁" in ml:
            return "80-150 m/min"
        return "80-200 m/min"

    def _generate_operation_notes(
        self,
        fe: MachiningFeature,
        method: str,
    ) -> str:
        notes_parts = []
        if fe.is_datum_candidate:
            notes_parts.append("定位基准面，确保加工精度")
        if fe.tolerance_grade in ("IT5", "IT6", "IT7"):
            notes_parts.append(f"精密加工，公差等级{fe.tolerance_grade}")
        if "粗" in method:
            notes_parts.append("留余量0.5-1.0mm")
        if "精" in method:
            notes_parts.append("最终尺寸，使用切削液")
        if fe.is_hole():
            notes_parts.append("使用中心钻定位后钻孔")
        return "; ".join(notes_parts) if notes_parts else "标准加工"
