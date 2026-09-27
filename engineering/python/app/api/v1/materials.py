"""物料管理 API 路由。

提供制造物料的 CRUD 操作、统计汇总和种子数据初始化。
"""

import logging


from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission, require_role
from app.core.response import success, error, ErrorCode
from app.services import materials_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/materials",
    tags=["Materials"],
    dependencies=[Depends(require_permission("materials:read"))],
)


# Pydantic schemas


class MaterialCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, description="物料编码")
    name: str = Field(..., min_length=1, max_length=128, description="名称")
    spec: str = Field("", max_length=256, description="规格")
    category: str = Field("原材料", max_length=32, description="分类: 原材料/半成品/成品")
    quantity: int = Field(0, ge=0, description="库存数量")
    safe_quantity: int = Field(0, ge=0, description="安全库存")
    status: str = Field("正常", max_length=16, description="状态: 正常/低库存/缺货")
    location: str = Field("", max_length=64, description="库位")
    unit: str = Field("", max_length=16, description="单位")
    supplier: str = Field("", max_length=128, description="供应商")


class MaterialUpdate(BaseModel):
    code: str | None = Field(None, max_length=64, description="物料编码")
    name: str | None = Field(None, max_length=128, description="名称")
    spec: str | None = Field(None, max_length=256, description="规格")
    category: str | None = Field(None, max_length=32, description="分类")
    quantity: int | None = Field(None, ge=0, description="库存数量")
    safe_quantity: int | None = Field(None, ge=0, description="安全库存")
    status: str | None = Field(None, max_length=16, description="状态")
    location: str | None = Field(None, max_length=64, description="库位")
    unit: str | None = Field(None, max_length=16, description="单位")
    supplier: str | None = Field(None, max_length=128, description="供应商")


class StockInRequest(BaseModel):
    quantity: int = Field(..., gt=0, le=100000, description="入库数量")
    remark: str | None = Field(None, max_length=200, description="入库备注")


class PurchaseRequest(BaseModel):
    quantity: int = Field(..., gt=0, le=100000, description="采购数量")
    supplier: str | None = Field(None, max_length=128, description="供应商")


# Endpoints


@router.get("/")
async def list_materials(
    category: str | None = Query(None, description="按分类筛选"),
    status: str | None = Query(None, description="按状态筛选"),
    keyword: str | None = Query(None, description="搜索名称或编码"),
    page: int = Query(1, ge=1, le=500, description="页码（从 1 开始）"),
    page_size: int = Query(50, ge=1, le=100, description="每页条数（最大 500）"),
):
    """获取物料列表，支持分类、状态筛选、关键词搜索和分页。"""
    data = await materials_service.list_materials(
        category=category,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return success(data=data)


@router.get("/stats/summary")
async def stats_summary():
    """获取物料统计汇总：总数、低库存数、缺货数。"""
    data = await materials_service.stats_summary()
    return success(data=data)


@router.get("/{material_id}")
async def get_material(material_id: str):
    """根据 ID 获取单个物料详情。"""
    data = await materials_service.get_material(material_id)
    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"物料 {material_id} 不存在")
    return success(data=data)


@router.post("/")
async def create_material(body: MaterialCreate):
    """创建新物料。"""
    data = await materials_service.create_material(body.model_dump())
    return success(data=data, message="物料创建成功")


@router.put("/{material_id}")
async def update_material(material_id: str, body: MaterialUpdate):
    """更新物料信息。"""
    data = await materials_service.update_material(material_id, body.model_dump(exclude_unset=True))
    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"物料 {material_id} 不存在")
    return success(data=data, message="物料更新成功")


@router.delete("/{material_id}")
async def delete_material(material_id: str):
    """删除物料。"""
    result = await materials_service.delete_material(material_id)
    if result is None:
        return error(ErrorCode.NOT_FOUND, message=f"物料 {material_id} 不存在")
    return success(message="物料删除成功")


@router.post("/{material_id}/stock-in")
async def stock_in_material(material_id: str, body: StockInRequest):
    """物料入库：增加库存数量并自动重算状态。"""
    data = await materials_service.stock_in_material(material_id, body.quantity, remark=body.remark)
    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"物料 {material_id} 不存在")
    return success(data=data, message="入库成功")


@router.post("/{material_id}/purchase")
async def purchase_material(material_id: str, body: PurchaseRequest):
    """物料采购：更新供应商并增加库存数量，自动重算状态。"""
    data = await materials_service.purchase_material(material_id, body.quantity, supplier=body.supplier)
    if data is None:
        return error(ErrorCode.NOT_FOUND, message=f"物料 {material_id} 不存在")
    return success(data=data, message="采购成功")


# Seed data


@router.post("/seed", dependencies=[Depends(require_role("admin"))])
async def seed_materials():
    """初始化种子数据（仅在物料表为空时插入）。"""
    result = await materials_service.seed_materials()
    if result["already_exists"]:
        return success(message=f"物料表已有 {result['existing_count']} 条记录，跳过种子数据")

    logger.info("已插入 %d 条物料种子数据", result["inserted_count"])
    return success(message=f"成功插入 {result['inserted_count']} 条物料种子数据")
