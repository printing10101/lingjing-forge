"""3D几何参数回归头模块。

双分支结构输出：
1. 边界框回归分支：预测3D边界框（x,y,z中心点坐标 + 长,宽,高尺寸）
2. 特征点定位分支：预测关键几何特征点的三维坐标

Key components:
    - BBoxHead: 3D边界框回归头
    - KeypointHead: 关键特征点定位头
    - GeometryHead: 组合回归头

Example:
    >>> head = GeometryHead(feature_dim=512)
    >>> bbox, keypoints = head(fused_features)
"""

import torch
import torch.nn as nn
from typing import Tuple


class BBoxHead(nn.Module):
    """3D边界框回归头。

    预测零件的3D包围盒参数：(cx, cy, cz, l, w, h)
    - cx, cy, cz: 3D中心点坐标（mm）
    - l, w, h: 长度、宽度、高度（mm）

    Attributes:
        fc1: 第一全连接层
        fc2: 第二全连接层
        fc_out: 输出层
    """

    def __init__(
        self,
        feature_dim: int = 512,
        hidden_dim: int = 256,
        output_dim: int = 6,
        dropout: float = 0.1,
    ):
        """初始化边界框回归头。

        Args:
            feature_dim: 输入特征维度
            hidden_dim: 隐藏层维度
            output_dim: 输出维度（6: cx,cy,cz,l,w,h）
            dropout: dropout率
        """
        super().__init__()
        self.fc1 = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.fc2 = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.BatchNorm1d(hidden_dim // 2),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.fc_out = nn.Linear(hidden_dim // 2, output_dim)

        self._init_weights()

    def _init_weights(self) -> None:
        """初始化权重。"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：预测3D边界框参数。

        Args:
            x: 融合特征 (B, feature_dim) 或 (B, N, feature_dim)

        Returns:
            边界框参数 (B, 6): [cx, cy, cz, l, w, h]
        """
        if x.dim() == 3:
            x = x.mean(dim=1)  # 平均池化到 (B, feature_dim)

        x = self.fc1(x)
        x = self.fc2(x)
        x = self.fc_out(x)

        # 确保尺寸值为正（l, w, h > 0）
        center = x[:, :3]
        size = torch.abs(x[:, 3:6])
        return torch.cat([center, size], dim=-1)


class KeypointHead(nn.Module):
    """关键特征点定位头。

    预测至少10个关键几何特征点的3D坐标：
    孔中心、槽中心、凸台中心等。

    Attributes:
        fc1: 第一全连接层
        fc2: 第二全连接层
        fc_out: 输出层
    """

    def __init__(
        self,
        feature_dim: int = 512,
        hidden_dim: int = 512,
        num_keypoints: int = 10,
        dropout: float = 0.1,
    ):
        """初始化特征点定位头。

        Args:
            feature_dim: 输入特征维度
            hidden_dim: 隐藏层维度
            num_keypoints: 特征点数量（默认10）
            dropout: dropout率
        """
        super().__init__()
        self.num_keypoints = num_keypoints
        self.output_dim = num_keypoints * 3

        self.fc1 = nn.Sequential(
            nn.Linear(feature_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.fc2 = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
        )
        self.fc_out = nn.Linear(hidden_dim, self.output_dim)

        self._init_weights()

    def _init_weights(self) -> None:
        """初始化权重。"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：预测关键特征点坐标。

        Args:
            x: 融合特征 (B, feature_dim)

        Returns:
            特征点坐标 (B, num_keypoints, 3)
        """
        if x.dim() == 3:
            x = x.mean(dim=1)

        x = self.fc1(x)
        x = self.fc2(x)
        x = self.fc_out(x)

        # 重塑为 (B, num_keypoints, 3)
        return x.view(-1, self.num_keypoints, 3)


class GeometryHead(nn.Module):
    """组合几何参数回归头。

    包含边界框回归和特征点定位两个分支。

    Attributes:
        bbox_head: 边界框回归头
        keypoint_head: 特征点定位头
    """

    def __init__(
        self,
        feature_dim: int = 512,
        num_keypoints: int = 10,
        dropout: float = 0.1,
    ):
        """初始化几何参数回归头。

        Args:
            feature_dim: 输入特征维度（默认512）
            num_keypoints: 特征点数量（默认10）
            dropout: dropout率
        """
        super().__init__()
        self.bbox_head = BBoxHead(feature_dim, dropout=dropout)
        self.keypoint_head = KeypointHead(
            feature_dim, num_keypoints=num_keypoints, dropout=dropout,
        )

    def forward(
        self,
        fused_features: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """前向传播：预测3D几何参数。

        Args:
            fused_features: 融合后的三视图特征 (B, D) 或 (B, N, D)

        Returns:
            bbox_pred: 预测的边界框参数 (B, 6)
            keypoints_pred: 预测的特征点坐标 (B, num_keypoints, 3)
        """
        bbox_pred = self.bbox_head(fused_features)
        keypoints_pred = self.keypoint_head(fused_features)
        return bbox_pred, keypoints_pred
