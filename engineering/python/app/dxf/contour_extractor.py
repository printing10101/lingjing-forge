"""DXF 闭合环（轮廓）提取器。

用途：把 DXF 里的 LINE / ARC / 多段线碎片**链化成闭合轮廓环**，供工艺规划层
生成真实的多边形刀轨（外轮廓铣 / 型腔挖槽），而不是退化成
`length×width` 矩形包络。

背景（2026-10-07 全库定级发现）：
- `PolylineOutlineProcessor` 只吃 LWPOLYLINE，所以用 LINE/ARC 画的轮廓
  （本仓 20 个 fixture 里的 14 个）根本进不了轮廓通路；
- `ProcessPlanningPipeline._build_features` 因此永远只拿到「基准面 + 孔」，
  `cavities` / `bosses` 恒空 → `刀轨策略` 20/20 全是 `face_raster`，
  零件外形从来没被切过。

算法（确定性、无猜测）：
1. 把每个实体拆成「线段序列」：LINE→1 段；ARC→按角度采样成折线段；
   闭合多段线→直接成环（顶点顺序已知）；开放多段线→逐段入链。
2. 端点按 `ENDPOINT_TOL_MM` 量化成节点，构成无向图。
3. **全为 2 度节点**的连通分量（且边数=节点数≥3）判定为一个闭合环，按图遍历
   还原顶点顺序；含非 2 度节点的分量跳过并给警告（不猜桥接关系，避免造出
   错误几何）。
4. 用 shoelace 公式算带符号面积做去重与嵌套判定：面积最大者为外轮廓，
   被外轮廓包含者为内环（型腔/内孔）。

保守口径：
- bulge（多段线弧段凸度）在此不参与采样，弧段被当作弦处理，`warnings` 会
  如实标注「含 bulge 弧段的轮廓为弦近似」。这与 `PolylineOutlineProcessor`
  现状一致，不偷偷假装精度更高。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 端点重合容差（mm）。DXF 常见建模误差在 1e-6~1e-3 量级，0.05mm 足以
# 把"看起来首尾相接"的线段并到一个节点，又不至于把 0.1mm 的真实短边吃掉。
ENDPOINT_TOL_MM = 0.05

# 圆弧采样段数：单段最小步长角度对应的段数上限，兼顾精度与顶点膨胀
ARC_MAX_SEGMENTS = 24
ARC_MIN_SEGMENTS = 2

# 整圆（CIRCLE）折线近似段数。48 段对内切误差 ~0.5%·R 已够 2.5D 轮廓铣用；
# 误差会在环的 to_dict 里以 approximated_arcs=True 如实标注。
CIRCLE_SEGMENTS = 48


@dataclass
class ContourRing:
    """一个闭合轮廓环。

    Attributes:
        vertices: 按走刀顺序排列的顶点 [[x, y], ...]（不重复首顶点）
        area: 带符号面积（mm²，逆时针为正）
        min_x / min_y / max_x / max_y: 包络
        source: 来源标记——"polyline"（闭合多段线直接成环）或 "chained"（碎片链化）
        layer: 图层名（多源合并时取首个）
        handles: 参与成环的实体句柄列表（可追溯到 DXF 实体）
        approximated_arcs: 该环是否包含被弦近似处理的弧段
    """

    vertices: list[list[float]]
    area: float
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    source: str = "chained"
    layer: str = "0"
    handles: list[str] = field(default_factory=list)
    approximated_arcs: bool = False
    # 整圆来源才有值：原始半径（mm），供上层与孔特征按 (圆心, 半径) 去重
    radius: float | None = None

    @property
    def abs_area(self) -> float:
        return abs(self.area)

    @property
    def length(self) -> float:
        """包络 X 向尺寸（mm），仅作切深/刀具选择参考。"""
        return self.max_x - self.min_x

    @property
    def width(self) -> float:
        """包络 Y 向尺寸（mm）。"""
        return self.max_y - self.min_y

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)

    def bbox_contains(self, x: float, y: float) -> bool:
        return self.min_x - ENDPOINT_TOL_MM <= x <= self.max_x + ENDPOINT_TOL_MM and (
            self.min_y - ENDPOINT_TOL_MM <= y <= self.max_y + ENDPOINT_TOL_MM
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "vertices": self.vertices,
            "area": round(self.area, 3),
            "length": round(self.length, 3),
            "width": round(self.width, 3),
            "center_x": round(self.center[0], 3),
            "center_y": round(self.center[1], 3),
            "min_x": round(self.min_x, 3),
            "min_y": round(self.min_y, 3),
            "max_x": round(self.max_x, 3),
            "max_y": round(self.max_y, 3),
            "source": self.source,
            "layer": self.layer,
            "handles": list(self.handles),
            "approximated_arcs": self.approximated_arcs,
            "radius": round(self.radius, 3) if self.radius is not None else None,
        }


def _signed_area(pts: list[list[float]]) -> float:
    """shoelace 带符号面积。"""
    n = len(pts)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _point_in_polygon(x: float, y: float, poly: list[list[float]]) -> bool:
    """射线法判点在多边形内（闭合环，poly 不含重复首顶点）。"""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        if (yi > y) != (yj > y):
            x_int = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_int:
                inside = not inside
        j = i
    return inside


def _key(x: float, y: float) -> tuple[int, int]:
    """端点量化键（容差网格）。"""
    return (round(x / ENDPOINT_TOL_MM), round(y / ENDPOINT_TOL_MM))


class ContourExtractor:
    """从 DXF 解析结果中提取闭合轮廓环。

    用法::

        rings = ContourExtractor().extract(parse_result)
        outer, inner = classify_rings(rings)
    """

    def __init__(self, endpoint_tol_mm: float = ENDPOINT_TOL_MM) -> None:
        self.tol = endpoint_tol_mm

    # -- 公共入口 ----------------------------------------------------------

    def extract(self, parse_result: Any) -> tuple[list[ContourRing], list[str]]:
        """提取所有闭合环。

        Args:
            parse_result: DxfParseResult（需带 lines / arcs / polylines）

        Returns:
            (环列表, 警告列表)。无闭合轮廓时返回 ([], warnings)，不抛异常——
            轮廓缺失是常态（纯孔图/标注图），由上层决定是否降级。
        """
        warnings: list[str] = []
        rings: list[ContourRing] = []

        segments: list[tuple[list[float], list[float], str, str, bool]] = []
        # (p_start, p_end, layer, handle, arc_approximated)

        for line in getattr(parse_result, "lines", []) or []:
            seg = self._line_segment(line)
            if seg:
                segments.append(seg)

        for arc in getattr(parse_result, "arcs", []) or []:
            segments.extend(self._arc_segments(arc))

        for poly in getattr(parse_result, "polylines", []) or []:
            direct, poly_segs, approx = self._polyline_handling(poly)
            if direct is not None:
                rings.append(direct)
            segments.extend(poly_segs)
            if approx:
                warnings.append(f"多段线 {getattr(poly, 'handle', '')} 含 bulge 弧段，顶点按弦近似（未采样弧线）")

        rings.extend(self._chain_to_rings(segments, warnings))

        # CIRCLE 实体是独立来源：盘/法兰/链轮/联轴节类零件的外边界就是一整个圆，
        # 用折线近似采样（CIRCLE_SEGMENTS 段）。同心小圆留给上层与孔特征去重。
        for circle in getattr(parse_result, "circles", []) or []:
            ring = self._circle_ring(circle)
            if ring is not None:
                rings.append(ring)

        deduped = self._dedupe(rings)
        if not deduped:
            warnings.append("未找到闭合轮廓：DXF 中没有首尾相接的封闭线环（可能为纯孔图/标注图/开放线段）")
        return deduped, warnings

    # -- 单实体 → 线段 -----------------------------------------------------

    @staticmethod
    def _line_segment(line: Any) -> tuple[list[float], list[float], str, str, bool] | None:
        try:
            sx, sy = float(line.start[0]), float(line.start[1])
            ex, ey = float(line.end[0]), float(line.end[1])
        except (AttributeError, TypeError, ValueError, IndexError):
            return None
        if math.isclose(sx, ex, abs_tol=1e-9) and math.isclose(sy, ey, abs_tol=1e-9):
            return None  # 零长度线不参与成环
        return (
            [sx, sy],
            [ex, ey],
            str(getattr(line, "layer", "0") or "0"),
            str(getattr(line, "handle", "") or ""),
            False,
        )

    @staticmethod
    def _arc_segments(arc: Any) -> list[tuple[list[float], list[float], str, str, bool]]:
        """把圆弧按角度采样成折线段。"""
        try:
            cx, cy = float(arc.center[0]), float(arc.center[1])
            r = float(arc.radius)
            a0 = math.radians(float(arc.start_angle))
            a1 = math.radians(float(arc.end_angle))
        except (AttributeError, TypeError, ValueError, IndexError):
            return []
        if r <= 0:
            return []
        sweep = a1 - a0
        # DXF 逆时针为正；负角差补一周
        while sweep < 0:
            sweep += 2 * math.pi
        if sweep <= 0:
            return []
        steps = max(ARC_MIN_SEGMENTS, min(ARC_MAX_SEGMENTS, int(math.ceil(abs(sweep) / (math.pi / 12)))))
        pts = [
            [cx + r * math.cos(a0 + sweep * i / steps), cy + r * math.sin(a0 + sweep * i / steps)]
            for i in range(steps + 1)
        ]
        layer = str(getattr(arc, "layer", "0") or "0")
        handle = str(getattr(arc, "handle", "") or "")
        return [(pts[i], pts[i + 1], layer, handle, True) for i in range(len(pts) - 1) if pts[i] != pts[i + 1]]

    def _circle_ring(self, circle: Any) -> ContourRing | None:
        """整圆 → 采样折线环（source="circle"，带 radius 供上层与孔特征去重）。"""
        try:
            cx, cy = float(circle.center[0]), float(circle.center[1])
            r = float(circle.radius)
        except (AttributeError, TypeError, ValueError, IndexError):
            return None
        if r <= 0 or not (math.isfinite(cx) and math.isfinite(cy) and math.isfinite(r)):
            return None
        pts = [
            [cx + r * math.cos(2 * math.pi * i / CIRCLE_SEGMENTS), cy + r * math.sin(2 * math.pi * i / CIRCLE_SEGMENTS)]
            for i in range(CIRCLE_SEGMENTS)
        ]
        ring = self._build_ring(
            pts,
            "circle",
            str(getattr(circle, "layer", "0") or "0"),
            [str(getattr(circle, "handle", "") or "")],
            True,
        )
        ring.radius = r
        return ring

    def _polyline_handling(
        self, poly: Any
    ) -> tuple[ContourRing | None, list[tuple[list[float], list[float], str, str, bool]], bool]:
        """闭合多段线直接成环；开放多段线拆成线段入链。

        Returns:
            (直接成环的结果, 待链化线段, 是否含 bulge 弧段)
        """
        verts_raw = getattr(poly, "vertices", None) or []
        pts: list[list[float]] = []
        has_bulge = False
        for v in verts_raw:
            try:
                x, y = float(v[0]), float(v[1])
            except (TypeError, ValueError, IndexError):
                continue
            pts.append([x, y])
            if len(v) > 2 and v[2] not in (0, 0.0):
                has_bulge = True

        layer = str(getattr(poly, "layer", "0") or "0")
        handle = str(getattr(poly, "handle", "") or "")

        if len(pts) < 3:
            return None, [], has_bulge

        if getattr(poly, "is_closed", False):
            # 去掉可能重复的首尾点
            ring_pts = list(pts)
            if ring_pts[0] == ring_pts[-1]:
                ring_pts = ring_pts[:-1]
            if len(ring_pts) < 3:
                return None, [], has_bulge
            return self._build_ring(ring_pts, "polyline", layer, [handle], has_bulge), [], has_bulge

        segs = [(pts[i], pts[i + 1], layer, handle, has_bulge) for i in range(len(pts) - 1) if pts[i] != pts[i + 1]]
        return None, segs, has_bulge

    # -- 线段图 → 环 -------------------------------------------------------

    def _chain_to_rings(
        self,
        segments: list[tuple[list[float], list[float], str, str, bool]],
        warnings: list[str],
    ) -> list[ContourRing]:
        """把线段连成图，取出「所有节点度数=2」的连通分量作为闭合环。"""
        if not segments:
            return []

        # 节点表：量化键 → 代表坐标
        coord_of: dict[tuple[int, int], list[float]] = {}
        adjacency: dict[tuple[int, int], list[tuple[tuple[int, int], int]]] = {}

        def node(p: list[float]) -> tuple[int, int]:
            k = _key(p[0], p[1])
            coord_of.setdefault(k, [p[0], p[1]])
            return k

        for idx, (a, b, *_rest) in enumerate(segments):
            ka, kb = node(a), node(b)
            if ka == kb:
                continue  # 自环段不参与（退化为零长度）
            adjacency.setdefault(ka, []).append((kb, idx))
            adjacency.setdefault(kb, []).append((ka, idx))

        visited_nodes: set[tuple[int, int]] = set()
        rings: list[ContourRing] = []

        for start in list(adjacency):
            if start in visited_nodes:
                continue
            # BFS 收集连通分量
            comp_nodes: set[tuple[int, int]] = set()
            comp_edges: set[int] = set()
            stack = [start]
            while stack:
                cur = stack.pop()
                if cur in comp_nodes:
                    continue
                comp_nodes.add(cur)
                for nb, eidx in adjacency.get(cur, []):
                    comp_edges.add(eidx)
                    if nb not in comp_nodes:
                        stack.append(nb)

            visited_nodes |= comp_nodes
            degrees = {n: len(adjacency.get(n, [])) for n in comp_nodes}

            if any(d != 2 for d in degrees.values()):
                bad = sum(1 for d in degrees.values() if d != 2)
                warnings.append(
                    f"[轮廓链化跳过] 连通分量含 {bad} 个非 2 度节点（端点未严格闭合或有分叉）。"
                    f"建议操作：在 CAD 中用 JOIN/BOUNDARY 生成闭合多段线后重新导出 DXF。"
                )
                continue
            if len(comp_nodes) < 3 or len(comp_edges) != len(comp_nodes):
                continue

            ring = self._walk_cycle(comp_nodes, comp_edges, adjacency, coord_of, segments)
            if ring is not None:
                rings.append(ring)

        return rings

    def _walk_cycle(
        self,
        comp_nodes: set[tuple[int, int]],
        comp_edges: set[int],
        adjacency: dict[tuple[int, int], list[tuple[tuple[int, int], int]]],
        coord_of: dict[tuple[int, int], list[float]],
        segments: list[tuple[list[float], list[float], str, str, bool]],
    ) -> ContourRing | None:
        """沿 2-正则分量走一圈，还原顶点顺序。"""
        start = next(iter(comp_nodes))
        cur = start
        prev_edge: int | None = None
        ordered: list[list[float]] = []
        handles: list[str] = []
        layers: list[str] = []
        arc_approx = False
        used: set[int] = set()

        while True:
            ordered.append(list(coord_of[cur]))
            nxt = None
            for nb, eidx in adjacency.get(cur, []):
                if eidx == prev_edge or eidx in used:
                    continue
                nxt = (nb, eidx)
                break
            if nxt is None:
                break
            nb, eidx = nxt
            used.add(eidx)
            seg = segments[eidx]
            handles.append(seg[3])
            layers.append(seg[2])
            arc_approx = arc_approx or seg[4]
            prev_edge = eidx
            cur = nb
            if cur == start:
                break

        if len(used) != len(comp_edges) or len(ordered) < 3:
            return None

        area = _signed_area(ordered)
        if abs(area) < 1e-6:
            return None
        xs = [p[0] for p in ordered]
        ys = [p[1] for p in ordered]
        return self._build_ring(
            ordered,
            "chained",
            layers[0] if layers else "0",
            sorted({h for h in handles if h}),
            arc_approx,
            area=area,
            min_x=min(xs),
            min_y=min(ys),
            max_x=max(xs),
            max_y=max(ys),
        )

    @staticmethod
    def _build_ring(
        pts: list[list[float]],
        source: str,
        layer: str,
        handles: list[str],
        arc_approx: bool,
        *,
        area: float | None = None,
        min_x: float | None = None,
        min_y: float | None = None,
        max_x: float | None = None,
        max_y: float | None = None,
    ) -> ContourRing:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return ContourRing(
            vertices=pts,
            area=_signed_area(pts) if area is None else area,
            min_x=min(xs) if min_x is None else min_x,
            min_y=min(ys) if min_y is None else min_y,
            max_x=max(xs) if max_x is None else max_x,
            max_y=max(ys) if max_y is None else max_y,
            source=source,
            layer=layer,
            handles=handles,
            approximated_arcs=arc_approx,
        )

    @staticmethod
    def _dedupe(rings: list[ContourRing]) -> list[ContourRing]:
        """按（量化顶点集合 + 面积）去重，避免同一轮廓被 polyline/chained 各算一次。"""
        seen: dict[tuple[float, tuple[tuple[int, int], ...]], ContourRing] = {}
        for r in rings:
            sig = tuple(sorted(_key(p[0], p[1]) for p in r.vertices))
            key = (round(r.abs_area, 1), sig)
            prev = seen.get(key)
            # polyline 直接成环保留顶点顺序，优先于链化结果
            if prev is None or (prev.source == "chained" and r.source == "polyline"):
                seen[key] = r
        return sorted(seen.values(), key=lambda x: x.abs_area, reverse=True)


def classify_rings(rings: list[ContourRing]) -> tuple[ContourRing | None, list[ContourRing]]:
    """把环分为「外轮廓」与「内环（型腔/内孔轮廓）」。

    规则：面积最大者为外轮廓；其余且任一顶点落在外轮廓内的为内环。
    完全在外轮廓之外的独立轮廓（多图框/多个零件同图）忽略并交由调用方警告。

    Returns:
        (outer 或 None, inner 列表)
    """
    if not rings:
        return None, []
    outer = rings[0]
    inner: list[ContourRing] = []
    for r in rings[1:]:
        if not outer.bbox_contains(*r.center):
            continue
        if _point_in_polygon(r.vertices[0][0], r.vertices[0][1], outer.vertices):
            inner.append(r)
    return outer, inner
