"""训练任务执行器辅助模块。

将 ``services.run_training_task_v2`` 中的数据加载、数据集构建、
训练器构造与训练循环逻辑拆分为独立函数,便于单测与维护。
入口函数签名保持不变,本模块仅承担内部编排细节。
"""

import time
import asyncio
import logging
from typing import Any
from collections.abc import Callable

import numpy as np

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

from app.ai.lnn.inference.registry import get_torch_model_class

# P0#3 解耦: 通过 research_bridge 延迟导入，替代直接 import research/。
# 桥接模块在 torch 缺失时返回 None，训练 API 将降级返回 503。
_HAS_TRAINING_STACK = False
LNNConfig: Any = None
LNNTrainer: Any = None
mlflow_start_run: Callable[..., Any] | None = None
mlflow_log_params: Callable[..., Any] | None = None
mlflow_log_metrics: Callable[..., Any] | None = None
mlflow_log_model: Callable[..., Any] | None = None
detect_device: Callable[..., Any] | None = None
get_optimal_batch_size: Callable[..., Any] | None = None
get_optimal_num_workers: Callable[..., Any] | None = None


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


logger = logging.getLogger(__name__)


async def _load_training_data(data_path: str):
    """加载训练数据 CSV,返回特征矩阵 X、标签 y 与输入维度。

    使用 ``asyncio.to_thread`` 将同步阻塞的 ``np.loadtxt`` 移至工作线程,
    避免大数据集加载期间冻结事件循环。文件不存在时抛出 FileNotFoundError。
    """

    def _load_csv_sync() -> np.ndarray:
        try:
            return np.loadtxt(data_path, delimiter=",", skiprows=1, dtype=float)
        except (ValueError, UnicodeDecodeError):
            # Fallback：手动解析非标准 CSV（含非数值单元格）
            with open(data_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            numeric_lines = []
            for line in lines[1:]:
                parts = line.strip().split(",")
                numeric_line = []
                for p in parts:
                    try:
                        numeric_line.append(float(p))
                    except ValueError as e:
                        logger.debug(
                            f"Skipping non-numeric value in row: {e}",
                            exc_info=True,
                        )
                if numeric_line:
                    numeric_lines.append(numeric_line)
            return np.array(numeric_lines)

    # np.loadtxt 是同步阻塞 I/O，在大数据集上会长时间冻结事件循环。
    # 通过 asyncio.to_thread 将其移至工作线程，期间事件循环可继续处理
    # SSE 心跳、取消信号等其他协程。
    try:
        data = await asyncio.to_thread(_load_csv_sync)
    except FileNotFoundError:
        raise FileNotFoundError(f"Data file not found: {data_path}")

    if data.ndim == 1:
        data = data.reshape(-1, 1)
    if data.shape[1] == 1:
        data = np.column_stack([data, data])

    X = data[:, :-1]
    y = data[:, -1]
    input_dim = X.shape[1]
    return X, y, input_dim


def _prepare_datasets(X, y, hyperparameters: dict, device_preference: str):
    """将原始数组转换为 DataLoader,并完成训练/验证集划分与设备检测。

    返回 ``(train_loader, val_loader, train_size, val_size, device, num_workers)``。
    训练/验证集按 80/20 划分;CUDA 设备下批量大小会被自动优化。
    """
    assert _HAS_TRAINING_STACK and detect_device is not None, "训练栈未初始化（torch 缺失）"
    assert get_optimal_batch_size is not None and get_optimal_num_workers is not None
    X_tensor = torch.FloatTensor(X)
    y_tensor = torch.FloatTensor(y)
    dataset = TensorDataset(X_tensor, y_tensor)
    train_size = int(0.8 * len(dataset))
    train_dataset, val_dataset = torch.utils.data.random_split(dataset, [train_size, len(dataset) - train_size])

    device, _ = detect_device(device_preference)
    batch_size = hyperparameters.get("batch_size", 32)
    if device.type == "cuda":
        batch_size = get_optimal_batch_size(device, batch_size)

    num_workers = get_optimal_num_workers()
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, num_workers=num_workers)

    val_size = len(dataset) - train_size
    return train_loader, val_loader, train_size, val_size, device, num_workers


def _build_trainer(model_name: str, input_dim: int, hyperparameters: dict, device, use_amp: bool):
    """根据模型名查找注册表,构造模型与训练器。

    返回 ``(model, trainer, hidden_size, entry)``。
    模型类与配置从注册表 entry 解析;若模型不存在或类型不支持,抛出 ValueError。
    """
    from app.api.v1.lnn.dependencies import registry_service

    lnn_registry = registry_service.model_registry
    entry = lnn_registry.registry.get(model_name)
    if not entry or entry.info is None:
        raise ValueError(f"Model '{model_name}' not found or missing info")

    model_class = get_torch_model_class(entry.info.model_type)
    if not model_class:
        raise ValueError(f"Unsupported model type: {entry.info.model_type}")

    hidden_size = min(256, max(64, input_dim * 2))
    config_obj = LNNConfig(
        input_size=input_dim,
        hidden_size=hidden_size,
        output_size=1,
        num_layers=2,
        dropout=0.1,
    )
    model = model_class(config_obj)

    trainer = LNNTrainer(
        model=model,
        learning_rate=hyperparameters.get("learning_rate", 0.001),
        optimizer_type=hyperparameters.get("optimizer", "adam"),
        loss_type="mse",
        batch_size=hyperparameters.get("batch_size", 32),
        epochs=hyperparameters.get("epochs", 100),
        device=str(device),
        use_amp=use_amp,
    )
    return model, trainer, hidden_size, entry


async def _execute_training_loop(
    trainer,
    train_loader,
    val_loader,
    model,
    device,
    epochs: int,
    cancel_evt: asyncio.Event,
    progress_updater: Callable,
    model_name: str,
    input_dim: int,
    hidden_size: int,
    entry,
    use_amp: bool,
    num_workers: int,
    train_size: int,
    val_size: int,
    hyperparameters: dict,
) -> dict:
    """执行训练循环,集成 MLflow 追踪并计算 R² 指标。

    包含 MLflow 实验追踪上下文、早停机制(patience=5)、每 epoch 指标记录、
    验证集 R² 计算与最终模型权重落盘。返回与原 ``run_training_task_v2``
    相同结构的结果字典。
    """
    assert mlflow_start_run is not None and mlflow_log_params is not None, "训练栈未初始化（mlflow 缺失）"
    assert mlflow_log_metrics is not None and mlflow_log_model is not None
    # 学术诚信：集成 MLflow 实验追踪，记录超参数和每个 epoch 的指标。
    # mlflow 为软依赖，未安装时 start_run 降级为 no-op 上下文，不影响训练流程。
    # 注意：此处的自定义训练循环不调用 trainer.fit()，因此 trainer.track_experiment
    # 不会触发，必须在此单独集成追踪。
    run_name = f"{model_name}_{int(time.time())}"
    with mlflow_start_run(run_name=run_name, experiment_name="lnn_api_training"):
        mlflow_log_params(
            {
                "model_name": model_name,
                "model_type": entry.info.model_type,
                "input_dim": input_dim,
                "hidden_size": hidden_size,
                "learning_rate": hyperparameters.get("learning_rate", 0.001),
                "optimizer": hyperparameters.get("optimizer", "adam"),
                "batch_size": hyperparameters.get("batch_size", 32),
                "epochs": epochs,
                "use_amp": use_amp,
                "device": str(device),
                "num_workers": num_workers,
                "train_size": train_size,
                "val_size": val_size,
                "loss_type": "mse",
                "patience": 5,
            }
        )

        start_time = time.perf_counter()
        history: dict[str, list[float]] = {"train_loss": [], "val_loss": []}
        best_val_loss = float("inf")
        patience = 5
        patience_counter = 0

        for epoch in range(1, epochs + 1):
            if cancel_evt.is_set():
                raise asyncio.CancelledError()

            train_loss, train_acc = trainer.train_epoch(train_loader)
            val_loss, val_acc = trainer.validate(val_loader)

            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)

            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
            else:
                patience_counter += 1

            # 每 epoch 记录训练/验证指标，供审稿人验证训练曲线
            mlflow_log_metrics(
                {
                    "train_loss": train_loss,
                    "val_loss": val_loss,
                    "train_accuracy": train_acc,
                    "val_accuracy": val_acc,
                },
                step=epoch,
            )

            progress = 15.0 + (epoch / epochs) * 80.0
            await progress_updater(
                progress,
                f"Training: epoch {epoch}/{epochs}, val_loss={val_loss:.4f}",
                {
                    "epoch": epoch,
                    "train_loss": round(train_loss, 4),
                    "val_loss": round(val_loss, 4),
                },
            )

            if patience_counter >= patience:
                logger.info("Early stopping at epoch %s", epoch)
                break

        training_time = time.perf_counter() - start_time
        final_val_loss = best_val_loss

        # 学术诚信修复 [S4]：基于验证集计算真实 R² 分数。
        # 原实现直接返回 ``"r2_score": None``，违反学术诚信——论文中
        # 需要报告 R² 指标时无法从训练服务获取真实值。此处对验证集
        # 执行前向推理，按标准公式 R² = 1 - SS_res/SS_tot 计算真实值。
        # 若验证集为空或方差为零（常数目标），返回 None 并附带原因。
        r2_score: float | None = None
        try:
            model.eval()
            y_true_list: list[float] = []
            y_pred_list: list[float] = []
            with torch.no_grad():
                for X_batch, y_batch in val_loader:
                    preds = model(X_batch.to(device))
                    # 兼容 (B, 1) 与 (B,) 两种输出形状
                    preds_np = preds.detach().cpu().numpy().reshape(-1)
                    y_np = y_batch.detach().cpu().numpy().reshape(-1)
                    y_pred_list.extend(preds_np.tolist())
                    y_true_list.extend(y_np.tolist())

            if y_true_list:
                y_true_arr = np.array(y_true_list, dtype=np.float64)
                y_pred_arr = np.array(y_pred_list, dtype=np.float64)
                ss_res = float(np.sum((y_true_arr - y_pred_arr) ** 2))
                y_mean = float(np.mean(y_true_arr))
                ss_tot = float(np.sum((y_true_arr - y_mean) ** 2))
                if ss_tot > 1e-12:
                    r2_score = 1.0 - ss_res / ss_tot
                else:
                    # 目标方差为零时 R² 无法定义
                    r2_score = None
                    logger.warning("R² 不可计算：验证集目标方差为零（ss_tot≈0），请检查数据是否为常数标签。")
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, AttributeError) as r2_err:
            # Q1 修复：原 `except Exception` 过宽，会吞掉 asyncio.CancelledError /
            # KeyboardInterrupt。收窄为可预期的数值/张量/设备异常。
            # RuntimeError 覆盖 PyTorch/CUDA 错误；ValueError/TypeError 覆盖
            # numpy 数组转换与形状问题；OSError 覆盖 CUDA 设备 IO 错误。
            # R² 计算失败不应阻断训练流程，但必须记录以便排查
            logger.warning(
                f"R² 计算失败，本次训练将返回 r2_score=None：{r2_err}",
                exc_info=True,
            )
            r2_score = None

        # 记录最终训练指标和模型权重，审稿人可复现论文报告的数值
        final_metrics: dict[str, float] = {
            "best_val_loss": final_val_loss,
            "training_time_s": training_time,
            "epochs_completed": float(epoch),
        }
        if r2_score is not None:
            final_metrics["r2_score"] = r2_score
        mlflow_log_metrics(final_metrics)
        mlflow_log_model(model, artifact_path="model")

        return {
            "status": "completed",
            "model_name": model_name,
            "epochs_completed": epoch,
            "final_val_loss": round(final_val_loss, 4),
            "training_time": round(training_time, 2),
            "metrics": {
                # 真实 R² 分数（基于验证集前向推理计算）
                "r2_score": round(r2_score, 4) if r2_score is not None else None,
                "loss": round(final_val_loss, 4),
                "training_time": round(training_time, 2),
                "epochs_completed": epoch,
            },
        }
