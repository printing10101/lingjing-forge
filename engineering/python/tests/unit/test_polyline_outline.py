"""dxf/polyline_outline 覆盖率补强测试。

覆盖 PolylineOutlineProcessor：
- 外轮廓 + 内部孔识别
- 空输入 / 未闭合 / 顶点不足
- Shoelace 面积 / 射线法点判定
"""

from __future__ import annotations

import pytest

from app.dxf._entities import DxfPolyline
from app.dxf.polyline_outline import OutlineInfo, PolylineOutlineProcessor

pytestmark = pytest.mark.unit


def _poly(vertices, closed=True, layer="0", handle="h1") -> DxfPolyline:
    return DxfPolyline(
        handle=handle,
        layer=layer,
        is_closed=closed,
        vertices=[(x, y, 0.0) for x, y in vertices],
    )


class TestPolylineOutlineProcessor:
    def setup_method(self):
        self.p = PolylineOutlineProcessor()

    def test_empty_input(self):
        assert self.p.extract_outlines([]) == []

    def test_open_polyline_ignored(self):
        outlines = self.p.extract_outlines([_poly([(0, 0), (10, 0), (10, 10)], closed=False)])
        assert outlines == []

    def test_too_few_vertices_ignored(self):
        # 2 个顶点的闭合线不足 3 点
        outlines = self.p.extract_outlines([_poly([(0, 0), (10, 0)], closed=True)])
        assert outlines == []

    def test_empty_vertices_ignored(self):
        outlines = self.p.extract_outlines([_poly([], closed=True)])
        assert outlines == []

    def test_single_outer_outline(self):
        outlines = self.p.extract_outlines([_poly([(0, 0), (10, 0), (10, 10), (0, 10)])])
        assert len(outlines) == 1
        assert outlines[0].is_hole is False
        assert outlines[0].is_closed is True
        assert outlines[0].layer == "0"

    def test_outer_with_inner_hole(self):
        outer = _poly([(0, 0), (20, 0), (20, 20), (0, 20)], handle="outer")
        hole = _poly([(5, 5), (10, 5), (10, 10), (5, 10)], handle="hole")
        outlines = self.p.extract_outlines([outer, hole])
        assert len(outlines) == 2
        by_handle = {o.source_handle: o for o in outlines}
        assert by_handle["outer"].is_hole is False
        assert by_handle["hole"].is_hole is True

    def test_detached_polyline_ignored(self):
        # 面积更大的独立轮廓在外轮廓外 忽略
        outer = _poly([(0, 0), (10, 0), (10, 10), (0, 10)], handle="outer")
        detached = _poly([(50, 50), (60, 50), (60, 60), (50, 60)], handle="detached")
        outlines = self.p.extract_outlines([outer, detached])
        assert len(outlines) == 1
        assert outlines[0].source_handle == "outer"

    def test_largest_is_outer(self):
        small = _poly([(0, 0), (5, 0), (5, 5), (0, 5)], handle="small")
        big = _poly([(-10, -10), (10, -10), (10, 10), (-10, 10)], handle="big")
        outlines = self.p.extract_outlines([small, big])
        assert len(outlines) == 2
        assert outlines[0].source_handle == "big"  # 面积最大的排最前（外轮廓）
        assert outlines[1].source_handle == "small"
        assert outlines[1].is_hole is True

    def test_polygon_area(self):
        # 2x2 正方形 shoelace 面积 = 4
        area = self.p._polygon_area([(0, 0), (2, 0), (2, 2), (0, 2)])
        assert area == pytest.approx(4.0)

    def test_polygon_area_less_than_3(self):
        assert self.p._polygon_area([(0, 0), (1, 1)]) == 0.0

    def test_polygon_area_cw_vs_ccw(self):
        cw = self.p._polygon_area([(0, 0), (0, 2), (2, 2), (2, 0)])
        ccw = self.p._polygon_area([(0, 0), (2, 0), (2, 2), (0, 2)])
        assert cw == pytest.approx(ccw)  # 绝对值

    def test_point_in_polygon_inside(self):
        assert self.p._point_in_polygon((1, 1), [(0, 0), (2, 0), (2, 2), (0, 2)]) is True

    def test_point_in_polygon_outside(self):
        assert self.p._point_in_polygon((5, 5), [(0, 0), (2, 0), (2, 2), (0, 2)]) is False

    def test_point_in_polygon_insufficient(self):
        assert self.p._point_in_polygon((1, 1), [(0, 0), (2, 0)]) is False

    def test_outline_info_dataclass(self):
        o = OutlineInfo(
            vertices=[(0, 0), (1, 0), (1, 1)],
            is_closed=True,
            is_hole=True,
            layer="L1",
            source_handle="h",
        )
        assert o.vertices == [(0, 0), (1, 0), (1, 1)]
        assert o.is_hole is True
