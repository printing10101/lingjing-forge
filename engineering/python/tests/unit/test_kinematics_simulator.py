"""程序级运动学校验（app/simulation/kinematics/）单元测试。

覆盖：
- 解释器：模态状态（G90/G91、G20/G21）、固定循环展开（G81/G98/G99）、
  圆弧采样（I/J 与 R 形式）、主轴/刀具/进给状态跟踪
- 校验器：K001-K009 各检查项的触发与放行、行程坐标换算（work_offset）
- 集成：cam_validation pipeline 中 kinematics_check_passed 的写入与 DNC 闸门
"""

from __future__ import annotations

import pytest

from app.simulation.kinematics import (
    GCodeKinematicsInterpreter,
    KinematicsValidator,
    auto_work_offset,
    load_profile,
    profile_from_machine_dict,
)

pytestmark = pytest.mark.unit


REALISTIC_PROGRAM = """%
O1000 (POCKET)
G21 G17 G40 G49 G80 G90 G94
T01 M06
G00 G90 G54 X0. Y0.
G43 Z80.000 H01
M03 S1850
G00 Z52.000
G00 X85.000 Y75.000
G01 X115.000 Y75.000 Z48.000 F275.
G01 X115.000 Y85.000 Z48.000 F275.
G01 X85.000 Y85.000 Z48.000 F275.
G00 Z52.000
M30
%"""


class TestInterpreter:
    @pytest.mark.unit
    def test_modal_state_tracks_motion(self):
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("G90 G21\nG00 X10 Y10\nG01 X20 Y0 F200\n")
        kinds = [r.kind for r in trace.records]
        assert kinds == ["rapid", "linear"]
        assert trace.records[1].feed == 200
        assert trace.records[1].end == (20.0, 0.0, 0.0)

    @pytest.mark.unit
    def test_incremental_mode(self):
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("G91\nG00 X10 Y10\nG01 X10 F200\n")
        assert trace.records[0].end == (10.0, 10.0, 0.0)
        assert trace.records[1].end == (20.0, 10.0, 0.0)

    @pytest.mark.unit
    def test_canned_cycle_expansion(self):
        """G81 一次孔位 → 4 段基本运动（定位/R面/孔底/退回）。"""
        interp = GCodeKinematicsInterpreter()
        trace = interp.run(
            "G90 G54 G00 X10 Y10 Z25\n"
            "M03 S3000\n"
            "G99 G81 X10 Y10 Z-8 R3 F120\n"
            "X30 Y10\n"  # 模态循环：仅坐标词触发第二次钻孔
            "G80\n"
        )
        drills = [r for r in trace.records if r.kind == "drill"]
        assert len(drills) == 2
        assert drills[0].end == (10.0, 10.0, -8.0)
        assert drills[1].end == (30.0, 10.0, -8.0)
        # G99 退回 R 面（Z=3）
        retracts = [r for r in trace.records if r.kind == "rapid"][-1]
        assert retracts.end[2] == 3.0

    @pytest.mark.unit
    def test_arc_ij_sampling(self):
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("G90\nG00 X0 Y0\nG02 X20 Y0 I10 J0 F150\n")
        arcs = [r for r in trace.records if r.kind == "arc_cw"]
        assert len(arcs) == 1
        pts = arcs[0].points
        assert len(pts) >= 5  # 180° 圆弧应被采样为多段
        # 采样点不偏离圆心 (10,0) 半径 10
        for (px, py, _) in pts:
            assert abs(((px - 10) ** 2 + py**2) ** 0.5 - 10.0) < 0.5

    @pytest.mark.unit
    def test_arc_r_form(self):
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("G90\nG00 X0 Y0\nG03 X40 Y0 R20 F150\n")
        arcs = [r for r in trace.records if r.kind == "arc_ccw"]
        assert arcs and arcs[0].end == (40.0, 0.0, 0.0)

    @pytest.mark.unit
    def test_spindle_and_tool_state(self):
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("T02 M06\nM03 S2000\nG01 X10 F100\nM05\nG01 X20\n")
        assert trace.records[0].spindle_on is True and trace.records[0].tool == 2
        assert trace.records[1].spindle_on is False and trace.records[1].tool == 2

    @pytest.mark.unit
    def test_dual_g_codes_on_one_line(self):
        """同行多 G 代码不得互相覆盖（G90 必须生效）。"""
        interp = GCodeKinematicsInterpreter()
        trace = interp.run("G91\nG90 G01 X10 F100\n")
        assert trace.records[0].end == (10.0, 0.0, 0.0)  # 绝对模式生效


class TestValidator:
    def _v(self) -> KinematicsValidator:
        return KinematicsValidator()

    @pytest.mark.unit
    def test_realistic_program_passes(self):
        r = self._v().validate(
            REALISTIC_PROGRAM,
            work_offset=auto_work_offset(self._v().profile, 50.0),
            stock_top_z=50.0,
        )
        assert r.passed, [i.to_dict() for i in r.issues]
        assert r.move_count >= 6

    @pytest.mark.unit
    def test_overtravel_k001(self):
        bad = REALISTIC_PROGRAM.replace("X115.000", "X1950.000")
        r = self._v().validate(bad)
        assert not r.passed
        assert any(i.code == "K001" for i in r.issues)

    @pytest.mark.unit
    def test_spindle_off_cutting_k002(self):
        bad = REALISTIC_PROGRAM.replace("M03 S1850\n", "")
        r = self._v().validate(bad)
        assert not r.passed
        assert any(i.code == "K002" for i in r.issues)

    @pytest.mark.unit
    def test_no_tool_k007(self):
        bad = REALISTIC_PROGRAM.replace("T01 M06\n", "")
        r = self._v().validate(bad)
        assert not r.passed
        assert any(i.code == "K007" for i in r.issues)

    @pytest.mark.unit
    def test_rapid_into_stock_k006(self):
        bad = REALISTIC_PROGRAM.replace(
            "G00 X85.000 Y75.000\n", "G00 X85.000 Y75.000 Z40.000\n"
        ).replace("G01 X115.000 Y75.000 Z48.000 F275.", "G01 X115.000 Y75.000 Z48.000 F275.")
        r = self._v().validate(bad, stock_top_z=50.0, work_offset=(0, 0, 200))
        assert not r.passed
        assert any(i.code == "K006" for i in r.issues)

    @pytest.mark.unit
    def test_feed_over_limit_k004(self):
        bad = REALISTIC_PROGRAM.replace("F275.", "F99999.")
        r = self._v().validate(bad, work_offset=(0, 0, 200), stock_top_z=50.0)
        assert any(i.code == "K004" for i in r.issues)

    @pytest.mark.unit
    def test_spindle_over_limit_k005(self):
        bad = REALISTIC_PROGRAM.replace("S1850", "S99999")
        r = self._v().validate(bad, work_offset=(0, 0, 200))
        assert any(i.code == "K005" for i in r.issues)

    @pytest.mark.unit
    def test_no_program_end_k009(self):
        bad = REALISTIC_PROGRAM.replace("M30\n", "")
        r = self._v().validate(bad, work_offset=(0, 0, 200))
        assert r.passed  # warning 级不拦截
        assert any(i.code == "K009" for i in r.issues if i.severity == "warning")

    @pytest.mark.unit
    def test_work_offset_resolves_negative_z(self):
        """工件顶面在程序 Z0、钻深为负——自动偏移后不应误报超程。"""
        prog = (
            "T01 M06\nM03 S3000\nG90 G00 X10 Y10 Z5\n"
            "G81 X10 Y10 Z-8 R3 F120\nG80\nM30\n"
        )
        profile = self._v().profile
        r = self._v().validate(prog, work_offset=auto_work_offset(profile, 0.0), stock_top_z=0.0)
        assert r.passed, [i.to_dict() for i in r.issues]
        # 机床坐标 Z 应落在正行程内
        assert r.axis_extent["Z"][0] >= 0

    @pytest.mark.unit
    def test_machine_profile_from_dict(self):
        profile = profile_from_machine_dict(
            {
                "id": "test_mc",
                "name": "测试机床",
                "travel_xyz_mm": [400, 300, 250],
                "spindle_speed_rpm": [100, 6000],
                "feed_cutting_max_mmmin": 2000,
                "tool_changer_capacity": 8,
            }
        )
        assert profile.axes["X"].max_mm == 400
        assert profile.max_spindle_rpm == 6000
        assert profile.tool_count == 8
        # 刀号超限
        v = KinematicsValidator(profile)
        r = v.validate("T99 M06\nM03 S1000\nG01 X10 F100\n", work_offset=(0, 0, 100))
        assert any(i.code == "K008" for i in r.issues)

    @pytest.mark.unit
    def test_load_profile_fallback(self):
        profile = load_profile("nonexistent_machine_xyz")
        assert profile.axes  # 回退内置默认画像
