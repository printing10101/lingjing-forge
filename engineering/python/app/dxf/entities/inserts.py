"""DXF inserts 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfInsert

logger = logging.getLogger(__name__)


def insert_to_obj(entity) -> DxfInsert:
    """将单个 INSERT 实体转换为 DxfInsert。"""
    block_name = str(getattr(entity.dxf, "name", "") or "")
    insert_point = getattr(entity.dxf, "insert", None)
    if insert_point is None:
        position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    else:
        position = (
            float(insert_point.x),
            float(insert_point.y),
            float(getattr(insert_point, "z", 0.0)),
        )
    # scale (x, y, z) —— 显式 None 检查，避免 0.0 被错误覆盖
    _sx_raw = getattr(entity.dxf, "xscale", None)
    _sy_raw = getattr(entity.dxf, "yscale", None)
    _sz_raw = getattr(entity.dxf, "zscale", None)
    sx = float(_sx_raw) if _sx_raw is not None else 1.0
    sy = float(_sy_raw) if _sy_raw is not None else 1.0
    sz = float(_sz_raw) if _sz_raw is not None else 1.0
    # rotation —— 同样显式 None 检查
    _rot_raw = getattr(entity.dxf, "rotation", None)
    rotation = float(_rot_raw) if _rot_raw is not None else 0.0
    return DxfInsert(
        block_name=block_name,
        position=position,
        scale=(sx, sy, sz),
        rotation=rotation,
        layer=str(getattr(entity.dxf, "layer", "0")),
        handle=str(entity.dxf.handle),
    )
