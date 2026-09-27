"""DXF circles 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging


from app.dxf._entities import DxfCircle
from .common import safe_color

logger = logging.getLogger(__name__)


def circle_to_obj(entity) -> DxfCircle | None:
    """将单个 CIRCLE 实体转换为 DxfCircle；radius<=0 返回 None。"""
    circle = DxfCircle(
        center=(
            float(entity.dxf.center.x),
            float(entity.dxf.center.y),
            float(entity.dxf.center.z) if entity.dxf.hasattr("center") and hasattr(entity.dxf.center, "z") else 0.0,
        ),
        radius=float(entity.dxf.radius),
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
    )
    return circle if circle.radius > 0 else None
