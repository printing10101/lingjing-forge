"""时空ViT编码器模块。

实现V-JEPA架构核心的时空Vision Transformer编码器：
- 将视频序列（16帧×224×224）分割为时空patch
- 时间patch大小=2帧，空间patch大小=16×16
- 12层时空Transformer，8头注意力，嵌入维度512
- 支持EMA权重更新防坍缩

输入: (B, T, C, H, W) = (B, 16, 3, 224, 224)
输出: (B, num_temporal_patches * num_spatial_patches, 512)

Key components:
    - SpatioTemporalPatchEmbed: 3D时空patch嵌入
    - SpatioTemporalTransformerBlock: 时空Transformer块
    - SpatioTemporalViT: 完整时空ViT编码器
"""

import torch
import torch.nn as nn
import copy
from typing import Optional


class SpatioTemporalPatchEmbed(nn.Module):
    """3D时空Patch嵌入层。

    将视频序列分割为时空patch并映射到嵌入空间。
    patch维度：时间2帧 × 空间16×16像素。

    Attributes:
        proj: 3D卷积投影层
        num_temporal_patches: 时间维度patch数
        num_spatial_patches: 空间维度patch数
        embed_dim: 嵌入维度
    """

    def __init__(
        self,
        num_frames: int = 16,
        frame_size: int = 224,
        temporal_patch_size: int = 2,
        spatial_patch_size: int = 16,
        in_channels: int = 3,
        embed_dim: int = 512,
    ):
        super().__init__()
        self.temporal_patch_size = temporal_patch_size
        self.spatial_patch_size = spatial_patch_size
        self.num_temporal_patches = num_frames // temporal_patch_size
        self.num_spatial_patches = (frame_size // spatial_patch_size) ** 2
        self.num_patches = self.num_temporal_patches * self.num_spatial_patches
        self.embed_dim = embed_dim

        self.proj = nn.Conv3d(
            in_channels, embed_dim,
            kernel_size=(temporal_patch_size, spatial_patch_size, spatial_patch_size),
            stride=(temporal_patch_size, spatial_patch_size, spatial_patch_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """将视频转换为时空patch嵌入序列。

        Args:
            x: (B, C, T, H, W)

        Returns:
            (B, num_patches, embed_dim)
        """
        x = self.proj(x)  # (B, D, T', H', W')
        x = x.flatten(2)  # (B, D, num_patches)
        x = x.transpose(1, 2)  # (B, num_patches, D)
        return x


class SpatioTemporalAttention(nn.Module):
    """时空联合自注意力。

    将时间和空间维度的patch统一进行自注意力计算，
    使模型能够学习跨帧的时序依赖。

    Attributes:
        num_heads: 注意力头数
        head_dim: 每头维度
        scale: 缩放因子
        qkv: Q/K/V联合投影
        proj: 输出投影
    """

    def __init__(self, embed_dim: int = 512, num_heads: int = 8, dropout: float = 0.0):
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        self.scale = self.head_dim ** -0.5

        self.qkv = nn.Linear(embed_dim, embed_dim * 3)
        self.proj = nn.Linear(embed_dim, embed_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, N, C = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.num_heads, self.head_dim)
        qkv = qkv.permute(2, 0, 3, 1, 4)  # (3, B, H, N, D_h)
        q, k, v = qkv[0], qkv[1], qkv[2]

        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.softmax(dim=-1)
        attn = self.dropout(attn)

        x = (attn @ v).transpose(1, 2).reshape(B, N, C)
        x = self.proj(x)
        x = self.dropout(x)
        return x


class SpatioTemporalTransformerBlock(nn.Module):
    """时空Transformer编码器块。

    LayerNorm -> Self-Attention -> Residual
    -> LayerNorm -> MLP -> Residual
    """

    def __init__(
        self,
        embed_dim: int = 512,
        num_heads: int = 8,
        mlp_ratio: int = 4,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.norm1 = nn.LayerNorm(embed_dim)
        self.attn = SpatioTemporalAttention(embed_dim, num_heads, dropout)
        self.norm2 = nn.LayerNorm(embed_dim)
        self.mlp = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * mlp_ratio),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim * mlp_ratio, embed_dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x))
        x = x + self.mlp(self.norm2(x))
        return x


class SpatioTemporalViT(nn.Module):
    """时空ViT编码器。

    将视频序列编码为时空patch嵌入。
    支持EMA目标编码器用于防坍缩训练。

    Attributes:
        patch_embed: 时空patch嵌入层
        cls_token: 可学习分类token
        pos_embed: 可学习时空位置编码
        pos_drop: 位置嵌入dropout
        blocks: Transformer层列表
        norm: 最终LayerNorm
        ema_decay: EMA衰减率
        ema_encoder: EMA目标编码器
    """

    def __init__(
        self,
        num_frames: int = 16,
        frame_size: int = 224,
        temporal_patch_size: int = 2,
        spatial_patch_size: int = 16,
        in_channels: int = 3,
        embed_dim: int = 512,
        depth: int = 12,
        num_heads: int = 8,
        mlp_ratio: int = 4,
        dropout: float = 0.1,
        ema_decay: float = 0.996,
    ):
        super().__init__()
        self.embed_dim = embed_dim
        self.ema_decay = ema_decay

        self.patch_embed = SpatioTemporalPatchEmbed(
            num_frames, frame_size, temporal_patch_size,
            spatial_patch_size, in_channels, embed_dim,
        )
        num_patches = self.patch_embed.num_patches

        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, num_patches + 1, embed_dim))
        self.pos_drop = nn.Dropout(dropout)

        self.blocks = nn.ModuleList([
            SpatioTemporalTransformerBlock(embed_dim, num_heads, mlp_ratio, dropout)
            for _ in range(depth)
        ])
        self.norm = nn.LayerNorm(embed_dim)

        self.ema_encoder: Optional[nn.Module] = None
        self._init_weights()

    def _init_weights(self):
        nn.init.trunc_normal_(self.pos_embed, std=0.02)
        nn.init.trunc_normal_(self.cls_token, std=0.02)
        self.apply(self._init_weights_impl)

    @staticmethod
    def _init_weights_impl(m):
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0)

    def init_ema_encoder(self):
        """初始化EMA目标编码器。"""
        self.ema_encoder = copy.deepcopy(self)
        for p in self.ema_encoder.parameters():
            p.requires_grad = False

    @torch.no_grad()
    def update_ema(self):
        """更新EMA参数。"""
        if self.ema_encoder is None:
            return
        for online_p, ema_p in zip(self.parameters(), self.ema_encoder.parameters()):
            ema_p.data.mul_(self.ema_decay).add_(online_p.data, alpha=1.0 - self.ema_decay)

    def forward_embeddings(self, x: torch.Tensor, return_all: bool = True) -> torch.Tensor:
        """提取时空patch嵌入。

        Args:
            x: (B, C, T, H, W)
            return_all: 是否返回所有token

        Returns:
            如果return_all=True: (B, num_patches, D)
            否则返回cls_token: (B, D)
        """
        B = x.shape[0]
        x = self.patch_embed(x)
        cls_tokens = self.cls_token.expand(B, -1, -1)
        x = torch.cat([cls_tokens, x], dim=1)
        x = x + self.pos_embed
        x = self.pos_drop(x)

        for block in self.blocks:
            x = block(x)

        x = self.norm(x)
        return x[:, 1:, :] if return_all else x[:, 0, :]

    def forward(self, x: torch.Tensor, mask: Optional[torch.Tensor] = None):
        """前向传播。

        Args:
            x: (B, C, T, H, W)
            mask: 可选的patch级掩码 (B, num_patches)

        Returns:
            patch嵌入 (B, num_patches, D) 或可见patch列表
        """
        features = self.forward_embeddings(x, return_all=True)

        if mask is not None:
            B = features.shape[0]
            return [features[b, ~mask[b]] for b in range(B)]

        return features

    def get_target_embeddings(self, x: torch.Tensor) -> torch.Tensor:
        """使用EMA编码器获取目标嵌入。

        Args:
            x: 完整（未掩码）视频 (B, C, T, H, W)

        Returns:
            EMA编码器的目标嵌入 (B, num_patches, D)
        """
        if self.ema_encoder is None:
            self.init_ema_encoder()
        self.ema_encoder.eval()
        with torch.no_grad():
            return self.ema_encoder.forward_embeddings(x, return_all=True)
