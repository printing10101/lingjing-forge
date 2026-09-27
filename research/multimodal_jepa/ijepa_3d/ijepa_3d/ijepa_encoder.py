"""I-JEPA ViT编码器模块。
实现ViT-Small配置的Transformer编码器：
- 12层Transformer
- 8头自注意力
- 嵌入维度512
- 16×16 patch大小

支持EMA权重更新机制用于防坍缩。
Key components:
    - PatchEmbed: 图像到patch嵌入的转换
    - TransformerBlock: 单个Transformer编码器块
    - IJEPAEncoder: 完整的ViT-Small编码器

Example:
    >>> encoder = IJEPAEncoder(img_size=256, patch_size=16, embed_dim=512)
    >>> features = encoder(images)  # (B, 64, 64, 64) -> (B, 256, 512)
"""

import torch
import torch.nn as nn
import copy
from typing import Optional


class PatchEmbed(nn.Module):
    """图像Patch嵌入层。
    将D维特征图分割为固定大小的patch，并通过线性投影映射到嵌入空间。
    Attributes:
        proj: 卷积投影层
        num_patches: patch总数
        embed_dim: 嵌入维度
    """

    def __init__(
        self,
        img_size: int = 64,
        patch_size: int = 4,
        in_channels: int = 64,
        embed_dim: int = 512,
    ):
        """初始化Patch嵌入层。
        Args:
            img_size: 输入特征图大小（正方形）
            patch_size: patch大小
            in_channels: 输入通道数
            embed_dim: 输出嵌入维度
        """
        super().__init__()
        self.img_size = img_size
        self.patch_size = patch_size
        self.num_patches = (img_size // patch_size) ** 2
        self.embed_dim = embed_dim

        self.proj = nn.Conv2d(
            in_channels, embed_dim,
            kernel_size=patch_size, stride=patch_size,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：将特征图转换为patch嵌入序列。
        Args:
            x: 输入特征图 (B, C, H, W)

        Returns:
            patch嵌入 (B, num_patches, embed_dim)
        """
        _ = x.shape[0]
        x = self.proj(x)  # (B, embed_dim, H/patch, W/patch)
        x = x.flatten(2)  # (B, embed_dim, num_patches)
        x = x.transpose(1, 2)  # (B, num_patches, embed_dim)
        return x


class MultiHeadSelfAttention(nn.Module):
    """多头自注意力机制。
    Attributes:
        num_heads: 注意力头数
        head_dim: 每个头的维度
        scale: 缩放因子
        qkv: Query/Key/Value联合投影
        proj: 输出投影
    """

    def __init__(
        self,
        embed_dim: int = 512,
        num_heads: int = 8,
        dropout: float = 0.0,
    ):
        """初始化多头自注意力。
        Args:
            embed_dim: 嵌入维度
            num_heads: 注意力头数
            dropout: dropout率
        """
        super().__init__()
        self.embed_dim = embed_dim
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        self.scale = self.head_dim ** -0.5

        self.qkv = nn.Linear(embed_dim, embed_dim * 3)
        self.proj = nn.Linear(embed_dim, embed_dim)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播：计算自注意力。
        Args:
            x: 输入序列 (B, N, embed_dim)

        Returns:
            注意力输出 (B, N, embed_dim)
        """
        B, N, C = x.shape

        qkv = self.qkv(x)  # (B, N, 3*C)
        qkv = qkv.reshape(B, N, 3, self.num_heads, self.head_dim)
        qkv = qkv.permute(2, 0, 3, 1, 4)  # (3, B, num_heads, N, head_dim)
        q, k, v = qkv[0], qkv[1], qkv[2]

        attn = (q @ k.transpose(-2, -1)) * self.scale
        attn = attn.softmax(dim=-1)
        attn = self.dropout(attn)

        x = (attn @ v).transpose(1, 2)  # (B, N, num_heads, head_dim)
        x = x.reshape(B, N, C)
        x = self.proj(x)
        x = self.dropout(x)

        return x


class TransformerBlock(nn.Module):
    """Transformer编码器块。
    LayerNorm -> MultiHeadSelfAttention -> residual
    -> LayerNorm -> MLP -> residual

    Attributes:
        norm1: 第一个LayerNorm
        attn: 多头自注意力
        norm2: 第二个LayerNorm
        mlp: 前馈网络
    """

    def __init__(
        self,
        embed_dim: int = 512,
        num_heads: int = 8,
        mlp_ratio: int = 4,
        dropout: float = 0.0,
    ):
        """初始化Transformer块。
        Args:
            embed_dim: 嵌入维度
            num_heads: 注意力头数
            mlp_ratio: MLP扩展比例
            dropout: dropout率
        """
        super().__init__()
        self.norm1 = nn.LayerNorm(embed_dim)
        self.attn = MultiHeadSelfAttention(embed_dim, num_heads, dropout)
        self.norm2 = nn.LayerNorm(embed_dim)
        self.mlp = nn.Sequential(
            nn.Linear(embed_dim, embed_dim * mlp_ratio),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(embed_dim * mlp_ratio, embed_dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """前向传播。
        Args:
            x: 输入 (B, N, embed_dim)

        Returns:
            输出 (B, N, embed_dim)
        """
        x = x + self.attn(self.norm1(x))
        x = x + self.mlp(self.norm2(x))
        return x


class IJEPAEncoder(nn.Module):
    """I-JEPA ViT-Small编码器。
    12层Transformer，8头注意力，嵌入维度512。
    支持EMA权重更新用于防坍缩。
    Attributes:
        patch_embed: Patch嵌入层
        cls_token: 可学习的分类token
        pos_embed: 位置编码
        pos_drop: 位置嵌入dropout
        blocks: Transformer块列表
        norm: 最终LayerNorm
        embed_dim: 嵌入维度
        ema_decay: EMA衰减率
        ema_encoder: EMA目标编码器（防坍缩用）
    """

    def __init__(
        self,
        img_size: int = 64,
        patch_size: int = 4,
        in_channels: int = 64,
        embed_dim: int = 512,
        depth: int = 12,
        num_heads: int = 8,
        mlp_ratio: int = 4,
        dropout: float = 0.0,
        ema_decay: float = 0.996,
    ):
        """初始化I-JEPA编码器。
        Args:
            img_size: CNN输出特征图大小（默认64）
            patch_size: ViT patch大小（默认4，得到16个patch）
            in_channels: 输入通道数（CNN输出通道数）
            embed_dim: 嵌入维度（默认512）
            depth: Transformer层数（默认12）
            num_heads: 注意力头数（默认8）
            mlp_ratio: MLP扩展比例（默认4）
            dropout: dropout率
            ema_decay: EMA衰减率（默认0.996）
        """
        super().__init__()
        self.embed_dim = embed_dim
        self.depth = depth
        self.ema_decay = ema_decay

        # Patch嵌入
        self.patch_embed = PatchEmbed(img_size, patch_size, in_channels, embed_dim)
        num_patches = self.patch_embed.num_patches

        # 分类token和位置编码
        self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
        self.pos_embed = nn.Parameter(
            torch.zeros(1, num_patches + 1, embed_dim),
        )
        self.pos_drop = nn.Dropout(dropout)

        # Transformer块
        self.blocks = nn.ModuleList([
            TransformerBlock(embed_dim, num_heads, mlp_ratio, dropout)
            for _ in range(depth)
        ])
        self.norm = nn.LayerNorm(embed_dim)

        # EMA目标编码器（推理时不使用）
        self.ema_encoder: Optional[nn.Module] = None

        self._init_weights()

    def _init_weights(self) -> None:
        """初始化权重。"""
        nn.init.trunc_normal_(self.pos_embed, std=0.02)
        nn.init.trunc_normal_(self.cls_token, std=0.02)
        self.apply(self._init_weights_impl)

    @staticmethod
    def _init_weights_impl(m: nn.Module) -> None:
        """逐模块权重初始化。"""
        if isinstance(m, nn.Linear):
            nn.init.trunc_normal_(m.weight, std=0.02)
            if m.bias is not None:
                nn.init.constant_(m.bias, 0)
        elif isinstance(m, nn.LayerNorm):
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0)

    def init_ema_encoder(self) -> None:
        """初始化EMA目标编码器。
        创建编码器的深拷贝作为EMA目标网络。
        目标网络不参与梯度计算，参数通过EMA更新。
        """
        self.ema_encoder = copy.deepcopy(self)
        # 冻结EMA编码器所有参数
        for param in self.ema_encoder.parameters():
            param.requires_grad = False

    @torch.no_grad()
    def update_ema(self) -> None:
        """更新EMA目标编码器参数。
        使用公式：theta_ema = decay * theta_ema + (1 - decay) * theta_online
        """
        if self.ema_encoder is None:
            return

        for online_param, ema_param in zip(
            self.parameters(), self.ema_encoder.parameters(),
        ):
            ema_param.data.mul_(self.ema_decay).add_(
                online_param.data, alpha=1.0 - self.ema_decay,
            )

    def forward_features(
        self,
        x: torch.Tensor,
        return_all_tokens: bool = True,
    ) -> torch.Tensor:
        """提取patch特征（不包含分类token）。
        Args:
            x: CNN输出特征图 (B, 64, H, W)
            return_all_tokens: 是否返回所有token

        Returns:
            patch特征 (B, num_patches, embed_dim)
        """
        B = x.shape[0]

        # Patch嵌入
        x = self.patch_embed(x)  # (B, num_patches, embed_dim)

        # 添加分类token
        cls_tokens = self.cls_token.expand(B, -1, -1)
        x = torch.cat([cls_tokens, x], dim=1)  # (B, 1+num_patches, embed_dim)

        # 添加位置编码
        x = x + self.pos_embed
        x = self.pos_drop(x)

        # Transformer编码
        for block in self.blocks:
            x = block(x)

        x = self.norm(x)

        if return_all_tokens:
            # 返回所有patch token（去除cls_token）
            return x[:, 1:, :]
        else:
            # 仅返回cls_token
            return x[:, 0, :]

    def forward(
        self,
        x: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """前向传播。
        Args:
            x: CNN输出特征图 (B, 64, H, W)
            mask: 可选的patch级掩码 (B, num_patches)

        Returns:
            patch嵌入 (B, num_patches, embed_dim)
        """
        features = self.forward_features(x, return_all_tokens=True)

        # 如果提供了掩码，仅返回可见（未掩码）patch的特征
        if mask is not None:
            # mask: True=被掩码 False=可见
            B = features.shape[0]
            visible_features = []
            for b in range(B):
                visible_idx = ~mask[b]
                visible_features.append(features[b, visible_idx])
            return visible_features

        return features

    def get_target_embeddings(
        self,
        x: torch.Tensor,
    ) -> torch.Tensor:
        """使用EMA目标编码器获取目标嵌入。
        用于I-JEPA自监督训练中的预测目标。
        Args:
            x: 完整（未掩码）特征图 (B, 64, H, W)

        Returns:
            EMA编码器生成的目标嵌入 (B, num_patches, embed_dim)
        """
        if self.ema_encoder is None:
            self.init_ema_encoder()

        target_encoder = self.ema_encoder
        target_encoder.eval()
        with torch.no_grad():
            targets = target_encoder.forward_features(x, return_all_tokens=True)
        return targets
