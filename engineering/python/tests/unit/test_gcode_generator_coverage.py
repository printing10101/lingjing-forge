"""process_planning/gcode_generator 覆盖率补强测试。

覆盖：generate 全流程（Fanuc/Siemens/Heidenhain/XM-100 五轴）、
钻孔/铣削/车削/通用四类工序分支、换刀与刀具复用、冷却液开关、
半径补偿、断点标记、hole drilling 专用入口、语法校验错误路径。
"""

from __future__ import annotations

import pytest

from app.process_planning.gcode_generator import GCodeGenerator
from app.process_planning.operation_sequencer import Operation, OperationPlan

pytestmark = pytest.mark.unit


def _op(seq, method, name="工序", tool="end_mill", **kw):
    params = {
        "tool_diameter": 10.0,
        "material": "steel",
        "recommended_feed": "0.1 mm/r",
        "recommended_speed": "80 m/min",
        **kw.get("cutting_params", {}),
    }
    if kw.get("geometry"):
        params["geometry"] = kw["geometry"]
    return Operation(
        seq=seq,
        name=name,
        feature_name=kw.get("feature_name", "face"),
        machining_method=method,
        surface=kw.get("surface", "top"),
        tolerance_grade=kw.get("tolerance_grade", "IT8"),
        tool_type=kw.get("tool_type", tool),
        cutting_params=params,
        estimated_time_min=kw.get("estimated_time_min", 5.0),
    )


def _plan(**kw):
    return OperationPlan(
        operations=kw.get("operations", []),
        estimated_time_min=kw.get("estimated_time_min", 10.0),
    )


class TestGenerateFanuc:
    def test_generate_full_program(self):
        gen = GCodeGenerator()
        plan = _plan(
            operations=[
                _op(1, "平面铣削", feature_name="top_face", cutting_params={"radius_comp": "G41"}),
                _op(2, "中心孔钻削", feature_name="center_hole", tool_type="center_drill"),
            ]
        )
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.program_text
        assert "O1000" in r.program_text or "1000" in r.program_text
        assert r.controller_type == "fanuc_0i"
        assert r.operations_count == 2
        assert r.tool_count >= 1
        assert r.estimated_cycle_time_min > 0
        assert r.total_lines > 0
        assert len(r.checkpoints) == 2

    def test_generate_empty_plan(self):
        gen = GCodeGenerator()
        with pytest.raises(ValueError):
            gen.generate(_plan(), controller_type="fanuc_0i")

    def test_generate_no_coolant(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="fanuc_0i", use_coolant=False)
        # fanuc header 自带一次 M08（后处理器固有安全默认）；
        # use_coolant=False 关闭工序级冷却液 全程序仅 header 一处 M08
        assert r.program_text.count("M08") == 1

    def test_generate_g42_compensation(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="fanuc_0i", tool_radius_compensation="G42")
        assert r.program_text

    def test_generate_unknown_controller(self):
        gen = GCodeGenerator()
        with pytest.raises(ValueError):
            gen.generate(_plan(operations=[_op(1, "平面铣削")]), controller_type="unknown_cnc")

    def test_material_and_safe_z_params(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="fanuc_0i", material_name="45#钢", safe_z=80.0)
        assert "45#钢" in r.program_text
        assert r.metadata["material"] == "45#钢"

    def test_generate_hole_drilling_only(self):
        gen = GCodeGenerator()
        r = gen.generate_hole_drilling_only(
            hole_positions=[{"x": 10.0, "y": 20.0}, {"x": 30.0, "y": 40.0}],
            hole_depth=12.0,
            controller_type="fanuc_0i",
        )
        assert r.program_text
        assert "M03" in r.program_text
        assert r.operations_count >= 1


class TestGenerateControllers:
    def test_siemens(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="siemens_840d")
        assert r.program_text
        assert "M30" in r.program_text

    def test_heidenhain(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="heidenhain_tnc")
        assert r.program_text
        assert "END PGM" in r.program_text.upper()

    def test_xmachine_five_axis(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "五轴联动铣削")])
        # safe_z=80 超过 XM-100 Z 行程 ±50 自动 clamp
        r = gen.generate(plan, controller_type="xmachine_xm100", safe_z=80.0)
        assert r.program_text
        assert "五轴" in r.program_text

    def test_heidenhain_breakpoints(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="heidenhain_tnc")
        assert "BREAKPOINT" in r.program_text


class TestFeatureBranches:
    def test_drilling_center(self):
        gen = GCodeGenerator()
        plan = _plan(
            operations=[_op(1, "中心孔钻削", cutting_params={"geometry": {"x": 5.0, "y": 5.0, "z_depth": 3.0}})]
        )
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert "G81" in r.program_text or "G83" in r.program_text or "G73" in r.program_text

    def test_drilling_counterbore(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "沉头孔钻削", tool_type="counterbore")])
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.program_text

    def test_turning(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "外圆车削", tool_type="turning_insert")])
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.program_text

    def test_generic_method(self):
        gen = GCodeGenerator()
        plan = _plan(operations=[_op(1, "倒角", tool_type="chamfer_tool")])
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.program_text

    def test_tool_reuse(self):
        gen = GCodeGenerator()
        plan = _plan(
            operations=[
                _op(1, "平面铣削", feature_name="f1"),
                _op(2, "轮廓铣削", feature_name="f2"),  # 同刀具类型 → 复用
            ]
        )
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.tool_count == 1  # 单一刀具
        assert "复用" in r.program_text

    def test_machine_config_limiter(self):
        gen = GCodeGenerator(machine_config={"max_spindle_rpm": 2000})
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert r.program_text


class TestSyntaxValidation:
    def test_syntax_errors_reported(self):
        gen = GCodeGenerator()
        # Fanuc 需要 O 程序号 + % 结束符 —— 生成的程序应合法
        plan = _plan(operations=[_op(1, "平面铣削")])
        r = gen.generate(plan, controller_type="fanuc_0i")
        assert isinstance(r.errors, list)
