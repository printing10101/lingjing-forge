"""推理相关端点 + 其他 Agent Gateway 端点。

P1-7：从原 ``agent_gateway.py`` 拆分而来，包含：
- ``GET  /health`` —— 健康检查
- ``GET  /models`` —— 已注册模型列表
- ``GET  /models/{name}/info`` —— 模型详细信息
- ``POST /predict`` —— LNN 预测
- ``POST /execute`` —— 工艺参数下发（Paper-Only 模拟；实模式在下发通道接线前显式 501）
- ``GET  /audit-log`` —— 审计日志查询
- ``POST /tokens`` —— 创建 Agent Token
- ``GET  /tokens`` —— 列出 Agent Token
- ``DELETE /tokens/{agent_id}`` —— 撤销 Agent Token
- ``POST /tokens/revoke-t-all`` —— 一键撤销所有 T 类 Token
- ``POST /pipeline/execute`` —— 执行工作流管线
- ``GET  /pipeline/history`` —— 查询管线执行历史
- ``GET  /pipeline/{pipeline_id}/trace`` —— 获取管线执行追踪详情
"""

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from app.agent.auth import agent_token_store
from app.agent.middleware import (
    agent_audit_log,
)
from app.api.v1.agent_gateway._state import (
    LNNPredictor,
    PredictionResult,
    agent_model_cache,
    model_registry,
    orchestrator,
)
from app.auth.permissions import paper_only_guard, require_permission
from app.core.response import ErrorCode, error, success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message
from app.middleware.rate_limiter import limiter
from app.models.schemas import (
    AgentExecuteRequest,
    AgentPipelineRequest,
    AgentPredictRequest,
    AgentTokenCreateRequest,
    AgentTokenResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])


@router.get(
    "/health",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def agent_health():
    """健康检查（R类）"""
    return success(
        data={
            "status": "healthy",
            "timestamp": time.time(),
            "models_registered": len(model_registry.registry),
        }
    )


@router.get(
    "/models",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def list_models():
    """已注册模型列表（R类）"""
    models = model_registry.list_models(return_objects=True)
    return success(
        data={
            "models": [
                {
                    "name": m.name,
                    "model_type": m.model_type,
                    "version": m.version,
                    "input_features": m.input_features,
                    "output_features": m.output_features,
                }
                for m in models
            ],
            "total": len(models),
        }
    )


@router.get(
    "/models/{name}/info",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def model_info(name: str):
    """模型详细信息（R类）"""
    entry = model_registry.registry.get(name)
    if not entry:
        return error(code=ErrorCode.NOT_FOUND, message=f"Model '{name}' not found")

    info = entry.info
    return success(
        data={
            "name": info.name,
            "model_type": info.model_type,
            "model_path": info.model_path,
            "input_features": info.input_features,
            "output_features": info.output_features,
            "version": info.version,
            "is_loaded": entry.is_loaded,
            "access_count": entry.access_count,
        }
    )


# 认证：预测操作需要 agent:predict 权限
@router.post(
    "/predict",
    dependencies=[Depends(require_permission("agent:predict"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
# P2-4-3 修复：LNN 推理消耗 GPU/CPU 计算资源，需速率限制防止 DoS。
@limiter.limit("60/minute")
async def agent_predict(request: Request, body: AgentPredictRequest):
    """LNN 预测（R类）"""
    try:
        entry = model_registry.registry.get(body.model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{body.model_name}' not found",
            )

        if not body.input_data or any(not isinstance(x, (int, float)) for x in body.input_data):
            return error(code=ErrorCode.INVALID_REQUEST, message="输入数据必须为非空数值数组")

        expected_dim = len(entry.info.input_features) if entry.info.input_features else None
        if expected_dim:
            input_len = len(body.input_data)
            if input_len != expected_dim and input_len % expected_dim != 0:
                return error(
                    code=ErrorCode.INVALID_REQUEST,
                    message=f"输入维度不匹配: 期望{expected_dim}维或其倍数，实际{input_len}维",
                )

        predictor = agent_model_cache.get(body.model_name)
        if predictor is None:
            predictor = LNNPredictor.from_registry(
                registry=model_registry,
                model_name=body.model_name,
                use_amp=True,
                auto_device=True,
            )
            agent_model_cache.put(body.model_name, predictor)

        result = predictor.predict(
            input_data=body.input_data,
            return_confidence=body.return_confidence,
        )

        if not isinstance(result, PredictionResult):
            result = PredictionResult(value=result, confidence=0.0, inference_time=0.0)

        value = result.value
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, list) and len(value) == 1:
            value = value[0]

        resp = {
            "value": value,
            "inference_time": result.inference_time,
            "model_info": {
                "name": entry.info.name,
                "version": entry.info.version,
            },
        }
        if body.return_confidence:
            resp["confidence"] = result.confidence

        return success(data=resp, message="Prediction completed")

    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError, OSError) as e:
        # 修复：使用 safe_error_message 包装异常，避免 str(e) 泄露
        # 内部错误详情到前端用户/调用方。
        safe = safe_error_message(e, context=f"agent.predict[{body.model_name}]")
        logger.warning(
            "Prediction failed | model=%s | error_id=%s | exc=%s: %s",
            body.model_name,
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


# 认证：执行类操作需要 agent:execute 权限
@router.post(
    "/execute",
    dependencies=[Depends(require_permission("agent:execute"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def agent_execute(request: AgentExecuteRequest, http_request: Request):
    """工艺参数下发（T类，paper_only默认）"""
    try:
        # [F-P0-4] 获取操作员标识用于审计留痕
        operator = getattr(http_request.state, "username", None) or "unknown"

        # Paper-Only 安全检查
        is_live = paper_only_guard.is_live_execution_allowed()
        if request.simulate or not is_live:
            result = paper_only_guard.simulate_t_operation(
                {
                    "machine_id": request.machine_id,
                    "parameters": request.parameters,
                },
                operator=operator,
            )
            return success(data=result, message="Operation simulated (Paper-Only mode)")

        # [F-P0-4] 实模式：必须通过双因子确认 + 机床安全前置校验
        # - has_t_permission: 权限已由 require_permission 依赖项校验，此处置 True
        # - ui_confirmed: simulate=False 表示用户在 UI 上明确选择实模式执行
        # - supervisor_confirmed: 班长双因子确认（请求体显式传入）
        # - machine_safety_status: 机床物理安全状态（请求体显式传入）
        allowed, reason = paper_only_guard.check_t_operation(
            has_t_permission=True,
            ui_confirmed=not request.simulate,
            supervisor_confirmed=request.supervisor_confirmed,
            machine_safety_status=request.machine_safety_status,
        )
        if not allowed:
            # 审计留痕：实模式被拒绝也必须记录
            logger.warning(
                "T operation rejected | machine_id=%s | operator=%s | reason=%s",
                request.machine_id,
                operator,
                reason,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=reason,
            )

        # Actual execution (requires LNN_LIVE_EXECUTION_ENABLED=true + 双因子确认通过)
        # [学术诚信/安全修复] 此前此处直接返回写死的 "executed"，但机床下发通道
        # （参数 → OPC UA 节点 / FOCAS 句柄的映射）从未接线，形成"已下发"假象。
        # 实模式现在必须显式失败：没有任何真实通道时禁止谎报执行成功。
        # 注意：用 JSONResponse 直接返回固定引导文案——HTTPException(5xx) 会被
        # 全局异常处理器脱敏成"系统内部错误"，用户将看不到原因；本消息为
        # 固定常量、不含任何异常内部细节，无泄露风险。
        logger.warning(
            "T operation reached live dispatch but no channel is wired | machine_id=%s | operator=%s | param_keys=%s",
            request.machine_id,
            operator,
            list(request.parameters.keys()),
        )
        return JSONResponse(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            content=error(
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message=(
                    "实模式机床下发通道未接线：系统尚无工艺参数到 DNC/OPC UA 节点的映射，"
                    "无法真实下发参数，已阻止本次执行。"
                ),
                suggestion=(
                    "使用 simulate=true（Paper-Only 模式）获取模拟结果；或通过 DNC 模块下发经过校验的 NC 程序。"
                ),
                detail={"machine_id": request.machine_id, "guard": "live_dispatch_not_wired"},
            ),
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        # 修复：使用 safe_error_message 包装异常，避免直接
        # 将 str(e) 暴露到 HTTP 错误响应中。
        safe = safe_error_message(e, context="agent.execute")
        logger.warning(
            "Execute operation failed | machine_id=%s | error_id=%s | exc=%s: %s",
            request.machine_id,
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


# 认证：审计日志查询需要 agent:audit:read 权限
@router.get(
    "/audit-log",
    dependencies=[Depends(require_permission("agent:audit:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def get_audit_log(
    agent_id: str | None = None,
    permission_class: str | None = None,
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    """审计日志查询（C类，仅管理员）"""
    entries = agent_audit_log.get_entries(
        agent_id=agent_id,
        permission_class=permission_class,
        limit=limit,
        offset=offset,
    )
    return success(
        data={
            "entries": entries,
            "total": len(entries),
            "limit": limit,
            "offset": offset,
        },
        message="Audit log retrieved",
    )


# Token management endpoints (for internal use / settings page)
# 认证：创建 Token 需要 agent:token:create 权限
@router.post(
    "/tokens",
    dependencies=[Depends(require_permission("agent:token:create"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def create_agent_token(req: AgentTokenCreateRequest):
    """创建 Agent Token"""
    try:
        for scope in req.scopes:
            if scope not in ("R", "W", "B", "N", "C", "T"):
                return error(code=ErrorCode.INVALID_REQUEST, message=f"Invalid scope: {scope}")

        raw_token, token = agent_token_store.create_token(
            scopes=req.scopes,
            expires_in=req.expires_in,
            paper_only=req.paper_only,
        )

        return success(
            data=AgentTokenResponse(
                agent_id=token.agent_id,
                token=raw_token,
                scopes=token.scopes,
                created_at=token.created_at,
                expires_at=token.expires_at,
                paper_only=token.paper_only,
            ).model_dump(),
            message="Agent token created successfully. Save the token now, it will not be shown again.",
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        # 修复：使用 safe_error_message 包装异常，避免直接
        # 将 str(e) 暴露到 HTTP 错误响应中。
        safe = safe_error_message(e, context="agent.create_token")
        logger.warning(
            "Token creation failed | error_id=%s | exc=%s: %s",
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


# 认证：列出 Token 需要 agent:token:create 权限（Token 管理操作）
@router.get(
    "/tokens",
    dependencies=[Depends(require_permission("agent:token:create"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def list_agent_tokens():
    """列出所有 Agent Token"""
    tokens = agent_token_store.list_tokens()
    return success(data={"tokens": tokens, "total": len(tokens)})


# 认证：撤销单个 Token 需要 agent:token:revoke 权限
@router.delete(
    "/tokens/{agent_id}",
    dependencies=[Depends(require_permission("agent:token:revoke"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def revoke_agent_token(agent_id: str):
    """撤销 Agent Token"""
    if agent_token_store.revoke_token(agent_id):
        return success(data={"agent_id": agent_id}, message="Token revoked")
    return error(code=ErrorCode.NOT_FOUND, message=f"Token '{agent_id}' not found")


# 认证：一键撤销所有 T 类 Token 需要 agent:token:revoke_all 权限（紧急停止）
@router.post(
    "/tokens/revoke-t-all",
    dependencies=[Depends(require_permission("agent:token:revoke_all"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def revoke_all_t_tokens():
    """一键撤销所有 T 类 Token（紧急停止）"""
    count = agent_token_store.revoke_t_tokens()
    return success(data={"revoked_count": count}, message=f"Revoked {count} T-class tokens")


# Workflow Pipeline Execution Endpoints


# 认证：管线执行属于执行类操作，需要 agent:execute 权限
@router.post(
    "/pipeline/execute",
    dependencies=[Depends(require_permission("agent:execute"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def execute_pipeline(request: AgentPipelineRequest):
    """
    执行工作流管线（B类，需要认证）

    通过 AgentOrchestrator 执行多步骤业务流程，支持：
    - 顺序执行模式（SEQUENTIAL）
    - 条件执行模式（CONDITIONAL）

    管线类型：
    - process_planning: 工艺规划管线（DXF解析 → 工艺理解 → 参数推荐 → G代码生成）
    - model_training: 模型训练管线（数据验证 → 训练 → 评估 → 部署）
    - quality_analysis: 质量分析管线（数据采集 → 特征提取 → 预测 → 报告生成）
    """
    try:
        # 记录审计日志
        agent_audit_log.log(
            agent_id=request.agent_id or "unknown",
            route=f"pipeline:{request.pipeline_type}",
            permission_class="B",
            status_code=200,
            latency_ms=0.0,
            details={
                "action": "pipeline.execute",
                "pipeline_type": request.pipeline_type,
                "mode": request.mode,
                "input_keys": list(request.input_data.keys()),
            },
        )

        # 执行管线
        from app.agent.orchestrator import OrchestratorMode

        result = await orchestrator.execute_pipeline(
            pipeline_type=request.pipeline_type,
            input_data=request.input_data,
            mode=OrchestratorMode(request.mode),
        )

        # 根据结果状态返回相应响应
        if result.success:
            return success(
                data={
                    "pipeline_id": result.pipeline_id,
                    "trace_id": result.trace_id,
                    "status": "completed",
                    "steps": [
                        {
                            "name": step.step_name,
                            "status": step.status.value,
                            "duration_ms": step.duration_ms,
                            "output_keys": list(step.output.keys()) if step.output else [],
                        }
                        for step in result.steps
                    ],
                    "total_duration_ms": result.total_duration_ms,
                    "final_output": result.final_output,
                },
                message=f"Pipeline '{request.pipeline_type}' executed successfully",
            )
        else:
            # 管线失败但有降级处理
            return success(
                data={
                    "pipeline_id": result.pipeline_id,
                    "trace_id": result.trace_id,
                    "status": "completed_with_fallback",
                    "steps": [
                        {
                            "name": step.step_name,
                            "status": step.status.value,
                            "duration_ms": step.duration_ms,
                            "error": step.error,
                        }
                        for step in result.steps
                    ],
                    "total_duration_ms": result.total_duration_ms,
                    "fallback_triggered": result.fallback_triggered,
                    "fallback_reason": result.fallback_reason,
                    "final_output": result.final_output,
                },
                message=f"Pipeline '{request.pipeline_type}' completed with fallback",
            )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"agent.pipeline[{request.pipeline_type}]")
        logger.error(
            "Pipeline execution failed | pipeline_type=%s | error_id=%s | exc=%s: %s",
            request.pipeline_type,
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get(
    "/pipeline/history",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={500: {"model": ErrorResponse}},
)
async def get_pipeline_history(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0, le=10000),
):
    """
    查询管线执行历史（R类，需要认证）

    返回最近执行的管线记录，包括执行状态、耗时、步骤详情等。
    """
    try:
        history = orchestrator.get_pipeline_history(limit=limit, offset=offset)

        return success(
            data={
                "pipelines": [
                    {
                        "pipeline_id": r.pipeline_id,
                        "trace_id": r.trace_id,
                        "success": r.success,
                        "timestamp": r.timestamp,
                        "total_duration_ms": r.total_duration_ms,
                        "step_count": len(r.steps),
                        "fallback_triggered": r.fallback_triggered,
                    }
                    for r in history
                ],
                "total": len(history),
                "limit": limit,
                "offset": offset,
            },
            message="Pipeline history retrieved",
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context="agent.pipeline_history")
        logger.warning(
            "Pipeline history query failed | error_id=%s | exc=%s: %s",
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get(
    "/pipeline/{pipeline_id}/trace",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def get_pipeline_trace(pipeline_id: str):
    """
    获取管线执行追踪详情（R类，需要认证）

    返回指定管线的完整执行追踪信息，包括每个步骤的输入输出、耗时、错误信息等。
    """
    try:
        trace = orchestrator.get_pipeline_trace(pipeline_id)

        if not trace:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Pipeline trace '{pipeline_id}' not found",
            )

        return success(
            data=trace,
            message="Pipeline trace retrieved",
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"agent.pipeline_trace[{pipeline_id}]")
        logger.warning(
            "Pipeline trace retrieval failed | pipeline_id=%s | error_id=%s | exc=%s: %s",
            pipeline_id,
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
