"""DXF splines 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging

from app.dxf._entities import DxfSpline

logger = logging.getLogger(__name__)


def spline_to_obj(entity) -> DxfSpline:
    """将单个 SPLINE 实体转换为 DxfSpline。"""
    # degree —— 显式 None 检查，避免 0 被错误覆盖为 3
    _deg_raw = getattr(entity.dxf, "degree", None)
    if _deg_raw is None:
        degree = 3
    else:
        degree = int(_deg_raw) if int(_deg_raw) > 0 else 3
    # control points（可能为空；fit_points 单独提取）
    cp: list[tuple[float, float, float]] = []
    try:
        # 部分 ezdxf 版本：从 control_points 获取
        for ctl in entity.control_points:
            cp.append((float(ctl[0]), float(ctl[1]), float(ctl[2])))
    except (AttributeError, TypeError, ValueError) as e:
        # 退化：基于 fit_points 估计
        logger.warning("SPLINE control_points 解析失败，尝试 fit_points: %s", e, exc_info=True)
        try:
            for f in entity.fit_points:
                cp.append((float(f[0]), float(f[1]), float(f[2])))
        except (AttributeError, TypeError, ValueError) as e2:
            logger.warning("SPLINE fit_points 也解析失败: %s", e2, exc_info=True)
    # fit points
    fp: list[tuple[float, float, float]] = []
    try:
        for f in entity.fit_points:
            fp.append((float(f[0]), float(f[1]), float(f[2])))
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning("SPLINE fit_points 解析失败: %s", e, exc_info=True)
    # knots
    knots: list[float] = []
    try:
        knots = [float(k) for k in entity.knots]
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning("SPLINE knots 解析失败: %s", e, exc_info=True)
    # closed —— 显式取布尔值，避免 0/False 混淆
    _closed_dxf = getattr(entity.dxf, "closed", 0)
    _closed_attr = getattr(entity, "closed", False)
    closed = bool(_closed_dxf) or bool(_closed_attr)
    return DxfSpline(
        degree=degree,
        control_points=cp,
        fit_points=fp,
        knots=knots,
        closed=closed,
        layer=str(getattr(entity.dxf, "layer", "0")),
        handle=str(entity.dxf.handle),
    )
