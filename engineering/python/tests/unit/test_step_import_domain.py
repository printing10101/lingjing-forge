"""T2 step_import 域补测：真实 STEP 解析与 STL 转换。"""

from __future__ import annotations

from pathlib import Path

import cadquery as cq
import pytest

from app.step_import.step_converter import (
    PRECISION_PRESETS,
    BatchConvertResult,
    StepConverter,
    StlExportOptions,
)
from app.step_import.step_parser import (
    BoundingBox,
    StepParseError,
    StepParser,
)

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def step_file(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("step_import") / "box_60x40x10.step"
    cq.exporters.export(cq.Workplane("XY").box(60, 40, 10), str(path))
    return path


def test_parse_real_step_reports_geometry(step_file: Path):
    result = StepParser().parse(step_file)
    assert result.success is True
    info = result.model_info
    assert info.volume == pytest.approx(24000.0, rel=1e-3)
    assert info.surface_area == pytest.approx(6800.0, rel=1e-3)
    assert info.bounding_box.length == pytest.approx(60.0, abs=1e-3)
    assert info.bounding_box.width == pytest.approx(40.0, abs=1e-3)
    assert info.bounding_box.height == pytest.approx(10.0, abs=1e-3)
    assert info.face_count == 6 and info.solid_count == 1
    assert len(result.entities) == 1


def test_get_cadquery_shape_matches_volume(step_file: Path):
    shape = StepParser().get_cadquery_shape(step_file)
    assert shape.Volume() == pytest.approx(24000.0, rel=1e-3)


def test_parse_missing_file_raises():
    with pytest.raises((StepParseError, FileNotFoundError)):
        StepParser().parse("no_such_file.step")


def test_bounding_box_dataclass_roundtrip():
    box = BoundingBox(length=10.0, width=20.0, height=30.0,
                      min_point=(0.0, 0.0, 0.0), max_point=(10.0, 20.0, 30.0))
    assert (box.length, box.width, box.height) == (10.0, 20.0, 30.0)


@pytest.mark.parametrize("preset", ["low", "medium", "high"])
def test_export_options_tolerance_conversion(preset: str):
    options = PRECISION_PRESETS[preset]
    assert options.linear_tolerance == pytest.approx(options.linear_deflection / 1000.0)
    assert options.angular_tolerance == pytest.approx(
        __import__("math").radians(options.angular_deflection))


def test_convert_to_stl_writes_file(tmp_path: Path):
    converter = StepConverter(output_dir=tmp_path, base_url="/api/import/step/output")
    shape = cq.Workplane("XY").box(60, 40, 10).val()
    result = converter.convert_to_stl(shape, "part_demo", options=PRECISION_PRESETS["low"])
    assert result.format.lower() == "stl"
    assert result.file_size > 0
    assert Path(result.stl_path).exists()
    assert result.stl_url.startswith("/api/import/step/output")
    assert result.face_count >= 1


def test_batch_convert_result_success_semantics():
    assert BatchConvertResult().success is True
    failed = BatchConvertResult(errors=["boom"])
    assert failed.success is False
    custom = StlExportOptions(linear_deflection=0.002, angular_deflection=0.2)
    assert custom.linear_tolerance == pytest.approx(2e-6)


def test_convert_to_brep_writes_file(tmp_path: Path):
    converter = StepConverter(output_dir=tmp_path)
    shape = cq.Workplane("XY").box(20, 10, 5).val()
    result = converter.convert_to_brep(shape, "part_brep")
    assert result.format.lower() == "brep"
    assert Path(result.stl_path).exists()
    assert result.file_size > 0


def test_parse_invalid_step_reports_failure(tmp_path: Path):
    bad = tmp_path / "broken.step"
    bad.write_text("这不是 STEP 文件", encoding="utf-8")
    parser = StepParser()
    try:
        result = parser.parse(bad)
    except (StepParseError, ValueError, OSError):
        return
    assert result.success is False


def test_parse_multiple_solids(tmp_path: Path):
    compound = (
        cq.Workplane("XY").box(10, 10, 10)
        .union(cq.Workplane("XY").transformed(offset=(30, 0, 0)).box(10, 10, 10))
    )
    path = tmp_path / "two_solids.step"
    cq.exporters.export(compound, str(path))
    result = StepParser().parse(path)
    assert result.success is True
    assert len(result.entities) >= 1
    assert result.model_info.volume == pytest.approx(2000.0, rel=1e-2)
