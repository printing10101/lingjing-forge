"""
Agent State Management API Routes

Provides REST endpoints for:
- Agent state CRUD (list, get, save, delete)
- Checkpoint management (save, list, rollback)
- Context updates (incremental updates)
- Session recovery and resumption
- State cloning for A/B testing
- Checkpoint lifecycle management
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.v1.auth import get_current_user
from app.auth.permissions import check_user_has_permission
from app.core.response import success as api_response
from app.core.response_models import ErrorResponse, SuccessResponse
from app.state.state_persistence import StatePersistenceManager, StateRecoveryManager
from app.models.agent_state import (
    AgentState,
    AgentStatus,
    Checkpoint,
    CheckpointType,
    MemoryEntry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agents", tags=["Agent State Management"])


# [P0-17] 安全修复：Pydantic 请求模型替换 payload: dict 弱验证
# 防止 AgentState(agent_id=agent_id, **payload) 解包注入 created_at /
# updated_at / state_version / agent_id 等内部字段，以及 setattr 路径
# 篡改不该被 API 修改的字段。仅暴露业务可写字段。


class AgentStateSaveRequest(BaseModel):
    """保存 Agent 状态的请求体（白名单字段）。

    仅允许更新业务字段，agent_id / created_at / updated_at / state_version
    等内部字段由服务端管理，不接受客户端传入。
    """

    current_task_id: str | None = Field(None, description="当前任务ID")
    status: AgentStatus | None = Field(None, description="Agent 状态")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


# [P0-18] 安全修复：save_checkpoint / add_memory / rollback_checkpoint /
# update_context / clone_agent 端点 Pydantic 请求模型
# 替换 payload: dict 弱验证，约束字段类型并校验枚举值，防止
# CheckpointType(payload.get(...)) 接收非法值抛未处理 ValueError


class CheckpointSaveRequest(BaseModel):
    """保存 Checkpoint 的请求体（白名单字段）。

    checkpoint_id / created_at / file_size_bytes 由服务端管理，
    不接受客户端传入。checkpoint_type 通过枚举校验防止非法值。
    """

    epoch: int = Field(0, ge=0, description="训练轮次")
    step: int = Field(0, ge=0, description="训练步数")
    best_metric: float | None = Field(None, description="最佳指标值")
    best_metric_name: str = Field("loss", description="最佳指标名称")
    state_dict_path: str = Field("", description="状态字典存储路径")
    optimizer_state_path: str = Field("", description="优化器状态存储路径")
    rng_state: dict[str, Any] | None = Field(None, description="随机数生成器状态")
    checkpoint_type: CheckpointType = Field(CheckpointType.MANUAL, description="检查点类型")
    metrics: dict[str, Any] = Field(default_factory=dict, description="指标字典")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class MemoryEntryAddRequest(BaseModel):
    """添加 MemoryEntry 的请求体（白名单字段）。

    memory_id / created_at / last_accessed / access_count / embedding_ref
    由服务端管理。importance 约束在 [0, 1] 区间。
    """

    content: str = Field("", description="记忆内容")
    memory_type: str = Field("observation", description="记忆类型")
    importance: float = Field(0.5, ge=0.0, le=1.0, description="重要性权重 [0,1]")
    tags: list[str] = Field(default_factory=list, description="标签列表")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")


class CheckpointRollbackRequest(BaseModel):
    """回滚到指定 Checkpoint 的请求体。"""

    checkpoint_id: str = Field(..., description="目标检查点ID（必填）")


class ContextUpdateRequest(BaseModel):
    """更新 Agent 会话上下文的请求体。

    updates 为动态字典，由 :meth:`update_context_increment` 内部处理。
    允许直接传业务字段（无 updates 键时整体作为 updates）。
    """

    updates: dict[str, Any] = Field(default_factory=dict, description="上下文更新字典")


class AgentCloneRequest(BaseModel):
    """克隆 Agent 状态的请求体。"""

    target_agent_id: str = Field(..., description="目标 Agent ID（必填）")


class _AgentStateHolder:
    """Thread-safe holder for agent state managers (initialized externally)."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._persistence: StatePersistenceManager | None = None
        self._recovery: StateRecoveryManager | None = None

    def set_persistence(self, manager: StatePersistenceManager) -> None:
        """Set the persistence manager and (re)build the recovery manager."""
        with self._lock:
            self._persistence = manager
            self._recovery = StateRecoveryManager(manager)

    def get_persistence(self) -> StatePersistenceManager | None:
        with self._lock:
            return self._persistence

    def get_recovery(self) -> StateRecoveryManager | None:
        with self._lock:
            return self._recovery

    def reset(self) -> None:
        with self._lock:
            self._persistence = None
            self._recovery = None


_holder = _AgentStateHolder()


def get_persistence() -> StatePersistenceManager:
    """FastAPI 依赖：获取 :class:`StatePersistenceManager` 实例。

    Returns:
        :class:`StatePersistenceManager` 实例。

    Raises:
        HTTPException: 如果尚未通过 :func:`set_persistence_manager` 初始化则返回 503。
    """
    persistence = _holder.get_persistence()
    if persistence is None:
        raise HTTPException(status_code=503, detail="State persistence not initialized")
    return persistence


def get_recovery() -> StateRecoveryManager:
    """FastAPI 依赖：获取 :class:`StateRecoveryManager` 实例。

    Returns:
        :class:`StateRecoveryManager` 实例。

    Raises:
        HTTPException: 如果尚未通过 :func:`set_persistence_manager` 初始化则返回 503。
    """
    recovery = _holder.get_recovery()
    if recovery is None:
        raise HTTPException(status_code=503, detail="State recovery not initialized")
    return recovery


async def _require_perm(_user: dict, permission: str) -> None:
    """函数体内同步权限校验（替代误用的 FastAPI 依赖工厂调用）。"""
    import os as _os

    if _os.environ.get("LNN_PERMISSION_ENFORCED", "true").strip().lower() in ("0", "false", "no", "off"):
        return
    username = (_user or {}).get("username", "")
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not await check_user_has_permission(username, permission):
        raise HTTPException(status_code=403, detail=f"Insufficient permission: {permission}")


def set_persistence_manager(manager: StatePersistenceManager):
    """Initialize the agent state managers (typically called at app startup)."""
    _holder.set_persistence(manager)


@router.get(
    "/",
    response_model=SuccessResponse[list[Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_agents(
    status: str | None = Query(None, description="Filter by agent status"),
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:read")
    agents = await persistence.list_all_agent_states()
    if status:
        agents = [a for a in agents if a.get("status") == status]
    return api_response(data=agents)


@router.get(
    "/{agent_id}",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_agent_state(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:read")
    state = await persistence.load_state(agent_id)
    if not state:
        logger.info("Agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_response(data=state.to_dict())


@router.post(
    "/{agent_id}/save",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def save_agent_state(
    agent_id: str,
    payload: AgentStateSaveRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    # 仅取客户端实际提供且非 None 的字段，防止覆盖未传入字段的默认值
    update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
    state = await persistence.load_state(agent_id)
    if state:
        # 更新路径：仅 setattr 白名单字段（schema 已约束，update_data 仅含业务字段）
        for key, value in update_data.items():
            setattr(state, key, value)
    else:
        # 新建路径：仅用白名单字段构造，防止 **payload 注入内部字段
        state = AgentState(agent_id=agent_id, **update_data)
    await persistence.save_state(state, trigger="api_manual")
    return api_response(data=state.to_dict(), message="Agent state saved")


@router.delete(
    "/{agent_id}",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def delete_agent_state(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:admin")
    await persistence.delete_state(agent_id)
    return api_response(message=f"Agent '{agent_id}' state deleted")


@router.post(
    "/{agent_id}/heartbeat/start",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def start_heartbeat(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    await persistence.start_heartbeat(agent_id)
    return api_response(message=f"Heartbeat started for agent '{agent_id}'")


@router.post(
    "/{agent_id}/heartbeat/stop",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def stop_heartbeat(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    await persistence.stop_heartbeat(agent_id)
    return api_response(message=f"Heartbeat stopped for agent '{agent_id}'")


@router.post(
    "/{agent_id}/checkpoints/save",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def save_checkpoint(
    agent_id: str,
    payload: CheckpointSaveRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    # Pydantic schema 已校验字段类型与枚举值，model_dump 提取白名单字段
    data = payload.model_dump(exclude_unset=True)
    checkpoint = Checkpoint(
        epoch=data.get("epoch", 0),
        step=data.get("step", 0),
        best_metric=data.get("best_metric"),
        best_metric_name=data.get("best_metric_name", "loss"),
        state_dict_path=data.get("state_dict_path", ""),
        optimizer_state_path=data.get("optimizer_state_path", ""),
        rng_state=data.get("rng_state"),
        checkpoint_type=data.get("checkpoint_type", CheckpointType.MANUAL),
        metrics=data.get("metrics", {}),
        metadata=data.get("metadata", {}),
    )
    state = await persistence.save_checkpoint(agent_id, checkpoint, trigger="api")
    return api_response(data=state.to_dict(), message="Checkpoint saved")


@router.get(
    "/{agent_id}/checkpoints",
    response_model=SuccessResponse[list[Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def list_checkpoints(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:read")
    state = await persistence.load_state(agent_id)
    if not state:
        logger.info("Agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")
    checkpoints = [c.to_dict() for c in state.checkpoints_history]
    return api_response(data=checkpoints)


@router.post(
    "/{agent_id}/checkpoints/rollback",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def rollback_checkpoint(
    agent_id: str,
    payload: CheckpointRollbackRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    checkpoint_id = payload.checkpoint_id
    state = await persistence.load_state(agent_id)
    if not state:
        logger.info("Agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")
    success = state.rollback_to_checkpoint(checkpoint_id)
    if not success:
        logger.info("Checkpoint not found: %s", checkpoint_id)
        raise HTTPException(status_code=404, detail="Checkpoint not found")
    await persistence.save_state(state, trigger="rollback")
    return api_response(data=state.to_dict(), message=f"Rolled back to checkpoint '{checkpoint_id}'")


@router.post(
    "/{agent_id}/checkpoints/cleanup",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def cleanup_checkpoints(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:admin")
    removed = await persistence.cleanup_checkpoints(agent_id)
    return api_response(message=f"Cleaned up {removed} checkpoint files")


@router.post(
    "/{agent_id}/context/update",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def update_context(
    agent_id: str,
    payload: ContextUpdateRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    updates = payload.updates
    state = await persistence.update_context_increment(agent_id, updates)
    return api_response(data=state.to_dict(), message="Context updated")


@router.post(
    "/{agent_id}/memory/add",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def add_memory(
    agent_id: str,
    payload: MemoryEntryAddRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    state = await persistence.load_state(agent_id)
    if not state:
        state = AgentState(agent_id=agent_id)
    # Pydantic schema 已校验 importance 范围与字段类型
    entry = MemoryEntry(
        content=payload.content,
        memory_type=payload.memory_type,
        importance=payload.importance,
        tags=payload.tags,
        metadata=payload.metadata,
    )
    state.add_memory(entry)
    await persistence.save_state(state, trigger="memory_add")
    return api_response(data=state.to_dict(), message="Memory entry added")


@router.post(
    "/{agent_id}/memory/prune",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def prune_memory(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    state = await persistence.prune_memory(agent_id)
    return api_response(data=state.to_dict(), message="Memory pruned")


@router.post(
    "/{agent_id}/resume",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def resume_agent(
    agent_id: str,
    request: Request,
    persistence: StatePersistenceManager = Depends(get_persistence),
    recovery: StateRecoveryManager = Depends(get_recovery),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    result = await recovery.resume_agent(agent_id)
    return api_response(data=result)


@router.post(
    "/{agent_id}/deploy",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def deploy_agent(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    """部署 Agent：加载状态、标记部署时间并将状态切换为 busy（真实状态变更）。

    部署记录写入 Agent 元数据（deployed / deployed_at / deploy_version），
    状态持久化到后端存储。
    """
    await _require_perm(_user, "agents:write")
    state = await persistence.load_state(agent_id)
    if state is None:
        logger.info("Agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")

    state.metadata = {
        **(state.metadata or {}),
        "deployed": True,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deploy_version": (state.metadata or {}).get("state_version", "current"),
    }
    state.status = AgentStatus.BUSY
    await persistence.save_state(state)
    return api_response(
        data=state.to_dict(),
        message=f"Agent '{agent_id}' deployed successfully",
    )


@router.post(
    "/{agent_id}/clone",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def clone_agent(
    agent_id: str,
    payload: AgentCloneRequest,
    persistence: StatePersistenceManager = Depends(get_persistence),
    recovery: StateRecoveryManager = Depends(get_recovery),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    target_id = payload.target_agent_id
    clone = await recovery.clone_agent_state(agent_id, target_id)
    if not clone:
        logger.info("Source agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Source agent not found")
    return api_response(data=clone.to_dict(), message=f"Cloned agent '{agent_id}' to '{target_id}'")


@router.post(
    "/{agent_id}/snapshot",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def create_snapshot(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:write")
    key = await persistence.snapshot_for_rollback(agent_id)
    if not key:
        logger.info("Agent not found: %s", agent_id)
        raise HTTPException(status_code=404, detail="Agent not found")
    return api_response(message="Snapshot created", data={"snapshot_key": key})


@router.post(
    "/{agent_id}/rollback",
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def rollback_state(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:admin")
    state = await persistence.rollback_to_version(agent_id)
    if not state:
        raise HTTPException(status_code=404, detail="No rollback snapshot available")
    return api_response(data=state.to_dict(), message="Rollback successful")


@router.get(
    "/{agent_id}/history",
    response_model=SuccessResponse[list[Any]],
    responses={
        403: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_state_history(
    agent_id: str,
    persistence: StatePersistenceManager = Depends(get_persistence),
    recovery: StateRecoveryManager = Depends(get_recovery),
    _user: dict = Depends(get_current_user),
):
    await _require_perm(_user, "agents:read")
    history = await recovery.get_recovery_history(agent_id)
    return api_response(data=history)
