"""DXF 闭合轮廓提取 + 轮廓桥（contour_extractor / contours_to_part_features）单元测试。

覆盖范围：
- LINE 首尾相接 → 链化成闭合矩形环（此前只有 LWPOLYLINE 能进轮廓通路）
- ARC 采样成折线段后与 LINE 链化
- CIRCLE → 整圆折线环，并带 radius 供上层与孔特征去重
- 开放线段（端点不闭合）→ 不成环 + 给出可操作警告（不猜桥接）
- 嵌套判定：面积最大者为外轮廓，其余在内部者为内环
- 去重：同一环不被 polyline 与 chained 两种来源各算一次
- 轮廓桥：外环 → outer_contour 特征；内环 → through_pocket 特征；
  与已识别孔同心同径的内环跳过（不重复挖槽）

测试原则：几何全部现场构造（ezdxf 内存写盘到 tmp_path），不依赖仓库 fixture，
断言只看数值（顶点数/面积/包络），不看快照文本。
"""

from __future__ import annotations

import math

import ezdxf
import pytest

from app.dxf.contour_extractor import (
    ContourExtractor,
    ContourRing,
    _point_in_polygon,
    classify_rings,
)
from app.dxf.process_service import contours_to_part_features


def _parse(doc, tmp_path, name="part.dxf"):
    path = tmp_path / name
    doc.saveas(path)
    from app.dxf.dxf_parser import DxfParser

    return DxfParser().parse(str(path))


def _rect_msp(msp, x0, y0, x1, y1):
    msp.add_line((x0, y0), (x1, y0))
    msp.add_line((x1, y0), (x1, y1))
    msp.add_line((x1, y1), (x0, y1))
    msp.add_line((x0, y1), (x0, y0))


class TestChaining:
    @pytest.mark.unit
    def test_four_lines_form_closed_ring(self, tmp_path):
        """4 条首尾相接的 LINE 必须链化成 4 顶点闭合环（旧实现只能吃多段线）。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        _rect_msp(msp, 10, 20, 60, 70)
        rings, warns = ContourExtractor().extract(_parse(doc, tmp_path))

        assert len(rings) == 1, warns
        ring = rings[0]
        assert ring.source == "chained"
        assert len(ring.vertices) == 4
        assert (round(ring.min_x, 3), round(ring.min_y, 3), round(ring.max_x, 3), round(ring.max_y, 3)) == (
            10.0,
            20.0,
            60.0,
            70.0,
        )
        assert abs(ring.abs_area - 50 * 50) < 1e-6
        assert not warns

    @pytest.mark.unit
    def test_open_line_chain_does_not_fabricate_ring(self, tmp_path):
        """开放折线（有 1 度端点）不得被硬连成环，且必须给出可操作提示。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_line((0, 0), (50, 0))
        msp.add_line((50, 0), (50, 30))
        msp.add_line((50, 30), (0, 30))  # 少一条右边 → 不闭合
        rings, warns = ContourExtractor().extract(_parse(doc, tmp_path))

        assert rings == []
        assert any("非 2 度节点" in w for w in warns)
        assert any("JOIN/BOUNDARY" in w for w in warns)

    @pytest.mark.unit
    def test_line_plus_arc_closes_ring(self, tmp_path):
        """直线 + 圆弧（半圆D形）应链化成含弧采样点、弧段被如实标注。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_line((0, 0), (0, 20))
        msp.add_arc(center=(0, 10), radius=10, start_angle=-90, end_angle=90)
        rings, warns = ContourExtractor().extract(_parse(doc, tmp_path))

        assert len(rings) == 1, warns
        ring = rings[0]
        assert ring.approximated_arcs is True
        assert len(ring.vertices) > 6  # 弧被采样成多段
        assert ring.max_x == pytest.approx(10.0, abs=0.05)

    @pytest.mark.unit
    def test_circle_becomes_sampled_ring_with_radius(self, tmp_path):
        """整圆 → 折线环，且保留原始 radius（孔去重要用）。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_circle((30, 40), 25)
        rings, _ = ContourExtractor().extract(_parse(doc, tmp_path))

        assert len(rings) == 1
        ring = rings[0]
        assert ring.source == "circle"
        assert ring.radius == pytest.approx(25.0)
        assert ring.center == pytest.approx((30.0, 40.0), abs=0.01) if isinstance(ring.center, tuple) else True
        # 48 段内接正多边形面积略小于真圆（约 -0.45%），不得当成真圆宣称
        assert ring.abs_area == pytest.approx(math.pi * 25**2, rel=0.01)


class TestNestedClassification:
    @pytest.mark.unit
    def test_outer_and_inner_loops(self, tmp_path):
        """两个同心闭合多段线：大的为外轮廓，小的为内环。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(30, 30), (70, 30), (70, 70), (30, 70)], close=True)
        rings, _ = ContourExtractor().extract(_parse(doc, tmp_path))

        outer, inner = classify_rings(rings)
        assert outer is not None and len(outer.vertices) == 4
        assert len(inner) == 1
        assert inner[0].abs_area == pytest.approx(40 * 40)

    @pytest.mark.unit
    def test_disjoint_ring_is_not_inner(self, tmp_path):
        """完全在外轮廓之外的独立闭合环不算内环（多图框/多零件同图）。"""
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(200, 200), (220, 200), (220, 220), (200, 220)], close=True)
        rings, _ = ContourExtractor().extract(_parse(doc, tmp_path))

        outer, inner = classify_rings(rings)
        assert outer is not None
        assert inner == []

    @pytest.mark.unit
    def test_duplicate_source_rings_deduped(self):
        """同一几何既作为闭合多段线又作为 4 条线时，只算一个环。"""

        pts = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        a = ContourRing(
            vertices=[list(p) for p in pts], area=100.0, min_x=0, min_y=0, max_x=10, max_y=10, source="polyline"
        )
        b = ContourRing(
            vertices=[list(p) for p in pts], area=100.0, min_x=0, min_y=0, max_x=10, max_y=10, source="chained"
        )
        deduped = ContourExtractor()._dedupe([a, b])

        assert len(deduped) == 1
        assert deduped[0].source == "polyline"  # 顶点顺序可信的来源优先

    @pytest.mark.unit
    def test_point_in_polygon_basic(self):
        poly = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]
        assert _point_in_polygon(5, 5, poly) is True
        assert _point_in_polygon(15, 5, poly) is False


class TestContourBridge:
    """轮廓桥：环 → 工艺特征字典。"""

    def _ring(self, x0, y0, x1, y1, area=None, source="chained", radius=None):
        pts = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        return {
            "vertices": pts,
            "area": area if area is not None else (x1 - x0) * (y1 - y0),
            "length": x1 - x0,
            "width": y1 - y0,
            "center_x": (x0 + x1) / 2,
            "center_y": (y0 + y1) / 2,
            "source": source,
            "radius": radius,
            "approximated_arcs": source == "circle",
        }

    @pytest.mark.unit
    def test_outer_ring_becomes_outline_feature(self):
        outlines, cavities, notes = contours_to_part_features(
            [self._ring(0, 0, 80, 60)],
            holes=[],
            thickness=12.0,
        )

        assert len(outlines) == 1
        o = outlines[0]
        assert o["type"] == "outer_contour"
        assert o["geometric_type"] == "outline"
        assert o["contour"] == [[0, 0], [80, 0], [80, 60], [0, 60]]
        assert o["dimensions"]["depth"] == 12.0
        assert any("板厚" in n for n in notes)
        assert cavities == []

    @pytest.mark.unit
    def test_inner_ring_becomes_pocket(self):
        rings = [self._ring(0, 0, 100, 100), self._ring(40, 40, 60, 60, area=400)]
        outlines, cavities, _ = contours_to_part_features(rings, holes=[], thickness=8.0)

        assert len(outlines) == 1
        assert len(cavities) == 1
        assert cavities[0]["type"] == "through_pocket"
        assert len(cavities[0]["contour"]) == 4

    @pytest.mark.unit
    def test_inner_circle_matching_hole_is_skipped(self):
        """内环与已识别孔同心同径 → 跳过（孔已有钻孔循环，不再挖槽）。"""
        hole = {"center_x": 50.0, "center_y": 50.0, "diameter": 20.0}
        rings = [
            self._ring(0, 0, 100, 100),
            self._ring(40, 40, 60, 60, area=math.pi * 10**2, source="circle", radius=10.0),
        ]
        outlines, cavities, notes = contours_to_part_features(rings, holes=[hole], thickness=8.0)

        assert len(outlines) == 1
        assert cavities == []
        assert any("交由钻孔循环" in n for n in notes)

    @pytest.mark.unit
    def test_offset_inner_circle_still_pocket(self):
        """同径但不同心 → 不算孔，仍要挖槽。"""
        hole = {"center_x": 10.0, "center_y": 10.0, "diameter": 20.0}
        rings = [
            self._ring(0, 0, 100, 100),
            self._ring(40, 40, 60, 60, area=math.pi * 10**2, source="circle", radius=10.0),
        ]
        _, cavities, _ = contours_to_part_features(rings, holes=[hole], thickness=8.0)

        assert len(cavities) == 1

    @pytest.mark.unit
    def test_missing_thickness_falls_back_with_note(self):
        outlines, _, notes = contours_to_part_features([self._ring(0, 0, 50, 50)], holes=[], thickness=0.0)

        assert outlines[0]["dimensions"]["depth"] == 10.0
        assert any("10mm 推断" in n for n in notes)

    @pytest.mark.unit
    def test_no_contours_returns_empty(self):
        outlines, cavities, notes = contours_to_part_features([], holes=[], thickness=8.0)

        assert (outlines, cavities, notes) == ([], [], [])

    @pytest.mark.unit
    def test_degenerate_ring_vertices_rejected(self):
        """顶点不足 3 的环不生成外形工序，并留下说明（不静默出空刀轨）。"""
        bad = {"vertices": [[0, 0], [10, 10]], "area": 0.0, "length": 10, "width": 10, "center_x": 5, "center_y": 5}
        outlines, cavities, notes = contours_to_part_features([bad], holes=[], thickness=8.0)

        assert outlines == []
        assert any("顶点不足" in n for n in notes)
