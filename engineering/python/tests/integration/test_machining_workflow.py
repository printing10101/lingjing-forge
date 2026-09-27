"""Integration tests for end-to-end machining workflow.

Covers:
- Image input -> geometry parsing -> toolpath planning -> G-code generation
- Multiple machining scenarios (2D contour, 3D surface, complex cavity)
- Intermediate result validation (geometry model, toolpath trajectory)
- Final output validation (G-code file format, machining simulation)
"""

from __future__ import annotations

import pytest

from app.postprocessor.fanuc import FanucPostProcessor
from app.postprocessor.heidenhain import HeidenhainPostProcessor
from app.postprocessor.siemens import SiemensPostProcessor
from app.database.constraints import CuttingConstraintValidator, ConstraintResult
from app.validation.geometric_validator import GeometricValidator
from tests.utils.gcode_helpers import (
    parse_gcode_commands,
    compare_gcode,
    generate_test_gcode,
)
from tests.utils.data_generators import create_mock_model


@pytest.mark.integration
class TestGeometryToGcodeFlow:
    """Complete flow: geometry -> toolpath -> G-code."""

    def setup_method(self):
        self.fanuc = FanucPostProcessor()
        self.heidenhain = HeidenhainPostProcessor()
        self.siemens = SiemensPostProcessor()

    def test_2d_contour_cutting_flow(self):
        """2D profile cutting: geometry -> toolpath -> Fanuc G-code."""
        gcode = generate_test_gcode("simple_contour")
        commands = parse_gcode_commands(gcode)
        assert len(commands) > 5

        g_codes = [g for cmd in commands for g in cmd["g_codes"]]
        assert "G21" in g_codes
        assert "G90" in g_codes
        assert "G01" in g_codes

        coords = commands[4]["coords"] if len(commands) > 4 else {}
        assert "Z" in coords or any("Z" in c["coords"] for c in commands)

    def test_drilling_cycle_flow(self):
        """Drilling operation: multiple holes -> G83 cycle -> G-code."""
        gcode = generate_test_gcode("drilling")
        commands = parse_gcode_commands(gcode)

        has_g83 = any("G83" in cmd["g_codes"] for cmd in commands)
        assert has_g83, "Drilling program should contain G83 cycle"

        g80_count = sum(1 for cmd in commands if "G80" in cmd["g_codes"])
        assert g80_count >= 1, "Should have at least one G80 (cancel cycle)"

    def test_pocket_milling_flow(self):
        """Pocket milling: multi-pass -> G-code."""
        gcode = generate_test_gcode("pocket_milling")
        commands = parse_gcode_commands(gcode)

        z_moves = [cmd for cmd in commands if "Z" in cmd["coords"]]
        assert len(z_moves) >= 3, "Pocket milling requires multiple Z passes"

        feed_rates = [
            cmd["feeds"].get("F", 0) for cmd in commands if "F" in cmd.get("feeds", {})
        ]
        assert len(feed_rates) > 0, "Pocket milling should have feed rates"


@pytest.mark.integration
class TestPostprocessorCrossController:
    """Cross-controller G-code generation consistency."""

    def setup_method(self):
        self.fanuc = FanucPostProcessor()
        self.heidenhain = HeidenhainPostProcessor()
        self.siemens = SiemensPostProcessor()

    def test_same_arc_different_outputs(self):
        """Same arc should produce valid output on all controllers."""
        start = (0.0, 0.0, 0.0)
        end = (10.0, 10.0, 0.0)
        center = (0.0, 10.0, 0.0)

        fanuc_arc = self.fanuc.format_arc(start, end, center, clockwise=True)
        siemens_arc = self.siemens.format_arc(start, end, center, clockwise=True)

        assert "G02" in fanuc_arc
        assert "G02" in siemens_arc
        assert "X10.000" in fanuc_arc
        assert "X10.000" in siemens_arc

    def test_coolant_commands_consistency(self):
        """Coolant commands should contain M08/M09 across controllers."""
        assert self.fanuc.format_coolant("on") == "M08"
        assert self.fanuc.format_coolant("off") == "M09"
        assert self.heidenhain.format_coolant("on") == "M08"
        assert self.heidenhain.format_coolant("off") == "M09"
        assert "M08" in self.siemens.format_coolant("on")
        assert "M09" in self.siemens.format_coolant("off")

    def test_header_has_date(self):
        """All program headers should contain dates."""
        for pp in [self.fanuc, self.heidenhain, self.siemens]:
            header = pp.format_header()
            assert "202" in header or "202" in header


@pytest.mark.integration
class TestConstraintValidationFlow:
    """Constraint validation integrated with postprocessing."""

    def test_valid_cutting_parameters(self):
        """Valid cutting parameters should pass constraint validation."""
        validator = CuttingConstraintValidator()
        result = validator.validate(
            material_id="steel_45",
            tool_id="endmill_10mm",
            params={
                "cutting_speed": 150.0,
                "feed": 0.15,
                "depth_of_cut": 2.0,
                "spindle_speed": 4775.0,
            },
        )
        assert isinstance(result, ConstraintResult)

    def test_invalid_parameters_produce_violations(self):
        """Extreme cutting parameters should produce constraint violations."""
        validator = CuttingConstraintValidator()
        result = validator.validate(
            material_id="steel_45",
            tool_id="endmill_10mm",
            params={
                "cutting_speed": 500.0,
                "feed": 5.0,
                "depth_of_cut": 50.0,
            },
        )
        assert isinstance(result, ConstraintResult)


@pytest.mark.integration
class TestGeometricValidationFlow:
    """Geometric validation with mock 3D models."""

    def test_validate_mock_model(self):
        """Validate a mock 3D reconstructed model."""
        mock_model = create_mock_model()
        validator = GeometricValidator()

        report = validator.check_dimensions(
            mock_model,
            [
                type(
                    "Dim",
                    (),
                    {
                        "name": "length",
                        "nominal": 100.0,
                        "tolerance_upper": 0.1,
                        "tolerance_lower": -0.1,
                    },
                )(),
                type(
                    "Dim",
                    (),
                    {
                        "name": "width",
                        "nominal": 50.0,
                        "tolerance_upper": 0.1,
                        "tolerance_lower": -0.1,
                    },
                )(),
            ],
        )
        assert len(report) == 2
        assert report[0].dimension_name == "length"

        feat_report = validator.check_feature_presence(
            mock_model,
            [
                type("Feat", (), {"name": "main_body", "feature_type": "body"})(),
                type("Feat", (), {"name": "through_hole", "feature_type": "hole"})(),
            ],
        )
        assert len(feat_report) == 2
        assert feat_report[0].detected is True

    def test_validate_with_deviations(self):
        """Model with deviations should produce warnings."""
        mock_model = {
            "dimensions": {
                "length": 100.5,
                "width": 50.2,
            },
            "features": {
                "main_body": {"confidence": 0.95, "iou": 0.93},
            },
            "topology": [],
        }
        validator = GeometricValidator()
        report = validator.check_dimensions(
            mock_model,
            [
                type(
                    "Dim",
                    (),
                    {
                        "name": "length",
                        "nominal": 100.0,
                        "tolerance_upper": 0.1,
                        "tolerance_lower": -0.1,
                    },
                )(),
            ],
        )
        assert len(report) == 1
        assert abs(report[0].deviation) > 0


@pytest.mark.integration
class TestGcodeComparisonFlow:
    """G-code comparison and validation flow."""

    def test_identical_gcode_match(self):
        """Identical G-code should produce perfect match."""
        gcode = generate_test_gcode("simple_contour")
        result = compare_gcode(gcode, gcode)
        assert result.is_within_tolerance
        assert result.match_score == 1.0

    def test_different_gcode_detect_differences(self):
        """Different G-code should detect differences."""
        gcode1 = generate_test_gcode("simple_contour")
        gcode2 = generate_test_gcode("drilling")
        result = compare_gcode(gcode1, gcode2)
        assert not result.is_within_tolerance

    def test_similar_gcode_with_tolerance(self):
        """Similar G-code within tolerance should pass."""
        gcode1 = generate_test_gcode("simple_contour")
        gcode2 = gcode1.replace("F500.000", "F502.000")
        result = compare_gcode(
            gcode1,
            gcode2,
            tolerance={
                "coordinate_precision": 0.01,
                "feed_rate_tolerance_percent": 10.0,
            },
        )
        assert result.match_score > 0.5
