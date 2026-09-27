"""G 代码生成任务端点（Agent Gateway · W12 扩面，写类 job 化）。

让外部决策智能体（华为 ICT 赛道三）能发起 G 代码生成任务并追踪进度：

- ``POST /gcode/jobs`` —— 创建生成任务（写类，``agent:execute`` 权限），
  后台异步执行，立即返回 task_id；
- ``GET  /gcode/jobs`` —— 最近任务列表（R 类）；
- ``GET  ``/gcode/jobs/{task_id}`` —— 任务详情（R 类），含状态/错误/
  警告/特征摘要；G 代码全文通过 ``?include_gcode=true`` 显式索取。

安全设计（fail-closed）：
- 输入文件路径白名单：必须在 ``LINGJING_GCODE_INPUT_ROOTS``（
  ``os.pathsep`` 分隔，默认 cwd 下 data/output/outputs）之内
  （resolve 后前缀校验，拒绝路径遍历）；仅接受 ``.json``；
- controller_type 白名单（四种控制器）；
- 任务执行走真实 pipeline：阶段 5 报告加载、L1-L6 安全校验、
  失败案例库与成功案例采集挂钩全部生效——智能体发起的失败同样
  进入自进化管道。

G 代码产物定位「工程师助手」：生成结果仅供 CAM 二次校验，绝不
直接接口 CNC 控制器（项目记忆硬约束，在响应 disclaimer 中告知）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message
from app.gcode_generation.gcode_store import (
    GCodeGenerationError,
    GCodeGenerationPipelineError,
    get_task_store,
)
from app.gcode_generation.pipeline import GCodeGenerationPipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])

_VALID_CONTROLLERS = frozenset({"fanuc_0i", "siemens_840d", "heidenhain_tnc", "xmachine_xm100"})
_DEFAULT_INPUT_ROOTS = ("data", "output", "outputs")
_MAX_MATERIAL_LEN = 64

# 共享 pipeline 实例（TaskStore 本身是进程级单例，pipeline 无状态可复用）
_shared_pipeline: GCodeGenerationPipeline | None = None


def get_shared_gcode_pipeline() -> GCodeGenerationPipeline:
    """懒初始化共享 pipeline（测试可通过 reset 注入隔离实例）。"""
    global _shared_pipeline
    if _shared_pipeline is None:
        _shared_pipeline = GCodeGenerationPipeline()
    return _shared_pipeline


def reset_shared_gcode_pipeline() -> None:
    """重置共享单例（测试用）。"""
    global _shared_pipeline
    _shared_pipeline = None


def _allowed_input_roots() -> list[Path]:
    """输入文件允许的根目录（W13 起统一走 _state 共享实现，保持兼容）。"""
    from app.api.v1.agent_gateway._state import agent_input_roots

    return agent_input_roots()


def _validate_input_path(path: str, field: str) -> Path:
    """输入文件白名单校验（W13 起统一走 _state 共享实现）。"""
    from app.api.v1.agent_gateway._state import resolve_agent_input_path

    return resolve_agent_input_path(path, field, ".json")


class GCodeJobRequest(BaseModel):
    """创建 G 代码生成任务请求。"""

    chatter_report_path: str = Field(min_length=1, max_length=1024)
    operation_plan_path: str = Field(min_length=1, max_length=1024)
    controller_type: str = Field(default="fanuc_0i")
    material_name: str = Field(default="45#钢", max_length=_MAX_MATERIAL_LEN)


@router.post(
    "/gcode/jobs",
    dependencies=[Depends(require_permission("agent:execute"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def gcode_create_job(req: GCodeJobRequest):
    """创建 G 代码生成任务（写类）：后台异步执行，返回 task_id。"""
    if req.controller_type not in _VALID_CONTROLLERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(f"controller_type 须为 {sorted(_VALID_CONTROLLERS)} 之一，当前: {req.controller_type!r}"),
        )
    chatter = _validate_input_path(req.chatter_report_path, "chatter_report_path")
    plan = _validate_input_path(req.operation_plan_path, "operation_plan_path")
    try:
        pipeline = get_shared_gcode_pipeline()
        task = pipeline.create_task(
            source_chatter_report_path=str(chatter),
            source_operation_plan_path=str(plan),
            controller_type=req.controller_type,
            material_name=req.material_name,
        )
    except GCodeGenerationPipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=safe_error_message(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 网关统一兜底
        logger.exception("gcode_create_job failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc

    # 后台异步执行；done 回调兜底记录未捕获异常（run_pipeline 内部自捕获，
    # 此处防御「Task exception was never retrieved」）
    runner = asyncio.create_task(pipeline.run_pipeline(task.task_id))

    def _log_done(t: asyncio.Task) -> None:
        if t.cancelled():
            logger.warning("G 代码任务 %s 被取消", task.task_id)
        elif t.exception() is not None:
            logger.error("G 代码任务 %s 未捕获异常: %s", task.task_id, t.exception())

    runner.add_done_callback(_log_done)

    logger.info(
        "Agent Gateway 创建 G 代码任务 task_id=%s controller=%s chatter=%s plan=%s",
        task.task_id,
        req.controller_type,
        chatter,
        plan,
    )
    return success(
        data={
            "task_id": task.task_id,
            "status": task.status,
            "controller_type": req.controller_type,
            "message": "任务已创建并开始后台执行，请用 task_id 轮询 /gcode/jobs/{task_id}",
        }
    )


@router.get(
    "/gcode/jobs",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def gcode_list_jobs(limit: int = Query(default=20, ge=1, le=100)):
    """最近 G 代码生成任务列表（R 类），按创建时间倒序。"""
    try:
        # list_tasks 不支持 limit，取全量后按创建时间倒序截断
        tasks = get_task_store().list_tasks()[:limit]
        return success(
            data={
                "count": len(tasks),
                "tasks": [t.to_dict() for t in tasks],
            }
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_list_jobs failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc


@router.get(
    "/gcode/jobs/{task_id}",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
def gcode_get_job(
    task_id: str,
    include_gcode: bool = Query(default=False),
):
    """任务详情（R 类）：状态/错误/警告/特征摘要，G 代码全文按需索取。"""
    if not task_id or len(task_id) > 256 or "/" in task_id or ".." in task_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="task_id 格式非法",
        )
    try:
        try:
            task = get_task_store().get_task(task_id)
        except GCodeGenerationError:
            # TaskStore 契约：不存在时抛任务异常（非返回 None）
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"任务不存在: {task_id}",
            )
        if task is None:  # 防御：子类实现若返回 None
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"任务不存在: {task_id}",
            )
        payload = task.to_dict()
        if not include_gcode:
            gcode_len = len(payload.get("gcode_text") or "")
            payload["gcode_text"] = ""
            payload["gcode_text_length"] = gcode_len
        payload["disclaimer"] = "生成结果仅供 CAM 软件（NX/PowerMill/PyCAM）二次校验，绝不直接接口 CNC 控制器"
        return success(data=payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("gcode_get_job failed for %s", task_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=safe_error_message(exc),
        ) from exc
