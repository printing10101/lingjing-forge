"""多源信号融合知识库 API。

端点前缀：/api/v1/signal-fusion-kb

端点列表：
1. POST   /samples                  注册单个信号样本
2. POST   /samples/batch            批量注册信号样本
3. GET    /samples                  列出信号样本（分页）
4. GET    /samples/by-type/{type}   按信号类型列出样本
5. POST   /retrieve                 检索相似信号样本
6. POST   /fuse                     多源信号融合（加权 / 注意力）
7. POST   /correlate/wear           关联磨损状态（生成 ToolWearPredictor 输入）
8. POST   /correlate/chatter        关联颤振状态（生成 ChatterPredictor 输入）
9. GET    /stats                    知识库统计
10. DELETE /samples/{sample_id}     按样本 ID 删除
11. DELETE /samples/by-type/{type}  按信号类型批量删除
12. GET   /health                   健康检查
"""

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.response import success, error, ErrorCode
from app.core.safe_errors import safe_error_message
from app.core.endpoint_handler import safe_endpoint
from app.auth.permissions import require_permission

# P2-4-5 修复：引入共享速率限制器，信号检索/融合端点消耗向量计算资源，需速率限制防止 DoS。
from app.middleware.rate_limiter import limiter
from app.rag.signal_fusion_kb import (
    SUPPORTED_SIGNAL_TYPES,
    SignalSample,
    get_signal_fusion_kb,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/signal-fusion-kb",
    tags=["SignalFusionKB"],
    dependencies=[Depends(require_permission("signal_kb:read"))],
)


# 请求模型


class SignalSampleRequest(BaseModel):
    """信号样本注册请求。"""

    signal_type: str = Field(..., description="信号类型")
    source: str = Field(..., description="数据源标识")
    features: list[float] = Field(..., min_length=1, description="9 维特征向量")
    sensor_features: dict[str, float] = Field(
        default_factory=dict, description="传感器读数（与 ToolWearPredictor 对齐）"
    )
    process_context: dict[str, Any] = Field(default_factory=dict, description="工艺上下文")
    machine_id: str = Field(default="", description="机床 ID")
    tool_id: int | None = Field(default=None, description="刀具 ID")
    material: str = Field(default="", description="工件材料")
    label: str = Field(default="", description="可选标签")
    sample_id: str | None = Field(default=None, description="自定义样本 ID")
    metadata: dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class BatchSamplesRequest(BaseModel):
    """批量注册请求。"""

    samples: list[SignalSampleRequest] = Field(..., min_length=1, max_length=500)


class RetrieveRequest(BaseModel):
    """相似样本检索请求。"""

    features: list[float] = Field(..., min_length=1, description="9 维查询特征")
    signal_type: str | None = Field(default=None, description="信号类型过滤")
    machine_id: str | None = Field(default=None, description="机床 ID 过滤")
    material: str | None = Field(default=None, description="材料过滤")
    tool_id: int | None = Field(default=None, description="刀具 ID 过滤")
    top_k: int = Field(default=10, ge=1, le=100, description="返回前 K 个")


class FuseRequest(BaseModel):
    """多源信号融合请求。"""

    sample_ids: list[str] = Field(
        default_factory=list,
        description="参与融合的样本 ID 列表（与 samples 二选一）",
    )
    samples: list[SignalSampleRequest] = Field(
        default_factory=list,
        description="直接传入样本数据（与 sample_ids 二选一）",
    )
    strategy: str = Field(
        default="weighted",
        description="融合策略: weighted 或 attention",
    )
    weights: dict[str, float] | None = Field(
        default=None,
        description="自定义权重（仅 weighted 策略）",
    )


class CorrelateWearRequest(BaseModel):
    """磨损关联请求。"""

    sample_ids: list[str] = Field(
        default_factory=list,
        description="信号样本 ID 列表",
    )
    samples: list[SignalSampleRequest] = Field(
        default_factory=list,
        description="直接传入样本数据",
    )


class CorrelateChatterRequest(BaseModel):
    """颤振关联请求。"""

    sample_ids: list[str] = Field(
        default_factory=list,
        description="信号样本 ID 列表",
    )
    samples: list[SignalSampleRequest] = Field(
        default_factory=list,
        description="直接传入样本数据",
    )
    process_context: dict[str, Any] = Field(
        default_factory=dict,
        description="工艺上下文覆盖（优先级高于样本内的 process_context）",
    )


# 辅助函数


def _to_signal_sample(req: SignalSampleRequest) -> SignalSample:
    """将 Pydantic 请求转为 SignalSample dataclass。"""
    kwargs = req.model_dump()
    sample_id = kwargs.pop("sample_id", None)
    metadata = kwargs.pop("metadata", None) or {}
    # 若提供 sample_id 则使用，否则让 dataclass 默认生成
    if sample_id:
        kwargs["sample_id"] = sample_id
    if metadata:
        kwargs["metadata"] = metadata
    return SignalSample(**kwargs)


def _fetch_samples_by_ids(sample_ids: list[str]) -> list[SignalSample]:
    """通过 ID 列表从知识库取出样本。"""
    kb = get_signal_fusion_kb()
    all_samples = kb.list_samples(limit=500, offset=0)
    id_set = set(sample_ids)
    return [s for s in all_samples if s.sample_id in id_set]


def _collect_samples(
    sample_ids: list[str],
    samples: list[SignalSampleRequest],
) -> list[SignalSample]:
    """合并 sample_ids 与直接传入的 samples。"""
    result: list[SignalSample] = []
    if sample_ids:
        result.extend(_fetch_samples_by_ids(sample_ids))
    for s_req in samples:
        result.append(_to_signal_sample(s_req))
    return result


# 1. 注册单个样本


@router.post("/samples", dependencies=[Depends(require_permission("signal_kb:write"))])
# P2-4-5 修复：样本注册端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
@safe_endpoint(context="signal_fusion_kb.register_sample", fallback="注册失败")
async def register_sample(request: Request, req: SignalSampleRequest):
    """注册单个信号样本到知识库。"""
    sample = _to_signal_sample(req)
    kb = get_signal_fusion_kb()
    sample_id = kb.register_sample(sample)
    return success(
        data={"sample_id": sample_id},
        message="信号样本已注册",
    )


# 2. 批量注册


@router.post("/samples/batch", dependencies=[Depends(require_permission("signal_kb:write"))])
# P2-4-5 修复：批量注册（最多 500 样本）资源消耗较高，限制为 30/minute。
@limiter.limit("30/minute")
async def register_samples_batch(request: Request, req: BatchSamplesRequest):
    """批量注册信号样本。"""
    try:
        samples = [_to_signal_sample(s) for s in req.samples]
        kb = get_signal_fusion_kb()
        ids = kb.register_samples_batch(samples)
        return success(
            data={"sample_ids": ids, "count": len(ids)},
            message=f"已批量注册 {len(ids)} 个样本",
        )
    except ValueError as e:
        safe = safe_error_message(e, context="signal_fusion_kb.register_samples_batch", fallback="参数错误")
        return error(ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.register_samples_batch", fallback="批量注册失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 3. 列出样本（分页）


@router.get("/samples")
# P2-4-5 修复：查询端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
async def list_samples(
    request: Request,
    # P2-批次2 修复：裸参数改用 Query 校验，避免负数/超大值穿透到 KB 层。
    limit: int = Query(100, ge=1, le=100, description="每页数量（1-500）"),
    offset: int = Query(0, ge=0, description="偏移量（>=0）"),
):
    """列出所有信号融合样本（分页）。"""
    try:
        kb = get_signal_fusion_kb()
        samples = kb.list_samples(limit=limit, offset=offset)
        return success(
            data={
                "count": len(samples),
                "limit": limit,
                "offset": offset,
                "samples": [s.to_dict() for s in samples],
            }
        )
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.list_samples", fallback="列出失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 4. 按信号类型列出


@router.get("/samples/by-type/{signal_type}")
# P2-4-5 修复：查询端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
async def list_by_type(
    request: Request,
    signal_type: str,
    # P2-批次2 修复：裸参数改用 Query 校验。
    limit: int = Query(50, ge=1, le=100, description="每页数量（1-500）"),
):
    """按信号类型列出样本。"""
    try:
        if signal_type not in SUPPORTED_SIGNAL_TYPES:
            return error(
                ErrorCode.INVALID_REQUEST,
                message=f"不支持的信号类型: {signal_type}",
                detail={"supported": list(SUPPORTED_SIGNAL_TYPES)},
            )
        kb = get_signal_fusion_kb()
        samples = kb.retrieve_by_signal_type(signal_type, limit=limit)
        return success(
            data={
                "signal_type": signal_type,
                "count": len(samples),
                "samples": [s.to_dict() for s in samples],
            }
        )
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.list_by_type", fallback="列出失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 5. 检索相似样本


@router.post("/retrieve", dependencies=[Depends(require_permission("signal_kb:read"))])
# P2-4-5 修复：相似样本检索涉及向量计算，限制为 60/minute。
@limiter.limit("60/minute")
async def retrieve_similar(request: Request, req: RetrieveRequest):
    """检索与给定特征向量相似的信号样本。"""
    try:
        kb = get_signal_fusion_kb()
        samples = kb.retrieve_similar(
            features=req.features,
            signal_type=req.signal_type,
            machine_id=req.machine_id,
            material=req.material,
            tool_id=req.tool_id,
            top_k=req.top_k,
        )
        return success(
            data={
                "count": len(samples),
                "top_k": req.top_k,
                "samples": [s.to_dict() for s in samples],
            }
        )
    except ValueError as e:
        safe = safe_error_message(e, context="signal_fusion_kb.retrieve_similar", fallback="参数错误")
        return error(ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.retrieve_similar", fallback="检索失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 6. 多源信号融合


@router.post("/fuse", dependencies=[Depends(require_permission("signal_kb:read"))])
# P2-4-5 修复：多源信号融合涉及加权/注意力计算，限制为 60/minute。
@limiter.limit("60/minute")
async def fuse_signals(request: Request, req: FuseRequest):
    """将多个信号样本融合为统一特征向量。

    注意：``attention`` 策略因依赖未经训练的随机权重默认被拒绝
    （学术诚信守卫），需显式设置 ``LNN_SIGNAL_FUSION_ALLOW_UNTRAINED=1``
    方可使用；生产场景请使用 ``weighted`` 策略。
    """
    try:
        if not req.sample_ids and not req.samples:
            return error(
                ErrorCode.INVALID_REQUEST,
                message="sample_ids 与 samples 至少提供一个",
            )
        if req.strategy not in ("weighted", "attention"):
            return error(
                ErrorCode.INVALID_REQUEST,
                message=f"不支持的融合策略: {req.strategy}",
                detail={"supported": ["weighted", "attention"]},
            )
        # [学术诚信守卫] attention 策略使用未经训练的随机权重（CrossModalAttentionFusion），
        # 默认拒绝；此处显式返回引导信息（不经过 safe_error_message 脱敏，保证可读）。
        if req.strategy == "attention" and os.environ.get(
            "LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", ""
        ).strip().lower() not in ("1", "true", "yes"):
            return error(
                ErrorCode.INVALID_REQUEST,
                message=(
                    "attention 融合策略当前使用未经训练的随机权重，融合结果不具备物理意义，默认禁止。"
                    "建议操作：改用 strategy='weighted'（确定性加权平均，结果可信）；"
                    "或仅在开发调试时设置环境变量 LNN_SIGNAL_FUSION_ALLOW_UNTRAINED=1。"
                ),
                detail={"strategy": req.strategy, "guard": "untrained_attention_fusion"},
            )

        samples = _collect_samples(req.sample_ids, req.samples)
        if not samples:
            return error(
                ErrorCode.NOT_FOUND,
                message="未找到任何可融合的样本（请检查 sample_ids）",
            )

        kb = get_signal_fusion_kb()
        result = kb.fuse_signals(
            samples=samples,
            strategy=req.strategy,
            weights=req.weights,
        )
        return success(
            data=result.to_dict(),
            message=f"已使用 {req.strategy} 策略融合 {len(samples)} 个样本",
        )
    except ValueError as e:
        safe = safe_error_message(e, context="signal_fusion_kb.fuse_signals", fallback="参数错误")
        return error(ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.fuse_signals", fallback="融合失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 7. 关联磨损状态


@router.post("/correlate/wear", dependencies=[Depends(require_permission("signal_kb:read"))])
# P2-4-5 修复：磨损关联涉及样本聚合计算，限制为 60/minute。
@limiter.limit("60/minute")
async def correlate_wear(request: Request, req: CorrelateWearRequest):
    """将信号样本关联为 ToolWearPredictor 可消费的 sensor_features。"""
    try:
        if not req.sample_ids and not req.samples:
            return error(
                ErrorCode.INVALID_REQUEST,
                message="sample_ids 与 samples 至少提供一个",
            )
        samples = _collect_samples(req.sample_ids, req.samples)
        if not samples:
            return error(
                ErrorCode.NOT_FOUND,
                message="未找到任何可关联的样本",
            )

        kb = get_signal_fusion_kb()
        result = kb.correlate_with_wear(samples=samples)
        return success(
            data=result.to_dict(),
            message=f"已关联 {len(samples)} 个样本到磨损预测输入",
        )
    except ValueError as e:
        safe = safe_error_message(e, context="signal_fusion_kb.correlate_wear", fallback="参数错误")
        return error(ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.correlate_wear", fallback="关联失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 8. 关联颤振状态


@router.post("/correlate/chatter", dependencies=[Depends(require_permission("signal_kb:read"))])
# P2-4-5 修复：颤振关联涉及样本聚合计算，限制为 60/minute。
@limiter.limit("60/minute")
async def correlate_chatter(request: Request, req: CorrelateChatterRequest):
    """将信号样本关联为 ChatterPredictor 可消费的特征。"""
    try:
        if not req.sample_ids and not req.samples:
            return error(
                ErrorCode.INVALID_REQUEST,
                message="sample_ids 与 samples 至少提供一个",
            )
        samples = _collect_samples(req.sample_ids, req.samples)
        if not samples:
            return error(
                ErrorCode.NOT_FOUND,
                message="未找到任何可关联的样本",
            )

        kb = get_signal_fusion_kb()
        result = kb.correlate_with_chatter(
            samples=samples,
            process_context=req.process_context or None,
        )
        return success(
            data=result.to_dict(),
            message=f"已关联 {len(samples)} 个样本到颤振预测输入",
        )
    except ValueError as e:
        safe = safe_error_message(e, context="signal_fusion_kb.correlate_chatter", fallback="参数错误")
        return error(ErrorCode.INVALID_REQUEST, message=safe["message"], detail={"error_id": safe["error_id"]})
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.correlate_chatter", fallback="关联失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 9. 知识库统计


@router.get("/stats")
# P2-4-5 修复：查询端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
async def stats(request: Request):
    """返回多源信号融合知识库统计信息。"""
    try:
        kb = get_signal_fusion_kb()
        return success(data=kb.stats())
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.stats", fallback="统计失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 10. 按样本 ID 删除


@router.delete("/samples/{sample_id}", dependencies=[Depends(require_permission("signal_kb:write"))])
# P2-4-5 修复：删除端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
async def delete_sample(request: Request, sample_id: str):
    """按样本 ID 删除。"""
    try:
        kb = get_signal_fusion_kb()
        deleted = kb.delete_sample(sample_id)
        return success(
            data={"sample_id": sample_id, "deleted": deleted},
            message=f"已删除 {deleted} 条样本",
        )
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.delete_sample", fallback="删除失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 11. 按信号类型批量删除


@router.delete("/samples/by-type/{signal_type}", dependencies=[Depends(require_permission("signal_kb:write"))])
# P2-4-5 修复：批量删除可能影响大量数据，限制为 30/minute。
@limiter.limit("30/minute")
async def delete_by_type(request: Request, signal_type: str):
    """按信号类型批量删除。"""
    try:
        if signal_type not in SUPPORTED_SIGNAL_TYPES:
            return error(
                ErrorCode.INVALID_REQUEST,
                message=f"不支持的信号类型: {signal_type}",
                detail={"supported": list(SUPPORTED_SIGNAL_TYPES)},
            )
        kb = get_signal_fusion_kb()
        deleted = kb.delete_by_signal_type(signal_type)
        return success(
            data={"signal_type": signal_type, "deleted": deleted},
            message=f"已删除 {deleted} 条 {signal_type} 样本",
        )
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.delete_by_type", fallback="删除失败")
        return error(ErrorCode.INTERNAL_ERROR, message=safe["message"], detail={"error_id": safe["error_id"]})


# 12. 健康检查


@router.get("/health")
# P2-4-5 修复：健康检查端点添加速率限制，限制为 120/minute。
@limiter.limit("120/minute")
async def health(request: Request):
    """健康检查。"""
    try:
        kb = get_signal_fusion_kb()
        # 触发懒加载但不强制写入
        kb._get_vector_store()
        return success(
            data={
                "status": "healthy",
                "supported_signal_types": list(SUPPORTED_SIGNAL_TYPES),
                "vector_store_loaded": kb._vector_store is not None,
            }
        )
    except Exception as e:
        safe = safe_error_message(e, context="signal_fusion_kb.health", fallback="知识库不可用")
        return error(
            ErrorCode.SERVICE_UNAVAILABLE,
            message=safe["message"],
            detail={"error_id": safe["error_id"]},
        )
