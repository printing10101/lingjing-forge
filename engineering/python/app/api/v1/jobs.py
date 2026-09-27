"""
Jobs API - Async task management and SSE streaming.

Supports PostgreSQL-persisted task queries, Redis progress retrieval,
and real-time SSE event streaming.
"""

import asyncio
import logging


from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.response import ErrorCode, error, success
from app.tasks.task_manager import TaskType, TaskStatus
from app.tasks.task_system import AsyncTaskManager
from app.auth.permissions import require_permission
from app.api.v1.auth import get_current_user
from app.config.limits import SSE_HEARTBEAT_TIMEOUT_SEC

logger = logging.getLogger(__name__)

# SSE 心跳超时（秒）：由 ``app.config.limits`` 集中管理，
# 与 workflows.py / lnn/services.py 共享同一基准值，避免不同 SSE 通道行为不一致。

router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["Async Jobs"],
    dependencies=[Depends(require_permission("job:read"))],
)
task_manager = AsyncTaskManager()


class CreateJobRequest(BaseModel):
    """通用任务创建请求体。"""

    task_type: str = Field(..., description="任务类型（lnn_training/lnn_inference/data_processing 等）")
    params: dict = Field(default_factory=dict, description="任务参数")
    name: str | None = Field(None, max_length=128, description="任务名称（并入 params.name）")
    idempotency_key: str | None = Field(None, max_length=128, description="幂等键")


@router.post("", dependencies=[Depends(require_permission("job:manage"))])
async def create_job(body: CreateJobRequest):
    """创建通用任务（真实落库到任务管理器，返回 job_id 供轮询/SSE 跟踪）。"""
    try:
        tt = TaskType(body.task_type)
    except ValueError:
        valid_types = [t.value for t in TaskType]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid task_type '{body.task_type}'. Valid values: {valid_types}",
        )

    try:
        params = dict(body.params)
        if body.name:
            params.setdefault("name", body.name)
        record = await task_manager.create_task(
            task_type=tt,
            params=params,
            idempotency_key=body.idempotency_key,
        )
    except RuntimeError as e:
        logger.error("创建任务失败: %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))

    return success(data=record.to_dict(), message="任务创建成功")


@router.get("/{job_id}")
async def get_job(job_id: str):
    record = await task_manager.get_task(job_id)
    if not record:
        return error(code=ErrorCode.NOT_FOUND, message=f"Job '{job_id}' not found")

    progress_data = await task_manager.get_task_progress_from_redis(job_id)
    result = record.to_dict()
    if progress_data:
        result["progress_redis"] = progress_data

    return success(data=result, message="Job retrieved")


@router.get("/{job_id}/progress")
async def get_job_progress(job_id: str):
    record = await task_manager.get_task(job_id)
    if not record:
        return error(code=ErrorCode.NOT_FOUND, message=f"Job '{job_id}' not found")

    progress_data = await task_manager.get_task_progress_from_redis(job_id)
    return success(
        data={
            "job_id": job_id,
            "status": record.status.value,
            "progress_db": record.progress,
            "progress_redis": progress_data.get("progress"),
            "message": progress_data.get("message", ""),
            "metrics": progress_data.get("metrics"),
        },
        message="Progress retrieved",
    )


@router.get("/{job_id}/stream", dependencies=[Depends(get_current_user)])
async def stream_job_events(job_id: str):
    record = await task_manager.get_task(job_id)
    if not record:
        return error(code=ErrorCode.NOT_FOUND, message=f"Job '{job_id}' not found")

    queue = task_manager.subscribe(job_id)

    async def event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=SSE_HEARTBEAT_TIMEOUT_SEC)
                    yield event
                except asyncio.TimeoutError:
                    record = await task_manager.get_task(job_id)
                    if record and record.status in (
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                    ):
                        yield f'event: done\ndata: {{"status": "{record.status.value}"}}\n\n'
                        break
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            logger.info("SSE stream cancelled for job %s", job_id)
        finally:
            task_manager.unsubscribe(job_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{job_id}/cancel",
    dependencies=[Depends(require_permission("job:manage"))],
)
async def cancel_job(job_id: str):
    result = await task_manager.cancel_task(job_id)
    if not result:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Cannot cancel job '{job_id}'")
    return success(data={"job_id": job_id, "status": "cancelled"}, message="Job cancelled")


@router.delete(
    "/{job_id}",
    dependencies=[Depends(require_permission("job:manage"))],
)
async def delete_job(job_id: str):
    result = await task_manager.cancel_task(job_id)
    if not result:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Cannot cancel job '{job_id}'")
    return success(data={"job_id": job_id, "status": "cancelled"}, message="Job cancelled")


@router.get("")
async def list_jobs(
    task_type: str | None = Query(None),
    status: str | None = Query(None),
    owner_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    try:
        tt = TaskType(task_type) if task_type else None
    except ValueError:
        valid_types = [t.value for t in TaskType]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid task_type '{task_type}'. Valid values: {valid_types}",
        )

    try:
        st = TaskStatus(status) if status else None
    except ValueError:
        valid_statuses = [s.value for s in TaskStatus]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid status '{status}'. Valid values: {valid_statuses}",
        )

    tasks = await task_manager.list_tasks(task_type=tt, status=st, owner_id=owner_id, limit=limit, offset=offset)
    # 修复：从数据库查询真实总数，而非使用分页后的结果长度
    total = await task_manager.count_tasks(task_type=tt, status=st, owner_id=owner_id)

    items = []
    for t in tasks:
        td = t.to_dict()
        items.append(
            {
                "job_id": t.job_id,
                "task_type": t.task_type.value,
                "status": t.status.value,
                "progress": t.progress,
                "created_at": td.get("created_at_iso", ""),
                "duration_seconds": td.get("duration_seconds"),
                "owner_id": t.owner_id,
                "error": t.error,
            }
        )

    return success(
        data={
            "jobs": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        message="Jobs retrieved",
    )


@router.get("/stats")
async def get_task_stats():
    return success(data=task_manager.get_stats(), message="Stats retrieved")
