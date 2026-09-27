"""Dataset API - 数据集 / 版本 / 血缘 REST 接口.

对应 ADR-005 阶段 2 / core-contracts-design.md 第 4 章。

端点总览：
    GET    /api/v1/datasets                          数据集列表（分页 + 过滤）
    POST   /api/v1/datasets                          创建数据集
    GET    /api/v1/datasets/{dataset_id}             数据集详情（含 schema + 版本概要）
    GET    /api/v1/datasets/{dataset_id}/versions    列出所有版本
    POST   /api/v1/datasets/{dataset_id}/commit      提交一个不可变版本
    GET    /api/v1/datasets/{dataset_id}/read        读取版本内容（流式 JSONL）
    POST   /api/v1/datasets/{dataset_id}/deprecate   废弃某版本
    POST   /api/v1/datasets/lineage                  记录血缘
    GET    /api/v1/datasets/lineage/{target_uri}     查询血缘图（上游/下游可视化）

权限模型：
    dataset:read    —— 查询 / 列表 / 读取
    dataset:write   —— 创建 / 提交版本 / 记录血缘
    dataset:manage  —— 废弃版本
"""

from __future__ import annotations

import json
import logging
from typing import Any
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.auth import get_current_user
from app.auth.permissions import require_permission
from app.core.response import success
from app.contracts.dataset import DatasetSchema, DatasetStatus, LineageRecord
from app.dependencies import get_dataset_store
from app.data.lineage_store import get_lineage_store, make_lineage_record

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/api/v1/datasets",
    tags=["Dataset & Lineage"],
    dependencies=[Depends(require_permission("dataset:read"))],
)


class DatasetSchemaModel(BaseModel):
    """DatasetSchema 的 Pydantic 模型版本（用于 API JSON 传输）。"""

    model_config = ConfigDict(populate_by_name=True)

    fields: dict[str, dict[str, Any]]
    primary_key: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateDatasetRequest(BaseModel):
    """创建数据集请求体。"""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str = ""
    # 兼容两种输入名：schema（契约标准）与 dataset_schema（旧调用方）
    dataset_schema: DatasetSchemaModel = Field(alias="schema")
    owner_id: str


class CommitVersionRequest(BaseModel):
    """提交版本请求体。

    records 为空且 dataset_id 是 TrainingDataLake 适配器时，
    适配器会自动从 lake 加载当前全部 records。
    """

    records: list[dict[str, Any]] = Field(default_factory=list)
    version: str | None = None  # None 自动递增 patch
    # `from __future__ import annotations` 已启用，注解为惰性字符串。
    # 勿手写引号字符串 + `|`（如 "LineageModel" | None），Pydantic v2 会在
    # 模型定义时对 str|None 立即求值，报 unsupported operand for |: str/None。
    # 无引号写法由 model_rebuild()（本文件末尾）在 LineageModel 定义后解析。
    lineage: LineageModel | None = None


class LineageModel(BaseModel):
    """LineageRecord 的 API 入参模型。"""

    target: str
    source_type: str  # task / workflow / manual / external
    source_ref: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    operation: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


# 解决前向引用
CommitVersionRequest.model_rebuild()


# 模型转换


def _schema_from_model(m: DatasetSchemaModel) -> DatasetSchema:
    return DatasetSchema(
        fields=m.fields,
        primary_key=list(m.primary_key),
        metadata=dict(m.metadata),
    )


def _lineage_from_model(m: LineageModel) -> LineageRecord:
    return make_lineage_record(
        target=m.target,
        source_type=m.source_type,
        source_ref=m.source_ref,
        inputs=list(m.inputs),
        outputs=list(m.outputs),
        operation=m.operation,
        metadata=dict(m.metadata),
    )


def _version_to_dict(ver: Any) -> dict[str, Any]:
    """DatasetVersion dataclass → dict（用于响应序列化）."""
    return {
        "dataset_id": ver.dataset_id,
        "version": ver.version,
        "status": ver.status.value if isinstance(ver.status, DatasetStatus) else ver.status,
        "content_hash": ver.content_hash,
        "row_count": ver.row_count,
        "size_bytes": ver.size_bytes,
        "created_at": ver.created_at.isoformat() if ver.created_at else None,
        "created_by": ver.created_by,
        "storage_uri": ver.storage_uri,
        "lineage": ver.lineage,
    }


def _lineage_to_dict(rec: LineageRecord) -> dict[str, Any]:
    return {
        "record_id": rec.record_id,
        "target": rec.target,
        "source_type": rec.source_type,
        "source_ref": rec.source_ref,
        "inputs": list(rec.inputs),
        "outputs": list(rec.outputs),
        "operation": rec.operation,
        "timestamp": rec.timestamp.isoformat() if rec.timestamp else None,
        "metadata": dict(rec.metadata),
    }


# API 端点


@router.get("")
async def list_datasets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    owner_id: str | None = Query(None),
) -> dict:
    """List datasets with pagination."""
    dataset_store = get_dataset_store()
    items = await dataset_store.list_datasets(
        owner_id=owner_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    total = await dataset_store.count_datasets(owner_id=owner_id)
    return success(
        {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }
    )


@router.get("/metrics")
async def get_metrics() -> dict:
    """Get global dataset metrics."""
    dataset_store = get_dataset_store()
    metrics = await dataset_store.get_metrics()
    return success(metrics)


@router.post("")
async def create_dataset(
    req: CreateDatasetRequest,
    user=Depends(get_current_user),
) -> dict:
    """Create a dataset."""
    dataset_store = get_dataset_store()
    dataset_id = await dataset_store.create(
        name=req.name,
        schema=_schema_from_model(req.dataset_schema),
        owner_id=user["username"],
        description=req.description,
    )
    return success(
        {
            "id": dataset_id,
            "name": req.name,
            "description": req.description,
            "owner_id": user["username"],
        }
    )


@router.get("/{dataset_id}")
async def get_dataset(
    dataset_id: str,
) -> dict:
    """Get dataset details."""
    dataset_store = get_dataset_store()
    ds = await dataset_store.get_dataset(dataset_id)
    return success(ds)


@router.get("/{dataset_id}/versions")
async def list_versions(
    dataset_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    """List all versions."""
    dataset_store = get_dataset_store()
    versions = await dataset_store.list_versions(dataset_id)
    total = len(versions)
    start = (page - 1) * page_size
    items = [_version_to_dict(v) for v in versions[start : start + page_size]]
    return success(
        {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }
    )


@router.get("/{dataset_id}/versions/{version}")
async def get_version_detail(
    dataset_id: str,
    version: str,
) -> dict:
    """Get specific version details."""
    dataset_store = get_dataset_store()
    ver = await dataset_store.get_version(dataset_id, version)
    return success(_version_to_dict(ver))


@router.post("/{dataset_id}/commit")
async def commit_version(
    dataset_id: str,
    req: CommitVersionRequest,
    user=Depends(get_current_user),
) -> dict:
    """Commit a new version."""
    dataset_store = get_dataset_store()
    if req.lineage:
        lineage = _lineage_from_model(req.lineage)
    else:
        lineage = None

    ver = await dataset_store.commit_version(
        dataset_id=dataset_id,
        records=req.records,
        version=req.version,
        lineage=lineage,
    )
    return success(_version_to_dict(ver))


@router.get("/{dataset_id}/versions/{version}/read", response_class=StreamingResponse)
async def read_version(
    dataset_id: str,
    version: str,
) -> StreamingResponse:
    """Stream version contents as JSONL."""
    dataset_store = get_dataset_store()

    async def iterable() -> AsyncGenerator[str, None]:
        async for batch in dataset_store.read(dataset_id, version):
            for record in batch:
                yield json.dumps(record, ensure_ascii=False) + "\n"

    return StreamingResponse(
        iterable(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{dataset_id}_{version}.jsonl"'},
    )


@router.post("/{dataset_id}/deprecate")
async def deprecate_version(
    dataset_id: str,
    version: str = Query(None),
) -> dict:
    """Deprecate a version."""
    dataset_store = get_dataset_store()
    await dataset_store.deprecate(dataset_id=dataset_id, version=version)
    return success({"message": f"Version {version} deprecated"})


@router.post("/lineage")
async def record_lineage(
    req: LineageModel,
    user=Depends(get_current_user),
) -> dict:
    """Record lineage."""
    lineage_store = get_lineage_store()
    record = make_lineage_record(
        target=req.target,
        source_type=req.source_type,
        source_ref=req.source_ref,
        inputs=list(req.inputs),
        outputs=list(req.outputs),
        operation=req.operation,
        metadata=dict(req.metadata),
    )
    await lineage_store.record(record)
    return success({"record_id": record.record_id, "target": record.target})


@router.get("/lineage/{target_uri}")
async def query_lineage(
    target_uri: str,
    direction: str = Query("both", regex="^(upstream|downstream|both)$"),
) -> dict:
    """Query lineage graph."""
    lineage_store = get_lineage_store()
    upstream: list[LineageRecord] = []
    downstream: list[LineageRecord] = []
    if direction in ("upstream", "both"):
        upstream = await lineage_store.get_upstream(target_uri=target_uri)
    if direction in ("downstream", "both"):
        downstream = await lineage_store.get_downstream(target_uri=target_uri)
    return success(
        {
            "target": target_uri,
            "upstream": [_lineage_to_dict(r) for r in upstream],
            "downstream": [_lineage_to_dict(r) for r in downstream],
        }
    )
