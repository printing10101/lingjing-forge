"""FastAPI router for MES/ERP integration endpoints.

This module provides REST API endpoints for synchronizing data with external
MES/ERP systems. It exposes endpoints for work order synchronization,
production reporting, material queries, quality data upload, and health checks.

The router uses dependency injection to provide a configured MESClient instance.

Example::

    from fastapi import FastAPI
    from app.integrations.mes.api import router, get_mes_client

    app = FastAPI()
    app.include_router(router)

    # Configure MES client
    app.dependency_overrides[get_mes_client] = lambda: MESClient(
        base_url="https://mes.example.com",
        api_key="your-api-key"
    )
"""

from __future__ import annotations

import logging
from datetime import datetime


from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission

from app.integrations.mes.client import (
    MESClient,
    WorkOrderData,
    QualityData,
)

logger = logging.getLogger(__name__)


# Request/Response Models


class WorkOrderRequest(BaseModel):
    """工单同步请求模型。

    Attributes:
        work_order_no: 工单编号
        product_code: 产品编码
        quantity: 计划数量
        priority: 优先级 (1-10, 10 最高)
        planned_start: 计划开始时间（ISO 格式）
        planned_end: 计划结束时间（ISO 格式）
        customer_order_no: 客户订单号（可选）
        remarks: 备注信息（可选）
    """

    work_order_no: str = Field(..., description="工单编号", examples=["WO-2024-001"])
    product_code: str = Field(..., description="产品编码", examples=["PROD-001"])
    quantity: int = Field(..., gt=0, description="计划数量", examples=[100])
    priority: int = Field(5, ge=1, le=10, description="优先级 (1-10)", examples=[5])
    planned_start: datetime | None = Field(None, description="计划开始时间", examples=["2024-01-01T08:00:00"])
    planned_end: datetime | None = Field(None, description="计划结束时间", examples=["2024-01-05T17:00:00"])
    customer_order_no: str | None = Field(None, description="客户订单号", examples=["CUST-ORD-123"])
    remarks: str | None = Field(None, description="备注信息", examples=["加急订单"])


class ProductionReportRequest(BaseModel):
    """生产数据上报请求模型。

    Attributes:
        batch_no: 批次号
        quantity: 生产总数量
        qualified: 合格数量
    """

    batch_no: str = Field(..., description="批次号", examples=["BATCH-2024-001"])
    quantity: int = Field(..., gt=0, description="生产总数量", examples=[100])
    qualified: int = Field(..., ge=0, description="合格数量", examples=[95])


class QualityReportRequest(BaseModel):
    """质量数据上报请求模型。

    Attributes:
        batch_no: 批次号
        product_code: 产品编码
        inspection_type: 检验类型
        result: 检验结果
        inspector: 检验员
        inspection_time: 检验时间
        sample_size: 抽样数量
        qualified_qty: 合格数量
        defective_qty: 不合格数量
        defect_code: 缺陷代码（可选）
        remarks: 备注（可选）
    """

    batch_no: str = Field(..., description="批次号", examples=["BATCH-2024-001"])
    product_code: str = Field(..., description="产品编码", examples=["PROD-001"])
    inspection_type: str = Field(..., description="检验类型", examples=["in_process"])
    result: str = Field(..., description="检验结果", examples=["pass"])
    inspector: str = Field(..., description="检验员", examples=["张三"])
    inspection_time: datetime = Field(..., description="检验时间", examples=["2024-01-01T10:00:00"])
    sample_size: int = Field(..., gt=0, description="抽样数量", examples=[10])
    qualified_qty: int = Field(..., ge=0, description="合格数量", examples=[9])
    defective_qty: int = Field(0, ge=0, description="不合格数量", examples=[1])
    defect_code: str | None = Field(None, description="缺陷代码", examples=["DEF-001"])
    remarks: str | None = Field(None, description="备注", examples=["轻微划痕"])


class SyncResultResponse(BaseModel):
    """同步结果响应模型。

    Attributes:
        success: 是否成功
        message: 结果消息
        data_id: MES 系统中的数据 ID
        error_code: 错误代码
        timestamp: 结果时间戳
    """

    success: bool = Field(..., description="是否成功")
    message: str = Field(..., description="结果消息")
    data_id: str | None = Field(None, description="MES 系统中的数据 ID")
    error_code: str | None = Field(None, description="错误代码")
    timestamp: datetime = Field(..., description="结果时间戳")


class MaterialResponse(BaseModel):
    """物料信息响应模型。

    Attributes:
        material_code: 物料编码
        name: 物料名称
        specification: 规格型号
        unit: 单位
        stock_quantity: 库存数量
        warehouse_location: 仓库位置
        batch_no: 批次号
        expiry_date: 有效期
    """

    material_code: str = Field(..., description="物料编码")
    name: str = Field(..., description="物料名称")
    specification: str = Field(..., description="规格型号")
    unit: str = Field(..., description="单位")
    stock_quantity: float = Field(..., description="库存数量")
    warehouse_location: str = Field(..., description="仓库位置")
    batch_no: str | None = Field(None, description="批次号")
    expiry_date: datetime | None = Field(None, description="有效期")


class HealthResponse(BaseModel):
    """健康检查响应模型。

    Attributes:
        status: 系统状态
        mes_connected: MES 系统是否可连接
    """

    status: str = Field(..., description="系统状态")
    mes_connected: bool = Field(..., description="MES 系统是否可连接")


# Dependency Injection


async def get_mes_client() -> MESClient:
    """获取 MES 客户端实例的依赖函数。

    从全局配置读取 MES 连接信息并返回客户端实例。

    Returns:
        MESClient: 配置好的 MES 客户端

    Raises:
        HTTPException: 如果 MES 未启用或配置不完整
    """
    from app.config import config

    if not config.mes.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MES integration is not enabled. Set MES_ENABLED=true to enable.",
        )

    if not config.mes.base_url or not config.mes.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MES integration not configured. Set MES_BASE_URL and MES_API_KEY.",
        )

    return MESClient(
        base_url=config.mes.base_url,
        api_key=config.mes.api_key,
        timeout=config.mes.timeout,
    )


# Router


router = APIRouter(
    prefix="/api/v1/mes",
    tags=["MES Integration"],
    dependencies=[Depends(require_permission("mes:read"))],
    responses={
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Internal server error"},
    },
)


@router.post(
    "/sync-work-order",
    response_model=SyncResultResponse,
    status_code=status.HTTP_200_OK,
    summary="同步工单到 MES",
    description="将工单数据同步到 MES 系统进行生产排程和跟踪",
    responses={
        status.HTTP_200_OK: {"description": "同步成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "请求参数错误"},
        status.HTTP_502_BAD_GATEWAY: {"description": "MES 系统不可用"},
    },
)
async def sync_work_order(
    request: WorkOrderRequest,
    client: MESClient = Depends(get_mes_client),
) -> SyncResultResponse:
    """同步工单到 MES 系统。

    Args:
        request: 工单同步请求
        client: MES 客户端（通过依赖注入）

    Returns:
        SyncResultResponse: 同步结果

    Raises:
        HTTPException: 如果同步失败
    """
    try:
        logger.info(
            "收到工单同步请求: work_order_no=%s",
            request.work_order_no,
        )

        # 转换为数据对象
        work_order = WorkOrderData(
            work_order_no=request.work_order_no,
            product_code=request.product_code,
            quantity=request.quantity,
            priority=request.priority,
            planned_start=request.planned_start,
            planned_end=request.planned_end,
            customer_order_no=request.customer_order_no,
            remarks=request.remarks,
        )

        # 调用客户端同步
        result = await client.sync_work_order(work_order)

        logger.info(
            "工单同步完成: success=%s, message=%s",
            result.success,
            result.message,
        )

        return SyncResultResponse(
            success=result.success,
            message=result.message,
            data_id=result.data_id,
            error_code=result.error_code,
            timestamp=result.timestamp,
        )

    except ValueError as e:
        logger.error("工单同步参数错误: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求参数无效",
        )

    except Exception as e:
        logger.error("工单同步异常: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="处理失败，请联系管理员",
        )


@router.post(
    "/report-production",
    response_model=SyncResultResponse,
    status_code=status.HTTP_200_OK,
    summary="上报生产数据",
    description="向 MES 系统上报生产数量和合格数量",
    responses={
        status.HTTP_200_OK: {"description": "上报成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "请求参数错误"},
        status.HTTP_502_BAD_GATEWAY: {"description": "MES 系统不可用"},
    },
)
async def report_production(
    request: ProductionReportRequest,
    client: MESClient = Depends(get_mes_client),
) -> SyncResultResponse:
    """上报生产数据到 MES 系统。

    Args:
        request: 生产数据上报请求
        client: MES 客户端（通过依赖注入）

    Returns:
        SyncResultResponse: 上报结果

    Raises:
        HTTPException: 如果上报失败
    """
    try:
        logger.info(
            "收到生产数据上报请求: batch_no=%s, qty=%d, qualified=%d",
            request.batch_no,
            request.quantity,
            request.qualified,
        )

        # 调用客户端上报
        result = await client.report_production(
            batch_no=request.batch_no,
            qty=request.quantity,
            qualified=request.qualified,
        )

        logger.info(
            "生产数据上报完成: success=%s, message=%s",
            result.success,
            result.message,
        )

        return SyncResultResponse(
            success=result.success,
            message=result.message,
            data_id=result.data_id,
            error_code=result.error_code,
            timestamp=result.timestamp,
        )

    except ValueError as e:
        logger.error("生产数据上报参数错误: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求参数无效",
        )

    except Exception as e:
        logger.error("生产数据上报异常: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="处理失败，请联系管理员",
        )


@router.get(
    "/material/{code}",
    response_model=MaterialResponse,
    status_code=status.HTTP_200_OK,
    summary="查询物料信息",
    description="从 MES 系统查询指定编码的物料信息",
    responses={
        status.HTTP_200_OK: {"description": "查询成功"},
        status.HTTP_404_NOT_FOUND: {"description": "物料未找到"},
        status.HTTP_502_BAD_GATEWAY: {"description": "MES 系统不可用"},
    },
)
async def query_material(
    code: str,
    client: MESClient = Depends(get_mes_client),
) -> MaterialResponse:
    """查询物料信息。

    Args:
        code: 物料编码
        client: MES 客户端（通过依赖注入）

    Returns:
        MaterialResponse: 物料信息

    Raises:
        HTTPException: 如果查询失败或物料未找到
    """
    try:
        logger.info("收到物料查询请求: code=%s", code)

        # 调用客户端查询
        material = await client.query_material(code)

        if material is None:
            logger.warning("物料未找到: code=%s", code)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"物料未找到: {code}",
            )

        logger.info(
            "物料查询成功: code=%s, name=%s",
            material.material_code,
            material.name,
        )

        return MaterialResponse(
            material_code=material.material_code,
            name=material.name,
            specification=material.specification,
            unit=material.unit,
            stock_quantity=material.stock_quantity,
            warehouse_location=material.warehouse_location,
            batch_no=material.batch_no,
            expiry_date=material.expiry_date,
        )

    except HTTPException:
        raise

    except ValueError as e:
        logger.error("物料查询参数错误: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求参数无效",
        )

    except Exception as e:
        logger.error("物料查询异常: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="处理失败，请联系管理员",
        )


@router.post(
    "/report-quality",
    response_model=SyncResultResponse,
    status_code=status.HTTP_200_OK,
    summary="上报质量数据",
    description="向 MES 系统上报质量检验数据",
    responses={
        status.HTTP_200_OK: {"description": "上报成功"},
        status.HTTP_400_BAD_REQUEST: {"description": "请求参数错误"},
        status.HTTP_502_BAD_GATEWAY: {"description": "MES 系统不可用"},
    },
)
async def report_quality(
    request: QualityReportRequest,
    client: MESClient = Depends(get_mes_client),
) -> SyncResultResponse:
    """上报质量数据到 MES 系统。

    Args:
        request: 质量数据上报请求
        client: MES 客户端（通过依赖注入）

    Returns:
        SyncResultResponse: 上报结果

    Raises:
        HTTPException: 如果上报失败
    """
    try:
        logger.info(
            "收到质量数据上报请求: batch_no=%s, result=%s",
            request.batch_no,
            request.result,
        )

        # 转换为数据对象
        quality_data = QualityData(
            batch_no=request.batch_no,
            product_code=request.product_code,
            inspection_type=request.inspection_type,
            result=request.result,
            inspector=request.inspector,
            inspection_time=request.inspection_time,
            sample_size=request.sample_size,
            qualified_qty=request.qualified_qty,
            defective_qty=request.defective_qty,
            defect_code=request.defect_code,
            remarks=request.remarks,
        )

        # 调用客户端上报
        result = await client.report_quality(quality_data)

        logger.info(
            "质量数据上报完成: success=%s, message=%s",
            result.success,
            result.message,
        )

        return SyncResultResponse(
            success=result.success,
            message=result.message,
            data_id=result.data_id,
            error_code=result.error_code,
            timestamp=result.timestamp,
        )

    except ValueError as e:
        logger.error("质量数据上报参数错误: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求参数无效",
        )

    except Exception as e:
        logger.error("质量数据上报异常: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="处理失败，请联系管理员",
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="健康检查",
    description="检查 MES 系统连接状态",
    responses={
        status.HTTP_200_OK: {"description": "检查完成"},
    },
)
async def health_check(
    client: MESClient = Depends(get_mes_client),
) -> HealthResponse:
    """检查 MES 系统连接状态。

    Args:
        client: MES 客户端（通过依赖注入）

    Returns:
        HealthResponse: 健康检查结果
    """
    try:
        logger.info("收到健康检查请求")

        # 调用客户端健康检查
        is_connected = await client.health_check()

        status_msg = "healthy" if is_connected else "unhealthy"
        logger.info("健康检查完成: mes_connected=%s", is_connected)

        return HealthResponse(
            status=status_msg,
            mes_connected=is_connected,
        )

    except Exception as e:
        logger.error("健康检查异常: %s", str(e), exc_info=True)
        return HealthResponse(
            status="error",
            mes_connected=False,
        )


__all__ = [
    "router",
    "get_mes_client",
    "WorkOrderRequest",
    "ProductionReportRequest",
    "QualityReportRequest",
    "SyncResultResponse",
    "MaterialResponse",
    "HealthResponse",
]
