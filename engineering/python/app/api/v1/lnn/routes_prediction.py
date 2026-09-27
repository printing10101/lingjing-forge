"""LNN 推理端点（predict / predict_stream / predict_windowed / batch-inference）。

从 routes.py 拆分而来（P0-2.3 子路由拆分）。本模块仅承载推理相关端点，
模块级状态（_hybrid_engine 等）集中在 ``dependencies.py``。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone


from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission
from app.dependencies import get_ring_log_buffer
from app.middleware.rate_limiter import limiter
from app.models.schemas import (
    LNNPredictRequest,
    LNNModelInfo,
    LNNBatchInferenceRequest,
    LNNStreamPredictRequest,
    LNNWindowedPredictRequest,
    LNNStreamingConfig,
)
from app.tasks.task_manager import TaskType
from app.ai.lnn.inference.predictor import LNNPredictor, PredictionResult
from app.audit.audit_log import AIModule, UserDecision, OperationStatus

from app.api.v1.lnn import dependencies as _lnn_deps
from app.api.v1.lnn.dependencies import (
    model_registry,
    model_cache,
    audit_log,
    task_manager,
)
from app.api.v1.lnn.services import (
    _generate_prediction_reasoning,
    _generate_alternatives,
    run_batch_inference_v2,
)
from app.api.v1.lnn.routes_training import _log_task_exception

logger = logging.getLogger(__name__)

router = APIRouter()

# 模块级集合：保存 asyncio.create_task 返回的批量推理任务引用，
# 防止任务在执行完成前被 Python GC 回收（局部变量引用丢失会导致
# 任务被静默取消且不抛异常）。任务完成后通过 done_callback 自动移除。
_active_batch_tasks: set[asyncio.Task] = set()


def _validate_predict_request(body: LNNPredictRequest, model_info) -> dict | None:
    """校验 predict 请求参数；返回错误响应或 None。

    检查项：input_data 非空、元素为数值类型、维度与模型期望一致或为其整数倍。
    """
    if not body.input_data:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="输入数据必须为非空列表",
        )
    if any(not isinstance(x, (int, float)) for x in body.input_data):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message="输入数据必须为数值类型",
        )
    expected_dim = len(model_info.input_features) if model_info.input_features else None
    if expected_dim:
        input_len = len(body.input_data)
        if input_len != expected_dim and input_len % expected_dim != 0:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"输入维度不匹配: 期望{expected_dim}维或其倍数,实际{input_len}维",
            )
    return None


def _load_model_for_predict(model_name: str) -> LNNPredictor:
    """从缓存或注册表加载 LNNPredictor（缓存未命中时构建并写入）。"""
    predictor = model_cache.get(model_name)
    if predictor is None:
        predictor = LNNPredictor.from_registry(
            registry=model_registry,
            model_name=model_name,
            use_amp=True,
            auto_device=True,
        )
        model_cache.put(model_name, predictor)
    return predictor


async def _run_inference(predictor: LNNPredictor, body: LNNPredictRequest):
    """执行推理；返回 (PredictionResult, None) 或 (None, error_response)。

    H9 修复：predict() 是同步阻塞调用（可能耗时数百毫秒），使用
    asyncio.to_thread 将其转移到线程池执行，避免冻结事件循环。
    """
    try:
        result = await asyncio.to_thread(
            predictor.predict,
            input_data=body.input_data,
            return_confidence=body.return_confidence,
        )
    except (ValueError, KeyError, TypeError, AttributeError, RuntimeError, OSError) as model_err:
        safe = safe_error_message(
            model_err,
            context=f"lnn.predict_inference[{body.model_name}]",
        )
        logger.error(
            "Model inference error | model=%s | error_id=%s | exc=%s",
            body.model_name,
            safe.get("error_id"),
            model_err,
            exc_info=True,
        )
        get_ring_log_buffer().append(
            "ai_inference",
            level="ERROR",
            message=f"Model '{body.model_name}' inference failed",
            data={
                "error_id": safe.get("error_id"),
                "model": body.model_name,
            },
        )
        return None, error(
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
    return result, None


def _format_predict_response(
    result: PredictionResult,
    body: LNNPredictRequest,
    model_info,
):
    """构造 predict 响应数据。

    返回 (response_data, value, confidence, inference_time, alternatives, reasoning)，
    其中 response_data 已包含可选 confidence 字段。
    """
    value = result.value
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    confidence = result.confidence if body.return_confidence else None
    inference_time = result.inference_time

    reasoning = _generate_prediction_reasoning(
        model_name=body.model_name,
        input_data=body.input_data,
        prediction=value,
        confidence=confidence,
        inference_time=inference_time,
    )

    alternatives = _generate_alternatives(
        model_name=body.model_name,
        input_data=body.input_data,
        primary_value=value,
        primary_confidence=confidence if confidence else 0.0,
    )

    model_info_response = LNNModelInfo(
        name=model_info.name,
        version=model_info.version,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )

    response_data = {
        "value": value,
        "inference_time": inference_time,
        "model_info": model_info_response.model_dump(),
        "reasoning": reasoning,
        "alternatives": [alt.model_dump() for alt in alternatives],
    }
    if confidence is not None:
        response_data["confidence"] = confidence
    return response_data, value, confidence, inference_time, alternatives, reasoning


def _log_predict_success(
    body: LNNPredictRequest,
    value,
    confidence,
    alternatives,
    inference_time,
    reasoning,
) -> None:
    """记录推理成功的审计日志与环形日志。"""
    audit_log.log_decision(
        ai_module=AIModule.LNN_PREDICT,
        ai_recommendation={
            "model_name": body.model_name,
            "prediction": value,
            "confidence": confidence,
            "alternatives": [alt.model_dump() for alt in alternatives],
        },
        user_decision=UserDecision.AUTO_EXECUTED,
        final_execution={"prediction": value},
        operation_status=OperationStatus.SUCCESS,
        input_parameters={
            "model_name": body.model_name,
            "input_data": body.input_data,
            "return_confidence": body.return_confidence,
        },
        confidence=confidence,
        reasoning=reasoning,
    )
    get_ring_log_buffer().append(
        "ai_inference",
        level="INFO",
        message=f"Model '{body.model_name}' prediction completed",
        data={
            "model": body.model_name,
            "inference_time_ms": inference_time,
            "input_size": len(body.input_data) if isinstance(body.input_data, list) else 1,
        },
    )


@router.post("/predict", dependencies=[Depends(require_permission("lnn:write"))])
@limiter.limit("60/minute")
async def predict_lnn(request: Request, body: LNNPredictRequest):
    try:
        entry = model_registry.registry.get(body.model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{body.model_name}' not found",
            )
        model_info = entry.info

        err = _validate_predict_request(body, model_info)
        if err:
            return err

        predictor = _load_model_for_predict(body.model_name)

        result, err = await _run_inference(predictor, body)
        if err:
            return err

        response_data, value, confidence, inference_time, alternatives, reasoning = _format_predict_response(
            result, body, model_info
        )

        _log_predict_success(body, value, confidence, alternatives, inference_time, reasoning)

        return success(data=response_data, message="Prediction completed successfully")

    except KeyError:
        get_ring_log_buffer().append(
            "ai_inference",
            level="WARN",
            message=f"Model '{body.model_name}' not found",
            data={"model": body.model_name},
        )
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{body.model_name}' not found in registry",
        )
    except (ValueError, TypeError, OSError, RuntimeError, AttributeError) as e:
        get_ring_log_buffer().append(
            "ai_inference",
            level="ERROR",
            message=f"Model '{body.model_name}' unexpected error",
            data={"model": body.model_name, "error_id": None},
        )
        safe = safe_error_message(
            e,
            context=f"lnn.predict[{body.model_name}]",
        )
        get_ring_log_buffer().append(
            "ai_inference",
            level="ERROR",
            message=f"safe_error_id={safe.get('error_id')}",
            data={"model": body.model_name},
        )
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/batch-inference", dependencies=[Depends(require_permission("lnn:write"))])
# P2-4-4 修复：批量推理消耗大量计算资源，需速率限制防止 DoS。
# 限制为 10/hour（比单次 predict 的 60/minute 更严格，因批量任务资源消耗高）。
@limiter.limit("10/hour")
async def batch_inference(
    request: Request,
    body: LNNBatchInferenceRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """异步启动批量推理,立即返回 job_id。"""
    try:
        record = await task_manager.create_task(
            TaskType.LNN_BATCH_INFERENCE,
            {
                "model_name": body.model_name,
                "input_data": body.input_data,
                "batch_size": body.batch_size,
            },
            idempotency_key=idempotency_key,
        )

        async def batch_executor(cancel_evt, progress_updater):
            return await run_batch_inference_v2(
                record.job_id,
                body.model_name,
                body.input_data,
                body.batch_size,
                cancel_evt,
                progress_updater,
            )

        # 修复：保存任务引用防止 GC 提前回收，并添加异常处理
        batch_task = asyncio.create_task(task_manager.execute_task(record.job_id, batch_executor))
        _active_batch_tasks.add(batch_task)

        def _on_batch_done(t: asyncio.Task) -> None:
            # 先从集合移除引用，再记录异常，避免任务永久驻留集合导致内存泄漏
            _active_batch_tasks.discard(t)
            _log_task_exception(t, f"batch-{record.job_id}")

        batch_task.add_done_callback(_on_batch_done)

        return success(
            data={"job_id": record.job_id, "status": "queued"},
            message="Batch inference job queued",
        )

    except (ValueError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"lnn.batch_inference_init[{body.model_name}]")
        logger.warning("Batch inference init failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


# 流式长时序推理（借鉴 lingbot-map GCT 五项核心思想）
#
# 本节暴露 HybridInferenceEngine 的 infer_stream / infer_windowed 能力为
# HTTP 端点，使前端/外部服务可消费流式推理结果。引擎与 StreamingPredictor
# 的实现在 app.ai.lnn.engine / app.ai.lnn.inference.streaming 中，本节仅
# 做 HTTP 编排、输入校验、审计日志与错误安全。
#
# 设计要点：
# 1. HybridInferenceEngine 作为模块级惰性单例，避免每次请求重建路由表；
# 使用双重检查锁保证线程安全。
# 2. StreamingPredictor 每次请求新建（内部隐状态缓存、锚点、轨迹记忆
# 随请求隔离，避免跨请求状态污染）。
# 3. 流式端点返回 NDJSON（application/x-ndjson），每行一帧结果，便于
# 客户端增量消费；窗口化端点返回一次性 JSON 数组。
# 4. 速率限制：流式 20/minute（单帧轻量），窗口化 10/hour（批量重负载）。


def _get_hybrid_engine():
    """惰性获取 HybridInferenceEngine 模块级单例。

    使用双重检查锁（double-checked locking）保证线程安全。引擎以
    ``enable_fusion=False`` 初始化，因为流式推理路径为单模型，无需
    Dempster-Shafer 融合开销；融合能力保留给多模型 infer() 路径。

    单例状态保存在 ``dependencies._hybrid_engine``，供多个子路由模块共享。

    Returns:
        HybridInferenceEngine 实例。

    Raises:
        ImportError: 当 app.ai.lnn.engine 不可导入时（torch 依赖缺失等）。
    """
    if _lnn_deps._hybrid_engine is not None:
        return _lnn_deps._hybrid_engine
    with _lnn_deps._hybrid_engine_lock:
        if _lnn_deps._hybrid_engine is None:
            # 惰性导入：避免在模块加载阶段触发 torch 导入链
            from app.ai.lnn.engine import HybridInferenceEngine

            _lnn_deps._hybrid_engine = HybridInferenceEngine(enable_fusion=False)
            logger.info("HybridInferenceEngine 流式推理单例已初始化")
    return _lnn_deps._hybrid_engine


def _build_streaming_predictor(
    model_name: str,
    config: LNNStreamingConfig | None,
):
    """从 model_cache 获取 LNNPredictor 并构建 StreamingPredictor。

    复用 /predict 端点的 model_cache 模式，使单次推理与流式推理共享同一份
    模型权重与预处理器。StreamingPredictor 每次新建，保证隐状态隔离。

    Args:
        model_name: 已在 model_registry 注册的模型名称。
        config: 来自请求体的流式配置，None 时使用 StreamingConfig 默认值。

    Returns:
        StreamingPredictor 实例。

    Raises:
        ValueError: 当模型未注册或配置无效时。
        ImportError: 当 streaming 模块依赖缺失时。
    """
    from app.ai.lnn.inference.streaming import StreamingConfig, StreamingPredictor

    predictor = model_cache.get(model_name)
    if predictor is None:
        predictor = LNNPredictor.from_registry(
            registry=model_registry,
            model_name=model_name,
            use_amp=True,
            auto_device=True,
        )
        model_cache.put(model_name, predictor)

    streaming_config_kwargs = config.model_dump() if config is not None else {}
    streaming_config = StreamingConfig(**streaming_config_kwargs)
    return StreamingPredictor(predictor=predictor, config=streaming_config)


def _inference_result_to_dict(result) -> dict:
    """将 InferenceResult 序列化为 JSON 兼容的 dict。

    处理 numpy 标量/数组、EngineType 枚举、以及 prediction 单元素列表
    的展平（与 /predict 端点的响应格式保持一致）。

    Args:
        result: app.ai.lnn.core.InferenceResult 实例。

    Returns:
        JSON 可序列化的 dict。
    """
    prediction = result.prediction
    if prediction is not None and hasattr(prediction, "tolist"):
        prediction = prediction.tolist()
    if isinstance(prediction, list) and len(prediction) == 1:
        prediction = prediction[0]

    engine_used = result.engine_used
    if hasattr(engine_used, "value"):
        engine_used = engine_used.value

    return {
        "prediction": prediction,
        "confidence": result.confidence,
        "engine_used": engine_used,
        "model_used": result.model_used,
        "processing_time_ms": result.processing_time_ms,
        "metadata": result.metadata or {},
        "evidence": result.evidence or [],
        "uncertainty": result.uncertainty or {},
    }


def _validate_streaming_frames(frames: list) -> str | None:
    """校验流式推理的帧序列数据，返回错误消息或 None。

    Args:
        frames: 请求体中的 frames 字段（list[list[float]]）。

    Returns:
        校验失败时返回错误消息字符串，成功时返回 None。
    """
    if not frames:
        return "frames 必须为非空列表"
    for i, frame in enumerate(frames):
        if not isinstance(frame, list) or not frame:
            return f"第 {i} 帧数据无效：必须为非空列表"
        if any(not isinstance(x, (int, float)) for x in frame):
            return f"第 {i} 帧数据无效：必须为数值类型"
    return None


@router.post("/predict_stream", dependencies=[Depends(require_permission("lnn:write"))])
@limiter.limit("20/minute")
async def predict_stream(request: Request, body: LNNStreamPredictRequest):
    """流式长时序推理（NDJSON 流式响应）。

    借鉴 lingbot-map GCT 思想：关键帧缓存 + 锚点漂移修正 + 轨迹记忆约束，
    适用于传感器实时采样流、长时序加工监控等场景。

    响应体为 ``application/x-ndjson``，每行一个 JSON 对象，对应一帧推理结果。
    每帧结果包含 prediction / confidence / metadata（含 is_keyframe、
    anchor_drift、trajectory_deviation 等流式元信息）。
    """
    try:
        # 输入校验
        err_msg = _validate_streaming_frames(body.frames)
        if err_msg:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=err_msg,
            )

        # 模型存在性检查
        entry = model_registry.registry.get(body.model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{body.model_name}' not found",
            )

        # 构建流式预测器（每次新建，保证隐状态隔离）
        try:
            streaming_predictor = _build_streaming_predictor(body.model_name, body.config)
        except (ValueError, KeyError, TypeError, RuntimeError, OSError, ImportError) as exc:
            safe = safe_error_message(
                exc,
                context=f"lnn.predict_stream.build[{body.model_name}]",
            )
            logger.error("构建流式预测器失败: %s", exc, exc_info=True)
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
                detail=safe.get("detail"),
            )

        # 注册到混合引擎并执行流式推理
        engine = _get_hybrid_engine()
        engine.register_streaming_predictor(body.model_name, streaming_predictor)

        # 审计日志（SOC 2 / ISO 27001 合规）
        audit_log.log_decision(
            ai_module=AIModule.LNN_PREDICT,
            ai_recommendation={
                "model_name": body.model_name,
                "streaming": True,
                "frame_count": len(body.frames),
            },
            user_decision=UserDecision.AUTO_EXECUTED,
            final_execution={"frame_count": len(body.frames)},
            operation_status=OperationStatus.SUCCESS,
            input_parameters={
                "model_name": body.model_name,
                "frame_count": len(body.frames),
                "config": body.config.model_dump() if body.config else None,
            },
            confidence=None,
            reasoning="streaming_inference",
        )

        get_ring_log_buffer().append(
            "ai_inference",
            level="INFO",
            message=f"Streaming inference started for model '{body.model_name}'",
            data={
                "model": body.model_name,
                "frame_count": len(body.frames),
                "streaming": True,
            },
        )

        # NDJSON 流式响应：每帧一行 JSON
        async def _ndjson_stream():
            for result in engine.infer_stream(body.model_name, iter(body.frames)):
                line = (
                    json.dumps(
                        _inference_result_to_dict(result),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                yield line

        return StreamingResponse(
            _ndjson_stream(),
            media_type="application/x-ndjson",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except (ValueError, TypeError, OSError, RuntimeError, ImportError) as e:
        safe = safe_error_message(e, context=f"lnn.predict_stream[{body.model_name}]")
        logger.error("流式推理失败: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/predict_windowed", dependencies=[Depends(require_permission("lnn:write"))])
@limiter.limit("10/hour")
async def predict_windowed(request: Request, body: LNNWindowedPredictRequest):
    """窗口化超长序列推理（一次性 JSON 响应）。

    对应 lingbot-map 的 windowed mode：将序列切分为多个窗口，窗口间通过
    ``overlap_keyframes`` 传递隐状态，避免每次窗口都从零初始化。适用于
    万帧以上跨工序连续切削监控、长时序颤振检测等场景。

    响应体为标准 ``success()`` 包装，``data.results`` 为完整序列的推理结果列表。
    """
    try:
        # 输入校验
        err_msg = _validate_streaming_frames(body.frames)
        if err_msg:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=err_msg,
            )

        # 模型存在性检查
        entry = model_registry.registry.get(body.model_name)
        if not entry:
            return error(
                code=ErrorCode.NOT_FOUND,
                message=f"Model '{body.model_name}' not found",
            )

        # 构建流式预测器
        try:
            streaming_predictor = _build_streaming_predictor(body.model_name, body.config)
        except (ValueError, KeyError, TypeError, RuntimeError, OSError, ImportError) as exc:
            safe = safe_error_message(
                exc,
                context=f"lnn.predict_windowed.build[{body.model_name}]",
            )
            logger.error("构建流式预测器失败: %s", exc, exc_info=True)
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
                detail=safe.get("detail"),
            )

        # 注册到混合引擎并执行窗口化推理
        engine = _get_hybrid_engine()
        engine.register_streaming_predictor(body.model_name, streaming_predictor)

        try:
            results = engine.infer_windowed(
                model_name=body.model_name,
                data_list=body.frames,
                window_size=body.window_size,
                overlap_keyframes=body.overlap_keyframes,
            )
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            safe = safe_error_message(
                exc,
                context=f"lnn.predict_windowed.infer[{body.model_name}]",
            )
            logger.error("窗口化推理失败: %s", exc, exc_info=True)
            return error(
                code=ErrorCode.INTERNAL_ERROR,
                message=safe["message"],
                detail=safe.get("detail"),
            )

        results_data = [_inference_result_to_dict(r) for r in results]

        # 审计日志
        audit_log.log_decision(
            ai_module=AIModule.LNN_PREDICT,
            ai_recommendation={
                "model_name": body.model_name,
                "streaming": True,
                "windowed": True,
                "frame_count": len(body.frames),
                "window_size": body.window_size,
                "overlap_keyframes": body.overlap_keyframes,
            },
            user_decision=UserDecision.AUTO_EXECUTED,
            final_execution={"result_count": len(results_data)},
            operation_status=OperationStatus.SUCCESS,
            input_parameters={
                "model_name": body.model_name,
                "frame_count": len(body.frames),
                "window_size": body.window_size,
                "overlap_keyframes": body.overlap_keyframes,
            },
            confidence=None,
            reasoning="windowed_inference",
        )

        get_ring_log_buffer().append(
            "ai_inference",
            level="INFO",
            message=f"Windowed inference completed for model '{body.model_name}'",
            data={
                "model": body.model_name,
                "frame_count": len(body.frames),
                "result_count": len(results_data),
                "windowed": True,
            },
        )

        return success(
            data={
                "results": results_data,
                "total_frames": len(results_data),
                "model_name": body.model_name,
            },
            message="Windowed streaming inference completed",
        )

    except (ValueError, TypeError, OSError, RuntimeError, ImportError) as e:
        safe = safe_error_message(e, context=f"lnn.predict_windowed[{body.model_name}]")
        logger.error("窗口化推理失败: %s", e, exc_info=True)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )
