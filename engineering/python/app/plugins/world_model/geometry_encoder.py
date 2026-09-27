"""几何 embedding 生成器：把 GeometryFeatures 投影到统一 embedding 空间。

借鉴 GUSH3R 用统一原语表示异质对象的思想，灵境制造的「几何」与「动力学」
两类异质状态需先各自投影到统一 embedding 空间，再由 FusionLayer 跨模态融合。

工程边界
========
- 输入特征来自 ADR-007 几何特征提取（平面/圆柱/孔统计向量），
  本模块只做 MLP 投影，不重新学习几何特征（v1 冻结下游特征提取器）
- 输出维度固定 d_model=64，与 DynamicsEncoder 对齐，便于 Concat 融合
- 不引入 3DGS（与工业 CAD 表示不兼容，GUSH3R 评估已否决）
- 仅在 WorldModelNet 输入层做融合，不替换 ADR-017 的 LSTM+LTC 架构

输入张量形状
============
- geometry_tensor: (batch, input_dim)
  其中 input_dim = bbox(3) + feature_vector(feature_dim) + symmetry(1) + complexity(1)
  默认 feature_dim=32 → input_dim=37

输出张量形状
============
- (batch, d_model)，默认 d_model=64
"""

from __future__ import annotations

import torch
import torch.nn as nn


class GeometryEncoder(nn.Module):
    """几何特征 → 统一 embedding 空间。

    输入：GeometryFeatures 的张量化表示
    输出：d_model 维几何 embedding

    网络结构：Linear(input_dim → 128) → ReLU → Linear(128 → d_model)
    """

    def __init__(self, feature_dim: int = 32, d_model: int = 64) -> None:
        super().__init__()
        # bbox(3) + feature_vector(feature_dim) + symmetry(1) + complexity(1)
        input_dim = 3 + feature_dim + 1 + 1
        self.proj = nn.Sequential(
            nn.Linear(input_dim, 128),
            nn.ReLU(),
            nn.Linear(128, d_model),
        )

    def forward(self, geometry_tensor: torch.Tensor) -> torch.Tensor:
        """前向传播。

        Args:
            geometry_tensor: (batch, input_dim) 几何特征张量

        Returns:
            (batch, d_model) 几何 embedding
        """
        return self.proj(geometry_tensor)
