"""DXF hatches 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfHatch
from .common import safe_color

logger = logging.getLogger(__name__)


def hatch_to_obj(entity) -> DxfHatch:
    """将单个 HATCH 实体转换为 DxfHatch。"""
    pattern_name = str(getattr(entity.dxf, "pattern_name", "") or "")
    solid_fill = bool(getattr(entity.dxf, "solid_fill", 0) or 0)
    # 提取边界路径
    boundary_paths: list[list[tuple[float, float, float]]] = []
    try:
        # 优先用 ezdxf.paths.make_path() 接口
        for path in entity.paths:
            pts: list[tuple[float, float, float]] = []
            try:
                for v in path.vertices:
                    # v 通常是 (x, y) 或 (x, y, bulge)
                    x = float(v[0])
                    y = float(v[1])
                    pts.append((x, y, 0.0))
            except (AttributeError, TypeError, ValueError):
                # 退化为遍历虚实体
                try:
                    for ve in path.virtual_entities():
                        if ve.dxftype() in ("LINE", "ARC", "LWPOLYLINE", "SPLINE"):
                            start = getattr(ve.dxf, "start", None)
                            if start is not None:
                                pts.append(
                                    (
                                        float(start[0]),
                                        float(start[1]),
                                        float(getattr(start, "z", 0.0)),
                                    )
                                )
                        end = getattr(ve.dxf, "end", None)
                        if end is not None:
                            pts.append(
                                (
                                    float(end[0]),
                                    float(end[1]),
                                    float(getattr(end, "z", 0.0)),
                                )
                            )
                except (AttributeError, TypeError, ValueError) as e_inner:
                    logger.warning(
                        "HATCH 边界路径点提取失败，跳过该路径: %s",
                        e_inner,
                        exc_info=True,
                    )
            if pts:
                boundary_paths.append(pts)
    except (AttributeError, TypeError, ValueError) as e_outer:
        # 极简兜底：边界抽取失败时记录日志，便于排查
        logger.warning(
            "HATCH 边界抽取失败(handle=%s): %s",
            getattr(entity.dxf, "handle", "<unknown>"),
            e_outer,
            exc_info=True,
        )
    return DxfHatch(
        pattern_name=pattern_name,
        solid_fill=solid_fill,
        boundary_paths=boundary_paths,
        layer=str(getattr(entity.dxf, "layer", "0")),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
    )
