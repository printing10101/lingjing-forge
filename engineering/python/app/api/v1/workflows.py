"""Workflow API - DAG 工作流编排 REST 接口 + SSE 事件流.

对应 ADR-005 阶段 1 / core-contracts-design.md 第 5 章。

端点总览：
    POST   /api/v1/workflows/validate          仅校验 WorkflowSpec，不执行
    POST   /api/v1/workflows/run               提交工作流（接收 WorkflowSpec JSON）
    POST   /api/v1/workflows/{run_id}/resume   断点续跑（spec 需与原 run 一致）
    GET    /api/v1/workflows/{run_id}          获取工作流状态（含节点状态）
    POST   /api/v1/workflows/{run_id}/cancel   取消工作流（下游未启动节点标记 SKIPPED）
    GET    /api/v1/workflows/{run_id}/stream   SSE 事件流（实时节点状态）
    GET    /api/v1/workflows                   工作流运行列表
    DELETE /api/v1/workflows/{run_id}          删除工作流运行记录

权限模型：
    workflow:read    —— 查询 / 列表 / SSE 订阅
    workflow:write   —— 提交 / 断点续跑
    workflow:manage  —— 取消 / 删除
"""

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.contracts.task import (
    Artifact,
    WorkflowEdge,
    WorkflowEvent,
    WorkflowNode,
    WorkflowSpec,
)
from app.workflow import (
    WorkflowValidationError,
    get_workflow_runner,
)
from app.workflow.dag_store import get_dag_store

logger = logging.getLogger(__name__)

# SSE 心跳超时（秒）：由 ``app.config.limits`` 集中管理，
# 与 jobs.py / lnn/services.py 共享同一基准值，避免不同 SSE 通道行为不一致。


router = APIRouter(
    prefix="/api/v1/workflows",
    tags=["Workflow Orchestration"],
    dependencies=[Depends(require_permission("workflow:read"))],
)


# Pydantic 请求模型（前端友好，避免直接暴露 dataclass）


class ArtifactModel(BaseModel):
    name: str
    type: str  # dataset | model | report | metrics | file
    uri: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowNodeModel(BaseModel):
    node_id: str
    task_type: str
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: dict[str, str] = Field(default_factory=dict)
    retry: int = 0
    timeout_seconds: int = 3600


class WorkflowEdgeModel(BaseModel):
    upstream: str
    downstream: str


class WorkflowSpecModel(BaseModel):
    """WorkflowSpec 的 API 入参模型。"""

    name: str
    version: str = "1.0.0"
    nodes: list[WorkflowNodeModel]
    edges: list[WorkflowEdgeModel] = Field(default_factory=list)
    inputs: dict[str, ArtifactModel] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RunRequestModel(BaseModel):
    """提交工作流请求体。"""

    spec: WorkflowSpecModel
    inputs: dict[str, ArtifactModel] | None = None  # 覆盖 spec.inputs
    owner_id: str | None = None


class ResumeRequestModel(BaseModel):
    """断点续跑请求体。"""

    spec: WorkflowSpecModel
    inputs: dict[str, ArtifactModel] | None = None
    owner_id: str | None = None


# 模型转换：Pydantic 契约 dataclass


def _artifact_from_model(m: ArtifactModel) -> Artifact:
    return Artifact(name=m.name, type=m.type, uri=m.uri, metadata=dict(m.metadata))


def _spec_from_model(model: WorkflowSpecModel) -> WorkflowSpec:
    """将 API 入参 model 转换为契约层 WorkflowSpec。"""
    nodes = [
        WorkflowNode(
            node_id=n.node_id,
            task_type=n.task_type,
            params=dict(n.params),
            inputs=dict(n.inputs),
            retry=n.retry,
            timeout_seconds=n.timeout_seconds,
        )
        for n in model.nodes
    ]
    edges = [WorkflowEdge(upstream=e.upstream, downstream=e.downstream) for e in model.edges]
    inputs = {k: _artifact_from_model(v) for k, v in model.inputs.items()}
    return WorkflowSpec(
        name=model.name,
        version=model.version,
        nodes=nodes,
        edges=edges,
        inputs=inputs,
        outputs=dict(model.outputs),
        metadata=dict(model.metadata),
    )


def _serialize_event(event: WorkflowEvent) -> str:
    """WorkflowEvent → SSE 文本帧。"""
    payload = {
        "workflow_run_id": event.workflow_run_id,
        "event_type": event.event_type,
        "node_id": event.node_id,
        "payload": event.payload,
        "timestamp": event.timestamp,
    }
    return f"event: {event.event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# 端点实现


@router.post("/validate")
async def validate_workflow(spec: WorkflowSpecModel):
    """仅校验 WorkflowSpec，不执行。返回校验错误列表（空表示通过）。"""
    try:
        contract_spec = _spec_from_model(spec)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Spec 构造失败: {e}")
    errors = contract_spec.validate()
    if errors:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="工作流校验失败",
            detail=errors,
        )
    return success(
        data={
            "valid": True,
            "node_count": len(contract_spec.nodes),
            "edge_count": len(contract_spec.edges),
        },
        message="工作流校验通过",
    )


@router.post(
    "/run",
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def run_workflow(request: RunRequestModel):
    """提交工作流，返回 workflow_run_id。"""
    try:
        spec = _spec_from_model(request.spec)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Spec 构造失败: {e}")

    inputs: dict[str, Artifact] | None = None
    if request.inputs:
        inputs = {k: _artifact_from_model(v) for k, v in request.inputs.items()}

    runner = await get_workflow_runner()
    try:
        workflow_run_id = await runner.run(
            spec,
            inputs=inputs,
            owner_id=request.owner_id,
        )
    except WorkflowValidationError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))

    return success(
        data={"workflow_run_id": workflow_run_id, "status": "running"},
        message="工作流已提交",
    )


@router.post(
    "/{workflow_run_id}/resume",
    dependencies=[Depends(require_permission("workflow:write"))],
)
async def resume_workflow(workflow_run_id: str, request: ResumeRequestModel):
    """断点续跑：从指定 workflow_run_id 继续，仅重跑 FAILED/PENDING 节点。"""
    try:
        spec = _spec_from_model(request.spec)
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=f"Spec 构造失败: {e}")

    inputs: dict[str, Artifact] | None = None
    if request.inputs:
        inputs = {k: _artifact_from_model(v) for k, v in request.inputs.items()}

    runner = await get_workflow_runner()
    try:
        new_run_id = await runner.run(
            spec,
            inputs=inputs,
            resume_from=workflow_run_id,
            owner_id=request.owner_id,
        )
    except WorkflowValidationError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except ValueError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"断点续跑失败: {e}",
        )

    return success(
        data={"workflow_run_id": new_run_id, "status": "resumed"},
        message="工作流已恢复",
    )


@router.get("/{workflow_run_id}")
async def get_workflow(workflow_run_id: str):
    """获取工作流运行状态（含各节点状态）。"""
    runner = await get_workflow_runner()
    status = await runner.get_status(workflow_run_id)
    if status.get("error"):
        return error(
            code=ErrorCode.NOT_FOUND,
            message=status["error"],
        )
    return success(data=status, message="工作流状态已获取")


@router.post(
    "/{workflow_run_id}/cancel",
    dependencies=[Depends(require_permission("workflow:manage"))],
)
async def cancel_workflow(workflow_run_id: str):
    """取消工作流。下游未启动节点标记为 SKIPPED。"""
    runner = await get_workflow_runner()
    cancelled = await runner.cancel(workflow_run_id)
    if not cancelled:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"无法取消工作流 '{workflow_run_id}'",
        )
    return success(
        data={"workflow_run_id": workflow_run_id, "status": "cancelled"},
        message="工作流已取消",
    )


@router.get("/{workflow_run_id}/stream")
async def stream_workflow_events(workflow_run_id: str):
    """SSE 事件流：实时推送节点状态变化。

    事件类型：
        - node_started / node_completed / node_failed / node_skipped
        - workflow_completed / workflow_failed / workflow_cancelled

    终态事件后流自动关闭。
    """
    runner = await get_workflow_runner()
    status = await runner.get_status(workflow_run_id)
    if status.get("error"):
        return error(
            code=ErrorCode.NOT_FOUND,
            message=status["error"],
        )

    async def event_generator():
        try:
            async for event in runner.subscribe(workflow_run_id):
                yield _serialize_event(event)
                if event.event_type in {
                    "workflow_completed",
                    "workflow_failed",
                    "workflow_cancelled",
                }:
                    return
        except asyncio.CancelledError:
            logger.info("SSE 流被取消: workflow_run_id=%s", workflow_run_id)
            raise
        except Exception as e:
            logger.exception("SSE 流异常: workflow_run_id=%s err=%s", workflow_run_id, e)
            err_payload = json.dumps(
                {"event_type": "stream_error", "error": str(e)},
                ensure_ascii=False,
            )
            yield f"event: stream_error\ndata: {err_payload}\n\n"
            # [H16] 发送错误事件后必须终止生成器，否则生成器会继续运行导致行为未定义
            return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("")
async def list_workflows(
    status_filter: str | None = Query(None, alias="status"),
    owner_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    """列出工作流运行记录。"""
    store = get_dag_store()
    runs = await store.list_runs(
        status=status_filter,
        owner_id=owner_id,
        limit=limit,
        offset=offset,
    )
    return success(
        data={
            "workflows": runs,
            "limit": limit,
            "offset": offset,
        },
        message="工作流列表已获取",
    )


@router.delete(
    "/{workflow_run_id}",
    dependencies=[Depends(require_permission("workflow:manage"))],
)
async def delete_workflow(workflow_run_id: str):
    """删除工作流运行记录（含节点状态）。"""
    store = get_dag_store()
    deleted = await store.delete_run(workflow_run_id)
    if not deleted:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"工作流 '{workflow_run_id}' 不存在",
        )
    return success(
        data={"workflow_run_id": workflow_run_id, "deleted": True},
        message="工作流已删除",
    )
