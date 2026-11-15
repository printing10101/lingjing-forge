"""T6 cad 域补测（二）：高级特征与 B-rep 校验。"""

from __future__ import annotations

import cadquery as cq
import pytest

from app.cad._brep_validator import (
    BrepValidationError,
    raise_if_invalid,
    sanitize_dimensions,
    validate_workplane,
)
from app.cad.advanced_features import AdvancedFeatureBuilder

pytestmark = pytest.mark.unit


def _box() -> cq.Workplane:
    return cq.Workplane("XY").box(50, 30, 20)


def test_chamfer_and_fillet_reduce_volume():
    builder = AdvancedFeatureBuilder()
    base_volume = _box().val().Volume()

    chamfered = builder.apply_features(_box().edges(), [{"type": "chamfer", "length": 1.0}])
    assert 0 < chamfered.val().Volume() < base_volume

    filleted = builder.apply_features(_box().edges(), [{"type": "fillet", "radius": 1.0}])
    assert 0 < filleted.val().Volume() < base_volume


def test_step_and_slot_cut_material():
    builder = AdvancedFeatureBuilder()
    base_volume = _box().val().Volume()

    stepped = builder.apply_features(_box(), [{"type": "step", "height": 15.0}])
    assert stepped.val().Volume() < base_volume

    slotted = builder.apply_features(_box(), [
        {"type": "slot", "center_x": 0.0, "center_y": 0.0, "length": 20.0, "width": 5.0, "depth": 2.5}])
    assert slotted.val().Volume() < base_volume


def test_unknown_feature_is_skipped_and_invalid_spec_does_not_crash():
    builder = AdvancedFeatureBuilder()
    base_volume = _box().val().Volume()
    unchanged = builder.apply_features(_box(), [{"type": "engrave", "text": "x"}])
    assert unchanged.val().Volume() == pytest.approx(base_volume)

    # 过大的倒角参数会被 cadquery 拒绝，构造器必须吞掉异常并返回原对象
    survived = builder.apply_features(_box().edges(), [{"type": "chamfer", "length": 999.0}])
    assert survived is not None


def test_validate_workplane_accepts_valid_solid():
    report = validate_workplane(_box())
    assert report.errors == []
    assert report.is_valid is True
    assert "校验通过" in report.summary()


def test_validate_workplane_flags_multi_solid():
    multi = cq.Workplane("XY").box(10, 10, 10).union(
        cq.Workplane("XY").transformed(offset=(30, 0, 0)).box(10, 10, 10))
    report = validate_workplane(multi)
    assert any(issue.code.startswith("WARN") or issue.severity == "warning" for issue in report.issues)


def test_raise_if_invalid_raises_for_invalid_report():
    report = validate_workplane(_box())
    report.is_valid = False
    with pytest.raises(BrepValidationError):
        raise_if_invalid(report)
    raise_if_invalid(None)  # None 直接放行


def test_sanitize_dimensions_clamps_and_scales():
    sanitized = sanitize_dimensions({"dimensions": {"length": -5, "width": 0.001, "height": 20}})
    dims = sanitized["dimensions"]
    assert dims["length"] > 0 and dims["width"] > 0
    assert dims["length"] * dims["width"] * dims["height"] > 0

    tiny = sanitize_dimensions({"dimensions": {"length": 0.001, "width": 0.001, "height": 0.001}})
    product = tiny["dimensions"]["length"] * tiny["dimensions"]["width"] * tiny["dimensions"]["height"]
    assert product >= 1e-6  # 体积下限（DEFAULT_MIN_VOLUME）兜底后仍为正
    assert min(tiny["dimensions"].values()) >= 1e-3
    # 非 dict 输入原样返回
    assert sanitize_dimensions({"dimensions": None})["dimensions"] is None


@pytest.mark.parametrize("shape", ["box", "sphere", "cylinder", "cone"])
def test_generator_exports_real_stl(shape):
    from pathlib import Path

    from app.cad.cadquery_gen import CadQueryGenerator

    path = CadQueryGenerator().generate_3d_model(
        {"shape_type": shape, "dimensions": {"length": 40, "width": 20, "height": 30}})
    export = Path(path)
    assert export.exists() and export.stat().st_size > 0
    assert export.suffix == ".stl"


def test_generator_with_features_applies_chamfer():
    from pathlib import Path

    from app.cad.cadquery_gen import CadQueryGenerator

    path = CadQueryGenerator().generate_with_features(
        {"shape_type": "box", "dimensions": {"length": 40, "width": 20, "height": 30}},
        features=[{"type": "chamfer", "length": 1.0}])
    assert Path(path).exists()
