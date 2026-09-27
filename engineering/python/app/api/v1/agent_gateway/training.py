"""训练相关端点（含训练并发控制）。

P1-7：从原 ``agent_gateway.py`` 拆分而来，包含：
- ``POST /train`` —— 启动训练任务（异步，返回 job_id）
- ``GET  /train/{job_id}`` —— 查询训练状态
- ``_run_agent_training`` —— 后台训练 worker（使用 :class:`TrainingCoordinator` 控制并发）
"""

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.v1.agent_gateway._state import (
    model_registry,
    training_coordinator,
    training_tasks,
)
from app.api.v1.sse import create_progress_callback, sse_manager
from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.core.response_models import ErrorResponse, SuccessResponse
from app.core.safe_errors import safe_error_message
from app.middleware.rate_limiter import limiter
from app.models.schemas import AgentTrainRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Agent Gateway"])

# 模块级任务引用集合：保存 asyncio.create_task 返回的 Task 对象，
# 防止任务被 GC 提前回收（CPython 弱引用机制下，本地变量出作用域即可能被回收）。
# 任务完成后由 done_callback 自动从集合中移除。
_active_agent_training_tasks: set[asyncio.Task] = set()


async def _run_agent_training(
    task_id: str,
    model_name: str,
    data_path: str,
    hyperparameters: dict,
    device: str = "auto",
) -> None:
    """Background training task."""
    async with training_coordinator.get_semaphore():
        training_coordinator.add_active(task_id)
        try:
            training_tasks[task_id]["status"] = "in_progress"

            if not os.path.exists(data_path):
                training_tasks[task_id]["status"] = "failed"
                # [H13] 不泄露服务器内部路径，仅记录通用错误消息给客户端；
                # 完整路径仅写入服务端日志便于运维排查
                logger.error("Training %s data file not found: %s", task_id, data_path)
                training_tasks[task_id]["message"] = "训练数据文件不存在，请检查上传的数据集"
                return

            entry = model_registry.registry.get(model_name)
            if not entry:
                training_tasks[task_id]["status"] = "failed"
                training_tasks[task_id]["message"] = f"Model '{model_name}' not found"
                return

            import torch
            import numpy as np
            from torch.utils.data import DataLoader, TensorDataset

            # P0#3 解耦: 通过 research_bridge 延迟导入，避免工程侧直接依赖 research/
            from app.ai.lnn._research_bridge import (
                get_lnn_config_factory,
                get_cfc_model_factory,
                get_device_detect,
                get_device_optimal_batch_size,
                get_device_optimal_num_workers,
                get_trainer_factory,
            )

            LNNConfig = get_lnn_config_factory()
            TorchCFCModel = get_cfc_model_factory()
            LNNTrainer = get_trainer_factory()
            detect_device = get_device_detect()
            get_optimal_batch_size = get_device_optimal_batch_size()
            get_optimal_num_workers = get_device_optimal_num_workers()
            if (
                LNNConfig is None
                or TorchCFCModel is None
                or LNNTrainer is None
                or detect_device is None
                or get_optimal_batch_size is None
                or get_optimal_num_workers is None
            ):
                raise ImportError("Research package not available for training")

            data = await asyncio.to_thread(np.loadtxt, data_path, delimiter=",")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            if data.shape[0] < 2:
                training_tasks[task_id]["status"] = "failed"
                training_tasks[task_id]["message"] = "Need at least 2 samples"
                return

            X = data[:, :-1]
            y = data[:, -1]
            input_dim = data.shape[1] - 1

            X_tensor = torch.FloatTensor(X)
            y_tensor = torch.FloatTensor(y)
            dataset = TensorDataset(X_tensor, y_tensor)
            train_size = int(0.8 * len(dataset))
            train_ds, val_ds = torch.utils.data.random_split(dataset, [train_size, len(dataset) - train_size])

            device_obj, _ = detect_device(device)
            batch_size = hyperparameters.get("batch_size", 32)
            if device_obj.type == "cuda":
                batch_size = get_optimal_batch_size(device_obj, batch_size)
            num_workers = get_optimal_num_workers()

            train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=num_workers)
            val_loader = DataLoader(val_ds, batch_size=batch_size, num_workers=num_workers)

            hidden_size = min(256, max(64, input_dim * 2))
            config_obj = LNNConfig(
                input_size=input_dim,
                hidden_size=hidden_size,
                output_size=1,
                num_layers=2,
                dropout=0.1,
            )
            model = TorchCFCModel(config_obj)

            use_amp = device_obj.type == "cuda" and torch.cuda.is_available()
            epochs = hyperparameters.get("epochs", 100)
            progress_cb = create_progress_callback(task_id, epochs)
            cancel_evt = sse_manager.get_cancel_event(task_id)

            trainer = LNNTrainer(
                model=model,
                learning_rate=hyperparameters.get("learning_rate", 0.001),
                optimizer_type=hyperparameters.get("optimizer", "adam"),
                loss_type="mse",
                batch_size=batch_size,
                epochs=epochs,
                device=str(device_obj),
                use_amp=use_amp,
                progress_callback=progress_cb,
                cancel_event=cancel_evt,
            )

            start = time.perf_counter()
            try:
                history = trainer.fit(train_loader, val_loader)
                elapsed = time.perf_counter() - start
                final_val_loss = history["val_loss"][-1] if history["val_loss"] else 0.0

                training_tasks[task_id]["status"] = "success"
                training_tasks[task_id]["message"] = "Training completed"
                training_tasks[task_id]["metrics"] = {
                    "loss": round(final_val_loss, 4),
                    "training_time": round(elapsed, 2),
                    "epochs_completed": len(history["train_loss"]),
                }
                await progress_cb.send_complete("completed", final_val_loss, elapsed)
            except asyncio.CancelledError:
                training_tasks[task_id]["status"] = "cancelled"
                training_tasks[task_id]["message"] = "Training cancelled"
            except (RuntimeError, ValueError, KeyError, OSError, TypeError, AttributeError) as e:
                # 修复：使用 safe_error_message 包装异常，避免直接
                # 将 str(e) 写入 training_tasks.message 暴露内部错误详情。
                safe = safe_error_message(e, context=f"agent.train_worker[{task_id}]")
                logger.error(
                    "Agent training worker failed | task_id=%s | error_id=%s | exc=%s: %s",
                    task_id,
                    safe.get("error_id"),
                    type(e).__name__,
                    e,
                )
                training_tasks[task_id]["status"] = "failed"
                training_tasks[task_id]["message"] = safe["message"]
                training_tasks[task_id]["error_id"] = safe.get("error_id")

        finally:
            training_coordinator.discard_active(task_id)


# 认证：训练操作需要 agent:train 权限
@router.post(
    "/train",
    dependencies=[Depends(require_permission("agent:train"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
# P2-4-3 修复：训练消耗大量 GPU/CPU 资源，限制为 5/hour（与 lnn/routes.py train 一致）。
@limiter.limit("5/hour")
async def agent_train(request: Request, body: AgentTrainRequest):
    """启动训练（B类，异步，返回job_id）"""
    try:
        task_id = str(uuid.uuid4())
        training_tasks[task_id] = {
            "status": "queued",
            "message": "Training task queued",
            "metrics": None,
        }

        hyperparams = {
            "learning_rate": body.hyperparameters.learning_rate,
            "epochs": body.hyperparameters.epochs,
            "batch_size": body.hyperparameters.batch_size,
            "optimizer": body.hyperparameters.optimizer,
        }

        # 修复：保存任务引用防止 GC 提前回收，并添加异常处理
        task = asyncio.create_task(
            _run_agent_training(
                task_id,
                body.model_name,
                body.data_path,
                hyperparams,
                body.device,
            )
        )
        _active_agent_training_tasks.add(task)

        def _on_train_done(t: asyncio.Task) -> None:
            _active_agent_training_tasks.discard(t)
            training_coordinator.handle_task_done(t, task_id)

        task.add_done_callback(_on_train_done)

        return success(
            data={
                "job_id": task_id,
                "status": "queued",
                "message": "Training task queued",
            },
            message="Training task started",
        )

    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as e:
        # 修复：使用 safe_error_message 包装异常，避免直接
        # 将 str(e) 暴露到 HTTP 错误响应中。
        safe = safe_error_message(e, context=f"agent.train_init[{body.model_name}]")
        logger.warning(
            "Training initiation failed | model=%s | error_id=%s | exc=%s: %s",
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


@router.get(
    "/train/{job_id}",
    dependencies=[Depends(require_permission("agent:read"))],
    response_model=SuccessResponse[dict[str, Any]],
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def get_train_status(job_id: str):
    """训练状态（R类）"""
    if job_id not in training_tasks:
        return error(code=ErrorCode.NOT_FOUND, message=f"Training task '{job_id}' not found")

    result = training_tasks[job_id]
    return success(
        data={
            "job_id": job_id,
            "status": result["status"],
            "message": result["message"],
            "metrics": result.get("metrics"),
            "is_active": training_coordinator.is_active(job_id),
        }
    )
