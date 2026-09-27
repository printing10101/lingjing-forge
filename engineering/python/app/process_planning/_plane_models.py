"""Plane 特征数据类（从 plane_recognizer 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlaneFeature:
    """A single plane feature with complete geometric information.

    Attributes:
        plane_id: Unique identifier for the plane, e.g. 'P001'.
        type: Plane type - top_plane/bottom_plane/side_plane.
        area: Plane area (mm²).
        normal_x: X component of the normal vector.
        normal_y: Y component of the normal vector.
        normal_z: Z component of the normal vector.
        length: Plane length (mm).
        width: Plane width (mm).
        center_x: Center X coordinate (mm).
        center_y: Center Y coordinate (mm).
        center_z: Center Z coordinate (mm).
        boundary: List of boundary points [[x, y], ...].
        surface: Machining surface identifier.
        tolerance_grade: Tolerance grade, e.g. 'IT8'.
        surface_roughness_ra: Surface roughness Ra value (um).
        is_datum_candidate: Whether this plane can serve as a datum feature.
        metadata: Additional metadata dictionary.
    """

    plane_id: str
    type: str
    area: float = 0.0
    normal_x: float = 0.0
    normal_y: float = 0.0
    normal_z: float = 1.0
    length: float = 0.0
    width: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    boundary: list[list[float]] = field(default_factory=list)
    surface: str = "A"
    tolerance_grade: str = "IT8"
    surface_roughness_ra: float = 3.2
    is_datum_candidate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_top(self) -> bool:
        """Check whether the plane is a top surface.

        Returns:
            True if the plane type is 'top_plane'.
        """
        return self.type == "top_plane"

    def is_bottom(self) -> bool:
        """Check whether the plane is a bottom surface.

        Returns:
            True if the plane type is 'bottom_plane'.
        """
        return self.type == "bottom_plane"

    def is_side(self) -> bool:
        """Check whether the plane is a side surface.

        Returns:
            True if the plane type is 'side_plane'.
        """
        return self.type == "side_plane"

    def normal_vector(self) -> list[float]:
        """Get the normal vector as a 3-component list.

        Returns:
            Normal vector [nx, ny, nz].
        """
        return [self.normal_x, self.normal_y, self.normal_z]

    def to_dict(self) -> dict[str, Any]:
        """Convert the plane feature to a dictionary representation.

        Returns:
            A dictionary containing all relevant plane properties suitable
            for serialization, including dimensions, normal vector, and tolerances.
        """
        return {
            "plane_id": self.plane_id,
            "type": self.type,
            "area": round(self.area, 2),
            "normal": self.normal_vector(),
            "length": self.length,
            "width": self.width,
            "center": {"x": self.center_x, "y": self.center_y, "z": self.center_z},
            "boundary_points": len(self.boundary),
            "surface": self.surface,
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": self.is_datum_candidate,
        }

    def to_machining_feature(self) -> dict[str, Any]:
        return {
            "name": self.plane_id,
            "type": self.type,
            "geometric_type": "plane",
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": self.is_datum_candidate,
            "machining_method": "milling",
            "priority": "high" if self.is_datum_candidate else "medium",
            "surface": self.surface,
            "dimensions": {
                "length": self.length,
                "width": self.width,
                "area": self.area,
                "center_x": self.center_x,
                "center_y": self.center_y,
            },
            "parent_feature": "",
            "tolerances": {
                "flatness": 0.0,
                "parallelism": 0.0,
            },
        }


@dataclass
class PlaneRecognitionResult:
    """Complete result of plane feature recognition.

    Attributes:
        planes: List of recognized plane features.
        total_count: Total number of planes.
        type_summary: Count summary by plane type {type: count}.
        warnings: Warning messages from the recognition process.
        errors: Error messages from the recognition process.
        accuracy_metrics: Recognition accuracy metrics.
    """

    planes: list[PlaneFeature] = field(default_factory=list)
    total_count: int = 0
    type_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accuracy_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the recognition result to a dictionary representation.

        Returns:
            A dictionary containing total count, type summary, all plane details,
            warnings, errors, and accuracy metrics.
        """
        return {
            "total_count": self.total_count,
            "type_summary": self.type_summary,
            "planes": [p.to_dict() for p in self.planes],
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
