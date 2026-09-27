"""SHARP 三元组验证 REST API（M5.3）。

前缀：``/api/v1/sharp``

端点：
    - POST /verify                          单条三元组验证
    - POST /verify/batch                    批量三元组验证
    - GET  /status                          服务状态
    - GET  /trajectory/{verification_id}    按 ID 取历史轨迹
    - POST /trajectory/query                查询历史轨迹（带过滤）
    - DELETE /trajectory                    清空轨迹库
    - GET  /ablation                        查询当前消融模式
    - POST /ablation                        切换消融模式

设计要点
--------
- **单例服务**：通过 ``SharpService.instance()`` 拿到组装好的 SHARP pipeline
- **权限注入**：复用 ``require_permission`` 装饰器（sharp:read / sharp:write）
- **异常包装**：所有端点 try-except 把内部异常转为 503，避免堆栈泄露
- **OpenAPI 友好**：所有请求/响应均使用 Pydantic 模型，自动生成 schema
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException

from app.auth.permissions import require_permission
from app.core.safe_errors import safe_error_message
from app.sharp.schemas import (
    AblationInfo,
    AblationUpdateRequest,
    BatchVerifyItem,
    BatchVerifyRequest,
    BatchVerifyResponse,
    StatusResponse,
    TrajectoryListResponse,
    TrajectoryQueryRequest,
    TrajectoryRecord,
    VerifyRequest,
    VerifyResponse,
)
from app.sharp.schema.domain_schema import Triple
from app.sharp.service import SharpService, VALID_ABLATION_MODES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/sharp", tags=["SHARP 三元组验证"])


# 异常包装（与项目其他 API 风格一致）


def _raise_internal(
    exc: BaseException,
    *,
    context: str,
    fallback: str,
    status_code: int = 500,
) -> NoReturn:
    """统一的 HTTPException 5xx 包装：避免将内部异常细节泄露给客户端。"""
    safe = safe_error_message(exc, context=context, fallback=fallback)
    headers = {"X-Error-ID": safe.get("error_id", "")}
    detail: Any = safe.get("message")
    if "detail" in safe:
        detail = f"{safe['message']} ({safe['detail']})"
    raise HTTPException(status_code=status_code, detail=detail, headers=headers)


# 验证端点


@router.post(
    "/verify",
    response_model=VerifyResponse,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def verify_triple(request: VerifyRequest) -> VerifyResponse:
    """验证单个三元组。

    使用 SHARP 智能体执行完整 ReAct 循环，返回 verdict / confidence /
    证据链 / 完整轨迹。
    """
    try:
        triple = Triple.from_dict(request.triple.to_triple_dict())
        service = SharpService.instance()
        result = await service.verify(
            triple=triple,
            ablation_mode=request.ablation_mode,
            max_react_steps=request.max_react_steps,
        )
        return VerifyResponse.from_result(result, return_trajectory=request.return_trajectory)
    except HTTPException:
        raise
    except ValueError as e:
        # 包装异常消息，避免直接回显内部错误细节
        safe = safe_error_message(e, context="sharp.verify", fallback="参数验证失败")
        raise HTTPException(
            status_code=400,
            detail=safe["message"],
            headers={"X-Error-ID": safe["error_id"]},
        )
    except (RuntimeError, OSError, AttributeError, TypeError, KeyError) as e:
        logger.exception("SHARP verify failed")
        _raise_internal(e, context="sharp.verify", fallback="三元组验证失败")


@router.post(
    "/verify/batch",
    response_model=BatchVerifyResponse,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def verify_batch(request: BatchVerifyRequest) -> BatchVerifyResponse:
    """批量验证三元组（单次最多 50 条）。"""
    try:
        triples = [Triple.from_dict(t.to_triple_dict()) for t in request.triples]
        service = SharpService.instance()
        raw_results = await service.batch_verify(
            triples=triples,
            ablation_mode=request.ablation_mode,
            max_react_steps=request.max_react_steps,
        )

        items: list[BatchVerifyItem] = []
        summary: dict[str, int] = {"supported": 0, "refuted": 0, "uncertain": 0}
        succeeded = 0
        failed = 0

        for idx, result, error in raw_results:
            if error is not None or result is None:
                failed += 1
                items.append(
                    BatchVerifyItem(
                        index=idx,
                        verification_id="",
                        triple=triples[idx].as_dict(),
                        verdict="uncertain",
                        confidence=0.0,
                        reasoning="",
                        steps_taken=0,
                        elapsed_ms=0.0,
                        error=error or "未知错误",
                    )
                )
                continue

            succeeded += 1
            if result.verdict in summary:
                summary[result.verdict] += 1

            d = result.to_dict()
            items.append(
                BatchVerifyItem(
                    index=idx,
                    verification_id=d["verification_id"],
                    triple=d["triple_detail"],
                    verdict=d["verdict"],
                    confidence=d["confidence"],
                    reasoning=d["reasoning"],
                    steps_taken=d["steps_taken"],
                    elapsed_ms=d["elapsed_ms"],
                    error=None,
                )
            )

        return BatchVerifyResponse(
            total=len(triples),
            succeeded=succeeded,
            failed=failed,
            summary=summary,
            results=items,
        )
    except HTTPException:
        raise
    except ValueError as e:
        # 包装异常消息，避免直接回显内部错误细节
        safe = safe_error_message(e, context="sharp.verify_batch", fallback="参数验证失败")
        raise HTTPException(
            status_code=400,
            detail=safe["message"],
            headers={"X-Error-ID": safe["error_id"]},
        )
    except (RuntimeError, OSError, AttributeError, TypeError, KeyError) as e:
        logger.exception("SHARP batch verify failed")
        _raise_internal(e, context="sharp.verify_batch", fallback="批量验证失败")


# 状态端点


@router.get(
    "/status",
    response_model=StatusResponse,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def get_status() -> StatusResponse:
    """获取 SHARP 服务状态。"""
    try:
        service = SharpService.instance()
        status = service.get_status()
        return StatusResponse(
            version=status["version"],
            enabled_components=status["enabled_components"],
            tool_registry_size=status["tool_registry_size"],
            trajectory_count=status["trajectory_count"],
            ablation_mode=status["ablation_mode"],
        )
    except (RuntimeError, OSError, AttributeError) as e:
        logger.exception("SHARP status failed")
        _raise_internal(e, context="sharp.status", fallback="获取状态失败")


# 轨迹端点


@router.get(
    "/trajectory/{verification_id}",
    response_model=TrajectoryRecord,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def get_trajectory(verification_id: str) -> TrajectoryRecord:
    """按 verification_id 取单条历史轨迹。"""
    try:
        service = SharpService.instance()
        record = service.get_trajectory(verification_id)
        if record is None:
            logger.info("轨迹不存在: %s", verification_id)
            raise HTTPException(
                status_code=404,
                detail="轨迹不存在",
            )
        return TrajectoryRecord(**record.to_dict())
    except HTTPException:
        raise
    except (RuntimeError, OSError, AttributeError, TypeError) as e:
        logger.exception("SHARP get_trajectory failed")
        _raise_internal(e, context="sharp.trajectory_get", fallback="查询轨迹失败")


@router.post(
    "/trajectory/query",
    response_model=TrajectoryListResponse,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def query_trajectories(
    request: TrajectoryQueryRequest,
) -> TrajectoryListResponse:
    """查询历史轨迹列表（带过滤）。"""
    try:
        service = SharpService.instance()
        records = service.list_trajectories(
            limit=request.limit,
            verdict=request.verdict,
            relation=request.relation,
        )
        return TrajectoryListResponse(
            total=len(records),
            records=[TrajectoryRecord(**r.to_dict()) for r in records],
        )
    except (RuntimeError, OSError, AttributeError, TypeError) as e:
        logger.exception("SHARP trajectory query failed")
        _raise_internal(e, context="sharp.trajectory_query", fallback="查询轨迹失败")


@router.delete(
    "/trajectory",
    dependencies=[Depends(require_permission("sharp:write"))],
)
async def clear_trajectories() -> dict[str, Any]:
    """清空轨迹库（需 sharp:write 权限）。"""
    try:
        service = SharpService.instance()
        cleared = service.clear_trajectories()
        return {"cleared": cleared, "message": f"已清空 {cleared} 条历史轨迹"}
    except (RuntimeError, OSError, AttributeError) as e:
        logger.exception("SHARP clear trajectories failed")
        _raise_internal(e, context="sharp.trajectory_clear", fallback="清空轨迹失败")


# 消融配置端点


@router.get(
    "/ablation",
    response_model=AblationInfo,
    dependencies=[Depends(require_permission("sharp:read"))],
)
async def get_ablation() -> AblationInfo:
    """查询当前消融模式与可选模式列表。"""
    try:
        service = SharpService.instance()
        current = service.get_ablation_mode()
        descriptions = {
            None: "完整 SHARP（4 组件全部启用）",
            "no_schema": "禁用 Schema-Aware 规划器，回退到统一策略",
            "no_memory": "禁用 Memory-Augmented 机制",
            "no_react": "禁用 ReAct 循环，仅单次 LLM 推理",
            "no_toolset": "禁用 Hybrid Knowledge Toolset，仅保留 LLM 推理工具",
        }
        return AblationInfo(
            current_mode=current,
            available_modes=list(VALID_ABLATION_MODES),
            description=descriptions.get(current, "未知模式"),
        )
    except (RuntimeError, OSError, AttributeError) as e:
        logger.exception("SHARP ablation get failed")
        _raise_internal(e, context="sharp.ablation_get", fallback="查询消融模式失败")


@router.post(
    "/ablation",
    response_model=AblationInfo,
    dependencies=[Depends(require_permission("sharp:write"))],
)
async def set_ablation(request: AblationUpdateRequest) -> AblationInfo:
    """切换消融模式（需 sharp:write 权限）。

    切换后会重建 SHARP pipeline，已有的 LLMRouter / KG / RAG 单例会被复用。
    """
    try:
        service = SharpService.instance()
        service.set_ablation_mode(request.mode)
        return await get_ablation()
    except HTTPException:
        raise
    except ValueError as e:
        # 包装异常消息，避免直接回显内部错误细节
        safe = safe_error_message(e, context="sharp.set_ablation", fallback="参数验证失败")
        raise HTTPException(
            status_code=400,
            detail=safe["message"],
            headers={"X-Error-ID": safe["error_id"]},
        )
    except (RuntimeError, OSError, AttributeError) as e:
        logger.exception("SHARP ablation set failed")
        _raise_internal(e, context="sharp.ablation_set", fallback="切换消融模式失败")


__all__ = ["router"]
