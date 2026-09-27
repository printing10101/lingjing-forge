"""加工特征数据类（从 feature_extractor 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROXIMITY_THRESHOLD = 15.0
RECTANGLE_ANGLE_TOLERANCE = 2.0
RECTANGLE_LENGTH_TOLERANCE = 5.0


@dataclass
class HoleFeatureInfo:
    """孔特征信息。

    Attributes:
        hole_id: 孔标识符
        center_x: 圆心X坐标
        center_y: 圆心Y坐标
        diameter: 孔径(直径，mm)
        depth: 孔深(mm)，通孔为0
        depth_inferred: 深度是否由推断得出
        tolerance_grade: 公差等级
        hole_type: 孔类型 (through_hole/blind_hole/counterbore/center_hole)
        surface: 所在加工面
        layer: 原始图层
        associated_dim_handle: 关联尺寸标注句柄
        dimension_text: 尺寸标注文本
    """

    hole_id: str
    center_x: float
    center_y: float
    diameter: float
    depth: float = 0.0
    depth_inferred: bool = True
    tolerance_grade: str = "IT8"
    hole_type: str = "through_hole"
    surface: str = "A"
    layer: str = "0"
    associated_dim_handle: str = ""
    dimension_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "hole_id": self.hole_id,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "diameter": self.diameter,
            "depth": self.depth,
            "depth_inferred": self.depth_inferred,
            "tolerance_grade": self.tolerance_grade,
            "hole_type": self.hole_type,
            "surface": self.surface,
            "layer": self.layer,
            "dimension_text": self.dimension_text,
        }


@dataclass
class PlaneFeatureInfo:
    """平面特征信息（矩形轮廓）。

    Attributes:
        plane_id: 平面标识符
        center_x: 中心X坐标
        center_y: 中心Y坐标
        length: 长度(X方向)
        width: 宽度(Y方向)
        surface: 所在加工面
        layer: 原始图层
    """

    plane_id: str
    center_x: float
    center_y: float
    length: float
    width: float
    surface: str = "A"
    layer: str = "0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plane_id": self.plane_id,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "length": self.length,
            "width": self.width,
            "surface": self.surface,
            "layer": self.layer,
        }


@dataclass
class FeatureExtractionResult:
    """特征提取结果。

    Attributes:
        holes: 孔特征列表
        planes: 平面特征列表
        overall_length: 零件总长(X方向)
        overall_width: 零件总宽(Y方向)
        overall_height: 推断的零件高度(Z方向)
        height_inferred: 高度是否由推断得出
        warnings: 提取过程中的警告
        errors: 提取过程中的错误
    """

    holes: list[HoleFeatureInfo] = field(default_factory=list)
    planes: list[PlaneFeatureInfo] = field(default_factory=list)
    overall_length: float = 0.0
    overall_width: float = 0.0
    overall_height: float = 10.0
    height_inferred: bool = True
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    @property
    def hole_count(self) -> int:
        return len(self.holes)

    @property
    def plane_count(self) -> int:
        return len(self.planes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hole_count": self.hole_count,
            "plane_count": self.plane_count,
            "overall_length": self.overall_length,
            "overall_width": self.overall_width,
            "overall_height": self.overall_height,
            "height_inferred": self.height_inferred,
            "holes": [h.to_dict() for h in self.holes],
            "planes": [p.to_dict() for p in self.planes],
            "warnings": self.warnings,
            "errors": self.errors,
        }
