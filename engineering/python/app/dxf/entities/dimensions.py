"""DXF dimensions 实体转换函数（从 DxfParser 拆出的纯函数）。"""

from __future__ import annotations

import logging


from app.dxf._entities import DxfDimension
from .common import safe_color

logger = logging.getLogger(__name__)


def dimension_to_obj(entity) -> DxfDimension | None:
    """提取单个尺寸标注实体的完整信息。

    利用ezdxf的Dimension对象API获取标注的几何信息、
    测量值和关联实体，并安全包装所有属性访问以防数据缺失。
    """
    dim_type = get_dimension_type(entity)
    measurement = get_dimension_measurement(entity)
    text_content = get_dimension_text(entity)
    position = get_dimension_position(entity)

    associated = []
    try:
        if hasattr(entity.dxf, "geometry"):
            geo_handle = entity.dxf.geometry
            if geo_handle:
                associated.append(str(geo_handle))
    except (AttributeError, KeyError, TypeError, ValueError) as assoc_err:
        # 标注几何关联属性访问失败时不影响其他属性返回，记录以便排查
        logger.warning(
            "Failed to read DIMENSION geometry handle (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            assoc_err,
            exc_info=True,
        )

    return DxfDimension(
        dim_type=dim_type,
        measurement=measurement,
        text=text_content,
        position=position,
        layer=str(entity.dxf.layer),
        color=safe_color(entity),
        handle=str(entity.dxf.handle),
        associated_entities=associated,
    )


def get_dimension_type(entity) -> str:
    """根据DXF组码70判断标注类型。"""
    dimtype_map = {
        0: "LINEAR_ROTATED",
        1: "ALIGNED",
        2: "ANGULAR",
        3: "DIAMETER",
        4: "RADIUS",
        5: "ANGULAR_3PT",
        6: "ORDINATE",
        32: "ORDINATE_X",
        64: "ORDINATE_Y",
        160: "ARC_LENGTH",
    }
    try:
        flag = entity.dxf.dimtype
        return dimtype_map.get(flag & 0x7F, f"UNKNOWN_{flag}")
    except (AttributeError, KeyError, TypeError) as exc:
        # 修复：原代码用裸 except Exception 静默吞掉所有错误，
        # 实际只可能是 DIMENSION 字段缺失/类型异常。
        logger.warning(
            "_get_dimtype 降级到 UNKNOWN | handle=%s | exc=%s: %s",
            getattr(entity.dxf, "handle", "?"),
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return "UNKNOWN"


def get_dimension_measurement(entity) -> float:
    """安全获取标注的测量值。"""
    try:
        return float(entity.dxf.measurement)
    except (AttributeError, TypeError, ValueError) as e:
        logger.warning(
            "无法从 entity.dxf.measurement 获取测量值 (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            e,
            exc_info=True,
        )
        try:
            raw_text = get_dimension_text(entity)
            import re

            nums = re.findall(r"[\d.]+", raw_text)
            if nums:
                return float(nums[0])
        except (AttributeError, TypeError, ValueError) as parse_err:
            # 备选策略：解析失败时使用 0.0 占位，记录以便后续排查
            logger.warning(
                "Failed to parse measurement fallback from text (handle=%s): %s",
                getattr(entity.dxf, "handle", "?"),
                parse_err,
                exc_info=True,
            )
        return 0.0


def get_dimension_text(entity) -> str:
    """安全获取标注文本。"""
    try:
        text = entity.dxf.text
        if text:
            return str(text).strip()
    except (AttributeError, TypeError, ValueError) as text_err:
        # 主路径读不到文本时，会回退到 measurement 占位，记录失败原因
        logger.warning(
            "Failed to read DIMENSION text (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            text_err,
            exc_info=True,
        )
    try:
        return str(entity.dxf.measurement)
    except (AttributeError, TypeError, ValueError) as exc:
        # 修复：原代码用裸 except Exception 静默吞掉所有错误。
        logger.warning(
            "_get_dimension_text measurement 兜底失败 (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            exc,
            exc_info=True,
        )
        return ""


def get_dimension_position(entity) -> tuple[float, float, float]:
    """安全获取标注文本位置。"""
    try:
        return (
            float(entity.dxf.text_midpoint.x),
            float(entity.dxf.text_midpoint.y),
            float(entity.dxf.text_midpoint.z) if hasattr(entity.dxf.text_midpoint, "z") else 0.0,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        # 修复：原代码用裸 except Exception 静默吞掉所有错误。
        logger.warning(
            "_get_dimension_position: text_midpoint 缺失, 尝试 def_point (handle=%s): %s",
            getattr(entity.dxf, "handle", "?"),
            exc,
            exc_info=True,
        )
        try:
            return (
                float(entity.dxf.def_point.x),
                float(entity.dxf.def_point.y),
                float(entity.dxf.def_point.z) if hasattr(entity.dxf.def_point, "z") else 0.0,
            )
        except (AttributeError, TypeError, ValueError) as exc2:
            logger.warning(
                "_get_dimension_position: def_point 兜底失败 (handle=%s): %s",
                getattr(entity.dxf, "handle", "?"),
                exc2,
                exc_info=True,
            )
            return (0.0, 0.0, 0.0)
