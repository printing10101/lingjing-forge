"""参数优化 API（Phase D）。

端点：
- POST /optimizer/recommend       参数推荐（L0/L1 分层，物理安全钳制）
- POST /optimizer/evaluate        单条实测结果评估（0-1 得分）
- POST /optimizer/compare         A/B 两组结果对比（提升率）
- GET  /optimizer/baselines      列出基线参数库（L0 经验表）

权限：
- 读 require_permission("optimizer:read")
- 写 require_permission("optimizer:write")
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.optimizer.baseline import BaselineLibrary
from app.optimizer.evaluator import compare_parameter_sets, evaluate_recommendation
from app.optimizer.recommender import (
    OptimizationTarget,
    ParameterRecommender,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimizer", tags=["parameter-optimizer"])


# 请求/响应模型


class RecommendRequest(BaseModel):
    """参数推荐请求。"""

    material: str = Field(..., min_length=1, max_length=64)
    machining_type: str = Field(default="milling", max_length=32)
    tool_id: str = Field(default="", max_length=64)
    target: OptimizationTarget = Field(default=OptimizationTarget.BALANCED)


class EvaluateRequest(BaseModel):
    """实测结果评估请求。"""

    cycle_time_s: float | None = Field(default=None, gt=0)
    tool_wear_percent: float | None = Field(default=None, ge=0, le=100)
    surface_roughness_ra: float | None = Field(default=None, ge=0)
    result: str = Field(default="ok", pattern="^(ok|rework|scrap)$")


class CompareRequest(BaseModel):
    """A/B 对比请求。"""

    a_results: list[dict] = Field(..., min_length=1)
    b_results: list[dict] = Field(..., min_length=1)


# 端点


@router.post("/recommend")
async def recommend(
    req: RecommendRequest,
    _: None = Depends(require_permission("optimizer:read")),
) -> dict:
    """推荐切削参数（分层策略 + 物理安全钳制）。"""
    recommender = ParameterRecommender()
    rec = recommender.recommend(
        material=req.material,
        machining_type=req.machining_type,
        tool_id=req.tool_id,
        target=req.target,
    )
    if rec is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"未找到 {req.material}/{req.machining_type} 的基线参数且无历史数据。"
                "建议操作：检查材料名称或先在切削体验中采集数据。"
            ),
        )
    return {"recommendation": rec.to_dict()}


@router.post("/evaluate")
async def evaluate(
    req: EvaluateRequest,
    _: None = Depends(require_permission("optimizer:read")),
) -> dict:
    """评估单条实测结果。"""
    result = evaluate_recommendation(
        cycle_time_s=req.cycle_time_s,
        tool_wear_percent=req.tool_wear_percent,
        surface_roughness_ra=req.surface_roughness_ra,
        result=req.result,
    )
    return {
        "score": result.score,
        "cycle_time_ok": result.cycle_time_ok,
        "wear_ok": result.wear_ok,
        "roughness_ok": result.roughness_ok,
        "result_ok": result.result_ok,
        "details": result.details,
    }


@router.post("/compare")
async def compare(
    req: CompareRequest,
    _: None = Depends(require_permission("optimizer:read")),
) -> dict:
    """A/B 两组结果对比。"""
    result = compare_parameter_sets(req.a_results, req.b_results)
    return {
        "better": result.better,
        "improvement_pct": result.improvement_pct,
        "a_samples": result.a_samples,
        "b_samples": result.b_samples,
        "a_avg_cycle": result.a_avg_cycle,
        "b_avg_cycle": result.b_avg_cycle,
        "a_avg_wear": result.a_avg_wear,
        "b_avg_wear": result.b_avg_wear,
    }


@router.get("/baselines")
async def list_baselines(
    material: str | None = None,
    machining_type: str | None = None,
    _: None = Depends(require_permission("optimizer:read")),
) -> dict:
    """列出基线参数库（L0 经验表），支持按材料/加工类型过滤。"""
    library = BaselineLibrary()
    entries = []
    for entry in library.entries:
        if material and entry.material.lower() != material.lower():
            continue
        if machining_type and entry.machining_type.lower() != machining_type.lower():
            continue
        entries.append(
            {
                "material": entry.material,
                "machining_type": entry.machining_type,
                "tool_material": entry.tool_material,
                "depth_of_cut_mm": entry.depth_of_cut_mm,
                "feed_mm_per_rev": entry.feed_mm_per_rev,
                "spindle_rpm": entry.spindle_rpm,
                "cutting_speed_m_min": entry.cutting_speed_m_min,
            }
        )
    return {"entries": entries, "total": len(entries)}
