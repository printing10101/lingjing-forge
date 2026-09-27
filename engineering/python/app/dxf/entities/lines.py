"""DXF lines 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfLine
from .common import safe_color

logger = logging.getLogger(__name__)


def line_to_obj(entity) -> DxfLine:
    """将单个 LINE 实体转换为 DxfLine。"""
    return DxfLine(
        start=(
            float(entity.dxf.start.x),
            float(entity.dxf.start.y),
            float(entity.dxf.start.z) if entity.dxf.hasattr("start") and hasattr(entity.dxf.start, "z") else 0.0,
        ),
        end=(
            float(entity.dxf.end.x),
            float(entity.dxf.end.y),
            float(entity.dxf.end.z) if entity.dxf.hasattr("end") and hasattr(entity.dxf.end, "z") else 0.0,
        ),
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
    )
