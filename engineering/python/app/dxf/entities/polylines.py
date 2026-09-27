"""DXF polylines 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfPolyline
from .common import safe_color

logger = logging.getLogger(__name__)


def lwpolyline_to_obj(entity) -> DxfPolyline:
    """将单个 LWPOLYLINE 实体转换为 DxfPolyline。"""
    vertices: list[tuple[float, ...]] = []
    # ezdxf 的 points() 方法返回带 bulge 的顶点
    try:
        points_with_bulge = entity.get_points(format="xyseb")  # x, y, start_width, end_width, bulge
    except (AttributeError, TypeError, ValueError) as e:
        # 旧版 ezdxf 退路
        logger.warning(
            "LWPOLYLINE get_points(format='xyseb') 失败，尝试 vertices() (handle=%s): %s",
            str(entity.dxf.handle),
            e,
            exc_info=True,
        )
        points_with_bulge = [(p[0], p[1], 0.0, 0.0, p[2] if len(p) > 2 else 0.0) for p in entity.vertices()]
    for pt in points_with_bulge:
        x = float(pt[0])
        y = float(pt[1])
        bulge = float(pt[4]) if len(pt) > 4 else 0.0
        if abs(bulge) > 1e-6:
            vertices.append((x, y, bulge))
        else:
            vertices.append((x, y))
    return DxfPolyline(
        vertices=vertices,
        is_closed=bool(entity.closed),
        is_3d=False,
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
        entity_type="LWPOLYLINE",
    )


def polyline_to_obj(entity) -> DxfPolyline:
    """将单个 POLYLINE 实体（带 VERTEX 子实体）转换为 DxfPolyline。"""
    vertices: list[tuple[float, ...]] = []
    is_3d = False
    # 遍历子实体（顶点级别容错：单个坏顶点跳过，不影响整体）
    for v in entity.virtual_entities():
        try:
            if v.dxftype() == "VERTEX":
                loc = v.dxf.location
                z = float(getattr(loc, "z", 0.0))
                if abs(z) > 1e-6:
                    is_3d = True
                bulge = float(getattr(v.dxf, "bulge", 0.0))
                if abs(bulge) > 1e-6:
                    vertices.append((float(loc.x), float(loc.y), bulge))
                else:
                    vertices.append((float(loc.x), float(loc.y), 0.0))
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            logger.warning("POLYLINE 顶点解析失败，跳过 (handle=%s): %s", str(entity.dxf.handle), e, exc_info=True)
            continue
    return DxfPolyline(
        vertices=vertices,
        is_closed=bool(entity.is_closed),
        is_3d=is_3d,
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
        entity_type="POLYLINE",
    )
