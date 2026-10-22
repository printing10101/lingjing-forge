"""Agent candidate submission and status; approval remains a human-only route."""

from typing import Annotated, Any
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator

from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.rag.process_quadruple import ReviewConflict, get_process_quadruple_index
from app.rag.process_review import is_approved

router = APIRouter(prefix="/api/agent/v1/cam/knowledge-candidates", tags=["Agent knowledge candidates"])
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
PositiveParameter = Annotated[FiniteFloat, Field(gt=0, strict=True)]


class CandidateContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature: Identifier
    process: Identifier
    tool: Identifier
    material: Identifier
    parameters: dict[str, PositiveParameter] = Field(min_length=1, max_length=64)
    candidate_context: dict = Field(default_factory=dict)
    chunk_ids: list[str] = Field(default_factory=list, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=32)


class CandidateSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    submission_id: Identifier
    content: CandidateContent

    @model_validator(mode="before")
    @classmethod
    def reject_nonfinite_json(cls, value):
        # Avoid reflecting NaN/Infinity in a framework validation response (invalid JSON).
        try:
            json.dumps(value, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "候选请求必须是有限数值组成的有效 JSON") from exc
        return value


def agent_identity(request: Request, *, write: bool = False) -> str:
    agent_id = getattr(request.state, "agent_id", None)
    scopes = getattr(request.state, "agent_scopes", [])
    if not agent_id or "R" not in scopes or (write and "W" not in scopes):
        raise HTTPException(403, "需要已认证 Agent 的 R 权限；提交候选还需 W 权限")
    return agent_id


def writer(request: Request) -> str:
    return agent_identity(request, write=True)


def reader(request: Request) -> str:
    return agent_identity(request)


@router.post(
    "",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def submit_candidate(request: CandidateSubmission, agent: str = Depends(writer)):
    if len(request.model_dump_json().encode()) > 64 * 1024:
        raise HTTPException(413, "候选请求超过 64KB")
    try:
        record, created = get_process_quadruple_index().submit_candidate(
            agent, request.submission_id, request.content.model_dump()
        )
        return success(
            data={
                "created": created,
                "record": record,
                "review_url": "/process-knowledge-review",
                "human_review_required": not is_approved(record),
            }
        )
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, "待审依据字段无效，请核对类型和单位") from exc
    except RuntimeError as exc:
        raise HTTPException(503, "候选未保存，请检查数据目录后重试") from exc


@router.get(
    "/{record_id}",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
def candidate_status(record_id: str, agent: str = Depends(reader)):
    try:
        record = get_process_quadruple_index().get_candidate_for_agent(record_id, agent)
        eligible = is_approved(record)
        return success(
            data={
                "record": record,
                "eligible_for_recommendation": eligible,
                "next_action": "query_recommendation" if eligible else "human_review",
                "review_url": "/process-knowledge-review",
            }
        )
    except KeyError as exc:
        raise HTTPException(404, "候选记录不存在或不属于当前 Agent") from exc
