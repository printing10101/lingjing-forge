"""Three-layer interface definitions for LLM+LNN+JEPA hybrid architecture.

Cognitive Layer (LLM-powered): process knowledge understanding, natural language interaction
Perception Layer (JEPA-powered): visual understanding, world model, sensor fusion
Execution Layer (LNN-powered): time-series prediction, real-time control

Interface protocols:
    Cognitive → Perception: RESTful API, HTTPS, <100ms
    Perception → Execution:   gRPC, binary stream, <50ms
    Execution → Cognitive:    WebSocket, continuous stream, >=10Hz

本模块为门面：实现已拆分至 _enums / _models / _protocols / _flow。
"""

from __future__ import annotations

from app.ai.unified_embedding._enums import (  # noqa: F401
    AdjustmentPriority,
    EventSeverity,
    EventType,
    FeatureExtractionAlgorithm,
    LayerType,
    ProcessCategory,
    QualityLevel,
    SensorType,
    SurfaceFinishGrade,
)
from app.ai.unified_embedding._flow import MachiningProcessFlow  # noqa: F401
from app.ai.unified_embedding._models import (  # noqa: F401
    AdjustmentSuggestion,
    AnomalyEvent,
    CuttingParameters,
    DimensionalTolerance,
    FeedbackSignal,
    GeometryInput,
    PointCloudData,
    QualityRequirements,
    RealTimeState,
    SensorConfig,
    SurfaceRoughnessSpec,
)
from app.ai.unified_embedding._protocols import (  # noqa: F401
    CognitiveToPerceptionRequest,
    CognitiveToPerceptionResponse,
    ExecutionToCognitiveRequest,
    ExecutionToCognitiveResponse,
    MonitoringPointConfig,
    PerceptionToExecutionRequest,
    PerceptionToExecutionResponse,
    PredictionBaseline,
)
