"""平面刀轨引擎（2.5D）单元测试与 G 代码集成测试。

覆盖：
- 轮廓偏置正确性（内缩/外扩量 = 刀具半径）
- 环切挖槽的可加工区域全覆盖（圆刀铣不到的壁面过渡带除外）
- 窄槽/退化输入的拒绝与回退语义
- Z 分层、斜坡下刀（含开放路径拉锯折返）的正确性
- OperationSequencer 特征几何注入与铣削方法映射
- GCodeGenerator 端到端：引擎路径替换模板走线、无几何时保持旧行为
"""

from __future__ import annotations

import numpy as np
import pytest

from app.process_planning.gcode_generator import GCodeGenerator
from app.process_planning.operation_sequencer import (
    MachiningFeature,
    Operation,
    OperationPlan,
    OperationSequencer,
)
from app.toolpath.planar_engine import (
    MillingToolpath,
    PlanarToolpathEngine,
    PlanarToolpathError,
    PocketTooNarrowError,
    ToolpathScaleExceededError,
    _min_dist_to_polygon,
    _offset_polygon,
    _signed_area,
)

pytestmark = pytest.mark.unit

RECT_40x20 = np.array([[80.0, 90.0], [120.0, 90.0], [120.0, 110.0], [80.0, 110.0]])
L_SHAPE = np.array(
    [[0.0, 0.0], [40.0, 0.0], [40.0, 20.0], [20.0, 20.0], [20.0, 40.0], [0.0, 40.0]]
)
U_SHAPE = np.array(
    [
        [0.0, 0.0], [50.0, 0.0], [50.0, 40.0], [30.0, 40.0],
        [30.0, 15.0], [20.0, 15.0], [20.0, 40.0], [0.0, 40.0],
    ]
)
T_SHAPE = np.array(
    [
        [0.0, 0.0], [60.0, 0.0], [60.0, 15.0], [35.0, 15.0],
        [35.0, 50.0], [25.0, 50.0], [25.0, 15.0], [0.0, 15.0],
    ]
)


def _engine() -> PlanarToolpathEngine:
    return PlanarToolpathEngine(tool_diameter=10.0, feed_rate=300, stepdown=3.0)


def _point_in_poly(p: np.ndarray, poly: np.ndarray) -> bool:
    x, y = p
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0.0, 1.0)
    return float(np.hypot(*(p - (a + t * ab))))


def _worst_uncovered(tp: MillingToolpath, poly: np.ndarray, n: int = 30, r: float = 5.0) -> float:
    """可加工区域（材料内部且距壁面 >= 刀具半径）内点到刀轨线段的最大距离。

    物理背景：圆截面刀具无法切出方形内角，壁面半径过渡带必然残留，
    因此全覆盖判定只针对 eroded-by-r 区域。
    """
    mv = [(m.x, m.y) for m in tp.moves if m.kind == "cut"]
    segs = [(np.array(a), np.array(b)) for a, b in zip(mv[:-1], mv[1:])]
    assert segs, "刀轨缺少切削移动"
    bb0 = poly.min(axis=0) + 0.01
    bb1 = poly.max(axis=0) - 0.01
    worst = 0.0
    for x in np.linspace(bb0[0], bb1[0], n):
        for y in np.linspace(bb0[1], bb1[1], n):
            p = np.array([x, y])
            if not _point_in_poly(p, poly):
                continue
            if _min_dist_to_polygon(p, poly) < r - 0.01:
                continue
            worst = max(worst, min(_seg_dist(p, a, b) for a, b in segs))
    return worst


class TestOffsetGeometry:
    def test_inward_offset_distance(self):
        """内缩后环上每点到原轮廓距离 = 偏置量。"""
        cand = _offset_polygon(RECT_40x20, 5.0, 4.0)
        for v in cand:
            assert abs(_min_dist_to_polygon(v, RECT_40x20) - 5.0) < 1e-6

    def test_inward_offset_dims(self):
        """40×20 内缩 5 → 30×10 环。"""
        cand = _offset_polygon(RECT_40x20, 5.0, 4.0)
        assert np.allclose(cand.min(axis=0), [85, 95])
        assert np.allclose(cand.max(axis=0), [115, 105])

    def test_outward_offset_direction(self):
        """负偏置（外形）向轮廓外侧扩张且保持 CCW。"""
        poly = np.array([[0.0, 0.0], [30.0, 0.0], [30.0, 30.0], [0.0, 30.0]])
        cand = _offset_polygon(poly, -5.0, 4.0)
        assert np.allclose(cand.min(axis=0), [-5, -5])
        assert np.allclose(cand.max(axis=0), [35, 35])
        assert _signed_area(cand) > 0

    def test_miter_limit_clamps_sharp_corner(self):
        """尖角 miter 超限被截断，避免偏置点飞出。"""
        spike = np.array([[0.0, 0.0], [40.0, 0.0], [40.0, 0.2], [0.0, 0.2]])
        cand = _offset_polygon(spike, 2.0, miter_limit=2.0)
        assert np.all(np.isfinite(cand))
        for v in cand:
            assert _min_dist_to_polygon(v, spike) < 10.0  # 无飞点


class TestPocket:
    def test_rect_ring_bounds(self):
        tp = _engine().pocket(RECT_40x20, 50.0, 44.0)
        xs = [m.x for m in tp.moves if m.kind == "cut"]
        ys = [m.y for m in tp.moves if m.kind == "cut"]
        assert min(xs) >= 85.0 - 1e-6 and max(xs) <= 115.0 + 1e-6
        assert min(ys) >= 95.0 - 1e-6 and max(ys) <= 105.0 + 1e-6
        assert tp.ring_count == 2  # d=5 环 + d=10 中线清底 slit

    def test_full_coverage_various_shapes(self):
        for poly in (RECT_40x20, L_SHAPE, U_SHAPE, T_SHAPE):
            tp = _engine().pocket(poly, 10.0, 7.5)
            worst = _worst_uncovered(tp, poly, n=25)
            assert worst <= 5.0 + 1e-3, f"覆盖漏洞 {worst:.3f}mm: {poly.tolist()}"

    def test_narrow_pocket_rejected(self):
        """槽宽 8 < 刀径 10 → 明确报错（调用方回退钻削/换刀）。"""
        narrow = np.array([[0.0, 0.0], [20.0, 0.0], [20.0, 8.0], [0.0, 8.0]])
        with pytest.raises(PocketTooNarrowError):
            _engine().pocket(narrow, 10.0, 5.0)

    def test_cw_input_normalized(self):
        cw = RECT_40x20[::-1].copy()
        tp = _engine().pocket(cw, 50.0, 44.0)
        assert tp.ring_count == 2

    def test_z_semantics_error(self):
        with pytest.raises(PlanarToolpathError):
            _engine().pocket(RECT_40x20, 40.0, 45.0)  # 底高于顶


class TestProfileAndRaster:
    def test_profile_outside_offset(self):
        poly = np.array([[0.0, 0.0], [30.0, 0.0], [30.0, 30.0], [0.0, 30.0]])
        tp = _engine().profile(poly, 10.0, 8.0)
        xs = [m.x for m in tp.moves if m.kind == "cut"]
        ys = [m.y for m in tp.moves if m.kind == "cut"]
        assert min(xs) <= -5.0 + 1e-6 and max(xs) >= 35.0 - 1e-6
        assert min(ys) <= -5.0 + 1e-6 and max(ys) >= 35.0 - 1e-6

    def test_profile_with_allowance(self):
        poly = np.array([[0.0, 0.0], [30.0, 0.0], [30.0, 30.0], [0.0, 30.0]])
        tp = _engine().profile(poly, 10.0, 8.0, finish_allowance=0.5)
        xs = [m.x for m in tp.moves if m.kind == "cut"]
        assert max(xs) >= 35.5 - 1e-6

    def test_raster_rows_and_serpentine(self):
        tp = _engine().face_raster(0.0, 0.0, 60.0, 40.0, 20.0, 18.0)
        assert tp.strategy == "face_raster"
        assert tp.ring_count >= 2
        ys = sorted({round(m.y, 3) for m in tp.moves if m.kind == "cut"})
        assert ys[0] == 5.0  # 内缩刀具半径
        # 蛇形：相邻行起点交替
        firsts = []
        seen_rows: dict[float, float] = {}
        for m in tp.moves:
            if m.kind != "cut":
                continue
            key = round(m.y, 3)
            if key not in seen_rows:
                seen_rows[key] = m.x
                firsts.append(m.x)
        assert firsts[0] != firsts[1], "相邻行应交替方向"

    def test_raster_ramp_zigzag_returns_to_start(self):
        tp = _engine().face_raster(0.0, 0.0, 60.0, 40.0, 20.0, 18.0)
        plunges = [(m.x, m.y, m.z) for m in tp.moves if m.kind == "plunge"]
        first_row = [p for p in plunges if abs(p[1] - 5.0) < 1e-9]
        assert first_row[-1][0] == 5.0  # 拉锯末点回到行起点
        assert abs(first_row[-1][2] - 18.0) < 1e-9
        zs = [p[2] for p in first_row]
        assert all(zs[i] >= zs[i + 1] - 1e-9 for i in range(len(zs) - 1))


class TestZLevelsAndRamp:
    def test_z_levels_exact_bottom(self):
        levels = _engine()._z_levels(50.0, 44.0)
        assert levels == pytest.approx([47.0, 44.0])
        levels2 = _engine()._z_levels(50.0, 43.5)
        assert levels2[-1] == 43.5

    def test_ramp_monotonic_z_and_endpoints(self):
        eng = _engine()
        tp = eng.pocket(RECT_40x20, 50.0, 44.0)
        plunges = [m for m in tp.moves if m.kind == "plunge"]
        assert plunges, "应有斜坡下刀"
        # 第一环首层：从清除面斜坡降至第一层，Z 非增
        first_lap = plunges[:8]
        zs = [m.z for m in first_lap]
        assert all(zs[i] >= zs[i + 1] - 1e-9 for i in range(len(zs) - 1))

    def test_raster_ramp_interpolates_depth(self):
        """行内拉锯斜坡：中间拐点 Z 应介于起止之间。"""
        eng = _engine()
        tp = eng.face_raster(0.0, 0.0, 60.0, 40.0, 20.0, 18.0)
        first_row = [m for m in tp.moves if m.kind == "plunge" and abs(m.y - 5.0) < 1e-9]
        assert len(first_row) >= 2
        assert first_row[0].z > first_row[-1].z


class TestErrorsAndParams:
    def test_invalid_tool_diameter(self):
        with pytest.raises(ValueError):
            PlanarToolpathEngine(tool_diameter=0, feed_rate=300)

    def test_invalid_stepover_ratio(self):
        with pytest.raises(ValueError):
            PlanarToolpathEngine(tool_diameter=10, feed_rate=300, stepover_ratio=1.5)

    def test_scale_limit(self):
        eng = PlanarToolpathEngine(tool_diameter=10.0, feed_rate=300, max_moves=10)
        with pytest.raises(ToolpathScaleExceededError):
            eng.pocket(RECT_40x20, 50.0, 20.0)

    def test_bad_polygon_inputs(self):
        eng = _engine()
        with pytest.raises(PlanarToolpathError):
            eng.pocket(np.array([[0.0, 0.0], [1.0, 1.0]]), 10.0, 5.0)
        with pytest.raises(PlanarToolpathError):
            eng.pocket(np.array([[0.0, 0.0], [10.0, 0.0], [5.0, np.nan]]), 10.0, 5.0)

    def test_summary_consistent(self):
        tp = _engine().pocket(RECT_40x20, 50.0, 44.0)
        s = tp.summary()
        assert s["moves"] == len(tp.moves)
        assert s["rings"] == tp.ring_count
        assert tp.est_cut_time_min > 0


class TestSequencerIntegration:
    def test_pocket_method_mapping(self):
        """挖槽特征必须映射到铣削分支（此前落入通用分支不生成 G 代码）。"""
        seq = OperationSequencer()
        fe = MachiningFeature(
            name="C001",
            type="blind_pocket",
            dimensions={"length": 40, "width": 20, "depth": 6, "center_x": 100, "center_y": 80},
        )
        plan = seq.plan_operations([fe])
        assert plan.operations[0].machining_method == "精铣挖槽"

    def test_boss_method_mapping(self):
        seq = OperationSequencer()
        fe = MachiningFeature(
            name="B001",
            type="rectangular_boss",
            dimensions={"side_length": 30, "height": 10, "center_x": 50, "center_y": 50},
        )
        plan = seq.plan_operations([fe])
        # IT8（默认）为精加工档
        assert plan.operations[0].machining_method == "精铣外形"

        fe_rough = MachiningFeature(
            name="B002",
            type="rectangular_boss",
            tolerance_grade="IT11",
            dimensions={"side_length": 30, "height": 10, "center_x": 50, "center_y": 50},
        )
        plan_rough = seq.plan_operations([fe_rough])
        assert plan_rough.operations[0].machining_method == "粗铣外形"

    def test_geometry_injection(self):
        seq = OperationSequencer()
        fe = MachiningFeature(
            name="C001",
            type="through_pocket",
            dimensions={"length": 40, "width": 20, "depth": 6, "center_x": 100, "center_y": 80},
        )
        plan = seq.plan_operations([fe])
        geom = plan.operations[0].cutting_params["geometry"]
        assert geom["anchor"] == "center"
        assert geom["x"] == 100.0 and geom["y"] == 80.0
        assert geom["length"] == 40.0 and geom["width"] == 20.0 and geom["depth"] == 6.0

    def test_plane_geometry_corner_anchor(self):
        seq = OperationSequencer()
        fe = MachiningFeature(
            name="面A",
            type="plane_surface",
            dimensions={"area": 20000, "length": 200, "width": 100},
        )
        plan = seq.plan_operations([fe])
        geom = plan.operations[0].cutting_params["geometry"]
        assert geom["anchor"] == "corner"
        assert geom["x"] == 0.0 and geom["length"] == 200.0


def _make_op(method: str, geometry: dict | None) -> Operation:
    return Operation(
        seq=1,
        name="OP01-测试",
        feature_name="F001",
        machining_method=method,
        surface="A",
        tolerance_grade="IT8",
        cutting_params={"geometry": geometry} if geometry is not None else {},
    )


class TestGCodeGeneratorIntegration:
    def _generate(self, ops: list[Operation]) -> str:
        plan = OperationPlan(
            operations=ops,
            setups=[],
            estimated_time_min=10.0,
            face_change_count=0,
            fixture_recommendations=[],
        )
        result = GCodeGenerator().generate(
            operation_plan=plan,
            controller_type="fanuc_0i",
            material_name="aluminum",
            safe_z=50.0,
        )
        assert result.is_valid, f"G代码校验失败: {result.errors}"
        return result.program_text

    def test_pocket_uses_engine_toolpath(self):
        """带几何的挖槽必须输出真实环切坐标，而非 10×10 模板走线。"""
        text = self._generate(
            [
                _make_op(
                    "精铣挖槽",
                    {"x": 100.0, "y": 80.0, "length": 40.0, "width": 20.0, "depth": 6.0, "anchor": "center"},
                )
            ]
        )
        assert "刀轨策略: pocket_contour_parallel" in text
        assert "不使用刀具半径补偿" in text
        # 首环贴壁坐标：40×20 槽、φ10 刀 → 刀心环 30×10 (中心 100,80)
        assert "G01 X115.000" in text
        assert "G01 X85.000" in text
        assert "G01 X115.000 Y85.000" in text
        # 不应有半径补偿
        assert "G41" not in text.replace("不使用刀具半径补偿(G41/G42)", "")

    def test_face_mill_uses_raster(self):
        text = self._generate(
            [_make_op("精铣平面", {"x": 0.0, "y": 0.0, "length": 60.0, "width": 40.0, "depth": 1.0, "anchor": "corner"})]
        )
        assert "刀轨策略: face_raster" in text
        assert "G01 X55.000 Y5.000" in text  # 内缩刀具半径后的行

    def test_profile_uses_engine(self):
        text = self._generate(
            [_make_op("粗铣外形", {"x": 50.0, "y": 50.0, "length": 30.0, "width": 30.0, "depth": 4.0, "anchor": "center"})]
        )
        assert "刀轨策略: profile_offset" in text
        assert "G01 X70.000" in text  # 30×30 凸台外扩 5 → x ∈ [30, 70]

    def test_no_geometry_falls_back_to_legacy(self):
        """无几何信息时保持旧行为（模板直线走线 + G41），确保兼容。"""
        text = self._generate([_make_op("精铣平面", None)])
        assert "G41 D10" in text
        assert "G01 X10.000 Y0.000" in text
        assert "刀轨策略" not in text

    def test_degenerate_geometry_falls_back(self):
        """几何退化（槽宽小于刀径）时静默回退模板走线，不中断管线。"""
        text = self._generate(
            [_make_op("精铣挖槽", {"x": 0.0, "y": 0.0, "length": 20.0, "width": 8.0, "depth": 5.0, "anchor": "corner"})]
        )
        assert "G41 D10" in text
        assert "刀轨策略" not in text

    def test_engine_gcode_passes_syntax_validation(self):
        """引擎输出的指令全部为 G00/G01，可通过内置语法校验。"""
        text = self._generate(
            [_make_op("精铣挖槽", {"x": 100.0, "y": 80.0, "length": 40.0, "width": 20.0, "depth": 6.0, "anchor": "center"})]
        )
        g01_lines = [
            ln for ln in text.splitlines()
            if ln.strip().startswith(("G00", "G01"))
            and "G28" not in ln and "G54" not in ln and "G91" not in ln and "G90" not in ln
        ]
        assert len(g01_lines) > 20  # 环切+斜坡的实际刀轨体
        for ln in g01_lines:
            assert ln.startswith(("G00 ", "G01 ")), ln
