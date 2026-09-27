"""CFC (Context-Free Continuous-time) Model for Fast Inference.

Implements a lightweight feed-forward neural network optimized for low-latency
inference scenarios (< 100ms response time). Uses ReLU activations with
He initialization and supports both inference and training workflows.

Key components:
    - CFCModel: Fast inference model inheriting from BaseLNNModel.

Example:
    >>> model = CFCModel(
    ...     model_name="CFC-Fast",
    ...     input_dim=128,
    ...     output_dim=10,
    ...     hidden_dim=256,
    ... )
    >>> model.build()
    >>> output = model.predict(np.random.randn(32, 128))

数学模型:
    CFC (Closed-form Continuous-time) 连续时间更新公式:

        h_new = h + dt * dh

    其中 dh 由闭式连续时间函数 f_cfc 给出:

        dh = f_cfc(W·x, U·h, h, t)

    具体地，本实现通过 backbone 网络（Linear → Tanh → Linear）计算 dh:

        dh = backbone([x, h])  # 拼接输入与隐藏状态后过两层 MLP

    然后以闭式更新隐藏状态:

        h_new = h + dt * dh

    相比传统 ODE 求解器，CFC 避免了迭代式数值积分，直接以闭式计算
    dh，从而在保持连续时间动态特性的同时实现 160x 加速（相对 LSTM）。

工程运行时版（W 引擎验证修复）：V2.7 解耦时本模块被误迁至 research/，
导致 LNNModelRegistry 的 CFC 映射为 None、全部 LNN 在线预测不可用。
2026-09 自 research/models/cfc_model.py 回迁；训练转换路径
（to_torch）依赖 research 侧的 torch 变体，运行时仅使用 NumPy 推理。
"""

import numpy as np
from typing import Any

from .base_lnn import BaseLNNModel, DEFAULT_WEIGHT_DECAY, _collect_indexed_arrays


class CFCModel(BaseLNNModel):
    """CFC model optimized for fast inference (< 100ms latency).

    A feed-forward neural network with configurable depth and width,
    using ReLU activations and He initialization. Supports NumPy-based
    inference and can be converted to PyTorch for GPU training.

    Attributes:
        hidden_dim: Hidden layer dimension.
        num_layers: Number of hidden layers.
        dropout_rate: Dropout probability during training.
        weights: List of weight matrices for each layer.
        biases: List of bias vectors for each layer.
        training: Whether the model is in training mode (enables dropout).

    Example:
        >>> model = CFCModel(input_dim=10, output_dim=2, hidden_dim=64)
        >>> model.build()
        >>> x = np.random.randn(5, 10)
        >>> predictions = model.predict(x)
        >>> predictions.shape
        (5, 2)
    """

    def __init__(
        self,
        model_name: str = "CFC",
        input_dim: int = 128,
        output_dim: int = 10,
        hidden_dim: int = 256,
        num_layers: int = 2,
        dropout_rate: float = 0.1,
        device: str = "cpu",
        **kwargs,
    ):
        """
        初始化CFC模型

        Args:
            model_name: 模型名称
            input_dim: 输入维度
            output_dim: 输出维度
            hidden_dim: 隐藏层维度
            num_layers: 网络层数
            dropout_rate: Dropout比率
            device: 计算设备
        """
        super().__init__(model_name, input_dim, output_dim, device, **kwargs)

        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.dropout_rate = dropout_rate

        # 网络权重（使用随机初始化，可通过训练流程更新）
        self.weights: list[np.ndarray] = []
        self.biases: list[np.ndarray] = []
        self._initialized = False
        self.training = False

    def build(self) -> None:
        """构建CFC网络结构"""
        if self._initialized:
            return

        # 初始化网络权重
        layer_dims = [self.input_dim] + [self.hidden_dim] * self.num_layers + [self.output_dim]

        for i in range(len(layer_dims) - 1):
            # He初始化 (适用于ReLU激活函数)
            fan_in = layer_dims[i]
            fan_out = layer_dims[i + 1]
            std = np.sqrt(2.0 / (fan_in + 1e-8))
            W = np.random.normal(0, std, (fan_in, fan_out))
            b = np.zeros(fan_out)

            self.weights.append(W)
            self.biases.append(b)

        self._initialized = True

    def forward(self, x: np.ndarray) -> np.ndarray:
        """
        前向传播

        Args:
            x: 输入数据 (batch_size, input_dim)

        Returns:
            模型输出 (batch_size, output_dim)
        """
        if not self._initialized:
            self.build()

        # 确保输入是2D
        if x.ndim == 1:
            x = x.reshape(1, -1)

        # 逐层传播
        activations = x
        for i, (W, b) in enumerate(zip(self.weights, self.biases)):
            z = activations @ W + b

            # 最后一层不使用激活函数（或根据任务选择）
            if i < len(self.weights) - 1:
                activations = self._relu(z)
                # 应用Dropout（仅在训练时）
                if self.training:
                    mask = (np.random.random(z.shape) > self.dropout_rate).astype(float)
                    activations = activations * mask / (1 - self.dropout_rate)
            else:
                activations = z

        return activations

    def predict(self, x: np.ndarray) -> np.ndarray:
        """
        快速预测接口

        Args:
            x: 输入数据

        Returns:
            预测结果
        """
        return self.forward(x)

    def _relu(self, x: np.ndarray) -> np.ndarray:
        """ReLU激活函数"""
        return np.maximum(0, x)

    def _softmax(self, x: np.ndarray) -> np.ndarray:
        """Softmax激活函数"""
        exp_x = np.exp(x - np.max(x, axis=-1, keepdims=True))
        return exp_x / np.sum(exp_x, axis=-1, keepdims=True)

    def _cross_entropy_loss(self, predictions: np.ndarray, labels: np.ndarray) -> float:
        """交叉熵损失

        .. deprecated::
            学术诚信修复：CFC 用于颤振预测（回归任务），训练路径已统一为 MSE 损失。
            此方法保留供未来分类任务复用，当前训练/验证路径不再调用。
        """
        # 数值稳定性处理
        predictions = predictions - np.max(predictions, axis=-1, keepdims=True)
        log_probs = predictions - np.log(np.sum(np.exp(predictions), axis=-1, keepdims=True))

        if labels.ndim == 1:
            # 转换为one-hot
            labels = np.eye(predictions.shape[1])[labels.astype(int)]

        return -np.mean(np.sum(labels * log_probs, axis=-1))

    def _mse_loss(self, predictions: np.ndarray, labels: np.ndarray) -> float:
        """均方误差损失（学术诚信修复：统一训练/验证损失语义）"""
        if labels.ndim == 1 and predictions.ndim == 2:
            labels = labels.reshape(-1, 1)
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

        替代原有的数值梯度近似方法，使用PyTorch的loss.backward()和
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
            # P3-AI-3: 使用 torch.tensor + 显式 dtype 替代 FloatTensor，避免受全局默认 dtype 影响
            batch_data = torch.tensor(data[indices], dtype=torch.float32)
            batch_labels = torch.tensor(labels[indices], dtype=torch.float32)
            if batch_labels.ndim == 1:
                batch_labels = batch_labels.unsqueeze(1)

            # 转换为PyTorch模型
            # 学术诚信修复：使用 self.device 而非硬编码 "cpu"，支持 GPU 训练
            torch_model = self.to_torch(device=self.device)
            torch_model.train()

            optimizer = torch.optim.AdamW(torch_model.parameters(), lr=learning_rate, weight_decay=DEFAULT_WEIGHT_DECAY)
            criterion = torch.nn.MSELoss()

            optimizer.zero_grad()
            outputs = torch_model(batch_data)
            if isinstance(outputs, tuple):
                outputs = outputs[0]
            loss = criterion(outputs, batch_labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(torch_model.parameters(), 1.0)
            optimizer.step()

            # 同步权重回NumPy
            self._sync_from_torch(torch_model)

            return float(loss.item())

        except ImportError:
            # PyTorch不可用时使用NumPy解析梯度回退（保证收敛）
            return self._train_step_numpy(data, labels, batch_size, learning_rate)

    def _train_step_numpy(
        self,
        data: np.ndarray,
        labels: np.ndarray,
        batch_size: int,
        learning_rate: float,
    ) -> float:
        """NumPy训练回退方案（仅当PyTorch不可用时使用）

        通过解析反向传播计算真实梯度，避免随机噪声无法保证收敛的问题。
        网络结构：输入 -> [ReLU(Linear)] * (L-1) -> Linear -> 输出
        """
        n_samples = data.shape[0]
        indices = np.random.choice(n_samples, min(batch_size, n_samples), replace=False)
        batch_data = data[indices]
        batch_labels = labels[indices]

        # 前向传播并缓存中间结果用于反向传播
        activations = [batch_data]  # a[0] = x
        pre_activations = []  # z[i] = a[i] @ W[i] + b[i]
        current = batch_data
        for i, (W, b) in enumerate(zip(self.weights, self.biases)):
            z = current @ W + b
            pre_activations.append(z)
            if i < len(self.weights) - 1:
                current = self._relu(z)
            else:
                current = z  # 输出层线性激活
            activations.append(current)

        predictions = activations[-1]
        # 学术诚信修复：统一为 MSE 损失，与训练路径（MSELoss）和验证路径语义一致
        loss = self._mse_loss(predictions, batch_labels)

        # 反向传播：计算梯度（MSE 损失，线性输出层）
        # dL/dz = 2 * (predictions - labels) / N
        if batch_labels.ndim == 1 and predictions.ndim == 2:
            batch_labels = batch_labels.reshape(-1, 1)
        grad_z = 2.0 * (predictions - batch_labels) / n_samples  # dL/dz

        for i in reversed(range(len(self.weights))):
            # dL/dW[i] = a[i].T @ dL/dz[i]
            grad_W = activations[i].T @ grad_z
            # dL/db[i] = mean(dL/dz[i], axis=0)
            grad_b = np.mean(grad_z, axis=0)

            self.weights[i] -= learning_rate * grad_W
            self.biases[i] -= learning_rate * grad_b

            # 将梯度传播到上一层（若非输入层）
            if i > 0:
                # dL/da[i] = dL/dz[i] @ W[i].T
                grad_a = grad_z @ self.weights[i].T
                # dL/dz[i-1] = dL/da[i] * relu'(z[i-1])
                relu_mask = (pre_activations[i - 1] > 0).astype(float)
                grad_z = grad_a * relu_mask

        return float(loss)

    def _sync_from_torch(self, torch_model) -> None:
        """从PyTorch模型同步权重回NumPy模型"""
        import torch as _torch

        # P2-AI-4: 使用 inference_mode 替代 no_grad，权重同步为纯读操作，无需 autograd 图
        with _torch.no_grad():
            # 同步CFC层权重
            backbone = torch_model.cfc_layer.backbone
            if len(self.weights) >= 1:
                self.weights[0] = backbone[0].weight.data.cpu().numpy().T
                self.biases[0] = backbone[0].bias.data.cpu().numpy()
            if len(self.weights) >= 2:
                self.weights[1] = backbone[2].weight.data.cpu().numpy().T
                self.biases[1] = backbone[2].bias.data.cpu().numpy()
            # 同步输出层
            if len(self.weights) >= 3:
                self.weights[-1] = torch_model.output_layer.weight.data.cpu().numpy().T
                self.biases[-1] = torch_model.output_layer.bias.data.cpu().numpy()

    def _validate(self, val_data: np.ndarray, val_labels: np.ndarray) -> float:
        """
        验证模型

        Args:
            val_data: 验证数据
            val_labels: 验证标签

        Returns:
            验证损失
        """
        predictions = self.forward(val_data)
        # 学术诚信修复：验证损失统一为 MSE，与训练路径（MSELoss）语义一致
        loss = self._mse_loss(predictions, val_labels)
        return float(loss)

    def get_model_info(self) -> dict[str, Any]:
        """获取CFC模型信息"""
        info = super().get_model_info()
        info.update(
            {
                "model_type": "CFC",
                "hidden_dim": self.hidden_dim,
                "num_layers": self.num_layers,
                "dropout_rate": self.dropout_rate,
                "target_latency_ms": 100,
            }
        )
        return info

    def state_arrays(self) -> dict[str, np.ndarray]:
        """导出全部参数：weights / biases。"""
        if not self._initialized:
            self.build()
        arrays: dict[str, np.ndarray] = {}
        for i, w in enumerate(self.weights):
            arrays[f"weights.{i}"] = w
        for i, b in enumerate(self.biases):
            arrays[f"biases.{i}"] = b
        return arrays

    def load_state_arrays(self, arrays: dict[str, np.ndarray]) -> None:
        """从数组字典恢复完整参数结构（含维度派生属性），无需再 build()。"""
        weights = _collect_indexed_arrays(arrays, "weights")
        biases = _collect_indexed_arrays(arrays, "biases")
        if not weights:
            raise ValueError("CFC 加载失败：权重文件缺少 weights.* 参数数组")
        if len(weights) != len(biases):
            raise ValueError(f"CFC 加载失败：weights 层数（{len(weights)}）与 biases 层数（{len(biases)}）不一致")
        self.weights = weights
        self.biases = biases
        # 维度派生属性与 build() 保持一致
        self.hidden_dim = int(weights[0].shape[1])
        self.num_layers = len(weights) - 1
        self._initialized = True

    def to_torch(self, device: str = "cpu"):
        """
        将NumPy模型转换为PyTorch模型以支持GPU训练

        Args:
            device: 目标设备 ('cpu' 或 'cuda')

        Returns:
            PyTorch CFCModel实例

        Note:
            训练转换依赖 research 侧的 ``models.torch_cfc_model``（torch 变体），
            工程运行时不含该依赖——调用将抛出带指引的 RuntimeError。
        """
        if not self._initialized:
            self.build()

        try:
            from models.torch_cfc_model import CFCModel as TorchCFCModel
            from models.torch_base_lnn import LNNConfig

            config = LNNConfig(
                input_size=self.input_dim,
                hidden_size=self.hidden_dim,
                output_size=self.output_dim,
                num_layers=self.num_layers,
                dropout=self.dropout_rate,
            )

            torch_model = TorchCFCModel(config)

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
                "CFC 模型转换失败：转换为 PyTorch 张量需要安装 PyTorch 库（工程运行时"
                "不含 torch 与训练变体）。训练/转换请在 research 环境执行，"
                "并将产物权重放入 models/ 后通过 load() 加载。"
            )

    def _assign_torch_weights(self, torch_model) -> None:
        """将 NumPy 训练得到的权重赋值到 PyTorch CFC 模型。

        在 ``torch.inference_mode`` 上下文中以纯赋值方式加载权重，
        避免构建 autograd 图。权重映射:
            - weights[0] -> cfc_layer.backbone[0].weight (转置) / .bias
            - weights[1] -> cfc_layer.backbone[2].weight (转置) / .bias
            - weights[-1] -> output_layer.weight (转置) / .bias

        Args:
            torch_model: 已构建的 ``TorchCFCModel`` 实例。
        """
        import torch

        with torch.no_grad():
            torch_model.cfc_layer.backbone[0].weight.data = torch.tensor(self.weights[0].T, dtype=torch.float32)
            torch_model.cfc_layer.backbone[0].bias.data = torch.tensor(self.biases[0], dtype=torch.float32)

            if len(self.weights) > 1:
                torch_model.cfc_layer.backbone[2].weight.data = torch.tensor(self.weights[1].T, dtype=torch.float32)
                torch_model.cfc_layer.backbone[2].bias.data = torch.tensor(self.biases[1], dtype=torch.float32)

            if len(self.weights) >= 3:
                # output_layer 为 Sequential(Linear, ReLU, Dropout?, Linear)——末层是输出 Linear
                torch_model.output_layer[-1].weight.data = torch.tensor(self.weights[-1].T, dtype=torch.float32)
                torch_model.output_layer[-1].bias.data = torch.tensor(self.biases[-1], dtype=torch.float32)
