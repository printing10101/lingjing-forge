"""DXF 图纸解析端点（Agent Gateway · W13 扩面，只读）。

让决策智能体读懂图纸输入（CAM 链路最上游）：

- ``GET /dxf/summary?path=...`` —— 解析 DXF 文件返回结构化摘要：
  DXF 版本、各类实体统计（线/圆/弧/多段线/标注/HATCH/BLOCK/SPLINE）、
  图形范围、解析警告与错误。

安全设计：与 G-code job 端点同一白名单机制（``resolve_agent_input_path``，
LINGJING_AGENT_INPUT_ROOTS，默认 data/output/outputs），仅接受 ``.dxf``，
fail-closed 拒绝路径遍历。

只返回统计摘要，不回传实体几何明细（体积控制；后续按需扩展）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.agent_gateway._state import resolve_agent_input_path
from app.auth.permissions import require_permission
from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])


@router.get(
    "/dxf/summary",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def dxf_summary(
    path: str = Query(min_length=1, max_length=1024),
):
    """DXF 文件解析摘要（R 类）：实体统计/版本/范围/警告。"""
    dxf_path = resolve_agent_input_path(path, "path", ".dxf")
    try:
        from app.dxf.api import get_dxf_parser

        result = get_dxf_parser().parse(str(dxf_path))
        payload = result.to_dict()
        payload["path"] = str(dxf_path)
        return success(data=payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 网关统一兜底，不泄露堆栈
        logger.exception("dxf_summary failed for %s", dxf_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc
