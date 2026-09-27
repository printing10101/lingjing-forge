"""动作条件预测器模块。

实现基于当前加工动作的条件预测：
- 接收上下文嵌入 + 动作条件（刀具移动/换刀/暂停）
- 预测被遮挡区域的语义嵌入
- 条件融合模块将动作编码整合到预测过程中

Key components:
    - ActionConditionedPredictor: 动作条件预测器
"""

import torch
import torch.nn as nn


class ActionEmbedding(nn.Module):
    """动作类型嵌入层。

    将离散动作类型映射为连续嵌入向量。

    Attributes:
        embed: 动作类型嵌入表
        num_types: 动作类型数
    """

    def __init__(self, num_types: int = 3, embed_dim: int = 64):
        super().__init__()
        self.embed = nn.Embedding(num_types, embed_dim)
        nn.init.trunc_normal_(self.embed.weight, std=0.02)

    def forward(self, action_ids: torch.Tensor) -> torch.Tensor:
        """获取动作嵌入。

        Args:
            action_ids: (B,) 动作类型ID

        Returns:
            (B, embed_dim)
        """
        return self.embed(action_ids)


class ConditionFusion(nn.Module):
    """动作条件融合模块。

    将动作条件嵌入与上下文嵌入进行融合：
    - 使用交叉注意力机制让上下文特征"关注"动作条件
    - 通过FiLM（Feature-wise Linear Modulation）调制上下文特征

    Attributes:
        action_proj: 动作嵌入投影层
        context_proj: 上下文投影层
        film_gamma: FiLM缩放因子
        film_beta: FiLM偏置因子
    """

    def __init__(self, context_dim: int = 512, action_dim: int = 64, hidden_dim: int = 256):
        super().__init__()
        self.action_proj = nn.Sequential(
            nn.Linear(action_dim, hidden_dim),
            nn.GELU(),
            nn.Linear(hidden_dim, context_dim),
        )
        self.context_proj = nn.Linear(context_dim, context_dim)

        # FiLM调制参数
        self.film_gamma = nn.Linear(context_dim, context_dim)
        self.film_beta = nn.Linear(context_dim, context_dim)

        self.norm = nn.LayerNorm(context_dim)

    def forward(
        self,
        context_embeddings: torch.Tensor,
        action_embeddings: torch.Tensor,
    ) -> torch.Tensor:
        """融合动作条件与上下文嵌入。

        Args:
            context_embeddings: (B, N_context, D_context)
            action_embeddings: (B, D_action)

        Returns:
            调制后的特征 (B, N_context, D_context)
        """
        B, N, D = context_embeddings.shape

        # 投影动作嵌入
        action_feat = self.action_proj(action_embeddings)  # (B, D)

        # 计算FiLM参数
        gamma = self.film_gamma(action_feat).unsqueeze(1) + 1.0  # (B, 1, D)
        beta = self.film_beta(action_feat).unsqueeze(1)  # (B, 1, D)

        # FiLM调制
        modulated = context_embeddings * gamma + beta
        return self.norm(modulated)


class ActionConditionedPredictor(nn.Module):
    """动作条件预测器。

    基于上下文嵌入和当前动作状态，预测被遮挡区域的语义嵌入。
    架构：条件融合 -> MLP预测器

    Attributes:
        action_embed: 动作类型嵌入
        condition_fusion: 条件融合模块
        mask_token: 可学习掩码token
        position_embed: 目标位置编码
        mlp: MLP预测网络
    """

    def __init__(
        self,
        input_dim: int = 512,
        hidden_dim: int = 1024,
        output_dim: int = 512,
        num_layers: int = 3,
        num_action_types: int = 3,
        action_embed_dim: int = 64,
        num_positions: int = 1568,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.output_dim = output_dim

        self.action_embed = ActionEmbedding(num_action_types, action_embed_dim)
        self.condition_fusion = ConditionFusion(input_dim, action_embed_dim)

        self.mask_token = nn.Parameter(torch.zeros(1, 1, input_dim))
        self.position_embed = nn.Parameter(torch.zeros(1, num_positions, input_dim))

        # MLP预测器
        layers = []
        in_dim = input_dim
        for i in range(num_layers):
            out_dim = hidden_dim if i < num_layers - 1 else output_dim
            layers.append(nn.Linear(in_dim, out_dim))
            if i < num_layers - 1:
                layers.append(nn.GELU())
                layers.append(nn.LayerNorm(out_dim))
                layers.append(nn.Dropout(dropout))
            in_dim = out_dim
        self.mlp = nn.Sequential(*layers)

        self._init_weights()

    def _init_weights(self):
        nn.init.trunc_normal_(self.mask_token, std=0.02)
        nn.init.trunc_normal_(self.position_embed, std=0.02)
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.trunc_normal_(m.weight, std=0.02)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(
        self,
        context_embeddings: torch.Tensor,
        action_ids: torch.Tensor,
        target_positions: torch.Tensor,
    ) -> torch.Tensor:
        """预测目标位置的嵌入。

        Args:
            context_embeddings: (B, N_context, D) 上下文嵌入
            action_ids: (B,) 当前动作类型ID
            target_positions: (B, N_target) 目标位置索引

        Returns:
            预测嵌入 (B, N_target, D)
        """
        B, N_context, D = context_embeddings.shape
        N_target = target_positions.shape[1]

        # 动作条件融合
        action_emb = self.action_embed(action_ids)
        context_modulated = self.condition_fusion(context_embeddings, action_emb)

        # 聚合上下文信息
        context_summary = context_modulated.mean(dim=1)  # (B, D)

        # 构造预测token
        pos_embeds = self.position_embed[:, :N_target, :]
        target_tokens = self.mask_token.expand(B, N_target, -1)
        combined = context_summary.unsqueeze(1) + target_tokens + pos_embeds

        return self.mlp(combined)
