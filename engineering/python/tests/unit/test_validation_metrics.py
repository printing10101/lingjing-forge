"""validation.metrics 单元测试（六项几何精度指标 + bbox 交并计算）。"""

from __future__ import annotations

import pytest

from app.validation.metrics import (
    DimensionResult,
    MetricsResult,
    TopologyEdge,
    _bbox3d_intersection_volume,
    _bbox_intersection_area,
    compute_dimension_accuracy,
    compute_feature_iou,
    compute_feature_precision,
    compute_feature_recall,
    compute_tolerance_compliance,
    compute_topology_correctness,
)

pytestmark = pytest.mark.unit


def _dim(name='d1', measured=10.0, nominal=10.0, within=True, **kw) -> DimensionResult:
    return DimensionResult(
        name=name,
        nominal=nominal,
        measured=measured,
        deviation_abs=abs(measured - nominal),
        deviation_rel=kw.get('deviation_rel', abs(measured - nominal) / nominal if nominal else 0.0),
        tolerance_upper=kw.get('tolerance_upper', 0.1),
        tolerance_lower=kw.get('tolerance_lower', -0.1),
        within_tolerance=within,
    )


def _edge(a='f1', b='f2', rel='adjacent') -> TopologyEdge:
    return TopologyEdge(feature_a=a, feature_b=b, relation=rel)


class TestMetricsResult:
    def test_to_dict_rounds(self):
        m = MetricsResult(
            dimension_accuracy={}, feature_iou={}, feature_recall=0.123456,
            feature_precision=0.98765, topology_correctness=0.5, tolerance_compliance=99.99,
        )
        d = m.to_dict()
        assert d['feature_recall'] == 0.1235
        assert d['feature_precision'] == 0.9877
        assert d['topology_correctness'] == 0.5
        assert d['tolerance_compliance'] == 100.0


class TestDimensionAccuracy:
    def test_empty(self):
        r = compute_dimension_accuracy([])
        assert r['mean_absolute_deviation'] == 0.0
        assert r['total_count'] == 0

    def test_normal(self):
        dims = [_dim('d1', measured=10.0, nominal=10.0, within=True), _dim('d2', measured=10.5, nominal=10.0, within=False)]
        r = compute_dimension_accuracy(dims)
        assert r['total_count'] == 2
        assert r['within_tolerance_count'] == 1
        assert r['max_absolute_deviation'] == 0.5
        assert len(r['dimensions']) == 2


class TestFeatureIoU:
    def test_pixel_mode_perfect_overlap(self):
        det = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_iou(det, gt, mode='pixel')['a'] == 1.0

    def test_volume_mode(self):
        det = [{'name': 'a', 'volume': 8.0, 'bbox_3d': (0, 0, 0, 2, 2, 2)}]
        gt = [{'name': 'a', 'volume': 8.0, 'bbox_3d': (0, 0, 0, 2, 2, 2)}]
        assert compute_feature_iou(det, gt, mode='volume')['a'] == 1.0

    def test_precomputed_iou(self):
        det = [{'name': 'a', 'iou': 0.75}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_iou(det, gt)['a'] == 0.75

    def test_missing_name_skipped(self):
        det = [{'name': ''}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_iou(det, gt) == {}

    def test_unknown_feature_zero(self):
        det = [{'name': 'x', 'area': 10.0, 'bbox': (0, 0, 1, 1)}]
        gt = [{'name': 'a', 'area': 10.0, 'bbox': (0, 0, 1, 1)}]
        assert compute_feature_iou(det, gt)['x'] == 0.0


class TestFeatureRecall:
    def test_empty_gt_returns_one(self):
        assert compute_feature_recall([], []) == 1.0

    def test_normal(self):
        det = [{'name': 'a', 'confidence': 0.9, 'iou': 0.8, 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_recall(det, gt) == 1.0

    def test_low_confidence_not_counted(self):
        det = [{'name': 'a', 'confidence': 0.3, 'iou': 0.8, 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_recall(det, gt) == 0.0


class TestFeaturePrecision:
    def test_empty_det_returns_one(self):
        assert compute_feature_precision([], [{'name': 'a'}]) == 1.0

    def test_normal(self):
        det = [{'name': 'a', 'confidence': 0.9, 'iou': 0.8, 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        gt = [{'name': 'a', 'area': 100.0, 'bbox': (0, 0, 10, 10)}]
        assert compute_feature_precision(det, gt) == 1.0

    def test_false_positive(self):
        det = [{'name': 'ghost', 'confidence': 0.9, 'iou': 0.8, 'area': 10.0, 'bbox': (0, 0, 1, 1)}]
        gt = [{'name': 'a', 'area': 10.0, 'bbox': (0, 0, 1, 1)}]
        assert compute_feature_precision(det, gt) == 0.0


class TestTopologyCorrectness:
    def test_empty_gt_returns_one(self):
        assert compute_topology_correctness([], []) == 1.0

    def test_perfect_match(self):
        det = [_edge('f1', 'f2', 'adjacent')]
        gt = [_edge('f1', 'f2', 'adjacent')]
        assert compute_topology_correctness(det, gt) == 1.0

    def test_mismatch(self):
        det = [_edge('f1', 'f3', 'adjacent')]
        gt = [_edge('f1', 'f2', 'adjacent')]
        assert compute_topology_correctness(det, gt) == 0.0


class TestToleranceCompliance:
    def test_empty_returns_100(self):
        assert compute_tolerance_compliance([]) == 100.0

    def test_half_compliant(self):
        dims = [_dim('d1', within=True), _dim('d2', measured=11.0, within=False)]
        assert compute_tolerance_compliance(dims) == 50.0


class TestBBoxIntersection:
    def test_area_intersection(self):
        a = (0, 0, 10, 10)
        b = (5, 5, 15, 15)
        assert _bbox_intersection_area(a, b) == 25.0

    def test_area_no_intersection(self):
        a = (0, 0, 1, 1)
        b = (2, 2, 3, 3)
        assert _bbox_intersection_area(a, b) == 0.0

    def test_volume_intersection(self):
        a = (0, 0, 0, 2, 2, 2)
        b = (1, 1, 1, 3, 3, 3)
        assert _bbox3d_intersection_volume(a, b) == 1.0

    def test_volume_no_intersection(self):
        a = (0, 0, 0, 1, 1, 1)
        b = (2, 2, 2, 3, 3, 3)
        assert _bbox3d_intersection_volume(a, b) == 0.0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
