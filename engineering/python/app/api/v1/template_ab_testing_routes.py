"""A/B Testing API Routes."""

import logging


from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.templates.template_ab_testing import get_ab_testing
from app.core.response import success, error, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/templates/ab_tests",
    tags=["ab_testing"],
    dependencies=[Depends(require_permission("template-abtest:read"))],
)


class CreateExperimentRequest(BaseModel):
    name: str = Field(..., description="Experiment name")
    control_branch: str = Field(..., description="Control branch ID")
    candidate_branch: str = Field(..., description="Candidate branch ID")
    traffic_split: float = Field(default=0.10, description="Traffic split for candidate (0.0-1.0)")


class RecordExecutionRequest(BaseModel):
    experiment_id: str = Field(..., description="Experiment ID")
    branch: str = Field(..., description="Branch used (control/candidate)")
    execution_time: float = Field(..., description="Execution time in seconds")
    resource_cost: float = Field(default=0.0, description="Resource cost")


class AssignBranchRequest(BaseModel):
    project_id: str = Field(..., description="Project ID")


@router.post("", dependencies=[Depends(require_permission("template-abtest:write"))])
def create_experiment(req: CreateExperimentRequest):
    """Create a new A/B experiment."""
    framework = get_ab_testing()
    exp = framework.create_experiment(
        name=req.name,
        control_branch=req.control_branch,
        candidate_branch=req.candidate_branch,
        traffic_split=req.traffic_split,
    )
    return success(data=exp.to_dict())


@router.post("/record", dependencies=[Depends(require_permission("template-abtest:write"))])
def record_execution(req: RecordExecutionRequest):
    """Record an execution in an experiment."""
    framework = get_ab_testing()
    framework.record_execution(
        experiment_id=req.experiment_id,
        branch=req.branch,
        metrics={
            "execution_time": req.execution_time,
            "success": True,
            "resource_cost": req.resource_cost,
        },
    )
    return success(data={"recorded": True})


@router.post("/assign", dependencies=[Depends(require_permission("template-abtest:write"))])
def assign_branch(req: AssignBranchRequest):
    """Assign a project to a branch in all active experiments."""
    framework = get_ab_testing()
    results = {}
    for exp in framework.get_active_experiments():
        branch = framework.assign_branch(req.project_id, exp.experiment_id)
        results[exp.experiment_id] = branch
    return success(data=results)


@router.get("")
def list_experiments(status: str | None = None):
    """List experiments."""
    framework = get_ab_testing()
    exps = framework.list_experiments(status_filter=status)
    return success(data=[e.to_dict() for e in exps])


@router.get("/{experiment_id}")
def get_experiment(experiment_id: str):
    """Get experiment details."""
    framework = get_ab_testing()
    result = framework.get_experiment_results(experiment_id)
    if result is None:
        return error(code=ErrorCode.NOT_FOUND, message="Experiment not found")
    return success(data=result)


@router.post("/{experiment_id}/evaluate", dependencies=[Depends(require_permission("template-abtest:write"))])
def evaluate_experiment(experiment_id: str):
    """Evaluate an experiment."""
    framework = get_ab_testing()
    result = framework.evaluate(experiment_id)
    if result is None:
        return error(code=ErrorCode.NOT_FOUND, message="Experiment not found")
    return success(data=result)


@router.post("/{experiment_id}/conclude", dependencies=[Depends(require_permission("template-abtest:write"))])
def conclude_experiment(experiment_id: str):
    """Auto-conclude an experiment (merge or rollback)."""
    framework = get_ab_testing()
    exp = framework.auto_conclude(experiment_id)
    if exp is None:
        return error(code=ErrorCode.NOT_FOUND, message="Experiment not found or not running")
    return success(data=exp.to_dict())
