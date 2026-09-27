"""I-JEPA预测器网络模块。

实现3层MLP预测器，用于基于上下文嵌入预测被遮挡区域的语义嵌入。
预测目标为编码器生成的上下文语义嵌入向量（非像素级重建）。

Key components:
    - Predictor: 3层MLP预测器网络

Example:
    >>> predictor = Predictor(input_dim=512, hidden_dim=1024, output_dim=512)
    >>> predicted = predictor(context_embeddings, target_positions)
"""

import torch
import torch.nn as nn


class Predictor(nn.Module):
    """I-JEPA预测器网络。

    基于3层MLP架构的嵌入预测器，输入上下文patch嵌入和预测目标位置编码，
    输出被遮挡区域在嵌入空间中的预测表示。

    架构：Linear(input_dim -> hidden_dim) -> GELU -> LayerNorm
         -> Linear(hidden_dim -> hidden_dim) -> GELU -> LayerNorm
         -> Linear(hidden_dim -> output_dim)

    Attributes:
        input_dim: 输入嵌入维度
        hidden_dim: 隐藏层维度
        output_dim: 输出嵌入维度
        num_layers: MLP层数
        position_embed: 目标位置编码
        mlp: MLP网络
    """

    def __init__(
        self,
        input_dim: int = 512,
        hidden_dim: int = 1024,
        output_dim: int = 512,
        num_layers: int = 3,
        dropout: float = 0.0,
        num_positions: int = 16,  # 16 patches (4x4 grid from 64x64 with patch=4)
    ):
        """初始化预测器网络。

        Args:
            input_dim: 输入嵌入维度（默认512）
            hidden_dim: 隐藏层维度（默认1024）
            output_dim: 输出嵌入维度（默认512）
            num_layers: MLP层数（默认3）
            dropout: dropout率
            num_positions: 最大预测位置数（patch总数）
        """
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.num_layers = num_layers

        # 可学习的位置编码（用于预测目标位置）
        self.mask_token = nn.Parameter(torch.zeros(1, 1, input_dim))
        self.position_embed = nn.Parameter(
            torch.zeros(1, num_positions, input_dim),
        )

        # 构建MLP
        layers = []
        in_dim = input_dim

        for i in range(num_layers):
            out_dim = hidden_dim if i < num_layers - 1 else output_dim
            layers.append(nn.Linear(in_dim, out_dim))

            if i < num_layers - 1:
                layers.append(nn.GELU())
                layers.append(nn.LayerNorm(out_dim))
                if dropout > 0:
                    layers.append(nn.Dropout(dropout))

            in_dim = out_dim

        self.mlp = nn.Sequential(*layers)

        self._init_weights()

    def _init_weights(self) -> None:
        """初始化权重。"""
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
        target_positions: torch.Tensor,
    ) -> torch.Tensor:
        """前向传播：预测目标位置的嵌入。

        Args:
            context_embeddings: 上下文patch嵌入 (B, N_context, embed_dim)
            target_positions: 目标位置索引 (B, N_target)，标识要预测的patch位置

        Returns:
            预测嵌入 (B, N_target, embed_dim)
        """
        B, N_context, D = context_embeddings.shape
        N_target = target_positions.shape[1]

        # 聚合上下文信息（平均池化）

        context_summary = context_embeddings.mean(dim=1)  # (B, D)

        # 为每个目标位置添加位置信息
        pos_embeds = self.position_embed[:, :N_target, :]  # (1, N_target, D)
        target_tokens = self.mask_token.expand(B, N_target, -1)  # (B, N_target, D)

        # 组合：上下文摘要 + 目标token + 位置编码
        combined = context_summary.unsqueeze(1) + target_tokens + pos_embeds
        # (B, N_target, D)

        # MLP预测
        predicted = self.mlp(combined)  # (B, N_target, output_dim)

        return predicted

    def predict_from_context(
        self,
        context_embeddings: torch.Tensor,
        num_targets: int,
    ) -> torch.Tensor:
        """从上下文嵌入预测指定数量的目标嵌入。

        用于推理时从上下文特征预测被遮挡区域的嵌入表示。

        Args:
            context_embeddings: 上下文patch嵌入 (B, N_context, D)
            num_targets: 要预测的目标数量

        Returns:
            预测嵌入 (B, num_targets, D)
        """
        B = context_embeddings.shape[0]
        device = context_embeddings.device

        # 默认预测前num_targets个位置
        target_positions = torch.arange(
            num_targets, device=device,
        ).unsqueeze(0).expand(B, -1)

        return self.forward(context_embeddings, target_positions)
