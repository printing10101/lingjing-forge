"""
工艺路线 API - 工艺路线及工序管理。

提供工艺路线的 CRUD（含工序步骤）、状态筛选及演示数据填充功能。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.permissions import require_permission, require_role

from app.core.response import ErrorCode, error, success
from app.core.response_models import ErrorResponse
from app.services import process_routes_service


# Pydantic schemas


class ProcessStepCreate(BaseModel):
    sequence: int
    name: str
    work_center: str
    hours: int
    equipment: str | None = None
    tooling: str | None = None


class ProcessRouteCreate(BaseModel):
    name: str
    part_type: str
    status: str = "草稿"
    description: str | None = None
    steps: list[ProcessStepCreate] = []


class ProcessRouteUpdate(BaseModel):
    name: str | None = None
    part_type: str | None = None
    status: str | None = None
    description: str | None = None
    steps: list[ProcessStepCreate] | None = None


# Router

router = APIRouter(
    prefix="/api/v1/process-routes",
    tags=["Process Routes"],
    dependencies=[Depends(require_permission("process:read"))],
)


@router.get("/")
async def list_process_routes(
    status: str | None = Query(None, description="状态筛选"),
    part_type: str | None = Query(None, description="零件类型筛选"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取工艺路线列表，支持按状态、零件类型筛选。"""
    try:
        data = await process_routes_service.list_process_routes(
            status=status, part_type=part_type, limit=limit, offset=offset
        )
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/{route_id}")
async def get_process_route(route_id: str):
    """获取工艺路线详情（含所有工序步骤）。"""
    try:
        data = await process_routes_service.get_process_route(route_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"工艺路线 '{route_id}' 未找到")

    return success(data=data)


@router.post("/")
async def create_process_route(body: ProcessRouteCreate):
    """创建工艺路线（含工序步骤）。"""
    body_data = {
        "name": body.name,
        "part_type": body.part_type,
        "status": body.status,
        "description": body.description,
    }
    steps = [s.model_dump() for s in body.steps]

    try:
        data = await process_routes_service.create_process_route(body_data, steps)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data, message="工艺路线创建成功")


@router.put("/{route_id}")
async def update_process_route(route_id: str, body: ProcessRouteUpdate):
    """更新工艺路线（含工序步骤替换）。"""
    update_fields = body.model_dump(exclude_unset=True)
    steps_data = update_fields.pop("steps", None)
    steps = [s.model_dump() for s in steps_data] if steps_data is not None else None

    try:
        data = await process_routes_service.update_process_route(route_id, update_fields, steps)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"工艺路线 '{route_id}' 未找到")

    return success(data=data, message="工艺路线更新成功")


@router.delete("/{route_id}")
async def delete_process_route(route_id: str):
    """删除工艺路线及其所有工序。"""
    try:
        result = await process_routes_service.delete_process_route(route_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"工艺路线 '{route_id}' 未找到")

    return success(message="工艺路线删除成功")


@router.post(
    "/seed",
    dependencies=[Depends(require_role("admin"))],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def seed_process_routes():
    """填充工艺路线演示数据：6条路线及其工序。"""
    try:
        result = await process_routes_service.seed_process_routes()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result["already_exists"]:
        return success(message="工艺路线数据已存在，跳过填充")

    return success(
        message="工艺路线演示数据填充成功",
        data={
            "routes": result["routes_count"],
            "steps": result["steps_count"],
        },
    )
