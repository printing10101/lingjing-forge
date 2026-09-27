"""动力学 embedding 生成器：把 DynamicsState 投影到统一 embedding 空间。

借鉴 GUSH3R 用统一原语表示异质对象的思想，灵境制造的「切削动力学」状态
需投影到与几何 embedding 相同维度的统一空间，供 FusionLayer 跨模态融合。

工程边界
========
- 输入特征来自 ADR-013 颤振预测的 6 维切削动力学向量：
  (spindle_speed, feed_rate, depth_of_cut, tool_wear, vibration_rms, temperature)
- 输出维度 d_model=64，与 GeometryEncoder 对齐
- 不修改 ADR-013 的颤振预测输出（保持向后兼容）
- 仅做 MLP 投影，不引入时序建模（时序建模在 WorldModelNet 的 LSTM+LTC 层）

输入张量形状
============
- dynamics_tensor: (batch, 6)

输出张量形状
============
- (batch, d_model)，默认 d_model=64

工程现实约束
============
物理量纲差异较大（rpm / mm·min⁻¹ / mm / g / °C），训练前必须在数据预处理
阶段做标准化（z-score 或 min-max）。本模块假设输入已标准化，不做内置归一化，
避免与数据加载器的归一化逻辑重复或冲突。
"""

from __future__ import annotations

import torch
import torch.nn as nn


class DynamicsEncoder(nn.Module):
    """切削动力学状态 → 统一 embedding 空间。

    输入：DynamicsState 的张量化表示（6 维）
    输出：d_model 维动力学 embedding

    网络结构：Linear(6 → 128) → ReLU → Linear(128 → d_model)
    """

    def __init__(self, d_model: int = 64) -> None:
        super().__init__()
        # 6 维切削动力学输入：主轴转速、进给、切深、磨损、振动RMS、温度
        self.proj = nn.Sequential(
            nn.Linear(6, 128),
            nn.ReLU(),
            nn.Linear(128, d_model),
        )

    def forward(self, dynamics_tensor: torch.Tensor) -> torch.Tensor:
        """前向传播。

        Args:
            dynamics_tensor: (batch, 6) 切削动力学张量（假设已标准化）

        Returns:
            (batch, d_model) 动力学 embedding
        """
        return self.proj(dynamics_tensor)
