"""碰撞检测API端点。

提供G代码和刀路碰撞检测的REST API接口。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.simulation.collision_detector import (
    CollisionDetector,
    WorkspaceLimits,
)
from app.simulation.stock_model import StockModel
from app.simulation.toolpath_parser import ToolpathSegment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["collision"])


class StockModelRequest(BaseModel):
    """工件模型请求。"""

    length: float = Field(..., description="工件长度 (mm)")
    width: float = Field(..., description="工件宽度 (mm)")
    height: float = Field(..., description="工件高度 (mm)")
    x_offset: float = Field(0.0, description="X方向偏移 (mm)")
    y_offset: float = Field(0.0, description="Y方向偏移 (mm)")
    z_offset: float = Field(0.0, description="Z方向偏移 (mm)")


class ToolpathSegmentRequest(BaseModel):
    """刀路段请求。"""

    type: str = Field(..., description="段类型 (rapid/linear/arc)")
    start_point: tuple[float, float, float] = Field(..., description="起点坐标 (x, y, z)")
    end_point: tuple[float, float, float] = Field(..., description="终点坐标 (x, y, z)")
    block_number: int = Field(0, description="NC程序段号")
    feed_rate: float | None = Field(None, description="进给率 (mm/min)")
    a_angle: float | None = Field(None, description="A轴角度 (度)")
    c_angle: float | None = Field(None, description="C轴角度 (度)")


class CollisionCheckRequest(BaseModel):
    """碰撞检测请求。"""

    stock: StockModelRequest = Field(..., description="工件模型")
    segments: list[ToolpathSegmentRequest] = Field(..., description="刀路段列表")
    safe_z_height: float = Field(10.0, description="安全Z高度 (mm)")
    mode: str = Field("3axis", description="检测模式 (3axis/5axis)")
    workspace_limits: dict[str, float] | None = Field(None, description="机床工作空间限制")


class CollisionCheckResponse(BaseModel):
    """碰撞检测响应。"""

    code: int = Field(0, description="状态码 (0=成功)")
    message: str = Field("OK", description="状态消息")
    data: dict[str, Any] = Field(..., description="碰撞检测报告")


@router.post(
    "/collision-check",
    response_model=CollisionCheckResponse,
    dependencies=[Depends(require_permission("collision:check"))],
)
async def check_collision(request: CollisionCheckRequest) -> CollisionCheckResponse:
    """执行碰撞检测。

    接受G代码或刀路段作为输入，返回碰撞报告。

    Args:
        request: 碰撞检测请求

    Returns:
        碰撞检测报告

    Raises:
        HTTPException: 检测失败时
    """
    try:
        # 创建工件模型
        stock = StockModel(
            length=request.stock.length,
            width=request.stock.width,
            height=request.stock.height,
            x_offset=request.stock.x_offset,
            y_offset=request.stock.y_offset,
            z_offset=request.stock.z_offset,
        )

        # 创建工作空间限制
        workspace_limits = None
        if request.workspace_limits:
            workspace_limits = WorkspaceLimits(
                x_min=request.workspace_limits.get("x_min", -300.0),
                x_max=request.workspace_limits.get("x_max", 300.0),
                y_min=request.workspace_limits.get("y_min", -300.0),
                y_max=request.workspace_limits.get("y_max", 300.0),
                z_min=request.workspace_limits.get("z_min", -200.0),
                z_max=request.workspace_limits.get("z_max", 200.0),
                a_min=request.workspace_limits.get("a_min", -120.0),
                a_max=request.workspace_limits.get("a_max", 120.0),
                c_min=request.workspace_limits.get("c_min", -360.0),
                c_max=request.workspace_limits.get("c_max", 360.0),
            )

        # 创建碰撞检测器
        detector = CollisionDetector(
            stock=stock,
            safe_z_height=request.safe_z_height,
            mode=request.mode,
            workspace_limits=workspace_limits,
        )

        # 解析刀路段
        segments: list[ToolpathSegment] = []
        for seg_req in request.segments:
            segment = ToolpathSegment(
                type=seg_req.type,
                start_point=seg_req.start_point,
                end_point=seg_req.end_point,
                block_number=seg_req.block_number,
                feed_rate=seg_req.feed_rate,
            )
            # 五轴模式：设置A/C轴角度
            if seg_req.a_angle is not None:
                segment.a_angle = seg_req.a_angle
            if seg_req.c_angle is not None:
                segment.c_angle = seg_req.c_angle
            segments.append(segment)

        # 执行碰撞检测
        if request.mode == "5axis":
            report = detector.check_segments_5axis(segments)
        else:
            report = detector.check_segments(segments)

        # 返回报告
        return CollisionCheckResponse(
            code=0,
            message="碰撞检测完成",
            data=report.to_dict(),
        )

    except Exception:
        logger.exception("碰撞检测失败")
        raise HTTPException(status_code=500, detail="碰撞检测失败，请联系管理员")


@router.get("/collision-check/health")
async def collision_check_health() -> dict[str, Any]:
    """碰撞检测服务健康检查。

    Returns:
        健康状态
    """
    return {
        "status": "healthy",
        "service": "collision-check",
        "version": "1.0.0",
    }
