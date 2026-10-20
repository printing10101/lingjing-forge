"""Guard against false-green NC validation and ineffective feedback policies."""
import json
from pathlib import Path

import pytest

from app.pipelines.drawing_feedback import acceptance, cutting_parameters, drawing_contract, load_policy, run_drawing_feedback, write_json

pytestmark = pytest.mark.unit


def complete_report():
    return {"passed": True, "dimensions": [
        {"name": "syntax", "status": "evaluated", "passed": True},
        {"name": "geometry", "status": "evaluated", "passed": True,
         "detail": {"removed_voxel_count": 20, "cutting_segments": 3}},
        {"name": "physics", "status": "evaluated", "passed": True},
    ]}


@pytest.mark.parametrize("dimension", [0, 1, 2])
@pytest.mark.parametrize("status", ["skipped", "error"])
def test_skipped_or_failed_dimension_is_never_accepted(dimension, status):
    report = complete_report()
    report["dimensions"][dimension]["status"] = status
    assert not acceptance(report)[0]


def test_collision_free_air_cut_is_rejected_even_with_high_score():
    report = complete_report()
    report["composite_score"] = 100
    report["dimensions"][1]["detail"]["removed_voxel_count"] = 0
    passed, reasons = acceptance(report)
    assert not passed
    assert any("NO_MATERIAL_REMOVAL" in reason for reason in reasons)
    report["dimensions"][1]["detail"]["cutting_segments"] = 0
    assert any("NO_CUTTING_SEGMENTS" in reason for reason in acceptance(report)[1])


def test_pass_requires_all_actual_verdicts():
    report = complete_report()
    assert acceptance(report) == (True, [])
    report["passed"] = False
    assert not acceptance(report)[0]
    report = complete_report()
    report["dimensions"][2]["passed"] = False
    assert not acceptance(report)[0]
    assert not acceptance({})[0]


def test_cutting_parameters_use_engaged_nc_moves():
    nc = "G90\nG00 Z80\nS1850 M03\nG01 Z80 F10000\nG00 Z12\nG01 X5 Y5 Z8 F137.5\nG01 X15 Y5 Z8 F275\nG00 Z12\nG01 X5 Y5 Z6 F275"
    params = cutting_parameters(nc, 10)
    assert params["spindle_rpm"] == 1850
    assert params["feed_rate"] == 275
    assert params["depth_of_cut"] == 2
    assert cutting_parameters("G00 Z80\nG01 X20 Z50 F275", 10) is None


def test_interleaved_layers_from_different_operations_do_not_understate_cut_depth():
    nc = "S1850 M03\n; ---- OP01 face\nG01 X5 Z8 F275\nG01 X10 Z6 F275\n; ---- OP02 profile\nG01 X5 Z8.8 F275\nG01 X10 Z7.6 F275"
    assert cutting_parameters(nc, 10)["depth_of_cut"] == 2


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_generator_refuses_invalid_stock_top_and_stepdown(value):
    from app.process_planning.gcode_generator import GCodeGenerator

    generator = GCodeGenerator()
    with pytest.raises(ValueError, match="INVALID_STOCK_TOP"):
        generator.generate(None, stock_top_z=value)
    with pytest.raises(ValueError, match="INVALID_STEPDOWN"):
        generator.generate(None, max_stepdown=value)


@pytest.mark.parametrize("change", [
    {"hole_count": 1}, {"contour_count": 0}, {"contour_count": 2},
    {"overall_height": 0}, {"overall_width": float("nan")}, {"overall_length": -1},
])
def test_unsupported_or_invalid_geometry_is_rejected(change):
    summary = {"hole_count": 0, "contour_count": 1, "overall_length": 80, "overall_width": 50, "overall_height": 10}
    summary.update(change)
    with pytest.raises(ValueError):
        drawing_contract(summary)


def test_policy_cannot_leak_between_drawings_or_change_z_without_revalidation(tmp_path):
    path = tmp_path / "policy.json"
    policy = {"source_sha256": "a" * 64, "status": "accepted", "stock_top_z": 10}
    write_json(path, policy)
    assert load_policy(path, "a" * 64, 10) == policy
    with pytest.raises(ValueError, match="POLICY_MISMATCH"):
        load_policy(path, "b" * 64, 10)
    with pytest.raises(ValueError, match="POLICY_Z_MISMATCH"):
        load_policy(path, "a" * 64, 15)
    policy["status"] = "rejected"
    write_json(path, policy)
    with pytest.raises(ValueError, match="POLICY_MISMATCH"):
        load_policy(path, "a" * 64, 10)


def test_real_dxf_feedback_changes_regeneration_and_rejects_regression(tmp_path):
    source = Path(__file__).resolve().parents[4] / "data/test_fixtures/case3_polyline_outer.dxf"
    output = tmp_path / "experiment"
    result = run_drawing_feedback(source, output)
    assert result["completed"]
    assert result["baseline_accepted"] is False
    assert result["candidate_accepted"] and result["replay_accepted"]
    assert result["nc_changed"] and result["replay_nc_matches_candidate"]
    assert result["failed_candidate_rejected"] and result["active_policy_unchanged_after_rejection"]
    policy = json.loads((output / "active_policy.json").read_text(encoding="utf-8"))
    assert 0 < policy["max_stepdown"] <= 1
    assert Path(result["final_nc"]).is_file()
    report = json.loads((output / "policy_replay/validation.json").read_text(encoding="utf-8"))
    assert all(d["status"] == "evaluated" for d in report["dimensions"])
    assert report["dimensions"][1]["detail"]["removed_voxel_count"] > 0
    assert report["cutting_parameters_from_nc"]["depth_of_cut"] <= 1.00001
    with pytest.raises(FileExistsError):
        run_drawing_feedback(source, output)


def test_invalid_source_does_not_create_output(tmp_path):
    source = tmp_path / "part.step"
    source.write_text("not DXF")
    with pytest.raises(ValueError, match="INVALID_DRAWING"):
        run_drawing_feedback(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_circular_plate_boundary_is_not_a_hole_and_through_depth_is_plate_height():
    from app.dxf.feature_extractor import FeatureExtractor
    from app.dxf.dxf_to_model import DxfToModelConverter

    source = Path(__file__).resolve().parents[4] / "data/test_fixtures/case6_flange.dxf"
    features = FeatureExtractor().extract(source)
    assert len(features.holes) == 7  # central bore + six bolt holes
    assert sorted(h.diameter for h in features.holes) == [16] * 6 + [60]
    assert all(h.depth == features.overall_height for h in features.holes)
    model = DxfToModelConverter().convert(features)
    assert model.success
    import math

    expected_volume = math.pi * (100**2 - 30**2 - 6 * 8**2) * 10
    assert model.workplane.val().Volume() == pytest.approx(expected_volume, rel=1e-5)


def test_separate_circles_are_not_silently_reinterpreted_as_plate_boundary(tmp_path):
    import ezdxf
    from app.dxf.feature_extractor import FeatureExtractor

    doc = ezdxf.new()
    for x, radius in [(0, 20), (100, 5)]:
        doc.modelspace().add_circle((x, 0), radius)
    source = tmp_path / "disjoint.dxf"
    doc.saveas(source)
    assert len(FeatureExtractor().extract(source).holes) == 2
