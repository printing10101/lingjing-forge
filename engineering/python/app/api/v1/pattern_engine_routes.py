"""Pattern Engine API Routes."""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.patterns.pattern_engine import get_pattern_engine
from app.core.response import success, error, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/templates/patterns",
    tags=["patterns"],
    dependencies=[Depends(require_permission("pattern:read"))],
)


class ExecutionRecordRequest(BaseModel):
    task_id: str = Field(..., description="Task ID")
    branch_id: str = Field(..., description="Branch ID")
    elements: dict[str, Any] = Field(default_factory=dict, description="Execution elements")
    conditions: dict[str, Any] = Field(default_factory=dict, description="Execution conditions")
    metrics: dict[str, Any] = Field(default_factory=dict, description="Execution metrics")


@router.get("")
def list_patterns(
    pattern_type: str | None = None,
):
    """List all discovered patterns."""
    engine = get_pattern_engine()
    patterns = engine.get_patterns(pattern_type=pattern_type)
    return success(data=[p.to_dict() for p in patterns])


@router.get("/anti_patterns")
def list_anti_patterns():
    """List all detected anti-patterns."""
    engine = get_pattern_engine()
    patterns = engine.get_anti_patterns()
    return success(data=[p.to_dict() for p in patterns])


@router.post("/record")
def record_execution(req: ExecutionRecordRequest):
    """Record a task execution for pattern analysis."""
    engine = get_pattern_engine()
    record = engine.record_execution(
        task_id=req.task_id,
        branch_id=req.branch_id,
        elements=req.elements,
        conditions=req.conditions,
        metrics=req.metrics,
        success=True,
    )
    return success(data={"task_id": record.task_id})


@router.post("/analyze")
def analyze_patterns(min_samples: int = 10):
    """Run pattern analysis on accumulated execution data."""
    engine = get_pattern_engine()
    new_patterns = engine.analyze_patterns(min_samples=min_samples)
    return success(
        data={
            "new_patterns": len(new_patterns),
            "patterns": [p.to_dict() for p in new_patterns],
        }
    )


@router.get("/{pattern_id}")
def get_pattern(pattern_id: str):
    """Get details of a specific pattern."""
    engine = get_pattern_engine()
    patterns = engine.get_patterns()
    pattern = next((p for p in patterns if p.pattern_id == pattern_id), None)
    if pattern is None:
        return error(code=ErrorCode.NOT_FOUND, message="Pattern not found")
    return success(data=pattern.to_dict())


@router.get("/{pattern_id}/suggestions")
def get_pattern_suggestions(pattern_id: str):
    """Get auto-generated suggestions from a pattern."""
    engine = get_pattern_engine()
    suggestion = engine.generate_suggestions(pattern_id)
    if suggestion is None:
        return error(code=ErrorCode.NOT_FOUND, message="Pattern not found")
    return success(data=suggestion)
