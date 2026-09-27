"""Workflow Template Marketplace API - 工作流模板市场 REST 接口.

对应 ADR-010 阶段 6 p6-1：工作流模板市场。

端点总览：
    POST   /api/v1/workflow-templates/publish            发布模板（新模板或新版本）
    GET    /api/v1/workflow-templates                    模板列表（分页/过滤/排序）
    GET    /api/v1/workflow-templates/search             关键词搜索
    GET    /api/v1/workflow-templates/stats              市场全局统计
    GET    /api/v1/workflow-templates/{template_id}      模板详情（可选 version 查询参数）
    GET    /api/v1/workflow-templates/{template_id}/versions   模板的所有版本列表
    GET    /api/v1/workflow-templates/{template_id}/download   下载模板（自增下载计数）
    POST   /api/v1/workflow-templates/{template_id}/rate       评分（1.0-5.0）
    POST   /api/v1/workflow-templates/{template_id}/unpublish  下架模板（管理员）

权限模型：
    workflow_template:read    —— 查询 / 列表 / 搜索 / 详情 / 下载 / 版本列表 / 统计
    workflow_template:publish —— 发布
    workflow_template:rate    —— 评分
    workflow_template:manage  —— 下架

路由顺序注意：
    /stats 和 /{template_id} 都是 GET，FastAPI 按定义顺序匹配，
    所以 /stats 与 /search 必须在 /{template_id} 之前定义。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dependencies import get_workflow_template_service
from app.plugins.workflow_template_loader import (
    TemplateValidationError,
    load_template_from_dict,
)
from app.services.workflow_template_service import (
    InvalidVersionError,
    TemplateAlreadyExistsError,
    TemplateNotFoundError,
    VersionAlreadyExistsError,
)

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(prefix="/api/v1/workflow-templates", tags=["Workflow Templates"])


# Pydantic 请求模型


class WorkflowSpecModel(BaseModel):
    """WorkflowSpec 的 API 入参（与 workflows.py 对齐，但简化为 dict 投影）.

    模板市场的 spec 字段允许任意结构，由 WorkflowTemplateManifest 校验
    必须包含 nodes。
    """

    name: str
    version: str = "1.0.0"
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublishRequestModel(BaseModel):
    """发布工作流模板请求体.

    template_dict 必须满足 workflow_template.yaml 的 schema（见
    workflow_template_loader.validate_template_dict），含 id / name /
    version / description / author / license / spec 等字段。
    """

    template_dict: dict[str, Any] = Field(..., description="模板 manifest 字典（template.yaml 的反序列化形式）")
    changelog: str = Field(default="", description="版本变更说明")


class RateRequestModel(BaseModel):
    """评分请求体."""

    rating: float = Field(..., ge=1.0, le=5.0, description="评分（1.0-5.0）", examples=[4.5])


# 端点实现


@router.post(
    "/publish",
    dependencies=[Depends(require_permission("workflow_template:publish"))],
)
async def publish_template(request: PublishRequestModel):
    """发布工作流模板（新模板或新版本）.

    - 首次发布：创建主表记录 + 版本记录
    - 已存在：创建新版本记录（version 必须不同于 latest_version）
    - 模板 manifest 由 workflow_template_loader 校验后构造
    """
    service = get_workflow_template_service()

    try:
        manifest = load_template_from_dict(request.template_dict)
    except TemplateValidationError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="模板 manifest 校验失败",
            detail=e.errors if hasattr(e, "errors") else [str(e)],
        )

    try:
        result = await service.publish(manifest, changelog=request.changelog)
    except TemplateAlreadyExistsError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except VersionAlreadyExistsError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except InvalidVersionError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except RuntimeError as e:
        logger.error("Publish workflow template failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Publish workflow template failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="发布工作流模板失败",
            detail=str(e),
        )

    return success(
        data=result,
        message="新模板已发布" if result.get("is_new_template") else "新版本已发布",
    )


@router.get("")
async def list_templates(
    category: str | None = Query(None, description="分类过滤"),
    tag: str | None = Query(None, description="标签过滤（精确匹配）"),
    author: str | None = Query(None, description="作者过滤"),
    limit: int = Query(50, ge=1, le=100, description="每页数量"),
    offset: int = Query(0, ge=0, description="偏移量"),
    sort_by: str = Query(
        "downloads",
        description="排序字段（downloads / avg_rating / created_at / updated_at）",
    ),
):
    """分页列出模板（支持分类/标签/作者过滤，多种排序）."""
    service = get_workflow_template_service()
    try:
        result = await service.list_templates(
            category=category,
            tag=tag,
            author=author,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
        )
    except InvalidVersionError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except RuntimeError as e:
        logger.error("List workflow templates failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("List workflow templates failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="列出工作流模板失败",
            detail=str(e),
        )

    return success(data=result, message="工作流模板列表已获取")


@router.get("/search")
async def search_templates(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(50, ge=1, le=100, description="返回数量上限"),
):
    """关键词搜索模板（name / description / tags / author 模糊匹配）."""
    service = get_workflow_template_service()
    try:
        result = await service.search(query=q, limit=limit)
    except RuntimeError as e:
        logger.error("Search workflow templates failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Search workflow templates failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="搜索工作流模板失败",
            detail=str(e),
        )

    return success(data=result, message="搜索完成")


@router.get("/stats")
async def market_stats():
    """市场全局统计（模板总数 / 总下载 / 平均评分）.

    路由顺序注意：必须定义在 /{template_id} 之前，否则 'stats' 会被
    识别为 template_id 参数。
    """
    service = get_workflow_template_service()
    try:
        result = await service.market_stats()
    except RuntimeError as e:
        logger.error("Market stats failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Market stats failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="获取市场统计失败",
            detail=str(e),
        )

    return success(data=result, message="市场统计已获取")


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    version: str | None = Query(None, description="版本号（None 表示最新版本）"),
):
    """获取模板详情（含指定版本的 manifest + spec）."""
    service = get_workflow_template_service()
    try:
        result = await service.get_template(template_id, version=version)
    except TemplateNotFoundError as e:
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    except RuntimeError as e:
        logger.error("Get workflow template failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Get workflow template failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="获取工作流模板详情失败",
            detail=str(e),
        )

    return success(data=result, message="工作流模板详情已获取")


@router.get("/{template_id}/versions")
async def list_versions(template_id: str):
    """列出某模板的所有版本（按创建时间倒序）."""
    service = get_workflow_template_service()
    try:
        result = await service.list_versions(template_id)
    except TemplateNotFoundError as e:
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    except RuntimeError as e:
        logger.error("List versions failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("List versions failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="获取版本列表失败",
            detail=str(e),
        )

    return success(data=result, message="版本列表已获取")


@router.get("/{template_id}/download")
async def download_template(
    template_id: str,
    version: str | None = Query(None, description="版本号（None 表示最新版本）"),
):
    """下载模板（自增下载计数，返回完整 manifest + spec）."""
    service = get_workflow_template_service()
    try:
        result = await service.download(template_id, version=version)
    except TemplateNotFoundError as e:
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    except RuntimeError as e:
        logger.error("Download workflow template failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Download workflow template failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="下载工作流模板失败",
            detail=str(e),
        )

    return success(data=result, message="工作流模板已下载")


@router.post(
    "/{template_id}/rate",
    dependencies=[Depends(require_permission("workflow_template:rate"))],
)
async def rate_template(template_id: str, request: RateRequestModel):
    """给模板评分（1.0-5.0），增量更新 avg_rating / rating_count."""
    service = get_workflow_template_service()
    try:
        result = await service.rate(template_id, rating=request.rating)
    except TemplateNotFoundError as e:
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    except InvalidVersionError as e:
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    except RuntimeError as e:
        logger.error("Rate workflow template failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Rate workflow template failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="评分失败",
            detail=str(e),
        )

    return success(data=result, message="评分已提交")


@router.post(
    "/{template_id}/unpublish",
    dependencies=[Depends(require_permission("workflow_template:manage"))],
)
async def unpublish_template(template_id: str):
    """下架模板（status -> unpublished，不删除数据）.

    下架后模板不再出现在 list/search 结果中，但已发布的版本数据保留，
    便于历史追溯和重新上架。
    """
    service = get_workflow_template_service()
    try:
        result = await service.unpublish(template_id)
    except TemplateNotFoundError as e:
        return error(code=ErrorCode.NOT_FOUND, message=str(e))
    except RuntimeError as e:
        logger.error("Unpublish workflow template failed (runtime): %s", e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    except Exception as e:
        logger.error("Unpublish workflow template failed: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message="下架工作流模板失败",
            detail=str(e),
        )

    return success(data=result, message="工作流模板已下架")
