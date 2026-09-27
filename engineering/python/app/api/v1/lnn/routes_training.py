"""LNN 训练端点（train / dry_run / stream / cancel）。

从 routes.py 拆分而来（P0-2.3 子路由拆分）。本模块承载训练相关端点，
模块级状态（_TRAINING_QUEUES 等）集中在 ``dependencies.py``。

本模块还导出 ``_log_task_exception`` 辅助函数，供 routes_prediction /
routes_quantization 子路由模块复用（用于给 asyncio.create_task 添加异常回调）。
"""

import asyncio
import logging
import uuid
from typing import Any
from collections.abc import Callable

import numpy as np

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.response import ErrorCode, error, success
from app.core.safe_errors import safe_error_message
from app.auth.permissions import require_permission
from app.api.v1.auth import get_current_user
from app.utils.utils import validate_user_path
from app.utils.time import utcnow
from app.middleware.rate_limiter import limiter
from app.models.schemas import (
    LNNTrainRequest,
    LNNTrainDryRunRequest,
    LNNTrainDryRunResponse,
    TrainingPlanSummary,
)
from app.tasks.task_manager import TaskType, TaskStatus
from app.api.v1.sse import sse_manager

# P0#3 解耦: 通过 research_bridge 延迟导入。
_HAS_DEVICE_MANAGER = False
detect_device: Callable[..., Any] | None = None


def _lazy_init_device_manager() -> bool:
    global _HAS_DEVICE_MANAGER, detect_device
    if _HAS_DEVICE_MANAGER:
        return True
    try:
        from app.ai.lnn._research_bridge import get_device_detect

        detect_device = get_device_detect()
        _HAS_DEVICE_MANAGER = detect_device is not None
    except Exception:
        _HAS_DEVICE_MANAGER = False
    return _HAS_DEVICE_MANAGER


from app.api.v1.lnn.dependencies import (
    model_registry,
    training_tasks,
    task_manager,
    _ALLOWED_DATA_BASE_DIRS,
    _TRAINING_QUEUES,
    _TRAINING_QUEUES_LOCK,
    _TRAINING_QUEUE_TTL,
)
from app.api.v1.lnn.services import (
    _run_training_task_async,
    _broadcast_training_events,
    sse_event_generator,
)

logger = logging.getLogger(__name__)

# 模块级集合：保存 asyncio.create_task 返回的训练/广播任务引用，
# 防止任务在执行完成前被 Python GC 回收（局部变量引用丢失会导致
# 任务被静默取消且不抛异常）。任务完成后通过 done_callback 自动移除。
_active_training_tasks: set[asyncio.Task] = set()
_active_broadcast_tasks: set[asyncio.Task] = set()

# 训练 dry-run 参数
_DRY_RUN_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 数据文件大小上限：100 MB
_DRY_RUN_TRAIN_RATIO = 0.8  # 训练集占比（剩余作为验证集）
_DRY_RUN_MEM_MULTIPLIER = 3  # 内存估算系数（数据体积倍数）
_DRY_RUN_GPU_MEM_MULTIPLIER = 2  # GPU 显存估算系数（在 CPU 估算基础上倍乘）
_DRY_RUN_DURATION_COEFF = 0.001  # 训练时长估算系数（分钟/样本）
_DRY_RUN_LOW_SAMPLE_THRESHOLD = 100  # 小样本风险阈值
_DRY_RUN_HIGH_EPOCHS_THRESHOLD = 500  # 高训练轮数风险阈值
_DRY_RUN_HIGH_LR_THRESHOLD = 0.01  # 高学习率风险阈值
_DRY_RUN_HIGH_RISK_COUNT = 2  # 触发低置信度的风险数量阈值
_DRY_RUN_CONFIDENCE_HIGH = 0.85  # 无风险时的置信度
_DRY_RUN_CONFIDENCE_MEDIUM = 0.75  # 1-2 个风险时的置信度
_DRY_RUN_CONFIDENCE_LOW = 0.6  # >2 个风险时的置信度


def _validate_data_path(user_path: str):
    """校验 /train/dry_run 接收的数据文件路径，防止路径遍历攻击。

    修复 [B10]：原端点直接将 ``request.data_path`` 透传给 ``np.loadtxt()``，
    攻击者可构造 ``/etc/passwd``、``../../../app.db`` 等任意路径读取服务器文件。
    本函数委托给统一的 ``app.utils.utils.validate_user_path``，在 ``resolve()``
    后强制校验绝对路径必须位于 ``_ALLOWED_DATA_BASE_DIRS`` 之一之下，并要求
    扩展名为常见数据文件类型，从而将可读取范围严格限制在数据目录内。

    Args:
        user_path: 用户提交的数据文件路径（相对或绝对）

    Returns:
        校验通过后的 Path 对象

    Raises:
        HTTPException: 400 当路径为空、扩展名不允许或解析后超出允许目录范围
    """
    # 允许的数据文件扩展名白名单（避免读取 .py、.db 等敏感文件）
    _allowed_exts = {".csv", ".txt", ".tsv", ".dat", ".json", ".jsonl", ".npy"}
    try:
        return validate_user_path(
            user_path=user_path,
            allowed_base_dirs=_ALLOWED_DATA_BASE_DIRS,
            allowed_extensions=_allowed_exts,
            project_root=_ALLOWED_DATA_BASE_DIRS[1],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=safe_error_message(exc, fallback="请求参数无效", context="lnn.routes")
        ) from exc


def _cleanup_training_queues() -> None:
    """清理超过 TTL 的训练队列，防止内存泄漏。

    在添加新队列时顺便调用，惰性清理策略避免引入后台定时任务。
    """
    now = utcnow()
    expired = [
        job_id for job_id, info in _TRAINING_QUEUES.items() if now - info.get("created_at", now) > _TRAINING_QUEUE_TTL
    ]
    for job_id in expired:
        _TRAINING_QUEUES.pop(job_id, None)
        logger.info("清理过期训练队列: %s", job_id)


def _log_task_exception(task: asyncio.Task, context: str) -> None:
    """记录后台任务未捕获异常，避免静默失败。

    供本模块的 train_lnn / routes_prediction.batch_inference /
    routes_quantization.quantize_model 复用，统一为 asyncio.create_task
    产生的后台任务添加 done_callback。
    """
    if task.cancelled():
        logger.debug("Task %s cancelled", context)
    elif task.exception():
        logger.error(
            "Task %s failed: %s",
            context,
            task.exception(),
        )


async def _validate_dry_run_request(request: LNNTrainDryRunRequest):
    """校验 dry-run 请求路径并加载数据；返回 (data, None) 或 (None, error_response)。

    修复 [B10]：data_path 通过 _validate_data_path 做路径遍历校验，限制在允许的数据目录内。
    修复 P2：同步文件 IO + np.loadtxt 通过 asyncio.to_thread 包装，避免阻塞事件循环。
    """
    validated_data_path = _validate_data_path(request.data_path)

    def _load_data_sync():
        """同步读取文件大小并加载 CSV（在线程池中执行避免阻塞事件循环）。"""
        with open(str(validated_data_path), "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size > _DRY_RUN_MAX_FILE_SIZE_BYTES:
                raise ValueError(
                    f"File too large ({file_size / 1024 / 1024:.1f} MB), "
                    f"max {_DRY_RUN_MAX_FILE_SIZE_BYTES / 1024 / 1024:.0f} MB"
                )
            f.seek(0)
            return np.loadtxt(f, delimiter=",")

    try:
        data = await asyncio.to_thread(_load_data_sync)
    except FileNotFoundError:
        return None, error(
            code=ErrorCode.NOT_FOUND,
            message=f"Data file not found: {request.data_path}",
        )
    except IsADirectoryError:
        return None, error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Not a regular file: {request.data_path}",
        )
    except PermissionError:
        return None, error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Permission denied: {request.data_path}",
        )
    except ValueError as e:
        if "File too large" in str(e):
            # 包装异常消息，避免直接回显内部错误细节
            safe = safe_error_message(e, context="lnn.upload_data[file_too_large]", fallback="数据文件过大")
            return None, error(
                code=ErrorCode.INVALID_REQUEST,
                message=safe["message"],
                detail={"error_id": safe["error_id"]},
            )
        raise

    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.size == 0:
        return None, error(
            code=ErrorCode.INVALID_REQUEST,
            message="Data file is empty",
        )
    if data.shape[0] < 2:
        return None, error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Need at least 2 samples for train/val split, got {data.shape[0]}",
        )
    if not np.isfinite(data).all():
        return None, error(
            code=ErrorCode.INVALID_REQUEST,
            message="Data contains NaN or Inf values",
        )
    return data, None


def _build_training_config(request: LNNTrainDryRunRequest, data):
    """根据请求与数据构造训练计划配置；返回 (config_dict, None) 或 (None, error_response)。"""
    entry = model_registry.registry.get(request.model_name)
    if not entry:
        return None, error(
            code=ErrorCode.NOT_FOUND,
            message=f"Model '{request.model_name}' not found",
        )

    dataset_samples = data.shape[0]
    train_size = int(_DRY_RUN_TRAIN_RATIO * dataset_samples)
    val_size = dataset_samples - train_size

    if detect_device is None and not _lazy_init_device_manager():
        return None, error(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="设备检测服务不可用（research 训练栈未安装）",
        )
    assert detect_device is not None
    device, device_info = detect_device(request.device)

    estimated_memory_mb = (data.nbytes / (1024 * 1024)) * _DRY_RUN_MEM_MULTIPLIER
    estimated_gpu_memory_mb = None
    if device.type == "cuda":
        estimated_gpu_memory_mb = estimated_memory_mb * _DRY_RUN_GPU_MEM_MULTIPLIER

    epochs = request.hyperparameters.epochs
    batch_size = request.hyperparameters.batch_size
    samples_per_epoch = dataset_samples
    estimated_duration_minutes = (epochs * samples_per_epoch / batch_size) * _DRY_RUN_DURATION_COEFF

    potential_risks = []
    recommendations = []

    if dataset_samples < _DRY_RUN_LOW_SAMPLE_THRESHOLD:
        potential_risks.append("数据集样本较少(<100),可能导致模型过拟合")
        recommendations.append("建议增加训练数据量以提升模型泛化能力")

    if epochs > _DRY_RUN_HIGH_EPOCHS_THRESHOLD:
        potential_risks.append("训练轮数较多(>500),训练时间可能较长")
        recommendations.append("可考虑使用早停策略(early stopping)避免过拟合")

    if request.hyperparameters.learning_rate > _DRY_RUN_HIGH_LR_THRESHOLD:
        potential_risks.append("学习率较高(>0.01),可能导致训练不稳定")
        recommendations.append("建议从较低学习率(0.001-0.005)开始训练")

    if device.type == "cpu":
        recommendations.append("当前使用CPU训练,如需加速可考虑使用GPU")

    return {
        "dataset_samples": dataset_samples,
        "train_size": train_size,
        "val_size": val_size,
        "estimated_memory_mb": estimated_memory_mb,
        "estimated_gpu_memory_mb": estimated_gpu_memory_mb,
        "estimated_duration_minutes": estimated_duration_minutes,
        "potential_risks": potential_risks,
        "recommendations": recommendations,
    }, None


def _execute_dry_run(config: dict) -> LNNTrainDryRunResponse:
    """根据配置构造 dry-run 响应对象（TrainingPlanSummary + 置信度 + reasoning）。"""
    risk_count = len(config["potential_risks"])
    if risk_count > _DRY_RUN_HIGH_RISK_COUNT:
        confidence = _DRY_RUN_CONFIDENCE_LOW
    elif risk_count > 0:
        confidence = _DRY_RUN_CONFIDENCE_MEDIUM
    else:
        confidence = _DRY_RUN_CONFIDENCE_HIGH

    training_plan = TrainingPlanSummary(
        estimated_duration_minutes=round(config["estimated_duration_minutes"], 2),
        estimated_memory_mb=round(config["estimated_memory_mb"], 2),
        estimated_gpu_memory_mb=round(config["estimated_gpu_memory_mb"], 2)
        if config["estimated_gpu_memory_mb"]
        else None,
        dataset_samples=config["dataset_samples"],
        train_val_split={
            "train": config["train_size"],
            "validation": config["val_size"],
        },
        potential_risks=config["potential_risks"],
        recommendations=config["recommendations"],
    )

    reasoning = (
        f"基于数据集规模({config['dataset_samples']} 样本)和超参数配置,"
        f"预计训练时间约为 {config['estimated_duration_minutes']:.2f} 分钟。"
        f"{'检测到以下风险:' + '、'.join(config['potential_risks']) if config['potential_risks'] else '未发现重大风险。'}"
        f"建议使用推荐配置开始训练。"
    )

    return LNNTrainDryRunResponse(
        is_dry_run=True,
        training_plan=training_plan,
        confidence=confidence,
        reasoning=reasoning,
    )


def _format_dry_run_response(dry_run_response: LNNTrainDryRunResponse):
    """格式化 dry-run 成功响应。"""
    return success(
        data=dry_run_response.model_dump(),
        message="Dry run completed: training plan generated for review",
    )


router = APIRouter()


@router.post("/train/dry_run", dependencies=[Depends(require_permission("lnn:train"))])
async def dry_run_training(request: LNNTrainDryRunRequest):
    try:
        data, err = await _validate_dry_run_request(request)
        if err:
            return err

        config, err = _build_training_config(request, data)
        if err:
            return err

        dry_run_response = _execute_dry_run(config)
        return _format_dry_run_response(dry_run_response)

    except (ValueError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context="lnn.dry_run_training")
        logger.warning("Dry run training failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.post("/train", dependencies=[Depends(require_permission("lnn:train"))])
@limiter.limit("5/hour")
async def train_lnn(
    request: Request,
    body: LNNTrainRequest,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    """异步启动 LNN 训练,立即返回 job_id。"""
    try:
        existing = await task_manager.create_task(
            TaskType.LNN_TRAINING,
            {
                "model_name": body.model_name,
                "data_path": body.data_path,
                "hyperparameters": body.hyperparameters.model_dump(),
                "device": body.device,
            },
            idempotency_key=idempotency_key,
        )

        if idempotency_key and existing.status not in (
            TaskStatus.PENDING,
            TaskStatus.QUEUED,
        ):
            return success(
                data={
                    "job_id": existing.job_id,
                    "status": existing.status.value,
                    "cached": True,
                },
                message="Cached job retrieved",
            )

        task_id = existing.job_id

        progress_q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        cancel_evt = asyncio.Event()
        # H16 修复：_TRAINING_QUEUES 与 task_manager._training_queues 必须在同一把锁内
        # 同步更新，避免 services.py 在两份字典不一致时读到脏数据。
        # 锁引用也存到 task_manager 上，供 services.py 跨文件获取。
        if not hasattr(task_manager, "_training_queues"):
            task_manager._training_queues = {}
        if not hasattr(task_manager, "_training_queues_lock"):
            task_manager._training_queues_lock = _TRAINING_QUEUES_LOCK
        with _TRAINING_QUEUES_LOCK:
            _cleanup_training_queues()  # 惰性清理过期队列
            queue_entry = {
                "cancel": cancel_evt,
                "progress": progress_q,
                "created_at": utcnow(),
            }
            _TRAINING_QUEUES[task_id] = queue_entry
            # 同步到 task_manager 的内部队列引用（在同一锁内，保证一致性）
            task_manager._training_queues[task_id] = queue_entry

        def cancel_training_hook():
            cancel_evt.set()

        task_manager.register_cancel_hook(task_id, cancel_training_hook)

        # 修复：保存任务引用防止 GC 提前回收，并添加异常处理
        # 原实现只通过 add_done_callback 记录异常，但未保留任务引用，
        # 局部变量 training_task / broadcast_task 在函数返回后被 GC，
        # asyncio 事件循环仅持有弱引用 任务被静默取消且不抛异常，
        # 训练流程会被悄悄杀死。现使用模块级 set 持有强引用，与
        # routes_prediction.py 中的 _active_batch_tasks 修复方式保持一致。
        training_task = asyncio.create_task(
            _run_training_task_async(
                task_id,
                body.model_name,
                body.data_path,
                body.hyperparameters.model_dump(),
                body.device,
            )
        )
        _active_training_tasks.add(training_task)

        def _on_training_done(t: asyncio.Task) -> None:
            _active_training_tasks.discard(t)
            _log_task_exception(t, f"training-{task_id}")

        training_task.add_done_callback(_on_training_done)

        broadcast_task = asyncio.create_task(_broadcast_training_events(task_id))
        _active_broadcast_tasks.add(broadcast_task)

        def _on_broadcast_done(t: asyncio.Task) -> None:
            _active_broadcast_tasks.discard(t)
            _log_task_exception(t, f"broadcast-{task_id}")

        broadcast_task.add_done_callback(_on_broadcast_done)

        return success(
            data={"job_id": task_id, "status": "queued"},
            message="Training job queued",
        )

    except (ValueError, TypeError, OSError, RuntimeError) as e:
        safe = safe_error_message(e, context=f"lnn.train_init[{body.model_name}]")
        logger.warning("Training init failed: %s", e)
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=safe["message"],
            detail=safe.get("detail"),
        )


@router.get("/train/{task_id}/stream", dependencies=[Depends(get_current_user)])
async def stream_training_status(task_id: str):
    """SSE 端点,用于实时训练状态更新。"""
    record = await task_manager.get_task(task_id)
    if not record:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Training task '{task_id}' not found",
        )

    client_id = f"client_{uuid.uuid4().hex[:8]}"
    logger.info("Client %s connecting to SSE stream for task %s", client_id, task_id)

    return StreamingResponse(
        sse_event_generator(task_id, client_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/train/{task_id}/cancel", dependencies=[Depends(require_permission("lnn:train"))])
async def cancel_training_task(task_id: str):
    """取消正在运行的训练任务。"""
    record = await task_manager.get_task(task_id)
    if not record:
        return error(
            code=ErrorCode.NOT_FOUND,
            message=f"Training task '{task_id}' not found",
        )

    if record.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"Training task '{task_id}' is already {record.status.value}",
        )

    await sse_manager.signal_cancel(task_id)

    if task_id in training_tasks:
        training_tasks[task_id]["status"] = "cancelling"
        training_tasks[task_id]["message"] = "Training cancellation requested"

    result = await task_manager.cancel_task(task_id)

    return success(
        data={"task_id": task_id, "status": "cancelled" if result else "cancelling"},
        message="Training cancellation processed",
    )
