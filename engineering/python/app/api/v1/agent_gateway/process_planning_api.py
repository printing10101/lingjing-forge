"""工艺规划端点（Agent Gateway · W14 扩面，写类同步）。

让决策智能体发起端到端工艺规划（阶段 3 核心）：

- ``POST /process-planning/run`` —— 输入零件描述（材料 + 孔列表），
  走真实 ``ProcessPlanningPipeline``：孔特征识别 → 工艺知识库查询 →
  工序排序 → G 代码生成，返回各阶段记录与完整结果。

同步执行设计依据：全链路为 CPU 计算（知识库查询 + 排序 + 模板生成），
无 GPU/LLM 依赖，单任务秒级完成；异步 job 化收益为负（多一次轮询往返）。

安全/规模约束（fail-closed）：
- ``part_description`` ≤ 64KB；``holes`` ≤ 50 个（防资源放大）；
- material 必填字符串；controller 白名单四类；
- 产物定位「工程师助手」：G 代码须经 CAM 二次校验，绝不直连 CNC。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])

_VALID_CONTROLLERS = frozenset({"fanuc_0i", "siemens_840d", "heidenhain_tnc", "xmachine_xm100"})
_MAX_PART_DESC_BYTES = 64 * 1024
_MAX_HOLES = 50


class ProcessPlanningRequest(BaseModel):
    """工艺规划请求。"""

    part_description: dict[str, Any] = Field(description="零件描述：material 必填 + holes 列表")
    controller_type: str = Field(default="fanuc_0i")
    safe_z: float = Field(default=50.0, ge=1.0, le=500.0)
    program_number: int = Field(default=1000, ge=1, le=9999)


@router.post(
    "/process-planning/run",
    dependencies=[Depends(require_permission("agent:execute"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def process_planning_run(req: ProcessPlanningRequest):
    """端到端工艺规划（写类，同步）：零件描述 → 工序 → G 代码。"""
    if req.controller_type not in _VALID_CONTROLLERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(f"controller_type 须为 {sorted(_VALID_CONTROLLERS)} 之一，当前: {req.controller_type!r}"),
        )
    # 规模护栏：防资源放大
    if len(json.dumps(req.part_description, ensure_ascii=False).encode("utf-8")) > _MAX_PART_DESC_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"part_description 超过 {_MAX_PART_DESC_BYTES // 1024}KB 上限",
        )
    material = req.part_description.get("material")
    if not isinstance(material, str) or not material.strip() or len(material) > 64:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="part_description.material 必须为 1-64 字符字符串",
        )
    holes = req.part_description.get("holes", [])
    if not isinstance(holes, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="part_description.holes 必须为列表（可为空）",
        )
    if len(holes) > _MAX_HOLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"holes 数量 {len(holes)} 超过上限 {_MAX_HOLES}",
        )

    try:
        from app.process_planning.pipeline import ProcessPlanningPipeline

        result = ProcessPlanningPipeline().run(
            part_description=req.part_description,
            controller_type=req.controller_type,
            safe_z=req.safe_z,
            program_number=req.program_number,
        )
        payload = result.to_dict()
        payload["disclaimer"] = "生成结果仅供 CAM 软件（NX/PowerMill/PyCAM）二次校验，绝不直接接口 CNC 控制器"
        return success(data=payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - 网关统一兜底，不泄露堆栈
        logger.exception("process_planning_run failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc
