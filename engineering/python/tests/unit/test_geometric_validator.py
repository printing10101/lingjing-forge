"""validation.geometric_validator 单元测试（dataclass + 纯静态方法）。"""

from __future__ import annotations

import json

import pytest

from app.validation.geometric_validator import (
    DimensionCheckResult,
    FeatureCheckResult,
    GeometricValidator,
    ValidationReport,
)
from app.validation.metrics import MetricsResult

pytestmark = pytest.mark.unit


def _metrics() -> MetricsResult:
    return MetricsResult(
        dimension_accuracy={}, feature_iou={}, feature_recall=1.0,
        feature_precision=1.0, topology_correctness=1.0, tolerance_compliance=100.0,
    )


def _report(**kw) -> ValidationReport:
    return ValidationReport(
        part_id=kw.get('part_id', 'p1'),
        part_name=kw.get('part_name', 'part'),
        timestamp=kw.get('timestamp', '2026-01-01T00:00:00Z'),
        metrics=kw.get('metrics', _metrics()),
        dimension_checks=kw.get('dimension_checks', []),
        feature_checks=kw.get('feature_checks', []),
        topology_checks=kw.get('topology_checks', []),
        overall_pass=kw.get('overall_pass', True),
        validation_duration_seconds=kw.get('validation_duration_seconds', 1.0),
        warnings=kw.get('warnings', []),
        errors=kw.get('errors', []),
    )


class TestResultDataclasses:
    def test_dimension_check_result(self):
        d = DimensionCheckResult(
            dimension_name='d1', nominal=10.0, measured=10.1, deviation=0.1,
            tolerance_upper=0.2, tolerance_lower=-0.2, within_tolerance=True, deviation_percent=1.0,
        )
        assert d.dimension_name == 'd1'
        assert d.within_tolerance is True

    def test_feature_check_result(self):
        f = FeatureCheckResult(
            feature_name='f1', detected=True, confidence=0.9, iou=0.8, feature_type='hole',
        )
        assert f.feature_name == 'f1'
        assert f.detected is True


class TestValidationReport:
    def test_to_dict(self):
        r = _report()
        d = r.to_dict()
        assert d['part_id'] == 'p1'
        assert d['overall_pass'] is True
        assert d['report_version'] == '1.0.0'
        assert 'metrics' in d

    def test_to_json(self):
        r = _report()
        j = r.to_json()
        data = json.loads(j)
        assert data['part_id'] == 'p1'
        assert data['report_version'] == '1.0.0'

    def test_to_dict_with_checks(self):
        r = _report(
            dimension_checks=[DimensionCheckResult('d1', 10.0, 10.0, 0.0, 0.1, -0.1, True, 0.0)],
            feature_checks=[FeatureCheckResult('f1', True, 0.9, 0.8, 'hole')],
        )
        d = r.to_dict()
        assert len(d['dimension_checks']) == 1
        assert len(d['feature_checks']) == 1


class TestConvertTopoChecksToEdges:
    def test_edge_tuple_format(self):
        topo = [{'edge': ('f1', 'f2'), 'relation': 'adjacent'}]
        edges = GeometricValidator._convert_topo_checks_to_edges(topo)
        assert len(edges) == 1
        assert edges[0].feature_a == 'f1'
        assert edges[0].feature_b == 'f2'
        assert edges[0].relation == 'adjacent'

    def test_feature_a_b_format(self):
        topo = [{'feature_a': 'f1', 'feature_b': 'f2', 'relation': 'contains'}]
        edges = GeometricValidator._convert_topo_checks_to_edges(topo)
        assert len(edges) == 1
        assert edges[0].feature_a == 'f1'
        assert edges[0].feature_b == 'f2'
        assert edges[0].relation == 'contains'

    def test_empty(self):
        assert GeometricValidator._convert_topo_checks_to_edges([]) == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
