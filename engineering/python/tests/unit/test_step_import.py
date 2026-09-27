"""step_import parser/converter 单元测试（dataclass + property + 纯逻辑）。"""

from __future__ import annotations

import math

import pytest

from app.step_import.step_converter import (
    BatchConvertResult,
    ConvertResult,
    StlExportOptions,
)
from app.step_import.step_parser import (
    BoundingBox,
    EntityInfo,
    ModelInfo,
    StepParseResult,
)

pytestmark = pytest.mark.unit


def _convert_result(name='a', **kw) -> ConvertResult:
    return ConvertResult(
        file_name=name,
        stl_path=kw.get('stl_path', '/tmp/a.stl'),
        stl_url=kw.get('stl_url', '/api/a.stl'),
        format=kw.get('format', 'stl'),
        face_count=kw.get('face_count', 100),
        vertex_count=kw.get('vertex_count', 200),
        file_size=kw.get('file_size', 1024),
        entity_index=kw.get('entity_index', 0),
        entity_name=kw.get('entity_name', 'part'),
        precision_used=kw.get('precision_used', 'medium'),
        conversion_time_ms=kw.get('conversion_time_ms', 1.5),
    )


def _bbox(**kw) -> BoundingBox:
    return BoundingBox(
        length=kw.get('length', 10.0),
        width=kw.get('width', 20.0),
        height=kw.get('height', 30.0),
        min_point=kw.get('min_point', (0.0, 0.0, 0.0)),
        max_point=kw.get('max_point', (10.0, 20.0, 30.0)),
    )


def _model_info(**kw) -> ModelInfo:
    return ModelInfo(
        volume=kw.get('volume', 1000.0),
        surface_area=kw.get('surface_area', 800.0),
        bounding_box=kw.get('bounding_box', _bbox()),
        center_of_mass=kw.get('center_of_mass', (5.0, 10.0, 15.0)),
        entity_count=kw.get('entity_count', 1),
        face_count=kw.get('face_count', 12),
        vertex_count=kw.get('vertex_count', 8),
    )


class TestStlExportOptions:
    def test_defaults(self):
        o = StlExportOptions()
        assert o.linear_deflection == 0.01
        assert o.angular_deflection == 0.5
        assert o.binary is True
        assert o.precision_level == 'medium'

    def test_linear_tolerance_conversion(self):
        o = StlExportOptions(linear_deflection=10.0)
        assert o.linear_tolerance == pytest.approx(0.01)

    def test_linear_tolerance_default(self):
        o = StlExportOptions()
        assert o.linear_tolerance == pytest.approx(1e-5)

    def test_angular_tolerance_conversion(self):
        o = StlExportOptions(angular_deflection=180.0)
        assert o.angular_tolerance == pytest.approx(math.pi)

    def test_angular_tolerance_default(self):
        o = StlExportOptions()
        assert o.angular_tolerance == pytest.approx(math.radians(0.5))


class TestConvertResult:
    def test_fields(self):
        r = _convert_result()
        assert r.file_name == 'a'
        assert r.face_count == 100
        assert r.vertex_count == 200
        assert r.precision_used == 'medium'


class TestBatchConvertResult:
    def test_success_when_no_errors(self):
        b = BatchConvertResult(files=[_convert_result()])
        assert b.success is True

    def test_failure_when_errors(self):
        b = BatchConvertResult(files=[], errors=['boom'])
        assert b.success is False

    def test_totals(self):
        b = BatchConvertResult(
            files=[_convert_result(face_count=10, vertex_count=20), _convert_result('b', face_count=30, vertex_count=40)],
            total_face_count=40,
            total_vertex_count=60,
        )
        assert len(b.files) == 2
        assert b.total_face_count == 40
        assert b.total_vertex_count == 60


class TestBoundingBox:
    def test_fields(self):
        b = _bbox()
        assert b.length == 10.0
        assert b.width == 20.0
        assert b.height == 30.0
        assert b.min_point == (0.0, 0.0, 0.0)
        assert b.max_point == (10.0, 20.0, 30.0)


class TestModelInfo:
    def test_fields(self):
        m = _model_info()
        assert m.volume == 1000.0
        assert m.entity_count == 1
        assert m.bounding_box.length == 10.0
        assert m.center_of_mass == (5.0, 10.0, 15.0)


class TestEntityInfo:
    def test_fields(self):
        e = EntityInfo(
            name='part1', entity_index=0, volume=100.0, surface_area=80.0,
            bounding_box=_bbox(), center_of_mass=(0.0, 0.0, 0.0),
            face_count=6, vertex_count=8,
        )
        assert e.name == 'part1'
        assert e.is_solid is True
        assert e.entity_index == 0


class TestStepParseResult:
    def test_success_when_no_errors(self):
        r = StepParseResult(
            file_name='a.step', file_size=100, parse_time_ms=5.0,
            model_info=_model_info(),
        )
        assert r.success is True

    def test_failure_when_errors(self):
        r = StepParseResult(
            file_name='a.step', file_size=100, parse_time_ms=5.0,
            model_info=_model_info(), errors=['parse failed'],
        )
        assert r.success is False

    def test_is_assembly(self):
        r = StepParseResult(
            file_name='a.step', file_size=100, parse_time_ms=5.0,
            model_info=_model_info(), is_assembly=True,
        )
        assert r.is_assembly is True


class TestStepConverterPaths:
    def test_get_stl_path(self, tmp_path):
        from app.step_import.step_converter import StepConverter
        conv = StepConverter(output_dir=str(tmp_path))
        assert conv.get_stl_path('a.stl') == tmp_path / 'a.stl'

    def test_stl_exists(self, tmp_path):
        from app.step_import.step_converter import StepConverter
        conv = StepConverter(output_dir=str(tmp_path))
        assert conv.stl_exists('a.stl') is False
        (tmp_path / 'a.stl').write_text('x', encoding='utf-8')
        assert conv.stl_exists('a.stl') is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
