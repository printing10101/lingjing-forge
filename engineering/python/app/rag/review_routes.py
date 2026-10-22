"""Authenticated process knowledge review API."""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import get_current_user
from .process_quadruple import ReviewConflict, get_process_quadruple_index

router = APIRouter(prefix="/api/rag/process/review", tags=["工艺知识审核"])


def reviewer(user: dict = Depends(get_current_user)) -> str:
    username = user.get("username")
    if (
        not username
        or username == "_anonymous_"
        or user.get("is_guest")
        or user.get("role") not in {"admin", "engineer"}
    ):
        raise HTTPException(403, "工艺审核需要已登录的工程师或管理员身份")
    return username


class ReviewAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(strict=True, ge=1)
    decision: Literal["approved", "rejected", "revoked", "revise"]
    note: str = Field(min_length=1, max_length=4000)
    evidence: dict[str, Any] | None = None
    replacement: dict[str, Any] | None = None


@router.get("/records")
def records(actor: str = Depends(reviewer)):
    return {"records": get_process_quadruple_index().list_review_records()}


@router.post("/{record_id}")
def review(record_id: str, request: ReviewAction, actor: str = Depends(reviewer)):
    try:
        if request.decision == "revise" and not request.replacement:
            raise ValueError("修订需要完整 replacement 四元组")
        return get_process_quadruple_index().update_review(record_id, actor=actor, **request.model_dump())
    except KeyError as exc:
        raise HTTPException(404, "知识记录不存在") from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            400,
            "[审核输入错误] 依据缺失或格式无效。建议操作：核对来源、页码、单位、结构化钻孔范围（硬度、孔径、深径比、冷却与装夹）及未解决事项。",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(503, "[持久化错误] 审核未保存。建议操作：检查数据目录后重试。") from exc
