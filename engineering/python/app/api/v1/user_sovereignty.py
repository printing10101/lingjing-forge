import uuid
import logging
from fastapi import APIRouter, Depends
from datetime import datetime, timezone

from app.auth.dependencies import get_current_user
from app.auth.permissions import require_permission

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.audit.audit_log import AuditLog, AIModule, UserDecision, OperationStatus
from app.models.schemas import (
    LNNPredictRequest,
    AlternativePlan,
    LNNModelInfo,
    AuditLogQueryRequest,
    AuditLogSearchRequest,
    AuditLogExportRequest,
    UserSovereigntySettings,
)
from app.dependencies import get_model_registry_service
from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from app.api.v1._shared.task_infra import handle_sovereignty_errors

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/user-sovereignty",
    tags=["User Sovereignty"],
    dependencies=[Depends(require_permission("user:read"))],
)

model_registry = get_model_registry_service().model_registry
audit_log = AuditLog()

# 备选方案参数调整乘数（±5%）
CONSERVATIVE_FACTOR = 0.95
AGGRESSIVE_FACTOR = 1.05
# 备选方案置信度衰减
CONSERVATIVE_CONFIDENCE_DROP = 0.05
AGGRESSIVE_CONFIDENCE_DROP = 0.08
# 置信度下限
CONFIDENCE_FLOOR = 0.0


@router.post("/predict")
@handle_sovereignty_errors(
    context="user_sovereignty.predict",
    log_tag="User sovereignty predict",
    catch_key_error=True,
    catch_attribute_error=True,
)
async def predict_with_sovereignty(request: LNNPredictRequest):
    entry = model_registry.registry.get(request.model_name)
    if not entry:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{request.model_name}' not found",
        )

    model_info = entry.info

    if not request.input_data:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="输入数据必须为非空列表",
        )

    if any(not isinstance(x, (int, float)) for x in request.input_data):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="输入数据必须为数值类型",
        )

    expected_dim = len(model_info.input_features) if model_info.input_features else None
    if expected_dim:
        input_len = len(request.input_data)
        if input_len != expected_dim and input_len % expected_dim != 0:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"输入维度不匹配: 期望{expected_dim}维或其倍数，实际{input_len}维",
            )

    predictor = LNNPredictor.from_registry(
        registry=model_registry,
        model_name=request.model_name,
        use_amp=True,
        auto_device=True,
    )

    try:
        result = predictor.predict(
            input_data=request.input_data,
            return_confidence=True,
        )
    except ValueError as ve:
        # 模型推理输入值无效
        logger.error(
            "User sovereignty inference value error | model=%s | err=%s",
            request.model_name,
            ve,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"模型推理输入值无效: {ve}",
        )
    except TypeError as te:
        # 模型推理输入类型错误
        logger.error(
            "User sovereignty inference type error | model=%s | err=%s",
            request.model_name,
            te,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"模型推理输入类型错误: {te}",
        )
    except RuntimeError as rte:
        # 模型推理运行时错误
        safe = safe_error_message(rte, context=f"user_sovereignty.predict_inference[{request.model_name}]")
        logger.error(
            "User sovereignty inference runtime error | model=%s | error_id=%s | exc=%s",
            request.model_name,
            safe.get("error_id"),
            rte,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
    except (RuntimeError, ValueError, TypeError, AttributeError) as specific_err:
        # 捕获模型推理相关的常见异常
        safe = safe_error_message(specific_err, context=f"user_sovereignty.predict_inference[{request.model_name}]")
        logger.error(
            "User sovereignty inference specific error | model=%s | error_id=%s | exc=%s",
            request.model_name,
            safe.get("error_id"),
            specific_err,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
    except Exception as model_err:
        # 兜底：捕获未预期的异常
        safe = safe_error_message(model_err, context=f"user_sovereignty.predict_inference[{request.model_name}]")
        logger.error(
            "User sovereignty inference unexpected error | model=%s | error_id=%s | exc=%s",
            request.model_name,
            safe.get("error_id"),
            model_err,
            exc_info=True,
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )

    if not isinstance(result, PredictionResult):
        result = PredictionResult(
            value=result,
            confidence=0.0,
            inference_time=0.0,
        )

    value = result.value
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list) and len(value) == 1:
        value = value[0]

    confidence = result.confidence if result.confidence is not None else 0.0

    reasoning = generate_prediction_reasoning(
        model_name=request.model_name,
        input_data=request.input_data,
        prediction=value,
        confidence=confidence,
        inference_time=result.inference_time,
    )

    alternatives = generate_alternatives(
        model_name=request.model_name,
        input_data=request.input_data,
        primary_value=value,
        primary_confidence=confidence,
    )

    model_info_response = LNNModelInfo(
        name=model_info.name,
        version=model_info.version,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )

    response_data = {
        "value": value,
        "confidence": confidence,
        "reasoning": reasoning,
        "inference_time": result.inference_time,
        "model_info": model_info_response.model_dump(),
        "alternatives": [alt.model_dump() for alt in alternatives],
    }

    return success(
        data=response_data,
        message="Prediction completed with full sovereignty context",
    )


def generate_prediction_reasoning(
    model_name: str,
    input_data: list[float],
    prediction: float | list[float],
    confidence: float,
    inference_time: float,
) -> str:
    reasoning_parts = [
        f"模型 {model_name} 基于输入的 {len(input_data)} 个特征进行推理。",
        f"预测值为 {prediction if isinstance(prediction, (int, float)) else f'{len(prediction)} 个输出值'}。",
    ]

    if confidence >= 0.8:
        reasoning_parts.append(f"置信度较高 ({confidence:.2f})，表明模型对当前输入数据的预测结果有较高的把握。")
    elif confidence >= 0.5:
        reasoning_parts.append(f"置信度中等 ({confidence:.2f})，建议结合实际情况综合判断预测结果。")
    else:
        reasoning_parts.append(f"置信度较低 ({confidence:.2f})，建议参考备选方案或调整输入数据后重新预测。")

    reasoning_parts.append(f"推理耗时 {inference_time:.2f}ms。")

    return " ".join(reasoning_parts)


def _adjust_value(value: float | list[float], factor: float) -> float | list[float]:
    """对 list 或 scalar 统一应用乘数。"""
    if isinstance(value, list):
        return [v * factor for v in value]
    return value * factor


def generate_alternatives(
    model_name: str,
    input_data: list[float],
    primary_value: float | list[float],
    primary_confidence: float,
) -> list[AlternativePlan]:
    alternatives = []
    is_scalar = isinstance(primary_value, (int, float))

    if is_scalar:
        alt_1_value = _adjust_value(primary_value, CONSERVATIVE_FACTOR)
        alt_2_value = _adjust_value(primary_value, AGGRESSIVE_FACTOR)
        conservative_outcome = f"保守方案：预测值 {alt_1_value:.4f}，偏向安全边际，适合对稳定性要求高的场景。"
        aggressive_outcome = f"激进方案：预测值 {alt_2_value:.4f}，偏向性能优化，适合追求效率的场景。"
        conservative_reasoning = "保守方案通过降低预测值约5%提供额外的安全缓冲，适用于风险敏感型决策。"
        aggressive_reasoning = "激进方案通过提高预测值约5%追求性能最优，适用于对效率要求高的场景。"
    else:
        conservative_outcome = "保守方案：输出值整体下调5%，偏向安全边际，适合对稳定性要求高的场景。"
        aggressive_outcome = "激进方案：输出值整体上调5%，偏向性能优化，适合追求效率的场景。"
        conservative_reasoning = "保守方案通过降低各输出值约5%提供额外的安全缓冲，适用于风险敏感型决策。"
        aggressive_reasoning = "激进方案通过提高各输出值约5%追求性能最优，适用于对效率要求高的场景。"

    alternatives.append(
        AlternativePlan(
            plan_id=f"alt_{uuid.uuid4().hex[:8]}",
            parameters={
                "optimization_target": "conservative",
                "safety_margin": "+5%",
            },
            expected_outcome=conservative_outcome,
            confidence=max(CONFIDENCE_FLOOR, primary_confidence - CONSERVATIVE_CONFIDENCE_DROP),
            reasoning=conservative_reasoning,
        )
    )

    alternatives.append(
        AlternativePlan(
            plan_id=f"alt_{uuid.uuid4().hex[:8]}",
            parameters={
                "optimization_target": "aggressive",
                "efficiency_gain": "+5%",
            },
            expected_outcome=aggressive_outcome,
            confidence=max(CONFIDENCE_FLOOR, primary_confidence - AGGRESSIVE_CONFIDENCE_DROP),
            reasoning=aggressive_reasoning,
        )
    )

    return alternatives


@router.post("/audit-log/record")
@handle_sovereignty_errors(
    context="user_sovereignty.record_decision",
    log_tag="Failed to record audit log",
)
async def record_user_decision(
    ai_module: str,
    ai_recommendation: dict,
    user_decision: str,
    final_execution: dict,
    operation_status: str,
    user_id: str | None = None,
    username: str | None = None,
    input_parameters: dict | None = None,
    confidence: float | None = None,
    reasoning: str | None = None,
    user_modifications: dict | None = None,
    metadata: dict | None = None,
):
    module_map = {
        "lnn_predict": AIModule.LNN_PREDICT,
        "lnn_train": AIModule.LNN_TRAIN,
        "process_optimize": AIModule.PROCESS_OPTIMIZE,
        "tool_wear_analyze": AIModule.TOOL_WEAR_ANALYZE,
        "cad_generate": AIModule.CAD_GENERATE,
    }

    decision_map = {
        "accept": UserDecision.ACCEPT,
        "modify": UserDecision.MODIFY,
        "reject": UserDecision.REJECT,
        "auto_executed": UserDecision.AUTO_EXECUTED,
    }

    status_map = {
        "success": OperationStatus.SUCCESS,
        "failed": OperationStatus.FAILED,
        "cancelled": OperationStatus.CANCELLED,
        "pending": OperationStatus.PENDING,
    }

    ai_module_enum = module_map.get(ai_module)
    if not ai_module_enum:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid AI module: {ai_module}",
        )

    user_decision_enum = decision_map.get(user_decision)
    if not user_decision_enum:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid user decision: {user_decision}",
        )

    status_enum = status_map.get(operation_status)
    if not status_enum:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Invalid operation status: {operation_status}",
        )

    entry = audit_log.log_decision(
        ai_module=ai_module_enum,
        ai_recommendation=ai_recommendation,
        user_decision=user_decision_enum,
        final_execution=final_execution,
        operation_status=status_enum,
        user_id=user_id,
        username=username,
        input_parameters=input_parameters,
        confidence=confidence,
        reasoning=reasoning,
        user_modifications=user_modifications,
        metadata=metadata,
    )

    return success(
        data={"timestamp_ms": entry.timestamp_ms},
        message="Audit log entry recorded successfully",
    )


@router.post("/audit-log/query")
@handle_sovereignty_errors(
    context="user_sovereignty.query_audit_logs",
    log_tag="Failed to query audit logs",
)
async def query_audit_logs(request: AuditLogQueryRequest):
    logs = audit_log.get_logs(
        start_time=request.start_time,
        end_time=request.end_time,
        ai_module=request.ai_module,
        user_decision=request.user_decision,
        limit=request.limit,
        offset=request.offset,
    )

    return success(
        data={
            "logs": [entry.to_dict() for entry in logs],
            "total": len(logs),
            "limit": request.limit,
            "offset": request.offset,
        },
        message="Audit logs retrieved successfully",
    )


@router.post("/audit-log/search")
@handle_sovereignty_errors(
    context="user_sovereignty.search_audit_logs",
    log_tag="Failed to search audit logs",
)
async def search_audit_logs(request: AuditLogSearchRequest):
    logs = audit_log.search_logs(
        keyword=request.keyword,
        limit=request.limit,
    )

    return success(
        data={
            "logs": [entry.to_dict() for entry in logs],
            "total": len(logs),
            "keyword": request.keyword,
        },
        message="Audit logs search completed",
    )


@router.post("/audit-log/export")
@handle_sovereignty_errors(
    context="user_sovereignty.export_audit_logs",
    log_tag="Failed to export audit logs",
    catch_permission_error=True,
)
async def export_audit_logs(request: AuditLogExportRequest):
    exported_data = audit_log.export_logs(
        format=request.format,
        start_time=request.start_time,
        end_time=request.end_time,
        ai_module=request.ai_module,
    )

    return success(
        data={
            "format": request.format,
            "content": exported_data,
            "size_bytes": len(exported_data.encode("utf-8")),
        },
        message="Audit logs exported successfully",
    )


@router.get("/audit-log/statistics")
@handle_sovereignty_errors(
    context="user_sovereignty.get_audit_statistics",
    log_tag="Failed to retrieve audit log statistics",
    type_error_code=ErrorCode.INTERNAL_ERROR,
)
async def get_audit_log_statistics():
    stats = audit_log.get_statistics()

    return success(
        data=stats,
        message="Audit log statistics retrieved successfully",
    )


@router.delete("/audit-log/clear", dependencies=[Depends(require_permission("user:write"))])
@handle_sovereignty_errors(
    context="user_sovereignty.clear_audit_logs",
    log_tag="Failed to clear audit logs",
    catch_permission_error=True,
)
async def clear_audit_logs(
    current_user: dict = Depends(get_current_user),
):
    # clear_logs() 因合规（FDA 21 CFR Part 11 / SOC 2）默认禁止，必须走
    # 带授权信息的 clear_logs_with_authorization（清空前归档 + 记录操作审计）。
    result = audit_log.clear_logs_with_authorization(
        authorizer_id=current_user.get("username", "_anonymous_"),
        authorizer_role=current_user.get("role", "T"),
        reason="API 清空审计日志",
    )

    return success(
        data={
            "cleared_entries": result.get("cleared_count", 0),
            "backup_path": result.get("backup_path"),
            "authorizer": result.get("authorizer"),
        },
        message="Audit log cleared successfully",
    )


@router.get("/settings")
@handle_sovereignty_errors(
    context="user_sovereignty.get_settings",
    log_tag="Failed to retrieve settings",
    type_error_code=ErrorCode.INTERNAL_ERROR,
)
async def get_user_sovereignty_settings():
    settings = UserSovereigntySettings()

    return success(
        data=settings.model_dump(),
        message="User sovereignty settings retrieved",
    )
