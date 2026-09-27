"""孔特征数据类（从 hole_recognizer 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HoleFeature:
    """单个孔特征的完整信息。

    Attributes:
        hole_id: 孔的唯一标识符，如 "H001"
        type: 孔类型 - through_hole/blind_hole/counterbore/center_hole/threaded_hole
        position_x: 孔中心X坐标 (mm)，世界坐标系
        position_y: 孔中心Y坐标 (mm)，世界坐标系
        position_z: 孔起始Z坐标 (mm)，世界坐标系 - 钻孔起点
        diameter: 孔的公称直径 (mm)
        depth: 孔的总深度 (mm)，通孔时为壁厚
        bottom_angle: 孔底角度 (度)，标准麻花钻底角118°
        tolerance_grade: 公差等级，如 "H7", "H8" (默认IT8)
        surface_roughness_ra: 表面粗糙度 Ra值 (μm)
        direction: 孔轴线方向向量 [nx, ny, nz]，默认 [0,0,-1] 即Z轴负向钻孔
        surface: 孔所在加工面标识 "A"/"B"/"C"/"D"/"E"/"F"
        parent_feature_id: 父特征ID（如沉头孔的大孔引用）
        is_threaded: 是否为螺纹孔
        thread_spec: 螺纹规格，如 "M8×1.25"
        counterbore_diameter: 沉头孔大径 (mm)，仅counterbore类型
        counterbore_depth: 沉头孔深度 (mm)，仅counterbore类型
        metadata: 附加元数据字典
    """

    hole_id: str
    type: str
    position_x: float
    position_y: float
    position_z: float
    diameter: float
    depth: float
    bottom_angle: float = 118.0
    tolerance_grade: str = "H8"
    surface_roughness_ra: float = 3.2
    direction: list[float] = field(default_factory=lambda: [0.0, 0.0, -1.0])
    surface: str = "A"
    parent_feature_id: str = ""
    is_threaded: bool = False
    thread_spec: str = ""
    counterbore_diameter: float = 0.0
    counterbore_depth: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_through(self) -> bool:
        """Check whether the hole is a through-hole.

        Returns:
            True if the hole type is 'through_hole'.
        """
        return self.type == "through_hole"

    def is_blind(self) -> bool:
        """Check whether the hole is a blind hole.

        Returns:
            True if the hole type is 'blind_hole'.
        """
        return self.type == "blind_hole"

    def is_counterbore(self) -> bool:
        """Check whether the hole is a counterbore hole.

        Returns:
            True if the hole type is 'counterbore'.
        """
        return self.type == "counterbore"

    def aspect_ratio(self) -> float:
        """Calculate the depth-to-diameter ratio (L/D) of the hole.

        The ratio is used to evaluate machining difficulty:
        - L/D < 3: Standard drilling
        - 3 <= L/D < 5: Deep hole, requires peck drilling
        - L/D >= 5: Deep hole, requires gun drilling or multiple peck cycles

        Returns:
            Depth-to-diameter ratio. Returns 0.0 if diameter is zero or negative.
        """
        return self.depth / self.diameter if self.diameter > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert the hole feature to a dictionary representation.

        Returns:
            A dictionary containing all relevant hole properties suitable
            for serialization, including position, dimensions, tolerances,
            and computed aspect ratio.
        """
        return {
            "hole_id": self.hole_id,
            "type": self.type,
            "position": {
                "x": self.position_x,
                "y": self.position_y,
                "z": self.position_z,
            },
            "diameter": self.diameter,
            "depth": self.depth,
            "bottom_angle": self.bottom_angle,
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "direction": self.direction,
            "surface": self.surface,
            "parent_feature_id": self.parent_feature_id,
            "is_threaded": self.is_threaded,
            "thread_spec": self.thread_spec,
            "counterbore_diameter": self.counterbore_diameter,
            "counterbore_depth": self.counterbore_depth,
            "aspect_ratio": round(self.aspect_ratio(), 2),
        }

    def to_machining_feature(self) -> dict[str, Any]:
        """转换为MachiningFeature兼容字典，用于工艺规划模块。

        Returns:
            包含name/type/geometric_type等字段的字典
        """
        feature_type = "through_hole" if self.is_through() else "blind_hole"
        if self.is_counterbore():
            feature_type = "counterbore"
        if self.type == "center_hole":
            feature_type = "center_hole"

        return {
            "name": self.hole_id,
            "type": feature_type,
            "geometric_type": "cylinder",
            "tolerance_grade": self._it_grade_from_hole_tolerance(),
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": self.type == "center_hole",
            "machining_method": "",
            "priority": "high" if self.tolerance_grade in ("H6", "H7") else "medium",
            "surface": self.surface,
            "dimensions": {
                "diameter": self.diameter,
                "depth": self.depth,
                "position_x": self.position_x,
                "position_y": self.position_y,
            },
            "parent_feature": self.parent_feature_id,
            "tolerances": {"diameter_upper": 0.0, "diameter_lower": 0.0},
        }

    def _it_grade_from_hole_tolerance(self) -> str:
        """将H7/H8等孔公差转换为IT等级表示"""
        grade_map = {"H5": "IT5", "H6": "IT6", "H7": "IT7", "H8": "IT8", "H9": "IT9", "H10": "IT10", "H11": "IT11"}
        return grade_map.get(self.tolerance_grade.upper(), "IT8")


@dataclass
class HoleRecognitionResult:
    """孔特征识别的完整结果。

    Attributes:
        holes: 识别出的所有孔特征列表
        total_count: 孔总数
        type_summary: 各类型孔数量统计 {类型: 数量}
        warnings: 识别过程中的警告信息
        errors: 识别过程中的错误信息
        accuracy_metrics: 识别准确率指标
    """

    holes: list[HoleFeature] = field(default_factory=list)
    total_count: int = 0
    type_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accuracy_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the recognition result to a dictionary representation.

        Returns:
            A dictionary containing total count, type summary, all hole details,
            warnings, errors, and accuracy metrics.
        """
        return {
            "total_count": self.total_count,
            "type_summary": self.type_summary,
            "holes": [h.to_dict() for h in self.holes],
            "warnings": self.warnings,
            "errors": self.errors,
            "accuracy_metrics": self.accuracy_metrics,
        }

    @property
    def is_reliable(self) -> bool:
        """Check whether the recognition result meets the reliability threshold.

        Returns:
            True if there are no errors and the overall accuracy rate is >= 99%.
        """
        rate = self.accuracy_metrics.get("overall", 0.0)
        return len(self.errors) == 0 and rate >= 0.99
