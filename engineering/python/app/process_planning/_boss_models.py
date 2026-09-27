"""Boss 特征数据类（从 boss_recognizer 拆出）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BossStep:
    """A single step/layer in a stepped boss feature.

    Attributes:
        step_index: Step number (1-based).
        diameter: Step diameter (mm).
        height: Step height (mm).
        position_z: Z position of the step (mm).
        tolerance_grade: Tolerance grade, e.g. 'H8'.
    """

    step_index: int
    diameter: float
    height: float
    position_z: float = 0.0
    tolerance_grade: str = "H8"


@dataclass
class BossFeature:
    """A single boss feature with complete geometric information.

    Attributes:
        boss_id: Unique identifier for the boss, e.g. 'B001'.
        type: Boss type - circular_boss/rectangular_boss/stepped_boss.
        diameter: Boss diameter (mm), for circular and stepped types.
        side_length: Side length (mm), for rectangular type.
        height: Boss height (mm).
        center_x: Center X coordinate (mm) in world coordinates.
        center_y: Center Y coordinate (mm) in world coordinates.
        center_z: Center Z coordinate (mm) in world coordinates.
        tolerance_grade: Tolerance grade, e.g. 'H8'.
        surface_roughness_ra: Surface roughness Ra value (um).
        surface: Machining surface identifier 'A'/'B'/'C' etc.
        steps: Step list for stepped bosses.
        metadata: Additional metadata dictionary.
    """

    boss_id: str
    type: str
    diameter: float = 0.0
    side_length: float = 0.0
    height: float = 0.0
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    tolerance_grade: str = "H8"
    surface_roughness_ra: float = 3.2
    surface: str = "A"
    steps: list[BossStep] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_circular(self) -> bool:
        """Check whether the boss is circular.

        Returns:
            True if the boss type is 'circular_boss'.
        """
        return self.type == "circular_boss"

    def is_rectangular(self) -> bool:
        """Check whether the boss is rectangular.

        Returns:
            True if the boss type is 'rectangular_boss'.
        """
        return self.type == "rectangular_boss"

    def is_stepped(self) -> bool:
        """Check whether the boss is a stepped boss.

        Returns:
            True if the boss type is 'stepped_boss'.
        """
        return self.type == "stepped_boss"

    def effective_diameter(self) -> float:
        """Get the effective diameter of the boss.

        For circular bosses, returns the diameter directly.
        For stepped bosses, returns the diameter of the first step.
        For other types, returns the diameter attribute.

        Returns:
            Effective diameter in mm.
        """
        if self.is_circular():
            return self.diameter
        if self.is_stepped() and self.steps:
            return self.steps[0].diameter
        return self.diameter

    def to_dict(self) -> dict[str, Any]:
        """Convert the boss feature to a dictionary representation.

        Returns:
            A dictionary containing all relevant boss properties suitable
            for serialization, including dimensions, steps, and tolerances.
        """
        return {
            "boss_id": self.boss_id,
            "type": self.type,
            "diameter": self.diameter,
            "side_length": self.side_length,
            "height": self.height,
            "center": {"x": self.center_x, "y": self.center_y, "z": self.center_z},
            "tolerance_grade": self.tolerance_grade,
            "surface_roughness_ra": self.surface_roughness_ra,
            "surface": self.surface,
            "steps": [
                {
                    "step_index": s.step_index,
                    "diameter": s.diameter,
                    "height": s.height,
                    "position_z": s.position_z,
                    "tolerance_grade": s.tolerance_grade,
                }
                for s in self.steps
            ],
        }

    def to_machining_feature(self) -> dict[str, Any]:
        return {
            "name": self.boss_id,
            "type": self.type,
            "geometric_type": self.type,
            "tolerance_grade": self._it_grade_from_tolerance(),
            "surface_roughness_ra": self.surface_roughness_ra,
            "is_datum_candidate": self.type == "circular_boss" and self.tolerance_grade in ("H6", "H7"),
            "machining_method": "",
            "priority": "high" if self.tolerance_grade in ("H6", "H7") else "medium",
            "surface": self.surface,
            "dimensions": {
                "diameter": self.diameter,
                "side_length": self.side_length,
                "height": self.height,
                "center_x": self.center_x,
                "center_y": self.center_y,
            },
            "parent_feature": "",
            "tolerances": {
                "diameter_upper": 0.0,
                "diameter_lower": 0.0,
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
class BossRecognitionResult:
    """Complete result of boss feature recognition.

    Attributes:
        bosses: List of recognized boss features.
        total_count: Total number of bosses.
        type_summary: Count summary by boss type {type: count}.
        warnings: Warning messages from the recognition process.
        errors: Error messages from the recognition process.
        accuracy_metrics: Recognition accuracy metrics.
    """

    bosses: list[BossFeature] = field(default_factory=list)
    total_count: int = 0
    type_summary: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    accuracy_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert the recognition result to a dictionary representation.

        Returns:
            A dictionary containing total count, type summary, all boss details,
            warnings, errors, and accuracy metrics.
        """
        return {
            "total_count": self.total_count,
            "type_summary": self.type_summary,
            "bosses": [b.to_dict() for b in self.bosses],
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
