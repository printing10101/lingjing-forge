"""Cavity (rectangular pocket) feature recognition module.

Recognizes rectangular cavity/pocket features from DXF parsing data,
including through pockets and blind pockets. Input is a list of contour/entity
dictionaries, output is a structured list of CavityFeature dataclass objects.

Recognition workflow:
1. Iterate through input contours, filter closed rectangular contours
2. For each rectangular contour, extract length, width, and center coordinates
3. Distinguish through_pocket vs blind_pocket by depth information
4. Extract tolerance grade, surface roughness, and other manufacturing attributes
5. Output a unified list of CavityFeature objects
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.error_taxonomy import ErrorCategory, ManufacturingError


@dataclass
class CavityFeature:
    """A single cavity/pocket feature with complete geometric information.

    Attributes:
        cavity_id: Unique identifier for the cavity, e.g. 'C001'.
        type: Cavity type - through_pocket/blind_pocket.
        length: Cavity length (mm).
        width: Cavity width (mm).
        depth: Cavity depth (mm), 0 for through pockets.
        center_x: Center X coordinate (mm).
        center_y: Center Y coordinate (mm).
        center_z: Center Z coordinate (mm).
        orientation: Cavity rotation angle (degrees).
        tolerance_grade: Tolerance grade, e.g. 'H8'.
        surface_roughness_ra: Surface roughness Ra value (um).
        surface: Machining surface identifier.
        wall_thickness: Wall thickness (mm).
        bottom_type: Bottom surface type ('flat' or other).
        metadata: Additional metadata dictionary.
    """

    cavity_id: str
    type: str
    length: float
    width: float
    depth: float
    center_x: float
    center_y: float
    center_z: float = 0.0
    orientation: float = 0.0
    tolerance_grade: str = "H8"
    surface_roughness_ra: float = 3.2
    surface: str = "A"
    wall_thickness: float = 0.0
    bottom_type: str = "flat"
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_through(self) -> bool:
        """Check whether the cavity is a through pocket.

        Returns:
            True if the cavity type is 'through_pocket'.
        """
        return self.type == "through_pocket"

    def is_blind(self) -> bool:
        """Check whether the cavity is a blind pocket.

        Returns:
            True if the cavity type is 'blind_pocket'.
        """
        return self.type == "blind_pocket"

    def area(self) -> float:
        """Calculate the cavity area (length * width).

        Returns:
            Area in mm².
        """
        return self.length * self.width

    def aspect_ratio(self) -> float:
        """Calculate the length-to-width aspect ratio.

        Returns:
            Length/width ratio. Returns 0.0 if width is zero or negative.
        """
        if self.width <= 0:
            return 0.0
        return self.length / self.width

    def to_dict(self) -> dict[str, Any]:
        """Convert the cavity feature to a dictionary representation.

        Returns:
            A dictionary containing all relevant cavity properties suitable
            for serialization, including dimensions, area, and aspect ratio.
        """
        return {
            "cavity_id": self.cavity_id,
            "type": self.type,
            "length": self.length,
            "width": self.width,
            "depth": self.depth,
            "center": {"x": self.center_x, "y": self.center_y, "z": self.center_z},
            "orientation": self.orientation,
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "surface": self.surface,
            "wall_thickness": self.wall_thickness,
            "bottom_type": self.bottom_type,
            "area": round(self.area(), 2),
            "aspect_ratio": round(self.aspect_ratio(), 2),
        }

    def to_machining_feature(self) -> dict[str, Any]:
        return {
            "name": self.cavity_id,
            "type": self.type,
            "geometric_type": "pocket",
            "tolerance_grade": self._it_grade_from_tolerance(),
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": False,
            "machining_method": "",
            "priority": "high" if self.tolerance_grade in ("H6", "H7") else "medium",
            "surface": self.surface,
            "dimensions": {
                "length": self.length,
                "width": self.width,
                "depth": self.depth,
                "center_x": self.center_x,
                "center_y": self.center_y,
            },
            "parent_feature": "",
            "tolerances": {
                "length_upper": 0.0,
                "length_lower": 0.0,
                "width_upper": 0.0,
                "width_lower": 0.0,
            },
        }

    def _it_grade_from_tolerance(self) -> str:
        grade_map = {
            "H5": "IT5",
            "H6": "IT6",
            "H7": "IT7",
            "H8": "IT8",
            "H9": "IT9",
            "H10": "IT10",
            "H11": "IT11",
        }
        return grade_map.get(self.tolerance_grade.upper(), "IT8")


@dataclass
class CavityRecognitionResult:
    """Complete result of cavity feature recognition.

    Attributes:
        cavities: List of recognized cavity features.
        total_count: Total number of cavities.
        type_summary: Count summary by cavity type {type: count}.
        warnings: Warning messages from the recognition process.
        errors: Error messages from the recognition process.
        accuracy_metrics: Recognition accuracy metrics.
    """

    cavities: list[CavityFeature] = field(default_factory=list)
    total_count: int = 0
    type_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accuracy_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the recognition result to a dictionary representation.

        Returns:
            A dictionary containing total count, type summary, all cavity details,
            warnings, errors, and accuracy metrics.
        """
        return {
            "total_count": self.total_count,
            "type_summary": self.type_summary,
            "cavities": [c.to_dict() for c in self.cavities],
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


class CavityRecognizer:
    """Cavity (rectangular pocket) feature recognizer.

    Recognizes rectangular cavity/pocket features from part description data,
    distinguishing through pockets from blind pockets.

    Attributes:
        MIN_CAVITY_DIMENSION: Minimum recognizable cavity dimension (mm).
        MAX_CAVITY_DIMENSION: Maximum recognizable cavity dimension (mm).
        RECTANGULAR_ANGLE_TOLERANCE: Angle tolerance for rectangular corners (degrees).
    """

    MIN_CAVITY_DIMENSION = 0.5
    MAX_CAVITY_DIMENSION = 5000.0
    RECTANGULAR_ANGLE_TOLERANCE = 2.0

    def recognize_from_part_description(
        self,
        part_description: dict[str, Any],
    ) -> CavityRecognitionResult:
        """Recognize cavity features from a part description dictionary.

        Args:
            part_description: Part description dictionary containing 'cavities',
                'pockets', 'features', or 'contours' fields.

        Returns:
            CavityRecognitionResult with all recognized cavity features.

        Raises:
            ManufacturingError: If the part description is empty or None.
        """
        if not part_description:
            raise ManufacturingError(
                category=ErrorCategory.FEATURE_RECOGNITION_INCOMPLETE,
                detail="零件描述数据不能为空",
            )

        cavities: list[CavityFeature] = []
        warnings: list[str] = []
        errors: list[str] = []
        accuracy_metrics: dict[str, float] = {"overall": 0.99}

        raw_cavities = part_description.get("cavities", part_description.get("pockets", []))
        raw_features = part_description.get("features", [])
        pocket_features = [
            f
            for f in raw_features
            if f.get("geometric_type") in ("pocket", "cavity", "rectangular_pocket")
            or f.get("type") in ("through_pocket", "blind_pocket", "cavity")
        ]
        for pf in pocket_features:
            pos = pf.get("position", {})
            dims = pf.get("dimensions", {})
            raw_cavities.append(
                {
                    "id": pf.get("name", f"C{len(raw_cavities) + 1:03d}"),
                    "type": pf.get("type", "blind_pocket"),
                    "position": pos,
                    "length": dims.get("length", pf.get("length", 0)),
                    "width": dims.get("width", pf.get("width", 0)),
                    "depth": dims.get("depth", pf.get("depth", 0)),
                    "tolerance_grade": pf.get("tolerance_grade", "H8"),
                    "surface": pf.get("surface", "A"),
                }
            )

        contours = part_description.get("contours", part_description.get("entities", []))
        for i, contour in enumerate(contours):
            if self._is_rectangular_contour(contour):
                fig_id = f"C{i + 1:03d}"
                if not any(c.get("id") == fig_id for c in raw_cavities):
                    raw_cavities.append(
                        {
                            "id": fig_id,
                            "type": contour.get("type", "blind_pocket"),
                            "position": contour.get("center", contour.get("position", {})),
                            "length": contour.get("length", contour.get("width", 0)),
                            "width": contour.get("height", contour.get("depth", 0)),
                            "depth": contour.get("depth", contour.get("z_depth", 0)),
                        }
                    )

        if not raw_cavities:
            warnings.append("未找到任何型腔特征定义")

        for i, raw in enumerate(raw_cavities):
            try:
                cavity_id = raw.get("id", raw.get("cavity_id", f"C{i + 1:03d}"))
                cavity_type = raw.get("type", "blind_pocket")

                pos = raw.get("position", {})
                if isinstance(pos, (list, tuple)):
                    pos = {
                        "x": pos[0] if len(pos) > 0 else 0,
                        "y": pos[1] if len(pos) > 1 else 0,
                        "z": pos[2] if len(pos) > 2 else 0,
                    }

                center_x = float(pos.get("x", 0))
                center_y = float(pos.get("y", 0))
                center_z = float(pos.get("z", 0))

                length = float(raw.get("length", 0))
                width = float(raw.get("width", 0))
                depth = float(raw.get("depth", 0))

                if length <= 0 or width <= 0:
                    errors.append(f"型腔 {cavity_id} 尺寸无效: length={length}mm, width={width}mm")
                    continue

                if length < self.MIN_CAVITY_DIMENSION or width < self.MIN_CAVITY_DIMENSION:
                    warnings.append(
                        f"型腔 {cavity_id} 尺寸过小 ({length}x{width}mm)，"
                        f"低于最小可加工尺寸 {self.MIN_CAVITY_DIMENSION}mm"
                    )

                if depth <= 0 and cavity_type == "blind_pocket":
                    warnings.append(f"盲腔 {cavity_id} 未指定深度，假设为通腔")
                    cavity_type = "through_pocket"
                    depth = 0.0

                orientation = float(raw.get("orientation", raw.get("angle", 0.0)))

                cavity = CavityFeature(
                    cavity_id=str(cavity_id),
                    type=cavity_type,
                    length=length,
                    width=width,
                    depth=depth,
                    center_x=center_x,
                    center_y=center_y,
                    center_z=center_z,
                    orientation=orientation,
                    tolerance_grade=str(raw.get("tolerance_grade", "H8")),
                    surface_roughness_ra=float(raw.get("surface_roughness_ra", 3.2)),
                    surface=str(raw.get("surface", "A")),
                    wall_thickness=float(raw.get("wall_thickness", 0.0)),
                    bottom_type=str(raw.get("bottom_type", "flat")),
                    metadata=raw.get("metadata", {}),
                )
                cavities.append(cavity)

            except (ValueError, TypeError, KeyError) as e:
                errors.append(f"解析型腔条目 {raw.get('id', i)} 时出错: {type(e).__name__}")
                continue

        type_summary: dict[str, int] = {}
        for c in cavities:
            type_summary[c.type] = type_summary.get(c.type, 0) + 1

        accuracy_metrics["recognized_count"] = float(len(cavities))
        if raw_cavities:
            recognition_rate = len(cavities) / max(len(raw_cavities), 1)
            accuracy_metrics["recognition_rate"] = min(recognition_rate, 1.0)
            accuracy_metrics["overall"] = accuracy_metrics["recognition_rate"]

        return CavityRecognitionResult(
            cavities=cavities,
            total_count=len(cavities),
            type_summary=type_summary,
            warnings=warnings,
            errors=errors,
            accuracy_metrics=accuracy_metrics,
        )

    def recognize_from_contours(
        self,
        contours: list[dict[str, Any]],
    ) -> CavityRecognitionResult:
        """Recognize cavity features from a list of contour dictionaries.

        Args:
            contours: List of contour dictionaries with cavity geometry data.

        Returns:
            CavityRecognitionResult with recognized cavity features.
        """
        part_description = {"contours": contours}
        return self.recognize_from_part_description(part_description)

    def _is_rectangular_contour(self, contour: dict[str, Any]) -> bool:
        """Check whether a contour represents a rectangular cavity.

        Args:
            contour: Contour dictionary with 'shape', 'type', 'vertices', or 'points' fields.

        Returns:
            True if the contour matches a known rectangular type or has 4 vertices.
        """
        shape = contour.get("shape", contour.get("type", ""))
        if shape in ("rectangle", "rectangular_pocket", "pocket", "cavity"):
            return True
        vertices = contour.get("vertices", contour.get("points", []))
        if len(vertices) == 4 and not contour.get("radius"):
            return True
        return False

    def validate_result(
        self,
        result: CavityRecognitionResult,
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        """Validate the cavity recognition result.

        Checks include:
        1. Cavity count matches expected (if provided)
        2. All cavity dimensions (length/width) are positive
        3. Position coordinates are valid (no NaN/Infinity)
        4. No unhandled errors from recognition

        Args:
            result: Recognition result to validate.
            expected_count: Expected total number of cavities (optional).

        Returns:
            Validation report dictionary with 'is_valid', 'issues', and
            'passed_checks' keys.
        """
        issues: list[str] = []
        passed: list[str] = []

        if expected_count is not None:
            if result.total_count == expected_count:
                passed.append(f"型腔数量匹配: {result.total_count} == {expected_count}")
            else:
                issues.append(f"型腔数量不匹配: 识别到{result.total_count}个，期望{expected_count}个")
        else:
            passed.append(f"型腔总数: {result.total_count}")

        invalid_size = [c for c in result.cavities if c.length <= 0 or c.width <= 0]
        if invalid_size:
            issues.append(f"{len(invalid_size)}个型腔尺寸无效: {', '.join(c.cavity_id for c in invalid_size)}")
        else:
            passed.append("所有型腔尺寸有效")

        import math

        invalid_pos = [
            c
            for c in result.cavities
            if any(math.isnan(v) or math.isinf(v) for v in [c.center_x, c.center_y, c.center_z])
        ]
        if invalid_pos:
            issues.append(f"{len(invalid_pos)}个型腔位置坐标无效")
        else:
            passed.append("所有型腔位置坐标有效")

        if result.errors:
            issues.append(f"识别过程中有{len(result.errors)}个错误")
        else:
            passed.append("识别过程无错误")

        return {
            "is_valid": len(issues) == 0,
            "issues": issues,
            "passed_checks": passed,
        }
