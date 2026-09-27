"""三层架构加工流程状态机（从 interfaces 拆出）。"""

from __future__ import annotations

import uuid
from typing import Any

from app.ai.unified_embedding._models import (
    AnomalyEvent,
    CuttingParameters,
    GeometryInput,
    QualityRequirements,
    RealTimeState,
)
from app.ai.unified_embedding._protocols import (
    CognitiveToPerceptionRequest,
    ExecutionToCognitiveRequest,
    PerceptionToExecutionRequest,
)


class MachiningProcessFlow:
    """Orchestrates the three-layer machining process flow with validation at each step."""

    def __init__(self):
        self.flow_id = uuid.uuid4().hex[:16]
        self.steps: list[dict[str, Any]] = []
        self.errors: list[str] = []

    def step_cognitive_to_perception(
        self,
        process_intent: str,
        quality_requirements: QualityRequirements,
        material_spec: dict[str, Any] | None = None,
    ) -> tuple[CognitiveToPerceptionRequest, list[str]]:
        req = CognitiveToPerceptionRequest(
            process_intent=process_intent,
            quality_requirements=quality_requirements,
            material_spec=material_spec,
        )
        errors = req.validate()
        self.steps.append(
            {
                "step": "cognitive_to_perception",
                "request_id": req.request_id,
                "timestamp": req.timestamp,
                "valid": len(errors) == 0,
            }
        )
        if errors:
            self.errors.extend(errors)
        return req, errors

    def step_perception_to_execution(
        self,
        geometry: GeometryInput,
        cutting_parameters: CuttingParameters,
        material_spec: dict[str, Any] | None = None,
        tool_spec: dict[str, Any] | None = None,
    ) -> tuple[PerceptionToExecutionRequest, list[str]]:
        req = PerceptionToExecutionRequest(
            geometry=geometry,
            cutting_parameters=cutting_parameters,
            material_spec=material_spec or {},
            tool_spec=tool_spec or {},
        )
        errors = req.validate()
        self.steps.append(
            {
                "step": "perception_to_execution",
                "request_id": req.request_id,
                "timestamp": req.timestamp,
                "valid": len(errors) == 0,
            }
        )
        if errors:
            self.errors.extend(errors)
        return req, errors

    def step_execution_to_cognitive(
        self,
        real_time_state: RealTimeState,
        anomaly_events: list[AnomalyEvent] | None = None,
    ) -> tuple[ExecutionToCognitiveRequest, list[str]]:
        req = ExecutionToCognitiveRequest(
            real_time_state=real_time_state,
            anomaly_events=anomaly_events or [],
        )
        errors = req.validate()
        self.steps.append(
            {
                "step": "execution_to_cognitive",
                "stream_id": req.stream_id,
                "timestamp": req.timestamp,
                "valid": len(errors) == 0,
            }
        )
        if errors:
            self.errors.extend(errors)
        return req, errors

    def get_flow_report(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "total_steps": len(self.steps),
            "valid_steps": sum(1 for s in self.steps if s["valid"]),
            "invalid_steps": sum(1 for s in self.steps if not s["valid"]),
            "error_count": len(self.errors),
            "errors": self.errors,
            "steps": self.steps,
        }
