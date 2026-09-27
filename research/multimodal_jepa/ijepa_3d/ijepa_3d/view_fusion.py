"""三视图特征融合模块。

实现基于交叉注意力机制的三视图特征融合。
正交视图（正视/侧视/俯视）的特征通过交叉注意力进行融合，
权重分配：正视60%，侧视20%，俯视20%（可通过训练动态微调）。

Key components:
    - CrossAttentionFusion: 交叉注意力融合层
    - ViewFusion: 三视图融合模块

Example:
    >>> fusion = ViewFusion(embed_dim=512, num_heads=8)
    >>> fused = fusion(front_feat, side_feat, top_feat)
"""

import torch
import torch.nn as nn
from typing import Tuple


class CrossAttentionBlock(nn.Module):
    """交叉注意力块。

    实现query-view与key-value-view之间的交叉注意力计算。

    Attributes:
        norm_q: query的LayerNorm
        norm_kv: key/value的LayerNorm
        attn: 多头注意力
        norm_out: 输出的LayerNorm
        mlp: 前馈网络
    """

    def __init__(
        self,
        embed_dim: int = 512,
        num_heads: int = 8,
        dropout: float = 0.1,
    ):
        """初始化交叉注意力块。

        Args:
            embed_dim: 嵌入维度
            num_heads: 注意力头数
            dropout: dropout率
        """
        super().__init__()
        self.norm_q = nn.LayerNorm(embed_dim)
        self.norm_kv = nn.LayerNorm(embed_dim)
        self.attn = nn.MultiheadAttention(
            embed_dim, num_heads,
            dropout=dropout, batch_first=True,
        )
        self.norm_out = nn.LayerNorm(embed_dim)
        self.mlp = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * 4),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim * 4, embed_dim),
            nn.Dropout(dropout),
        )

    def forward(
        self,
        query: torch.Tensor,
        key_value: torch.Tensor,
    ) -> torch.Tensor:
        """前向传播：计算交叉注意力。

        Args:
            query: 查询序列 (B, N, D)
            key_value: 键值序列 (B, M, D)

        Returns:
            融合后的特征 (B, N, D)
        """
        q_norm = self.norm_q(query)
        kv_norm = self.norm_kv(key_value)

        attn_out, _ = self.attn(q_norm, kv_norm, kv_norm)
        x = query + attn_out  # 残差连接

        x = x + self.mlp(self.norm_out(x))  # FFN + 残差连接
        return x


class ViewFusion(nn.Module):
    """三视图特征融合模块。

    通过交叉注意力机制融合三个正交视图的特征。
    实现层次化融合策略：
    1. 以正视视图为query，侧视视图为key/value进行融合
    2. 以正视视图为query，俯视视图为key/value进行融合
    3. 加权合并所有视图特征

    Attributes:
        embed_dim: 嵌入维度
        front_cross_side: 正视->侧视交叉注意力
        front_cross_top: 正视->俯视交叉注意力
        fusion_proj: 融合后投影层
        front_weight: 正视视图权重（可学习）
        side_weight: 侧视视图权重（可学习）
        top_weight: 俯视视图权重（可学习）
    """

    def __init__(
        self,
        embed_dim: int = 512,
        num_heads: int = 8,
        dropout: float = 0.1,
        front_weight: float = 0.60,
        side_weight: float = 0.20,
        top_weight: float = 0.20,
    ):
        """初始化视图融合模块。

        Args:
            embed_dim: 嵌入维度（默认512）
            num_heads: 注意力头数（默认8）
            dropout: dropout率（默认0.1）
            front_weight: 正视视图初始权重（默认0.60）
            side_weight: 侧视视图初始权重（默认0.20）
            top_weight: 俯视视图初始权重（默认0.20）
        """
        super().__init__()
        self.embed_dim = embed_dim

        # 交叉注意力：以正视视图为query与其他视图交互
        self.front_cross_side = CrossAttentionBlock(embed_dim, num_heads, dropout)
        self.front_cross_top = CrossAttentionBlock(embed_dim, num_heads, dropout)

        # 融合投影
        self.fusion_proj = nn.Sequential(
            nn.Linear(embed_dim * 3, embed_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim * 2, embed_dim),
            nn.LayerNorm(embed_dim),
        )

        # 可学习的视图权重（初始化为指定比例）
        self.front_weight = nn.Parameter(
            torch.tensor(front_weight, dtype=torch.float32),
        )
        self.side_weight = nn.Parameter(
            torch.tensor(side_weight, dtype=torch.float32),
        )
        self.top_weight = nn.Parameter(
            torch.tensor(top_weight, dtype=torch.float32),
        )

        self.dropout = nn.Dropout(dropout)

    def forward(
        self,
        front_features: torch.Tensor,
        side_features: torch.Tensor,
        top_features: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """前向传播：融合三视图特征。

        Args:
            front_features: 正视视图特征 (B, N, D)
            side_features: 侧视视图特征 (B, N, D)
            top_features: 俯视视图特征 (B, N, D)

        Returns:
            fused_features: 融合后的特征 (B, N, D)
            view_weights: 归一化后的视图权重 (3,)
        """
        # 正视视图与侧视视图交互
        _ = self.front_cross_side(front_features, side_features)

        # 正视视图与俯视视图交互
        _ = self.front_cross_top(front_features, top_features)

        # 加权融合
        weights = torch.softmax(torch.stack([
            self.front_weight, self.side_weight, self.top_weight,
        ]), dim=0)

        # 拼接所有视图特征
        all_features = torch.cat([
            front_features * weights[0],
            side_features * weights[1],
            top_features * weights[2],
        ], dim=-1)  # (B, N, 3*D)

        # 融合投影
        fused = self.fusion_proj(all_features)
        fused = self.dropout(fused)

        return fused, weights.detach()

    def get_fused_global_feature(
        self,
        front_features: torch.Tensor,
        side_features: torch.Tensor,
        top_features: torch.Tensor,
    ) -> torch.Tensor:
        """获取融合后的全局特征向量。

        对所有patch特征进行平均池化得到全局表示。

        Args:
            front_features: 正视视图特征
            side_features: 侧视视图特征
            top_features: 俯视视图特征

        Returns:
            全局融合特征 (B, D)
        """
        fused, _ = self.forward(front_features, side_features, top_features)
        return fused.mean(dim=1)  # (B, D)
