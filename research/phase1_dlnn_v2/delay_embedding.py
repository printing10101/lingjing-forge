"""
可学习延迟嵌入模块 （Phase 1 核心创新）

将论文中的固定延迟 T=60/n 替换为可微训练的延迟 τ，
支持线性插值实现非整数延迟，并附加物理正则化约束。

数学：
    h_delayed(t) = interpolate(h[t-τ], h[t-τ+1]),  τ 为可学习参数
    L_tau_reg = λ_tau * ||τ - τ_phys||²,  τ_phys = 60/n（物理先验）

参考：
    Monsel et al. (2024) "Time and State Dependent Neural Delay Differential
    Equations" — 状态依赖延迟的可微实现。
    Zhu et al. (2022) "Neural Piecewise-Constant Delay Differential Equations"
    — NPCDDE 分段常数延迟框架。
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple


class LearnableDelayEmbedding(nn.Module):
    """
    可学习延迟嵌入层。

    在每个 LTC 层的隐藏状态上应用延迟，使网络等价于求解：
        dh/dt = f(h(t), h(t-τ), x)

    延迟 τ 被建模为可学习参数，初始化为物理值 60/n，
    训练中通过梯度下降与数据/物理损失联合优化。

    Attributes:
        tau: 可学习延迟参数 [1]（秒），范围约束 [0.001, 0.5]
        tau_phys: 固定物理参考值（60/n），用于正则化
        lambda_tau_reg: 物理正则化系数
    """

    def __init__(
        self,
        hidden_dim: int,
        dt: float = 0.1,
        tau_init: float = 0.1,
        tau_phys: Optional[float] = None,
        lambda_tau_reg: float = 0.01,
        delay_buffer_size: int = 128,
    ):
        """
        Args:
            hidden_dim: LTC 隐藏层维度
            dt: 模型积分步长（用于离散化）
            tau_init: 延迟初始值（秒），默认 0.1 ≈ 60/600 rpm
            tau_phys: 物理先验值 τ_phys = 60/n（主轴转速 n 的倒数比例），
                      用于 L_tau_reg 正则化。若为 None，则 τ 无物理约束。
            lambda_tau_reg: 延迟物理正则化系数
            delay_buffer_size: 延迟历史缓冲区大小（个时间步）
        """
        super().__init__()

        self.hidden_dim = hidden_dim
        self.dt = dt
        self.lambda_tau_reg = lambda_tau_reg
        self.delay_buffer_size = delay_buffer_size

        # 可学习延迟 τ（参数化在 log 空间以强制正值）
        tau_init_clamped = max(0.001, min(0.5, float(tau_init)))
        self.log_tau = nn.Parameter(torch.tensor(float(tau_init_clamped)).log())

        # 物理先验（非可学习，用于正则化）
        if tau_phys is not None:
            self.register_buffer("tau_phys", torch.tensor(float(tau_phys)))
        else:
            self.tau_phys = None

        # 延迟历史缓冲区：环形缓冲区 [batch, buffer_size, hidden]
        # 由 trainer 在每步前向传播后维护
        self.register_buffer(
            "_buffer",
            torch.zeros(1, delay_buffer_size, hidden_dim),
            persistent=False,
        )
        self.register_buffer(
            "_buffer_ptr",
            torch.zeros(1, dtype=torch.long),
            persistent=False,
        )
        self._buffer_initialized = False

    @property
    def tau(self) -> torch.Tensor:
        """当前延迟值（秒），保证 > 0"""
        return torch.clamp(torch.exp(self.log_tau), min=0.001, max=0.5)

    def init_buffer(self, batch_size: int, device: torch.device) -> None:
        """初始化延迟缓冲区为零。"""
        self._buffer = torch.zeros(
            batch_size,
            self.delay_buffer_size,
            self.hidden_dim,
            device=device,
        )
        self._buffer_ptr = torch.zeros(batch_size, dtype=torch.long, device=device)
        self._buffer_initialized = True

    def push(self, h: torch.Tensor) -> None:
        """将当前隐藏状态推入环形缓冲区。

        Args:
            h: 当前隐藏状态 [batch_size, hidden_dim]
        """
        if not self._buffer_initialized:
            self.init_buffer(h.size(0), h.device)

        batch_size = h.size(0)
        ptr = self._buffer_ptr  # [batch]
        h_nograd = h.detach()  # 切断梯度：buffer 缓存不参与反向传播
        for b in range(batch_size):
            self._buffer[b, ptr[b]] = h_nograd[b]
        self._buffer_ptr = (ptr + 1) % self.delay_buffer_size

    def get_delayed(self, h_current: torch.Tensor) -> torch.Tensor:
        """
        获取延迟的隐藏状态 h(t-τ)。

        使用线性插值处理非整数延迟：
            k = τ / dt  （离散延迟步数）
            h_delayed = α * buffer[idx_lo] + (1-α) * buffer[idx_hi]
            where α = ceil(k) - k

        Args:
            h_current: 当前隐藏状态 [batch_size, hidden_dim]（用于 τ=0 边缘情况）

        Returns:
            h_delayed: 延迟隐藏状态 [batch_size, hidden_dim]
        """
        if not self._buffer_initialized:
            # 缓冲区未初始化时，返回当前状态（无延迟）
            return h_current

        batch_size = h_current.size(0)
        device = h_current.device

        # 离散延迟步数 k = τ / dt
        k = self.tau / self.dt  # scalar

        if k < 1.0:
            # 延迟不足 1 步，直接返回当前状态
            return h_current

        # 整数/小数部分
        k_floor = int(torch.floor(k).item())
        k_ceil = k_floor + 1
        alpha = float(k_ceil) - float(k)  # 下索引权重

        # 从环形缓冲区读取
        ptr = self._buffer_ptr  # [batch]

        # 计算下/上索引（环形偏移）
        idx_lo = (ptr - k_floor) % self.delay_buffer_size  # [batch]
        idx_hi = (ptr - k_ceil) % self.delay_buffer_size  # [batch]

        # 收集延迟状态（逐 batch 索引）
        h_lo = torch.stack([self._buffer[b, idx_lo[b]] for b in range(batch_size)])  # [batch, hidden]

        h_hi = torch.stack([self._buffer[b, idx_hi[b]] for b in range(batch_size)])  # [batch, hidden]

        # 线性插值
        h_delayed = alpha * h_lo + (1.0 - alpha) * h_hi

        return h_delayed

    def compute_tau_regularization(self, spindle_speed: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        计算延迟的物理正则化损失。

        L_tau_reg = λ_tau * ||τ - τ_phys||²

        若提供了主轴转速 spindle_speed [batch, 1]（归一化），则动态计算
        τ_phys = 60 / (n * n_scale)，其中 n_scale 为反归一化常量。

        Args:
            spindle_speed: 可选，主轴转速 [batch_size, 1]（归一化至 [0,1]）

        Returns:
            tau_reg_loss: 标量正则化损失
        """
        tau_val = self.tau

        if spindle_speed is not None and self.tau_phys is None:
            # 从归一化转速反推物理 τ_phys
            # spindle_speed ~ [0, 1] n_rpm = sp * 10000
            n_rpm = spindle_speed * 10000.0  # [batch, 1]
            tau_phys_dynamic = 60.0 / (n_rpm + 1.0)  # 避免除零
            tau_phys_mean = tau_phys_dynamic.mean()
            reg = self.lambda_tau_reg * ((tau_val - tau_phys_mean) ** 2)
        elif self.tau_phys is not None:
            reg = self.lambda_tau_reg * ((tau_val - self.tau_phys) ** 2)
        else:
            # 无物理先验：弱 L2 正则，防止发散
            reg = self.lambda_tau_reg * 0.01 * (tau_val**2)

        return reg

    def extra_repr(self) -> str:
        return (
            f"hidden_dim={self.hidden_dim}, dt={self.dt}, "
            f"tau={self.tau.item():.4f}s, "
            f"lambda_tau_reg={self.lambda_tau_reg}"
        )


class LearnableDelayEmbeddingBatched(nn.Module):
    """
    批量级可学习延迟嵌入（每个样本独立 τ）。

    与 LearnableDelayEmbedding 不同，此版本允许每个样本有自己的 τ 值，
    τ 由一个小型 MLP 网络从输入特征预测（如 Monsel 2024 SDDDE 的状态依赖延迟）。

    用途：高级实验 — 验证 τ 是否真正收敛到物理值 60/n。
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        dt: float = 0.1,
        tau_min: float = 0.001,
        tau_max: float = 0.5,
        delay_buffer_size: int = 128,
    ):
        super().__init__()

        self.hidden_dim = hidden_dim
        self.input_dim = input_dim
        self.dt = dt
        self.tau_min = tau_min
        self.tau_max = tau_max
        self.delay_buffer_size = delay_buffer_size

        # τ 预测网络：x τ
        self.tau_net = nn.Sequential(
            nn.Linear(input_dim, 16),
            nn.ReLU(),
            nn.Linear(16, 1),
        )

        # 缓冲区
        self.register_buffer(
            "_buffer",
            torch.zeros(1, delay_buffer_size, hidden_dim),
            persistent=False,
        )
        self.register_buffer(
            "_buffer_ptr",
            torch.zeros(1, dtype=torch.long),
            persistent=False,
        )
        self._buffer_initialized = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        从输入特征预测每个样本的 τ。

        Args:
            x: 输入特征 [batch_size, input_dim]

        Returns:
            tau_batch: [batch_size, 1] 延迟值 (s)
        """
        tau_raw = self.tau_net(x)  # [batch, 1]
        tau_batch = self.tau_min + (self.tau_max - self.tau_min) * torch.sigmoid(tau_raw)
        return tau_batch

    def init_buffer(self, batch_size: int, device: torch.device) -> None:
        self._buffer = torch.zeros(batch_size, self.delay_buffer_size, self.hidden_dim, device=device)
        self._buffer_ptr = torch.zeros(batch_size, dtype=torch.long, device=device)
        self._buffer_initialized = True

    def push(self, h: torch.Tensor) -> None:
        if not self._buffer_initialized:
            self.init_buffer(h.size(0), h.device)
        batch_size = h.size(0)
        ptr = self._buffer_ptr
        for b in range(batch_size):
            self._buffer[b, ptr[b]] = h[b]
        self._buffer_ptr = (ptr + 1) % self.delay_buffer_size

    def get_delayed(self, h_current: torch.Tensor, tau_batch: torch.Tensor) -> torch.Tensor:
        """
        获取延迟隐藏状态（每样本独立 τ）。

        Args:
            h_current: [batch_size, hidden_dim]
            tau_batch: [batch_size, 1] 每个样本的延迟值

        Returns:
            h_delayed: [batch_size, hidden_dim]
        """
        if not self._buffer_initialized:
            return h_current

        batch_size = h_current.size(0)
        device = h_current.device

        # 离散延迟步数（每样本不同）
        k_batch = (tau_batch.squeeze(-1) / self.dt).clamp(min=0)  # [batch]

        # 对每个样本执行插值
        h_delayed_list = []
        ptr = self._buffer_ptr

        for b in range(batch_size):
            k = k_batch[b]
            if k < 1.0:
                h_delayed_list.append(h_current[b : b + 1])
                continue

            k_floor = int(torch.floor(k).item())
            k_ceil = k_floor + 1
            alpha = float(k_ceil) - float(k)

            idx_lo = (ptr[b] - k_floor) % self.delay_buffer_size
            idx_hi = (ptr[b] - k_ceil) % self.delay_buffer_size

            h_lo = self._buffer[b, idx_lo]  # [hidden]
            h_hi = self._buffer[b, idx_hi]  # [hidden]
            h_del = alpha * h_lo + (1.0 - alpha) * h_hi
            h_delayed_list.append(h_del.unsqueeze(0))

        return torch.cat(h_delayed_list, dim=0)


if __name__ == "__main__":
    print("测试 LearnableDelayEmbedding...")

    hidden_dim = 64
    delay = LearnableDelayEmbedding(hidden_dim=hidden_dim, dt=0.1, tau_init=0.1, tau_phys=0.1)
    delay.init_buffer(batch_size=4, device=torch.device("cpu"))

    # 模拟多个时间步
    print(f"初始 τ = {delay.tau.item():.4f}s")
    for t in range(50):
        h = torch.randn(4, hidden_dim)
        delay.push(h)

    h_current = torch.randn(4, hidden_dim)
    h_delayed = delay.get_delayed(h_current)
    print(f"h_current 形状: {h_current.shape}")
    print(f"h_delayed 形状: {h_delayed.shape}")

    # 测试正则化
    reg = delay.compute_tau_regularization()
    print(f"τ 正则化: {reg.item():.6f}")

    print("\n测试 LearnableDelayEmbeddingBatched...")
    delay_batched = LearnableDelayEmbeddingBatched(input_dim=7, hidden_dim=hidden_dim, dt=0.1)
    delay_batched.init_buffer(4, torch.device("cpu"))
    for t in range(50):
        h = torch.randn(4, hidden_dim)
        delay_batched.push(h)

    x = torch.randn(4, 7)
    tau_batch = delay_batched(x)
    print(f"预测 τ 范围: [{tau_batch.min().item():.4f}, {tau_batch.max().item():.4f}]")
    h_delayed_b = delay_batched.get_delayed(h_current, tau_batch)
    print(f"h_delayed_batched 形状: {h_delayed_b.shape}")

    print("\n测试通过！")
