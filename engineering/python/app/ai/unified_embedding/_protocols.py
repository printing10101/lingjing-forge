"""三层架构协议 DTO（从 interfaces 拆出）。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app.ai.unified_embedding._enums import (
    FeatureExtractionAlgorithm,
    QualityLevel,
    SensorType,
)
from app.ai.unified_embedding._models import (
    AnomalyEvent,
    CuttingParameters,
    DimensionalTolerance,
    FeedbackSignal,
    GeometryInput,
    QualityRequirements,
    RealTimeState,
    SensorConfig,
    SurfaceRoughnessSpec,
)


class CognitiveToPerceptionRequest:
    """Cognitive layer → Perception layer interface.

    Protocol: RESTful API over HTTPS
    Response time: <100ms
    """

    def __init__(
        self,
        process_intent: str,
        quality_requirements: QualityRequirements,
        material_spec: dict[str, Any] | None = None,
        request_id: str | None = None,
        max_tokens: int = 512,
    ):
        self.request_id = request_id or uuid.uuid4().hex[:16]
        self.process_intent = process_intent
        self.quality_requirements = quality_requirements
        self.material_spec = material_spec or {}
        self.max_tokens = max_tokens
        self.timestamp = time.time()

    def validate(self) -> list[str]:
        errors = []
        if not self.process_intent or len(self.process_intent) > 512:
            errors.append(
                f"process_intent must be 1-512 characters, got {len(self.process_intent) if self.process_intent else 0}"
            )
        errors.extend(self.quality_requirements.validate())
        return errors

    def to_json(self) -> str:
        return json.dumps(
            {
                "request_id": self.request_id,
                "process_intent": self.process_intent,
                "quality_requirements": {
                    "dimensional_tolerances": [asdict(t) for t in self.quality_requirements.dimensional_tolerances],
                    "surface_roughness": asdict(self.quality_requirements.surface_roughness)
                    if self.quality_requirements.surface_roughness
                    else None,
                    "geometric_tolerances_mm": self.quality_requirements.geometric_tolerances_mm,
                    "max_burr_height_mm": self.quality_requirements.max_burr_height_mm,
                    "target_quality_level": self.quality_requirements.target_quality_level.value,
                },
                "material_spec": self.material_spec,
                "max_tokens": self.max_tokens,
                "timestamp": self.timestamp,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, data: str) -> "CognitiveToPerceptionRequest":
        d = json.loads(data)
        qr_data = d["quality_requirements"]
        tols = [DimensionalTolerance(**t) for t in qr_data.get("dimensional_tolerances", [])]
        sr = None
        if qr_data.get("surface_roughness"):
            sr = SurfaceRoughnessSpec(**qr_data["surface_roughness"])
        qr = QualityRequirements(
            dimensional_tolerances=tols,
            surface_roughness=sr,
            geometric_tolerances_mm=qr_data.get("geometric_tolerances_mm", {}),
            max_burr_height_mm=qr_data.get("max_burr_height_mm", 0.1),
            target_quality_level=QualityLevel(qr_data.get("target_quality_level", "IT8")),
        )
        return cls(
            process_intent=d["process_intent"],
            quality_requirements=qr,
            material_spec=d.get("material_spec", {}),
            request_id=d.get("request_id"),
        )


@dataclass
class CognitiveToPerceptionResponse:
    """Perception layer → Cognitive layer (response to perception task config)."""

    request_id: str
    sensor_configs: list[SensorConfig] = field(default_factory=list)
    feature_algorithm: FeatureExtractionAlgorithm = FeatureExtractionAlgorithm.RESNET50
    sampling_frequency_hz: float = 100.0
    region_of_interest: dict[str, Any] | None = None
    preprocessing_pipeline: list[str] = field(default_factory=list)
    estimated_processing_time_ms: float = 0.0
    embedding_projection: list[float] | None = None

    def validate(self) -> list[str]:
        errors = []
        for i, sc in enumerate(self.sensor_configs):
            for e in sc.validate():
                errors.append(f"sensor_configs[{i}]: {e}")
        if self.sampling_frequency_hz <= 0 or self.sampling_frequency_hz > 100000:
            errors.append(f"sampling_frequency_hz must be in (0, 100000], got {self.sampling_frequency_hz}")
        return errors

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, data: str) -> "CognitiveToPerceptionResponse":
        d = json.loads(data)
        d["sensor_configs"] = [SensorConfig(**s) for s in d.get("sensor_configs", [])]
        d["feature_algorithm"] = FeatureExtractionAlgorithm(d.get("feature_algorithm", "resnet50"))
        return cls(**d)


@dataclass
class PerceptionToExecutionRequest:
    """Perception layer → Execution layer interface.

    Protocol: gRPC with binary stream
    Response time: <50ms
    """

    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    geometry: GeometryInput = field(default_factory=GeometryInput)
    cutting_parameters: CuttingParameters = field(
        default_factory=lambda: CuttingParameters(feed_rate_mm_min=500.0, depth_of_cut_mm=2.0, spindle_speed_rpm=8000.0)
    )
    material_spec: dict[str, Any] = field(default_factory=dict)
    tool_spec: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def validate(self) -> list[str]:
        errors = []
        errors.extend(self.geometry.validate())
        errors.extend(self.cutting_parameters.validate())
        return errors

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.geometry.point_cloud is not None:
            d["geometry"]["point_cloud"]["points"] = self.geometry.point_cloud.points.tolist()
        return d


@dataclass
class MonitoringPointConfig:
    sensor_type: SensorType
    installation_position: str
    sampling_frequency_hz: float
    data_format: str = "float32"
    channel_count: int = 1

    def validate(self) -> list[str]:
        errors = []
        if self.installation_position not in {"spindle", "tool_holder", "worktable", "bed", "column", "coolant_line"}:
            errors.append(f"Invalid installation_position: {self.installation_position}")
        if self.sampling_frequency_hz <= 0 or self.sampling_frequency_hz > 100000:
            errors.append(f"sampling_frequency_hz must be in (0, 100000], got {self.sampling_frequency_hz}")
        return errors


@dataclass
class PredictionBaseline:
    expected_surface_roughness_ra: float
    expected_tool_wear_rate_um_per_min: float
    expected_cutting_force_n: float
    expected_power_consumption_kw: float
    tolerance_range: dict[str, tuple[float, float]] = field(default_factory=dict)
    control_thresholds: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0

    def validate(self) -> list[str]:
        errors = []
        if self.expected_surface_roughness_ra < 0:
            errors.append(f"expected_surface_roughness_ra must be >= 0, got {self.expected_surface_roughness_ra}")
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"confidence must be in [0.0, 1.0], got {self.confidence}")
        return errors


@dataclass
class PerceptionToExecutionResponse:
    """Execution layer → Perception layer (monitoring config + prediction baseline)."""

    request_id: str
    monitoring_points: list[MonitoringPointConfig] = field(default_factory=list)
    prediction_baseline: PredictionBaseline | None = None
    toolpath_segments: int = 0
    estimated_cycle_time_s: float = 0.0
    embedding_projection: list[float] | None = None

    def validate(self) -> list[str]:
        errors = []
        for i, mp in enumerate(self.monitoring_points):
            for e in mp.validate():
                errors.append(f"monitoring_points[{i}]: {e}")
        if self.prediction_baseline:
            errors.extend(self.prediction_baseline.validate())
        return errors


@dataclass
class ExecutionToCognitiveRequest:
    """Execution layer → Cognitive layer interface.

    Protocol: WebSocket, continuous data stream
    Update frequency: >=10Hz
    """

    stream_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    real_time_state: RealTimeState = field(default_factory=RealTimeState)
    anomaly_events: list[AnomalyEvent] = field(default_factory=list)
    session_id: str | None = None
    batch_sequence: int = 0
    timestamp: float = field(default_factory=time.time)

    def validate(self) -> list[str]:
        errors = []
        errors.extend(self.real_time_state.validate())
        for i, evt in enumerate(self.anomaly_events):
            for e in evt.validate():
                errors.append(f"anomaly_events[{i}]: {e}")
        return errors


@dataclass
class ExecutionToCognitiveResponse:
    """Cognitive layer → Execution layer (feedback for plan adjustment)."""

    stream_id: str
    feedback: FeedbackSignal = field(default_factory=FeedbackSignal)
    timestamp: float = field(default_factory=time.time)

    def validate(self) -> list[str]:
        return self.feedback.validate()
