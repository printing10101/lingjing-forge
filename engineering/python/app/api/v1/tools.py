"""刀具库管理 API 路由。

提供刀具的 CRUD 操作、磨损跟踪、寿命预测集成和种子数据初始化。
"""

import logging


from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission, require_role
from app.core.response import success, error, ErrorCode
from app.services import tools_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/tools",
    tags=["Tools"],
    dependencies=[Depends(require_permission("tools:read"))],
)


# Pydantic schemas


class ToolCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=32, description="刀具编码 (T01, T02, ...)")
    name: str = Field(..., min_length=1, max_length=128, description="刀具名称")
    type: str = Field(
        ...,
        max_length=32,
        description="刀具类型: end_mill/ball_mill/drill/reamer/tap/insert/grooving/threading",
    )
    diameter: float = Field(..., gt=0, description="刀具直径 (mm)")
    length: float | None = Field(None, gt=0, description="刀具长度 (mm)")
    flute_count: int | None = Field(2, ge=1, description="刃数")
    material: str | None = Field(None, max_length=32, description="刀具材料: carbide/hss/ceramic/cbn/diamond")
    coating: str | None = Field(None, max_length=32, description="涂层类型: TiN/TiAlN/AlCrN/DLC/None")
    max_rpm: float | None = Field(None, gt=0, description="最大允许转速 (RPM)")
    max_feed: float | None = Field(None, gt=0, description="最大允许进给 (mm/min)")
    vendor: str | None = Field(None, max_length=128, description="供应商")
    cost: float | None = Field(None, ge=0, description="采购成本")
    notes: str | None = Field(None, description="备注")


class ToolUpdate(BaseModel):
    code: str | None = Field(None, max_length=32, description="刀具编码")
    name: str | None = Field(None, max_length=128, description="刀具名称")
    type: str | None = Field(None, max_length=32, description="刀具类型")
    diameter: float | None = Field(None, gt=0, description="刀具直径 (mm)")
    length: float | None = Field(None, gt=0, description="刀具长度 (mm)")
    flute_count: int | None = Field(None, ge=1, description="刃数")
    material: str | None = Field(None, max_length=32, description="刀具材料")
    coating: str | None = Field(None, max_length=32, description="涂层类型")
    max_rpm: float | None = Field(None, gt=0, description="最大允许转速 (RPM)")
    max_feed: float | None = Field(None, gt=0, description="最大允许进给 (mm/min)")
    usage_time: float | None = Field(None, ge=0, description="累计使用时间 (分钟)")
    wear_amount: float | None = Field(None, ge=0, description="磨损量 (mm)")
    status: str | None = Field(None, max_length=16, description="刀具状态: active/worn/broken/maintenance")
    vendor: str | None = Field(None, max_length=128, description="供应商")
    cost: float | None = Field(None, ge=0, description="采购成本")
    notes: str | None = Field(None, description="备注")


class ToolWearUpdate(BaseModel):
    """刀具磨损更新请求。"""

    additional_usage_time: float = Field(0.0, ge=0, description="新增使用时间 (分钟)")
    additional_wear: float = Field(0.0, ge=0, description="新增磨损量 (mm)")
    sharpened: bool = Field(False, description="是否进行了刃磨")


# Endpoints


@router.get("/")
async def list_tools(
    type: str | None = Query(None, description="按刀具类型筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    keyword: str | None = Query(None, description="搜索名称或编码"),
):
    """获取刀具列表，支持类型、状态筛选和关键词搜索。"""
    try:
        data = await tools_service.list_tools(type=type, status=status, keyword=keyword)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/stats/summary")
async def stats_summary():
    """获取刀具统计汇总：总数、磨损数、报废数、平均寿命剩余。"""
    try:
        data = await tools_service.stats_summary()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    return success(data=data)


@router.get("/{tool_id}")
async def get_tool(tool_id: str):
    """根据 ID 获取单个刀具详情。"""
    try:
        data = await tools_service.get_tool(tool_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"刀具 {tool_id} 不存在")
    return success(data=data)


@router.post("/")
async def create_tool(body: ToolCreate):
    """创建新刀具。"""
    try:
        data = await tools_service.create_tool(body.model_dump())
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")
    except ValueError as exc:
        return error(
            ErrorCode.INVALID_REQUEST,
            message=str(exc),
        )

    return success(data=data, message="刀具创建成功")


@router.put("/{tool_id}")
async def update_tool(tool_id: str, body: ToolUpdate):
    """更新刀具信息。"""
    try:
        data = await tools_service.update_tool(tool_id, body.model_dump(exclude_unset=True))
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"刀具 {tool_id} 不存在")
    return success(data=data, message="刀具更新成功")


@router.delete("/{tool_id}")
async def delete_tool(tool_id: str):
    """删除刀具。"""
    try:
        result = await tools_service.delete_tool(tool_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result is None:
        return error(ErrorCode.NOT_FOUND, message=f"刀具 {tool_id} 不存在")
    return success(message="刀具删除成功")


@router.post("/{tool_id}/wear")
async def update_tool_wear(tool_id: str, body: ToolWearUpdate):
    """更新刀具磨损信息。

    累加使用时间和磨损量，如果进行了刃磨则重置磨损量并记录刃磨时间。
    """
    try:
        data = await tools_service.update_tool_wear(
            tool_id,
            additional_usage_time=body.additional_usage_time,
            additional_wear=body.additional_wear,
            sharpened=body.sharpened,
        )
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"刀具 {tool_id} 不存在")
    return success(data=data, message="刀具磨损信息更新成功")


@router.get("/{tool_id}/life-prediction")
async def tool_life_prediction(tool_id: str):
    """获取刀具寿命预测信息。

    基于使用时间和磨损量估算剩余寿命。
    """
    try:
        data = await tools_service.tool_life_prediction(tool_id)
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"刀具 {tool_id} 不存在")
    return success(data=data)


# Seed data


@router.post("/seed", dependencies=[Depends(require_role("admin"))])
async def seed_tools():
    """初始化种子数据（仅在刀具表为空时插入）。"""
    try:
        result = await tools_service.seed_tools()
    except RuntimeError:
        return error(code=ErrorCode.SERVICE_UNAVAILABLE, message="数据库未配置")

    if result["already_exists"]:
        return success(message=f"刀具表已有 {result['existing_count']} 条记录，跳过种子数据")

    logger.info("已插入 %d 条刀具种子数据", result["inserted_count"])
    return success(message=f"成功插入 {result['inserted_count']} 条刀具种子数据")
