"""
DL-LNN v2 模型 （Phase 1 重构）

核心改动 vs 原 models.py:
    1. 延迟嵌入：集成 LearnableDelayEmbedding，替代缺失的 T=60/n 固定延迟
    2. 长时间预测：输出从 [B,1] 改为 [B, prediction_horizon]（时序轨迹预测）
    3. DDE 语义：隐藏状态演化真正包含延迟项 h(t-τ)

架构：
    DLLNNModelV2:
        input [B, 7]
          → input_proj
          → LTC Layer 1:  dh/dt = f(h, h(t-τ₁), x)  ← 延迟嵌入
          → LTC Layer 2:  dh/dt = f(h, h(t-τ₂), x)  ← 延迟嵌入
          → LTC Layer 3:  dh/dt = f(h, h(t-τ₃), x)  ← 延迟嵌入
          → horizon_head  →  output [B, horizon]
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Tuple, Optional, List
import sys
import os

# 导入原代码库模块
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from experiments.models import (
    LTCODEFunc, LTCCell, DifferentiableTlustyPhysics,
    _HAS_TORCHDIFFEQ,
)
from delay_embedding import LearnableDelayEmbedding


class LTCWithDelayCell(nn.Module):
    """
    带延迟嵌入的 LTC 单元。

    在原 LTCCell 的基础上，将延迟隐藏状态 h(t-τ) 作为附加输入：
        dh/dt = ( -h + tanh(W·x + U·h + V·h_delayed + b) ) / tau

    这种形式将 ODE → DDE，是延迟微分网络的忠实实现。

    Args:
        input_size: 输入维度
        hidden_size: 隐藏层维度
        dt: 积分步长
        tau_init: 延迟初始值 (s)
        solver: ODE 求解器方法
    """

    def __init__(
        self,
        input_size: int,
        hidden_size: int,
        dt: float = 0.1,
        tau_init: float = 0.1,
        solver: str = "dopri5",
        delay_buffer_size: int = 128,
    ):
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.dt = dt
        self.solver = solver

        # 标准 LTC ODE 参数
        self.ode_func = LTCODEFunc(input_size, hidden_size)

        # 延迟反馈权重 V（新增）
        self.V = nn.Parameter(torch.randn(hidden_size, hidden_size) * 0.01)
        nn.init.xavier_uniform_(self.V)

        # 可学习延迟嵌入
        self.delay_embedding = LearnableDelayEmbedding(
            hidden_dim=hidden_size,
            dt=dt,
            tau_init=tau_init,
            tau_phys=None,  # 由 trainer 动态设置
            lambda_tau_reg=0.01,
            delay_buffer_size=delay_buffer_size,
        )

        self._current_x: Optional[torch.Tensor] = None
        self._current_h_delayed: Optional[torch.Tensor] = None

    @property
    def W(self) -> nn.Parameter:
        return self.ode_func.W

    @property
    def U(self) -> nn.Parameter:
        return self.ode_func.U

    @property
    def bias(self) -> nn.Parameter:
        return self.ode_func.bias

    def set_context(self, x: torch.Tensor, h_delayed: torch.Tensor) -> None:
        """设置 ODE 求解所需的上下文状态。"""
        self._current_x = x
        self._current_h_delayed = h_delayed

    def forward(self, x: torch.Tensor, h: torch.Tensor, h_delayed: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        前向传播（带延迟 DDE）。

        若提供 h_delayed，则将其注入动力学方程。
        若未提供，退化为标准 LTC（无延迟项）。

        Args:
            x: 输入 [batch_size, input_size]
            h: 当前隐藏状态 [batch_size, hidden_size]
            h_delayed: 延迟隐藏状态 [batch_size, hidden_size]，可选

        Returns:
            新的隐藏状态 [batch_size, hidden_size]
        """
        # 更新延迟缓冲区
        self.delay_embedding.push(h)

        if h_delayed is None or not _HAS_TORCHDIFFEQ:
            # 退化为标准 LTC（无延迟或无 ODE 求解器）
            tau = torch.clamp(self.ode_func.tau, min=0.01)
            Vh_delayed = 0.0
            if h_delayed is not None:
                Vh_delayed = torch.mm(h_delayed, self.V.t())

            dh = torch.tanh(
                torch.mm(x, self.ode_func.W.t())
                + torch.mm(h, self.ode_func.U.t())
                + Vh_delayed
                + self.ode_func.bias
            )
            h_new = h + self.dt * (dh - h) / tau.unsqueeze(0)
            return h_new

        # 连续时间 ODE 积分
        if h_delayed is not None:
            self.set_context(x, h_delayed)
        else:
            self.set_context(x, torch.zeros_like(h))

        self.ode_func.set_input(x)

        # 重写 ode_func.forward 以注入延迟项
        # 使用闭包捕获 h_delayed + V
        original_forward = self.ode_func.forward

        def dde_forward(t_tensor, h_tensor):
            x_val = self.ode_func._current_x
            tau_val = torch.clamp(self.ode_func.tau, min=0.01)
            Vh = 0.0
            if self._current_h_delayed is not None:
                Vh = torch.mm(self._current_h_delayed, self.V.t())
            dh_val = torch.tanh(
                torch.mm(x_val, self.ode_func.W.t())
                + torch.mm(h_tensor, self.ode_func.U.t())
                + Vh
                + self.ode_func.bias
            )
            return (dh_val - h_tensor) / tau_val.unsqueeze(0)

        self.ode_func.forward = dde_forward

        from torchdiffeq import odeint
        t_span = torch.tensor([0.0, self.dt], device=h.device, dtype=h.dtype)
        h_new = odeint(self.ode_func, h, t_span, method=self.solver)[-1]

        # 恢复原始 forward
        self.ode_func.forward = original_forward

        return h_new

    def get_delayed(self, h_current: torch.Tensor) -> torch.Tensor:
        """获取延迟隐藏状态 h(t-τ)。"""
        return self.delay_embedding.get_delayed(h_current)


class DLLNNModelV2(nn.Module):
    """
    DL-LNN v2 模型：可学习延迟 + 长时间序列预测。

    每层 LTC 都配备独立的可学习延迟嵌入 τ_i。
    输出层预测未来 `prediction_horizon` 帧的时序轨迹。

    Args:
        input_dim: 输入特征维度（默认 7）
        hidden_dim: 隐藏层维度
        num_layers: LTC 层数
        prediction_horizon: 预测未来帧数
        dt: 积分步长
        dropout: Dropout 率
        tau_init: 延迟初始值 (s)
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 128,
        num_layers: int = 3,
        prediction_horizon: int = 50,
        dt: float = 0.1,
        dropout: float = 0.2,
        tau_init: float = 0.1,
        delay_buffer_size: int = 128,
    ):
        super().__init__()

        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.prediction_horizon = prediction_horizon
        self.dt = dt

        # 输入投影
        self.input_proj = nn.Linear(input_dim, hidden_dim)

        # LTC 层（每层带独立延迟）
        self.ltc_cells = nn.ModuleList([
            LTCWithDelayCell(
                hidden_dim, hidden_dim,
                dt=dt, tau_init=tau_init, delay_buffer_size=delay_buffer_size,
            )
            for _ in range(num_layers)
        ])

        # 长时间预测头
        self.horizon_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, prediction_horizon),
        )

        # 用于消融实验的辅助预测头（可选，用于兼容性）
        self.scalar_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 1),
        )

    @property
    def all_taus(self) -> List[torch.Tensor]:
        """获取所有层的当前 τ 值。"""
        return [cell.delay_embedding.tau for cell in self.ltc_cells]

    def forward(self, x: torch.Tensor, use_horizon: bool = True) -> torch.Tensor:
        """
        前向传播。

        Args:
            x: 输入特征 [batch_size, input_dim]
            use_horizon: True=返回 [B, horizon]，False=返回 [B, 1]（兼容）

        Returns:
            output: [batch_size, prediction_horizon] 或 [batch_size, 1]
        """
        batch_size = x.size(0)
        device = x.device

        # 输入投影
        h = self.input_proj(x)

        # 通过 LTC 层（每层包含延迟嵌入）
        for layer_idx, ltc_cell in enumerate(self.ltc_cells):
            # 初始化隐藏状态
            h_state = torch.zeros(batch_size, self.hidden_dim, device=device)

            # 获取延迟隐藏状态
            h_delayed = ltc_cell.get_delayed(h_state)

            # LTC + 延迟前向
            h_state = ltc_cell(h, h_state, h_delayed)
            h = h_state

        # 输出投影
        if use_horizon:
            output = self.horizon_head(h)  # [B, horizon]
        else:
            output = self.scalar_head(h)   # [B, 1]

        return output

    def reset_buffers(self) -> None:
        """重置所有延迟缓冲区（每个 epoch 开始时调用）。"""
        for cell in self.ltc_cells:
            cell.delay_embedding._buffer_initialized = False


class DLLNNWithPhysicsV2(nn.Module):
    """
    DL-LNN v2 完整模型（带物理分支 + 门控融合 + 长时间预测）。

    与 DLLNNWithPhysics 区别：
        - LTC 分支使用 DLLNNModelV2（延迟嵌入 + 长时间预测）
        - 门控融合在时间维度上操作
        - 仍保留可微 Tlusty 物理分支用于 L_pcc
    """

    def __init__(
        self,
        input_dim: int = 7,
        hidden_dim: int = 128,
        num_layers: int = 3,
        prediction_horizon: int = 50,
        dt: float = 0.1,
        dropout: float = 0.2,
        tau_init: float = 0.1,
        delay_buffer_size: int = 128,
    ):
        super().__init__()

        # 数据驱动分支（LTC + 延迟）
        self.ltc_branch = DLLNNModelV2(
            input_dim=input_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            prediction_horizon=prediction_horizon,
            dt=dt,
            dropout=dropout,
            tau_init=tau_init,
            delay_buffer_size=delay_buffer_size,
        )

        # 门控融合（长时间预测用，输出每步权重）
        self.gate = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Linear(32, prediction_horizon),
            nn.Sigmoid(),
        )

        # 门控融合（标量预测用）
        self.gate_scalar = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid(),
        )

        # 物理分支参数（可学习）
        self.physics_scale = nn.Parameter(torch.ones(1))
        self.physics_bias = nn.Parameter(torch.zeros(1))

        # 可微 Tlusty 解析物理分支
        self.physics_branch = DifferentiableTlustyPhysics()

        self.prediction_horizon = prediction_horizon

    @property
    def all_taus(self) -> List[torch.Tensor]:
        return self.ltc_branch.all_taus

    def compute_differentiable_physics(self, x: torch.Tensor) -> torch.Tensor:
        return self.physics_branch(x)

    def forward(
        self,
        x: torch.Tensor,
        physics_pred: Optional[torch.Tensor] = None,
        use_horizon: bool = True,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """
        前向传播。

        Args:
            x: [batch_size, input_dim]
            physics_pred: [batch_size, 1] 预计算物理预测（可选）
            use_horizon: True=长时间预测，False=标量

        Returns:
            final_pred: [B, horizon] 或 [B, 1]
            ltc_pred: [B, horizon] 或 [B, 1]
        """
        ltc_pred = self.ltc_branch(x, use_horizon=use_horizon)

        if physics_pred is None:
            return ltc_pred, ltc_pred

        # 扩展 physics_pred 到预测时域
        if use_horizon:
            physics_pred_expanded = physics_pred.repeat(1, self.prediction_horizon)
        else:
            physics_pred_expanded = physics_pred

        # 门控融合
        alpha = self.gate(x) if use_horizon else self.gate_scalar(x)

        if use_horizon:
            final_pred = alpha * ltc_pred + (1.0 - alpha) * (
                self.physics_scale * physics_pred_expanded + self.physics_bias
            )
        else:
            final_pred = alpha * ltc_pred + (1.0 - alpha) * (
                self.physics_scale * physics_pred_expanded + self.physics_bias
            )

        return final_pred, ltc_pred

    def reset_buffers(self) -> None:
        self.ltc_branch.reset_buffers()


if __name__ == "__main__":
    print("测试 DLLNNModelV2...")

    model = DLLNNModelV2(
        input_dim=7,
        hidden_dim=64,
        num_layers=3,
        prediction_horizon=50,
        dt=0.1,
        tau_init=0.1,
    )

    x = torch.randn(32, 7)
    output = model(x, use_horizon=True)
    print(f"输入: {x.shape}")
    print(f"输出 (horizon): {output.shape}")

    output_scalar = model(x, use_horizon=False)
    print(f"输出 (scalar): {output_scalar.shape}")

    taus = model.all_taus
    for i, t in enumerate(taus):
        print(f"  层 {i+1} τ = {t.item():.4f}s")

    total_params = sum(p.numel() for p in model.parameters())
    print(f"总参数量: {total_params:,}")

    print("\n测试 DLLNNWithPhysicsV2...")
    model_full = DLLNNWithPhysicsV2(
        input_dim=7, hidden_dim=64, num_layers=3, prediction_horizon=50,
    )
    x2 = torch.randn(16, 7)
    phys = torch.randn(16, 1)
    final, ltc = model_full(x2, physics_pred=phys, use_horizon=True)
    print(f"最终输出: {final.shape}, LTC 输出: {ltc.shape}")

    final_s, ltc_s = model_full(x2, physics_pred=phys, use_horizon=False)
    print(f"标量输出: {final_s.shape}, LTC 标量: {ltc_s.shape}")

    print("\n测试通过！")
