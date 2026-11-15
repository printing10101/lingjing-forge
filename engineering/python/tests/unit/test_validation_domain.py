"""T5 validation 域补测：数据集、元数据与几何校验器。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.validation.benchmark_dataset import (
    BenchmarkDataset,
    DimensionSpec,
    FeatureDef,
    PartMetadata,
    TopologyRelation,
)
from app.validation.geometric_validator import GeometricValidator

pytestmark = pytest.mark.unit


def _metadata(part_id: str = "p1") -> PartMetadata:
    return PartMetadata(
        part_id=part_id,
        part_name="测试板",
        part_type="plate",
        material="45#钢",
        overall_dimensions={"length": 100.0, "width": 80.0, "height": 10.0},
        features=[
            FeatureDef(name="H001", feature_type="through_hole", parameters={"diameter": 6.0},
                       bbox=(5.0, 5.0, 11.0, 11.0), bbox_3d=(5.0, 5.0, 0.0, 11.0, 11.0, 10.0)),
            FeatureDef(name="P001", feature_type="plane", area=8000.0),
        ],
        dimensions=[DimensionSpec(name="length", nominal=100.0, tolerance_upper=0.1, tolerance_lower=-0.1),
                    DimensionSpec(name="height", nominal=10.0, tolerance_upper=0.05, tolerance_lower=-0.05)],
        topology=[TopologyRelation(feature_a="H001", feature_b="P001", relation="on_surface")],
    )


def _dataset(tmp_path: Path) -> BenchmarkDataset:
    part_dir = tmp_path / "p1"
    (part_dir / "input_views").mkdir(parents=True)
    (part_dir / "ground_truth").mkdir()
    (part_dir / "input_views" / "view1.svg").write_text("<svg/>", encoding="utf-8")
    (part_dir / "ground_truth" / "p1.step").write_text("ISO-10303-21;", encoding="utf-8")
    (part_dir / "metadata.json").write_text(
        json.dumps(_metadata().to_dict(), ensure_ascii=False), encoding="utf-8")
    return BenchmarkDataset(root_dir=str(tmp_path))


def test_part_metadata_roundtrip():
    original = _metadata()
    restored = PartMetadata.from_dict(original.to_dict())
    assert restored.part_id == original.part_id
    assert restored.overall_dimensions == original.overall_dimensions
    assert [feature.name for feature in restored.features] == ["H001", "P001"]
    assert restored.dimensions[0].nominal == 100.0
    assert restored.topology[0].relation == "on_surface"


def test_dataset_navigation_helpers(tmp_path: Path):
    dataset = _dataset(tmp_path)
    assert dataset.list_parts() == ["p1"]
    metadata = dataset.load_metadata("p1")
    assert metadata.part_name == "测试板"
    # 缓存命中：第二次返回同一对象
    assert dataset.load_metadata("p1") is metadata
    assert dataset.get_input_views_dir("p1").name == "input_views"
    assert dataset.get_step_path("p1").suffix == ".step"
    assert dataset.get_obj_path("p1") is None
    assert [path.name for path in dataset.get_svg_views("p1")] == ["view1.svg"]
    assert dataset.get_png_views("p1") == []


def test_dataset_missing_paths_raise(tmp_path: Path):
    dataset = _dataset(tmp_path)
    with pytest.raises(FileNotFoundError):
        dataset.load_metadata("missing")
    with pytest.raises(FileNotFoundError):
        dataset.get_ground_truth_dir("missing")


def test_check_dimensions_marks_within_and_out_of_tolerance():
    validator = GeometricValidator()
    results = validator.check_dimensions(
        {"dimensions": {"length": 100.05, "height": 10.3}}, _metadata().dimensions)
    by_name = {result.dimension_name: result for result in results}
    assert by_name["length"].within_tolerance is True
    assert by_name["length"].deviation == pytest.approx(0.05)
    assert by_name["height"].within_tolerance is False
    assert by_name["height"].deviation_percent == pytest.approx(3.0)


def test_check_feature_presence_confidence_and_iou():
    validator = GeometricValidator()
    model = {"features": {"H001": {"confidence": 0.92, "iou": 0.81}}}
    results = validator.check_feature_presence(model, _metadata().features)
    by_name = {result.feature_name: result for result in results}
    assert by_name["H001"].detected is True
    assert by_name["H001"].confidence == pytest.approx(0.92)
    assert by_name["P001"].detected is False


def test_validate_reconstruction_rejects_missing_model(tmp_path: Path):
    """生产路径必须显式传重建模型（学术诚信硬约束）。"""
    validator = GeometricValidator(dataset=_dataset(tmp_path))
    with pytest.raises(ValueError, match="allow_mock_fallback"):
        validator.validate_reconstruction("p1")


def test_validate_reconstruction_builds_report_and_html(tmp_path: Path):
    validator = GeometricValidator(dataset=_dataset(tmp_path))
    model = {
        "dimensions": {"length": 100.02, "height": 9.98},
        "features": {"H001": {"confidence": 0.9, "iou": 0.85}, "P001": {"confidence": 0.99, "iou": 0.95}},
    }
    report = validator.validate_reconstruction("p1", reconstructed_model=model)
    payload = report.to_dict()
    assert payload["part_id"] == "p1"
    assert payload["dimension_checks"]
    assert payload["feature_checks"]
    assert report.to_json()

    html = validator.generate_report(report, output_path=str(tmp_path / "report.html"))
    assert "p1" in html
    assert (tmp_path / "report.html").exists()
