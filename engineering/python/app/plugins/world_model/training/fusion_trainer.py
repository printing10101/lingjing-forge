"""融合世界模型权重训练器（ADR-020 思路 1 P1）.

复用 ``app.ai.lnn.training`` 的 MLflow / 种子 / 设备 / AMP / 梯度裁剪 /
早停 / LR 调度器 / checkpoint 约定，但适配 ``WorldModelNet.forward`` 的
``unified_states=(geometry, dynamics)`` 输入契约——这是融合路径区别于
普通 LNN 训练器的核心差异。

训练流程
--------
1. ``set_global_seed`` 保证可复现（DataLoader / 权重初始化 / dropout）
2. MLflow run 记录融合专属超参（use_fusion / feature_dim / d_model / fused_dim）
   + 每轮 train_loss / val_loss / learning_rate
3. 每个 epoch：
   - 训练循环：``model(states=None, actions=..., horizon=...,
     unified_states=(geo, dyn))`` → MSE(predicted_trajectory, target)
   - AMP 混合精度 + 梯度裁剪 + optimizer.step
   - 验证循环：``torch.inference_mode`` + 同前向但不计算梯度
   - LR 调度器 step（cosine / step / reduce_on_plateau / exponential）
   - 早停检查（val_loss 连续 patience 轮不下降则停止）
4. checkpoint 保存：调用 ``build_canonical_weights_path(version, models_dir)``
   决定写入位置，格式与 ``LNNTrainer.save_checkpoint`` 对齐，便于
   ``TrajectoryPredictor.load_model`` 通过 ``torch.load + load_state_dict`` 加载。

工程边界
--------
- torch 不可用时实例化抛 RuntimeError（训练必须 torch）；导入本模块不抛错，
  方便 ``pytest.importorskip("torch")`` 自然跳过。
- horizon 在 ``train()`` 入参传入（与 ``WorldModelNet.forward`` 契约一致，
  每个 batch 的 horizon 必须一致，由 ``fusion_collate_fn`` 保证）。
- 不实现 R² / accuracy（融合路径是回归任务，仅记录 loss）。
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Union

logger = logging.getLogger(__name__)

# torch 为硬依赖（训练必须 torch），但导入期不抛错，由调用方在测试中
# 通过 pytest.importorskip("torch") 自然跳过。
try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader

    HAS_TORCH = True
except ImportError:  # pragma: no cover - 测试环境通过 importorskip 跳过
    HAS_TORCH = False
    torch = None
    nn = None
    DataLoader = None

from app.plugins.world_model.training.weights_resolver import (
    build_canonical_weights_path,
)

# 训练器常量（与 LNNTrainer 对齐，便于运维一致性）
DEFAULT_SGD_MOMENTUM = 0.9
DEFAULT_STEP_LR_STEP_SIZE = 30
DEFAULT_LR_DECAY_GAMMA = 0.1
DEFAULT_PLATEAU_PATIENCE = 10
DEFAULT_EXPONENTIAL_GAMMA = 0.95
DEFAULT_FUSION_EXPERIMENT_NAME = "WorldModel_Fusion"


class FusionTrainerError(RuntimeError):
    """融合训练器运行时错误（配置非法 / 前向契约违反等）。"""


class FusionWorldModelTrainer:
    """融合世界模型训练器.

    Parameters
    ----------
    model : WorldModelNet
        待训练的融合世界模型（``config.use_fusion=True``）。
    model_uri : str
        模型 URI，如 ``model://world_model/1.0.0``。仅用于 MLflow 标记
        和日志，不直接决定 checkpoint 路径（checkpoint 路径由
        ``save_checkpoint(version)`` 的 ``version`` 参数决定）。
    learning_rate : float
        初始学习率。
    optimizer_type : str
        优化器类型：``adam`` / ``adamw`` / ``sgd`` / ``rmsprop``。
    weight_decay : float
        权重衰减（L2 正则）。
    epochs : int
        最大训练轮数。
    early_stopping_patience : int
        早停耐心值（val_loss 连续 N 轮不下降则停止）。
    gradient_clip_value : Optional[float]
        梯度裁剪阈值（None 表示不裁剪）。
    lr_scheduler_type : str
        学习率调度器：``cosine`` / ``step`` / ``reduce_on_plateau`` /
        ``exponential`` / ``none``。
    lr_scheduler_params : Optional[dict[str, Any]]
        调度器参数（如 ``{"step_size": 30, "gamma": 0.1}``）。
    device : Union[str, torch.device]
        计算设备（``"cpu"`` / ``"cuda"`` / ``"auto"``）。
    use_amp : bool
        是否启用自动混合精度训练。
    seed : int
        随机种子（保证可复现）。
    track_experiment : bool
        是否启用 MLflow 实验追踪（未安装 mlflow 时自动降级为空操作）。
    experiment_name : str
        MLflow 实验名。
    models_dir : Optional[str]
        checkpoint 存储根目录。None 时使用
        ``weights_resolver.DEFAULT_MODELS_DIR``。
    save_every_epoch : bool
        是否每轮保存 checkpoint（覆盖同名文件）。False 时仅由
        ``save_checkpoint`` 显式调用时保存。

    Raises
    ------
    RuntimeError
        torch 不可用，或模型 ``config.use_fusion=False``。
    """

    def __init__(
        self,
        model: "nn.Module",
        model_uri: str,
        *,
        learning_rate: float = 1e-3,
        optimizer_type: str = "adamw",
        weight_decay: float = 1e-5,
        epochs: int = 100,
        early_stopping_patience: int = 10,
        gradient_clip_value: float | None = 1.0,
        lr_scheduler_type: str = "cosine",
        lr_scheduler_params: dict[str, Any] | None = None,
        device: Union[str, "torch.device"] = "auto",
        use_amp: bool = True,
        seed: int = 42,
        track_experiment: bool = True,
        experiment_name: str = DEFAULT_FUSION_EXPERIMENT_NAME,
        models_dir: str | None = None,
        save_every_epoch: bool = False,
    ) -> None:
        if not HAS_TORCH:
            raise RuntimeError(
                "FusionWorldModelTrainer 需要 torch，当前环境未安装。请安装 torch 后再执行融合权重训练。"
            )
        if not isinstance(model, nn.Module):
            raise TypeError(f"model 必须为 nn.Module，实际={type(model).__name__}")
        # 融合路径硬约束：模型必须启用 use_fusion
        model_config = getattr(model, "config", None)
        if model_config is None or not getattr(model_config, "use_fusion", False):
            raise FusionTrainerError(
                "模型 config.use_fusion=False，无法走融合训练路径。"
                "请用 WorldModelConfig(use_fusion=True, ...) 构造模型。"
            )

        self.model = model
        self.model_uri = model_uri
        self.learning_rate = learning_rate
        self.optimizer_type = optimizer_type
        self.weight_decay = weight_decay
        self.epochs = epochs
        self.early_stopping_patience = early_stopping_patience
        self.gradient_clip_value = gradient_clip_value
        self.lr_scheduler_type = lr_scheduler_type
        self.lr_scheduler_params = lr_scheduler_params or {}
        self.use_amp = use_amp
        self.seed = seed
        self.track_experiment = track_experiment
        self.experiment_name = experiment_name
        self.models_dir = models_dir
        self.save_every_epoch = save_every_epoch

        # 设备解析（auto cuda if available else cpu）
        if isinstance(device, str) and device == "auto":
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        else:
            self.device = torch.device(device)
        self.model = self.model.to(self.device)

        # 优化器
        self.optimizer = self._build_optimizer()

        # AMP scaler（CPU 时禁用）
        self.scaler: "torch.cuda.amp.GradScaler" | None = None
        if self.use_amp and self.device.type == "cuda":
            self.scaler = torch.cuda.amp.GradScaler()

        # LR 调度器
        self.lr_scheduler = self._build_lr_scheduler()

        # 损失函数：融合路径是回归任务，固定 MSE
        self.criterion = nn.MSELoss()

        # 训练状态
        self.current_epoch = 0
        self.best_val_loss = float("inf")
        self.epochs_without_improvement = 0
        self.training_history: dict[str, list[float]] = {
            "train_loss": [],
            "val_loss": [],
            "learning_rate": [],
        }

    # 内部构造器

    def _build_optimizer(self) -> "torch.optim.Optimizer":
        """构造优化器."""
        params = self.model.parameters()
        opt_type = self.optimizer_type.lower()
        if opt_type == "adam":
            return torch.optim.Adam(params, lr=self.learning_rate, weight_decay=self.weight_decay)
        if opt_type == "adamw":
            return torch.optim.AdamW(params, lr=self.learning_rate, weight_decay=self.weight_decay)
        if opt_type == "sgd":
            return torch.optim.SGD(
                params,
                lr=self.learning_rate,
                momentum=DEFAULT_SGD_MOMENTUM,
                weight_decay=self.weight_decay,
            )
        if opt_type == "rmsprop":
            return torch.optim.RMSprop(params, lr=self.learning_rate, weight_decay=self.weight_decay)
        raise FusionTrainerError(f"不支持的优化器类型: {self.optimizer_type}（支持 adam/adamw/sgd/rmsprop）")

    def _build_lr_scheduler(self) -> Any | None:
        """构造学习率调度器."""
        sch_type = self.lr_scheduler_type.lower()
        params = self.lr_scheduler_params
        if sch_type == "none":
            return None
        if sch_type == "cosine":
            return torch.optim.lr_scheduler.CosineAnnealingLR(
                self.optimizer,
                T_max=params.get("T_max", self.epochs),
                eta_min=params.get("eta_min", 0.0),
            )
        if sch_type == "step":
            return torch.optim.lr_scheduler.StepLR(
                self.optimizer,
                step_size=params.get("step_size", DEFAULT_STEP_LR_STEP_SIZE),
                gamma=params.get("gamma", DEFAULT_LR_DECAY_GAMMA),
            )
        if sch_type == "reduce_on_plateau":
            return torch.optim.lr_scheduler.ReduceLROnPlateau(
                self.optimizer,
                mode="min",
                factor=params.get("factor", 0.5),
                patience=params.get("patience", DEFAULT_PLATEAU_PATIENCE),
            )
        if sch_type == "exponential":
            return torch.optim.lr_scheduler.ExponentialLR(
                self.optimizer,
                gamma=params.get("gamma", DEFAULT_EXPONENTIAL_GAMMA),
            )
        raise FusionTrainerError(
            f"不支持的 LR 调度器类型: {self.lr_scheduler_type}（支持 cosine/step/reduce_on_plateau/exponential/none）"
        )

    # 训练循环

    def train(
        self,
        train_loader: "DataLoader",
        val_loader: "DataLoader",
        horizon: int,
    ) -> dict[str, list[float]]:
        """执行完整训练流程.

        Parameters
        ----------
        train_loader : DataLoader
            训练数据加载器，``collate_fn`` 必须为 ``fusion_collate_fn``
            （返回 ``(geometry, dynamics, actions, target)`` 四元组）。
        val_loader : DataLoader
            验证数据加载器，同上。
        horizon : int
            预测步长。所有 batch 的 horizon 必须一致（由
            ``fusion_collate_fn`` 保证）。

        Returns
        -------
        Dict[str, List[float]]
            训练历史，含 ``train_loss`` / ``val_loss`` / ``learning_rate``。
        """
        if horizon <= 0:
            raise FusionTrainerError(f"horizon 必须为正数: {horizon}")

        # 延迟导入：app.ai.lnn.training 包初始化会触发 dataset.py 的硬 torch
        # 依赖，放在模块级会导致无 torch 环境下整个 fusion_trainer 不可导入
        # （FusionTrainerError / _extract_version_from_uri 等 torch-free 符号
        # 也无法被测试验证）。此处 train() 本就需要 torch，延迟导入不影响契约。
        # P0#3 解耦: 通过 research_bridge 延迟导入
        from app.ai.lnn._research_bridge import (
            get_set_global_seed,
            get_mlflow_start_run,
            get_mlflow_log_params,
            get_mlflow_log_metrics,
        )

        set_global_seed: Any = get_set_global_seed()
        mlflow_start_run: Any = get_mlflow_start_run()
        mlflow_log_params: Any = get_mlflow_log_params()
        mlflow_log_metrics: Any = get_mlflow_log_metrics()
        if set_global_seed is None:
            import random
            import numpy as np

            def set_global_seed(seed: int = 42) -> None:
                random.seed(seed)
                np.random.seed(seed)

        # P0#3 解耦兜底：research 包不可用（无 torch/工程侧）时 mlflow 系列为 None，
        # 必须提供 no-op fallback，否则训练流程在工程侧崩溃（NoneType not callable）。
        if mlflow_start_run is None:
            from contextlib import nullcontext as _nullcontext

            def mlflow_start_run(*args: Any, **kwargs: Any) -> Any:
                return _nullcontext()

        if mlflow_log_params is None:

            def mlflow_log_params(*args: Any, **kwargs: Any) -> None:
                return None

        if mlflow_log_metrics is None:

            def mlflow_log_metrics(*args: Any, **kwargs: Any) -> None:
                return None

        # 学术诚信：训练开始前设置随机种子
        set_global_seed(self.seed)

        run_name = f"fusion_seed{self.seed}_{self.optimizer_type}_lr{self.learning_rate}"
        if self.track_experiment:
            tracking_ctx = mlflow_start_run(
                run_name=run_name,
                experiment_name=self.experiment_name,
            )
        else:
            tracking_ctx = nullcontext()

        with tracking_ctx:
            # 记录超参（含融合专属字段）
            mlflow_log_params(self._collect_hyperparams(horizon))

            train_size = len(train_loader.dataset)
            val_size = len(val_loader.dataset)
            logger.info(
                "融合训练启动: train=%d val=%d horizon=%d epochs=%d device=%s amp=%s",
                train_size,
                val_size,
                horizon,
                self.epochs,
                self.device,
                bool(self.scaler),
            )

            training_start = time.perf_counter()

            for epoch in range(self.epochs):
                self.current_epoch = epoch + 1
                epoch_start = time.perf_counter()

                train_loss = self._train_epoch(train_loader, horizon)
                val_loss = self._validate(val_loader, horizon)
                epoch_time = time.perf_counter() - epoch_start

                # 记录历史
                self.training_history["train_loss"].append(train_loss)
                self.training_history["val_loss"].append(val_loss)
                self.training_history["learning_rate"].append(self.optimizer.param_groups[0]["lr"])

                # MLflow 指标记录
                mlflow_log_metrics(
                    {
                        "train_loss": train_loss,
                        "val_loss": val_loss,
                        "learning_rate": self.optimizer.param_groups[0]["lr"],
                    },
                    step=epoch + 1,
                )

                logger.info(
                    "epoch %d/%d train_loss=%.6f val_loss=%.6f lr=%.2e time=%.2fs",
                    epoch + 1,
                    self.epochs,
                    train_loss,
                    val_loss,
                    self.optimizer.param_groups[0]["lr"],
                    epoch_time,
                )

                # LR 调度器 step
                if self.lr_scheduler is not None:
                    if isinstance(
                        self.lr_scheduler,
                        torch.optim.lr_scheduler.ReduceLROnPlateau,
                    ):
                        self.lr_scheduler.step(val_loss)
                    else:
                        self.lr_scheduler.step()

                # 早停检查
                if val_loss < self.best_val_loss - 1e-9:
                    self.best_val_loss = val_loss
                    self.epochs_without_improvement = 0
                else:
                    self.epochs_without_improvement += 1
                    if self.epochs_without_improvement >= self.early_stopping_patience:
                        logger.info(
                            "早停触发：连续 %d 轮 val_loss 未下降，停止训练",
                            self.epochs_without_improvement,
                        )
                        break

                # per-epoch checkpoint（可选）
                if self.save_every_epoch:
                    try:
                        # 用 model_uri 的 version 部分作为 checkpoint 版本
                        version = self._extract_version_from_uri(self.model_uri)
                        self.save_checkpoint(
                            version=version,
                            epoch=self.current_epoch,
                            metrics={"train_loss": train_loss, "val_loss": val_loss},
                        )
                    except (OSError, RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
                        # Q1 修复：收窄为可预期的 IO/序列化/状态异常。
                        # OSError 覆盖磁盘满/权限问题；RuntimeError 覆盖 PyTorch
                        # state_dict 序列化失败；其他覆盖 metrics 字典访问异常。
                        logger.warning(
                            "per-epoch checkpoint 保存失败（不影响训练）: %s",
                            exc,
                        )

            total_time = time.perf_counter() - training_start
            logger.info(
                "融合训练结束: total_epochs=%d best_val_loss=%.6f time=%.2fs",
                self.current_epoch,
                self.best_val_loss,
                total_time,
            )

        return self.training_history

    def _train_epoch(
        self,
        train_loader: "DataLoader",
        horizon: int,
    ) -> float:
        """单 epoch 训练，返回平均训练损失."""
        self.model.train()
        total_loss = 0.0
        total_samples = 0

        for geometry, dynamics, actions, target in train_loader:
            geometry = geometry.to(self.device)
            dynamics = dynamics.to(self.device)
            actions = actions.to(self.device)
            target = target.to(self.device)

            self.optimizer.zero_grad()

            if self.scaler is not None:
                with torch.cuda.amp.autocast():
                    output = self.model(
                        states=None,
                        actions=actions,
                        horizon=horizon,
                        unified_states=(geometry, dynamics),
                    )
                    pred = output["predicted_trajectory"]
                    loss = self.criterion(pred, target)

                self.scaler.scale(loss).backward()

                if self.gradient_clip_value is not None:
                    self.scaler.unscale_(self.optimizer)
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)

                self.scaler.step(self.optimizer)
                self.scaler.update()
            else:
                output = self.model(
                    states=None,
                    actions=actions,
                    horizon=horizon,
                    unified_states=(geometry, dynamics),
                )
                pred = output["predicted_trajectory"]
                loss = self.criterion(pred, target)

                loss.backward()

                if self.gradient_clip_value is not None:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.gradient_clip_value)

                self.optimizer.step()

            batch_size = geometry.size(0)
            total_loss += loss.item() * batch_size
            total_samples += batch_size

        if total_samples == 0:
            raise FusionTrainerError("训练数据为空（0 个样本）")
        return total_loss / total_samples

    def _validate(
        self,
        val_loader: "DataLoader",
        horizon: int,
    ) -> float:
        """验证循环，返回平均验证损失."""
        self.model.eval()
        total_loss = 0.0
        total_samples = 0

        # P2-AI-4: 使用 inference_mode 替代 no_grad，验证阶段无需 autograd 图
        with torch.inference_mode():
            for geometry, dynamics, actions, target in val_loader:
                geometry = geometry.to(self.device)
                dynamics = dynamics.to(self.device)
                actions = actions.to(self.device)
                target = target.to(self.device)

                if self.scaler is not None:
                    with torch.cuda.amp.autocast():
                        output = self.model(
                            states=None,
                            actions=actions,
                            horizon=horizon,
                            unified_states=(geometry, dynamics),
                        )
                        pred = output["predicted_trajectory"]
                        loss = self.criterion(pred, target)
                else:
                    output = self.model(
                        states=None,
                        actions=actions,
                        horizon=horizon,
                        unified_states=(geometry, dynamics),
                    )
                    pred = output["predicted_trajectory"]
                    loss = self.criterion(pred, target)

                batch_size = geometry.size(0)
                total_loss += loss.item() * batch_size
                total_samples += batch_size

        if total_samples == 0:
            raise FusionTrainerError("验证数据为空（0 个样本）")
        return total_loss / total_samples

    # checkpoint 保存 / 加载

    def save_checkpoint(
        self,
        version: str,
        epoch: int | None = None,
        metrics: dict[str, float] | None = None,
    ) -> str:
        """保存 checkpoint 到规范路径.

        使用 ``build_canonical_weights_path(version, models_dir)`` 决定
        写入位置，格式与 ``LNNTrainer.save_checkpoint`` 对齐，便于
        ``TrajectoryPredictor.load_model`` 加载。

        Parameters
        ----------
        version : str
            模型版本字符串（如 ``1.0.0``、``fusion-v1-20260715``）。
            仅允许 ``[A-Za-z0-9_.-]``，防止路径穿越。
        epoch : Optional[int]
            当前 epoch。None 时使用 ``self.current_epoch``。
        metrics : Optional[dict[str, float]]
            附加指标（如 ``{"val_loss": 0.01}``）。

        Returns
        -------
        str
            checkpoint 文件绝对路径。
        """
        path = build_canonical_weights_path(version, self.models_dir)
        checkpoint = {
            "epoch": epoch if epoch is not None else self.current_epoch,
            "best_val_loss": self.best_val_loss,
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "training_history": self.training_history,
            "model_config": self._serialize_model_config(),
            "metrics": metrics or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device": str(self.device),
            "use_amp": self.use_amp,
            "scaler_state_dict": self.scaler.state_dict() if self.scaler is not None else None,
            "lr_scheduler_state_dict": self.lr_scheduler.state_dict() if self.lr_scheduler is not None else None,
            # 融合训练专属字段，便于加载时校验
            "model_uri": self.model_uri,
            "trainer_type": "FusionWorldModelTrainer",
        }

        os.makedirs(
            os.path.dirname(path) if os.path.dirname(path) else ".",
            exist_ok=True,
        )
        torch.save(checkpoint, path)
        logger.info("融合模型 checkpoint 已保存: %s", path)
        return path

    def load_checkpoint(self, path: str) -> dict[str, Any]:
        """加载 checkpoint.

        Parameters
        ----------
        path : str
            checkpoint 文件路径。

        Returns
        -------
        Dict[str, Any]
            checkpoint 内容。

        Raises
        ------
        FileNotFoundError
            文件不存在。
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"融合模型 checkpoint 加载失败：找不到文件 '{path}'。请确认路径正确或重新训练。")

        # 安全加载：优先 weights_only=True
        try:
            checkpoint = torch.load(path, map_location=self.device, weights_only=True)
        except TypeError:
            # PyTorch < 2.0 不支持 weights_only，回退默认加载
            checkpoint = torch.load(path, map_location=self.device)

        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.current_epoch = checkpoint.get("epoch", 0)
        self.best_val_loss = checkpoint.get("best_val_loss", float("inf"))
        self.training_history = checkpoint.get("training_history", self.training_history)

        if self.scaler is not None and checkpoint.get("scaler_state_dict") is not None:
            self.scaler.load_state_dict(checkpoint["scaler_state_dict"])

        if self.lr_scheduler is not None and checkpoint.get("lr_scheduler_state_dict") is not None:
            self.lr_scheduler.load_state_dict(checkpoint["lr_scheduler_state_dict"])

        logger.info("融合模型 checkpoint 已加载: %s", path)
        return checkpoint

    # 辅助方法

    def _collect_hyperparams(self, horizon: int) -> dict[str, Any]:
        """收集超参（含融合专属字段）用于 MLflow 记录."""
        cfg = self.model.config
        params: dict[str, Any] = {
            "model_uri": self.model_uri,
            "learning_rate": self.learning_rate,
            "optimizer_type": self.optimizer_type,
            "weight_decay": self.weight_decay,
            "epochs": self.epochs,
            "seed": self.seed,
            "horizon": horizon,
            "early_stopping_patience": self.early_stopping_patience,
            "gradient_clip_value": self.gradient_clip_value,
            "lr_scheduler_type": self.lr_scheduler_type,
            "lr_scheduler_params": str(self.lr_scheduler_params),
            "device": str(self.device),
            "use_amp": self.use_amp,
            # 融合专属超参
            "use_fusion": cfg.use_fusion,
            "state_dim": cfg.state_dim,
            "action_dim": cfg.action_dim,
            "hidden_dim": cfg.hidden_dim,
            "num_lstm_layers": cfg.num_lstm_layers,
            "num_ltc_layers": cfg.num_ltc_layers,
            "feature_dim": cfg.feature_dim,
            "d_model": cfg.d_model,
            "fused_dim": cfg.fused_dim,
            "dropout": cfg.dropout,
        }
        return params

    def _serialize_model_config(self) -> dict[str, Any]:
        """序列化模型配置用于 checkpoint（恢复训练时重建模型）."""
        cfg = self.model.config
        if hasattr(cfg, "to_dict"):
            return cfg.to_dict()
        return {k: getattr(cfg, k) for k in dir(cfg) if not k.startswith("_")}

    @staticmethod
    def _extract_version_from_uri(model_uri: str) -> str:
        """从 ``model://world_model/<version>`` 提取 version 部分.

        若 URI 格式不匹配，回退到时间戳保证可保存。
        """
        prefix = "model://world_model/"
        if isinstance(model_uri, str) and model_uri.startswith(prefix):
            version = model_uri[len(prefix) :]
            # 仅保留安全字符（与 weights_resolver._VERSION_PATTERN 一致）
            safe = "".join(c for c in version if c.isalnum() or c in "._-")
            if safe:
                return safe
        # 回退：时间戳版本（保证 save_every_epoch 不会因 URI 异常而失败）
        return f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    def get_training_summary(self) -> dict[str, Any]:
        """获取训练摘要."""
        return {
            "total_epochs": self.current_epoch,
            "best_val_loss": self.best_val_loss,
            "final_train_loss": self.training_history["train_loss"][-1]
            if self.training_history["train_loss"]
            else None,
            "final_val_loss": self.training_history["val_loss"][-1] if self.training_history["val_loss"] else None,
            "optimizer": self.optimizer_type,
            "loss_function": "mse",
            "device": str(self.device),
            "use_amp": self.use_amp,
            "model_uri": self.model_uri,
        }


__all__ = [
    "DEFAULT_FUSION_EXPERIMENT_NAME",
    "FusionTrainerError",
    "FusionWorldModelTrainer",
]
