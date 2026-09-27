"""
知识库文档 API - 文档管理。

提供文档的 CRUD、分类统计、关键词搜索及演示数据填充功能。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.permissions import require_permission, require_role
from app.core.response import ErrorCode, error, success
from app.services import documents_service


# Pydantic schemas


class DocumentCreate(BaseModel):
    title: str
    category: str
    version: str = "v1.0"
    author: str
    content: str | None = None
    tags: list[str] = []
    status: str = "待审核"


class DocumentUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    version: str | None = None
    author: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    status: str | None = None


# Router

router = APIRouter(
    prefix="/api/v1/documents",
    tags=["Documents"],
    dependencies=[Depends(require_permission("documents:read"))],
)


@router.get("/")
async def list_documents(
    category: str | None = Query(None, description="分类筛选"),
    status: str | None = Query(None, description="状态筛选"),
    keyword: str | None = Query(None, description="关键词搜索"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取文档列表，支持按分类、状态、关键词筛选。"""
    try:
        data = await documents_service.list_documents(
            category=category,
            status=status,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/categories/")
async def list_categories():
    """获取所有分类及其文档数量。"""
    try:
        data = await documents_service.list_categories()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """获取单个文档详情（浏览量+1）。"""
    try:
        data = await documents_service.get_document(doc_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"文档 '{doc_id}' 未找到")

    return success(data=data)


@router.post("/")
async def create_document(body: DocumentCreate):
    """创建文档。"""
    try:
        data = await documents_service.create_document(
            title=body.title,
            category=body.category,
            version=body.version,
            author=body.author,
            content=body.content,
            tags=body.tags,
            status=body.status,
        )
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data, message="文档创建成功")


@router.put("/{doc_id}")
async def update_document(doc_id: str, body: DocumentUpdate):
    """更新文档。"""
    try:
        data = await documents_service.update_document(doc_id, body.model_dump(exclude_unset=True))
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"文档 '{doc_id}' 未找到")

    return success(data=data, message="文档更新成功")


@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """删除文档。"""
    try:
        result = await documents_service.delete_document(doc_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"文档 '{doc_id}' 未找到")

    return success(message="文档删除成功")


@router.post("/seed", dependencies=[Depends(require_role("admin"))])
async def seed_documents():
    """填充知识库文档演示数据。"""
    try:
        result = await documents_service.seed_documents()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result["already_exists"]:
        return success(message="文档数据已存在，跳过填充")

    return success(
        message="文档演示数据填充成功",
        data={
            "documents": result["count"],
        },
    )
