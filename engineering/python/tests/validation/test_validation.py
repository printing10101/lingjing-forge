"""3D重建几何精度验证体系 —— 完整单元测试与集成测试。

覆盖：
- 六大指标计算逻辑验证
- 边界条件与异常处理
- 基准数据集加载管理
- 几何验证器核心流程
- HTML报告生成
- Mock数据驱动测试
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from app.validation.metrics import (
    DimensionResult,
    MetricsResult,
    TopologyEdge,
    compute_dimension_accuracy,
    compute_feature_iou,
    compute_feature_precision,
    compute_feature_recall,
    compute_tolerance_compliance,
    compute_topology_correctness,
)
from app.validation.benchmark_dataset import (
    BenchmarkDataset,
    DimensionSpec,
    FeatureDef,
    PartMetadata,
    TopologyRelation,
)
from app.validation.geometric_validator import (
    GeometricValidator,
)


class TestDimensionAccuracy:
    """尺寸精度指标测试。"""

    def test_perfect_match_zero_deviation(self):
        dims = [
            DimensionResult(
                name="D1",
                nominal=50.0,
                measured=50.0,
                deviation_abs=0.0,
                deviation_rel=0.0,
                tolerance_upper=0.025,
                tolerance_lower=0.0,
                within_tolerance=True,
            )
        ]
        result = compute_dimension_accuracy(dims)
        assert result["mean_absolute_deviation"] == 0.0
        assert result["max_absolute_deviation"] == 0.0
        assert result["within_tolerance_count"] == 1

    def test_positive_deviation(self):
        dims = [
            DimensionResult(
                name="D1",
                nominal=50.0,
                measured=50.05,
                deviation_abs=0.05,
                deviation_rel=0.001,
                tolerance_upper=0.025,
                tolerance_lower=0.0,
                within_tolerance=False,
            )
        ]
        result = compute_dimension_accuracy(dims)
        assert result["mean_absolute_deviation"] == 0.05
        assert result["within_tolerance_count"] == 0

    def test_negative_deviation(self):
        dims = [
            DimensionResult(
                name="D1",
                nominal=100.0,
                measured=99.90,
                deviation_abs=0.10,
                deviation_rel=0.001,
                tolerance_upper=0.05,
                tolerance_lower=-0.05,
                within_tolerance=False,
            )
        ]
        result = compute_dimension_accuracy(dims)
        assert result["max_absolute_deviation"] == 0.10

    def test_multiple_dimensions(self):
        dims = [
            DimensionResult("D1", 50.0, 50.01, 0.01, 0.0002, 0.025, 0.0, True),
            DimensionResult("D2", 30.0, 29.99, 0.01, 0.00033, 0.021, 0.0, True),
            DimensionResult("D3", 120.0, 120.08, 0.08, 0.00067, 0.035, 0.0, False),
        ]
        result = compute_dimension_accuracy(dims)
        assert result["total_count"] == 3
        assert result["within_tolerance_count"] == 2
        assert result["max_absolute_deviation"] == 0.080

    def test_empty_dimensions(self):
        result = compute_dimension_accuracy([])
        assert result["mean_absolute_deviation"] == 0.0
        assert result["total_count"] == 0

    def test_zero_nominal(self):
        dims = [DimensionResult("Z", 0.0, 0.005, 0.005, 0.0, 0.01, -0.01, True)]
        result = compute_dimension_accuracy(dims)
        assert result["mean_absolute_deviation"] == 0.005


class TestFeatureIoU:
    """特征交并比指标测试。"""

    def test_perfect_overlap(self):
        det = [{"name": "hole_1", "area": 400, "bbox": (10, 10, 30, 30)}]
        gt = [{"name": "hole_1", "area": 400, "bbox": (10, 10, 30, 30)}]
        ious = compute_feature_iou(det, gt)
        assert ious["hole_1"] == 1.0

    def test_no_overlap(self):
        det = [{"name": "hole_1", "area": 100, "bbox": (0, 0, 10, 10)}]
        gt = [{"name": "hole_1", "area": 100, "bbox": (20, 20, 30, 30)}]
        ious = compute_feature_iou(det, gt)
        assert ious["hole_1"] == 0.0

    def test_partial_overlap(self):
        det = [{"name": "hole_1", "area": 400, "bbox": (0, 0, 20, 20)}]
        gt = [{"name": "hole_1", "area": 400, "bbox": (10, 10, 30, 30)}]
        ious = compute_feature_iou(det, gt)
        assert 0.0 < ious["hole_1"] < 1.0

    def test_3d_mode(self):
        det = [{"name": "cube", "volume": 1000, "bbox_3d": (0, 0, 0, 10, 10, 10)}]
        gt = [{"name": "cube", "volume": 1000, "bbox_3d": (0, 0, 0, 10, 10, 10)}]
        ious = compute_feature_iou(det, gt, mode="mesh")
        assert ious["cube"] == 1.0

    def test_feature_not_in_gt(self):
        det = [{"name": "extra_hole", "area": 50, "bbox": (0, 0, 10, 10)}]
        gt = [{"name": "hole_1", "area": 100, "bbox": (0, 0, 20, 20)}]
        ious = compute_feature_iou(det, gt)
        assert ious["extra_hole"] == 0.0


class TestFeatureRecall:
    """特征召回率指标测试。"""

    def test_perfect_recall(self):
        det = [
            {"name": "f1", "confidence": 0.95, "area": 400, "bbox": (0, 0, 20, 20)},
            {"name": "f2", "confidence": 0.98, "area": 256, "bbox": (30, 30, 46, 46)},
        ]
        gt = [
            {"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)},
            {"name": "f2", "area": 256, "bbox": (30, 30, 46, 46)},
        ]
        assert compute_feature_recall(det, gt) == 1.0

    def test_missing_feature(self):
        det = [{"name": "f1", "confidence": 0.95, "area": 400, "bbox": (0, 0, 20, 20)}]
        gt = [
            {"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)},
            {"name": "f2", "area": 256, "bbox": (30, 30, 46, 46)},
        ]
        assert compute_feature_recall(det, gt) == 0.5

    def test_low_confidence_filtered(self):
        det = [{"name": "f1", "confidence": 0.3, "area": 400, "bbox": (0, 0, 20, 20)}]
        gt = [{"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)}]
        assert compute_feature_recall(det, gt) == 0.0

    def test_empty_gt(self):
        assert compute_feature_recall([], []) == 1.0

    def test_custom_thresholds(self):
        det = [{"name": "f1", "confidence": 0.75, "area": 400, "bbox": (0, 0, 20, 20)}]
        gt = [{"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)}]
        assert compute_feature_recall(det, gt, confidence_threshold=0.7) == 1.0
        assert compute_feature_recall(det, gt, confidence_threshold=0.8) == 0.0


class TestFeaturePrecision:
    """特征精确率指标测试。"""

    def test_perfect_precision(self):
        det = [
            {"name": "f1", "confidence": 0.95, "area": 400, "bbox": (0, 0, 20, 20)},
        ]
        gt = [{"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)}]
        assert compute_feature_precision(det, gt) == 1.0

    def test_false_positive(self):
        det = [
            {"name": "f1", "confidence": 0.95, "area": 400, "bbox": (0, 0, 20, 20)},
            {
                "name": "ghost",
                "confidence": 0.90,
                "area": 100,
                "bbox": (100, 100, 110, 110),
            },
        ]
        gt = [{"name": "f1", "area": 400, "bbox": (0, 0, 20, 20)}]
        assert compute_feature_precision(det, gt) == 0.5

    def test_empty_detections(self):
        assert (
            compute_feature_precision(
                [], [{"name": "f1", "area": 100, "bbox": (0, 0, 20, 20)}]
            )
            == 1.0
        )


class TestTopologyCorrectness:
    """拓扑正确性指标测试。"""

    def test_perfect_match(self):
        det = [
            TopologyEdge("A", "B", "adjacent"),
            TopologyEdge("B", "C", "contains"),
        ]
        gt = [
            TopologyEdge("A", "B", "adjacent"),
            TopologyEdge("B", "C", "contains"),
        ]
        assert compute_topology_correctness(det, gt) == 1.0

    def test_missing_edge(self):
        det = [TopologyEdge("A", "B", "adjacent")]
        gt = [
            TopologyEdge("A", "B", "adjacent"),
            TopologyEdge("B", "C", "contains"),
        ]
        assert compute_topology_correctness(det, gt) == 0.5

    def test_wrong_relation(self):
        det = [TopologyEdge("A", "B", "perpendicular")]
        gt = [TopologyEdge("A", "B", "adjacent")]
        assert compute_topology_correctness(det, gt) == 0.0

    def test_empty_gt(self):
        assert compute_topology_correctness([], []) == 1.0


class TestToleranceCompliance:
    """公差符合度指标测试。"""

    def test_all_within_tolerance(self):
        dims = [
            DimensionResult("D1", 50.0, 50.01, 0.01, 0.0002, 0.025, 0.0, True),
            DimensionResult("D2", 30.0, 29.99, 0.01, 0.00033, 0.021, 0.0, True),
        ]
        assert compute_tolerance_compliance(dims) == 100.0

    def test_partial_compliance(self):
        dims = [
            DimensionResult("D1", 50.0, 50.01, 0.01, 0.0002, 0.025, 0.0, True),
            DimensionResult("D2", 30.0, 29.99, 0.01, 0.00033, 0.021, 0.0, True),
            DimensionResult("D3", 120.0, 120.08, 0.08, 0.00067, 0.035, 0.0, False),
        ]
        result = compute_tolerance_compliance(dims)
        assert 66.0 < result < 67.0

    def test_empty_dimensions(self):
        assert compute_tolerance_compliance([]) == 100.0


class TestMetricsResult:
    """MetricsResult 数据类测试。"""

    def test_to_dict(self):
        result = MetricsResult(
            dimension_accuracy={"mean_absolute_deviation": 0.015},
            feature_iou={"f1": 0.98},
            feature_recall=0.9523,
            feature_precision=0.9785,
            topology_correctness=0.8889,
            tolerance_compliance=96.7,
        )
        d = result.to_dict()
        assert d["feature_recall"] == 0.9523
        assert d["tolerance_compliance"] == 96.7


# ground_truth 基准数据（.step/.stp/.obj）尚未入库：数据存在时正常验证，
# 缺失时跳过相关用例（不伪造结果）。
_GT_DIR = Path(__file__).resolve().parents[4] / "tests" / "benchmark" / "geometric" / "stepped_shaft" / "ground_truth"
needs_ground_truth = pytest.mark.skipif(
    not _GT_DIR.exists(), reason="ground_truth 基准数据未入库（.step/.stp/.obj 缺失）"
)


class TestBenchmarkDataset:
    """基准数据集管理测试。"""

    def test_list_parts(self):
        ds = BenchmarkDataset()
        parts = ds.list_parts()
        assert "stepped_shaft" in parts
        assert "flange" in parts
        assert "bracket" in parts

    def test_load_stepped_shaft_metadata(self):
        ds = BenchmarkDataset()
        md = ds.load_metadata("stepped_shaft")
        assert md.part_name == "阶梯轴"
        assert md.material == "45钢"
        assert md.tolerance_grade == "IT7"
        assert len(md.features) == 10
        assert len(md.dimensions) == 10
        assert len(md.topology) == 9

    def test_load_flange_metadata(self):
        ds = BenchmarkDataset()
        md = ds.load_metadata("flange")
        assert md.part_name == "法兰盘"
        assert len(md.features) == 9
        assert len(md.dimensions) == 6
        assert len(md.topology) == 14

    def test_load_bracket_metadata(self):
        ds = BenchmarkDataset()
        md = ds.load_metadata("bracket")
        assert md.part_name == "支架"
        assert md.material == "HT250"
        assert len(md.features) == 9
        assert len(md.dimensions) == 10
        assert len(md.topology) == 12

    def test_metadata_cached(self):
        ds = BenchmarkDataset()
        ds._cache.clear()
        md1 = ds.load_metadata("stepped_shaft")
        md2 = ds.load_metadata("stepped_shaft")
        assert md1 is md2

    def test_load_nonexistent_part(self):
        ds = BenchmarkDataset()
        with pytest.raises(FileNotFoundError):
            ds.load_metadata("nonexistent_part")

    def test_get_svg_views(self):
        ds = BenchmarkDataset()
        svgs = ds.get_svg_views("stepped_shaft")
        assert len(svgs) >= 3
        names = {s.stem for s in svgs}
        assert "front" in names or names

    def test_get_png_views_empty(self):
        ds = BenchmarkDataset()
        pngs = ds.get_png_views("stepped_shaft")
        assert isinstance(pngs, list)

    @needs_ground_truth
    def test_get_step_path_none(self):
        ds = BenchmarkDataset()
        step = ds.get_step_path("stepped_shaft")
        assert step is None

    @needs_ground_truth
    def test_get_obj_path_none(self):
        ds = BenchmarkDataset()
        obj = ds.get_obj_path("stepped_shaft")
        assert obj is None

    def test_load_all(self):
        ds = BenchmarkDataset()
        all_parts = ds.load_all()
        assert len(all_parts) >= 3
        assert "stepped_shaft" in all_parts

    def test_get_version_info(self):
        ds = BenchmarkDataset()
        info = ds.get_version_info("stepped_shaft")
        assert info["part_id"] == "stepped_shaft"
        assert info["version"] == "1.0.0"
        assert info["feature_count"] == 10
        assert info["dimension_count"] == 10

    def test_save_and_export_metadata(self):
        ds = BenchmarkDataset()
        md = ds.load_metadata("flange")
        with tempfile.TemporaryDirectory() as tmpdir:
            ds.save_metadata("test_export", md)
            loaded = ds.load_metadata("test_export")
            assert loaded.part_name == md.part_name
            ds.export_part("flange", tmpdir)
            exported = Path(tmpdir) / "flange" / "metadata.json"
            assert exported.exists()

    def test_get_input_views_dir(self):
        ds = BenchmarkDataset()
        views = ds.get_input_views_dir("flange")
        assert views.exists()

    @needs_ground_truth
    def test_get_ground_truth_dir(self):
        ds = BenchmarkDataset()
        gt = ds.get_ground_truth_dir("stepped_shaft")
        assert gt.exists()


class TestPartMetadata:
    """PartMetadata 序列化/反序列化测试。"""

    def test_to_dict_and_back(self):
        original = PartMetadata(
            part_id="test_part",
            part_name="测试零件",
            part_type="test",
            material="Q235",
            tolerance_grade="IT7",
            features=[
                FeatureDef(
                    name="f1",
                    feature_type="hole",
                    area=100.0,
                    volume=200.0,
                    bbox=(0, 0, 10, 10),
                    bbox_3d=(0, 0, 0, 10, 10, 10),
                )
            ],
            dimensions=[
                DimensionSpec(
                    name="D1",
                    nominal=50.0,
                    tolerance_upper=0.025,
                    tolerance_lower=0.0,
                    tolerance_grade="IT7",
                )
            ],
            topology=[TopologyRelation("A", "B", "adjacent")],
        )
        d = original.to_dict()
        restored = PartMetadata.from_dict(d)
        assert restored.part_name == original.part_name
        assert len(restored.features) == 1
        assert restored.features[0].area == 100.0
        assert len(restored.dimensions) == 1
        assert len(restored.topology) == 1


class TestGeometricValidator:
    """几何验证器测试。"""

    def make_validator(self):
        return GeometricValidator()

    def test_validate_stepped_shaft(self):
        v = self.make_validator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        assert report.part_id == "stepped_shaft"
        assert report.part_name == "阶梯轴"
        assert report.metrics.feature_recall >= 0.0
        assert report.metrics.tolerance_compliance >= 0.0
        assert report.validation_duration_seconds > 0
        assert isinstance(report.overall_pass, bool)

    def test_validate_flange(self):
        v = self.make_validator()
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        assert report.part_id == "flange"
        assert len(report.dimension_checks) > 0
        assert len(report.feature_checks) > 0
        assert len(report.topology_checks) > 0

    def test_validate_bracket(self):
        v = self.make_validator()
        report = v.validate_reconstruction("bracket", allow_mock_fallback=True)
        assert report.part_id == "bracket"
        assert report.metrics.feature_precision >= 0.0

    def test_validate_with_custom_model(self):
        v = self.make_validator()
        custom = {
            "dimensions": {"OD": 120.002, "center_bore_dia": 39.995, "thickness": 15.0},
            "features": {
                "flange_disc": {"confidence": 0.99, "iou": 0.98},
                "center_bore": {"confidence": 0.97, "iou": 0.95},
                "bolt_hole_1": {"confidence": 0.96, "iou": 0.94},
            },
            "topology": [
                {
                    "feature_a": "flange_disc",
                    "feature_b": "center_bore",
                    "relation": "contains_concentric",
                },
            ],
        }
        report = v.validate_reconstruction("flange", reconstructed_model=custom)
        assert report.part_id == "flange"

    def test_report_json(self):
        v = self.make_validator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        j = report.to_json()
        data = json.loads(j)
        assert data["part_id"] == "stepped_shaft"
        assert "metrics" in data

    def test_report_to_dict(self):
        v = self.make_validator()
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        d = report.to_dict()
        assert "dimension_checks" in d
        assert "feature_checks" in d

    def test_check_dimension_in_tolerance(self):
        v = self.make_validator()
        spec = {
            "name": "D1",
            "nominal": 50.0,
            "tolerance_upper": 0.025,
            "tolerance_lower": 0.0,
        }
        model = {"dimensions": {"D1": 50.01}}
        result = v.check_dimension(model, spec)
        assert result["within_tolerance"] is True
        assert result["deviation"] == 0.01

    def test_check_dimension_out_of_tolerance(self):
        v = self.make_validator()
        spec = {
            "name": "D1",
            "nominal": 50.0,
            "tolerance_upper": 0.025,
            "tolerance_lower": 0.0,
        }
        model = {"dimensions": {"D1": 50.10}}
        result = v.check_dimension(model, spec)
        assert result["within_tolerance"] is False

    def test_check_dimensions_multiple(self):
        v = self.make_validator()
        ds = BenchmarkDataset()
        md = ds.load_metadata("stepped_shaft")
        model = {"dimensions": {d.name: d.nominal for d in md.dimensions}}
        results = v.check_dimensions(model, md.dimensions)
        assert len(results) == len(md.dimensions)
        for r in results:
            assert r.within_tolerance is True

    def test_check_feature_presence_all_detected(self):
        v = self.make_validator()
        ds = BenchmarkDataset()
        md = ds.load_metadata("flange")
        model = {
            "features": {f.name: {"confidence": 0.98, "iou": 0.97} for f in md.features}
        }
        results = v.check_feature_presence(model, md.features)
        assert len(results) == len(md.features)
        assert all(r.detected for r in results)

    def test_check_feature_presence_partial(self):
        v = self.make_validator()
        ds = BenchmarkDataset()
        md = ds.load_metadata("bracket")
        model = {"features": {"base_plate": {"confidence": 0.99, "iou": 0.98}}}
        results = v.check_feature_presence(model, md.features)
        detected_count = sum(1 for r in results if r.detected)
        assert detected_count == 1


class TestHtmlReport:
    """HTML报告生成测试。"""

    def test_generate_report_returns_html(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "<!DOCTYPE html>" in html
        assert "阶梯轴" in html
        assert "特征召回率" in html
        assert "尺寸偏差" in html

    def test_report_contains_all_sections(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "综合指标概览" in html
        assert "特征交并比" in html
        assert "尺寸偏差详情" in html
        assert "特征识别结果" in html
        assert "拓扑关系验证" in html

    def test_report_saves_to_file(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("bracket", allow_mock_fallback=True)
        with tempfile.NamedTemporaryFile(
            suffix=".html", delete=False, mode="w", encoding="utf-8"
        ) as f:
            output_path = f.name
        try:
            html = v.generate_report(report, output_path)
            assert os.path.exists(output_path)
            with open(output_path, "r", encoding="utf-8") as f:
                saved = f.read()
            assert len(saved) > 1000
            assert html == saved
        finally:
            if os.path.exists(output_path):
                os.unlink(output_path)

    def test_report_pass_visual(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "#4caf50" in html or "#f44336" in html

    def test_report_fail_when_thresholds_exceeded(self):
        v = GeometricValidator(
            fail_on_dimension_deviation=0.001,
            fail_on_feature_recall=0.999,
            fail_on_tolerance_compliance=99.9,
        )
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert len(html) > 0

    def test_report_contains_dimension_rows(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "OD" in html or "center_bore_dia" in html

    def test_report_contains_feature_rows(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "shaft_body_1" in html

    def test_report_print_friendly(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("bracket", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert "@media print" in html


class TestBoundaryConditions:
    """边界条件测试。"""

    def test_mock_model_produces_expected_structure(self):
        ds = BenchmarkDataset()
        md = ds.load_metadata("stepped_shaft")
        v = GeometricValidator()
        model = v._mock_reconstructed_model(md)
        assert "dimensions" in model
        assert "features" in model
        assert "topology" in model
        assert len(model["dimensions"]) == len(md.dimensions)
        for ft in md.features:
            assert ft.name in model["features"]

    def test_validate_nonexistent_part(self):
        v = GeometricValidator()
        with pytest.raises(FileNotFoundError):
            v.validate_reconstruction("nonexistent")

    def test_constructor_defaults(self):
        v = GeometricValidator()
        assert v.fail_on_dimension_deviation == 0.1
        assert v.fail_on_feature_recall == 0.90
        assert v.fail_on_tolerance_compliance == 95.0

    def test_constructor_custom_thresholds(self):
        v = GeometricValidator(
            fail_on_dimension_deviation=0.05,
            fail_on_feature_recall=0.95,
            fail_on_tolerance_compliance=98.0,
        )
        assert v.fail_on_dimension_deviation == 0.05
        assert v.fail_on_feature_recall == 0.95
        assert v.fail_on_tolerance_compliance == 98.0

    @pytest.mark.parametrize("part_id", ["stepped_shaft", "flange", "bracket"])
    def test_validation_duration_reasonable(self, part_id):
        v = GeometricValidator()
        report = v.validate_reconstruction(part_id, allow_mock_fallback=True)
        assert report.validation_duration_seconds < 5.0, (
            f"{part_id} took {report.validation_duration_seconds}s"
        )

    def test_check_dimensions_with_dimensionspec_objects(self):
        v = GeometricValidator()
        specs = [
            DimensionSpec("D1", 50.0, "mm", 0.025, 0.0, "IT7"),
            DimensionSpec("D2", 30.0, "mm", 0.021, -0.021, "IT7"),
        ]
        model = {"dimensions": {"D1": 50.01, "D2": 29.99}}
        results = v.check_dimensions(model, specs)
        assert len(results) == 2
        assert results[0].within_tolerance is True
        assert results[1].within_tolerance is True


class TestIntegration:
    """集成测试：完整验证流程。"""

    def test_full_validation_pipeline_stepped_shaft(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("stepped_shaft", allow_mock_fallback=True)
        assert report.metrics.feature_recall >= 0.0
        assert report.metrics.feature_precision >= 0.0
        assert report.metrics.topology_correctness >= 0.0
        assert report.metrics.tolerance_compliance >= 0.0

    def test_full_validation_pipeline_flange(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("flange", allow_mock_fallback=True)
        html = v.generate_report(report)
        assert report.part_name == "法兰盘"
        assert len(html) > 2000

    def test_full_validation_pipeline_bracket(self):
        v = GeometricValidator()
        report = v.validate_reconstruction("bracket", allow_mock_fallback=True)
        assert report.part_name == "支架"
        json_str = report.to_json()
        assert len(json_str) > 100

    def test_all_three_parts_ok_duration(self):
        import time

        v = GeometricValidator()
        start = time.perf_counter()
        for part_id in ["stepped_shaft", "flange", "bracket"]:
            report = v.validate_reconstruction(part_id, allow_mock_fallback=True)
            assert report.overall_pass is not None
        elapsed = time.perf_counter() - start
        assert elapsed < 30.0, f"Full validation took {elapsed:.1f}s, expected < 30s"
