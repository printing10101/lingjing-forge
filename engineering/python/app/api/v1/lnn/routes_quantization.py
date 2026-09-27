"""LNN 量化端点（quantize / status / cancel）。

从 routes.py 拆分而来（P0-2.3 子路由拆分）。本模块承载模型 INT8 量化
相关端点，模块级状态（task_manager 等）集中在 ``dependencies.py``。

``_log_task_exception`` 从 ``routes_training`` 复用，用于给
``asyncio.create_task`` 添加异常回调，避免静默失败。
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission
from app.core.api_response import api_response
from app.middleware.rate_limiter import limiter
from app.models.schemas import LNNQuantizeRequest
from app.tasks.task_manager import TaskType, TaskStatus
from app.ai.lnn.inference.registry import (
    is_quantized_model,
)

from app.api.v1.lnn.dependencies import (
    model_registry,
    task_manager,
)
from app.api.v1.lnn.services import _run_quantization_task_v2
from app.api.v1.lnn.routes_training import _log_task_exception

logger = logging.getLogger(__name__)

router = APIRouter()

# 模块级任务引用集合：保存 asyncio.create_task 返回的 Task 对象，
# 防止任务被 GC 提前回收（CPython 弱引用机制下，本地变量出作用域即可能被回收）。
# 任务完成后由 done_callback 自动从集合中移除。
_active_quantize_tasks: set[asyncio.Task] = set()


@router.post("/models/{model_name}/quantize", dependencies=[Depends(require_permission("lnn:write"))])
@limiter.limit("10/hour")
async def quantize_model(request: Request, model_name: str, body: LNNQuantizeRequest):
    """异步启动 INT8 量化任务,立即返回 job_id。"""
    try:
        entry = model_registry.registry.get(model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{model_name}' not found",
            )

        if is_quantized_model(model_name):
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"Model '{model_name}' is already quantized",
            )

        if body.quantization_type == "static" and not body.calibration_data_path:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message="Calibration data path is required for static quantization",
            )

        record = await task_manager.create_task(
            TaskType.MODEL_QUANTIZATION,
            {
                "model_name": model_name,
                "quantization_type": body.quantization_type,
                "calibration_data_path": body.calibration_data_path,
            },
        )
        task_id = record.job_id

        async def quantization_executor(cancel_evt, progress_updater):
            return await _run_quantization_task_v2(
                task_id,
                model_name,
                body.quantization_type,
                body.calibration_data_path,
                cancel_evt,
                progress_updater,
            )

        # 修复：保存任务引用防止 GC 提前回收，并添加异常处理
        quantize_task = asyncio.create_task(task_manager.execute_task(task_id, quantization_executor))
        _active_quantize_tasks.add(quantize_task)

        def _on_quantize_done(t: asyncio.Task) -> None:
            _active_quantize_tasks.discard(t)
            _log_task_exception(t, f"quantize-{task_id}")

        quantize_task.add_done_callback(_on_quantize_done)

        return success(
            data={"task_id": task_id, "status": "queued"},
            message="Quantization job queued",
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"lnn.quantize[{model_name}]")
        logger.warning("Quantization init failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/quantize/{task_id}/status")
@api_response
async def get_quantization_status(task_id: str):
    """查询异步量化任务的状态与结果。"""
    record = await task_manager.get_task(task_id)
    if not record or record.task_type != TaskType.MODEL_QUANTIZATION:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Quantization task '{task_id}' not found",
        )

    payload: dict = {
        "task_id": task_id,
        "status": record.status.value,
        "progress": getattr(record, "progress", 0.0),
    }
    if record.status == TaskStatus.COMPLETED and record.result:
        payload["result"] = record.result
    if record.status == TaskStatus.FAILED and record.error:
        payload["error"] = record.error
    return success(data=payload, message="Quantization status retrieved")


@router.post("/quantize/{task_id}/cancel", dependencies=[Depends(require_permission("lnn:write"))])
@api_response
async def cancel_quantization_task(task_id: str):
    """取消进行中的量化任务。"""
    record = await task_manager.get_task(task_id)
    if not record or record.task_type != TaskType.MODEL_QUANTIZATION:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Quantization task '{task_id}' not found",
        )

    if record.status in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Quantization task '{task_id}' is already {record.status.value}",
        )

    cancelled = await task_manager.cancel_task(task_id)
    return success(
        data={
            "task_id": task_id,
            "status": "cancelled" if cancelled else "cancelling",
        },
        message="Quantization cancellation processed",
    )
