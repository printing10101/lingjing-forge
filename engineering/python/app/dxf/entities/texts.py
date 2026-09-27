"""DXF texts 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfText
from .common import safe_color

logger = logging.getLogger(__name__)


def text_to_obj(entity) -> DxfText:
    """将单个 TEXT 实体转换为 DxfText。"""
    return DxfText(
        content=str(entity.dxf.text),
        position=(
            float(entity.dxf.insert.x),
            float(entity.dxf.insert.y),
            float(entity.dxf.insert.z) if entity.dxf.hasattr("insert") and hasattr(entity.dxf.insert, "z") else 0.0,
        ),
        height=float(entity.dxf.height) if entity.dxf.hasattr("height") else 2.5,
        rotation=float(entity.dxf.rotation) if entity.dxf.hasattr("rotation") else 0.0,
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
        entity_type="TEXT",
    )


def mtext_to_obj(entity) -> DxfText:
    """将单个 MTEXT 实体转换为 DxfText。"""
    raw_text = entity.plain_text() if hasattr(entity, "plain_text") else str(entity.dxf.text)
    return DxfText(
        content=raw_text,
        position=(
            float(entity.dxf.insert.x),
            float(entity.dxf.insert.y),
            float(entity.dxf.insert.z) if entity.dxf.hasattr("insert") and hasattr(entity.dxf.insert, "z") else 0.0,
        ),
        height=float(entity.dxf.char_height) if entity.dxf.hasattr("char_height") else 2.5,
        rotation=float(entity.dxf.rotation) if entity.dxf.hasattr("rotation") else 0.0,
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
        entity_type="MTEXT",
    )
