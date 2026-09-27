"""
LLM工艺理解与知识问答模块 API 路由

提供以下接口：
- POST /api/process-understanding/query    - 工艺理解主接口
- POST /api/process-understanding/explain  - 模型预测结果解释接口
- GET  /api/process-understanding/stats    - 模块统计信息
- GET  /api/process-understanding/health   - 健康检查
"""

# 注意：不可加 from __future__ import annotations（@router 装饰器 + 本地 Pydantic 模型参数会触发
# PydanticUndefinedAnnotation，见运维手册）。2026-08-03 安装验证修复。

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.dependencies import get_process_understanding_engine

# 修复：拆分/迁移时缺失的导入（2026-08-03 安装验证发现）
from .prediction_explainer import PredictionData

logger = logging.getLogger(__name__)

# 修复：迁移/拆分时遗漏的 router 实例化（3 个端点装饰器引用它，缺失导致 NameError——2026-08-03 安装验证发现）
router = APIRouter()


class ExplainRequest(BaseModel):
    """模型预测结果解释请求（修复：拆分时丢失的类型定义，2026-08-03）"""

    force_pred: float = Field(0.0, description="切削力预测值 (N)")
    force_conf: float = Field(0.0, description="切削力置信度 (%)")
    wear_pred: float = Field(0.0, description="刀具磨损预测值 (mm)")
    wear_conf: float = Field(0.0, description="刀具磨损置信度 (%)")
    visual_status: str = Field("", description="工件状态描述")
    anomaly_prob: float = Field(0.0, description="异常概率 (%)")


class QueryRequest(BaseModel):
    """工艺理解主接口请求。"""

    query: str = Field(..., min_length=1, max_length=2000, description="用户自然语言输入")


@router.post("/query", summary="工艺理解主接口")
async def process_query(request: QueryRequest) -> dict[str, Any]:
    """工艺理解主接口：任务分类 → 知识检索 → 方案生成。

    LLM 不可用时自动降级为规则模式（与引擎行为一致）。
    W 引擎验证修复：引擎与测试齐备但本接口此前从未注册——主入口缺失。
    """
    try:
        engine = get_process_understanding_engine()
        output = await engine.process(request.query)
        return success(data=output.to_dict(), message="工艺理解完成")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        safe = safe_error_message(e, context="process_understanding.query", fallback="工艺理解处理失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.post("/explain", summary="模型预测结果解释")
async def explain_prediction(request: ExplainRequest) -> dict[str, Any]:
    """将LNN/JEPA模型预测结果转化为操作员可理解的指导信息。

    接收模型预测数据，返回通俗易懂的解释和操作建议。

    Args:
        request: 包含LNN/JEPA预测数据的请求对象

    Returns:
        标准API响应，data中包含解释结果
    """
    try:
        prediction = PredictionData(
            force_pred=request.force_pred,
            force_conf=request.force_conf,
            wear_pred=request.wear_pred,
            wear_conf=request.wear_conf,
            visual_status=request.visual_status,
            anomaly_prob=request.anomaly_prob,
        )

        engine = get_process_understanding_engine()
        output = await engine.explain_prediction(prediction)

        return success(
            data=output.to_dict(),
            message="预测结果解释完成",
        )
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        # 修复：避免将 e!s 直接进入响应
        logger.exception("预测结果解释异常")
        safe = safe_error_message(e, context="process_understanding.explain", fallback="预测结果解释失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/stats", summary="模块统计信息")
async def get_stats() -> dict[str, Any]:
    """获取工艺理解模块的运行统计信息。

    包含各子模块的性能指标：
    - 分类器：规则命中率、平均延迟
    - 检索器：查询次数、平均延迟
    - 方案生成器：生成次数、平均延迟
    - 解释器：解释次数、平均延迟

    Returns:
        标准API响应，data中包含各模块统计信息
    """
    try:
        engine = get_process_understanding_engine()
        stats = engine.get_stats()
        return success(data=stats, message="统计信息获取成功")
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        # 修复：避免将 e!s 直接进入响应
        safe = safe_error_message(e, context="process_understanding.stats", fallback="统计信息获取失败")
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )


@router.get("/health", summary="健康检查")
async def health_check() -> dict[str, Any]:
    """工艺理解模块健康检查。

    Returns:
        模块运行状态
    """
    try:
        engine = get_process_understanding_engine()
        stats = engine.get_stats()
        return success(
            data={
                "status": "healthy",
                "total_requests": stats.get("total_requests", 0),
                "avg_latency_ms": stats.get("avg_latency_ms", 0),
            },
            message="模块运行正常",
        )
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
        # 修复：避免将 e!s 直接进入响应
        safe = safe_error_message(e, context="process_understanding.health", fallback="模块异常")
        return error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=safe["message"],
            detail={"error_id": safe.get("error_id")} if safe.get("error_id") else None,
        )
