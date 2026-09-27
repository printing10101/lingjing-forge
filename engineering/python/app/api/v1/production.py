"""
生产报表 API - 生产记录、工单管理及仪表盘。

提供生产记录查询、工单 CRUD、仪表盘 KPI、产线数据及演示数据填充功能。
"""

from datetime import date


from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.auth.permissions import require_permission, require_role
from app.core.response import ErrorCode, error, success
from app.services import production_service


# Pydantic schemas


class WorkOrderUpdate(BaseModel):
    product_name: str | None = None
    planned_qty: int | None = None
    completed_qty: int | None = None
    status: str | None = None
    priority: str | None = None
    start_date: date | None = None
    due_date: date | None = None


# Router

router = APIRouter(
    prefix="/api/v1/production",
    tags=["Production"],
    dependencies=[Depends(require_permission("production:read"))],
)


@router.get("/dashboard/")
async def get_dashboard():
    """仪表盘 KPI：今日产量、良品率、OEE、活跃告警数。"""
    try:
        data = await production_service.get_dashboard()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/records/")
async def list_production_records(
    date_from: date | None = Query(None, description="起始日期"),
    date_to: date | None = Query(None, description="结束日期"),
    line_name: str | None = Query(None, description="产线名称"),
    shift: str | None = Query(None, description="班次"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取生产记录列表，支持按日期范围、产线、班次筛选。"""
    try:
        data = await production_service.list_production_records(
            date_from=date_from,
            date_to=date_to,
            line_name=line_name,
            shift=shift,
            limit=limit,
            offset=offset,
        )
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/work-orders/")
async def list_work_orders(
    status: str | None = Query(None),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """获取工单列表，支持按状态、优先级筛选。"""
    try:
        data = await production_service.list_work_orders(status=status, priority=priority, limit=limit, offset=offset)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/work-orders/{wo_id}")
async def get_work_order(wo_id: str):
    """获取单个工单详情。"""
    try:
        data = await production_service.get_work_order(wo_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"工单 '{wo_id}' 未找到")
    return success(data=data)


@router.put("/work-orders/{wo_id}")
async def update_work_order(wo_id: str, body: WorkOrderUpdate):
    """更新工单信息。"""
    try:
        data = await production_service.update_work_order(wo_id, body.model_dump(exclude_unset=True))
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(code=ErrorCode.NOT_FOUND, message=f"工单 '{wo_id}' 未找到")

    return success(data=data, message="工单更新成功")


@router.get("/lines/")
async def get_production_lines():
    """获取产线列表及各班次数据。"""
    try:
        data = await production_service.get_production_lines()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/stats")
async def get_daily_stats(
    days: int = Query(7, ge=1, le=90, description="统计天数（1-90）"),
):
    """按天聚合生产统计（近 N 天计划/实际产量、良品率、设备利用率、达成率）。"""
    try:
        data = await production_service.get_daily_stats(days=days)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/stats/summary")
async def get_monthly_summary():
    """月度汇总 KPI。"""
    try:
        data = await production_service.get_monthly_summary()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.post("/seed", dependencies=[Depends(require_role("admin"))])
async def seed_production_data():
    """填充生产演示数据：14天 x 5产线 x 3班次 + 8个工单。"""
    try:
        result = await production_service.seed_production_data()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result["already_exists"]:
        return success(message="生产数据已存在，跳过填充")

    return success(
        message="生产演示数据填充成功",
        data={
            "production_records": result["record_count"],
            "work_orders": result["work_order_count"],
        },
    )
