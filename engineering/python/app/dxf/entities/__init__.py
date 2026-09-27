"""DXF 实体转换函数包（从 dxf_parser 拆出）。"""

from __future__ import annotations

from .common import safe_color
from .lines import line_to_obj
from .circles import circle_to_obj
from .arcs import arc_to_obj
from .texts import mtext_to_obj, text_to_obj
from .dimensions import (
    dimension_to_obj,
    get_dimension_measurement,
    get_dimension_position,
    get_dimension_text,
    get_dimension_type,
)
from .polylines import lwpolyline_to_obj, polyline_to_obj
from .hatches import hatch_to_obj
from .inserts import insert_to_obj
from .splines import spline_to_obj

__all__ = [
    "line_to_obj",
    "circle_to_obj",
    "arc_to_obj",
    "text_to_obj",
    "mtext_to_obj",
    "dimension_to_obj",
    "get_dimension_type",
    "get_dimension_measurement",
    "get_dimension_text",
    "get_dimension_position",
    "lwpolyline_to_obj",
    "polyline_to_obj",
    "hatch_to_obj",
    "insert_to_obj",
    "spline_to_obj",
    "safe_color",
]
