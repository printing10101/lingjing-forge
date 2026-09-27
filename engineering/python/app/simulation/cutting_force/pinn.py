"""PINN 模型架构实现。

基于 PyTorch 的残差学习网络，用于切削力预测。
采用物理约束神经网络 (PINN) 架构，结合 Kienzle 解析公式作为物理先验。

模型设计：
    - 输入: [speed_norm, feed_norm, depth_norm] (归一化到 [0,1])
    - 残差学习: 网络学习 Kienzle 解析解的残差修正
    - 输出: [Fx, Fy, Fz] (N)
    - 参数量 < 100K
"""

from __future__ import annotations

import torch
import torch.nn as nn


class ResidualBlock(nn.Module):
    """残差块：x + F(x)。"""

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.block = nn.Sequential(
            nn.Linear(dim, dim),
            nn.ReLU(inplace=True),
            nn.Linear(dim, dim),
        )
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.relu(x + self.block(x))


class CuttingForcePINN(nn.Module):
    """切削力 PINN 模型。

    采用残差学习机制：
        1. 主干网络将输入映射到高维特征空间
        2. 残差块学习物理修正量
        3. 输出头预测三向切削力

    输入参数归一化范围 [0, 1]：
        - speed_norm: 主轴转速归一化 (范围: 500~20000 rpm)
        - feed_norm: 进给量归一化 (范围: 100~5000 mm/min)
        - depth_norm: 切深归一化 (范围: 0.1~5.0 mm)

    Args:
        input_dim: 输入维度，默认 3
        hidden_dim: 隐藏层维度，默认 64
        num_blocks: 残差块数量，默认 3
        output_dim: 输出维度，默认 3 (Fx, Fy, Fz)
    """

    # 输入参数归一化范围
    PARAM_RANGES: dict[str, tuple[float, float]] = {
        "speed": (500.0, 20000.0),
        "feed": (100.0, 5000.0),
        "depth": (0.1, 5.0),
    }

    def __init__(
        self,
        input_dim: int = 3,
        hidden_dim: int = 64,
        num_blocks: int = 3,
        output_dim: int = 3,
    ) -> None:
        super().__init__()

        # 输入映射层
        self.input_proj = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(inplace=True),
        )

        # 残差块序列
        self.res_blocks = nn.Sequential(*[ResidualBlock(hidden_dim) for _ in range(num_blocks)])

        # 输出头
        self.output_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(inplace=True),
            nn.Linear(hidden_dim // 2, output_dim),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        """使用 Xavier 初始化权重。"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_normal_(m.weight)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    @staticmethod
    def normalize_params(
        speed: float,
        feed: float,
        depth: float,
    ) -> torch.Tensor:
        """将切削参数归一化到 [0, 1] 区间。

        Args:
            speed: 主轴转速 (rpm)
            feed: 进给量 (mm/min)
            depth: 切深 (mm)

        Returns:
            归一化后的张量 [speed_norm, feed_norm, depth_norm]
        """
        ranges = CuttingForcePINN.PARAM_RANGES
        s_norm = (speed - ranges["speed"][0]) / (ranges["speed"][1] - ranges["speed"][0])
        f_norm = (feed - ranges["feed"][0]) / (ranges["feed"][1] - ranges["feed"][0])
        d_norm = (depth - ranges["depth"][0]) / (ranges["depth"][1] - ranges["depth"][0])

        s_norm = max(0.0, min(1.0, s_norm))
        f_norm = max(0.0, min(1.0, f_norm))
        d_norm = max(0.0, min(1.0, d_norm))

        return torch.tensor([[s_norm, f_norm, d_norm]], dtype=torch.float32)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播。

        Args:
            x: 输入张量，形状 (batch, 3)，值为归一化后的参数

        Returns:
            输出张量，形状 (batch, 3)，对应 [Fx, Fy, Fz]
        """
        features = self.input_proj(x)
        features = self.res_blocks(features)
        forces = self.output_head(features)
        # 确保输出为正值（切削力不可能为负）
        forces = torch.abs(forces)
        return forces

    def count_parameters(self) -> int:
        """统计模型可训练参数数量。"""
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


class PINNLoss(nn.Module):
    """PINN 混合损失函数。

    结合物理损失 (Kienzle 公式约束) 与数据损失 (MSE)。

    L = w_data * L_data + w_physics * L_physics

    Args:
        physics_weight: 物理损失权重，初始值 0.1
    """

    def __init__(self, physics_weight: float = 0.1) -> None:
        super().__init__()
        self.physics_weight = physics_weight
        self.mse_loss = nn.MSELoss()

    def forward(
        self,
        pred_forces: torch.Tensor,
        target_forces: torch.Tensor,
        kienzle_forces: torch.Tensor | None = None,
    ) -> dict[str, torch.Tensor]:
        """计算混合损失。

        Args:
            pred_forces: 神经网络预测的切削力 (batch, 3)
            target_forces: 目标切削力 (batch, 3)
            kienzle_forces: Kienzle 公式计算的切削力 (batch, 3)，可选

        Returns:
            包含 total_loss, data_loss, physics_loss 的字典
        """
        data_loss = self.mse_loss(pred_forces, target_forces)

        if kienzle_forces is not None:
            physics_loss = self.mse_loss(pred_forces, kienzle_forces)
        else:
            physics_loss = torch.tensor(0.0, device=pred_forces.device)

        total_loss = data_loss + self.physics_weight * physics_loss

        return {
            "total_loss": total_loss,
            "data_loss": data_loss,
            "physics_loss": physics_loss,
        }
