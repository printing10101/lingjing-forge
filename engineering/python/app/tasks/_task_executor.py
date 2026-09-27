"""任务执行器（适配器模式，从 execution 拆出）。"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any
from collections.abc import Callable

from app.tasks._execution_models import ExecutionResult, ExecutionStatus

logger = logging.getLogger(__name__)


class TaskExecutor:
    """任务执行器 - 适配器模式"""

    def __init__(self):
        self._executors: dict[str, Callable] = {}
        self._register_default_executors()

    def _register_default_executors(self) -> None:
        """注册默认执行器"""
        self._executors["lnn_inference"] = self._execute_lnn_inference
        self._executors["lnn_training"] = self._execute_lnn_training
        self._executors["lnn_analysis"] = self._execute_lnn_analysis

    def register_executor(self, task_type: str, executor: Callable) -> None:
        """注册自定义执行器"""
        self._executors[task_type] = executor

    async def execute(
        self,
        task_type: str,
        workspace_context: Any,
        params: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """
        执行任务

        Args:
            task_type: 任务类型
            workspace_context: 工作空间上下文
            params: 任务参数

        Returns:
            执行结果
        """
        executor = self._executors.get(task_type)

        if executor is None:
            raise ValueError(f"No executor registered for task type: {task_type}")

        start_time = time.time()

        try:
            # 修复 P2：sync 执行器（含 np.loadtxt / torch 训练等阻塞 IO）通过
            # asyncio.to_thread 移至线程池执行，避免阻塞事件循环；async
            # 执行器则直接 await。通过 iscoroutinefunction 区分两种情况。
            if asyncio.iscoroutinefunction(executor):
                result = await executor(workspace_context, params or {})
            else:
                result = await asyncio.to_thread(executor, workspace_context, params or {})

            duration_ms = (time.time() - start_time) * 1000

            return ExecutionResult(
                task_id=workspace_context.task_id,
                status=ExecutionStatus.COMPLETED,
                start_time=start_time,
                end_time=time.time(),
                duration_ms=duration_ms,
                result_data=result if isinstance(result, dict) else {"output": result},
            )
        except (RuntimeError, ValueError, TypeError, TimeoutError, OSError):
            duration_ms = (time.time() - start_time) * 1000

            return ExecutionResult(
                task_id=workspace_context.task_id,
                status=ExecutionStatus.FAILED,
                start_time=start_time,
                end_time=time.time(),
                duration_ms=duration_ms,
                error_message="任务执行失败: 内部错误，请联系管理员",
                error_traceback=traceback.format_exc(),
            )

    def _execute_lnn_inference(self, workspace_context: Any, params: dict[str, Any]) -> dict[str, Any]:
        """执行LNN推理任务"""
        from app.ai.lnn.inference.predictor import LNNPredictor
        from app.dependencies import get_model_registry_service
        import numpy as np

        logger.info("Executing LNN inference for task %s", workspace_context.task_id)

        if workspace_context.model_path is None:
            raise RuntimeError("No model path available for inference")

        input_data = params.get("input_data")
        if input_data is None:
            raise ValueError("Missing input_data for inference")

        input_array = np.array(input_data, dtype=np.float32)  # M11 修复：指定 float32 dtype
        if input_array.ndim == 1:
            input_array = input_array.reshape(1, -1)

        # 接口修复：使用全局 model_registry 而非空字典 {}
        registry = get_model_registry_service().model_registry
        predictor = LNNPredictor.from_registry(
            registry=registry,
            model_name=workspace_context.model_path,
        )

        result = predictor.predict(input_array, return_confidence=True)

        return {
            "prediction": result.value.tolist() if hasattr(result.value, "tolist") else result.value,
            "confidence": result.confidence,
            "inference_time": result.inference_time,
        }

    def _execute_lnn_training(self, workspace_context: Any, params: dict[str, Any]) -> dict[str, Any]:
        """执行 LNN 训练任务。

        接口修复说明：
            原实现错误调用 ``LNNTrainer(model_name=..., dataset_path=..., output_dir=...)``
            与 ``trainer.train(params)`` / ``trainer.get_metrics()``，与
            ``trainer.py`` 真实签名（``__init__(model, ...)`` / ``fit`` /
            ``get_training_summary``）不匹配。此处改为：
                1. 从全局 model_registry 获取模型实例
                2. 从 dataset_path 加载 CSV 为 DataLoader
                3. 调用 ``fit(train_loader, val_loader)``
                4. 通过 ``get_training_summary()`` 获取训练指标
        """
        # P0#3 解耦: 通过 research_bridge 延迟导入
        from app.ai.lnn._research_bridge import get_trainer_factory

        LNNTrainer = get_trainer_factory()
        if LNNTrainer is None:
            raise ImportError("Research package not available for training")
        from app.dependencies import get_model_registry_service
        import torch
        from torch.utils.data import DataLoader, TensorDataset
        import numpy as np

        logger.info("Executing LNN training for task %s", workspace_context.task_id)

        if not workspace_context.dataset_path:
            raise RuntimeError("LNN训练任务缺少数据集路径。请指定训练数据集的文件路径。")

        # 1. 从全局 registry 获取模型实例（trainer.__init__ 需要 nn.Module）
        registry_service = get_model_registry_service()
        model_name = workspace_context.model_name or "default"
        model = registry_service.model_registry.get(model_name)
        if model is None:
            raise RuntimeError(f"模型 '{model_name}' 在注册表中未找到，请先注册或加载模型。")

        # 2. 从 dataset_path 加载 CSV 数据为 DataLoader
        dataset_path = workspace_context.dataset_path
        try:
            data = np.loadtxt(dataset_path, delimiter=",", skiprows=1)
        except (OSError, ValueError) as e:
            raise RuntimeError(f"数据集加载失败: {dataset_path}。错误: {e}") from e

        if data.ndim == 1:
            data = data.reshape(-1, 1)
        features = torch.tensor(data[:, :-1], dtype=torch.float32)
        labels = torch.tensor(data[:, -1], dtype=torch.float32).unsqueeze(1)
        full_dataset = TensorDataset(features, labels)

        # 简单 8:2 训练/验证划分
        n_total = len(full_dataset)
        n_train = max(1, int(n_total * 0.8))
        n_val = n_total - n_train
        train_dataset, val_dataset = torch.utils.data.random_split(full_dataset, [n_train, n_val])

        batch_size = params.get("batch_size", 64)
        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size) if n_val > 0 else train_loader

        # 3. 构造 trainer 并训练（使用真实接口签名）
        trainer = LNNTrainer(
            model=model,
            learning_rate=params.get("learning_rate", 0.001),
            epochs=params.get("epochs", 200),
            batch_size=batch_size,
            device=params.get("device", "cpu"),
        )
        trainer.fit(train_loader, val_loader)

        # 4. 保存模型到 output_dir
        if workspace_context.model_path:
            try:
                trainer.save_checkpoint(workspace_context.model_path)
            except (OSError, RuntimeError) as e:
                logger.warning("保存模型检查点失败: %s", e)

        return {
            "status": "training_completed",
            "model_path": workspace_context.model_path,
            "dataset_path": workspace_context.dataset_path,
            # 接口修复：get_metrics() get_training_summary()
            "metrics": trainer.get_training_summary(),
        }

    def _execute_lnn_analysis(self, workspace_context: Any, params: dict[str, Any]) -> dict[str, Any]:
        from app.ai.lnn.inference.predictor import LNNPredictor
        from app.dependencies import get_model_registry_service
        import numpy as np

        logger.info("Executing LNN analysis for task %s", workspace_context.task_id)

        input_data = params.get("input_data")
        if input_data is None:
            raise ValueError("分析任务缺少输入数据。请在params中提供input_data。")

        input_array = np.array(input_data, dtype=np.float32)  # M12 修复：指定 float32 dtype
        if input_array.ndim == 1:
            input_array = input_array.reshape(1, -1)

        # 接口修复：使用全局 model_registry 而非空字典 {}
        registry = get_model_registry_service().model_registry
        predictor = LNNPredictor.from_registry(
            registry=registry,
            model_name=workspace_context.model_path or "default",
        )
        result = predictor.predict(input_array, return_confidence=True)

        return {
            "status": "analysis_completed",
            "workspace": workspace_context.workspace_dir,
            "prediction": result.value.tolist() if hasattr(result.value, "tolist") else result.value,
            "confidence": result.confidence,
        }
