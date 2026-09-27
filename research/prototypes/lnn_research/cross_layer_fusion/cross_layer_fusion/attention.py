"""
跨层注意力基础模块 (CrossLayerAttention)

实现符合PyTorch标准的跨层注意力机制，支持:
    - 标准缩放点积注意力计算
    - 可自定义的query/key/value维度配置
    - 维度对齐与投影
    - 异常处理与维度检查
"""

from __future__ import annotations

import math
import warnings
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


class CrossLayerAttention(nn.Module):
    """跨层注意力机制基础模块。

    实现标准的多头缩放点积注意力，用于不同层之间的信息融合。
    支持自定义query、key、value的维度配置，以及多头注意力计算。

    Attributes:
        dim_q: Query向量维度。
        dim_k: Key向量维度。
        dim_v: Value向量维度。
        dim_out: 输出向量维度。
        n_heads: 注意力头数。
        scale: 注意力缩放因子 (1/sqrt(d_k))。
        dropout: Dropout比率。

    Example:
        >>> attn = CrossLayerAttention(dim_q=256, dim_k=256, dim_v=256, dim_out=256)
        >>> q = torch.randn(4, 16, 256)  # (batch, seq_len, dim)
        >>> k = torch.randn(4, 16, 256)
        >>> v = torch.randn(4, 16, 256)
        >>> output, weights = attn(q, k, v)
        >>> output.shape
        torch.Size([4, 16, 256])
    """

    def __init__(
        self,
        dim_q: int = 256,
        dim_k: int = 256,
        dim_v: int = 256,
        dim_out: int = 256,
        n_heads: int = 8,
        dropout: float = 0.1,
        use_projection: bool = True,
    ):
        """初始化跨层注意力模块。

        Args:
            dim_q: Query向量的特征维度。默认256。
            dim_k: Key向量的特征维度。默认256。
            dim_v: Value向量的特征维度。默认256。
            dim_out: 输出向量的特征维度。默认256。
            n_heads: 多头注意力的头数，必须能被dim_out整除。默认8。
            dropout: Dropout比率，范围[0,1)。默认0.1。
            use_projection: 是否使用可学习的线性投影层。默认True。

        Raises:
            ValueError: 当注意力头数无法整除输出维度时抛出。
        """
        super().__init__()

        self.dim_q = dim_q
        self.dim_k = dim_k
        self.dim_v = dim_v
        self.dim_out = dim_out
        self.n_heads = n_heads
        self.dropout_rate = dropout
        self.use_projection = use_projection

        # 计算每个注意力头的维度
        self.dim_head = dim_out // n_heads
        if dim_out % n_heads != 0:
            raise ValueError(
                f"输出维度 dim_out={dim_out} 必须能被注意力头数 n_heads={n_heads} 整除。请调整dim_out或n_heads参数。"
            )

        # 注意力缩放因子: 1 / sqrt(d_k)
        self.scale = 1.0 / math.sqrt(self.dim_head * dim_k / dim_out)

        # 可学习的线性投影层
        if use_projection:
            self.W_q = nn.Linear(dim_q, dim_out, bias=False)
            self.W_k = nn.Linear(dim_k, dim_out, bias=False)
            self.W_v = nn.Linear(dim_v, dim_out, bias=False)
        else:
            self.W_q = nn.Identity()
            self.W_k = nn.Identity()
            self.W_v = nn.Identity()

        # 输出投影层
        self.W_o = nn.Linear(dim_out, dim_out, bias=False)

        # Dropout层
        self.dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()

        # 层归一化
        self.layer_norm = nn.LayerNorm(dim_out)

        self._init_weights()

    def _init_weights(self):
        """初始化投影层权重，使用Xavier初始化。"""
        if self.use_projection:
            for module in [self.W_q, self.W_k, self.W_v, self.W_o]:
                if isinstance(module, nn.Linear):
                    nn.init.xavier_uniform_(module.weight)

    def _validate_inputs(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
    ) -> None:
        """验证输入张量的维度和类型。

        Args:
            query: 查询张量。
            key: 键张量。
            value: 值张量。

        Raises:
            TypeError: 输入类型不是torch.Tensor时抛出。
            ValueError: 输入维度不匹配时抛出。
        """
        for name, tensor in [("query", query), ("key", key), ("value", value)]:
            if not isinstance(tensor, torch.Tensor):
                raise TypeError(f"{name} 必须是 torch.Tensor 类型，实际类型为 {type(tensor).__name__}。")

        if query.dim() < 2:
            raise ValueError(
                f"query 维度必须 >= 2，实际维度为 {query.dim()}。"
                f"期望形状: (batch_size, seq_len, dim) 或 (batch_size, dim)。"
            )

        if key.dim() < 2:
            raise ValueError(f"key 维度必须 >= 2，实际维度为 {key.dim()}。")

        if value.dim() < 2:
            raise ValueError(f"value 维度必须 >= 2，实际维度为 {value.dim()}。")

        # 检查特征维度是否匹配初始化参数
        if query.size(-1) != self.dim_q and query.dim() >= 2:
            warnings.warn(
                f"query 最后一维 ({query.size(-1)}) 与初始化 dim_q ({self.dim_q}) 不匹配。将使用投影层进行维度对齐。"
            )

    def _align_dimensions(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """对齐输入张量的维度。

        将不同维度的query、key、value通过投影层对齐到统一维度空间。

        Args:
            query: 查询张量 (..., dim_q)。
            key: 键张量 (..., dim_k)。
            value: 值张量 (..., dim_v)。

        Returns:
            (query_proj, key_proj, value_proj) 对齐后的张量，最后一维均为dim_out。
        """
        # 通过投影层对齐维度
        q_proj = self.W_q(query)
        k_proj = self.W_k(key)
        v_proj = self.W_v(value)

        return q_proj, k_proj, v_proj

    def _reshape_for_multihead(
        self,
        tensor: torch.Tensor,
    ) -> torch.Tensor:
        """将张量重塑为多头注意力格式。

        (batch, seq_len, dim_out) -> (batch * n_heads, seq_len, dim_head)

        Args:
            tensor: 输入张量 (batch_size, seq_len, dim_out)。

        Returns:
            重塑后的多头张量 (batch_size * n_heads, seq_len, dim_head)。
        """
        batch_size, seq_len, _ = tensor.shape
        tensor = tensor.reshape(batch_size, seq_len, self.n_heads, self.dim_head)
        tensor = tensor.permute(0, 2, 1, 3)  # (batch, n_heads, seq_len, dim_head)
        tensor = tensor.reshape(batch_size * self.n_heads, seq_len, self.dim_head)
        return tensor

    def _reshape_from_multihead(
        self,
        tensor: torch.Tensor,
        batch_size: int,
    ) -> torch.Tensor:
        """将多头注意力输出重塑回原始格式。

        Args:
            tensor: 多头张量 (batch_size * n_heads, seq_len, dim_head)。
            batch_size: 批次大小。

        Returns:
            重塑后的张量 (batch_size, seq_len, dim_out)。
        """
        seq_len = tensor.size(1)
        tensor = tensor.reshape(batch_size, self.n_heads, seq_len, self.dim_head)
        tensor = tensor.permute(0, 2, 1, 3)  # (batch, n_heads, seq_len, dim_head)
        tensor = tensor.reshape(batch_size, tensor.size(1), self.dim_out)
        return tensor

    def forward(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        mask: Optional[torch.Tensor] = None,
        score_bias: Optional[torch.Tensor] = None,
        return_attention: bool = True,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """前向传播：执行标准缩放点积注意力计算。

        计算流程:
        1. 输入验证与维度检查
        2. 通过投影层对齐query/key/value维度
        3. 重塑为多头注意力格式
        4. 计算query与key的相似度矩阵: scores = Q @ K^T * scale
        5. 应用score_bias（如时间衰减因子）到scores
        6. 应用softmax归一化: attn = softmax(scores)
        7. 计算注意力权重与value的加权和: output = attn @ V
        8. 重塑并投影输出

        Args:
            query: 查询张量 (batch_size, seq_len_q, dim_q)。
            key: 键张量 (batch_size, seq_len_k, dim_k)。
            value: 值张量 (batch_size, seq_len_v, dim_v)。
            mask: 可选注意力掩码 (batch_size, seq_len_q, seq_len_k)，True位置将被屏蔽。
            score_bias: 可选分数偏置，广播到 (B*H, seq_q, seq_k) 后加到scores上。
                用于时间衰减等场景。默认None。
            return_attention: 是否返回注意力权重矩阵。默认True。

        Returns:
            (output, attention_weights) 元组:
                - output: 注意力输出张量 (batch_size, seq_len_q, dim_out)。
                - attention_weights: 注意力权重矩阵 (B*H, seq_q, seq_k) 格式，
                  当return_attention=False时为None。

        Raises:
            RuntimeError: 当输入维度不兼容导致计算失败时抛出。
        """
        # 输入验证
        self._validate_inputs(query, key, value)

        batch_size = query.size(0)

        # 5: 维度对齐 - 通过投影层统一特征空间
        q_proj, k_proj, v_proj = self._align_dimensions(query, key, value)

        # 6: 处理单样本输入 (batch, dim) -> (batch, 1, dim)
        if q_proj.dim() == 2:
            q_proj = q_proj.unsqueeze(1)
            k_proj = k_proj.unsqueeze(1)
            v_proj = v_proj.unsqueeze(1)

        # 重塑为多头格式
        q_multi = self._reshape_for_multihead(q_proj)  # (B*H, seq_q, d_head)
        k_multi = self._reshape_for_multihead(k_proj)  # (B*H, seq_k, d_head)
        v_multi = self._reshape_for_multihead(v_proj)  # (B*H, seq_v, d_head)

        # 计算相似度矩阵 scores = Q @ K^T * scale
        scores = torch.bmm(q_multi, k_multi.transpose(1, 2)) * self.scale

        # 5: 应用分数偏置（如时间衰减因子）
        if score_bias is not None:
            # score_bias广播到 (B*H, seq_q, seq_k)
            if score_bias.dim() == 1:
                score_bias = score_bias.unsqueeze(0).unsqueeze(0)  # (1, 1, seq_k)
            elif score_bias.dim() == 2:
                score_bias = score_bias.unsqueeze(1)  # (B, 1, seq_k)
            scores = scores + score_bias

        # 应用掩码（如果提供）
        if mask is not None:
            seq_q = q_multi.size(1)
            seq_k = k_multi.size(1)
            # 将任意形状的掩码转换为 (B*H, seq_q, seq_k) 格式
            if mask.dim() == 2:
                # (batch_size, seq_k) -> 每行掩码应用于所有query和所有head
                mask_expanded = mask.unsqueeze(1).unsqueeze(1)  # (B, 1, 1, seq_k)
                mask_expanded = mask_expanded.expand(batch_size, self.n_heads, seq_q, seq_k)
            elif mask.dim() == 3:
                # (batch_size, seq_q, seq_k)
                mask_expanded = mask.unsqueeze(1)  # (B, 1, seq_q, seq_k)
                mask_expanded = mask_expanded.expand(batch_size, self.n_heads, seq_q, seq_k)
            elif mask.dim() == 4:
                # (batch_size, n_heads, seq_q, seq_k)
                mask_expanded = mask
            else:
                raise ValueError(f"mask 维度 {mask.dim()} 不支持，预期 2~4 维。")

            mask_expanded = mask_expanded.reshape(batch_size * self.n_heads, seq_q, seq_k)
            scores = scores.masked_fill(mask_expanded, float("-inf"))

        # Softmax归一化
        attn_weights = F.softmax(scores, dim=-1)

        # 加权求和 output = attn @ V
        context = torch.bmm(attn_weights, v_multi)

        # 重塑回原始格式并投影
        output = self._reshape_from_multihead(context, batch_size)
        output = self.W_o(output)

        # 层归一化 + 残差连接
        output = self.layer_norm(output + q_proj)

        # 准备返回的注意力权重
        full_attn_weights = None
        if return_attention:
            # 返回 (B*H, seq_q, seq_k) 格式，由外部调用者按需reshape
            full_attn_weights = attn_weights.detach()

        return output, full_attn_weights

    def get_attention_heatmap(
        self,
        query: torch.Tensor,
        key: torch.Tensor,
        value: torch.Tensor,
        aggregate_heads: str = "mean",
    ) -> torch.Tensor:
        """获取注意力热力图，用于可视化分析。

        Args:
            query: 查询张量 (batch_size, seq_len_q, dim_q)。
            key: 键张量 (batch_size, seq_len_k, dim_k)。
            value: 值张量 (batch_size, seq_len_v, dim_v)。
            aggregate_heads: 多头聚合方式，'mean'取平均，'max'取最大值。

        Returns:
            聚合后的注意力权重 (batch_size, seq_len_q, seq_len_k)。

        Raises:
            ValueError: aggregate_heads参数无效时抛出。
        """
        _, attn_weights_flat = self.forward(query, key, value, return_attention=True)
        # attn_weights_flat: (B*H, seq_q, seq_k)
        batch_size = query.size(0)

        # 重塑为 (B, H, seq_q, seq_k)
        attn_weights = reshape_attention_weights(
            attn_weights_flat,
            batch_size,
            self.n_heads,
        )

        if aggregate_heads == "mean":
            return attn_weights.mean(dim=1)
        elif aggregate_heads == "max":
            return attn_weights.max(dim=1)[0]
        else:
            raise ValueError(f"aggregate_heads 参数必须是 'mean' 或 'max'，实际为 '{aggregate_heads}'。")

    def extra_repr(self) -> str:
        """返回模块的额外字符串表示。"""
        return (
            f"dim_q={self.dim_q}, dim_k={self.dim_k}, dim_v={self.dim_v}, "
            f"dim_out={self.dim_out}, n_heads={self.n_heads}, "
            f"dim_head={self.dim_head}, scale={self.scale:.4f}"
        )


def reshape_attention_weights(
    flat_weights: torch.Tensor,
    batch_size: int,
    n_heads: int,
) -> torch.Tensor:
    """将扁平注意力权重 (B*H, seq_q, seq_k) 重塑为 (B, H, seq_q, seq_k)。

    由于CrossLayerAttention.forward返回 (B*H, seq_q, seq_k) 格式的权重，
    此工具函数将其重塑为直观的 (batch, heads, query, key) 格式。

    Args:
        flat_weights: 扁平注意力权重 (batch*heads, seq_q, seq_k)。
        batch_size: 原始批次大小。
        n_heads: 注意力头数。

    Returns:
        (batch_size, n_heads, seq_q, seq_k) 格式的张量。
    """
    return flat_weights.reshape(batch_size, n_heads, flat_weights.size(1), flat_weights.size(2))
