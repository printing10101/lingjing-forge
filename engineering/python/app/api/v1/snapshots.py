"""Snapshot API - 实验快照 / 一键复现 REST 接口.

对应 ADR-005 阶段 2 / core-contracts-design.md 第 7 章。

端点总览：
    GET    /api/v1/snapshots                          快照列表（按 created_at 倒序，支持过滤）
    POST   /api/v1/snapshots                          创建快照（自动采集 git_sha + environment）
    GET    /api/v1/snapshots/{snapshot_id}            快照详情（含完整 config / metrics / environment）
    POST   /api/v1/snapshots/{snapshot_id}/reproduce  一键复现（根据 snapshot.config['workflow_spec'] 启动新工作流运行）

权限模型：
    snapshot:read      —— 查询 / 列表 / 详情
    snapshot:write     —— 创建快照
    snapshot:reproduce —— 触发一键复现
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.observability.snapshot import get_snapshot_store

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/snapshots",
    tags=["Experiment Snapshot"],
    dependencies=[Depends(require_permission("snapshot:read"))],
)


# Pydantic 请求 / 响应模型


class CreateSnapshotRequest(BaseModel):
    """创建实验快照请求体。

    config 中可包含 ``workflow_spec`` 字段（dict 形式的 WorkflowSpec），
    用于支持后续一键复现。其余字段由调用方按实验实际填写。
    """

    config: dict[str, Any] = Field(
        default_factory=dict,
        description="实验配置，可包含 workflow_spec / hyperparams / seed 等",
    )
    dataset_versions: list[str] = Field(
        default_factory=list,
        description="关联的数据集版本 URI 列表（dataset://<name>/<version>）",
    )
    model_uri: str = Field(..., description="模型 URI，如 model://ltc/1.0.0")
    metrics: dict[str, float] = Field(
        default_factory=dict,
        description="实验指标，如 {'mae': 0.123, 'r2': 0.956}",
    )
    created_by: str = Field(..., description="创建者标识（用户 ID 或 agent ID）")
    notes: str = Field(default="", description="备注信息")


# 模型转换


def _snapshot_to_dict(snap: Any) -> dict[str, Any]:
    """ExperimentSnapshot dataclass → dict（用于响应序列化）."""
    return {
        "snapshot_id": snap.snapshot_id,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
        "created_by": snap.created_by,
        "git_sha": snap.git_sha,
        "code_dirty": snap.code_dirty,
        "config": dict(snap.config) if snap.config else {},
        "dataset_versions": list(snap.dataset_versions) if snap.dataset_versions else [],
        "model_uri": snap.model_uri,
        "metrics": dict(snap.metrics) if snap.metrics else {},
        "environment": dict(snap.environment) if snap.environment else {},
        "lineage_record_id": snap.lineage_record_id,
        "mlflow_run_id": snap.mlflow_run_id,
        "notes": snap.notes or "",
    }


def _snapshot_summary(snap: Any) -> dict[str, Any]:
    """ExperimentSnapshot → 轻量摘要（用于列表视图，避免 config 全量序列化）."""
    return {
        "snapshot_id": snap.snapshot_id,
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
        "created_by": snap.created_by,
        "git_sha": snap.git_sha,
        "code_dirty": snap.code_dirty,
        "model_uri": snap.model_uri,
        "metrics": dict(snap.metrics) if snap.metrics else {},
        "mlflow_run_id": snap.mlflow_run_id,
        "notes": (snap.notes or "")[:120],  # 列表中截断长备注
    }


# 端点实现


@router.get("")
async def list_snapshots(
    created_by: str | None = Query(None, description="按创建者过滤"),
    git_sha: str | None = Query(None, description="按 git SHA 过滤"),
    model_uri: str | None = Query(None, description="按模型 URI 过滤（精确匹配）"),
    detail: bool = Query(False, description="true 返回完整字段，false 返回摘要"),
):
    """列出实验快照（按 created_at 倒序）。"""
    store = get_snapshot_store()
    filters: dict[str, Any] = {}
    if created_by is not None:
        filters["created_by"] = created_by
    if git_sha is not None:
        filters["git_sha"] = git_sha
    if model_uri is not None:
        filters["model_uri"] = model_uri

    try:
        snapshots = await store.list(filters=filters if filters else None)
    except Exception as e:
        logger.exception("list_snapshots 失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="查询实验快照列表失败",
            detail=str(e),
        )

    serialize = _snapshot_to_dict if detail else _snapshot_summary
    items = [serialize(s) for s in snapshots]
    return success(data={"items": items, "count": len(items)})


@router.post("", dependencies=[Depends(require_permission("snapshot:write"))])
async def create_snapshot(req: CreateSnapshotRequest):
    """创建实验快照（自动采集 git_sha 与环境信息）。"""
    store = get_snapshot_store()
    try:
        snap = await store.create(
            config=dict(req.config),
            dataset_versions=list(req.dataset_versions),
            model_uri=req.model_uri,
            metrics=dict(req.metrics),
            created_by=req.created_by,
            notes=req.notes,
        )
    except ValueError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except Exception as e:
        logger.exception("create_snapshot 失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="创建实验快照失败",
            detail=str(e),
        )
    return success(data=_snapshot_to_dict(snap), message="实验快照已创建")


@router.get("/{snapshot_id}")
async def get_snapshot(snapshot_id: str):
    """获取快照详情（含完整 config / metrics / environment）."""
    store = get_snapshot_store()
    try:
        snap = await store.get(snapshot_id)
    except KeyError as e:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=str(e),
            detail=f"snapshot_id={snapshot_id}",
        )
    except Exception as e:
        logger.exception("get_snapshot 失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="查询实验快照详情失败",
            detail=str(e),
        )
    return success(data=_snapshot_to_dict(snap))


@router.post(
    "/{snapshot_id}/reproduce",
    dependencies=[Depends(require_permission("snapshot:reproduce"))],
)
async def reproduce_snapshot(snapshot_id: str):
    """根据快照一键复现：重建 WorkflowSpec 并启动新工作流运行。

    Returns:
        data.workflow_run_id: 复现工作流的运行 ID（可用于订阅 SSE 事件）
    """
    store = get_snapshot_store()
    try:
        workflow_run_id = await store.reproduce(snapshot_id)
    except KeyError as e:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=str(e),
            detail=f"snapshot_id={snapshot_id}",
        )
    except NotImplementedError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="该快照不支持一键复现",
            detail=str(e),
            suggestion="请在创建快照时将完整 WorkflowSpec 序列化到 config['workflow_spec']",
        )
    except ValueError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="workflow_spec 反序列化失败",
            detail=str(e),
        )
    except Exception as e:
        logger.exception("reproduce_snapshot 失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="一键复现失败",
            detail=str(e),
        )
    return success(
        data={"workflow_run_id": workflow_run_id, "snapshot_id": snapshot_id},
        message="复现工作流已启动",
    )


__all__ = ["router"]
