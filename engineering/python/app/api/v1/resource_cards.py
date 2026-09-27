"""Resource Cards API - 资源卡片（模型/数据集卡片）REST 接口.

对应 ADR-012 阶段 6 p6-3：资源卡片。

端点总览（prefix: ``/api/v1/resource-cards``）：
    GET    /datasets/{dataset_id}                       数据集卡片（聚合元数据 + README + lineage 摘要）
    PUT    /datasets/{dataset_id}/readme                更新数据集 README（upsert）
    GET    /datasets/{dataset_id}/lineage               数据集 lineage 摘要
    GET    /datasets/{dataset_id}/metrics               数据集指标（版本数/总行数/总大小）
    GET    /models                                      模型卡片列表（分页+过滤）
    POST   /models                                      注册新模型产物
    GET    /models/{model_id}                           模型卡片详情（聚合 + 快照 + lineage）
    PUT    /models/{model_id}                           更新模型卡片（readme/tags/status/metrics）
    DELETE /models/{model_id}                           删除模型卡片
    GET    /models/{model_id}/lineage                   模型 lineage 摘要
    GET    /models/{model_id}/metrics                   模型指标历史
    POST   /models/{model_id}/metrics                   追加模型指标记录

权限模型：
    resource_card:read   —— 查询卡片 / lineage 摘要 / 指标
    resource_card:write  —— 更新 README / 注册/更新/删除模型 / 追加指标
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.contracts.resource_card import (
    ModelArtifactStatus,
    ModelArtifactType,
)
from app.dependencies import get_resource_card_service
from app.services.resource_card_service import (
    DatasetReadmeNotFoundError,
    InvalidModelStatusTransitionError,
    LineageSummaryError,
    ModelArtifactAlreadyExistsError,
    ModelArtifactNotFoundError,
    ResourceCardError,
)

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/resource-cards",
    tags=["Resource Cards"],
    dependencies=[Depends(require_permission("resource_card:read"))],
)


# Pydantic 请求模型


class UpsertDatasetReadmeRequest(BaseModel):
    """更新数据集 README 请求体（upsert 语义）."""

    readme_md: str = Field(..., min_length=1, max_length=200000, description="markdown README 内容")
    updated_by: str = Field(..., min_length=1, max_length=128, description="最后更新者（user_id 或 plugin_id）")
    version: str | None = Field(
        None,
        description="版本号（如 1.0.0），不传则更新数据集级 README",
    )


class RegisterModelRequest(BaseModel):
    """注册新模型产物请求体."""

    model_uri: str = Field(
        ...,
        min_length=1,
        max_length=512,
        description="模型 URI（model://<name>/<version>），全局唯一",
    )
    name: str = Field(..., min_length=1, max_length=128, description="模型显示名")
    model_type: str = Field(
        ...,
        description=f"模型类型（{ModelArtifactType.all()}）",
    )
    version: str = Field(..., min_length=1, max_length=32, description="semver 版本号（如 1.0.0）")
    framework: str = Field(..., min_length=1, max_length=64, description="框架版本（如 torch-2.1.0）")
    storage_uri: str = Field(..., min_length=1, max_length=512, description="模型文件存储位置")
    owner_id: str = Field(..., min_length=1, max_length=128, description="所有者 ID")
    readme_md: str = Field(default="", max_length=200000, description="markdown README")
    tags: list[str] = Field(default_factory=list, description="标签数组")
    metrics: dict[str, Any] = Field(default_factory=dict, description="初始指标快照（如 accuracy/loss）")
    status: str = Field(
        default=ModelArtifactStatus.DRAFT,
        description=f"初始状态（{ModelArtifactStatus.all()}，默认 draft）",
    )


class UpdateModelRequest(BaseModel):
    """更新模型卡片请求体（部分更新，仅非 None 字段被写入）."""

    readme_md: str | None = Field(None, min_length=1, max_length=200000, description="markdown README")
    tags: list[str] | None = Field(None, description="标签数组")
    status: str | None = Field(
        None,
        description=f"目标状态（{ModelArtifactStatus.all()}，受状态机约束）",
    )
    metrics: dict[str, Any] | None = Field(
        None, description="覆盖当前指标快照（不会追加到 history，请用 POST /metrics 追加）"
    )
    framework: str | None = Field(None, min_length=1, max_length=64, description="框架版本")
    storage_uri: str | None = Field(None, min_length=1, max_length=512, description="模型文件存储位置")


class AppendModelMetricsRequest(BaseModel):
    """追加模型指标记录请求体."""

    metrics: dict[str, Any] = Field(..., description="指标字典（如 {'accuracy': 0.95, 'loss': 0.05}）")
    timestamp: str | None = Field(
        None,
        description="自定义时间戳（ISO8601），不传则使用服务器当前时间",
    )


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应（风格与 project_sync.py 对齐）.

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(e, ModelArtifactNotFoundError):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, DatasetReadmeNotFoundError):
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    if isinstance(e, ModelArtifactAlreadyExistsError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, InvalidModelStatusTransitionError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查当前状态允许的转换目标，或先 deprecate 再 archive",
        )
    if isinstance(e, LineageSummaryError):
        logger.warning("Lineage summary failed during %s: %s", action, e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=f"lineage 摘要构建失败：{e}",
            suggestion="检查 LineageStore 是否可用，或缩小 lineage_depth",
        )
    if isinstance(e, ValueError):
        # 参数校验失败（含 semver 非法、状态非法、URI 格式错误等）
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, ResourceCardError):
        logger.error("Resource card error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


# 数据集卡片端点（4 个）


@router.get("/datasets/{dataset_id}")
async def get_dataset_card(
    dataset_id: str,
    include_lineage: bool = Query(True, description="是否包含 lineage 摘要"),
    lineage_depth: int = Query(3, ge=1, le=10, description="lineage 摘要深度（1-10，默认 3）"),
):
    """获取数据集卡片（聚合元数据 + 最新版本指标 + README + lineage 摘要）.

    聚合内容：
        - Dataset 元数据（name / description / schema / owner / status）
        - version_count / total_rows / total_size_bytes（基于所有版本求和）
        - latest_version（最新版本的指标快照）
        - readme（优先版本级，回退数据集级；可能为 null）
        - lineage_summary（按层分组的上下游摘要；include_lineage=False 时为 null）

    权限：``resource_card:read``
    """
    service = get_resource_card_service()
    try:
        card = await service.get_dataset_card(
            dataset_id,
            include_lineage=include_lineage,
            lineage_depth=lineage_depth,
        )
    except Exception as e:
        return _handle_service_exception(e, action="获取数据集卡片")

    return success(data=card.to_dict(), message="数据集卡片已获取")


@router.put(
    "/datasets/{dataset_id}/readme",
    dependencies=[Depends(require_permission("resource_card:write"))],
)
async def upsert_dataset_readme(
    dataset_id: str,
    request: UpsertDatasetReadmeRequest,
):
    """更新数据集 README（upsert 语义：不存在则创建，存在则覆盖）.

    - 请求体 ``version`` 不传或为 null → 更新数据集级 README（dataset_id 唯一）
    - 请求体 ``version`` 指定版本号 → 更新版本级 README（dataset_id + version 唯一）

    权限：``resource_card:write``
    """
    service = get_resource_card_service()
    try:
        readme = await service.upsert_dataset_readme(
            dataset_id,
            request.readme_md,
            request.updated_by,
            version=request.version,
        )
    except Exception as e:
        return _handle_service_exception(e, action="更新数据集 README")

    return success(
        data=readme.to_dict(),
        message=(f"数据集 README 已更新（dataset={dataset_id}, version={request.version or '<dataset_level>'}）"),
    )


@router.get("/datasets/{dataset_id}/lineage")
async def get_dataset_lineage(
    dataset_id: str,
    version: str | None = Query(
        None,
        description="版本号（如 1.0.0），不传则使用最新 published 版本",
    ),
    depth: int = Query(3, ge=1, le=10, description="lineage 深度（1-10，默认 3）"),
    max_nodes_per_layer: int = Query(10, ge=1, le=100, description="每层保留的最大节点数（1-100，默认 10）"),
):
    """获取数据集的 lineage 摘要（按层分组 + 关键路径）.

    返回字段（LineageSummary）：
        - target_uri / upstream_count / downstream_count / total_nodes
        - upstream_layers / downstream_layers（list[list[str]]，按层分组）
        - key_path（target → 根节点的最短路径）

    权限：``resource_card:read``
    """
    from app.dependencies import get_dataset_store

    service = get_resource_card_service()

    # 推导 target_uri
    try:
        if version is None:
            dataset_store = get_dataset_store()
            detail = await dataset_store.get_dataset(dataset_id)
            versions = detail.get("versions", [])
            if not versions:
                return error(
                    code=ErrorCode.NOT_FOUND,
                    message=f"数据集无可用版本: {dataset_id}",
                )
            target_version = versions[0].get("version", "latest")
        else:
            target_version = version
        target_uri = f"dataset://{dataset_id}/{target_version}"
    except Exception as e:
        return _handle_service_exception(e, action="解析数据集版本")

    try:
        summary = await service.get_lineage_summary(
            target_uri,
            max_depth=depth,
            max_nodes_per_layer=max_nodes_per_layer,
        )
    except Exception as e:
        return _handle_service_exception(e, action="获取数据集 lineage 摘要")

    return success(data=summary.to_dict(), message="数据集 lineage 摘要已获取")


@router.get("/datasets/{dataset_id}/metrics")
async def get_dataset_metrics(dataset_id: str):
    """获取数据集指标（版本数 / 总行数 / 总大小 / 各版本明细）.

    返回字段：
        - dataset_id / name / owner_id / status
        - version_count / total_rows / total_size_bytes
        - versions: list[dict]（每个版本的 row_count / size_bytes / content_hash / created_at）

    权限：``resource_card:read``
    """
    from app.dependencies import get_dataset_store

    try:
        dataset_store = get_dataset_store()
        detail = await dataset_store.get_dataset(dataset_id)
    except Exception as e:
        return _handle_service_exception(e, action="获取数据集指标")

    versions = detail.get("versions", [])
    metrics_payload = {
        "dataset_id": detail.get("id", dataset_id),
        "name": detail.get("name", ""),
        "owner_id": detail.get("owner_id", ""),
        "status": detail.get("status", "draft"),
        "version_count": len(versions),
        "total_rows": sum(v.get("row_count", 0) for v in versions),
        "total_size_bytes": sum(v.get("size_bytes", 0) for v in versions),
        "versions": [
            {
                "version": v.get("version"),
                "status": v.get("status"),
                "row_count": v.get("row_count", 0),
                "size_bytes": v.get("size_bytes", 0),
                "content_hash": v.get("content_hash", ""),
                "created_at": v.get("created_at"),
                "created_by": v.get("created_by", ""),
            }
            for v in versions
        ],
    }

    return success(data=metrics_payload, message="数据集指标已获取")


# 模型卡片端点（8 个）


@router.get("/models")
async def list_models(
    owner_id: str | None = Query(None, description="按所有者过滤"),
    model_type: str | None = Query(
        None,
        description=f"按模型类型过滤（{ModelArtifactType.all()}）",
    ),
    status: str | None = Query(
        None,
        description=f"按状态过滤（{ModelArtifactStatus.all()}）",
    ),
    tag: str | None = Query(None, description="按标签过滤（精确匹配）"),
    limit: int = Query(100, ge=1, le=1000, description="每页数量（1-1000）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出模型产物（支持 owner/type/status/tag 过滤）.

    返回字段：
        - items: list[ModelArtifact.to_dict()]
        - total / limit / offset

    权限：``resource_card:read``
    """
    # 前置校验：model_type 合法性
    if model_type is not None and not ModelArtifactType.is_valid(model_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"model_type 不支持: {model_type}（支持: {ModelArtifactType.all()}）",
        )
    if status is not None and not ModelArtifactStatus.is_valid(status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"status 不支持: {status}（支持: {ModelArtifactStatus.all()}）",
        )

    service = get_resource_card_service()
    try:
        result = await service.list_models(
            owner_id=owner_id,
            model_type=model_type,
            status=status,
            tag=tag,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出模型产物")

    # 序列化 items
    payload = {
        "items": [m.to_dict() for m in result["items"]],
        "total": result["total"],
        "limit": result["limit"],
        "offset": result["offset"],
    }
    return success(data=payload, message="模型产物列表已获取")


@router.post(
    "/models",
    dependencies=[Depends(require_permission("resource_card:write"))],
)
async def register_model(request: RegisterModelRequest):
    """注册新模型产物.

    - 校验 model_uri 全局唯一（重复抛 ModelArtifactAlreadyExistsError）
    - 校验 model_type / version / status 合法性（契约层 __post_init__）
    - 写入 DB，返回完整的 ModelArtifact dataclass

    权限：``resource_card:write``
    """
    # 前置校验：model_type / status 合法性
    if not ModelArtifactType.is_valid(request.model_type):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"model_type 不支持: {request.model_type}（支持: {ModelArtifactType.all()}）",
        )
    if not ModelArtifactStatus.is_valid(request.status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"status 不支持: {request.status}（支持: {ModelArtifactStatus.all()}）",
        )

    service = get_resource_card_service()
    try:
        artifact = await service.register_model(
            model_uri=request.model_uri,
            name=request.name,
            model_type=request.model_type,
            version=request.version,
            framework=request.framework,
            storage_uri=request.storage_uri,
            owner_id=request.owner_id,
            readme_md=request.readme_md,
            tags=request.tags,
            metrics=request.metrics,
            status=request.status,
        )
    except Exception as e:
        return _handle_service_exception(e, action="注册模型产物")

    return success(
        data=artifact.to_dict(),
        message=f"模型产物已注册: {artifact.name} v{artifact.version}",
    )


@router.get("/models/{model_id}")
async def get_model_card(
    model_id: str,
    include_lineage: bool = Query(True, description="是否包含 lineage 摘要"),
    lineage_depth: int = Query(3, ge=1, le=10, description="lineage 摘要深度（1-10，默认 3）"),
):
    """获取模型卡片详情（聚合 ModelArtifact + Snapshot 数 + lineage 摘要）.

    聚合内容：
        - model: ModelArtifact.to_dict()
        - snapshot_count: 关联的实验快照数
        - latest_snapshot: 最近一次实验快照（可能为 null）
        - lineage_summary: 按层分组的上下游摘要（include_lineage=False 时为 null）

    权限：``resource_card:read``
    """
    service = get_resource_card_service()
    try:
        card = await service.get_model_card(
            model_id,
            include_lineage=include_lineage,
            lineage_depth=lineage_depth,
        )
    except Exception as e:
        return _handle_service_exception(e, action="获取模型卡片")

    return success(data=card.to_dict(), message="模型卡片已获取")


@router.put(
    "/models/{model_id}",
    dependencies=[Depends(require_permission("resource_card:write"))],
)
async def update_model(
    model_id: str,
    request: UpdateModelRequest,
):
    """更新模型卡片字段（部分更新，仅非 None 字段被写入）.

    可更新字段：
        - readme_md / tags / metrics / framework / storage_uri
        - status（受状态机约束：draft→published→deprecated→archived）

    权限：``resource_card:write``
    """
    # 前置校验：status 合法性
    if request.status is not None and not ModelArtifactStatus.is_valid(request.status):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"status 不支持: {request.status}（支持: {ModelArtifactStatus.all()}）",
        )

    service = get_resource_card_service()
    try:
        artifact = await service.update_model(
            model_id,
            readme_md=request.readme_md,
            tags=request.tags,
            status=request.status,
            metrics=request.metrics,
            framework=request.framework,
            storage_uri=request.storage_uri,
        )
    except Exception as e:
        return _handle_service_exception(e, action="更新模型卡片")

    return success(
        data=artifact.to_dict(),
        message=f"模型卡片已更新: {artifact.name} v{artifact.version}",
    )


@router.delete(
    "/models/{model_id}",
    dependencies=[Depends(require_permission("resource_card:write"))],
)
async def delete_model(model_id: str):
    """删除模型卡片（同时删除关联的指标历史）.

    设计原则：
        - 不会删除 storage_uri 指向的实际模型文件（仅删除 DB 元数据）
        - 不会删除关联的 ExperimentSnapshot（snapshot 自有生命周期）
        - 删除后不可恢复，调用方需在前端二次确认

    权限：``resource_card:write``
    """
    service = get_resource_card_service()
    try:
        await service.delete_model(model_id)
    except Exception as e:
        return _handle_service_exception(e, action="删除模型卡片")

    return success(data={"model_id": model_id, "deleted": True}, message="模型卡片已删除")


@router.get("/models/{model_id}/lineage")
async def get_model_lineage(
    model_id: str,
    depth: int = Query(3, ge=1, le=10, description="lineage 深度（1-10，默认 3）"),
    max_nodes_per_layer: int = Query(10, ge=1, le=100, description="每层保留的最大节点数（1-100，默认 10）"),
):
    """获取模型的 lineage 摘要（按层分组 + 关键路径）.

    target_uri 取模型的 model_uri（model://<name>/<version>）。

    权限：``resource_card:read``
    """
    service = get_resource_card_service()
    try:
        artifact = await service.get_model(model_id)
        summary = await service.get_lineage_summary(
            artifact.model_uri,
            max_depth=depth,
            max_nodes_per_layer=max_nodes_per_layer,
        )
    except Exception as e:
        return _handle_service_exception(e, action="获取模型 lineage 摘要")

    return success(data=summary.to_dict(), message="模型 lineage 摘要已获取")


@router.get("/models/{model_id}/metrics")
async def get_model_metrics(model_id: str):
    """获取模型指标历史（追加式记录列表）.

    返回字段：
        - model_id / model_uri / name / version
        - current_metrics: 当前指标快照（最近一次 append 或 update 的结果）
        - metrics_history: list[dict]（按时间升序，每条含 timestamp + metrics）

    权限：``resource_card:read``
    """
    service = get_resource_card_service()
    try:
        artifact = await service.get_model(model_id)
    except Exception as e:
        return _handle_service_exception(e, action="获取模型指标历史")

    payload = {
        "model_id": artifact.model_id,
        "model_uri": artifact.model_uri,
        "name": artifact.name,
        "version": artifact.version,
        "current_metrics": dict(artifact.metrics),
        "metrics_history": list(artifact.metrics_history),
    }
    return success(data=payload, message="模型指标历史已获取")


@router.post(
    "/models/{model_id}/metrics",
    dependencies=[Depends(require_permission("resource_card:write"))],
)
async def append_model_metrics(
    model_id: str,
    request: AppendModelMetricsRequest,
):
    """追加一条指标记录到模型历史（同时更新当前指标快照）.

    - 新记录追加到 metrics_history（按时间升序）
    - 当前 metrics 字段被覆盖为本次提交的 metrics
    - 若指定 timestamp（ISO8601），按指定时间记录；否则使用服务器当前时间

    权限：``resource_card:write``
    """
    from datetime import datetime

    # 解析可选 timestamp
    timestamp: datetime | None = None
    if request.timestamp:
        try:
            timestamp = datetime.fromisoformat(request.timestamp)
        except ValueError as e:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"timestamp 不是合法 ISO8601 格式: {request.timestamp}",
                detail=str(e),
            )

    service = get_resource_card_service()
    try:
        artifact = await service.append_model_metrics(
            model_id,
            request.metrics,
            timestamp=timestamp,
        )
    except Exception as e:
        return _handle_service_exception(e, action="追加模型指标记录")

    return success(
        data={
            "model_id": artifact.model_id,
            "current_metrics": dict(artifact.metrics),
            "metrics_history_len": len(artifact.metrics_history),
        },
        message=f"指标已追加（历史共 {len(artifact.metrics_history)} 条）",
    )


__all__ = ["router"]
