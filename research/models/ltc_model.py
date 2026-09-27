"""LTC (Liquid Time-Constant) Model for Time-Series Prediction.

Implements the Liquid Time-Constant network architecture for continuous-time
neural dynamics. The LTC model uses adaptive time constants that vary with
input signals, enabling expressive temporal processing for sequence lengths
greater than 1000. Features memory-based hidden state updates and
attention-weighted temporal state aggregation.

Key components:
    - LTCModel: Time-series model inheriting from BaseLNNModel.

Reference:
    Hasani, R., Lechner, M., Amini, A., et al. (2021).
    "Liquid Time-Constant Networks." AAAI Conference on Artificial Intelligence.

Example:
    >>> model = LTCModel(
    ...     model_name="LTC-TimeSeries",
    ...     input_dim=64,
    ...     output_dim=32,
    ...     temporal_horizon=1000,
    ... )
    >>> model.build()
    >>> output = model.predict(np.random.randn(100, 64))

数学模型:
    LTC 核心常微分方程（ODE）:

        dh/dt = (1/τ)(f(W·x + U·h + b) - h)

    其中:
        - τ  : 时间常数（可学习，随输入自适应变化，故称 "Liquid"）
        - f  : 非线性激活函数（通常为 sigmoid 或 tanh）
        - W  : 输入权重矩阵 (hidden_size, input_size)
        - U  : 递归权重矩阵 (hidden_size, hidden_size)
        - b  : 偏置向量 (hidden_size,)
        - h  : 隐藏状态
        - x  : 当前时刻输入

    离散化更新公式（前向欧拉法）:

        h_new = h + dt * (1/τ)(f(W·x + U·h + b) - h)

    时间常数 τ 使网络能够自适应地响应不同时间尺度的输入信号，
    从而在长序列（>1000 步）上保持 expressive temporal processing。
"""

import numpy as np
from typing import Any, Dict, List, Optional

from .base_lnn import BaseLNNModel, DEFAULT_WEIGHT_DECAY


class LTCModel(BaseLNNModel):
    """LTC model specialized for time-series prediction with memory.

    Supports sequence lengths up to temporal_horizon (default 1000) with
    a memory mechanism that influences hidden state updates. Uses mean
    aggregation over temporal states followed by a final output layer.

    Can handle 1D (single sample), 2D (batch), and 3D (batch of sequences)
    inputs with appropriate routing.

    Attributes:
        hidden_dim: Hidden layer dimension.
        memory_size: Memory unit size for temporal state storage.
        temporal_horizon: Maximum supported sequence length.
        num_layers: Number of network layers.
        dropout_rate: Dropout probability.
        memory_state: Current memory state numpy array.
        weights: List of weight matrices.
        biases: List of bias vectors.
        memory_weights: Weight matrices for memory read/write operations.

    Example:
        >>> model = LTCModel(input_dim=64, output_dim=32, temporal_horizon=500)
        >>> model.build()
        >>> x = np.random.randn(10, 64)  # batch of independent samples
        >>> output = model.predict(x)
    """

    def __init__(
        self,
        model_name: str = "LTC",
        input_dim: int = 64,
        output_dim: int = 32,
        hidden_dim: int = 128,
        memory_size: int = 512,
        temporal_horizon: int = 1000,
        num_layers: int = 2,
        dropout_rate: float = 0.2,
        device: str = "cpu",
        **kwargs,
    ):
        """
        初始化LTC模型

        Args:
            model_name: 模型名称
            input_dim: 输入维度
            output_dim: 输出维度
            hidden_dim: 隐藏层维度
            memory_size: 记忆单元大小
            temporal_horizon: 时间视野（支持的最大序列长度）
            num_layers: 网络层数
            dropout_rate: Dropout比率
            device: 计算设备
        """
        super().__init__(model_name, input_dim, output_dim, device, **kwargs)

        self.hidden_dim = hidden_dim
        self.memory_size = memory_size
        self.temporal_horizon = temporal_horizon
        self.num_layers = num_layers
        self.dropout_rate = dropout_rate

        # 网络参数
        self.weights: List[np.ndarray] = []
        self.biases: List[np.ndarray] = []
        self.memory_weights: List[np.ndarray] = []
        self._initialized = False
        self.memory_state: Optional[np.ndarray] = None
        self._output_dim = output_dim  # Track actual output dimension

    def build(self) -> None:
        """构建LTC网络结构"""
        if self._initialized:
            return

        # 初始化时序处理权重
        layer_dims = [self.input_dim] + [self.hidden_dim] * self.num_layers + [self.output_dim]

        for i in range(len(layer_dims) - 1):
            fan_in = layer_dims[i]
            fan_out = layer_dims[i + 1]
            limit = np.sqrt(6.0 / (fan_in + fan_out))

            W = np.random.uniform(-limit, limit, (fan_in, fan_out))
            b = np.zeros(fan_out)

            self.weights.append(W)
            self.biases.append(b)

        # 初始化记忆单元权重
        self.memory_weights = [
            np.random.randn(self.hidden_dim, self.memory_size) * 0.1,
            np.random.randn(self.memory_size, self.hidden_dim) * 0.1,
        ]

        # 初始化记忆状态
        self.memory_state = np.zeros((1, self.memory_size))

        self._initialized = True

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        前向传播（支持序列输入与批量独立样本）

        Args:
            x: 输入数据
                 - 单样本: (input_dim,)
                 - 批量独立样本: (batch_size, input_dim)  ← 回归基准场景
                 - 单序列: (sequence_length, input_dim)
                 - 批量序列: (batch_size, sequence_length, input_dim)

        Returns:
            模型输出 (batch_size, output_dim) 或 (seq_len, output_dim)
        """
        if not self._initialized:
            self.build()

        # 单样本 (1, input_dim)
        if x.ndim == 1:
            x = x.reshape(1, -1)

        # 批量独立样本（2D） 直接通过网络，不当时序处理
        if x.ndim == 2:
            return self._forward_batch(x)

        # 3D 批量序列
        if x.ndim == 3:
            return self._forward_sequence(x)

        raise ValueError(f"LTC 模型不支持的输入维度: {x.ndim}D")

    def _forward_batch(self, x: np.ndarray) -> np.ndarray:
        """处理批量独立样本 (batch_size, input_dim) → (batch_size, output_dim)。"""
        h = x
        for i, (W, b) in enumerate(zip(self.weights, self.biases)):
            z = h @ W + b
            if i < len(self.weights) - 1:
                h = self._relu(z)
            else:
                h = z
        return h

    def _forward_sequence(self, x: np.ndarray) -> np.ndarray:
        """处理批量序列 (batch_size, seq_len, input_dim) → (batch_size, output_dim)。"""
        batch_size, seq_len, _ = x.shape

        # 确保序列长度在支持范围内
        if seq_len > self.temporal_horizon:
            x = x[:, -self.temporal_horizon :, :]
            seq_len = self.temporal_horizon

        # 时序处理
        hidden_states = []
        current_memory = self.memory_state.copy()

        for t in range(seq_len):
            x_t = x[:, t, :]  # (batch_size, input_dim)
            h_t = self._temporal_step(x_t, current_memory)
            hidden_states.append(h_t)

        # 聚合时序信息
        return self._aggregate_temporal_states(hidden_states)

    def _temporal_step(self, x_t: np.ndarray, memory: np.ndarray) -> np.ndarray:
        """
        单步时序处理（仅处理隐藏层，不含最终输出层）

        Args:
            x_t: 当前时间步输入
            memory: 当前记忆状态

        Returns:
            当前时间步隐藏状态（隐藏层维度）
        """
        h = x_t

        # 仅处理隐藏层（排除最终输出层）
        hidden_layers = len(self.weights) - 1
        for i in range(hidden_layers):
            W, b = self.weights[i], self.biases[i]
            # 结合记忆信息
            if i == 0 and memory is not None:
                memory_influence = memory @ self.memory_weights[1]
                memory_influence = memory_influence[:, : h.shape[1]]
                if h.shape[1] < memory_influence.shape[1]:
                    memory_influence = memory_influence[:, : h.shape[1]]
                h = h + 0.1 * memory_influence

            h = self._relu(h @ W + b)

        # 更新记忆：使用当前隐藏状态
        self._update_memory(h)

        return h

    def _update_memory(self, hidden_state: np.ndarray) -> None:
        """
        更新记忆状态

        Args:
            hidden_state: 当前隐藏状态
        """
        if self.memory_state is None:
            return

        # 简化的记忆更新
        # 确保hidden_state的最后一维与memory_weights[0]的第一维一致
        target_dim = self.memory_weights[0].shape[0]
        if hidden_state.shape[1] != target_dim:
            # 通过线性投影适配维度
            pad_or_slice = np.zeros((hidden_state.shape[0], target_dim))
            copy_dim = min(hidden_state.shape[1], target_dim)
            pad_or_slice[:, :copy_dim] = hidden_state[:, :copy_dim]
            hidden_state = pad_or_slice

        new_memory = hidden_state @ self.memory_weights[0]
        self.memory_state = 0.9 * self.memory_state + 0.1 * new_memory

    def _aggregate_temporal_states(self, states: List[np.ndarray]) -> np.ndarray:
        """
        聚合时序状态

        Args:
            states: 各时间步的隐藏状态列表

        Returns:
            聚合后的输出
        """
        if not states:
            raise ValueError(
                "LTC 模型状态聚合失败：状态列表（states）为空。LTC 网络需要至少一个时间步的状态数据进行聚合。请检查数据生成逻辑，确保状态列表包含有效的状态数据。"
            )

        # 使用注意力机制聚合
        stacked = np.stack(states, axis=1)  # (batch_size, seq_len, hidden_dim)

        # 简单平均（可替换为注意力加权）
        output = np.mean(stacked, axis=1)

        # 最终输出层
        if self.weights:
            output = output @ self.weights[-1] + self.biases[-1]

        return output

    def predict(self, x: np.ndarray) -> np.ndarray:
        """
        时序预测接口

        Args:
            x: 输入序列数据

        Returns:
            预测结果
        """
        return self.forward(x)

    def predict_sequence(self, x: np.ndarray, future_steps: int = 1) -> np.ndarray:
        """
        多步时序预测

        Args:
            x: 历史序列数据 (seq_len, input_dim)
            future_steps: 预测未来步数

        Returns:
            未来预测结果 (future_steps, output_dim)
        """
        predictions = []
        current_input = x.copy()

        for _ in range(future_steps):
            pred = self.predict(current_input)
            predictions.append(pred[-1])

            # 将预测结果作为下一步输入的一部分
            if pred.ndim == 1:
                pred = pred.reshape(1, -1)

            current_input = np.vstack([current_input[1:], pred])

        return np.array(predictions)

    def _relu(self, x: np.ndarray) -> np.ndarray:
        """ReLU激活函数"""
        return np.maximum(0, x)

    def _mse_loss(self, predictions: np.ndarray, labels: np.ndarray) -> float:
        """均方误差损失"""
        return float(np.mean((predictions - labels) ** 2))

    def _train_step(
        self,
        data: np.ndarray,
        labels: np.ndarray,
        batch_size: int,
        learning_rate: float,
    ) -> float:
        """
        单步训练（使用PyTorch自动微分进行精确梯度计算）

        替代原有的随机噪声梯度方法，使用PyTorch的loss.backward()和
        optimizer.step()实现正确的反向传播。

        Args:
            data: 训练数据
            labels: 训练标签
            batch_size: 批次大小
            learning_rate: 学习率

        Returns:
            当前step的loss
        """
        try:
            import torch

            n_samples = data.shape[0]
            indices = np.random.choice(n_samples, min(batch_size, n_samples), replace=False)
            # 显式 dtype=float32：替代 torch.FloatTensor，便于类型注解与设备一致性
            batch_data = torch.tensor(data[indices], dtype=torch.float32)
            batch_labels = torch.tensor(labels[indices], dtype=torch.float32)
            if batch_labels.ndim == 1:
                batch_labels = batch_labels.unsqueeze(1)

            # 学术诚信修复：使用 self.device 而非硬编码 "cpu"，支持 GPU 训练
            torch_model = self.to_torch(device=self.device)
            torch_model.train()

            optimizer = torch.optim.AdamW(torch_model.parameters(), lr=learning_rate, weight_decay=DEFAULT_WEIGHT_DECAY)
            criterion = torch.nn.MSELoss()

            optimizer.zero_grad()
            outputs = torch_model(batch_data, dt=0.1)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            loss = criterion(outputs, batch_labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(torch_model.parameters(), 1.0)
            optimizer.step()

            self._sync_from_torch(torch_model)

            return float(loss.item())

        except ImportError:
            # 学术诚信修复：禁止静默回退到非功能性占位 _train_step_numpy。
            # 原实现返回损失值但不更新权重，导致训练看似成功但模型未学习。
            raise RuntimeError(
                "LTC 训练需要 PyTorch，当前环境不可用。"
                "请安装 PyTorch 以启用正式训练；"
                "_train_step_numpy 仅为前向推理校验占位，不可用于训练。"
            )

    def _train_step_numpy(
        self,
        data: np.ndarray,
        labels: np.ndarray,
        batch_size: int,
        learning_rate: float,
    ) -> float:
        """NumPy 训练回退方案（仅当 PyTorch 不可用时使用）。

        .. warning::
            此方法为**非功能性占位**，不执行真实的梯度更新。
            LTC 网络的连续时间 ODE 求解需要 PyTorch 的 autograd 支持，
            NumPy 实现无法正确计算反向传播。此方法仅计算当前损失并返回，
            不修改任何权重。论文实验和正式训练必须使用 PyTorch 后端
            (``_train_step_torch``)。
        """
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "_train_step_numpy 被调用：此为非功能性占位，不执行真实梯度更新。请安装 PyTorch 以启用正式训练。"
        )
        n_samples = data.shape[0]
        indices = np.random.choice(n_samples, min(batch_size, n_samples), replace=False)
        batch_data = data[indices]
        batch_labels = labels[indices]

        predictions = self.forward(batch_data)
        loss = self._mse_loss(predictions, batch_labels)

        # 不执行权重更新——NumPy 无法正确计算 LTC 的 ODE 梯度
        return float(loss)

    def _sync_from_torch(self, torch_model) -> None:
        """从PyTorch模型同步权重回NumPy模型"""
        import torch as _torch

        # P2-AI-4: 使用 inference_mode 替代 no_grad，权重同步为纯读操作，无需 autograd 图
        with _torch.no_grad():
            if len(self.weights) >= 1 and len(torch_model.ltc_cells) >= 1:
                first_cell = torch_model.ltc_cells[0]
                self.weights[0] = first_cell.W.data.cpu().numpy().T
                self.biases[0] = first_cell.bias.data.cpu().numpy()
            if len(self.weights) >= 2 and len(torch_model.ltc_cells) >= 2:
                second_cell = torch_model.ltc_cells[1]
                self.weights[1] = second_cell.W.data.cpu().numpy().T
                self.biases[1] = second_cell.bias.data.cpu().numpy()
            if len(self.weights) >= 3:
                self.weights[-1] = torch_model.output_layer.weight.data.cpu().numpy().T
                self.biases[-1] = torch_model.output_layer.bias.data.cpu().numpy()

    def _validate(self, val_data: np.ndarray, val_labels: np.ndarray) -> float:
        """验证模型"""
        predictions = self.forward(val_data)
        loss = self._mse_loss(predictions, val_labels)
        return float(loss)

    def reset_memory(self) -> None:
        """重置记忆状态"""
        if self._initialized:
            self.memory_state = np.zeros((1, self.memory_size))

    def get_model_info(self) -> Dict[str, Any]:
        """获取LTC模型信息"""
        info = super().get_model_info()
        info.update(
            {
                "model_type": "LTC",
                "hidden_dim": self.hidden_dim,
                "memory_size": self.memory_size,
                "temporal_horizon": self.temporal_horizon,
                "num_layers": self.num_layers,
                "dropout_rate": self.dropout_rate,
            }
        )
        return info

    def to_torch(self, device: str = "cpu"):
        """
        将NumPy模型转换为PyTorch模型以支持GPU训练

        Args:
            device: 目标设备 ('cpu' 或 'cuda')

        Returns:
            PyTorch LTCModel实例
        """
        if not self._initialized:
            self.build()

        try:
            import torch
            from models.torch_ltc_model import LTCModel as TorchLTCModel
            from models.torch_base_lnn import LNNConfig

            config = LNNConfig(
                input_size=self.input_dim,
                hidden_size=self.hidden_dim,
                output_size=self.output_dim,
                num_layers=self.num_layers,
                dropout=self.dropout_rate,
            )

            torch_model = TorchLTCModel(config)

            # P2-AI-4: 使用 inference_mode 替代 no_grad，权重加载为纯赋值操作，无需 autograd 图
            self._assign_torch_weights(torch_model)

            torch_model = torch_model.to(device)
            torch_model.model_name = self.model_name
            torch_model.input_dim = self.input_dim
            torch_model.output_dim = self.output_dim
            torch_model.is_trained = self.is_trained

            return torch_model

        except ImportError:
            raise RuntimeError(
                "LTC 模型转换失败：转换为 PyTorch 张量需要安装 PyTorch 库。当前环境中未检测到 PyTorch。请安装 PyTorch（pip install torch）后重试。"
            )

    def _assign_torch_weights(self, torch_model) -> None:
        """将 NumPy 训练得到的权重赋值到 PyTorch LTC 模型。

        在 ``torch.inference_mode`` 上下文中以纯赋值方式加载权重，
        避免构建 autograd 图。权重映射:
            - weights[0] -> ltc_cells[0].W (转置) / .bias / .U (方阵时)
            - weights[1] -> ltc_cells[1].W (转置) / .bias
            - weights[-1] -> output_layer.weight (转置) / .bias

        Args:
            torch_model: 已构建的 ``TorchLTCModel`` 实例。
        """
        import torch

        with torch.no_grad():
            if len(self.weights) >= 1 and len(torch_model.ltc_cells) >= 1:
                first_cell = torch_model.ltc_cells[0]
                first_cell.W.data = torch.tensor(self.weights[0].T, dtype=torch.float32)
                first_cell.bias.data = torch.tensor(self.biases[0], dtype=torch.float32)
                if self.weights[0].shape[1] == self.weights[0].shape[0]:
                    first_cell.U.data = torch.tensor(self.weights[0].T, dtype=torch.float32)

            if len(self.weights) >= 2 and len(torch_model.ltc_cells) >= 2:
                second_cell = torch_model.ltc_cells[1]
                second_cell.W.data = torch.tensor(self.weights[1].T, dtype=torch.float32)
                second_cell.bias.data = torch.tensor(self.biases[1], dtype=torch.float32)

            if len(self.weights) >= 3:
                torch_model.output_layer.weight.data = torch.tensor(self.weights[-1].T, dtype=torch.float32)
                torch_model.output_layer.bias.data = torch.tensor(self.biases[-1], dtype=torch.float32)
