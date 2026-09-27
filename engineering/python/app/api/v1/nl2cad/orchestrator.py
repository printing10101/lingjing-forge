"""NL to NC code full pipeline orchestrator."""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any
from dataclasses import dataclass, field
from enum import Enum

from app.api.v1.nl2cad.services import get_nl2cad_service
from app.process_planning.pipeline import ProcessPlanningPipeline
from app.process_planning.gcode_generator import GCodeGenerator
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


class PipelineStage(str, Enum):
    """Pipeline execution stages."""

    NL_TO_CAD = "nl_to_cad"
    CAD_TO_PROCESS = "cad_to_process"
    PROCESS_TO_NC = "process_to_nc"
    NC_TO_SIMULATION = "nc_to_simulation"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class PipelineState:
    """State of the pipeline execution."""

    stage: PipelineStage = PipelineStage.NL_TO_CAD
    cad_params: dict[str, Any] | None = None
    model_path: str | None = None
    process_plan: dict[str, Any] | None = None
    nc_code: str | None = None
    simulation_result: dict[str, Any] | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class NL2NCPipelineOrchestrator:
    """Orchestrates the full NL-to-NC pipeline.

    Pipeline stages:
    1. NL → CAD parameters → 3D model
    2. 3D model → Process planning
    3. Process plan → NC code generation
    4. NC code → Simulation verification
    """

    def __init__(self) -> None:
        self._nl2cad_service = get_nl2cad_service()
        self._process_planner = ProcessPlanningPipeline()
        self._gcode_generator = GCodeGenerator()

    async def execute_full_pipeline(
        self,
        description: str,
        machine_type: str = "cnc_mill",
        material: str | None = None,
    ) -> PipelineState:
        """Execute the full NL-to-NC pipeline.

        Args:
            description: Natural language part description
            machine_type: Target machine type
            material: Material specification

        Returns:
            PipelineState with all results
        """
        state = PipelineState()

        try:
            # Stage 1: NL CAD
            logger.info("Stage 1: NL to CAD conversion")
            state.stage = PipelineStage.NL_TO_CAD

            model_path, cad_params, _script, _parameters = await self._nl2cad_service.generate_model_from_nl(
                description=description,
                output_format="stl",
            )
            state.model_path = model_path
            state.cad_params = cad_params
            logger.info("Stage 1 completed: model at %s", model_path)

            # Stage 2: CAD Process planning
            logger.info("Stage 2: Process planning")
            state.stage = PipelineStage.CAD_TO_PROCESS

            process_plan = await self._generate_process_plan(
                cad_params=cad_params,
                material=material or cad_params.get("material", "steel"),
            )
            state.process_plan = process_plan
            logger.info("Stage 2 completed: %d operations", len(process_plan.get("operations", [])))

            # Stage 3: Process NC code
            logger.info("Stage 3: NC code generation")
            state.stage = PipelineStage.PROCESS_TO_NC

            nc_code = self._generate_nc_code(
                process_plan=process_plan,
                machine_type=machine_type,
            )
            state.nc_code = nc_code
            logger.info("Stage 3 completed: %d chars of G-code", len(nc_code))

            # Stage 4: NC Simulation（已集成真实 voxel 仿真模块，仿真精度取决于 voxel_size 参数）
            logger.info("Stage 4: Simulation verification")
            state.stage = PipelineStage.NC_TO_SIMULATION

            simulation_result = await self._run_simulation(nc_code=nc_code)
            state.simulation_result = simulation_result
            logger.info("Stage 4 completed")

            state.stage = PipelineStage.COMPLETED
            return state

        except Exception as e:
            logger.error("Pipeline failed at stage %s: %s", state.stage, e, exc_info=True)
            state.stage = PipelineStage.FAILED
            # 包装异常消息，避免直接回显内部错误细节
            safe = safe_error_message(e, context="nl2cad.execute_full_pipeline", fallback="流水线执行失败")
            state.error = safe["message"]
            state.metadata["error_id"] = safe["error_id"]
            return state

    async def _generate_process_plan(
        self,
        cad_params: dict[str, Any],
        material: str,
    ) -> dict[str, Any]:
        """Generate process plan from CAD parameters."""
        # Extract features from CAD params
        features = self._extract_features_from_cad(cad_params)

        # Build part description for pipeline
        part_description = {
            "material": material,
            "part_type": cad_params.get("shape_type", "general"),
            "features": features,
        }

        # Run process planning pipeline (synchronous)
        result = self._process_planner.run(part_description=part_description)

        if not result.success:
            raise RuntimeError(f"Process planning failed: {result.summary}")

        # Convert to dict for downstream consumption
        return result.to_dict()

    def _extract_features_from_cad(self, cad_params: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract machining features from CAD parameters."""
        features = []

        shape_type = cad_params.get("shape_type", "box")
        dimensions = cad_params.get("dimensions", {})

        # Add base shape as a feature
        features.append(
            {
                "type": "shape",
                "shape_type": shape_type,
                "dimensions": dimensions,
            }
        )

        # Add explicit features (holes, slots, chamfers, etc.)
        for feat in cad_params.get("features", []):
            features.append(feat)

        return features

    def _generate_nc_code(
        self,
        process_plan: dict[str, Any],
        machine_type: str,
    ) -> str:
        """Generate NC code from process plan."""
        # Extract operation plan from process plan result
        operation_plan_data = process_plan.get("operation_plan")
        if not operation_plan_data:
            raise ValueError("Process plan missing operation_plan")

        # Reconstruct OperationPlan object (simplified - in production, deserialize properly)
        from app.process_planning.operation_sequencer import OperationPlan, Operation

        operations = [Operation(**op_data) for op_data in operation_plan_data.get("operations", [])]
        operation_plan = OperationPlan(operations=operations)

        # Map machine type to controller type
        controller_map = {
            "cnc_mill": "fanuc_0i",
            "cnc_lathe": "fanuc_0i",
            "machining_center": "siemens_840d",
        }
        controller_type = controller_map.get(machine_type, "fanuc_0i")

        # Generate G-code
        result = self._gcode_generator.generate(
            operation_plan=operation_plan,
            controller_type=controller_type,
        )

        return result.program_text

    async def _run_simulation(self, nc_code: str) -> dict[str, Any]:
        """Run simulation to verify NC code.

        Integrates with the voxel-based simulation module to execute
        machining simulation and return results.
        """
        try:
            from app.simulation.api import _run_simulation as run_voxel_simulation
            from app.simulation.api import SimulationRequest

            # Create simulation request
            task_id = f"sim_{uuid.uuid4().hex[:12]}"
            request = SimulationRequest(
                project_id="nl2nc_pipeline",
                gcode=nc_code,
                voxel_size=1.0,
                tool_diameter=10.0,
                tool_length=50.0,
                tool_type="flat",
            )

            # Run simulation in thread pool
            import asyncio

            result = await asyncio.to_thread(run_voxel_simulation, task_id, request)

            return {
                "status": "completed",
                "task_id": task_id,
                "collision_detected": result.collision.collided,
                "collision_positions": result.collision.collision_positions,
                "collision_severity": result.collision.collision_severity,
                "voxel_count": result.voxel_count,
                "removed_voxel_count": result.removed_voxel_count,
                "duration_seconds": result.duration_seconds,
                "stock_stl_url": result.stock_stl_url,
            }
        except Exception as e:
            logger.error("Simulation failed: %s", e, exc_info=True)
            # 包装异常消息，避免直接回显内部错误细节
            safe = safe_error_message(e, context="nl2cad._run_simulation", fallback="仿真执行失败")
            return {
                "status": "failed",
                "error": safe["message"],
                "error_id": safe["error_id"],
                "nc_code_length": len(nc_code),
            }

    async def refine_and_regenerate(
        self,
        current_state: PipelineState,
        refinement_instruction: str,
    ) -> PipelineState:
        """Refine the model and regenerate downstream results.

        Args:
            current_state: Current pipeline state
            refinement_instruction: User's refinement instruction

        Returns:
            Updated pipeline state
        """
        if current_state.cad_params is None:
            raise ValueError("No CAD parameters to refine")

        state = PipelineState()

        try:
            # Refine CAD params
            logger.info("Refining model: %s", refinement_instruction[:100])
            model_path, refined_params = await self._nl2cad_service.refine_model(
                current_params=current_state.cad_params,
                instruction=refinement_instruction,
            )
            state.model_path = model_path
            state.cad_params = refined_params

            # Regenerate downstream
            state.stage = PipelineStage.CAD_TO_PROCESS
            process_plan = await self._generate_process_plan(
                cad_params=refined_params,
                material=refined_params.get("material", "steel"),
            )
            state.process_plan = process_plan

            state.stage = PipelineStage.PROCESS_TO_NC
            nc_code = self._generate_nc_code(
                process_plan=process_plan,
                machine_type=current_state.metadata.get("machine_type", "cnc_mill"),
            )
            state.nc_code = nc_code

            state.stage = PipelineStage.NC_TO_SIMULATION
            simulation_result = await self._run_simulation(nc_code=nc_code)
            state.simulation_result = simulation_result

            state.stage = PipelineStage.COMPLETED
            return state

        except Exception as e:
            logger.error("Refinement failed: %s", e, exc_info=True)
            state.stage = PipelineStage.FAILED
            # 包装异常消息，避免直接回显内部错误细节
            safe = safe_error_message(e, context="nl2cad.refine_and_regenerate", fallback="模型精修失败")
            state.error = safe["message"]
            state.metadata["error_id"] = safe["error_id"]
            return state


# Singleton instance (双重检查锁，线程安全)
_orchestrator_instance: NL2NCPipelineOrchestrator | None = None
_orchestrator_lock = threading.Lock()


def get_nl2nc_orchestrator() -> NL2NCPipelineOrchestrator:
    """Get or create orchestrator instance."""
    global _orchestrator_instance
    if _orchestrator_instance is not None:
        return _orchestrator_instance
    with _orchestrator_lock:
        if _orchestrator_instance is None:
            _orchestrator_instance = NL2NCPipelineOrchestrator()
    return _orchestrator_instance
