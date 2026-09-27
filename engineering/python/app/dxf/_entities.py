"""DXF 实体数据类（V3.0 自 dxf_parser.py 拆分）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DxfLine:
    """DXF直线实体。

    Attributes:
        start: 起点坐标 (x, y, z)
        end: 终点坐标 (x, y, z)
        layer: 图层名称
        color: 颜色索引号 (ACI)，0=BYBLOCK, 256=BYLAYER
        handle: 实体句柄
        lineweight: 线宽枚举值
    """

    start: tuple[float, float, float]
    end: tuple[float, float, float]
    layer: str = "0"
    color: int = 256
    handle: str = ""
    lineweight: int = -1


@dataclass
class DxfCircle:
    """DXF圆实体。

    Attributes:
        center: 圆心坐标 (x, y, z)
        radius: 半径
        layer: 图层名称
        color: 颜色索引号
        handle: 实体句柄
    """

    center: tuple[float, float, float]
    radius: float
    layer: str = "0"
    color: int = 256
    handle: str = ""


@dataclass
class DxfArc:
    """DXF圆弧实体。

    Attributes:
        center: 圆心坐标 (x, y, z)
        radius: 半径
        start_angle: 起始角度(度)
        end_angle: 终止角度(度)
        layer: 图层名称
        color: 颜色索引号
        handle: 实体句柄
    """

    center: tuple[float, float, float]
    radius: float
    start_angle: float
    end_angle: float
    layer: str = "0"
    color: int = 256
    handle: str = ""


@dataclass
class DxfText:
    """DXF文字实体。

    Attributes:
        content: 文本内容
        position: 插入点坐标 (x, y, z)
        height: 文字高度
        rotation: 旋转角度(度)
        layer: 图层名称
        color: 颜色索引号
        handle: 实体句柄
        entity_type: 实体类型 ("TEXT" 或 "MTEXT")
    """

    content: str
    position: tuple[float, float, float]
    height: float = 2.5
    rotation: float = 0.0
    layer: str = "0"
    color: int = 256
    handle: str = ""
    entity_type: str = "TEXT"


@dataclass
class DxfDimension:
    """DXF尺寸标注实体。

    Attributes:
        dim_type: 标注类型 (LINEAR/ALIGNED/ANGULAR/RADIUS/DIAMETER/ORDINATE)
        measurement: 测量值(图形单位)
        text: 标注文本内容(可能含公差)
        position: 标注文本位置 (x, y, z)
        layer: 图层名称
        color: 颜色索引号
        handle: 实体句柄
        associated_entities: 关联的实体句柄列表
    """

    dim_type: str
    measurement: float
    text: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    layer: str = "0"
    color: int = 256
    handle: str = ""
    associated_entities: list[str] = field(default_factory=list)


@dataclass
class DxfPolyline:
    """DXF多段线实体（POLYLINE / LWPOLYLINE）。

    Attributes:
        vertices: 顶点列表，每项为 (x, y) 或 (x, y, bulge)
                  bulge 是切线凸度，用于表示圆弧段（LWPOLYLINE 专用）：
                  bulge = tan(arc_angle / 4)
                  bulge > 0 表示逆时针，< 0 表示顺时针
        is_closed: 是否闭合
        is_3d: 是否是 3D 多段线
        layer: 图层名称
        color: 颜色索引号
        handle: 实体句柄
        entity_type: "POLYLINE" 或 "LWPOLYLINE"
    """

    vertices: list[tuple[float, ...]]
    is_closed: bool = False
    is_3d: bool = False
    layer: str = "0"
    color: int = 256
    handle: str = ""
    entity_type: str = "LWPOLYLINE"

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "is_closed": self.is_closed,
            "is_3d": self.is_3d,
            "layer": self.layer,
            "color": self.color,
            "handle": self.handle,
            "vertex_count": self.vertex_count,
            "vertices": [list(v) for v in self.vertices],
        }

    def bbox(self) -> tuple[float, float, float, float]:
        """计算包围盒 (min_x, min_y, max_x, max_y)。"""
        if not self.vertices:
            return (0.0, 0.0, 0.0, 0.0)
        xs = [v[0] for v in self.vertices]
        ys = [v[1] for v in self.vertices]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class DxfHatch:
    """DXF 填充（HATCH）实体。

    Attributes:
        pattern_name: 填充图案名（如 ANSI31、SOLID）
        solid_fill: 是否为实心填充
        boundary_paths: 边界路径（每个是顶点列表）
        layer: 图层
        color: 颜色
        handle: 实体句柄
    """

    pattern_name: str = ""
    solid_fill: bool = False
    boundary_paths: list[list[tuple[float, float, float]]] = field(default_factory=list)
    layer: str = "0"
    color: int = 256
    handle: str = ""


@dataclass
class DxfInsert:
    """DXF 块插入（INSERT）实体 — 即 Block 引用。

    Attributes:
        block_name: 被引用的块名
        position: 插入点
        scale: X/Y/Z 缩放因子
        rotation: 旋转角度（度）
        layer: 图层
        handle: 实体句柄
    """

    block_name: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    rotation: float = 0.0
    layer: str = "0"
    handle: str = ""


@dataclass
class DxfSpline:
    """DXF 样条曲线（SPLINE）实体。

    Attributes:
        degree: 阶数（3=三次 B-Spline）
        control_points: 控制点
        fit_points: 拟合点（可能为空）
        knots: 节点向量
        closed: 是否闭合
        layer: 图层
        handle: 实体句柄
    """

    degree: int = 3
    control_points: list[tuple[float, float, float]] = field(default_factory=list)
    fit_points: list[tuple[float, float, float]] = field(default_factory=list)
    knots: list[float] = field(default_factory=list)
    closed: bool = False
    layer: str = "0"
    handle: str = ""


@dataclass
class DxfParseResult:
    """DXF解析结果。

    Attributes:
        file_name: 源文件名
        file_size: 文件大小(字节)
        dxf_version: DXF版本字符串
        parse_time_ms: 解析耗时(毫秒)
        lines: 直线列表
        circles: 圆列表
        arcs: 圆弧列表
        texts: 文字列表
        dimensions: 尺寸标注列表
        polylines: 多段线列表
        entity_counts: 各类型实体数量统计
        warnings: 解析过程中的警告
        errors: 解析过程中的错误
        extents: 图形范围 (min_x, min_y, max_x, max_y)
    """

    file_name: str = ""
    file_size: int = 0
    dxf_version: str = ""
    parse_time_ms: float = 0.0
    lines: list[DxfLine] = field(default_factory=list)
    circles: list[DxfCircle] = field(default_factory=list)
    arcs: list[DxfArc] = field(default_factory=list)
    texts: list[DxfText] = field(default_factory=list)
    dimensions: list[DxfDimension] = field(default_factory=list)
    polylines: list[DxfPolyline] = field(default_factory=list)
    # 高级实体类型（HATCH/BLOCK INSERT/SPLINE）
    hatches: list[DxfHatch] = field(default_factory=list)
    inserts: list[DxfInsert] = field(default_factory=list)
    splines: list[DxfSpline] = field(default_factory=list)
    entity_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    extents: dict[str, float] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def total_entities(self) -> int:
        return sum(self.entity_counts.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "file_size": self.file_size,
            "dxf_version": self.dxf_version,
            "parse_time_ms": round(self.parse_time_ms, 2),
            "entity_counts": self.entity_counts,
            "total_entities": self.total_entities,
            "lines_count": len(self.lines),
            "circles_count": len(self.circles),
            "arcs_count": len(self.arcs),
            "texts_count": len(self.texts),
            "dimensions_count": len(self.dimensions),
            "polylines_count": len(self.polylines),
            "hatches_count": len(self.hatches),
            "inserts_count": len(self.inserts),
            "splines_count": len(self.splines),
            "extents": self.extents,
            "warnings": self.warnings,
            "errors": self.errors,
            "success": self.success,
        }
