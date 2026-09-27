"""
三层跨层融合机制 (Three-Layer Cross-Layer Fusion)

实现三层（认知层、感知层、执行层）之间的高效注意力融合机制:
    1. 认知→感知 融合 (CognitiveToPerceptionFusion): 跨模态注意力融合
    2. 感知→执行 融合 (PerceptionToExecutionFusion): 时序注意力融合
    3. 执行→认知 反馈 (ExecutionToCognitiveFusion): 反馈注意力融合

维度配置:
    - 认知层工艺意图嵌入向量: 256维
    - 感知层三视图嵌入特征: 256维
    - 感知层3D几何参数张量: 256维
    - 传感器历史嵌入: 序列长度32, 每步128维
    - 执行层实时预测结果向量: 128维
    - 异常事件结构化描述: 64维
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from app.ai.cross_layer_fusion.attention import CrossLayerAttention


# 1. 认知感知 融合机制


class CognitiveToPerceptionFusion(nn.Module):
    """认知层到感知层的跨模态注意力融合模块。

    将认知层的工艺意图嵌入向量与感知层的三视图嵌入特征进行跨模态融合，
    生成感知任务重新加权向量，用于动态调整感知层的特征提取权重。

    架构:
        1. 跨模态维度对齐投影
        2. 多头跨模态注意力计算
        3. 特征重要性加权
        4. 感知任务重新加权向量生成

    Attributes:
        dim_cognitive: 认知层嵌入维度 (默认256)。
        dim_perception: 感知层嵌入维度 (默认256)。
        dim_fusion: 融合空间维度 (默认256)。
        n_heads: 注意力头数 (默认8)。
    """

    def __init__(
        self,
        dim_cognitive: int = 256,
        dim_perception: int = 256,
        dim_fusion: int = 256,
        n_heads: int = 8,
        dropout: float = 0.1,
    ):
        """初始化认知→感知融合模块。

        Args:
            dim_cognitive: 认知层工艺意图嵌入向量维度。默认256。
            dim_perception: 感知层三视图嵌入特征维度。默认256。
            dim_fusion: 融合空间维度。默认256。
            n_heads: 多头注意力头数。默认8。
            dropout: Dropout比率。默认0.1。
        """
        super().__init__()

        self.dim_cognitive = dim_cognitive
        self.dim_perception = dim_perception
        self.dim_fusion = dim_fusion

        # 跨模态维度对齐投影层 - 将不同模态特征映射到统一空间
        self.proj_cognitive = nn.Linear(dim_cognitive, dim_fusion, bias=False)
        self.proj_perception = nn.Linear(dim_perception, dim_fusion, bias=False)

        # 特征重要性权重学习器 - 学习各模态特征的重要性分数
        self.importance_scorer = nn.Sequential(
            nn.Linear(dim_fusion * 2, dim_fusion),
            nn.ReLU(),
            nn.Linear(dim_fusion, 2),  # 两个权重: cognitive, perception
            nn.Softmax(dim=-1),
        )

        # 跨模态注意力层 - 以认知意图为query，感知特征为key/value
        self.cross_modal_attn = CrossLayerAttention(
            dim_q=dim_fusion,
            dim_k=dim_fusion,
            dim_v=dim_fusion,
            dim_out=dim_fusion,
            n_heads=n_heads,
            dropout=dropout,
        )

        # 感知任务重新加权向量生成器
        self.reweight_generator = nn.Sequential(
            nn.Linear(dim_fusion, dim_fusion * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_fusion * 2, dim_perception),
            nn.Sigmoid(),  # 输出[0,1]范围的权重
        )

        # 层归一化
        self.norm_cog = nn.LayerNorm(dim_fusion)
        self.norm_per = nn.LayerNorm(dim_fusion)

        self._init_weights()

    def _init_weights(self):
        """初始化所有权重。"""
        for module in [self.proj_cognitive, self.proj_perception]:
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)

    def _compute_importance_weights(
        self,
        cog_proj: torch.Tensor,
        per_proj: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """计算认知与感知特征的重要性权重。

        处理不同模态间的语义鸿沟，通过可学习的重要性评分器
        动态评估两种模态在当前任务中的贡献度。

        Args:
            cog_proj: 投影后的认知特征 (batch, dim_fusion)。
            per_proj: 投影后的感知特征 (batch, dim_fusion)。

        Returns:
            (w_cognitive, w_perception) 归一化后的重要性权重。
        """
        # 拼接两种模态特征
        combined = torch.cat([cog_proj, per_proj], dim=-1)

        # 计算重要性分数
        importance_scores = self.importance_scorer(combined)  # (batch, 2)

        w_cognitive = importance_scores[:, 0:1]  # (batch, 1)
        w_perception = importance_scores[:, 1:2]  # (batch, 1)

        return w_cognitive, w_perception

    def forward(
        self,
        cognitive_embed: torch.Tensor,
        perception_embed: torch.Tensor,
        return_attention: bool = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """前向传播：认知→感知跨模态注意力融合。

        计算流程:
        1. 维度对齐投影: 将不同模态特征映射到统一融合空间
        2. 特征重要性计算: 评估两模态的当前贡献度
        3. 跨模态注意力: 以加权认知意图为query，感知特征为key/value
        4. 感知重加权: 生成感知任务权重向量

        Args:
            cognitive_embed: 认知层工艺意图嵌入 (batch, dim_cognitive)。
            perception_embed: 感知层三视图嵌入 (batch, dim_perception)。
            return_attention: 是否返回注意力权重矩阵。默认False。

        Returns:
            (reweight_vector, attn_weights) 元组:
                - reweight_vector: 感知任务重新加权向量 (batch, dim_perception)。
                - attn_weights: 跨模态注意力权重矩阵，return_attention=True时返回。
        """
        # 维度对齐投影
        cog_proj = self.norm_cog(self.proj_cognitive(cognitive_embed))
        per_proj = self.norm_per(self.proj_perception(perception_embed))

        # 计算模态重要性权重
        w_cog, w_per = self._compute_importance_weights(cog_proj, per_proj)

        # 加权融合 - 将重要性权重应用于特征
        cog_weighted = cog_proj * w_cog  # (batch, dim_fusion)
        per_weighted = per_proj * w_per  # (batch, dim_fusion)

        # 跨模态注意力 - 认知意图(query)关注感知特征(key/value)
        # 添加序列维度: (batch, dim) -> (batch, 1, dim)
        q = cog_weighted.unsqueeze(1)  # (batch, 1, dim_fusion)
        k = per_weighted.unsqueeze(1)  # (batch, 1, dim_fusion)
        v = per_weighted.unsqueeze(1)  # (batch, 1, dim_fusion)

        fused, attn_weights = self.cross_modal_attn(
            q,
            k,
            v,
            return_attention=return_attention,
        )
        fused = fused.squeeze(1)  # (batch, dim_fusion)

        # 生成感知任务重新加权向量
        reweight_vector = self.reweight_generator(fused)  # (batch, dim_perception)

        if return_attention:
            return reweight_vector, attn_weights
        return reweight_vector, None


# 2. 感知执行 融合机制


class TemporalPositionalEncoding(nn.Module):
    """时序位置编码模块。

    为正弦位置编码，为时序序列中的每个位置添加位置信息，
    使得注意力机制能够感知时间顺序。

    Attributes:
        d_model: 编码维度。
        max_len: 最大序列长度。
    """

    def __init__(self, d_model: int = 128, max_len: int = 64):
        """初始化时序位置编码。

        Args:
            d_model: 编码维度。默认128。
            max_len: 支持的最大序列长度。默认64。
        """
        super().__init__()
        self.d_model = d_model

        # 预计算位置编码矩阵
        position = torch.arange(max_len).unsqueeze(1).float()  # (max_len, 1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model))  # (d_model/2,)

        pe = torch.zeros(max_len, d_model)
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer("pe", pe)

    def forward(self, x: torch.Tensor, time_steps: Optional[torch.Tensor] = None) -> torch.Tensor:
        """添加位置编码。

        Args:
            x: 输入序列 (batch, seq_len, d_model)。
            time_steps: 可选的绝对时间步索引。

        Returns:
            添加位置编码后的序列。
        """
        seq_len = x.size(1)
        if time_steps is not None:
            # 使用给定的时间步索引
            pe_slice = self.pe[time_steps.long()]  # (batch, seq_len, d_model)
        else:
            pe_slice = self.pe[:seq_len].unsqueeze(0)  # (1, seq_len, d_model)

        return x + pe_slice.to(x.device)


class TimeDecayAttention(nn.Module):
    """时间衰减注意力机制。

    在标准注意力基础上引入时间衰减因子，使得越近的历史数据
    获得越高的注意力权重，体现时间相关性。

    Attributes:
        decay_rate: 时间衰减率。
    """

    def __init__(self, decay_rate: float = 0.05):
        """初始化时间衰减注意力。

        Args:
            decay_rate: 时间衰减率，控制历史数据权重衰减速度。默认0.05。
        """
        super().__init__()
        self.decay_rate = decay_rate

    def forward(
        self,
        attn_scores: torch.Tensor,
        time_deltas: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """应用时间衰减到注意力分数。

        Args:
            attn_scores: 原始注意力分数 (..., seq_len_q, seq_len_k)。
            time_deltas: 时间间隔 (seq_len_q, seq_len_k) 或可广播形状。
                         正值表示过去的时间步。

        Returns:
            施加时间衰减后的注意力分数。
        """
        if time_deltas is None:
            return attn_scores

        # 计算时间衰减因子: exp(-decay_rate * delta_t)
        decay = torch.exp(-self.decay_rate * time_deltas.abs())

        # 将衰减因子应用到注意力分数
        return attn_scores + torch.log(decay + 1e-10)


class PerceptionToExecutionFusion(nn.Module):
    """感知层到执行层的时序注意力融合模块。

    将感知层的3D几何参数与传感器历史数据进行时序融合，
    生成执行层初始状态向量，作为执行控制的基础参数。

    架构:
        1. 3D几何参数投影
        2. 传感器历史时序位置编码
        3. 时序注意力 + 时间衰减
        4. 执行层初始状态生成

    Attributes:
        dim_geometry: 3D几何参数维度 (默认256)。
        dim_sensor: 传感器嵌入维度 (默认128)。
        seq_len_sensor: 传感器历史序列长度 (默认32)。
        dim_exec: 执行层状态维度 (默认256)。
    """

    def __init__(
        self,
        dim_geometry: int = 256,
        dim_sensor: int = 128,
        seq_len_sensor: int = 32,
        dim_exec: int = 256,
        dim_fusion: int = 256,
        n_heads: int = 8,
        decay_rate: float = 0.05,
        dropout: float = 0.1,
    ):
        """初始化感知→执行融合模块。

        Args:
            dim_geometry: 感知层3D几何参数张量维度。默认256。
            dim_sensor: 传感器历史嵌入维度。默认128。
            seq_len_sensor: 传感器历史序列长度。默认32。
            dim_exec: 执行层输出状态维度。默认256。
            dim_fusion: 融合空间维度。默认256。
            n_heads: 注意力头数。默认8。
            decay_rate: 时间衰减率。默认0.05。
            dropout: Dropout比率。默认0.1。
        """
        super().__init__()

        self.dim_geometry = dim_geometry
        self.dim_sensor = dim_sensor
        self.seq_len_sensor = seq_len_sensor
        self.dim_exec = dim_exec

        # 几何参数投影层
        self.proj_geometry = nn.Linear(dim_geometry, dim_fusion, bias=False)

        # 传感器历史投影层
        self.proj_sensor = nn.Linear(dim_sensor, dim_fusion, bias=False)

        # 时序位置编码
        self.pos_encoding = TemporalPositionalEncoding(d_model=dim_fusion, max_len=seq_len_sensor)

        # 时间衰减注意力
        self.time_decay = TimeDecayAttention(decay_rate=decay_rate)

        # 时序注意力层 - 以几何参数为query，传感器历史为key/value
        self.temporal_attn = CrossLayerAttention(
            dim_q=dim_fusion,
            dim_k=dim_fusion,
            dim_v=dim_fusion,
            dim_out=dim_fusion,
            n_heads=n_heads,
            dropout=dropout,
        )

        # 执行层初始状态向量生成器
        self.state_generator = nn.Sequential(
            nn.Linear(dim_fusion, dim_fusion * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_fusion * 2, dim_exec),
        )

        # 层归一化
        self.norm_geom = nn.LayerNorm(dim_fusion)
        self.norm_sensor = nn.LayerNorm(dim_fusion)

        self._init_weights()

    def _init_weights(self):
        """初始化权重。"""
        for module in [self.proj_geometry, self.proj_sensor]:
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)

    def _compute_time_deltas(
        self,
        seq_len: int,
        time_stamps: Optional[torch.Tensor] = None,
        device: torch.device = torch.device("cpu"),
    ) -> torch.Tensor:
        """计算时间间隔矩阵。

        Args:
            seq_len: 序列长度。
            time_stamps: 实际时间戳 (batch, seq_len)，None则使用均匀间隔。
            device: 计算设备。

        Returns:
            时间间隔矩阵 (1, seq_len)，其中位置0为当前，位置i为i步之前。
        """
        if time_stamps is not None:
            # 使用实际时间戳计算间隔
            latest = time_stamps[:, -1:]  # (batch, 1)
            deltas = latest - time_stamps  # (batch, seq_len)，正值表示过去
        else:
            # 默认均匀时间间隔
            deltas = torch.arange(seq_len - 1, -1, -1, dtype=torch.float32, device=device)
            deltas = deltas.unsqueeze(0)  # (1, seq_len)

        return deltas

    def forward(
        self,
        geometry_params: torch.Tensor,
        sensor_history: torch.Tensor,
        time_stamps: Optional[torch.Tensor] = None,
        return_attention: bool = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """前向传播：感知→执行时序注意力融合。

        计算流程:
        1. 投影几何参数和传感器历史到统一空间
        2. 对传感器历史施加位置编码
        3. 计算时间衰减因子
        4. 时序注意力 - 以几何参数为query，传感器历史为key/value
        5. 生成执行层初始状态向量

        Args:
            geometry_params: 3D几何参数张量 (batch, dim_geometry)。
            sensor_history: 传感器历史嵌入 (batch, seq_len_sensor, dim_sensor)。
            time_stamps: 可选时间戳 (batch, seq_len_sensor)。
            return_attention: 是否返回注意力权重。

        Returns:
            (exec_state, attn_weights) 元组:
                - exec_state: 执行层初始状态向量 (batch, dim_exec)。
                - attn_weights: 时序注意力权重，return_attention=True时返回。
        """
        _ = geometry_params.size(0)

        # 维度投影
        geom_proj = self.norm_geom(self.proj_geometry(geometry_params))  # (batch, dim_fusion)
        sensor_proj = self.norm_sensor(self.proj_sensor(sensor_history))  # (batch, seq_len, dim_fusion)

        # 时序位置编码
        sensor_encoded = self.pos_encoding(sensor_proj)  # (batch, seq_len, dim_fusion)

        # 计算时间衰减因子
        time_deltas = self._compute_time_deltas(
            sensor_history.size(1),
            time_stamps,
            geometry_params.device,
        )  # (batch or 1, seq_len)

        # 时序注意力 - 施加时间衰减偏置到分数上
        # time_decay_bias = log(exp(-decay_rate * delta_t)) = -decay_rate * delta_t
        time_decay_bias = -self.time_decay.decay_rate * time_deltas.abs()  # (B or 1, seq_len)
        q = geom_proj.unsqueeze(1)  # (batch, 1, dim_fusion)
        k = sensor_encoded  # (batch, seq_len, dim_fusion)
        v = sensor_encoded  # (batch, seq_len, dim_fusion)

        fused, attn_weights = self.temporal_attn(
            q,
            k,
            v,
            score_bias=time_decay_bias,
            return_attention=return_attention,
        )
        fused = fused.squeeze(1)  # (batch, dim_fusion)

        # 生成执行层初始状态向量
        exec_state = self.state_generator(fused)  # (batch, dim_exec)

        if return_attention:
            return exec_state, attn_weights
        return exec_state, None


# 3. 执行认知 反馈机制


class AnomalyPriorityEncoder(nn.Module):
    """异常优先级编码器。

    将结构化异常事件编码为带优先级的嵌入向量，
    体现事件的紧急程度与影响范围。

    Attributes:
        dim_event: 事件描述维度。
        dim_priority: 优先级嵌入维度。
        n_severity_levels: 严重等级数量。
    """

    # 严重等级映射 - 数值越高越紧急
    SEVERITY_WEIGHTS = {
        0: 0.1,  # INFO
        1: 0.3,  # LOW
        2: 0.5,  # WARNING
        3: 0.8,  # HIGH
        4: 1.0,  # CRITICAL
    }

    def __init__(
        self,
        dim_event: int = 64,
        dim_priority: int = 64,
        n_severity_levels: int = 5,
    ):
        """初始化异常优先级编码器。

        Args:
            dim_event: 结构化事件描述维度。默认64。
            dim_priority: 优先级嵌入维度。默认64。
            n_severity_levels: 严重等级数量。默认5。
        """
        super().__init__()
        self.dim_event = dim_event
        self.dim_priority = dim_priority

        # 事件特征编码
        self.event_encoder = nn.Sequential(
            nn.Linear(dim_event, dim_priority * 2),
            nn.ReLU(),
            nn.Linear(dim_priority * 2, dim_priority),
        )

        # 严重等级嵌入表
        self.severity_embed = nn.Embedding(n_severity_levels, dim_priority)

    def forward(
        self,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
    ) -> torch.Tensor:
        """编码异常事件并附加优先级权重。

        Args:
            anomaly_events: 异常事件描述 (batch, n_events, dim_event)。
            severity_levels: 严重等级 (batch, n_events)，整数0-4。

        Returns:
            带优先级的异常嵌入 (batch, n_events, dim_priority)。
        """
        # 编码事件描述
        event_encoded = self.event_encoder(anomaly_events)  # (batch, n_events, dim_priority)

        # 获取严重等级嵌入
        severity_embed = self.severity_embed(severity_levels.long())  # (batch, n_events, dim_priority)

        # 优先级加权的异常嵌入
        priority_weighted = event_encoded * torch.sigmoid(severity_embed)

        return priority_weighted


class ExecutionToCognitiveFusion(nn.Module):
    """执行层到认知层的反馈注意力融合模块。

    将执行层实时预测结果与异常事件进行反馈融合，
    生成认知层方案调整参数，用于动态优化初始工艺方案。

    架构:
        1. 执行状态编码
        2. 异常事件优先级编码
        3. 反馈注意力 - 异常事件为query，执行状态为key/value
        4. 认知层方案调整参数生成

    Attributes:
        dim_exec_state: 执行层状态维度 (默认128)。
        dim_anomaly: 异常事件描述维度 (默认64)。
        dim_fusion: 融合空间维度 (默认256)。
        max_events: 最大异常事件数 (默认8)。
    """

    def __init__(
        self,
        dim_exec_state: int = 128,
        dim_anomaly: int = 64,
        dim_fusion: int = 256,
        dim_adjustment: int = 256,
        max_events: int = 8,
        n_heads: int = 8,
        dropout: float = 0.1,
    ):
        """初始化执行→认知反馈融合模块。

        Args:
            dim_exec_state: 执行层实时预测结果向量维度。默认128。
            dim_anomaly: 异常事件结构化描述维度。默认64。
            dim_fusion: 融合空间维度。默认256。
            dim_adjustment: 认知层方案调整参数维度。默认256。
            max_events: 最大同时处理的异常事件数。默认8。
            n_heads: 注意力头数。默认8。
            dropout: Dropout比率。默认0.1。
        """
        super().__init__()

        self.dim_exec_state = dim_exec_state
        self.dim_anomaly = dim_anomaly
        self.dim_fusion = dim_fusion
        self.max_events = max_events

        # 执行状态投影
        self.proj_exec = nn.Linear(dim_exec_state, dim_fusion, bias=False)

        # 异常事件优先级编码器
        self.anomaly_encoder = AnomalyPriorityEncoder(
            dim_event=dim_anomaly,
            dim_priority=dim_fusion,
        )

        # 反馈注意力层 - 以异常事件为query，执行状态为key/value
        # 异常事件驱动对执行状态的关注，快速响应关键异常
        self.feedback_attn = CrossLayerAttention(
            dim_q=dim_fusion,
            dim_k=dim_fusion,
            dim_v=dim_fusion,
            dim_out=dim_fusion,
            n_heads=n_heads,
            dropout=dropout,
        )

        # 认知层方案调整参数生成器
        self.adjustment_generator = nn.Sequential(
            nn.Linear(dim_fusion * 2, dim_fusion * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim_fusion * 2, dim_adjustment),
            nn.Tanh(),  # 输出[-1,1]范围的调整量
        )

        # 紧急程度门控 - 决定反馈信号强度
        self.urgency_gate = nn.Sequential(
            nn.Linear(dim_fusion, 1),
            nn.Sigmoid(),
        )

        # 层归一化
        self.norm_exec = nn.LayerNorm(dim_fusion)
        self.norm_anomaly = nn.LayerNorm(dim_fusion)

        self._init_weights()

    def _init_weights(self):
        """初始化权重。"""
        if isinstance(self.proj_exec, nn.Linear):
            nn.init.xavier_uniform_(self.proj_exec.weight)

    def _pad_anomaly_events(
        self,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """填充异常事件到固定最大长度。

        Args:
            anomaly_events: 异常事件描述 (batch, n_events, dim_anomaly)。
            severity_levels: 严重等级 (batch, n_events)。

        Returns:
            (padded_events, padded_severity, event_mask) 填充后的事件和掩码。
        """
        batch_size, n_events, _ = anomaly_events.shape

        if n_events >= self.max_events:
            # 截断到最大事件数
            return (
                anomaly_events[:, : self.max_events],
                severity_levels[:, : self.max_events],
                torch.ones(batch_size, self.max_events, device=anomaly_events.device),
            )

        # 需要填充
        pad_size = self.max_events - n_events
        padded_events = F.pad(anomaly_events, (0, 0, 0, pad_size), value=0.0)
        padded_severity = F.pad(severity_levels, (0, pad_size), value=0)

        # 事件掩码: 1表示有效事件, 0表示填充
        event_mask = torch.zeros(batch_size, self.max_events, device=anomaly_events.device)
        event_mask[:, :n_events] = 1.0

        return padded_events, padded_severity, event_mask

    def forward(
        self,
        exec_state: torch.Tensor,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
        return_attention: bool = False,
    ) -> Tuple[torch.Tensor, Optional[torch.Tensor]]:
        """前向传播：执行→认知反馈注意力融合。

        计算流程:
        1. 编码执行状态和异常事件
        2. 计算异常优先级权重
        3. 反馈注意力 - 异常事件驱动对执行状态的关注
        4. 紧急程度门控
        5. 生成认知层方案调整参数

        Args:
            exec_state: 执行层实时预测结果向量 (batch, dim_exec_state)。
            anomaly_events: 异常事件描述 (batch, n_events, dim_anomaly)。
            severity_levels: 严重等级 (batch, n_events)，整数0-4。
            return_attention: 是否返回注意力权重。默认False。

        Returns:
            (adjustment_params, attn_weights) 元组:
                - adjustment_params: 认知层方案调整参数 (batch, dim_adjustment)。
                - attn_weights: 反馈注意力权重，return_attention=True时返回。
        """
        # 投影执行状态
        exec_proj = self.norm_exec(self.proj_exec(exec_state))  # (batch, dim_fusion)

        # 填充异常事件到固定长度
        anomaly_padded, severity_padded, event_mask = self._pad_anomaly_events(
            anomaly_events,
            severity_levels,
        )

        # 异常事件优先级编码
        anomaly_encoded = self.anomaly_encoder(anomaly_padded, severity_padded)
        anomaly_encoded = self.norm_anomaly(anomaly_encoded)  # (batch, max_events, dim_fusion)

        # 反馈注意力 - 异常事件(query)关注执行状态(key/value)
        k = exec_proj.unsqueeze(1)  # (batch, 1, dim_fusion)
        v = exec_proj.unsqueeze(1)  # (batch, 1, dim_fusion)

        # 创建注意力掩码 - 屏蔽填充的异常事件
        attn_mask = event_mask == 0  # True表示需要屏蔽的位置
        attn_mask = attn_mask.unsqueeze(-1)  # (batch, max_events, 1)

        fused, attn_weights = self.feedback_attn(
            anomaly_encoded,
            k,
            v,
            mask=attn_mask,
            return_attention=return_attention,
        )  # fused: (batch, max_events, dim_fusion)

        # 聚合多事件反馈 - 使用掩码加权平均
        fused_weighted = fused * event_mask.unsqueeze(-1)  # 屏蔽填充事件
        fused_aggregated = fused_weighted.sum(dim=1) / (event_mask.sum(dim=1, keepdim=True) + 1e-10)
        # (batch, dim_fusion)

        # 紧急程度门控 - 决定反馈信号强度
        urgency = self.urgency_gate(fused_aggregated)  # (batch, 1)

        # 生成认知层方案调整参数
        combined = torch.cat([fused_aggregated, exec_proj * urgency], dim=-1)
        adjustment_params = self.adjustment_generator(combined)  # (batch, dim_adjustment)

        if return_attention:
            return adjustment_params, attn_weights
        return adjustment_params, None


# 完整三层融合系统


class CrossLayerFusionSystem(nn.Module):
    """完整的三层跨层注意力融合系统。

    整合三个融合路径（认知→感知、感知→执行、执行→认知），
    提供统一的接口用于端到端训练和推理。

    融合路径:
        C → P: 认知意图指导感知特征提取
        P → E: 感知几何参数结合传感器历史生成执行状态
        E → C: 执行状态与异常事件反馈优化认知方案

    Attributes:
        cog2per: 认知→感知融合模块。
        per2exec: 感知→执行融合模块。
        exec2cog: 执行→认知反馈模块。
    """

    def __init__(
        self,
        dim_cognitive: int = 256,
        dim_perception: int = 256,
        dim_sensor: int = 128,
        dim_exec_state: int = 128,
        dim_anomaly: int = 64,
        dim_fusion: int = 256,
        seq_len_sensor: int = 32,
        max_events: int = 8,
        n_heads: int = 8,
        decay_rate: float = 0.05,
        dropout: float = 0.1,
    ):
        """初始化完整三层融合系统。

        Args:
            dim_cognitive: 认知层嵌入维度。默认256。
            dim_perception: 感知层嵌入维度。默认256。
            dim_sensor: 传感器嵌入维度。默认128。
            dim_exec_state: 执行层状态维度。默认128。
            dim_anomaly: 异常事件描述维度。默认64。
            dim_fusion: 融合空间维度。默认256。
            seq_len_sensor: 传感器序列长度。默认32。
            max_events: 最大异常事件数。默认8。
            n_heads: 注意力头数。默认8。
            decay_rate: 时间衰减率。默认0.05。
            dropout: Dropout比率。默认0.1。
        """
        super().__init__()

        # 三个融合模块
        self.cog2per = CognitiveToPerceptionFusion(
            dim_cognitive=dim_cognitive,
            dim_perception=dim_perception,
            dim_fusion=dim_fusion,
            n_heads=n_heads,
            dropout=dropout,
        )

        self.per2exec = PerceptionToExecutionFusion(
            dim_geometry=dim_perception,  # 感知层输出作为几何参数
            dim_sensor=dim_sensor,
            seq_len_sensor=seq_len_sensor,
            dim_exec=dim_exec_state,
            dim_fusion=dim_fusion,
            n_heads=n_heads,
            decay_rate=decay_rate,
            dropout=dropout,
        )

        self.exec2cog = ExecutionToCognitiveFusion(
            dim_exec_state=dim_exec_state,
            dim_anomaly=dim_anomaly,
            dim_fusion=dim_fusion,
            dim_adjustment=dim_cognitive,
            max_events=max_events,
            n_heads=n_heads,
            dropout=dropout,
        )

        # 用于保存各层的注意力权重
        self._last_attentions: Dict[str, torch.Tensor] = {}

    @property
    def total_params(self) -> int:
        """获取模型总参数量。"""
        return sum(p.numel() for p in self.parameters())

    def forward_cognitive_to_perception(
        self,
        cognitive_embed: torch.Tensor,
        perception_embed: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """认知→感知融合前向传播。

        Args:
            cognitive_embed: 认知层工艺意图嵌入 (batch, dim_cognitive)。
            perception_embed: 感知层三视图嵌入 (batch, dim_perception)。

        Returns:
            (reweight_vector, attn_weights) 感知重加权向量和注意力权重。
        """
        reweight, attn = self.cog2per(cognitive_embed, perception_embed, return_attention=True)
        if attn is not None:
            self._last_attentions["cog2per"] = attn.detach()
        return reweight, attn

    def forward_perception_to_execution(
        self,
        geometry_params: torch.Tensor,
        sensor_history: torch.Tensor,
        time_stamps: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """感知→执行融合前向传播。

        Args:
            geometry_params: 3D几何参数张量 (batch, dim_perception)。
            sensor_history: 传感器历史 (batch, seq_len, dim_sensor)。
            time_stamps: 可选时间戳。

        Returns:
            (exec_state, attn_weights) 执行状态和注意力权重。
        """
        exec_state, attn = self.per2exec(
            geometry_params,
            sensor_history,
            time_stamps,
            return_attention=True,
        )
        if attn is not None:
            self._last_attentions["per2exec"] = attn.detach()
        return exec_state, attn

    def forward_execution_to_cognitive(
        self,
        exec_state: torch.Tensor,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """执行→认知反馈前向传播。

        Args:
            exec_state: 执行层状态 (batch, dim_exec_state)。
            anomaly_events: 异常事件 (batch, n_events, dim_anomaly)。
            severity_levels: 严重等级。

        Returns:
            (adjustment_params, attn_weights) 调整参数和注意力权重。
        """
        adjustment, attn = self.exec2cog(
            exec_state,
            anomaly_events,
            severity_levels,
            return_attention=True,
        )
        if attn is not None:
            self._last_attentions["exec2cog"] = attn.detach()
        return adjustment, attn

    def forward_full_cycle(
        self,
        cognitive_embed: torch.Tensor,
        perception_embed: torch.Tensor,
        sensor_history: torch.Tensor,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
        time_stamps: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """完整的三层融合循环前向传播。

        一次性执行所有三个融合路径，返回完整的融合结果。

        Args:
            cognitive_embed: 认知层嵌入 (batch, dim_cognitive)。
            perception_embed: 感知层嵌入 (batch, dim_perception)。
            sensor_history: 传感器历史 (batch, seq_len, dim_sensor)。
            anomaly_events: 异常事件 (batch, n_events, dim_anomaly)。
            severity_levels: 严重等级 (batch, n_events)。
            time_stamps: 可选时间戳。

        Returns:
            包含所有融合输出的字典:
                - "perception_reweight": 感知重加权向量
                - "exec_initial_state": 执行初始状态
                - "cognitive_adjustment": 认知方案调整参数
                - "attentions": 各层注意力权重字典
        """
        # C P
        per_reweight, attn_cp = self.forward_cognitive_to_perception(
            cognitive_embed,
            perception_embed,
        )

        # P E (使用重加权的感知特征)
        weighted_perception = perception_embed * per_reweight
        exec_state, attn_pe = self.forward_perception_to_execution(
            weighted_perception,
            sensor_history,
            time_stamps,
        )

        # E C
        adjustment, attn_ec = self.forward_execution_to_cognitive(
            exec_state,
            anomaly_events,
            severity_levels,
        )

        return {
            "perception_reweight": per_reweight,
            "exec_initial_state": exec_state,
            "cognitive_adjustment": adjustment,
            "attentions": {
                "cog2per": attn_cp,
                "per2exec": attn_pe,
                "exec2cog": attn_ec,
            },
        }

    def get_last_attentions(self) -> Dict[str, torch.Tensor]:
        """获取最近一次前向传播的注意力权重。"""
        return self._last_attentions.copy()

    def forward(
        self,
        cognitive_embed: torch.Tensor,
        perception_embed: torch.Tensor,
        sensor_history: torch.Tensor,
        anomaly_events: torch.Tensor,
        severity_levels: torch.Tensor,
        time_stamps: Optional[torch.Tensor] = None,
    ) -> Dict[str, torch.Tensor]:
        """标准forward方法，委托给forward_full_cycle。

        Args:
            cognitive_embed: 认知层嵌入。
            perception_embed: 感知层嵌入。
            sensor_history: 传感器历史。
            anomaly_events: 异常事件。
            severity_levels: 严重等级。
            time_stamps: 可选时间戳。

        Returns:
            融合结果字典。
        """
        return self.forward_full_cycle(
            cognitive_embed,
            perception_embed,
            sensor_history,
            anomaly_events,
            severity_levels,
            time_stamps,
        )

    def extra_repr(self) -> str:
        """返回模块的额外字符串表示。"""
        return f"total_params={self.total_params:,}"
