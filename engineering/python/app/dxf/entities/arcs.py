"""DXF arcs 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfArc
from .common import safe_color

logger = logging.getLogger(__name__)


def arc_to_obj(entity) -> DxfArc:
    """将单个 ARC 实体转换为 DxfArc。"""
    return DxfArc(
        center=(
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.center.z) if entity.dxf.hasattr("center") and hasattr(entity.dxf.center, "z") else 0.0,
        ),
        radius=float(entity.dxf.radius),
        start_angle=float(entity.dxf.start_angle),
        end_angle=float(entity.dxf.end_angle),
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
    )
