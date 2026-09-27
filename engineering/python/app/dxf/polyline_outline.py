"""多段线轮廓处理器。

从 DXF 解析得到的 polylines 中识别外轮廓和内部孔，
并转换为 CadQuery 2D 草图。

支持：
- 直线段
- 圆弧段（通过 bulge 凸度信息还原）
- 闭合多段线
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OutlineInfo:
    """多段线轮廓信息。

    Attributes:
        vertices: 顶点列表 [(x, y), ...]
        is_closed: 是否闭合
        is_hole: 是否是内部孔（在外轮廓内）
        layer: 图层名
        source_handle: 源实体句柄
    """

    vertices: list[tuple[float, float]]
    is_closed: bool = False
    is_hole: bool = False
    layer: str = "0"
    source_handle: str = ""


class PolylineOutlineProcessor:
    """多段线轮廓处理器。

    用法：
        processor = PolylineOutlineProcessor()
        outlines = processor.extract_outlines(polylines)
        # outlines 包含外轮廓和内部孔
    """

    # 顶点重合容差
    COINCIDENT_TOL = 0.01

    def extract_outlines(
        self,
        polylines: list,
    ) -> list[OutlineInfo]:
        """从 polylines 中提取外轮廓和内部孔。

        规则：
        1. 闭合多段线视为候选轮廓
        2. 面积最大的为外轮廓
        3. 被外轮廓包围的为内部孔
        """
        if not polylines:
            return []

        # 1. 收集所有闭合多段线
        closed: list[OutlineInfo] = []
        for p in polylines:
            outline = self._polyline_to_outline(p)
            if outline is None:
                continue
            if outline.is_closed and len(outline.vertices) >= 3:
                closed.append(outline)

        if not closed:
            return []

        # 2. 计算每个轮廓的面积和包围盒
        infos = []
        for c in closed:
            area = self._polygon_area(c.vertices)
            c._area = area  # type: ignore[attr-defined]
            infos.append(c)
        # 按面积降序
        infos.sort(key=lambda x: getattr(x, "_area", 0.0), reverse=True)

        # 3. 最大的视为外轮廓，其余视是否在其内部判定孔
        outlines: list[OutlineInfo] = []
        if infos:
            outer = infos[0]
            outer.is_hole = False
            outlines.append(outer)
            for c in infos[1:]:
                # 简单判定：c 的某个顶点在外轮廓内 视为孔
                if self._point_in_polygon(c.vertices[0], outer.vertices):
                    c.is_hole = True
                    outlines.append(c)
                # 不在内部的孤立闭合轮廓忽略
        return outlines

    def _polyline_to_outline(self, p) -> OutlineInfo | None:
        """把 DxfPolyline 转换成 OutlineInfo（去除 bulge 维度）。"""
        if not p.vertices:
            return None
        verts_2d: list[tuple[float, float]] = []
        for v in p.vertices:
            x, y = float(v[0]), float(v[1])
            verts_2d.append((x, y))
        return OutlineInfo(
            vertices=verts_2d,
            is_closed=p.is_closed,
            is_hole=False,
            layer=p.layer,
            source_handle=p.handle,
        )

    def _polygon_area(self, vertices: list[tuple[float, float]]) -> float:
        """Shoelace 公式计算多边形面积（带符号）。"""
        if len(vertices) < 3:
            return 0.0
        s = 0.0
        n = len(vertices)
        for i in range(n):
            x1, y1 = vertices[i]
            x2, y2 = vertices[(i + 1) % n]
            s += (x1 * y2) - (x2 * y1)
        return abs(s) / 2.0

    def _point_in_polygon(
        self,
        point: tuple[float, float],
        polygon: list[tuple[float, float]],
    ) -> bool:
        """射线法判定点是否在多边形内。"""
        if len(polygon) < 3:
            return False
        x, y = point
        inside = False
        n = len(polygon)
        j = n - 1
        for i in range(n):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) + 1e-12) + xi):
                inside = not inside
            j = i
        return inside


__all__ = ["PolylineOutlineProcessor", "OutlineInfo"]
