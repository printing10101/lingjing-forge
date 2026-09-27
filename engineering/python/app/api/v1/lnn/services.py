"""LNN API 业务服务层。"""

import os
import time
import asyncio
import uuid
import logging
from collections.abc import Callable
from datetime import timezone


# 阶段2 解耦改造：torch 训练栈已迁移到 research/。工程侧仅消费 ONNX 模型，
# 不再依赖 torch。此处保留 try/except 兼容旧路径，torch 缺失时降级为 None，
# 训练相关 API 将返回 503 服务不可用。
try:
    import torch
    from torch.utils.data import DataLoader, TensorDataset

    _HAS_TORCH = True
except ImportError:
    torch = None
    DataLoader = None
    TensorDataset = None
    _HAS_TORCH = False

from app.core.safe_errors import safe_error_message
from app.ai.lnn.inference.predictor import LNNPredictor
from app.ai.lnn.inference.registry import (
    get_quantized_model_name,
)

# P0#3 解耦: 通过 research_bridge 延迟导入，替代直接 import research/。
# 桥接模块在 torch 缺失时返回 None，训练 API 将降级返回 503。
_HAS_TRAINING_STACK = False
LNNConfig = None
LNNTrainer = None
mlflow_start_run = None
mlflow_log_params = None
mlflow_log_metrics = None
mlflow_log_model = None
detect_device = None
get_optimal_batch_size = None
get_optimal_num_workers = None


def _lazy_init_training_stack() -> bool:
    """延迟初始化训练栈（首次调用时执行，避免模块加载期 ImportError）。"""
    global _HAS_TRAINING_STACK, LNNConfig, LNNTrainer
    global mlflow_start_run, mlflow_log_params, mlflow_log_metrics, mlflow_log_model
    global detect_device, get_optimal_batch_size, get_optimal_num_workers
    if _HAS_TRAINING_STACK:
        return True
    try:
        from app.ai.lnn._research_bridge import (
            get_lnn_config_factory,
            get_trainer_factory,
            get_mlflow_start_run,
            get_mlflow_log_params,
            get_mlflow_log_metrics,
            get_mlflow_log_model,
            get_device_detect,
            get_device_optimal_batch_size,
            get_device_optimal_num_workers,
        )

        LNNConfig = get_lnn_config_factory()
        LNNTrainer = get_trainer_factory()
        mlflow_start_run = get_mlflow_start_run()
        mlflow_log_params = get_mlflow_log_params()
        mlflow_log_metrics = get_mlflow_log_metrics()
        mlflow_log_model = get_mlflow_log_model()
        detect_device = get_device_detect()
        get_optimal_batch_size = get_device_optimal_batch_size()
        get_optimal_num_workers = get_device_optimal_num_workers()
        _HAS_TRAINING_STACK = all(x is not None for x in (LNNConfig, LNNTrainer, detect_device))
    except Exception:
        _HAS_TRAINING_STACK = False
    return _HAS_TRAINING_STACK


from app.tasks.task_manager import TaskStatus
from app.models.schemas import (
    AlternativePlan,
)
from app.api.v1.sse import sse_manager
from app.config.limits import SSE_HEARTBEAT_TIMEOUT_SEC
from app.utils.utils import format_bytes

logger = logging.getLogger(__name__)

# SSE 事件流的统一心跳超时（秒）。由 ``app.config.limits`` 集中管理，
# 与 app.api.v1.jobs / app.api.v1.workflows 共享同一基准值，
# 避免不同 SSE 通道行为不一致。

# 文件大小格式化函数由 ``app.utils.utils.format_bytes`` 统一提供，
# 本模块原 ``_format_size`` 实现已删除（与 ``format_bytes`` 字节级一致）。


def _generate_prediction_reasoning(
    model_name: str,
    input_data: list[float],
    prediction: float | list[float],
    confidence: float | None,
    inference_time: float,
) -> str:
    """生成预测推理解释。"""
    reasoning_parts = [
        f"模型 {model_name} 基于输入的 {len(input_data)} 个特征进行推理。",
    ]

    if isinstance(prediction, (int, float)):
        reasoning_parts.append(f"预测值为 {prediction}。")
    else:
        reasoning_parts.append(f"输出 {len(prediction)} 个预测值。")

    if confidence is not None:
        if confidence >= 0.8:
            reasoning_parts.append(f"置信度较高 ({confidence:.2f}),表明模型对当前输入数据的预测结果有较高的把握。")
        elif confidence >= 0.5:
            reasoning_parts.append(f"置信度中等 ({confidence:.2f}),建议结合实际情况综合判断预测结果。")
        else:
            reasoning_parts.append(f"置信度较低 ({confidence:.2f}),建议参考备选方案或调整输入数据后重新预测。")

    reasoning_parts.append(f"推理耗时 {inference_time:.2f}ms。")

    return " ".join(reasoning_parts)


def _generate_alternatives(
    model_name: str,
    input_data: list[float],
    primary_value: float | list[float],
    primary_confidence: float,
) -> list[AlternativePlan]:
    """生成备选方案（启发式，非独立模型预测）。

    学术诚信说明 [S4]：
    ----------------------------
    本函数生成的"保守方案"与"激进方案"是**对主预测值的简单 ±5%
    偏移启发式**，并非通过独立模型推理得到的预测结果。它们的目的是
    为决策者提供围绕主预测的敏感性区间参考，不应在论文中报告为
    "多模型预测对比"或"集成学习结果"。

    如需真实的多模型对比，应当：
    1. 使用不同架构（如 CFC vs LTC）独立训练并预测；或
    2. 使用不同超参数/数据划分训练同一架构的多个实例；或
    3. 使用 Monte Carlo Dropout 等不确定性量化方法。

    论文报告时，本函数的输出应明确标注为"启发式敏感性分析"或
    "工程安全边际参考"，而非"备选预测模型输出"。
    """
    alternatives = []

    if isinstance(primary_value, (int, float)):
        alt_1_value = primary_value * 0.95
        alt_2_value = primary_value * 1.05

        alternatives.append(
            AlternativePlan(
                plan_id=f"alt_{uuid.uuid4().hex[:8]}",
                parameters={
                    "optimization_target": "conservative",
                    "safety_margin": "+5%",
                },
                expected_outcome=f"保守方案:预测值 {alt_1_value:.4f},偏向安全边际,适合对稳定性要求高的场景。",
                confidence=max(0.0, primary_confidence - 0.05),
                reasoning="保守方案通过降低预测值约5%提供额外的安全缓冲,适用于风险敏感型决策。",
            )
        )

        alternatives.append(
            AlternativePlan(
                plan_id=f"alt_{uuid.uuid4().hex[:8]}",
                parameters={
                    "optimization_target": "aggressive",
                    "efficiency_gain": "+5%",
                },
                expected_outcome=f"激进方案:预测值 {alt_2_value:.4f},偏向性能优化,适合追求效率的场景。",
                confidence=max(0.0, primary_confidence - 0.08),
                reasoning="激进方案通过提高预测值约5%追求性能最优,适用于对效率要求高的场景。",
            )
        )
    else:
        # 序列输出：各输出值整体偏移 ±5%（alt_*_value 仅供启发式说明，不参与构造）

        alternatives.append(
            AlternativePlan(
                plan_id=f"alt_{uuid.uuid4().hex[:8]}",
                parameters={
                    "optimization_target": "conservative",
                    "safety_margin": "+5%",
                },
                expected_outcome="保守方案:输出值整体下调5%,偏向安全边际,适合对稳定性要求高的场景。",
                confidence=max(0.0, primary_confidence - 0.05),
                reasoning="保守方案通过降低各输出值约5%提供额外的安全缓冲,适用于风险敏感型决策。",
            )
        )

        alternatives.append(
            AlternativePlan(
                plan_id=f"alt_{uuid.uuid4().hex[:8]}",
                parameters={
                    "optimization_target": "aggressive",
                    "efficiency_gain": "+5%",
                },
                expected_outcome="激进方案:输出值整体上调5%,偏向性能优化,适合追求效率的场景。",
                confidence=max(0.0, primary_confidence - 0.08),
                reasoning="激进方案通过提高各输出值约5%追求性能最优,适用于对效率要求高的场景。",
            )
        )

    return alternatives


async def _broadcast_error(task_id: str, code: str, message: str):
    """通过 SSE 广播错误消息。"""
    await sse_manager.broadcast(
        task_id,
        "error",
        {
            "code": code,
            "message": message,
            "details": {},
        },
    )


async def run_training_task_v2(
    task_id: str,
    model_name: str,
    data_path: str,
    hyperparameters: dict,
    device_preference: str,
    cancel_evt: asyncio.Event,
    progress_updater: Callable,
):
    """V2 训练执行器,带进度回调和取消支持。"""
    metrics = None
    try:
        from app.utils.utils import get_metrics_collector

        metrics = get_metrics_collector()
        metrics.set_active_training_tasks(max(0, metrics._active_training_tasks + 1))
    except (ImportError, AttributeError, RuntimeError) as e:
        logger.debug(
            f"Failed to increment active training tasks counter: {e}",
            exc_info=True,
        )

    try:
        # P0-3-a 重构：将数据加载、数据集构建、训练器构造、训练循环逻辑
        # 拆分到 ``_training_executor`` 模块,本函数仅保留编排与计数器管理。
        from app.api.v1.lnn._training_executor import (
            _load_training_data,
            _prepare_datasets,
            _build_trainer,
            _execute_training_loop,
        )

        await progress_updater(5.0, "Loading data...")
        X, y, input_dim = await _load_training_data(data_path)

        await progress_updater(10.0, "Preparing datasets...")
        train_loader, val_loader, train_size, val_size, device, num_workers = _prepare_datasets(
            X, y, hyperparameters, device_preference
        )

        use_amp = device.type == "cuda" and torch.cuda.is_available()
        epochs = hyperparameters.get("epochs", 100)

        await progress_updater(15.0, f"Starting training on {device.type}...")

        model, trainer, hidden_size, entry = _build_trainer(model_name, input_dim, hyperparameters, device, use_amp)

        return await _execute_training_loop(
            trainer,
            train_loader,
            val_loader,
            model,
            device,
            epochs,
            cancel_evt,
            progress_updater,
            model_name,
            input_dim,
            hidden_size,
            entry,
            use_amp,
            num_workers,
            train_size,
            val_size,
            hyperparameters,
        )
    finally:
        # 确保无论成功还是异常，计数器都会递减
        if metrics is not None:
            try:
                metrics.set_active_training_tasks(max(0, metrics._active_training_tasks - 1))
            except (ImportError, AttributeError, RuntimeError) as e:
                logger.debug(
                    f"Failed to decrement active training tasks counter: {e}",
                    exc_info=True,
                )


async def _run_training_task_async(
    task_id: str,
    model_name: str,
    data_path: str,
    hyperparameters: dict,
    device: str,
):
    """在 FastAPI 主事件循环中跑训练任务。"""
    from datetime import datetime as _datetime
    from app.api.v1.lnn.dependencies import task_manager

    # H16 修复：跨文件访问 _training_queues 必须加锁，
    # 避免 routes.py 并发写入时读到不一致的字典状态。
    _queues_lock = getattr(task_manager, "_training_queues_lock", None)
    if _queues_lock is not None:
        with _queues_lock:
            _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
            queue_data = dict(_TRAINING_QUEUES.get(task_id, {}))
    else:
        _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
        queue_data = _TRAINING_QUEUES.get(task_id, {})
    progress_q = queue_data.get("progress")
    cancel_evt = queue_data.get("cancel")
    if progress_q is None or cancel_evt is None:
        return

    started_at = _datetime.now(timezone.utc).isoformat()
    await progress_q.put(
        (
            "started",
            {
                "job_id": task_id,
                "started_at": started_at,
                "resources": {"max_concurrent": 3},
            },
        )
    )

    async def progress_updater(progress, message="", metrics=None):
        try:
            progress_q.put_nowait(
                (
                    "progress",
                    {
                        "job_id": task_id,
                        "percent": round(progress, 1),
                        "message": message,
                        "metrics": metrics or {},
                    },
                )
            )
        except asyncio.QueueFull:
            logger.debug("SSE progress queue full, dropping message")

    try:
        result = await run_training_task_v2(
            task_id,
            model_name,
            data_path,
            hyperparameters,
            device,
            cancel_evt,
            progress_updater,
        )
        await progress_q.put(
            (
                "complete",
                {
                    "job_id": task_id,
                    "result": result,
                    "completed_at": _datetime.now(timezone.utc).isoformat(),
                },
            )
        )
    except asyncio.CancelledError:
        await progress_q.put(
            (
                "cancelled",
                {
                    "job_id": task_id,
                    "cancelled_at": _datetime.now(timezone.utc).isoformat(),
                    "progress": 0,
                },
            )
        )
    except (ValueError, TypeError, RuntimeError, OSError) as e:
        safe = safe_error_message(
            e,
            context=f"lnn.training_worker[{task_id}]",
        )
        logger.error(
            "lnn training worker failed | task_id=%s | error_id=%s | exc=%s: %s",
            task_id,
            safe.get("error_id"),
            type(e).__name__,
            e,
        )
        await progress_q.put(
            (
                "failed",
                {
                    "job_id": task_id,
                    "error": safe["message"],
                    "error_id": safe.get("error_id"),
                    "failed_at": _datetime.now(timezone.utc).isoformat(),
                },
            )
        )


async def _broadcast_training_events(task_id: str):
    """广播训练事件到 SSE 客户端。"""
    from app.api.v1.lnn.dependencies import task_manager

    # H16 修复：跨文件读取 _training_queues 加锁，避免并发写入导致字典状态不一致
    cancel_evt: asyncio.Event | None = None
    progress_q: asyncio.Queue | None = None
    q = None
    _queues_lock = getattr(task_manager, "_training_queues_lock", None)
    if _queues_lock is not None:
        with _queues_lock:
            _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
            q = _TRAINING_QUEUES.get(task_id)
            if q:
                # 拿到引用后在锁外操作 queue（Queue 本身线程安全）
                cancel_evt = q["cancel"]
                progress_q = q["progress"]
    else:
        _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
        q = _TRAINING_QUEUES.get(task_id)
        if not q:
            return
        cancel_evt = q["cancel"]
        progress_q = q["progress"]

    if not q or cancel_evt is None or progress_q is None:
        return

    while True:
        try:
            item = await progress_q.get()
            event_type, data = item
            logger.debug("[BROADCAST] %s: %s", task_id, event_type)
            if event_type == "progress":
                await task_manager._broadcast_event(task_id, "progress", data)
            elif event_type == "complete":
                record = await task_manager.get_task(task_id)
                if record:
                    record.status = TaskStatus.COMPLETED
                    record.progress = 100.0
                    record.completed_at = time.time()
                    record.result = data.get("result")
                    await task_manager._persist_task_to_db(record)
                    await task_manager._broadcast_event(task_id, "complete", data)
                    if task_id in task_manager._tasks:
                        task_manager._tasks[task_id].status = TaskStatus.COMPLETED
                        task_manager._tasks[task_id].progress = 100.0
                        task_manager._tasks[task_id].completed_at = time.time()
                        task_manager._tasks[task_id].result = data.get("result")
                break
            elif event_type == "cancelled":
                record = await task_manager.get_task(task_id)
                if record:
                    record.status = TaskStatus.CANCELLED
                    record.completed_at = time.time()
                    await task_manager._persist_task_to_db(record)
                    await task_manager._broadcast_event(task_id, "cancelled", data)
                    if task_id in task_manager._tasks:
                        task_manager._tasks[task_id].status = TaskStatus.CANCELLED
                        task_manager._tasks[task_id].completed_at = time.time()
                break
            elif event_type == "failed":
                record = await task_manager.get_task(task_id)
                if record:
                    record.status = TaskStatus.FAILED
                    record.error = data.get("error", "")
                    record.completed_at = time.time()
                    await task_manager._persist_task_to_db(record)
                    await task_manager._broadcast_event(task_id, "failed", data)
                    if task_id in task_manager._tasks:
                        task_manager._tasks[task_id].status = TaskStatus.FAILED
                        task_manager._tasks[task_id].error = data.get("error", "")
                        task_manager._tasks[task_id].completed_at = time.time()
                break
            elif event_type == "started":
                record = await task_manager.get_task(task_id)
                if record:
                    record.status = TaskStatus.RUNNING
                    record.started_at = time.time()
                    await task_manager._persist_task_to_db(record)
                    await task_manager._broadcast_event(task_id, "started", data)
                    if task_id in task_manager._tasks:
                        task_manager._tasks[task_id].status = TaskStatus.RUNNING
        except asyncio.CancelledError:
            break
        except (RuntimeError, ValueError, KeyError) as exc:
            logger.warning(
                "lnn SSE consumer loop异常退出 | task_id=%s | exc=%s: %s",
                task_id,
                type(exc).__name__,
                exc,
            )
            break
        else:
            if cancel_evt.is_set() and progress_q.empty():
                break

    # 修复 P0-10：训练结束后主动清理队列，避免内存泄漏
    # H16 修复：pop 操作也需要加锁，避免与 routes.py 的写入操作竞态
    if _queues_lock is not None:
        with _queues_lock:
            _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
            _TRAINING_QUEUES.pop(task_id, None)
    else:
        _TRAINING_QUEUES = getattr(task_manager, "_training_queues", {})
        _TRAINING_QUEUES.pop(task_id, None)


async def _run_quantization_task_v2(
    task_id: str,
    model_name: str,
    quantization_type: str,
    calibration_data_path: str | None,
    cancel_evt: asyncio.Event,
    progress_updater: Callable,
):
    """真正的量化执行器(可取消,带进度回调)。"""
    # P0-3-a 重构：将模型加载、校准数据加载、量化器执行与量化模型注册
    # 拆分到 ``_quantization_executor`` 模块,本函数仅保留编排。
    from app.api.v1.lnn._quantization_executor import (
        _load_model_for_quantization,
        _load_calibration_data_async,
        _run_quantizer,
        _register_quantized_model,
    )

    await progress_updater(5.0, "加载模型元信息...")
    model, entry, model_path_str = _load_model_for_quantization(model_name)

    await progress_updater(15.0, "加载模型权重...")

    quantized_model_name = get_quantized_model_name(model_name)
    output_dir = os.path.dirname(model_path_str)
    quantized_model_path = os.path.join(output_dir, f"{quantized_model_name}.pt")

    calibration_data = None
    if quantization_type == "static":
        if calibration_data_path is None:
            raise ValueError("静态量化必须提供校准数据路径（calibration_data_path）")
        calibration_data = await _load_calibration_data_async(calibration_data_path)
        await progress_updater(30.0, "加载校准数据...")

    await progress_updater(50.0, "执行量化...")
    if cancel_evt.is_set():
        raise asyncio.CancelledError()

    quantized_model, result, quantizer = _run_quantizer(
        model, model_name, quantization_type, calibration_data, quantized_model_path
    )

    await progress_updater(85.0, "计算模型大小...")
    original_size = quantizer.get_model_size(model_path_str)
    quantized_size = quantizer.get_model_size(quantized_model_path)
    result.original_size_bytes = original_size
    result.quantized_size_bytes = quantized_size

    await progress_updater(95.0, "注册量化模型...")
    _register_quantized_model(
        model_name,
        entry,
        quantized_model_path,
        original_size,
        quantized_size,
        result,
        quantization_type,
    )

    await progress_updater(100.0, "量化完成")

    return {
        "task_id": task_id,
        "status": "success",
        "message": "Quantization completed successfully",
        "metrics": {
            "quantized_model_name": quantized_model_name,
            "quantized_model_path": quantized_model_path,
            "original_size_bytes": original_size,
            "quantized_size_bytes": quantized_size,
            "original_size_human": format_bytes(original_size),
            "quantized_size_human": format_bytes(quantized_size),
            "compression_ratio": round(result.compression_ratio, 4),
            "speedup_ratio": round(result.speedup_ratio, 4),
            "quantization_time_seconds": round(result.quantization_time_seconds, 2),
        },
    }


async def run_batch_inference_v2(
    job_id: str,
    model_name: str,
    input_data: list,
    batch_size: int,
    cancel_evt: asyncio.Event,
    progress_updater: Callable,
):
    """V2 批量推理执行器,带进度回调。"""
    from app.api.v1.lnn.dependencies import model_registry

    await progress_updater(5.0, "Loading model...")

    predictor = LNNPredictor.from_registry(
        registry=model_registry,
        model_name=model_name,
        use_amp=True,
        auto_device=True,
    )

    results = []
    total = len(input_data)

    for i in range(0, total, batch_size):
        if cancel_evt.is_set():
            raise asyncio.CancelledError()

        batch = input_data[i : i + batch_size]

        # 使用 predict_batch 替代逐样本 predict，将 N 次 forward pass 合并为 1 次。
        # 同时通过 asyncio.to_thread 将 CPU/GPU 密集型推理移至工作线程，
        # 避免阻塞事件循环（SSE 心跳、取消信号等其他协程可继续运行）。
        batch_predictions = await asyncio.to_thread(predictor.predict_batch, batch, len(batch))

        for result in batch_predictions:
            value = result.value
            if hasattr(value, "tolist"):
                value = value.tolist()
            results.append({"value": value, "confidence": result.confidence})

        progress = 10.0 + ((i + len(batch)) / total) * 85.0
        await progress_updater(progress, f"Processed {i + len(batch)}/{total} samples")

    await progress_updater(100.0, "Batch inference completed")

    return {
        "status": "completed",
        "total_samples": total,
        "results": results,
        "metrics": {"samples_processed": total},
    }


async def sse_event_generator(task_id: str, client_id: str):
    """SSE 事件生成器,用于训练任务状态更新。"""
    client = await sse_manager.subscribe(task_id, client_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(client.queue.get(), timeout=SSE_HEARTBEAT_TIMEOUT_SEC)
                yield event
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    except asyncio.CancelledError:
        logger.info("SSE stream cancelled for client %s", client_id)
    finally:
        await sse_manager.unsubscribe(task_id, client_id)
