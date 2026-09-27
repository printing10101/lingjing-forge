"""toolpath/dynamic_adjustment + simulation/toolpath_parser 覆盖率补强测试。

覆盖：磨损快照/参数数据结构、决策编排全流程（含实时校正闭环、
机床限幅降级）、NC 代码段级改写、G 代码解析器（Fanuc/Siemens/Heidenhain）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.simulation.toolpath_parser import ToolpathParser
from app.toolpath.dynamic_adjustment import (
    AdjustmentDecision,
    CurrentParameters,
    DynamicAdjustmentOrchestrator,
    NCRewriteResult,
    WearState,
    _SimpleLimiter,
)


def _wear(**kw):
    base = dict(
        tool_id=1,
        wear_amount=0.12,
        usage_time=45.0,
        wear_threshold=0.3,
    )
    base.update(kw)
    return WearState(**base)


def _current(**kw):
    base = dict(
        cutting_speed=120.0,
        feed_rate=0.15,
        depth_of_cut=1.0,
        width_of_cut=10.0,
    )
    base.update(kw)
    return CurrentParameters(**base)


# 数据结构


class TestWearState:
    def test_wear_ratio_normal(self):
        w = _wear(wear_amount=0.15, wear_threshold=0.3)
        assert w.wear_ratio == pytest.approx(0.5)

    def test_wear_ratio_zero_threshold(self):
        w = _wear(wear_amount=0.15, wear_threshold=0.0)
        assert w.wear_ratio == 0.0

    def test_wear_ratio_negative_threshold(self):
        w = _wear(wear_amount=0.15, wear_threshold=-1.0)
        assert w.wear_ratio == 0.0

    def test_tool_wear_factor_new(self):
        w = _wear(wear_amount=0.0, wear_threshold=0.3)
        assert w.tool_wear_factor == pytest.approx(1.0)

    def test_tool_wear_factor_half(self):
        w = _wear(wear_amount=0.15, wear_threshold=0.3)
        assert w.tool_wear_factor == pytest.approx(1.5)

    def test_tool_wear_factor_over_threshold(self):
        w = _wear(wear_amount=0.5, wear_threshold=0.3)
        assert w.tool_wear_factor == pytest.approx(2.0)

    def test_tool_wear_factor_negative_wear(self):
        w = _wear(wear_amount=-0.1, wear_threshold=0.3)
        assert w.tool_wear_factor == pytest.approx(1.0)


class TestCurrentParameters:
    def test_to_input_parameters(self):
        w = _wear()
        d = _current().to_input_parameters(w)
        assert d["cutting_speed"] == 120.0
        assert d["feed_rate"] == 0.15
        assert d["depth_of_cut"] == 1.0
        assert d["material_type"] == "steel_45"
        assert d["tool_type"] == "carbide"
        assert d["current_wear"] == 0.12
        assert d["coolant_flow"] == 10.0

    def test_defaults(self):
        w = _wear(tool_diameter=8.0, flute_count=3)
        d = CurrentParameters(120.0, 0.2, 2.0).to_input_parameters(w)
        assert d["tool_diameter"] == 8.0
        assert d["current_wear"] == 0.12


class TestDecisionDataclasses:
    def test_adjustment_decision_to_dict(self):
        d = AdjustmentDecision(
            strategy="moderate_compensation",
            urgency="warning",
            new_cutting_speed=100.0,
            new_feed_rate=0.12,
            new_depth_of_cut=0.9,
            new_spindle_rpm=3200.0,
            new_feed_rate_mm_min=400.0,
            life_extension_pct=12.5,
        )
        out = d.to_dict()
        assert out["strategy"] == "moderate_compensation"
        assert out["new_spindle_rpm"] == 3200.0
        assert out["life_extension_pct"] == 12.5

    def test_nc_rewrite_result_to_dict(self):
        r = NCRewriteResult(rewritten_gcode="G01 F100", segments_total=3, segments_adjusted=2)
        out = r.to_dict()
        assert out["segments_total"] == 3
        assert out["segments_adjusted"] == 2
        assert out["per_segment_log"] == []


# 辅助方法


class TestHelpers:
    def test_compute_spindle_rpm(self):
        o = DynamicAdjustmentOrchestrator()
        rpm = o._compute_spindle_rpm(120.0, 10.0)
        assert rpm == pytest.approx((120.0 * 1000) / (3.141592653589793 * 10.0), rel=1e-6)

    def test_compute_spindle_rpm_zero_diameter(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._compute_spindle_rpm(120.0, 0.0) == 0.0

    def test_normalize_material_aluminum(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._normalize_material_name("AL6061") == "aluminum"
        assert o._normalize_material_name("aluminum_7075") == "aluminum"

    def test_normalize_material_titanium(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._normalize_material_name("titanium_tc4") == "titanium"
        assert o._normalize_material_name("Ti64") == "titanium"

    def test_normalize_material_stainless(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._normalize_material_name("stainless_304") == "stainless"
        assert o._normalize_material_name("HRC45") == "stainless"

    def test_normalize_material_steel_default(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._normalize_material_name("steel_45") == "steel"
        assert o._normalize_material_name("unknown_xyz") == "steel"

    def test_extract_block_number(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._extract_block_number("N10 G01 X10") == 10
        assert o._extract_block_number("N0123 G90") == 123
        assert o._extract_block_number("G01 X10") is None
        assert o._extract_block_number("  N5 M30") == 5

    def test_replace_word(self):
        o = DynamicAdjustmentOrchestrator()
        assert o._replace_word("G01 X10 F200", "F", 150.0) == "G01 X10 F150"
        assert o._replace_word("S1000 M03", "S", 800) == "S800 M03"
        assert o._replace_word("F-50", "F", 30.0) == "F30"
        assert o._replace_word("G01 F200 ; comment", "F", 88.5) == "G01 F88.5 ; comment"
        # 无该字段时原样返回
        assert o._replace_word("G01 X10", "F", 150.0) == "G01 X10"

    def test_simple_limiter(self):
        lim = _SimpleLimiter(max_rpm=8000.0, max_feed=2000.0)
        assert lim.get_spindle_rpm(5000.0) == 5000.0
        assert lim.get_spindle_rpm(12000.0) == 8000.0
        assert lim.get_spindle_rpm(-100.0) == 0.0
        assert lim.get_spindle_rpm(None) == 0.0
        assert lim.get_feed_rate(1500.0) == 1500.0
        assert lim.get_feed_rate(5000.0) == 2000.0
        assert lim.get_feed_rate(None) == 0.0


# 决策编排


class TestDecideAdjustment:
    def test_no_adjustment_low_wear(self):
        o = DynamicAdjustmentOrchestrator()
        d = o.decide_adjustment(_wear(wear_amount=0.02), _current())
        assert d.strategy in ("no_adjustment", "slight_compensation")
        assert d.new_spindle_rpm > 0
        assert d.new_feed_rate > 0
        assert isinstance(d.to_dict(), dict)

    def test_critical_wear_has_suggestions(self):
        o = DynamicAdjustmentOrchestrator()
        d = o.decide_adjustment(_wear(wear_amount=0.29), _current())
        assert d.urgency == "critical"
        assert d.suggestions

    def test_machine_capability_limits(self):
        o = DynamicAdjustmentOrchestrator()
        d = o.decide_adjustment(
            _wear(wear_amount=0.25),
            _current(),
            machine_capabilities={"max_spindle_speed": 1000.0, "max_feed_rate": 100.0},
        )
        assert d.new_spindle_rpm <= 1000.0
        assert d.new_feed_rate_mm_min <= 100.0
        assert any("限幅" in w for w in d.warnings)

    def test_real_time_calibration_loop(self, monkeypatch):
        o = DynamicAdjustmentOrchestrator()
        fake = {
            "measured_wear": 0.20,
            "predicted_wear_at_time": 0.16,
            "corrected_wear": 0.19,
            "deviation_ratio": 0.25,
            "sensor_adjustment": 1.05,
            "adjustment_reasons": ["振动上升"],
            "confidence": 0.9,
            "sensor_coverage": 0.8,
        }
        monkeypatch.setattr(o.wear_predictor, "calibrate_with_real_time_data", lambda **kw: fake)
        d = o.decide_adjustment(
            _wear(wear_amount=0.12),
            _current(),
            real_time_wear=0.20,
            sensor_features={"vibration_rms": 1.2},
            elapsed_time=5.0,
        )
        assert any("实时校正闭环" in r for r in d.reasoning)
        assert any("振动上升" in r for r in d.reasoning)

    def test_real_time_calibration_failure_fallback(self, monkeypatch):
        o = DynamicAdjustmentOrchestrator()

        def _boom(**kw):
            raise RuntimeError("sensor unavailable")

        monkeypatch.setattr(o.wear_predictor, "calibrate_with_real_time_data", _boom)
        d = o.decide_adjustment(
            _wear(wear_amount=0.12),
            _current(),
            real_time_wear=0.20,
            sensor_features={"vibration_rms": 1.2},
            elapsed_time=5.0,
        )
        # 降级到原始磨损值决策，仍返回有效决策
        assert any("实时校正失败" in r for r in d.reasoning)
        assert d.new_spindle_rpm > 0

    def test_optimization_goal_efficiency(self):
        o = DynamicAdjustmentOrchestrator()
        d = o.decide_adjustment(_wear(wear_amount=0.05), _current(), optimization_goal="efficiency")
        assert d.strategy in ("no_adjustment", "slight_compensation")


# NC 改写


class TestRewriteNcCode:
    GCODE = "N10 G90 G21\nN20 S1500 M03\nN30 G00 X0 Y0 Z5\nN40 G01 X50 Y0 Z-2 F200\nN50 M30\n"

    def test_rewrite_motion_only(self):
        o = DynamicAdjustmentOrchestrator()
        dec = AdjustmentDecision(
            strategy="moderate_compensation",
            urgency="warning",
            new_cutting_speed=100.0,
            new_feed_rate=0.1,
            new_depth_of_cut=1.0,
            new_spindle_rpm=1200.0,
            new_feed_rate_mm_min=150.0,
            life_extension_pct=10.0,
        )
        r = o.rewrite_nc_code(self.GCODE, dec)
        assert r.segments_total >= 1
        assert r.segments_adjusted >= 1
        # 运动段 F 已替换（N40 G01 F200 F150）
        assert "N40 G01 X50 Y0 Z-2 F150" in r.rewritten_gcode
        # G00 段保留原样（apply_to_motion_only 默认 True，且 rapid 段不改写）
        assert "N30 G00 X0 Y0 Z5" in r.rewritten_gcode
        # S 独立行（N20 S1500 M03）不属于运动段，保守策略不改写
        assert "N20 S1500 M03" in r.rewritten_gcode

    def test_rewrite_all_segments(self):
        o = DynamicAdjustmentOrchestrator()
        dec = AdjustmentDecision(
            strategy="moderate_compensation",
            urgency="warning",
            new_cutting_speed=100.0,
            new_feed_rate=0.1,
            new_depth_of_cut=1.0,
            new_spindle_rpm=1200.0,
            new_feed_rate_mm_min=150.0,
            life_extension_pct=10.0,
        )
        r = o.rewrite_nc_code(self.GCODE, dec, apply_to_motion_only=False)
        assert r.segments_adjusted >= 1
        assert "N40 G01 X50 Y0 Z-2 F150" in r.rewritten_gcode

    def test_rewrite_heidenhain_controller(self):
        o = DynamicAdjustmentOrchestrator()
        dec = AdjustmentDecision(
            strategy="no_adjustment",
            urgency="normal",
            new_cutting_speed=100.0,
            new_feed_rate=0.1,
            new_depth_of_cut=1.0,
            new_spindle_rpm=1200.0,
            new_feed_rate_mm_min=150.0,
            life_extension_pct=0.0,
        )
        gcode = "BEGIN PGM 1 MM\nTOOL CALL 1 Z S1200\nL X0 Y0 Z5 FMAX\nL X50 Y0 Z-2 F200\nEND PGM 1 MM\n"
        r = o.rewrite_nc_code(gcode, dec, controller_type="heidenhain")
        assert r.segments_total >= 0  # 不崩溃
        assert isinstance(r.rewritten_gcode, str)

    def test_rewrite_parse_failure_returns_original(self):
        o = DynamicAdjustmentOrchestrator()
        dec = AdjustmentDecision(
            strategy="no_adjustment",
            urgency="normal",
            new_cutting_speed=100.0,
            new_feed_rate=0.1,
            new_depth_of_cut=1.0,
            new_spindle_rpm=1200.0,
            new_feed_rate_mm_min=150.0,
            life_extension_pct=0.0,
        )
        r = o.rewrite_nc_code("", dec)
        assert r.segments_total == 0
        assert r.rewritten_gcode == ""

    def test_rewrite_with_existing_parser_instance(self):
        parser = ToolpathParser(controller_type="fanuc")
        o = DynamicAdjustmentOrchestrator(toolpath_parser=parser)
        dec = AdjustmentDecision(
            strategy="no_adjustment",
            urgency="normal",
            new_cutting_speed=100.0,
            new_feed_rate=0.1,
            new_depth_of_cut=1.0,
            new_spindle_rpm=1200.0,
            new_feed_rate_mm_min=150.0,
            life_extension_pct=0.0,
        )
        r = o.rewrite_nc_code(self.GCODE, dec)
        assert r.segments_total >= 1
        # 已提供 fanuc parser 且要求 fanuc 复用实例
        assert o.toolpath_parser is parser


# G 代码解析器


class TestToolpathParser:
    def test_parse_rapid_and_linear(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N1 G00 X0 Y0 Z5\nN2 G01 X50 Y0 Z-2 F1000\n")
        assert len(segs) == 2
        assert segs[0].type == "rapid"
        assert segs[0].g_code == "RAPID"
        assert segs[1].type == "linear"
        assert segs[1].g_code == "LINEAR"
        assert segs[1].feed_rate == pytest.approx(1000.0)
        assert segs[1].end_point[0] == pytest.approx(50.0)
        assert segs[1].start_point[0] == pytest.approx(0.0)

    def test_parse_skips_comments_and_headers(self):
        p = ToolpathParser()
        gcode = "%\nO1000\n; comment\n(another)\nN10 G01 X10 F500\n"
        segs = p.parse_gcode(gcode)
        assert len(segs) == 1

    def test_modal_state_persists(self):
        p = ToolpathParser()
        gcode = "N10 S2000 M03\nN20 F800\nN30 G01 X10\nN40 G01 Y20\n"
        segs = p.parse_gcode(gcode)
        assert len(segs) == 2
        assert segs[0].spindle_speed == 2000
        assert segs[0].feed_rate == pytest.approx(800.0)
        assert segs[1].feed_rate == pytest.approx(800.0)

    def test_parse_arc_g02_g03(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N10 G17 G90\nN20 G02 X50 Y0 I25 J0 F500\nN30 G03 X0 Y0 I-25 J0\n")
        assert len(segs) == 2
        assert segs[0].type == "arc"
        assert segs[0].clockwise is True
        assert segs[1].type == "arc"
        assert segs[1].clockwise is False

    def test_parse_arc_radius_format(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N10 G02 X50 Y0 R25 F500\n")
        assert len(segs) == 1
        assert segs[0].type == "arc"
        assert segs[0].arc_radius == pytest.approx(25.0)

    def test_parse_tool_change_and_dwell(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N10 T2 M06\nN20 G04 P1000\nN30 G01 X5 F100\n")
        # T/M06 不产生运动段；G04 产生 dwell 段；G01 运动段
        assert len(segs) == 2
        assert segs[0].type == "dwell"
        assert segs[1].type == "linear"

    def test_heidenhain_filters_non_motion(self):
        p = ToolpathParser(controller_type="heidenhain")
        gcode = (
            "BEGIN PGM 1 MM\n"
            "BLK FORM 0.1 Z X0 Y0 Z0 X100 Y100 Z-50\n"
            "TOOL CALL 1 Z S1200\n"
            "M3\n"
            "L X0 Y0 Z5 FMAX\n"
            "L X50 Y0 Z-2 F200\n"
            "LBL 1\n"
            "END PGM 1 MM\n"
        )
        segs = p.parse_gcode(gcode)
        assert len(segs) == 2
        assert segs[0].type == "rapid"
        assert segs[1].type == "linear"

    def test_siemens_controller(self):
        p = ToolpathParser(controller_type="siemens")
        segs = p.parse_gcode("N10 G01 X10 Y20 F500\n")
        assert len(segs) == 1
        assert segs[0].type == "linear"

    def test_segment_to_dict(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N10 G01 X5 F100\n")
        d = segs[0].to_dict()
        assert d["type"] == "linear"
        assert d["start_point"] == [0.0, 0.0, 100.0]
        assert d["end_point"] == [5.0, 0.0, 100.0]
        assert d["arc_center"] is None

    def test_start_end_aliases(self):
        p = ToolpathParser()
        segs = p.parse_gcode("N10 G01 X5 F100\n")
        assert segs[0].start == segs[0].start_point
        assert segs[0].end == segs[0].end_point
