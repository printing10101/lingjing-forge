"""LNN 模型训练器。

实现 PyTorch 训练循环，包含优化器、损失函数、混合精度训练 (AMP)、
梯度裁剪、学习率调度、早停及 GPU 内存监控。
"""

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import nullcontext
import time
import os
import asyncio
import logging
import numpy as np
from datetime import datetime

from .device_manager import (
    check_gpu_memory_safe,
    clear_gpu_memory,
)
from .reproducibility import (
    set_global_seed,
    get_worker_init_fn,
)
from .experiment_tracker import (
    start_run as mlflow_start_run,
    log_params as mlflow_log_params,
    log_metrics as mlflow_log_metrics,
    log_model as mlflow_log_model,
)

logger = logging.getLogger(__name__)


# Trainer constants
DEFAULT_SGD_MOMENTUM = 0.9  # Default momentum for SGD optimizer
DEFAULT_STEP_LR_STEP_SIZE = 30  # Default step size for StepLR scheduler
DEFAULT_LR_DECAY_GAMMA = 0.1  # Default gamma for learning rate decay
DEFAULT_PLATEAU_PATIENCE = 10  # Default patience for ReduceLROnPlateau
DEFAULT_EXPONENTIAL_GAMMA = 0.95  # Default gamma for ExponentialLR scheduler
BCE_CONFIDENCE_THRESHOLD = 0.5  # Confidence threshold for BCE loss predictions
GPU_MEMORY_LOG_INTERVAL = 10  # Epoch interval for GPU memory logging
BYTES_PER_MB = 1024**2  # Conversion factor from bytes to megabytes
GPU_MEMORY_WARNING_THRESHOLD = 95.0  # GPU memory usage warning threshold (percent)


class LNNTrainer:
    """LNN 训练器，封装训练循环与检查点管理。"""

    def __init__(
        self,
        model: nn.Module,
        learning_rate: float = 0.001,
        optimizer_type: str = "adamw",
        loss_type: str = "mse",
        batch_size: int = 64,
        epochs: int = 200,
        early_stopping_patience: int = 10,
        gradient_clip_value: Optional[float] = 1.0,
        lr_scheduler_type: str = "cosine",
        lr_scheduler_params: Optional[Dict[str, Any]] = None,
        device: Union[str, torch.device] = "cpu",
        use_amp: bool = True,
        weight_decay: float = 1e-5,
        progress_callback: Optional[Any] = None,
        cancel_event: Optional[Any] = None,
        seed: int = 42,
        track_experiment: bool = True,
        # ADR-005 阶段 2：实验快照集成（可选，None 时不启用）
        snapshot_store: Optional[Any] = None,
        dataset_versions: Optional[List[str]] = None,
        model_uri: Optional[str] = None,
        created_by: str = "system:trainer",
        workflow_spec: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化训练器

        Args:
            model: 要训练的LNN模型 (PyTorch nn.Module)
            learning_rate: 学习率
            optimizer_type: 优化器类型 ('sgd', 'adam', 'rmsprop', 'adamw')
            loss_type: 损失函数类型 ('cross_entropy', 'mse', 'mae', 'bce')
            batch_size: 批次大小
            epochs: 训练轮数
            early_stopping_patience: 早停耐心值 (默认5轮)
            gradient_clip_value: 梯度裁剪阈值 (None表示不裁剪)
            lr_scheduler_type: 学习率调度器类型 ('step', 'cosine', 'reduce_on_plateau', 'exponential')
            lr_scheduler_params: 学习率调度器参数
            device: 计算设备 ('cpu', 'cuda', 或 'auto')
            use_amp: 是否启用自动混合精度训练
            progress_callback: 进度回调函数，每个epoch结束后调用
            cancel_event: asyncio.Event 用于接收取消信号
            seed: 随机种子，用于确保实验可复现性 (默认42)
            track_experiment: 是否启用 MLflow 实验追踪 (默认True)。
                mlflow 未安装时自动降级为空操作，不影响训练流程。
            snapshot_store: 可选的 ISnapshotStore 实例。提供后训练结束自动记录实验快照
                （含 git_sha + 配置 + 数据版本 + 指标 + 环境），支持一键复现。
            dataset_versions: 关联的数据集版本 URI 列表（dataset://<name>/<version>），
                写入快照用于血缘追溯。
            model_uri: 模型 URI（如 model://ltc/1.0.0），写入快照用于复现。
            created_by: 快照创建者标识，默认 "system:trainer"。
            workflow_spec: 可选的 WorkflowSpec dict，写入快照 config['workflow_spec']
                以支持一键复现（reproduce 时反序列化为 WorkflowSpec 并启动新运行）。
        """
        # 必须在任何随机操作之前调用，确保可复现性
        self.seed = seed
        set_global_seed(seed)

        self.track_experiment = track_experiment

        # ADR-005 阶段 2：实验快照集成参数
        self.snapshot_store = snapshot_store
        self.dataset_versions = list(dataset_versions) if dataset_versions else []
        self.model_uri = model_uri or "model://unknown"
        self.created_by = created_by
        self.workflow_spec = workflow_spec
        self._last_snapshot_id: Optional[str] = None

        self.model = model
        self.learning_rate = learning_rate
        self.optimizer_type = optimizer_type
        self.loss_type = loss_type
        self.batch_size = batch_size
        self.epochs = epochs
        self.early_stopping_patience = early_stopping_patience
        self.gradient_clip_value = gradient_clip_value
        self.lr_scheduler_type = lr_scheduler_type
        self.lr_scheduler_params = lr_scheduler_params or {}
        self.weight_decay = weight_decay
        if isinstance(device, str):
            self.device = torch.device(device)
        else:
            self.device = device
        self.use_amp = use_amp and self.device.type == "cuda" and torch.cuda.is_available()
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event

        self.model = self.model.to(self.device)

        self.optimizer = self._create_optimizer()
        self.lr_scheduler = self._create_lr_scheduler()
        self.criterion = self._create_criterion()

        self.scaler = torch.cuda.amp.GradScaler() if self.use_amp else None

        self.current_epoch = 0
        self.best_val_loss = float("inf")
        self.patience_counter = 0
        self.training_history: Dict[str, Any] = {
            "train_loss": [],
            "val_loss": [],
            "train_accuracy": [],
            "val_accuracy": [],
            "train_r2": [],
            "val_r2": [],
            "learning_rate": [],
            "seed": seed,
        }

        self.best_model_state: Optional[Dict[str, Any]] = None

        self._log_device_info()

    def _log_device_info(self) -> None:
        """记录训练设备信息到日志"""
        if self.device.type == "cuda":
            gpu_index = self.device.index if self.device.index is not None else 0
            props = torch.cuda.get_device_properties(gpu_index)
            vram_mb = props.total_memory / (1024**2)
            logger.info(
                f"Training on GPU: {props.name} "
                f"(VRAM: {vram_mb:.0f}MB, CUDA: {torch.version.cuda}, "
                f"Compute: {props.major}.{props.minor})"
            )
            if self.use_amp:
                logger.info("Mixed precision training (AMP) enabled")
        else:
            logger.info("Training on CPU")

    def _create_optimizer(self) -> torch.optim.Optimizer:
        """创建优化器"""
        if self.optimizer_type == "adam":
            return torch.optim.Adam(self.model.parameters(), lr=self.learning_rate)
        elif self.optimizer_type == "adamw":
            return torch.optim.AdamW(
                self.model.parameters(),
                lr=self.learning_rate,
                weight_decay=self.weight_decay,
            )
        elif self.optimizer_type == "sgd":
            return torch.optim.SGD(self.model.parameters(), lr=self.learning_rate, momentum=0.9)
        elif self.optimizer_type == "rmsprop":
            return torch.optim.RMSprop(self.model.parameters(), lr=self.learning_rate)
        else:
            return torch.optim.AdamW(
                self.model.parameters(),
                lr=self.learning_rate,
                weight_decay=self.weight_decay,
            )

    def _create_lr_scheduler(self) -> Optional[torch.optim.lr_scheduler._LRScheduler]:
        """创建学习率调度器"""
        if self.lr_scheduler_type == "step":
            step_size = self.lr_scheduler_params.get("step_size", 30)
            gamma = self.lr_scheduler_params.get("gamma", 0.1)
            return torch.optim.lr_scheduler.StepLR(self.optimizer, step_size=step_size, gamma=gamma)
        elif self.lr_scheduler_type == "cosine":
            T_max = self.lr_scheduler_params.get("T_max", self.epochs)
            eta_min = self.lr_scheduler_params.get("eta_min", 0)
            return torch.optim.lr_scheduler.CosineAnnealingLR(self.optimizer, T_max=T_max, eta_min=eta_min)
        elif self.lr_scheduler_type == "reduce_on_plateau":
            mode = self.lr_scheduler_params.get("mode", "min")
            factor = self.lr_scheduler_params.get("factor", 0.1)
            patience = self.lr_scheduler_params.get("patience", 10)
            return torch.optim.lr_scheduler.ReduceLROnPlateau(
                self.optimizer, mode=mode, factor=factor, patience=patience
            )
        elif self.lr_scheduler_type == "exponential":
            gamma = self.lr_scheduler_params.get("gamma", 0.95)
            return torch.optim.lr_scheduler.ExponentialLR(self.optimizer, gamma=gamma)
        else:
            return None

    def _create_criterion(self) -> nn.Module:
        """创建损失函数"""
        if self.loss_type == "cross_entropy":
            return nn.CrossEntropyLoss()
        elif self.loss_type == "mse":
            return nn.MSELoss()
        elif self.loss_type == "mae":
            return nn.L1Loss()
        elif self.loss_type == "bce":
            return nn.BCELoss()
        elif self.loss_type == "bce_with_logits":
            return nn.BCEWithLogitsLoss()
        else:
            return nn.CrossEntropyLoss()

    def train_epoch(self, dataloader: DataLoader) -> Tuple[float, float, float]:
        """
        实现单个epoch的训练逻辑，支持混合精度训练

        Args:
            dataloader: 训练数据加载器

        Returns:
            (训练损失, 训练准确率, 训练R²)
        """
        self.model.train()
        total_loss = 0.0
        total_correct = 0
        total_samples = 0
        all_preds = []
        all_labels = []

        for batch_X, batch_y in dataloader:
            batch_X = batch_X.to(self.device)
            batch_y = batch_y.to(self.device)

            self.optimizer.zero_grad()

            if self.use_amp and self.scaler is not None:
                with torch.cuda.amp.autocast():
                    outputs = self.model(batch_X)
                    if isinstance(outputs, tuple):
                        outputs = outputs[0]
                        if hasattr(self.model, "hidden_state") and self.model.hidden_state is not None:
                            self.model.hidden_state = self.model.hidden_state.detach()

                    loss = self.criterion(outputs, batch_y)

                self.scaler.scale(loss).backward()

                if self.gradient_clip_value is not None:
                    self.scaler.unscale_(self.optimizer)
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)

                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                outputs = self.model(batch_X)
                if isinstance(outputs, tuple):
                    outputs = outputs[0]
                    if hasattr(self.model, "hidden_state") and self.model.hidden_state is not None:
                        self.model.hidden_state = self.model.hidden_state.detach()

                loss = self.criterion(outputs, batch_y)

                loss.backward()

                if self.gradient_clip_value is not None:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)

                self.optimizer.step()

            total_loss += loss.item() * batch_X.size(0)
            all_preds.append(outputs.detach().cpu())
            all_labels.append(batch_y.detach().cpu())

            if self.loss_type in ["cross_entropy", "bce_with_logits"]:
                preds = torch.argmax(outputs, dim=1)
                true_labels = torch.argmax(batch_y, dim=1) if batch_y.ndim > 1 else batch_y
            elif self.loss_type == "bce":
                preds = (outputs > 0.5).float()
                true_labels = batch_y
            else:
                preds = outputs
                true_labels = batch_y

            total_correct += (preds == true_labels).sum().item()
            total_samples += batch_X.size(0)

        avg_loss = total_loss / total_samples
        accuracy = total_correct / total_samples

        # 计算R²
        all_preds = torch.cat(all_preds, dim=0).numpy()
        all_labels = torch.cat(all_labels, dim=0).numpy()
        r2 = self._compute_r2(all_labels, all_preds)

        return avg_loss, accuracy, r2

    def validate(self, dataloader: DataLoader) -> Tuple[float, float, float]:
        """
        实现模型验证逻辑

        Args:
            dataloader: 验证数据加载器

        Returns:
            (验证损失, 验证准确率, 验证R²)
        """
        self.model.eval()
        total_loss = 0.0
        total_correct = 0
        total_samples = 0
        all_preds = []
        all_labels = []

        # P2-AI-4: 使用 inference_mode 替代 no_grad，验证/评估阶段无需 autograd 图，更高效
        with torch.inference_mode():
            for batch_X, batch_y in dataloader:
                batch_X = batch_X.to(self.device)
                batch_y = batch_y.to(self.device)

                if self.use_amp:
                    with torch.cuda.amp.autocast():
                        outputs = self.model(batch_X)
                        if isinstance(outputs, tuple):
                            outputs = outputs[0]
                            if hasattr(self.model, "hidden_state") and self.model.hidden_state is not None:
                                self.model.hidden_state = self.model.hidden_state.detach()
                        loss = self.criterion(outputs, batch_y)
                else:
                    outputs = self.model(batch_X)
                    if isinstance(outputs, tuple):
                        outputs = outputs[0]
                        if hasattr(self.model, "hidden_state") and self.model.hidden_state is not None:
                            self.model.hidden_state = self.model.hidden_state.detach()
                    loss = self.criterion(outputs, batch_y)

                total_loss += loss.item() * batch_X.size(0)
                all_preds.append(outputs.detach().cpu())
                all_labels.append(batch_y.detach().cpu())

                if self.loss_type in ["cross_entropy", "bce_with_logits"]:
                    preds = torch.argmax(outputs, dim=1)
                    true_labels = torch.argmax(batch_y, dim=1) if batch_y.ndim > 1 else batch_y
                elif self.loss_type == "bce":
                    preds = (outputs > 0.5).float()
                    true_labels = batch_y
                else:
                    preds = outputs
                    true_labels = batch_y

                total_correct += (preds == true_labels).sum().item()
                total_samples += batch_X.size(0)

        avg_loss = total_loss / total_samples
        accuracy = total_correct / total_samples

        all_preds = torch.cat(all_preds, dim=0).numpy()
        all_labels = torch.cat(all_labels, dim=0).numpy()
        r2 = self._compute_r2(all_labels, all_preds)

        return avg_loss, accuracy, r2

    def fit(
        self,
        train_loader: DataLoader,
        val_loader: DataLoader,
        epochs: Optional[int] = None,
    ) -> Dict[str, List[float]]:
        """
        实现完整训练流程

        Args:
            train_loader: 训练数据加载器
            val_loader: 验证数据加载器
            epochs: 训练轮数 (如果为None则使用初始化时的设置)

        Returns:
            训练历史
        """
        # 学术诚信：在训练开始前设置随机种子，确保实验可复现
        # 必须在任何随机操作（DataLoader迭代、权重初始化、dropout等）之前执行
        set_global_seed(self.seed)

        epochs = epochs or self.epochs

        # 学术诚信：集成 MLflow 实验追踪，记录超参数和每个 epoch 的指标
        # mlflow 为软依赖，未安装时 start_run 降级为 no-op 上下文
        # track_experiment=False 时完全不追踪（即使 mlflow 已安装）
        run_name = f"seed{self.seed}_{self.optimizer_type}_lr{self.learning_rate}"
        if self.track_experiment:
            tracking_ctx = mlflow_start_run(
                run_name=run_name,
                experiment_name="lnn_training",
            )
        else:
            tracking_ctx = nullcontext()

        with tracking_ctx:
            mlflow_log_params(
                {
                    "learning_rate": self.learning_rate,
                    "optimizer_type": self.optimizer_type,
                    "loss_type": self.loss_type,
                    "batch_size": self.batch_size,
                    "epochs": epochs,
                    "seed": self.seed,
                    "early_stopping_patience": self.early_stopping_patience,
                    "gradient_clip_value": self.gradient_clip_value,
                    "lr_scheduler_type": self.lr_scheduler_type,
                    "lr_scheduler_params": str(self.lr_scheduler_params),
                    "weight_decay": self.weight_decay,
                    "device": str(self.device),
                    "use_amp": self.use_amp,
                }
            )

            train_size = len(train_loader.dataset)
            val_size = len(val_loader.dataset)

            logger.info(
                "Train: %d samples, Val: %d samples",
                train_size,
                val_size,
            )

            device_info = self.device.type.upper()
            if self.device.type == "cuda":
                gpu_index = self.device.index if self.device.index is not None else 0
                device_info = f"CUDA:{gpu_index} ({torch.cuda.get_device_properties(gpu_index).name})"

            logger.info("Starting training for %s epochs...", epochs)
            logger.info(
                "Optimizer: %s, LR: %s, Loss: %s",
                self.optimizer_type,
                self.learning_rate,
                self.loss_type,
            )
            logger.info(
                "Device: %s | AMP: %s",
                device_info,
                "enabled" if self.use_amp else "disabled",
            )

            training_start = time.perf_counter()

            for epoch in range(epochs):
                self.current_epoch = epoch + 1

                if self.cancel_event and self.cancel_event.is_set():
                    logger.info("Training cancelled at epoch %s", epoch + 1)
                    raise asyncio.CancelledError("Training cancelled by user")

                epoch_start = time.perf_counter()
                train_loss, train_acc, train_r2 = self.train_epoch(train_loader)
                epoch_time = time.perf_counter() - epoch_start

                val_loss, val_acc, val_r2 = self.validate(val_loader)

                self.training_history["train_loss"].append(train_loss)
                self.training_history["train_accuracy"].append(train_acc)
                self.training_history["val_loss"].append(val_loss)
                self.training_history["val_accuracy"].append(val_acc)
                self.training_history["train_r2"].append(train_r2)
                self.training_history["val_r2"].append(val_r2)
                self.training_history["learning_rate"].append(self.optimizer.param_groups[0]["lr"])

                # 学术诚信：每个 epoch 的指标记录到 MLflow
                mlflow_log_metrics(
                    {
                        "train_loss": train_loss,
                        "val_loss": val_loss,
                        "train_accuracy": train_acc,
                        "val_accuracy": val_acc,
                        "train_r2": train_r2,
                        "val_r2": val_r2,
                        "learning_rate": self.optimizer.param_groups[0]["lr"],
                    },
                    step=epoch,
                )

                if self.progress_callback:
                    try:
                        metrics = {
                            "train_accuracy": round(train_acc, 4),
                            "val_accuracy": round(val_acc, 4),
                            "train_loss": round(train_loss, 4),
                            "val_loss": round(val_loss, 4),
                            "train_r2": round(train_r2, 4),
                            "val_r2": round(val_r2, 4),
                        }
                        self.progress_callback(
                            epoch=self.current_epoch,
                            loss=val_loss,
                            metrics=metrics,
                        )
                    except (RuntimeError, ValueError, TypeError, AttributeError) as cb_err:
                        # 进度回调失败不应中断训练主流程，记录警告
                        logger.warning(f"Progress callback failed: {cb_err}", exc_info=True)

                device_display = device_info
                if self.device.type == "cuda" and epoch % 10 == 0:
                    gpu_index = self.device.index if self.device.index is not None else 0
                    mem_used_mb = torch.cuda.memory_allocated(gpu_index) / (1024**2)
                    mem_reserved_mb = torch.cuda.memory_reserved(gpu_index) / (1024**2)
                    device_display = f"{device_info} | GPU Mem: {mem_used_mb:.0f}/{mem_reserved_mb:.0f}MB"

                log_msg = (
                    f"Epoch {epoch + 1}/{epochs} | "
                    f"Device: {device_display} | "
                    f"Train Loss: {train_loss:.4f}, Train R²: {train_r2:.4f} | "
                    f"Val Loss: {val_loss:.4f}, Val R²: {val_r2:.4f} | "
                    f"LR: {self.optimizer.param_groups[0]['lr']:.6f} | "
                    f"Time: {epoch_time:.2f}s"
                )
                logger.info(log_msg)

                self._step_lr_scheduler(val_loss)

                if val_loss < self.best_val_loss:
                    self.best_val_loss = val_loss
                    self.patience_counter = 0
                    self.best_model_state = self._save_model_state()
                else:
                    self.patience_counter += 1
                    if self.patience_counter >= self.early_stopping_patience:
                        logger.info("Early stopping at epoch %s", epoch + 1)
                        break

                if self.device.type == "cuda" and not check_gpu_memory_safe(threshold_percent=95.0):
                    logger.warning("GPU memory usage high, clearing cache")
                    clear_gpu_memory(self.device)

            if self.best_model_state is not None:
                self._restore_model_state(self.best_model_state)
                logger.info("Restored best model with val_loss: %.4f", self.best_val_loss)

            total_training_time = time.perf_counter() - training_start
            logger.info(
                "Training completed in %.2fs (%.2fs/epoch)",
                total_training_time,
                total_training_time / epochs,
            )

            self.model.is_trained = True

            # 学术诚信：记录最终模型和训练摘要到 MLflow
            mlflow_log_model(self.model, artifact_path="model")
            mlflow_log_metrics(
                {
                    "best_val_loss": self.best_val_loss,
                    "total_training_time_s": total_training_time,
                    "total_epochs_run": self.current_epoch,
                }
            )

            # ADR-005 阶段 2：训练结束自动记录实验快照（best-effort，失败不中断）
            # 强制记录 git SHA + 数据版本 + 完整配置 + 指标 + 环境，支持一键复现
            if self.snapshot_store is not None:
                self._record_experiment_snapshot_sync(total_training_time=total_training_time)

            return self.training_history

    @staticmethod
    def _compute_r2(y_true: "np.ndarray", y_pred: "np.ndarray") -> float:
        """计算决定系数 R²"""
        y_true = y_true.flatten()
        y_pred = y_pred.flatten()
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
        if ss_tot == 0:
            return 0.0
        return float(1 - ss_res / ss_tot)

    def _step_lr_scheduler(self, val_loss: Optional[float] = None):
        """执行学习率调度"""
        if self.lr_scheduler is None:
            return

        if isinstance(self.lr_scheduler, torch.optim.lr_scheduler.ReduceLROnPlateau):
            self.lr_scheduler.step(val_loss)
        else:
            self.lr_scheduler.step()

    def _save_model_state(self) -> Dict[str, Any]:
        return {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
        }

    def _restore_model_state(self, state: Dict[str, Any]) -> None:
        """恢复模型状态"""
        self.model.load_state_dict(state["model_state_dict"])
        self.optimizer.load_state_dict(state["optimizer_state_dict"])

    # ADR-005 阶段 2：实验快照集成

    def _build_snapshot_config(self, total_training_time: float) -> Dict[str, Any]:
        """组装实验快照的 config 字段（含完整训练配置 + workflow_spec）."""
        config: Dict[str, Any] = {
            "hyperparams": {
                "learning_rate": self.learning_rate,
                "optimizer_type": self.optimizer_type,
                "loss_type": self.loss_type,
                "batch_size": self.batch_size,
                "epochs": self.epochs,
                "early_stopping_patience": self.early_stopping_patience,
                "gradient_clip_value": self.gradient_clip_value,
                "lr_scheduler_type": self.lr_scheduler_type,
                "lr_scheduler_params": self.lr_scheduler_params,
                "weight_decay": self.weight_decay,
                "use_amp": self.use_amp,
                "device": str(self.device),
            },
            "seed": self.seed,
            "total_training_time_s": float(total_training_time),
            "training_history": {k: list(v) if isinstance(v, list) else v for k, v in self.training_history.items()},
        }
        # workflow_spec 用于一键复现：reproduce 时反序列化为 WorkflowSpec 并启动新运行
        if self.workflow_spec is not None:
            config["workflow_spec"] = self.workflow_spec
        return config

    def _build_snapshot_metrics(self) -> Dict[str, float]:
        """组装实验快照的 metrics 字段（best_val_loss + final metrics）."""
        metrics: Dict[str, float] = {
            "best_val_loss": float(self.best_val_loss),
            "total_epochs_run": float(self.current_epoch),
        }
        # final 指标（若存在）
        if self.training_history.get("val_loss"):
            metrics["final_val_loss"] = float(self.training_history["val_loss"][-1])
        if self.training_history.get("train_loss"):
            metrics["final_train_loss"] = float(self.training_history["train_loss"][-1])
        if self.training_history.get("val_r2"):
            metrics["final_val_r2"] = float(self.training_history["val_r2"][-1])
        if self.training_history.get("train_r2"):
            metrics["final_train_r2"] = float(self.training_history["train_r2"][-1])
        return metrics

    def _record_experiment_snapshot_sync(self, total_training_time: float) -> None:
        """训练结束后 best-effort 同步记录实验快照.

        在 sync 上下文中调用 async snapshot_store.create()：
            - 若当前线程已有运行中的事件循环，调度为 task（非阻塞，可能未完成就退出）
            - 若无事件循环，用 asyncio.run 阻塞执行
        失败不中断训练流程，仅记录 warning。
        """
        if self.snapshot_store is None:
            return
        try:
            config = self._build_snapshot_config(total_training_time)
            metrics = self._build_snapshot_metrics()
            notes = (
                f"LNN training: seed={self.seed}, optimizer={self.optimizer_type}, "
                f"epochs_run={self.current_epoch}, best_val_loss={self.best_val_loss:.6f}"
            )
            coro = self.snapshot_store.create(
                config=config,
                dataset_versions=list(self.dataset_versions),
                model_uri=self.model_uri,
                metrics=metrics,
                created_by=self.created_by,
                notes=notes,
            )
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                # 已有事件循环运行：调度为 task，不阻塞
                future = asyncio.ensure_future(coro)

                # 注册回调记录 snapshot_id
                def _on_done(task: "asyncio.Task[Any]") -> None:
                    try:
                        result = task.result()
                        self._last_snapshot_id = getattr(result, "snapshot_id", None)
                        if self._last_snapshot_id:
                            logger.info(
                                "Experiment snapshot recorded: %s",
                                self._last_snapshot_id,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Snapshot creation task failed: %s", exc, exc_info=True)

                future.add_done_callback(_on_done)
            else:
                # 无运行中的事件循环：用 asyncio.run 阻塞执行
                result = asyncio.run(coro)
                self._last_snapshot_id = getattr(result, "snapshot_id", None)
                if self._last_snapshot_id:
                    logger.info("Experiment snapshot recorded: %s", self._last_snapshot_id)
        except Exception as e:  # noqa: BLE001
            # best-effort：快照记录失败不影响训练结果
            logger.warning(f"记录实验快照失败（不影响训练结果）: {e}", exc_info=True)

    async def record_experiment_snapshot(self, total_training_time: Optional[float] = None) -> Optional[str]:
        """显式记录实验快照（async 调用方使用）.

        Args:
            total_training_time: 训练总耗时（秒）。None 时用 current_epoch 估算。

        Returns:
            snapshot_id（成功）或 None（失败或未配置 snapshot_store）。
        """
        if self.snapshot_store is None:
            return None
        if total_training_time is None:
            total_training_time = float(self.current_epoch)
        try:
            config = self._build_snapshot_config(total_training_time)
            metrics = self._build_snapshot_metrics()
            notes = (
                f"LNN training (explicit): seed={self.seed}, "
                f"optimizer={self.optimizer_type}, "
                f"epochs_run={self.current_epoch}, "
                f"best_val_loss={self.best_val_loss:.6f}"
            )
            snap = await self.snapshot_store.create(
                config=config,
                dataset_versions=list(self.dataset_versions),
                model_uri=self.model_uri,
                metrics=metrics,
                created_by=self.created_by,
                notes=notes,
            )
            self._last_snapshot_id = getattr(snap, "snapshot_id", None)
            if self._last_snapshot_id:
                logger.info(
                    "Experiment snapshot recorded (async): %s",
                    self._last_snapshot_id,
                )
            return self._last_snapshot_id
        except Exception as e:  # noqa: BLE001
            logger.warning(f"显式记录实验快照失败: {e}", exc_info=True)
            return None

    def save_checkpoint(
        self,
        path: str,
        epoch: Optional[int] = None,
        metrics: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        实现检查点保存功能

        Args:
            path: 保存路径
            epoch: 当前epoch (如果为None则使用self.current_epoch)
            metrics: 性能指标
        """
        checkpoint = {
            "epoch": epoch if epoch is not None else self.current_epoch,
            "best_val_loss": self.best_val_loss,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "training_history": self.training_history,
            "model_config": {
                "optimizer_type": self.optimizer_type,
                "loss_type": self.loss_type,
                "learning_rate": self.learning_rate,
                "gradient_clip_value": self.gradient_clip_value,
                "lr_scheduler_type": self.lr_scheduler_type,
            },
            "metrics": metrics or {},
            "timestamp": datetime.now().isoformat(),
            "device": str(self.device),
            "use_amp": self.use_amp,
            "scaler_state_dict": self.scaler.state_dict() if self.scaler is not None else None,
            # 学术诚信修复：保存学习率调度器状态，避免恢复训练时调度器重置
            "lr_scheduler_state_dict": self.lr_scheduler.state_dict() if self.lr_scheduler is not None else None,
        }

        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        torch.save(checkpoint, path)
        logger.info("Checkpoint saved to %s", path)

    def load_checkpoint(self, path: str) -> Dict[str, Any]:
        """
        实现检查点加载功能

        Args:
            path: 检查点路径

        Returns:
            检查点信息
        """
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"训练检查点加载失败：找不到检查点文件 '{path}'。"
                "可能原因：文件路径错误或检查点已被删除/移动。"
                f"请确认：1) 路径 '{path}' 是否正确；"
                "2) 文件是否存在于预期位置；"
                "3) 如需重新训练，请调用 POST /api/v1/lnn/models/train 启动新训练任务。"
            )

        # 学术诚信修复：使用 weights_only=True 防止任意 pickle 反序列化（安全风险）
        # 检查点仅含 state_dict 和基础类型，weights_only=True 足够；
        # 对 PyTorch < 2.0 不支持该参数的情况，回退到默认加载。
        try:
            checkpoint = torch.load(path, map_location=self.device, weights_only=True)
        except TypeError:
            # PyTorch < 2.0 不支持 weights_only 参数，此处无法启用该安全选项
            # P2-AI-5: 风险权衡——旧版 PyTorch 无 weights_only 保护，反序列化风险由调用方
            # 保证 path 来源可信（仅加载本框架训练保存的检查点）来缓解
            checkpoint = torch.load(path, map_location=self.device)

        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.current_epoch = checkpoint.get("epoch", 0)
        self.best_val_loss = checkpoint.get("best_val_loss", float("inf"))
        self.training_history = checkpoint.get("training_history", self.training_history)

        if self.scaler is not None and checkpoint.get("scaler_state_dict") is not None:
            self.scaler.load_state_dict(checkpoint["scaler_state_dict"])

        # 学术诚信修复：恢复学习率调度器状态，避免调度器重置导致学习率跳变
        if self.lr_scheduler is not None and checkpoint.get("lr_scheduler_state_dict") is not None:
            self.lr_scheduler.load_state_dict(checkpoint["lr_scheduler_state_dict"])

        logger.info("Checkpoint loaded from %s", path)
        return checkpoint

    def export_torchscript(self, save_path: str, example_input: Optional[torch.Tensor] = None) -> str:
        """
        导出模型为TorchScript格式

        Args:
            save_path: 保存路径（.pt或.torchscript）
            example_input: 示例输入张量用于trace

        Returns:
            保存的文件路径
        """
        self.model.eval()

        if example_input is None:
            example_input = torch.randn(1, self.model.input_dim, device=self.device)

        if hasattr(self.model, "reset"):
            self.model.reset()

        # P2-AI-4: 使用 inference_mode 替代 no_grad，TorchScript trace 仅记录前向操作，无需 autograd 图
        with torch.inference_mode():
            scripted = torch.jit.trace(self.model, example_input, check_trace=False)

        save_dir = os.path.dirname(save_path)
        if save_dir and not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        scripted.save(save_path)
        logger.info("TorchScript model exported to %s", save_path)

        return save_path

    def get_training_summary(self) -> Dict[str, Any]:
        """获取训练摘要"""
        summary = {
            "total_epochs": self.current_epoch,
            "best_val_loss": self.best_val_loss,
            "final_train_loss": self.training_history["train_loss"][-1]
            if self.training_history["train_loss"]
            else None,
            "final_val_loss": self.training_history["val_loss"][-1] if self.training_history["val_loss"] else None,
            "final_train_accuracy": self.training_history["train_accuracy"][-1]
            if self.training_history["train_accuracy"]
            else None,
            "final_val_accuracy": self.training_history["val_accuracy"][-1]
            if self.training_history["val_accuracy"]
            else None,
            "optimizer": self.optimizer_type,
            "loss_function": self.loss_type,
            "device": str(self.device),
            "use_amp": self.use_amp,
        }

        if self.device.type == "cuda":
            gpu_index = self.device.index if self.device.index is not None else 0
            summary["gpu_name"] = torch.cuda.get_device_properties(gpu_index).name
            summary["gpu_max_memory_mb"] = round(torch.cuda.max_memory_allocated(gpu_index) / (1024**2), 2)

        return summary
