"""跨模态融合层：把几何 embedding 与动力学 embedding 融合为统一状态表示。

借鉴 GUSH3R 用统一原语表示异质对象的思想，灵境制造的「几何」与「动力学」
两类异质状态在各自投影到 d_model 维空间后，需经融合层输出 fused_embedding，
作为 WorldModelNet 时序预测（LSTM+LTC）的输入。

工程边界
========
- v1 用 Concat + MLP 融合，不引入 Cross-Attention（避免训练不稳定 + 参数膨胀）
- 输出维度 fused_dim=128，与 ADR-017 WorldModelConfig.input_dim 对齐
- 不替换 ADR-017 的 LSTM+LTC 混合架构，仅在输入层替换字段拼接为融合 embedding
- 融合后的 embedding 同时写回 UnifiedState.fused_embedding 字段，
  便于上层 plugin.py 序列化与调试

输入张量形状
============
- geometry_emb: (batch, d_model)
- dynamics_emb: (batch, d_model)

输出张量形状
============
- (batch, fused_dim)，默认 fused_dim=128

设计权衡
========
v1 选择 Concat+MLP 而非 Cross-Attention 的理由：
1. 几何与动力学是「同时刻配对」的两组特征，不存在长序列依赖，Attention 优势不明显
2. Concat+MLP 参数量小（d_model*2 → 256 → fused_dim），适合 PHM2010 量级数据集
3. Cross-Attention 在小数据上易过拟合，与 ADR-017 v1 仅离线 RL 的定位不符
4. 后续 v2 如需升级，可在保持本接口不变的前提下替换为 Cross-Attention
"""

from __future__ import annotations

import torch
import torch.nn as nn


class FusionLayer(nn.Module):
    """跨模态融合层：几何 embedding + 动力学 embedding → 统一状态 embedding。

    输入：geometry_emb (batch, d_model) + dynamics_emb (batch, d_model)
    输出：(batch, fused_dim) 融合 embedding

    网络结构：Concat([geometry_emb, dynamics_emb], dim=-1)
              → Linear(d_model*2 → 256) → ReLU → Linear(256 → fused_dim)
    """

    def __init__(self, d_model: int = 64, fused_dim: int = 128) -> None:
        super().__init__()
        self.fuse = nn.Sequential(
            nn.Linear(d_model * 2, 256),
            nn.ReLU(),
            nn.Linear(256, fused_dim),
        )

    def forward(
        self,
        geometry_emb: torch.Tensor,
        dynamics_emb: torch.Tensor,
    ) -> torch.Tensor:
        """前向传播。

        Args:
            geometry_emb: (batch, d_model) 几何 embedding
            dynamics_emb: (batch, d_model) 动力学 embedding

        Returns:
            (batch, fused_dim) 融合后的统一状态 embedding
        """
        concat = torch.cat([geometry_emb, dynamics_emb], dim=-1)
        return self.fuse(concat)
