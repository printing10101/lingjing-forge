"""Regression tests for G-code baseline comparison.

Covers:
- 5 standard test workpieces with baseline G-code files
- Automated comparison with configurable tolerance
- Difference report generation
- Acceptable deviation thresholds (coordinate precision, feed rate)
"""

from __future__ import annotations

import pytest

from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from tests.utils.gcode_helpers import (
    compare_gcode,
    generate_test_gcode,
)


REGRESSION_BASELINES = {
    "simple_contour": {
        "description": "Simple rectangular contour - 2D profile cutting",
        "controller": "fanuc",
        "scenario": "simple_contour",
    },
    "drilling_cycle": {
        "description": "Multiple hole drilling with G83 cycle",
        "controller": "fanuc",
        "scenario": "drilling",
    },
    "pocket_milling": {
        "description": "Multi-pass pocket milling",
        "controller": "fanuc",
        "scenario": "pocket_milling",
    },
    "heidenhain_contour": {
        "description": "Heidenhain contour milling",
        "controller": "heidenhain",
        "scenario": "simple_contour",
    },
    "siemens_contour": {
        "description": "Siemens contour milling",
        "controller": "siemens",
        "scenario": "simple_contour",
    },
}

DEFAULT_TOLERANCE = {
    "coordinate_precision": 0.01,
    "feed_rate_tolerance_percent": 5.0,
    "spindle_speed_tolerance_percent": 2.0,
    "ignore_comments": True,
    "ignore_program_numbers": True,
    "ignore_timestamps": True,
}


@pytest.fixture
def baseline_gcodes() -> dict[str, str]:
    """Load baseline G-code for all 5 standard test workpieces."""
    baselines = {}
    for name, config in REGRESSION_BASELINES.items():
        baselines[name] = generate_test_gcode(config["scenario"])
    return baselines


@pytest.mark.regression
@pytest.mark.gcode
class TestRegressionBaselines:
    """Regression tests comparing current output against baselines."""

    def test_simple_contour_fanuc_regression(self, baseline_gcodes):
        """Simple contour should match Fanuc baseline."""
        baseline = baseline_gcodes["simple_contour"]
        current = generate_test_gcode("simple_contour")
        result = compare_gcode(current, baseline, DEFAULT_TOLERANCE)
        assert result.is_within_tolerance, (
            f"Simple contour regression failed: {result.differences}"
        )
        assert result.match_score >= 0.95

    def test_drilling_cycle_regression(self, baseline_gcodes):
        """Drilling cycle should match baseline."""
        baseline = baseline_gcodes["drilling_cycle"]
        current = generate_test_gcode("drilling")
        result = compare_gcode(current, baseline, DEFAULT_TOLERANCE)
        assert result.is_within_tolerance, (
            f"Drilling cycle regression failed: {result.differences}"
        )
        assert result.match_score >= 0.95

    def test_pocket_milling_regression(self, baseline_gcodes):
        """Pocket milling should match baseline."""
        baseline = baseline_gcodes["pocket_milling"]
        current = generate_test_gcode("pocket_milling")
        result = compare_gcode(current, baseline, DEFAULT_TOLERANCE)
        assert result.is_within_tolerance, (
            f"Pocket milling regression failed: {result.differences}"
        )
        assert result.match_score >= 0.95

    def test_heidenhain_contour_regression(self, baseline_gcodes):
        """Heidenhain contour should have proper header/footer."""
        pp = HeidenhainPostProcessor()
        header = pp.format_header(1)
        footer = pp.format_footer()
        assert "BEGIN PGM" in header
        assert "END PGM" in footer
        assert "M30" in footer
        assert "M09" in footer
        assert "M05" in footer

    def test_siemens_contour_regression(self, baseline_gcodes):
        """Siemens contour should have proper header/footer."""
        pp = SiemensPostProcessor()
        header = pp.format_header(1)
        footer = pp.format_footer()
        assert "Siemens 840D" in header
        assert "M30" in footer
        assert "M09" in footer
        assert "M05" in footer


@pytest.mark.regression
class TestGcodeComparisonTool:
    """Test the G-code comparison tool itself."""

    def test_coordinate_precision_comparison(self):
        """Coordinate comparison within precision tolerance."""
        gcode1 = "G01 X10.000 Y10.000 F500.000"
        gcode2 = "G01 X10.005 Y10.005 F500.000"
        result = compare_gcode(gcode1, gcode2, {"coordinate_precision": 0.01})
        assert result.match_score == 1.0

    def test_feed_rate_tolerance(self):
        """Feed rate comparison within tolerance percentage."""
        gcode1 = "G01 X10.000 Y10.000 F500.000"
        gcode2 = "G01 X10.000 Y10.000 F510.000"
        result = compare_gcode(
            gcode1,
            gcode2,
            {
                "coordinate_precision": 0.01,
                "feed_rate_tolerance_percent": 5.0,
            },
        )
        assert result.match_score == 1.0

    def test_spindle_speed_tolerance(self):
        """Spindle speed comparison within tolerance."""
        gcode1 = "M03 S8000"
        gcode2 = "M03 S8100"
        result = compare_gcode(
            gcode1,
            gcode2,
            {
                "coordinate_precision": 0.01,
                "spindle_speed_tolerance_percent": 2.0,
            },
        )
        assert result.match_score == 1.0

    def test_ignore_comments(self):
        """Comments should be ignored when configured."""
        gcode1 = "G01 X10.000 Y10.000 (Comment 1)"
        gcode2 = "G01 X10.000 Y10.000 (Different Comment)"
        result = compare_gcode(gcode1, gcode2, {"ignore_comments": True})
        assert result.match_score == 1.0

    def test_empty_comparison(self):
        """Empty G-code comparison should return perfect match."""
        result = compare_gcode("", "")
        assert result.match_score == 0.0
        assert result.total_commands_compared == 0

    def test_partial_gcode(self):
        """Partial G-code with only M-codes."""
        gcode1 = "M03 S8000\nM08"
        gcode2 = "M03 S8000\nM08"
        result = compare_gcode(gcode1, gcode2)
        assert result.match_score == 1.0


@pytest.mark.regression
class TestRegressionReport:
    """Regression difference report generation."""

    def test_generate_difference_report(self, baseline_gcodes):
        """Generate a difference report for all workpieces."""
        report = {
            "regression_date": "2026-01-01",
            "total_workpieces": len(REGRESSION_BASELINES),
            "passed": 0,
            "failed": 0,
            "details": [],
        }

        for name, config in REGRESSION_BASELINES.items():
            baseline = baseline_gcodes[name]
            current = generate_test_gcode(config["scenario"])
            result = compare_gcode(current, baseline, DEFAULT_TOLERANCE)

            detail = {
                "workpiece": name,
                "description": config["description"],
                "controller": config["controller"],
                "passed": result.is_within_tolerance,
                "match_score": result.match_score,
                "differences": result.differences,
            }
            report["details"].append(detail)
            if result.is_within_tolerance:
                report["passed"] += 1
            else:
                report["failed"] += 1

        assert report["passed"] + report["failed"] == report["total_workpieces"]
        assert report["passed"] > 0


@pytest.mark.regression
@pytest.mark.gcode
class TestParameterSensitivity:
    """验证回归测试对刀轨算法参数修改的敏感度。

    核心验证：当后处理器关键参数（如安全高度、进给速率）发生改变时，
    G-code输出应与基准产生可检测的差异，回归测试能准确捕获。
    """

    def test_default_safe_z_baseline_match(self):
        """默认参数生成的G-code应与自身完全匹配（同一参数）。"""
        pp1 = FanucPostProcessor(safe_z_height=50.0, decimal_places=3)
        pp2 = FanucPostProcessor(safe_z_height=50.0, decimal_places=3)

        gcode1 = "\n".join(
            [
                pp1.format_header(1),
                "G01 X10.000 Y10.000 F500.000",
                pp1.format_footer(),
            ]
        )
        gcode2 = "\n".join(
            [
                pp2.format_header(1),
                "G01 X10.000 Y10.000 F500.000",
                pp2.format_footer(),
            ]
        )

        result = compare_gcode(gcode1, gcode2, DEFAULT_TOLERANCE)
        assert result.is_within_tolerance, (
            f"相同参数应完全匹配: {result.differences[:3]}"
        )
        assert result.match_score == 1.0

    def test_safe_z_sensitivity_detection(self):
        """修改安全高度后，回归测试应检测到G-code差异。

        参数变更: safe_z_height 50.0 → 25.0
        预期影响: 所有G43 Hxx Z值、安全回退Z坐标均改变
        """
        pp_baseline = FanucPostProcessor(safe_z_height=50.0, decimal_places=3)
        pp_modified = FanucPostProcessor(safe_z_height=25.0, decimal_places=3)

        baseline_gcode = "\n".join(
            [
                pp_baseline.format_header(1),
                "G01 X10.000 Y10.000 F500.000",
                "G00 Z50.000",
                pp_baseline.format_footer(),
            ]
        )

        modified_gcode = "\n".join(
            [
                pp_modified.format_header(1),
                "G01 X10.000 Y10.000 F500.000",
                "G00 Z25.000",
                pp_modified.format_footer(),
            ]
        )

        result = compare_gcode(
            baseline_gcode,
            modified_gcode,
            {
                **DEFAULT_TOLERANCE,
                "coordinate_precision": 0.001,
            },
        )

        assert not result.is_within_tolerance, "参数修改后回归测试应检测到差异！"
        assert result.match_score < 1.0, f"匹配度应为 {result.match_score} < 1.0"

    def test_feed_rate_sensitivity_detection(self):
        """修改进给速率参数后，回归测试应检测到差异。

        参数变更: rapid_feed 10000 → 5000
        预期影响: tool_change/cycle_drill中所有F指令值减半
        """
        pp_baseline = FanucPostProcessor(rapid_feed=10000, decimal_places=3)
        pp_modified = FanucPostProcessor(rapid_feed=5000, decimal_places=3)

        baseline_gcode = "\n".join(
            [
                pp_baseline.format_header(1),
                pp_baseline.format_tool_change(1, length_comp=-5.0, radius_comp=3.0),
                pp_baseline.format_cycle_drill(10.0, 20.0, 0.0, 15.0, dwell=0.5),
                pp_baseline.format_footer(),
            ]
        )

        modified_gcode = "\n".join(
            [
                pp_modified.format_header(1),
                pp_modified.format_tool_change(1, length_comp=-5.0, radius_comp=3.0),
                pp_modified.format_cycle_drill(10.0, 20.0, 0.0, 15.0, dwell=0.5),
                pp_modified.format_footer(),
            ]
        )

        result = compare_gcode(
            baseline_gcode,
            modified_gcode,
            {
                **DEFAULT_TOLERANCE,
                "coordinate_precision": 0.001,
                "feed_rate_tolerance_percent": 1.0,
            },
        )

        assert not result.is_within_tolerance, (
            f"进给速率修改后应检测到差异！匹配度: {result.match_score:.4f}"
        )

    def test_decimal_places_sensitivity_detection(self):
        """修改小数位数参数后，回归测试应检测到差异。

        参数变更: decimal_places 3 → 2
        预期影响: 坐标值截断 (如 1.234m → "1.230" vs "1.23")
        """
        pp_baseline = FanucPostProcessor(decimal_places=3)
        pp_modified = FanucPostProcessor(decimal_places=2)

        baseline_gcode = "\n".join(
            [
                pp_baseline.format_header(1),
                pp_baseline.format_arc(
                    start=(1.234, 5.678, 0.0),
                    end=(9.876, 3.210, 0.0),
                    center=(5.555, 4.444, 0.0),
                    clockwise=True,
                ),
                pp_baseline.format_footer(),
            ]
        )

        modified_gcode = "\n".join(
            [
                pp_modified.format_header(1),
                pp_modified.format_arc(
                    start=(1.234, 5.678, 0.0),
                    end=(9.876, 3.210, 0.0),
                    center=(5.555, 4.444, 0.0),
                    clockwise=True,
                ),
                pp_modified.format_footer(),
            ]
        )

        result = compare_gcode(
            baseline_gcode,
            modified_gcode,
            {
                **DEFAULT_TOLERANCE,
                "coordinate_precision": 0.0001,
                "feed_rate_tolerance_percent": 0.5,
            },
        )

        assert not result.is_within_tolerance, (
            f"小数位修改后应检测到差异！匹配度: {result.match_score:.4f}"
        )
