"""
Heartbeat Scheduling API Routes

Provides RESTful interfaces for task scheduling, budget management,
and execution monitoring.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.dependencies import get_budget_manager
from app.dependencies import get_scheduler
from app.heartbeat.heartbeat import ScheduledTask, ScheduleStatus
from app.tasks.execution import get_execution_engine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/heartbeat",
    tags=["heartbeat"],
    dependencies=[Depends(require_permission("heartbeat:read"))],
)


class CreateScheduledTaskRequest(BaseModel):
    """创建调度任务请求"""

    task_id: str = Field(..., description="任务唯一标识符", min_length=1)
    agent_id: str = Field(..., description="执行代理ID", min_length=1)
    schedule: str = Field(..., description="Cron表达式（分 时 日 月 星期）", min_length=1)
    task_type: str = Field(
        ...,
        description="任务类型（lnn_inference/lnn_training/lnn_analysis）",
        min_length=1,
    )
    params: dict[str, Any] = Field(default={}, description="任务参数")
    metadata: dict[str, Any] = Field(default={}, description="任务元数据")
    max_retries: int = Field(default=3, description="最大重试次数", ge=0, le=10)


class TaskResponse(BaseModel):
    """任务响应"""

    task_id: str
    agent_id: str
    schedule: str
    task_type: str
    status: str
    last_run: float | None = None
    next_run: float | None = None
    retry_count: int = 0
    max_retries: int = 3
    params: dict[str, Any] = {}
    metadata: dict[str, Any] = {}


class BudgetCheckResponse(BaseModel):
    """预算检查响应"""

    passed: bool
    status: str
    usages: list[dict[str, Any]] = []
    warnings: list[str] = []
    blocked_reasons: list[str] = []


class ExecutionResultResponse(BaseModel):
    """执行结果响应"""

    task_id: str
    status: str
    duration_ms: float
    result_data: dict[str, Any] | None = None
    error_message: str | None = None
    resource_usage: dict[str, Any] = {}


@router.post("/tasks", response_model=TaskResponse, dependencies=[Depends(require_permission("heartbeat:write"))])
async def create_scheduled_task(request: CreateScheduledTaskRequest):
    """创建调度任务"""
    scheduler = get_scheduler()

    existing = scheduler.wakeup_queue.get_task(request.task_id)
    if existing:
        logger.info("Task already exists: %s", request.task_id)
        raise HTTPException(status_code=409, detail="Task already exists")

    task = ScheduledTask(
        task_id=request.task_id,
        agent_id=request.agent_id,
        schedule=request.schedule,
        task_type=request.task_type,
        params=request.params,
        status=ScheduleStatus.PENDING,
        max_retries=request.max_retries,
        metadata=request.metadata,
    )

    created = scheduler.schedule_task(task)

    return TaskResponse(
        task_id=created.task_id,
        agent_id=created.agent_id,
        schedule=created.schedule,
        task_type=created.task_type,
        status=created.status.value,
        last_run=created.last_run,
        next_run=created.next_run,
        retry_count=created.retry_count,
        max_retries=created.max_retries,
        params=created.params,
        metadata=created.metadata,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_scheduled_task(task_id: str):
    """获取调度任务详情"""
    from app.dependencies import get_scheduler

    scheduler = get_scheduler()
    task = scheduler.wakeup_queue.get_task(task_id)

    if task is None:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskResponse(
        task_id=task.task_id,
        agent_id=task.agent_id,
        schedule=task.schedule,
        task_type=task.task_type,
        status=task.status.value,
        last_run=task.last_run,
        next_run=task.next_run,
        retry_count=task.retry_count,
        max_retries=task.max_retries,
        params=task.params,
        metadata=task.metadata,
    )


@router.get("/tasks", response_model=list[TaskResponse])
async def list_scheduled_tasks(agent_id: str | None = None, status: str | None = None):
    """列出所有调度任务"""
    from app.dependencies import get_scheduler
    from app.heartbeat.heartbeat import ScheduleStatus

    scheduler = get_scheduler()

    status_enum = ScheduleStatus(status) if status else None
    tasks = scheduler.wakeup_queue.list_tasks(agent_id=agent_id, status=status_enum)

    return [
        TaskResponse(
            task_id=t.task_id,
            agent_id=t.agent_id,
            schedule=t.schedule,
            task_type=t.task_type,
            status=t.status.value,
            last_run=t.last_run,
            next_run=t.next_run,
            retry_count=t.retry_count,
            max_retries=t.max_retries,
            params=t.params,
            metadata=t.metadata,
        )
        for t in tasks
    ]


@router.post("/tasks/{task_id}/trigger", dependencies=[Depends(require_permission("heartbeat:write"))])
async def trigger_task_now(task_id: str):
    """立即触发任务执行"""
    from app.dependencies import get_scheduler

    scheduler = get_scheduler()

    try:
        scheduler.trigger_now(task_id)
        return {"status": "triggered", "task_id": task_id}
    except KeyError:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/pause", dependencies=[Depends(require_permission("heartbeat:write"))])
async def pause_task(task_id: str):
    """暂停任务"""
    scheduler = get_scheduler()
    task = scheduler.wakeup_queue.get_task(task_id)

    if task is None:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")

    scheduler.pause_task(task_id)
    return {"status": "paused", "task_id": task_id}


@router.post("/tasks/{task_id}/resume", dependencies=[Depends(require_permission("heartbeat:write"))])
async def resume_task(task_id: str):
    """恢复任务"""
    from app.dependencies import get_scheduler

    scheduler = get_scheduler()

    try:
        scheduler.resume_task(task_id)
        return {"status": "resumed", "task_id": task_id}
    except KeyError:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")


@router.delete("/tasks/{task_id}", dependencies=[Depends(require_permission("heartbeat:write"))])
async def delete_task(task_id: str):
    """删除任务"""
    scheduler = get_scheduler()
    deleted = scheduler.wakeup_queue.delete_task(task_id)

    if not deleted:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")

    return {"status": "deleted", "task_id": task_id}


@router.get("/tasks/{task_id}/history")
async def get_task_history(task_id: str, limit: int = Query(50, ge=1, le=100)):
    """获取任务执行历史"""
    from app.dependencies import get_scheduler

    scheduler = get_scheduler()
    task = scheduler.wakeup_queue.get_task(task_id)

    if task is None:
        logger.info("Task not found: %s", task_id)
        raise HTTPException(status_code=404, detail="Task not found")

    history = scheduler.wakeup_queue.get_task_history(task_id, limit)
    return {"task_id": task_id, "history": history}


@router.get("/budget/{agent_id}", response_model=BudgetCheckResponse)
async def check_budget(agent_id: str):
    """检查代理预算状态"""
    from app.dependencies import get_budget_manager

    budget_manager = get_budget_manager()
    result = budget_manager.check_budget(agent_id)

    return BudgetCheckResponse(
        passed=result.passed,
        status=result.status.value,
        usages=[u.to_dict() for u in result.usages],
        warnings=result.warnings,
        blocked_reasons=result.blocked_reasons,
    )


@router.get("/budget/notifications")
async def get_budget_notifications(agent_id: str | None = None, limit: int = Query(50, ge=1, le=100)):
    """获取预算通知"""
    budget_manager = get_budget_manager()
    notifications = budget_manager.get_notifications(agent_id, limit)

    return {"notifications": notifications}


@router.get("/stats")
async def get_scheduler_stats():
    """获取调度器统计信息"""
    scheduler = get_scheduler()
    engine = get_execution_engine()

    return {
        "scheduler": scheduler.get_stats(),
        "engine": {
            "orphaned_sessions": len(engine.session_manager.get_orphaned_sessions()),
        },
    }


@router.post("/recovery/orphaned", dependencies=[Depends(require_permission("heartbeat:write"))])
async def recover_orphaned_tasks():
    """手动触发孤立任务恢复"""
    engine = get_execution_engine()
    recovered = await engine.recover_orphaned_tasks()

    return {"status": "completed", "recovered_count": recovered}
