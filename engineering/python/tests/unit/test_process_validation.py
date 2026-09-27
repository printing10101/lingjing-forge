"""process_planning/_validation 覆盖率补强测试。

覆盖：validate_gcode_syntax 六类安全检查全分支（Fanuc/Siemens/Heidenhain
方言、行程极限、切削参数、G00 碰撞、刀具补偿、坐标系）、
build_dry_run_preview 全流程（空规划/路径摘要/时间估算/刀具统计/
碰撞风险/断点/警告）、validate_gcode 独立校验器。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.process_planning._validation import (
    build_dry_run_preview,
    validate_gcode,
    validate_gcode_syntax,
)
from app.process_planning.operation_sequencer import Operation, OperationPlan


def _op(**kw):
    base = dict(
        seq=1,
        name="粗铣顶面",
        feature_name="顶面",
        machining_method="face_milling",
        surface="F7",
        tolerance_grade="IT9",
    )
    base.update(kw)
    return Operation(**base)


def _plan(ops):
    return OperationPlan(operations=ops, estimated_time_min=sum(o.estimated_time_min for o in ops))


# validate_gcode_syntax


class TestValidateGcodeSyntax:
    def test_empty_program(self):
        errs = validate_gcode_syntax("", "fanuc_0i")
        assert errs == ["G代码程序为空"]
        errs = validate_gcode_syntax("   \n  ", "siemens_840d")
        assert "G代码程序为空" in errs

    def test_fanuc_missing_header_and_end(self):
        errs = validate_gcode_syntax("G01 X10 F100", "fanuc_0i")
        assert any("程序号" in e for e in errs)
        assert any("结束符" in e for e in errs)

    def test_fanuc_ok(self):
        gcode = "O1000\nG90 G21\nG01 X10 F100\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert not any("程序号" in e for e in errs)
        assert not any("结束符" in e for e in errs)

    def test_siemens_missing_m30(self):
        errs = validate_gcode_syntax("G01 X10 F100", "siemens_840d")
        assert any("M30" in e for e in errs)

    def test_siemens_ok(self):
        errs = validate_gcode_syntax("G01 X10 F100\nM30", "siemens_840d")
        assert not any("M30" in e for e in errs)

    def test_heidenhain_missing_markers(self):
        errs = validate_gcode_syntax("L X0 Y0 FMAX", "heidenhain_tnc")
        assert any("BEGIN PGM" in e for e in errs)
        assert any("END PGM" in e for e in errs)

    def test_heidenhain_ok(self):
        gcode = "BEGIN PGM 1 MM\nL X0 Y0 FMAX\nEND PGM 1 MM"
        errs = validate_gcode_syntax(gcode, "heidenhain_tnc")
        assert not any("BEGIN PGM" in e for e in errs)
        assert not any("END PGM" in e for e in errs)

    def test_out_of_travel_limits(self):
        gcode = "O1000\nG01 X600 Y450 Z-350 F100\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert any("X坐标600" in e for e in errs)
        assert any("Y坐标450" in e for e in errs)
        assert any("Z坐标-350" in e for e in errs)

    def test_spindle_and_feed_limits(self):
        gcode = "O1000\nS10 M03\nG01 X10 F5\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert any("主轴转速10" in e for e in errs)
        assert any("进给速度5" in e for e in errs)

    def test_g00_collision_detection(self):
        gcode = "O1000\nG00 X10 Y10 Z-5\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert any("G00快速移动" in e for e in errs)

    def test_g00_safe_z_no_error(self):
        gcode = "O1000\nG00 X10 Y10 Z5\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert not any("G00快速移动" in e for e in errs)

    def test_cutter_comp_not_cancelled(self):
        gcode = "O1000\nG41 D1\nG01 X10 F100\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert any("G41" in e and "G40" in e for e in errs)

    def test_cutter_comp_cancelled(self):
        gcode = "O1000\nG41 D1\nG01 X10 F100\nG40\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert not any("G40" in e for e in errs)

    def test_comment_lines_skipped(self):
        gcode = "O1000\n; 注释\n(括号注释)\nG01 X10 F100\nM30\n%"
        errs = validate_gcode_syntax(gcode, "fanuc_0i")
        assert not any("X坐标10" in e for e in errs)
        assert not any("行程" in e for e in errs)


# build_dry_run_preview


class TestBuildDryRunPreview:
    def test_empty_plan_warns(self):
        r = build_dry_run_preview(_plan([]))
        assert r["warnings"] == ["工序规划结果为空"]
        assert r["tool_path_summary"] == []
        assert r["time_estimation"] == {}

    def test_none_plan_warns(self):
        r = build_dry_run_preview(None)
        assert "工序规划结果为空" in r["warnings"]

    def test_normal_plan(self):
        ops = [
            _op(
                seq=1,
                name="粗铣顶面",
                tool_type="face_mill",
                machining_method="face_milling",
                feature_name="顶面",
                cutting_params={"start_x": 10.0, "depth": 2.0},
                estimated_time_min=15.0,
            ),
            _op(
                seq=2,
                name="精铣侧面",
                tool_type="end_mill",
                machining_method="contour_milling",
                feature_name="侧面",
                cutting_params={"depth": 0.5},
                estimated_time_min=10.0,
            ),
        ]
        r = build_dry_run_preview(_plan(ops), safe_z=80.0, stock_top_z=50.0)
        assert len(r["tool_path_summary"]) == 2
        # 第一道工序从安全高度切入
        assert r["tool_path_summary"][0]["start_pos"]["z"] == 80.0
        assert r["tool_path_summary"][0]["end_pos"]["z"] == pytest.approx(48.0)
        # 时间估算包含换刀时间
        assert r["time_estimation"]["machining_time_min"] == 25.0
        assert r["time_estimation"]["tool_change_count"] == 2
        assert r["time_estimation"]["tool_change_time_min"] == 3.0
        # 刀具统计
        assert set(r["tool_usage"].keys()) == {"face_mill", "end_mill"}
        assert r["tool_usage"]["face_mill"]["usage_count"] == 1
        # 断点
        assert len(r["checkpoint_positions"]) == 2
        assert r["checkpoint_positions"][0]["checkpoint_id"] == "CP001"

    def test_no_cutting_params_uses_defaults(self):
        ops = [_op(seq=1, tool_type=None)]
        r = build_dry_run_preview(_plan(ops))
        assert r["tool_path_summary"][0]["tool_type"] == "UNKNOWN"
        assert r["tool_path_summary"][0]["start_pos"]["z"] == 80.0
        assert r["tool_path_summary"][0]["end_pos"]["z"] == 80.0
        # 未指定刀具 tool_change_count 0（set 去重后为空）
        assert r["time_estimation"]["tool_change_count"] == 0

    def test_deep_cavity_risk(self):
        ops = [_op(seq=1, name="深腔", cutting_params={"depth": 60.0})]
        r = build_dry_run_preview(_plan(ops))
        assert any(risk["risk_type"] == "deep_cavity" for risk in r["collision_risks"])
        assert any("潜在碰撞风险" in w for w in r["warnings"])

    def test_long_rapid_move_risk(self):
        # 起点安全高度 500 终点 50-10=40，差值 460 > 100 长距风险
        ops = [_op(seq=1, cutting_params={"depth": 10.0})]
        r = build_dry_run_preview(_plan(ops), safe_z=500.0, stock_top_z=50.0)
        assert any(risk["risk_type"] == "long_rapid_move" for risk in r["collision_risks"])

    def test_many_tool_changes_warning(self):
        ops = [_op(seq=i, tool_type=f"tool_{i % 3}", cutting_params={}) for i in range(1, 13)]
        r = build_dry_run_preview(_plan(ops), safe_z=80.0, stock_top_z=50.0)
        # 3 种刀具 换刀 3 次，不触发 >10 警告
        assert not any("刀具更换次数较多" in w for w in r["warnings"])
        # 每种刀具统计 usage_count 4
        assert r["tool_usage"]["tool_0"]["usage_count"] == 4

    def test_long_machining_warning(self):
        ops = [_op(seq=1, tool_type="mill", cutting_params={}, estimated_time_min=70.0)]
        r = build_dry_run_preview(_plan(ops))
        assert any("预估加工时间较长" in w for w in r["warnings"])


# validate_gcode


class TestValidateGcode:
    def test_empty_gcode(self):
        r = validate_gcode("")
        assert r["valid"] is False
        assert r["errors"] == ["G代码为空"]

    def test_valid_program(self):
        gcode = "O1000\nG90 G21 G54\nS1500 M03\nG00 Z50\nG01 X10 Y20 F500\nG00 Z100\nM30\n"
        r = validate_gcode(gcode)
        assert r["valid"] is True
        assert r["errors"] == []

    def test_missing_end_warning_and_errors(self):
        r = validate_gcode("O1000\nG01 X10 F500\n")
        assert r["valid"] is False
        assert any("程序结束" in e for e in r["errors"])
        # O1000 有程序号 无该警告；缺少主轴启动 有警告
        assert not any("程序号" in w for w in r["warnings"])
        assert any("主轴启动" in w for w in r["warnings"])

    def test_missing_feed_and_spindle_warnings(self):
        r = validate_gcode("O1000\nG01 X10\nM30")
        assert any("进给率" in w for w in r["warnings"])
        assert any("主轴启动" in w for w in r["warnings"])

    def test_g00_negative_z_warning(self):
        gcode = "O1000\nG00 X5 Y5 Z-3\nG01 X10 F100\nM30"
        r = validate_gcode(gcode)
        assert any("G00快速移动" in w for w in r["warnings"])

    def test_unknown_gcode_warning(self):
        gcode = "O1000\nG123 X10 F100\nM30"
        r = validate_gcode(gcode)
        assert any("不常见的G代码 G123" in w for w in r["warnings"])

    def test_unknown_mcode_warning(self):
        gcode = "O1000\nG01 X10 F100\nM77\nM30"
        r = validate_gcode(gcode)
        assert any("不常见的M代码 M77" in w for w in r["warnings"])

    def test_cutter_comp_cancel_check(self):
        gcode = "O1000\nG41 D1\nG01 X10 F100\nM30"
        r = validate_gcode(gcode)
        assert any("G40" in w for w in r["warnings"])

    def test_m02_end_detected(self):
        gcode = "O1000\nG01 X10 F100\nM02"
        r = validate_gcode(gcode)
        assert r["valid"] is True
