"""LNN (Liquid Neural Network) 模型基类接口。

定义所有 LNN 模型实现的统一接口：build、forward、predict、训练与评估。

工程运行时版（W 引擎验证修复）：V2.7 解耦时随 cfc/ltc/hybrid 一起被迁往
research/。train() 内对 research 侧 ``training.reproducibility`` 的依赖
已加防护——运行时以推理为主，训练请在 research 环境执行。
"""

from abc import ABC, abstractmethod
from typing import Any
import json
import numpy as np
import time
import logging

logger = logging.getLogger(__name__)


# AdamW 优化器默认权重衰减系数（L2 正则化）
DEFAULT_WEIGHT_DECAY: float = 1e-5

#: npz 权重文件格式版本：
#: - v1 = 仅元数据（历史缺陷：不含任何参数数组，加载后权重仍为初始化值）
#: - v2 = 元数据 + ``param.*`` 参数数组 + config_json，save/load 完整往返
NPZ_FORMAT_VERSION = 2

#: npz 中参数数组的键前缀（与元数据键区分）
_PARAM_PREFIX = "param."


def _collect_indexed_arrays(arrays: dict[str, np.ndarray], prefix: str) -> list[np.ndarray]:
    """按 ``prefix.0, prefix.1, ...`` 收集连续索引的参数数组。

    检测索引空洞（如 0/2 缺 1）——空洞意味着文件损坏，静默截断会加载出
    错位的权重，必须显式失败。

    Raises:
        ValueError: 索引不连续时抛出。
    """
    out: list[np.ndarray] = []
    i = 0
    while f"{prefix}.{i}" in arrays:
        out.append(np.asarray(arrays[f"{prefix}.{i}"]))
        i += 1
    total = sum(1 for k in arrays if k.startswith(prefix + "."))
    if total != len(out):
        raise ValueError(f"权重文件损坏：{prefix}.* 参数数组索引不连续（共 {total} 个，连续段仅 {len(out)}）。")
    return out


class BaseLNNModel(ABC):
    """LNN 模型抽象基类。

    子类需实现 build()、forward()、predict()、_train_step()、_validate()。

    Attributes:
        model_name: 模型名称。
        input_dim: 输入维度。
        output_dim: 输出维度。
        device: 计算设备 ('cpu', 'cuda')。
        is_trained: 是否已训练。
        training_history: 训练历史（loss/accuracy）。
        config: 其他配置参数。
    """

    def __init__(
        self,
        model_name: str,
        input_dim: int,
        output_dim: int,
        device: str = "cpu",
        **kwargs,
    ):
        """
        初始化LNN基类

        Args:
            model_name: 模型名称
            input_dim: 输入维度
            output_dim: 输出维度
            device: 计算设备 ('cpu', 'cuda')
            **kwargs: 其他模型参数
        """
        self.model_name = model_name
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.device = device
        self.is_trained = False
        self.training_history: dict[str, list[float]] = {
            "loss": [],
            "accuracy": [],
            "val_loss": [],
            "val_accuracy": [],
        }
        self.config = kwargs

    @abstractmethod
    def build(self) -> None:
        """构建模型结构，子类必须实现"""
        pass

    @abstractmethod
    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        前向传播

        Args:
            x: 输入数据 (batch_size, input_dim)

        Returns:
            模型输出 (batch_size, output_dim)
        """
        pass

    @abstractmethod
    def predict(self, x: np.ndarray) -> np.ndarray:
        """
        预测接口

        Args:
            x: 输入数据

        Returns:
            预测结果
        """
        pass

    def predict_with_confidence(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """
        带置信度的预测

        Args:
            x: 输入数据

        Returns:
            (predictions, confidences) 预测结果和对应的置信度
        """
        predictions = self.predict(x)
        confidences = self._calculate_confidence(predictions)
        return predictions, confidences

    def _calculate_confidence(self, predictions: np.ndarray) -> np.ndarray:
        """
        基于模型输出计算置信度

        对于分类任务（多维输出）：使用softmax归一化后的最大概率作为置信度
        对于回归任务（单值输出）：返回默认置信度0.8

        Args:
            predictions: 模型原始输出

        Returns:
            置信度数组 (0-1之间)
        """
        if predictions.ndim == 1:
            predictions = predictions.reshape(1, -1)

        # 回归任务：单值输出，使用默认置信度
        if predictions.shape[1] == 1:
            return np.ones(predictions.shape[0]) * 0.8

        # 分类任务：多维输出，使用softmax归一化后的最大概率
        exp_preds = np.exp(predictions - np.max(predictions, axis=1, keepdims=True))
        softmax_preds = exp_preds / np.sum(exp_preds, axis=1, keepdims=True)

        # 取最大概率作为置信度
        confidences = np.max(softmax_preds, axis=1)
        return confidences

    def calculate_uncertainty(self, predictions: np.ndarray) -> dict[str, float]:
        """
        计算预测不确定性

        Args:
            predictions: 模型预测结果

        Returns:
            不确定性指标字典
        """
        if predictions.ndim == 1:
            predictions = predictions.reshape(1, -1)

        # 计算熵
        exp_preds = np.exp(predictions - np.max(predictions, axis=1, keepdims=True))
        softmax_preds = exp_preds / np.sum(exp_preds, axis=1, keepdims=True)

        entropy = -np.sum(softmax_preds * np.log(softmax_preds + 1e-10), axis=1)
        max_entropy = np.log(predictions.shape[1])
        normalized_entropy = entropy / (max_entropy + 1e-10)

        return {
            "entropy": float(np.mean(entropy)),
            "normalized_entropy": float(np.mean(normalized_entropy)),
            "confidence": float(1 - np.mean(normalized_entropy)),
            "variance": float(np.var(predictions)),
        }

    def train(
        self,
        train_data: np.ndarray,
        train_labels: np.ndarray,
        val_data: np.ndarray | None = None,
        val_labels: np.ndarray | None = None,
        epochs: int = 100,
        batch_size: int = 32,
        learning_rate: float = 0.001,
        seed: int = 42,
        **kwargs,
    ) -> dict[str, list[float]]:
        """
        训练模型。

        Args:
            train_data: 训练数据
            train_labels: 训练标签
            val_data: 验证数据
            val_labels: 验证标签
            epochs: 训练轮数
            batch_size: 批次大小
            learning_rate: 学习率
            seed: 随机种子（默认42）

        Returns:
            训练历史记录
        """
        # 设置全局随机种子确保可复现性
        # 延迟导入避免 models training 循环依赖
        # 工程运行时版：research 侧 training 包不在工程依赖内，种子设置降级为可选
        try:
            from training.reproducibility import set_global_seed

            set_global_seed(seed)
        except ImportError:
            logger.debug("training.reproducibility 不可用，跳过全局种子设置（运行时推理版）")

        self.build()

        try:
            import torch
            from torch.utils.data import DataLoader, TensorDataset

            # 转换为PyTorch张量
            train_X = torch.FloatTensor(train_data)
            train_y = torch.FloatTensor(train_labels)
            if train_y.ndim == 1:
                train_y = train_y.unsqueeze(1)

            train_dataset = TensorDataset(train_X, train_y)
            train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)

            val_loader = None
            if val_data is not None and val_labels is not None:
                val_X = torch.FloatTensor(val_data)
                val_y = torch.FloatTensor(val_labels)
                if val_y.ndim == 1:
                    val_y = val_y.unsqueeze(1)
                val_dataset = TensorDataset(val_X, val_y)
                val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

            # 转换为PyTorch模型进行训练
            torch_model = self.to_torch(device=self.device)
            torch_model.train()

            optimizer = torch.optim.AdamW(
                torch_model.parameters(),
                lr=learning_rate,
                weight_decay=DEFAULT_WEIGHT_DECAY,
            )
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)
            criterion = torch.nn.MSELoss()

            best_val_loss = float("inf")
            patience_counter = 0
            early_stopping_patience = kwargs.get("early_stopping_patience", 10)

            for epoch in range(epochs):
                # 训练阶段
                torch_model.train()
                train_loss = 0.0
                for batch_X, batch_y in train_loader:
                    optimizer.zero_grad()
                    # LTC/CFC 时序模型：hidden_state 跨 batch 持久化会把计算图串联
                    # 导致二次 backward——每 batch 重置，图不跨 batch
                    if hasattr(torch_model, "hidden_state"):
                        torch_model.hidden_state = None
                    outputs = torch_model(batch_X)
                    if isinstance(outputs, tuple):
                        outputs = outputs[0]
                    loss = criterion(outputs, batch_y)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(torch_model.parameters(), 1.0)
                    optimizer.step()
                    train_loss += loss.item() * batch_X.size(0)

                train_loss /= len(train_dataset)
                self.training_history["loss"].append(train_loss)

                scheduler.step()

                # 验证阶段
                if val_loader is not None:
                    torch_model.eval()
                    val_loss = 0.0
                    # P2-AI-4: 使用 inference_mode 替代 no_grad，推理更高效（不记录 autograd 图）
                    with torch.no_grad():
                        for batch_X, batch_y in val_loader:
                            outputs = torch_model(batch_X)
                            if isinstance(outputs, tuple):
                                outputs = outputs[0]
                            loss = criterion(outputs, batch_y)
                            val_loss += loss.item() * batch_X.size(0)
                    val_loss /= len(val_loader.dataset)
                    self.training_history["val_loss"].append(val_loss)

                    # 早停检查
                    if val_loss < best_val_loss:
                        best_val_loss = val_loss
                        patience_counter = 0
                        # 保存最佳模型权重
                        self._best_torch_state = {k: v.cpu().clone() for k, v in torch_model.state_dict().items()}
                    else:
                        patience_counter += 1
                        if patience_counter >= early_stopping_patience:
                            logger.info(
                                "Early stopping at epoch %s/%s (val_loss=%.6f)",
                                epoch + 1,
                                epochs,
                                val_loss,
                            )
                            break

            # 恢复最佳模型状态并同步回NumPy权重
            if hasattr(self, "_best_torch_state") and self._best_torch_state is not None:
                torch_model.load_state_dict(self._best_torch_state)
                self._sync_from_torch(torch_model)

            self.is_trained = True
            return self.training_history

        except ImportError:
            logger.warning(
                "PyTorch not available, falling back to NumPy-based training. "
                "Install PyTorch (pip install torch) for proper gradient computation."
            )
            return self._train_numpy_fallback(
                train_data,
                train_labels,
                val_data,
                val_labels,
                epochs,
                batch_size,
                learning_rate,
            )

    def _train_numpy_fallback(
        self,
        train_data: np.ndarray,
        train_labels: np.ndarray,
        val_data: np.ndarray | None = None,
        val_labels: np.ndarray | None = None,
        epochs: int = 100,
        batch_size: int = 32,
        learning_rate: float = 0.001,
    ) -> dict[str, list[float]]:
        """NumPy训练回退方案（仅当PyTorch不可用时使用）"""
        for epoch in range(epochs):
            epoch_loss = self._train_step(train_data, train_labels, batch_size, learning_rate)
            self.training_history["loss"].append(epoch_loss)

            if val_data is not None and val_labels is not None:
                val_loss = self._validate(val_data, val_labels)
                self.training_history["val_loss"].append(val_loss)

        self.is_trained = True
        return self.training_history

    def _sync_from_torch(self, torch_model) -> None:
        """从PyTorch模型同步权重回NumPy模型（子类应重写此方法）"""
        pass

    @abstractmethod
    def _train_step(
        self,
        data: np.ndarray,
        labels: np.ndarray,
        batch_size: int,
        learning_rate: float,
    ) -> float:
        """
        单步训练

        Args:
            data: 训练数据
            labels: 训练标签
            batch_size: 批次大小
            learning_rate: 学习率

        Returns:
            当前step的loss
        """
        pass

    @abstractmethod
    def _validate(self, val_data: np.ndarray, val_labels: np.ndarray) -> float:
        """
        验证模型

        Args:
            val_data: 验证数据
            val_labels: 验证标签

        Returns:
            验证损失
        """
        pass

    def evaluate(
        self,
        test_data: np.ndarray,
        test_labels: np.ndarray,
        metrics: list[str] | None = None,
    ) -> dict[str, float]:
        """
        评估模型性能

        Args:
            test_data: 测试数据
            test_labels: 测试标签
            metrics: 评估指标列表

        Returns:
            评估结果字典
        """
        if not self.is_trained:
            raise RuntimeError("模型尚未训练，请先调用 train() 或加载已训练的检查点。")

        predictions = self.predict(test_data)

        results = {}

        if metrics is None or "accuracy" in metrics:
            results["accuracy"] = self._compute_accuracy(test_labels, predictions)

        if metrics is None or "precision" in metrics:
            results["precision"] = self._compute_precision(test_labels, predictions)

        if metrics is None or "recall" in metrics:
            results["recall"] = self._compute_recall(test_labels, predictions)

        if metrics is None or "loss" in metrics:
            results["loss"] = self._validate(test_data, test_labels)

        return results

    def _compute_accuracy(self, labels: np.ndarray, predictions: np.ndarray) -> float:
        """计算准确率"""
        if predictions.ndim > 1:
            pred_classes = np.argmax(predictions, axis=1)
        else:
            pred_classes = (predictions > 0.5).astype(int)

        if labels.ndim > 1:
            true_classes = np.argmax(labels, axis=1)
        else:
            true_classes = labels.astype(int)

        return float(np.mean(pred_classes == true_classes))

    def _compute_precision(self, labels: np.ndarray, predictions: np.ndarray) -> float:
        """计算精确率"""
        if predictions.ndim > 1:
            pred_classes = np.argmax(predictions, axis=1)
        else:
            pred_classes = (predictions > 0.5).astype(int)

        if labels.ndim > 1:
            true_classes = np.argmax(labels, axis=1)
        else:
            true_classes = labels.astype(int)

        tp = np.sum((pred_classes == 1) & (true_classes == 1))
        fp = np.sum((pred_classes == 1) & (true_classes == 0))

        if tp + fp == 0:
            return 0.0
        return float(tp / (tp + fp))

    def _compute_recall(self, labels: np.ndarray, predictions: np.ndarray) -> float:
        """计算召回率"""
        if predictions.ndim > 1:
            pred_classes = np.argmax(predictions, axis=1)
        else:
            pred_classes = (predictions > 0.5).astype(int)

        if labels.ndim > 1:
            true_classes = np.argmax(labels, axis=1)
        else:
            true_classes = labels.astype(int)

        tp = np.sum((pred_classes == 1) & (true_classes == 1))
        fn = np.sum((pred_classes == 0) & (true_classes == 1))

        if tp + fn == 0:
            return 0.0
        return float(tp / (tp + fn))

    def _compute_f1(self, labels: np.ndarray, predictions: np.ndarray) -> float:
        """计算F1分数"""
        precision = self._compute_precision(labels, predictions)
        recall = self._compute_recall(labels, predictions)

        if precision + recall == 0:
            return 0.0
        return float(2 * precision * recall / (precision + recall))

    def get_model_info(self) -> dict[str, Any]:
        """获取模型信息"""
        return {
            "model_name": self.model_name,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
            "device": self.device,
            "is_trained": self.is_trained,
            "config": self.config,
            "training_epochs": len(self.training_history.get("loss", [])),
        }

    def state_arrays(self) -> dict[str, np.ndarray]:
        """导出全部可学习参数数组（键不含 ``param.`` 前缀，save 时自动加）。

        子类必须重写以纳入自有参数（weights/biases/memory 等）；
        基类默认无参数。
        """
        return {}

    def load_state_arrays(self, arrays: dict[str, np.ndarray]) -> None:
        """从数组字典恢复参数（键与 :meth:`state_arrays` 一致，子类重写）。

        实现必须重建完整的参数结构（含维度派生属性）并置 ``_initialized=True``，
        使模型无需再调用 build() 即可前向推理。
        """
        if arrays:
            raise ValueError(
                f"{type(self).__name__} 未实现 load_state_arrays，无法加载含参数的权重文件"
                f"（{len(arrays)} 个参数数组被忽略）。"
            )

    def save(self, path: str) -> None:
        """保存模型到 .npz（元数据 + 全部可学习参数）。

        Args:
            path: 目标文件路径（建议 .npz 后缀；np.savez 缺后缀时自动补）。
        """
        # build 幂等：未构建时先构建，保证参数数组存在（保存随机初始化权重合法，
        # is_trained=False 已表明其未经训练）
        self.build()
        payload: dict[str, Any] = {
            "format_version": NPZ_FORMAT_VERSION,
            "model_name": self.model_name,
            "input_dim": self.input_dim,
            "output_dim": self.output_dim,
            "is_trained": self.is_trained,
            "config_json": json.dumps(self.config, default=str),
        }
        for key, arr in self.state_arrays().items():
            payload[f"{_PARAM_PREFIX}{key}"] = np.asarray(arr)
        np.savez(path, **payload)

    def load(self, path: str) -> None:
        """从 .npz 加载模型（元数据 + 参数）。

        兼容历史 v1 格式（仅元数据、无 ``param.*``）：仅恢复元数据并告警，
        参数保持当前初始化——调用方应通过运行时的 ``weights_source`` 语义
        辨识此类文件（不得视为已训练权重）。
        """
        data = np.load(path, allow_pickle=False)
        if "model_name" in data.files:
            self.model_name = str(data["model_name"])
        if "input_dim" in data.files:
            self.input_dim = int(data["input_dim"])
        if "output_dim" in data.files:
            self.output_dim = int(data["output_dim"])
        if "is_trained" in data.files:
            self.is_trained = bool(data["is_trained"])
        if "config_json" in data.files:
            try:
                self.config = json.loads(str(data["config_json"]))
            except (ValueError, TypeError):
                logger.warning("权重文件 %s 的 config_json 解析失败，保留当前 config", path)

        arrays = {key[len(_PARAM_PREFIX) :]: data[key] for key in data.files if key.startswith(_PARAM_PREFIX)}
        if not arrays:
            logger.warning(
                "权重文件 %s 为 v1 仅元数据格式（不含 param.* 参数数组）——参数保持初始化状态，不得视为已训练权重。",
                path,
            )
            return
        self.load_state_arrays(arrays)

    def measure_inference_time(self, x: np.ndarray, n_runs: int = 100) -> dict[str, float]:
        """
        测量推理时间

        Args:
            x: 输入数据
            n_runs: 运行次数

        Returns:
            推理时间统计
        """
        times = []
        for _ in range(n_runs):
            start = time.perf_counter()
            self.predict(x)
            end = time.perf_counter()
            times.append((end - start) * 1000)  # 转换为毫秒

        return {
            "mean_ms": np.mean(times),
            "std_ms": np.std(times),
            "min_ms": np.min(times),
            "max_ms": np.max(times),
            "p50_ms": np.percentile(times, 50),
            "p95_ms": np.percentile(times, 95),
            "p99_ms": np.percentile(times, 99),
        }
