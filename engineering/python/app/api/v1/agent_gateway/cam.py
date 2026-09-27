"""CAM 主链路只读端点（Agent Gateway · W11 扩面）。

把 Phase 0 自进化管道与工艺知识库以只读方式暴露给决策智能体
（华为 ICT 赛道三：Nexent 可进化决策智能体调得到核心能力的第一步）：

- ``GET /gcode/failure-stats`` —— 失败案例库基线报表
  （一次通过率 / 按来源 / 按错误码分布，M3）
- ``GET /gcode/failure-cases`` —— 最近案例列表（含错误分类，
  failure 案例附 G 代码文本，M1）
- ``GET /cam/process-recommend`` —— 工艺四元组推荐
  （feature+material → process/tool/parameters，含 generated_validated 实证，M4a）
- ``GET /cam/quadruple-stats`` —— 工艺知识库统计

全部为 R 类只读，权限 ``agent:read``；任何内部异常不泄露堆栈
（safe_error_message），认证与限流与既有网关端点一致。

写类操作（G 代码生成任务、灰度重放）暂不暴露——长任务需
job 化设计，见后续迭代。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth.permissions import require_permission
from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])

_MAX_CASES_LIMIT = 100


@router.get(
    "/gcode/failure-stats",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def gcode_failure_stats():
    """失败案例库基线报表（R 类）：一次通过率 + 失败分布。"""
    try:
        from app.gcode_generation.failure_case_store import get_failure_case_store

        return success(data=get_failure_case_store().stats())
    except Exception as exc:  # noqa: BLE001 - 网关统一兜底，不泄露堆栈
        logger.exception("gcode_failure_stats failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc


@router.get(
    "/gcode/failure-cases",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def gcode_failure_cases(
    limit: int = Query(default=20, ge=1, le=_MAX_CASES_LIMIT),
    offset: int = Query(default=0, ge=0),
    outcome: str | None = Query(default=None),
    source: str | None = Query(default=None),
):
    """最近运行案例列表（R 类），按时间倒序。

    outcome: success/failure 过滤；source: 失败来源过滤。
    failure 案例携带完整 G 代码文本与错误分类，供智能体学习。
    """
    try:
        from app.gcode_generation.failure_case_store import get_failure_case_store

        cases = get_failure_case_store().list_cases(limit=limit, offset=offset, outcome=outcome, source=source)
        return success(
            data={
                "count": len(cases),
                "cases": [c.to_dict() for c in cases],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_failure_cases failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc


@router.get(
    "/cam/process-recommend",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def cam_process_recommend(
    feature: str = Query(min_length=1, max_length=64),
    material: str = Query(default="general", max_length=64),
    top_k: int = Query(default=5, ge=1, le=20),
):
    """工艺四元组推荐（R 类）：feature+material → 工艺/刀具/参数。

    数据源含手册知识与 generated_validated 成功实证（Phase 0 M4a），
    按置信度降序返回。
    """
    try:
        from app.rag.process_quadruple import get_process_quadruple_index

        recs = get_process_quadruple_index().recommend_process(
            feature.strip().lower(), material.strip().lower(), top_k=top_k
        )
        return success(
            data={
                "feature": feature.strip().lower(),
                "material": material.strip().lower(),
                "recommendations": recs,
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("cam_process_recommend failed for feature=%s", feature)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc


@router.get(
    "/cam/quadruple-stats",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def cam_quadruple_stats():
    """工艺知识库统计（R 类）：四元组总数 / 特征/工艺/材料/刀具覆盖。"""
    try:
        from app.rag.process_quadruple import get_process_quadruple_index

        return success(data=get_process_quadruple_index().get_stats())
    except Exception as exc:  # noqa: BLE001
        logger.exception("cam_quadruple_stats failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc
