"""TorchMambaLNN：状态空间模型（SSM）预测 backbone（Phase 3a：④ Mamba 思路）。

借鉴 Mamba（state-spaces/mamba）的架构思想，实现为 BaseLNN 一族：

- **S4 风格（LTI，``selective=False``）**：对角 A 的线性状态空间，ZOH 离散化，参数恒定；
- **Mamba 风格（selective，默认）**：时间常数 Δ 随输入变化
  （``dt_eff = softplus(proj(x))``）——即 Mamba 选择性扫描的核心 + LNN
  液态时间常数 τ(x) 的语义，与你的 LNN/LTC 研究主线同源。

状态空间单元（输出维 = 状态维 H，C ∈ R^{H×H}）：
    h' = Ā ⊙ h + B̄ x        （Ā = exp(a·dt)，B̄ = (expm1(a·dt)/a)·b，对角 A 恒负）
    y  = C h + D x

降级策略（重要）：``mamba_ssm`` 在 Windows/国内环境通常无法编译安装，
故本实现为**纯 PyTorch**（无额外依赖）；若环境装有 ``mamba_ssm``，
可用 ``native_mamba_available()`` 探测并按需替换。

接口：
- 实现 BaseLNN 抽象（``forward(x, dt, hidden)`` 单步 + ``init_hidden``）；
- 额外提供 ``forward_sequence(x_seq, dt)`` 序列模式，供训练直接使用。
"""

from __future__ import annotations

import logging
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from .torch_base_lnn import BaseLNN, LNNConfig  # 相对导入：兼容 research 直跑与 engineering 桥接

logger = logging.getLogger(__name__)

__all__ = ["SSMCell", "TorchMambaLNN"]


def _safe_expm1_div(a: torch.Tensor, dt: torch.Tensor) -> torch.Tensor:
    """ZOH 积分系数：(exp(a*dt) - 1) / a，数值稳定处理 a→0。

    ONNX 导出兼容：torch.expm1 在 opset 17 不受支持，改用 exp(x)-1（等价值，
    由 where 守卫保证 |a·dt| 极小处走 dt 分支，精度无损）。
    """
    ax = a * dt
    return torch.where(ax.abs() < 1e-6, dt, (torch.exp(ax) - 1.0) / a)


class SSMCell(nn.Module):
    """状态空间单元：h' = Ā ⊙ h + B̄ x；y = C h + D x（输出维 = 状态维 H）。

    - A 为对角负参数（exp 参数化保证稳定）；
    - selective=True：Δ（时间常数）由输入投影生成（Mamba 选择性 + 液态 τ(x)）；
    - selective=False：LTI（S4 风格）。
    """

    def __init__(self, input_size: int, hidden_size: int, selective: bool = True) -> None:
        super().__init__()
        self.input_size = input_size
        self.hidden_size = hidden_size
        self.selective = selective

        # A 的对数尺度参数：a_eff = -exp(a_log)（恒负，保证状态稳定）
        self.a_log = nn.Parameter(torch.rand(hidden_size).mul(0.5).add(0.5))
        self.b = nn.Parameter(torch.randn(hidden_size, input_size).mul(0.05))
        self.c = nn.Parameter(torch.randn(hidden_size, hidden_size).mul(0.05))
        self.d = nn.Parameter(torch.randn(hidden_size, input_size).mul(0.05))

        if selective:
            self.dt_proj = nn.Linear(input_size, hidden_size)

    def step(
        self, x: torch.Tensor, h: torch.Tensor, dt: float | torch.Tensor = 0.01
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """单步状态更新。

        Args:
            x: (B, F) 当前输入
            h: (B, H) 上一步状态
            dt: 基线时间步长（selective 时 softplus 输出叠加）

        Returns:
            (y (B, H), h_new (B, H))
        """
        batch = x.shape[0]
        a_eff = -torch.exp(self.a_log)  # (H,)

        # 离散化时间步长
        if self.selective:
            dt_eff = F.softplus(self.dt_proj(x)) + 1e-4  # (B, H) 输入相关时间常数
        else:
            dt_eff = torch.full((batch, self.hidden_size), float(dt), device=x.device, dtype=x.dtype)

        # Ā = exp(a·dt) ∈ (B,H)；B̄ = (expm1(a·dt)/a) · b ∈ (B,H,F)
        a_dt = a_eff * dt_eff  # (B,H)
        a_bar = torch.exp(a_dt)
        coeff = _safe_expm1_div(a_eff, dt_eff)  # (B,H)
        b_bar = coeff.unsqueeze(-1) * self.b.unsqueeze(0)  # (B,1,H)*(1,H,F) → (B,H,F)

        h_new = a_bar * h + (b_bar @ x.unsqueeze(-1)).squeeze(-1)  # (B,H)
        y = (self.c @ h_new.unsqueeze(-1)).squeeze(-1) + (self.d @ x.unsqueeze(-1)).squeeze(-1)  # (B,H)
        return y, h_new


class TorchMambaLNN(BaseLNN):
    """SSM 预测 backbone（BaseLNN 一族，纯 PyTorch，无 mamba-ssm 依赖）。

    结构：输入 → num_layers × SSMCell（选择性 SSM）→ Dropout → Linear 预测头。
    output_size=1：连续强度回归；output_size=2：强度 + 颤振二分类 logit。
    """

    def __init__(self, config: LNNConfig, selective: bool = True) -> None:
        super().__init__(config)
        self.model_name = "TorchMambaLNN"
        self.selective = selective
        self.num_layers = config.num_layers

        cells = []
        in_size = config.input_size
        for _ in range(config.num_layers):
            cells.append(SSMCell(in_size, config.hidden_size, selective=selective))
            in_size = config.hidden_size
        self.layers = nn.ModuleList(cells)
        self.head = nn.Linear(config.hidden_size, config.output_size)
        self.dropout = nn.Dropout(config.dropout)
        logger.info(
            "TorchMambaLNN 初始化: layers=%d hidden=%d selective=%s",
            config.num_layers,
            config.hidden_size,
            selective,
        )

    # BaseLNN 抽象接口
    def init_hidden(self, batch_size: int) -> torch.Tensor:
        """初始状态 (num_layers, batch, hidden)。"""
        return torch.zeros(self.num_layers, batch_size, self.config.hidden_size, device=self.device)

    def forward(
        self,
        x: torch.Tensor,
        dt: float,
        hidden_state: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """单步前向（BaseLNN 契约）。

        Args:
            x: (batch, input_size)
            dt: 时间步长
            hidden_state: (num_layers, batch, hidden)

        Returns:
            (output (batch, output_size), hidden_state')
        """
        return self.step(x, hidden_state, dt)

    # 便捷接口
    def step(
        self,
        x: torch.Tensor,
        h: torch.Tensor,
        dt: float | torch.Tensor = 0.01,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """逐层状态更新。h: (L, B, H)。返回 (y (B, out), h_new (L, B, H))。"""
        inp = x
        h_new_layers: list[torch.Tensor] = []
        for i, cell in enumerate(self.layers):
            inp, h_i = cell.step(inp, h[i], dt)
            h_new_layers.append(h_i)
        h_stack = torch.stack(h_new_layers, dim=0)
        y = self.head(self.dropout(inp))
        return y, h_stack

    def forward_sequence(
        self,
        x_seq: torch.Tensor,
        dt: float | torch.Tensor = 0.01,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """序列前向（训练用）：逐时间步扫描状态空间。

        Args:
            x_seq: (batch, time, input_size)
            dt: 时间步长

        Returns:
            (outputs (batch, time, output_size), final_hidden (L, batch, hidden))
        """
        batch, time, _ = x_seq.shape
        h = self.init_hidden(batch).to(x_seq.device)
        outputs: list[torch.Tensor] = []
        for t in range(time):
            y, h = self.step(x_seq[:, t], h, dt)
            outputs.append(y)
        return torch.stack(outputs, dim=1), h

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["config"]["selective"] = self.selective
        info["architecture"] = f"SSM({self.num_layers}L x {self.config.hidden_size})"
        return info


# 兼容：若环境安装了原生 mamba_ssm，可在此选择使用（默认走纯 PyTorch 实现）
def native_mamba_available() -> bool:
    try:
        import mamba_ssm  # noqa: F401

        return True
    except ImportError:
        return False
