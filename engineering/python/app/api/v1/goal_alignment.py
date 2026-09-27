"""
Goal Alignment API Routes

Endpoints for goal hierarchy management, task goal association,
alignment verification, and progress tracking.
"""

import time
import uuid
import logging
import threading
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission
from app.dependencies import get_goal_chain_store
from app.goals.goal_alignment import GoalAlignmentChecker, GoalAlignmentError
from app.models.goals import (
    Goal,
    GoalLevel,
    GoalStatus,
)
from app.models.tasks import (
    EnhancedTask,
    EnhancedTaskType,
    EnhancedTaskStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/goal-alignment",
    tags=["Goal Alignment"],
    dependencies=[Depends(require_permission("goal:read"))],
)


# Pydantic 请求模型替换 data: dict 弱验证
# 参考 cost_budget.py 的 B13 修复模式：FastAPI 端点接收强类型模型而非裸 dict


class GoalCreateRequest(BaseModel):
    """目标对齐创建请求模型。

    字段对应 ``create_goal`` 端点原本从 ``data: dict`` 读取的键：
    level/status 在端点内会再做枚举校验（GoalLevel / GoalStatus），
    因此这里仅做基础字符串校验，避免重复实现枚举错误处理逻辑。
    """

    level: str = Field(default="task", description="目标层级: mission/strategic_goal/project/task")
    status: str = Field(
        default="not_started",
        description="目标状态: not_started/in_progress/completed/cancelled",
    )
    id: str | None = Field(default=None, description="目标ID（不传则自动生成）")
    name: str = Field(default="", description="目标名称")
    description: str = Field(default="", description="目标描述")
    parent_id: str | None = Field(default=None, description="父目标ID（非 mission 必填）")


class _AlignmentCheckerHolder:
    """线程安全的 :class:`GoalAlignmentChecker` 单例容器。

    替代重构前的 ``global _alignment_checker`` 模式。线程安全由
    :class:`threading.Lock` 保证；同时通过惰性创建的方式仅在首次访问
    时构造实例，避免不必要的启动开销。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: GoalAlignmentChecker | None = None

    def get(self) -> GoalAlignmentChecker:
        """获取（或懒创建）单例实例。"""
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = GoalAlignmentChecker()
        return self._instance

    def reset(self) -> None:
        """重置容器，主要用于测试场景。"""
        with self._lock:
            self._instance = None


_holder = _AlignmentCheckerHolder()


def get_alignment_checker() -> GoalAlignmentChecker:
    """FastAPI 依赖：获取共享的 :class:`GoalAlignmentChecker` 实例。

    Returns:
        :class:`GoalAlignmentChecker` 单例，线程安全地懒初始化。

    Note:
        与重构前的 ``get_alignment_checker()`` 行为完全一致；可被
        ``Depends(get_alignment_checker)`` 注入到任意路由或服务函数中。
    """
    return _holder.get()


def set_alignment_checker(checker: GoalAlignmentChecker | None) -> None:
    """显式注入 :class:`GoalAlignmentChecker` 实例（用于测试或启动期初始化）。

    传入 ``None`` 等价于 :func:`reset_alignment_checker`。
    """
    if checker is None:
        _holder.reset()
        return
    with _holder._lock:
        _holder._instance = checker


def reset_alignment_checker() -> None:
    """清除已缓存的 :class:`GoalAlignmentChecker` 单例（主要用于测试）。"""
    _holder.reset()


@router.get("/goals/tree")
async def get_goal_tree():
    store = get_goal_chain_store()
    tree = store.get_goal_tree()
    return success(data=tree, message="Goal tree retrieved")


@router.get("/goals")
async def list_goals(
    level: str | None = Query(None, description="Filter by level: mission/strategic_goal/project/task"),
):
    store = get_goal_chain_store()
    lvl = GoalLevel(level) if level else None
    goals = store.get_all_goals(lvl)
    return success(data=[g.to_dict() for g in goals], message="Goals retrieved")


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str):
    store = get_goal_chain_store()
    goal = store.get_goal(goal_id)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")
    return success(data=goal.to_dict(), message="Goal retrieved")


@router.get("/goals/{goal_id}/chain")
async def get_goal_chain(goal_id: str):
    store = get_goal_chain_store()
    chain = store.resolve_goal_chain(goal_id)
    return success(data=[ref.to_dict() for ref in chain], message="Goal chain resolved")


@router.get("/goals/{goal_id}/children")
async def get_goal_children(goal_id: str):
    store = get_goal_chain_store()
    goal = store.get_goal(goal_id)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")
    children = store.get_children(goal_id)
    return success(data=[c.to_dict() for c in children], message="Children retrieved")


@router.get("/goals/{goal_id}/progress")
async def get_goal_progress(
    goal_id: str,
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    progress = checker.compute_goal_progress(goal_id)
    return success(data=progress.to_dict(), message="Progress computed")


@router.get("/goals/{goal_id}/history")
async def get_goal_history(goal_id: str, limit: int = Query(50, ge=1, le=100)):
    store = get_goal_chain_store()
    goal = store.get_goal(goal_id)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")
    versions = store.get_version_history(goal_id, limit)
    return success(data=[v.to_dict() for v in versions], message="Version history retrieved")


@router.post("/goals", dependencies=[Depends(require_permission("goal:write"))])
async def create_goal(request: GoalCreateRequest):
    # 将 Pydantic 模型转为 dict 以复用下方既有的 dict 访问逻辑，
    # 字段级校验已由 GoalCreateRequest 完成。
    data: dict[str, Any] = request.model_dump()
    store = get_goal_chain_store()

    try:
        level = GoalLevel(data.get("level", "task"))
        status = GoalStatus(data.get("status", "not_started"))
    except ValueError as e:
        # 修复：不再直接 str(e) 暴露原始异常文本，使用 safe_error_message
        # 统一包装并通过日志保留完整堆栈。
        safe = safe_error_message(e, context="goal_alignment.create_goal")
        logger.warning(
            "Invalid enum value in create_goal | error_id=%s | exc=%s",
            safe.get("error_id"),
            e,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=safe["message"],
        )

    goal_id = data.get("id", f"{level.value}-{uuid.uuid4().hex[:8]}")
    goal = Goal(
        id=goal_id,
        name=data.get("name", ""),
        description=data.get("description", ""),
        level=level,
        parent_id=data.get("parent_id"),
        status=status,
        created_at=time.time(),
    )

    if goal.level != GoalLevel.MISSION and goal.parent_id is None:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="Non-mission goals must have a parent_id",
        )

    existing = store.get_goal(goal_id)
    if existing:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Goal '{goal_id}' already exists")

    store.add_goal(goal)
    return success(data=goal.to_dict(), message="Goal created")


@router.put("/goals/{goal_id}", dependencies=[Depends(require_permission("goal:write"))])
async def update_goal(
    goal_id: str,
    data: dict[str, Any],
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    store = get_goal_chain_store()

    updatable = {}
    if "name" in data:
        updatable["name"] = data["name"]
    if "description" in data:
        updatable["description"] = data["description"]
    if "status" in data:
        try:
            updatable["status"] = GoalStatus(data["status"])
        except ValueError:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"Invalid status: {data['status']}",
            )
    if "parent_id" in data:
        updatable["parent_id"] = data["parent_id"]

    if not updatable:
        return error(code=ErrorCode.INVALID_REQUEST, message="No fields to update")

    goal = store.update_goal(goal_id, **updatable)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")

    if goal.status == GoalStatus.CANCELLED:
        checker.propagate_goal_change(goal_id)

    return success(data=goal.to_dict(), message="Goal updated")


@router.delete("/goals/{goal_id}", dependencies=[Depends(require_permission("goal:write"))])
async def delete_goal(goal_id: str):
    store = get_goal_chain_store()
    goal = store.get_goal(goal_id)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")
    if goal.level == GoalLevel.MISSION:
        return error(code=ErrorCode.INVALID_REQUEST, message="Cannot delete the mission")

    store.delete_goal(goal_id)
    return success(message="Goal deleted")


@router.post("/tasks", dependencies=[Depends(require_permission("goal:write"))])
async def create_task(
    data: dict[str, Any],
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    store = get_goal_chain_store()

    try:
        task_type = EnhancedTaskType(data["task_type"])
    except (KeyError, ValueError):
        valid = [t.value for t in EnhancedTaskType]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid task_type. Must be one of: {valid}",
        )

    parent_goal_id = data.get("parent_goal_id")
    if not parent_goal_id:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="parent_goal_id is required. All tasks must be associated with a parent goal.",
        )

    goal = store.get_goal(parent_goal_id)
    if goal is None:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Parent goal '{parent_goal_id}' not found",
        )

    chain = store.resolve_goal_chain(parent_goal_id)
    if not chain:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Cannot resolve goal chain for '{parent_goal_id}'",
        )

    task_id = data.get("id", f"task-{uuid.uuid4().hex[:8]}")
    task = EnhancedTask(
        id=task_id,
        title=data.get("title", ""),
        description=data.get("description", ""),
        task_type=task_type,
        goal_chain=chain,
        blockers=data.get("blockers", []),
        params=data.get("params"),
    )

    try:
        checker.validate_task_goal_chain(task)
    except GoalAlignmentError as e:
        # GoalAlignmentError 是业务级校验异常（用户可理解），直接透出文本是合理的；
        # 但仍用 safe_error_message 包裹，避免泄露内部堆栈。
        safe = safe_error_message(e, context="goal_alignment.validate_task")
        logger.info(
            "Task goal chain validation failed | task_id=%s | error_id=%s",
            task_id,
            safe.get("error_id"),
        )
        return error(code=ErrorCode.INVALID_REQUEST, message=safe["message"])

    checker.register_task(task)

    context = checker.build_task_context(task)

    return success(
        data={
            "task": task.to_dict(),
            "context": context,
        },
        message="Task created with goal alignment",
    )


@router.post("/tasks/{task_id}/status", dependencies=[Depends(require_permission("goal:write"))])
async def update_task_status(
    task_id: str,
    data: dict[str, Any],
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    try:
        new_status = EnhancedTaskStatus(data["status"])
    except (KeyError, ValueError):
        valid = [s.value for s in EnhancedTaskStatus]
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid status. Must be one of: {valid}",
        )

    if task_id not in checker._task_map:
        return error(code=ErrorCode.NOT_FOUND, message=f"Task '{task_id}' not found")

    task = checker._task_map[task_id]
    if not task.can_transition_to(new_status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Cannot transition from {task.status.value} to {new_status.value}",
        )

    if new_status == EnhancedTaskStatus.IN_PROGRESS and not task.are_blockers_resolved(
        set(tid for tid, t in checker._task_map.items() if t.status == EnhancedTaskStatus.COMPLETED)
    ):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Task has unresolved blockers: {task.blockers}",
        )

    checker.update_task_status(task_id, new_status)

    return success(
        data={"task_id": task_id, "status": new_status.value},
        message="Task status updated",
    )


@router.get("/tasks/{task_id}/context")
async def get_task_context(
    task_id: str,
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    if task_id not in checker._task_map:
        return error(code=ErrorCode.NOT_FOUND, message=f"Task '{task_id}' not found")

    task = checker._task_map[task_id]
    context = checker.build_task_context(task)
    return success(data=context, message="Task context retrieved")


@router.get("/tasks/{task_id}/alignment")
async def check_task_alignment(
    task_id: str,
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    if task_id not in checker._task_map:
        return error(code=ErrorCode.NOT_FOUND, message=f"Task '{task_id}' not found")

    task = checker._task_map[task_id]
    try:
        checker.validate_task_goal_chain(task)
        return success(
            data={
                "task_id": task_id,
                "aligned": True,
                "chain_length": len(task.goal_chain),
            },
            message="Task is properly aligned",
        )
    except GoalAlignmentError as e:
        # GoalAlignmentError 文本对前端用户是业务可读信息；
        # 通过 safe_error_message 统一包装便于后续统一脱敏/审计。
        safe = safe_error_message(e, context="goal_alignment.check_task_alignment")
        return success(
            data={"task_id": task_id, "aligned": False, "issue": safe["message"]},
            message="Task alignment issue found",
        )


@router.post("/scan", dependencies=[Depends(require_permission("goal:write"))])
async def run_alignment_scan(
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    result = checker.run_alignment_scan()
    return success(data=result, message="Alignment scan completed")


@router.get("/summary")
async def get_alignment_summary(
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    summary = checker.get_alignment_summary()
    return success(data=summary, message="Alignment summary retrieved")


@router.get("/progress/all")
async def get_all_progress(
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    progresses = checker.compute_all_progress()
    return success(data=[p.to_dict() for p in progresses], message="All progress computed")


@router.post("/goals/{goal_id}/propagate", dependencies=[Depends(require_permission("goal:write"))])
async def propagate_goal_change(
    goal_id: str,
    checker: GoalAlignmentChecker = Depends(get_alignment_checker),
):
    store = get_goal_chain_store()
    goal = store.get_goal(goal_id)
    if goal is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"Goal '{goal_id}' not found")

    result = checker.propagate_goal_change(goal_id)
    return success(data=result, message="Goal change propagated")
