"""dxf/feature_extractor 覆盖率补强测试。

覆盖：extract 入口（None/空图/正常/防御兜底）、孔特征识别全分支
（直径/深度推断/标注覆盖/通盲沉头/公差/邻近映射）、
平面矩形识别、整体尺寸推断与异常告警、辅助函数。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit

from app.dxf._entities import DxfCircle, DxfDimension, DxfLine, DxfParseResult
from app.dxf.exceptions import DxfFeatureError
from app.dxf.feature_extractor import (
    FeatureExtractor,
    extract_tolerance_from_text,
    is_counterbore_text,
)


def _parse_result(**overrides) -> DxfParseResult:
    base = DxfParseResult(
        file_name="t.dxf",
        extents={"width": 100.0, "height": 80.0},
        entity_counts={"LINE": 0, "CIRCLE": 0, "DIMENSION": 0},
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    base.entity_counts = {
        "LINE": len(base.lines),
        "CIRCLE": len(base.circles),
        "DIMENSION": len(base.dimensions),
    }
    return base


def _circle(cx: float, cy: float, r: float, handle: str = "C1", layer: str = "holes") -> DxfCircle:
    return DxfCircle(center=(cx, cy, 0.0), radius=r, layer=layer, handle=handle)


def _line(p1: tuple, p2: tuple, handle: str = "L1") -> DxfLine:
    return DxfLine(start=(p1[0], p1[1], 0.0), end=(p2[0], p2[1], 0.0), handle=handle)


def _rect(x0, y0, x1, y1):
    """顺时针矩形 4 边。"""
    return [
        _line((x0, y0), (x1, y0), "L1"),
        _line((x1, y0), (x1, y1), "L2"),
        _line((x1, y1), (x0, y1), "L3"),
        _line((x0, y1), (x0, y0), "L4"),
    ]


class TestExtractEntry:
    def test_none_raises(self):
        with pytest.raises(DxfFeatureError):
            FeatureExtractor().extract(None)

    def test_no_entities_adds_error(self):
        pr = _parse_result()
        result = FeatureExtractor().extract(pr)
        assert result.errors
        assert "无几何实体" in result.errors[0]

    def test_empty_result_warns(self):
        # 有实体（1条线）但无圆、无闭合矩形 特征为空 + 告警
        pr = _parse_result(lines=[_line((0, 0), (10, 0))])
        result = FeatureExtractor().extract(pr)
        assert result.warnings
        assert any("未识别到任何孔特征" in w for w in result.warnings)

    def test_internal_error_caught(self):
        class Bad:
            total_entities = 5

            @property
            def circles(self):
                raise AttributeError("boom")

        result = FeatureExtractor().extract(Bad())
        assert result.errors
        assert "特征提取过程中发生异常" in result.errors[0]

    def test_dimension_semantics(self):
        pr = _parse_result(extents={"width": 120.0, "height": 90.0}, circles=[_circle(0, 0, 1.0)])
        result = FeatureExtractor().extract(pr)
        # widthlength, heightwidth（DXF 约定）
        assert result.overall_length == 120.0
        assert result.overall_width == 90.0
        assert result.overall_height == FeatureExtractor.DEFAULT_PLATE_THICKNESS
        assert result.height_inferred is True


class TestHoleFeatures:
    def test_simple_hole_inferred_depth(self):
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)])
        result = FeatureExtractor().extract(pr)
        assert len(result.holes) == 1
        hole = result.holes[0]
        assert hole.hole_id == "HOLE_001"
        assert hole.diameter == 10.0
        assert hole.depth_inferred is True
        assert hole.hole_type == "through_hole"  # depth(30) >= 90*0.9
        assert hole.center_x == 10.0
        assert hole.center_y == 10.0
        assert hole.layer == "holes"

    def test_depth_clamped_to_min(self):
        pr = _parse_result(circles=[_circle(0, 0, 0.5)])  # 直径 1 → 深度 3 < MIN
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].depth == FeatureExtractor.MIN_DEPTH

    def test_depth_clamped_to_max(self):
        pr = _parse_result(circles=[_circle(0, 0, 100.0)])  # 直径 200 → 深度 600 > MAX
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].depth == FeatureExtractor.MAX_DEPTH

    def test_diameter_dimension_overrides(self):
        dim = DxfDimension(
            dim_type="DIAMETER",
            measurement=12.5,
            text="Φ12.5",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        hole = result.holes[0]
        assert hole.diameter == 12.5
        assert hole.associated_dim_handle == "D1"

    def test_near_measurement_overrides_diameter(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=11.0,
            text="Φ11 深20",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].diameter == 11.0

    def test_far_measurement_not_applied(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=50.0,
            text="50",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].diameter == 10.0

    def test_depth_text_parsed(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=10.0,
            text="Φ10 深25",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        hole = result.holes[0]
        assert hole.depth == 25.0
        assert hole.depth_inferred is False

    def test_through_hole_keyword(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=10.0,
            text="Φ10 通孔",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].hole_type == "through_hole"
        assert result.holes[0].depth == result.overall_height

    def test_blind_hole_keyword(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=10.0,
            text="Φ10 盲孔",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].hole_type == "blind_hole"

    def test_counterbore_keyword(self):
        dim = DxfDimension(
            dim_type="LINEAR",
            measurement=10.0,
            text="Φ10 沉头",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].hole_type == "counterbore"

    def test_tolerance_extracted(self):
        dim = DxfDimension(
            dim_type="DIAMETER",
            measurement=10.0,
            text="Φ10 H7 通孔",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].tolerance_grade == "IT7"

    def test_second_hole_without_dim_warns(self):
        dim = DxfDimension(
            dim_type="DIAMETER",
            measurement=10.0,
            text="Φ10",
            position=(10.0, 10.0, 0.0),
            handle="D1",
        )
        pr = _parse_result(
            circles=[_circle(10.0, 10.0, 5.0, "C1"), _circle(50.0, 50.0, 3.0, "C2")],
            dimensions=[dim],
        )
        result = FeatureExtractor().extract(pr)
        assert any("缺少尺寸标注" in w for w in result.warnings)

    def test_inferred_depth_warns(self):
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)])
        result = FeatureExtractor().extract(pr)
        assert any("深度为推断值" in w for w in result.warnings)

    def test_no_dimension_assignment_for_far_dim(self):
        dim = DxfDimension(
            dim_type="DIAMETER",
            measurement=20.0,
            text="Φ20",
            position=(500.0, 500.0, 0.0),  # 距离远，超出阈值
            handle="D1",
        )
        pr = _parse_result(circles=[_circle(10.0, 10.0, 5.0)], dimensions=[dim])
        result = FeatureExtractor().extract(pr)
        assert result.holes[0].associated_dim_handle == ""


class TestPlaneFeatures:
    def test_rectangle_detected(self):
        pr = _parse_result(lines=_rect(0, 0, 100, 80))
        result = FeatureExtractor().extract(pr)
        assert len(result.planes) == 1
        plane = result.planes[0]
        assert plane.plane_id == "PLANE_001"
        assert plane.length == 100.0
        assert plane.width == 80.0
        assert plane.center_x == 50.0
        assert plane.center_y == 40.0

    def test_rectangle_updates_overall(self):
        pr = _parse_result(lines=_rect(0, 0, 100, 80))
        result = FeatureExtractor().extract(pr)
        assert result.overall_length >= 100.0
        assert result.overall_width >= 80.0

    def test_less_than_four_lines_skipped(self):
        pr = _parse_result(lines=[_line((0, 0), (10, 0))])
        result = FeatureExtractor().extract(pr)
        assert result.planes == []

    def test_open_polyline_not_rectangle(self):
        lines = [
            _line((0, 0), (10, 0)),
            _line((10, 0), (10, 10)),
            _line((10, 10), (0, 10)),
            _line((0, 10), (0, -10)),  # 明显不闭合（开口 10mm > 容差 5mm）
        ]
        pr = _parse_result(lines=lines)
        result = FeatureExtractor().extract(pr)
        assert result.planes == []

    def test_duplicate_rectangle_lines_not_double_counted(self):
        pr = _parse_result(lines=_rect(0, 0, 10, 10))
        result = FeatureExtractor().extract(pr)
        assert len(result.planes) == 1


class TestOverallDimensions:
    def test_dimension_numbers_update_extents(self):
        dims = [
            DxfDimension(dim_type="LINEAR", measurement=150.0, text="150", handle="D1"),
            DxfDimension(dim_type="LINEAR", measurement=120.0, text="120", handle="D2"),
        ]
        pr = _parse_result(dimensions=dims)
        result = FeatureExtractor().extract(pr)
        assert result.overall_length == 150.0
        assert result.overall_width == 120.0

    def test_small_dimension_not_applied(self):
        dims = [
            DxfDimension(dim_type="LINEAR", measurement=30.0, text="30", handle="D1"),
        ]
        pr = _parse_result(dimensions=dims, extents={"width": 100.0, "height": 80.0})
        result = FeatureExtractor().extract(pr)
        assert result.overall_length == 100.0  # 30 < 100*0.8 不覆盖

    def test_height_from_thickness_dim(self):
        dims = [
            DxfDimension(dim_type="LINEAR_ROTATED", measurement=15.0, text="板厚15", handle="D1"),
        ]
        pr = _parse_result(dimensions=dims)
        result = FeatureExtractor().extract(pr)
        assert result.overall_height == 15.0
        assert result.height_inferred is False

    def test_warns_on_abnormal_size(self):
        pr = _parse_result(extents={"width": 0.5, "height": 0.3}, circles=[_circle(0, 0, 1.0)])
        result = FeatureExtractor().extract(pr)
        assert any("零件尺寸异常" in w for w in result.warnings)


class TestHelpers:
    def test_is_counterbore_text(self):
        assert is_counterbore_text("沉头 Φ10")
        assert is_counterbore_text("CBORE 10")
        assert is_counterbore_text("COUNTERBORE")
        assert not is_counterbore_text("通孔 Φ10")
        assert not is_counterbore_text("普通孔")

    def test_tolerance_it_pattern(self):
        assert extract_tolerance_from_text("Φ10 IT8") == "IT8"
        assert not extract_tolerance_from_text("IT20")  # 超范围 → 空串

    def test_tolerance_h_pattern(self):
        assert extract_tolerance_from_text("Φ10 H6") == "IT6"
        assert not extract_tolerance_from_text("H3")

    def test_tolerance_plusminus(self):
        assert extract_tolerance_from_text("Φ10 ±0.01") == "IT5"
        assert extract_tolerance_from_text("Φ10 ±0.02") == "IT6"
        assert extract_tolerance_from_text("Φ10 ±0.04") == "IT7"

    def test_tolerance_none(self):
        assert not extract_tolerance_from_text("Φ10 深20")
        assert not extract_tolerance_from_text("")
