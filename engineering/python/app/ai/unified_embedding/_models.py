"""三层架构数据模型（从 interfaces 拆出）。"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.ai.unified_embedding._enums import (
    AdjustmentPriority,
    EventSeverity,
    EventType,
    QualityLevel,
    SensorType,
    SurfaceFinishGrade,
)


@dataclass
class DimensionalTolerance:
    nominal_mm: float
    upper_deviation_mm: float
    lower_deviation_mm: float
    it_grade: QualityLevel = QualityLevel.IT8

    def validate(self) -> list[str]:
        errors = []
        if self.nominal_mm <= 0:
            errors.append(f"nominal_mm must be positive, got {self.nominal_mm}")
        if self.upper_deviation_mm < self.lower_deviation_mm:
            errors.append(
                f"upper_deviation ({self.upper_deviation_mm}) must be >= lower_deviation ({self.lower_deviation_mm})"
            )
        return errors


@dataclass
class SurfaceRoughnessSpec:
    ra_um: float
    rz_um: float | None = None
    rmax_um: float | None = None
    grade: SurfaceFinishGrade = SurfaceFinishGrade.N6

    def validate(self) -> list[str]:
        errors = []
        if self.ra_um < 0.01 or self.ra_um > 50.0:
            errors.append(f"ra_um must be in [0.01, 50.0], got {self.ra_um}")
        if self.rz_um is not None and (self.rz_um < 0.04 or self.rz_um > 200.0):
            errors.append(f"rz_um must be in [0.04, 200.0], got {self.rz_um}")
        return errors


@dataclass
class QualityRequirements:
    dimensional_tolerances: list[DimensionalTolerance] = field(default_factory=list)
    surface_roughness: SurfaceRoughnessSpec | None = None
    geometric_tolerances_mm: dict[str, float] = field(default_factory=dict)
    max_burr_height_mm: float = 0.1
    target_quality_level: QualityLevel = QualityLevel.IT8

    def validate(self) -> list[str]:
        errors = []
        for i, tol in enumerate(self.dimensional_tolerances):
            for e in tol.validate():
                errors.append(f"dimensional_tolerances[{i}]: {e}")
        if self.surface_roughness:
            errors.extend(self.surface_roughness.validate())
        for key, val in self.geometric_tolerances_mm.items():
            if val <= 0:
                errors.append(f"geometric_tolerance '{key}' must be positive, got {val}")
        if not (0.0 <= self.max_burr_height_mm <= 5.0):
            errors.append(f"max_burr_height_mm must be in [0, 5.0], got {self.max_burr_height_mm}")
        return errors


@dataclass
class SensorConfig:
    sensor_type: SensorType
    installation_position: str
    sampling_frequency_hz: float
    measurement_range: tuple[float, float]
    resolution: float
    axis: str = "Z"

    def validate(self) -> list[str]:
        errors = []
        valid_positions = {"spindle", "tool_holder", "worktable", "bed", "column", "coolant_line", "enclosure"}
        if self.installation_position not in valid_positions:
            errors.append(f"installation_position must be one of {valid_positions}, got '{self.installation_position}'")
        if self.sampling_frequency_hz <= 0 or self.sampling_frequency_hz > 100000:
            errors.append(f"sampling_frequency_hz must be in (0, 100000], got {self.sampling_frequency_hz}")
        if self.measurement_range[0] >= self.measurement_range[1]:
            errors.append(f"measurement_range must have min < max, got {self.measurement_range}")
        return errors


@dataclass
class CuttingParameters:
    feed_rate_mm_min: float
    depth_of_cut_mm: float
    spindle_speed_rpm: float
    step_over_mm: float = 0.0
    cutting_speed_m_min: float = 0.0
    coolant_on: bool = True
    approach_distance_mm: float = 5.0
    retract_distance_mm: float = 5.0

    def validate(self) -> list[str]:
        errors = []
        if not (0.1 <= self.feed_rate_mm_min <= 10000.0):
            errors.append(f"feed_rate_mm_min must be in [0.1, 10000.0], got {self.feed_rate_mm_min}")
        if not (0.01 <= self.depth_of_cut_mm <= 100.0):
            errors.append(f"depth_of_cut_mm must be in [0.01, 100.0], got {self.depth_of_cut_mm}")
        if not (100 <= self.spindle_speed_rpm <= 100000):
            errors.append(f"spindle_speed_rpm must be in [100, 100000], got {self.spindle_speed_rpm}")
        if not (0.0 <= self.step_over_mm <= 100.0):
            errors.append(f"step_over_mm must be in [0.0, 100.0], got {self.step_over_mm}")
        return errors


@dataclass
class PointCloudData:
    points: np.ndarray
    normals: np.ndarray | None = None
    colors: np.ndarray | None = None
    point_count: int = 0
    bounding_box: dict[str, float] | None = None
    source_format: str = "numpy"

    def __post_init__(self):
        if isinstance(self.points, list):
            self.points = np.array(self.points, dtype=np.float32)
        self.point_count = len(self.points)

    def serialize(self) -> bytes:
        return self.points.astype(np.float32).tobytes()

    @classmethod
    def deserialize(cls, data: bytes, point_count: int) -> "PointCloudData":
        points = np.frombuffer(data, dtype=np.float32).reshape(point_count, 3)
        return cls(points=points, point_count=point_count)


@dataclass
class GeometryInput:
    point_cloud: PointCloudData | None = None
    stl_path: str | None = None
    step_path: str | None = None
    parametric_model: dict[str, Any] | None = None
    stock_dimensions_mm: tuple[float, float, float] | None = None
    material: str = "steel"

    def validate(self) -> list[str]:
        errors = []
        if (
            self.point_cloud is None
            and self.stl_path is None
            and self.step_path is None
            and self.parametric_model is None
        ):
            errors.append("At least one of point_cloud, stl_path, step_path, or parametric_model must be provided")
        if self.stl_path is not None and not self.stl_path.endswith(".stl"):
            errors.append(f"stl_path must end with .stl, got '{self.stl_path}'")
        if self.step_path is not None and not self.step_path.endswith((".step", ".stp")):
            errors.append(f"step_path must end with .step/.stp, got '{self.step_path}'")
        return errors


@dataclass
class RealTimeState:
    timestamp: float = field(default_factory=time.time)
    spindle_speed_rpm: float = 0.0
    spindle_load_pct: float = 0.0
    feed_rate_mm_min: float = 0.0
    vibration_x: float = 0.0
    vibration_y: float = 0.0
    vibration_z: float = 0.0
    spindle_temp_c: float = 25.0
    tool_temp_c: float = 25.0
    coolant_temp_c: float = 20.0
    cutting_force_x_n: float = 0.0
    cutting_force_y_n: float = 0.0
    cutting_force_z_n: float = 0.0
    tool_wear_mm: float = 0.0
    acoustic_emission_rms: float = 0.0
    position_x_mm: float = 0.0
    position_y_mm: float = 0.0
    position_z_mm: float = 0.0
    duty_cycle_pct: float = 0.0

    def validate(self) -> list[str]:
        errors = []
        if self.spindle_speed_rpm < 0:
            errors.append(f"spindle_speed_rpm must be >= 0, got {self.spindle_speed_rpm}")
        if not (0 <= self.spindle_load_pct <= 150):
            errors.append(f"spindle_load_pct must be in [0, 150], got {self.spindle_load_pct}")
        if not (0 <= self.tool_wear_mm <= 5.0):
            errors.append(f"tool_wear_mm must be in [0, 5.0], got {self.tool_wear_mm}")
        return errors

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    def to_numpy(self) -> np.ndarray:
        return np.array(
            [
                self.spindle_speed_rpm,
                self.spindle_load_pct,
                self.feed_rate_mm_min,
                self.vibration_x,
                self.vibration_y,
                self.vibration_z,
                self.spindle_temp_c,
                self.tool_temp_c,
                self.coolant_temp_c,
                self.cutting_force_x_n,
                self.cutting_force_y_n,
                self.cutting_force_z_n,
                self.tool_wear_mm,
                self.acoustic_emission_rms,
                self.position_x_mm,
                self.position_y_mm,
                self.position_z_mm,
                self.duty_cycle_pct,
            ],
            dtype=np.float32,
        )


@dataclass
class AnomalyEvent:
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    event_type: EventType = EventType.VIBRATION_ANOMALY
    severity: EventSeverity = EventSeverity.WARNING
    timestamp: float = field(default_factory=time.time)
    description: str = ""
    source_sensor: SensorType | None = None
    measured_value: float = 0.0
    threshold_value: float = 0.0
    duration_ms: float = 0.0
    related_positions: list[tuple[float, float, float]] | None = None

    def validate(self) -> list[str]:
        errors = []
        if self.duration_ms < 0:
            errors.append(f"duration_ms must be >= 0, got {self.duration_ms}")
        return errors


@dataclass
class AdjustmentSuggestion:
    suggestion_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    description: str = ""
    suggested_parameters: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    priority: AdjustmentPriority = AdjustmentPriority.MEDIUM
    reasoning: str = ""
    expected_improvement: dict[str, float] = field(default_factory=dict)
    alternatives: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors = []
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        return errors


@dataclass
class FeedbackSignal:
    suggestions: list[AdjustmentSuggestion] = field(default_factory=list)
    overall_confidence: float = 0.0
    execution_priority: AdjustmentPriority = AdjustmentPriority.MEDIUM
    estimated_impact: str = ""
    requires_halt: bool = False

    def validate(self) -> list[str]:
        errors = []
        for i, s in enumerate(self.suggestions):
            for e in s.validate():
                errors.append(f"suggestions[{i}]: {e}")
        if not (0.0 <= self.overall_confidence <= 1.0):
            errors.append(f"overall_confidence must be in [0.0, 1.0], got {self.overall_confidence}")
        return errors
