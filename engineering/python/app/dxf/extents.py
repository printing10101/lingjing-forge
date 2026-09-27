"""DXF 图形范围计算 compute_extents（从 dxf_parser 拆出）。"""

from __future__ import annotations

from app.dxf._entities import DxfParseResult


def compute_extents(result: DxfParseResult) -> None:
    """计算图形范围。"""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for line in result.lines:
        min_x = min(min_x, line.start[0], line.end[0])
        max_x = max(max_x, line.start[0], line.end[0])
        min_y = min(min_y, line.start[1], line.end[1])
        max_y = max(max_y, line.start[1], line.end[1])

    for circle in result.circles:
        min_x = min(min_x, circle.center[0] - circle.radius)
        max_x = max(max_x, circle.center[0] + circle.radius)
        min_y = min(min_y, circle.center[1] - circle.radius)
        max_y = max(max_y, circle.center[1] + circle.radius)

    for arc in result.arcs:
        min_x = min(min_x, arc.center[0] - arc.radius)
        max_x = max(max_x, arc.center[0] + arc.radius)
        min_y = min(min_y, arc.center[1] - arc.radius)
        max_y = max(max_y, arc.center[1] + arc.radius)

    for polyline in result.polylines:
        for v in polyline.vertices:
            min_x = min(min_x, v[0])
            max_x = max(max_x, v[0])
            min_y = min(min_y, v[1])
            max_y = max(max_y, v[1])

    # HATCH 边界范围
    for hatch in result.hatches:
        for path in hatch.boundary_paths:
            for p in path:
                min_x = min(min_x, p[0])
                max_x = max(max_x, p[0])
                min_y = min(min_y, p[1])
                max_y = max(max_y, p[1])

    # INSERT 位置
    for ins in result.inserts:
        min_x = min(min_x, ins.position[0])
        max_x = max(max_x, ins.position[0])
        min_y = min(min_y, ins.position[1])
        max_y = max(max_y, ins.position[1])

    # SPLINE 控制点范围
    for sp in result.splines:
        for p in sp.control_points:
            min_x = min(min_x, p[0])
            max_x = max(max_x, p[0])
            min_y = min(min_y, p[1])
            max_y = max(max_y, p[1])

    if min_x == float("inf"):
        min_x = min_y = max_x = max_y = 0.0

    result.extents = {
        "min_x": round(min_x, 4),
        "min_y": round(min_y, 4),
        "max_x": round(max_x, 4),
        "max_y": round(max_y, 4),
        "width": round(max_x - min_x, 4),
        "height": round(max_y - min_y, 4),
    }
