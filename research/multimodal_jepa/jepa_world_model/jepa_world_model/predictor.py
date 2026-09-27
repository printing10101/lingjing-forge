"""JEPA预测器模块。

实现基于JEPA架构的状态预测器，从当前状态和候选动作预测未来状态。
核心功能：
- 输入：当前状态嵌入向量 + 候选动作嵌入向量
- 处理：通过JEPA架构进行特征提取与预测
- 输出：下一状态嵌入向量 + 多维度奖励估计值
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from app.ai.jepa_world_model.config import JEPAWorldModelConfig


class JEPAPredictor(nn.Module):
    """JEPA状态预测器。

    基于JEPA架构实现从当前状态到未来状态的精准映射。
    使用Transformer风格的编码器-预测器架构，输入状态和动作的
    联合嵌入，输出下一状态嵌入及多维度奖励估计。

    架构：
    Input -> StateEncoder + ActionEncoder -> Fusion -> PredictorMLP -> Output
    - StateEncoder: 线性投影 + LayerNorm
    - ActionEncoder: 线性投影 + LayerNorm
    - Fusion: 交叉注意力融合
    - PredictorMLP: 3层MLP预测器

    Attributes:
        config: 模型配置
        state_encoder: 状态编码器
        action_encoder: 动作编码器
        fusion_layer: 交叉注意力融合层
        predictor: 预测器MLP
        reward_head: 奖励估计头
        confidence_head: 置信度估计头
    """

    def __init__(self, config: JEPAWorldModelConfig):
        super().__init__()
        self.config = config
        embed_dim = config.embed_dim
        hidden_dim = config.predictor_hidden_dim

        # 状态编码器
        self.state_encoder = nn.Sequential(
            nn.Linear(embed_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )

        # 动作编码器
        self.action_encoder = nn.Sequential(
            nn.Linear(embed_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.GELU(),
        )

        # 交叉注意力融合层
        self.fusion_attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=8,
            dropout=config.predictor_dropout,
            batch_first=True,
        )
        self.fusion_norm = nn.LayerNorm(hidden_dim)

        # 预测器MLP
        layers = []
        in_dim = hidden_dim
        for i in range(config.predictor_depth):
            out_dim = hidden_dim if i < config.predictor_depth - 1 else embed_dim
            layers.append(nn.Linear(in_dim, out_dim))
            if i < config.predictor_depth - 1:
                layers.append(nn.GELU())
                layers.append(nn.LayerNorm(out_dim))
                if config.predictor_dropout > 0:
                    layers.append(nn.Dropout(config.predictor_dropout))
            in_dim = out_dim
        self.predictor = nn.Sequential(*layers)

        # 奖励估计头（输出4维：质量、效率、风险、综合）
        self.reward_head = nn.Sequential(
            nn.Linear(embed_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, 4),
        )

        # 置信度估计头
        self.confidence_head = nn.Sequential(
            nn.Linear(embed_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Linear(hidden_dim // 2, 1),
            nn.Sigmoid(),
        )

        self._init_weights()

    def _init_weights(self):
        """初始化权重。"""
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.trunc_normal_(m.weight, std=0.02)
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(
        self,
        state_embedding: torch.Tensor,
        action_embedding: torch.Tensor,
    ) -> Dict[str, torch.Tensor]:
        """前向传播：预测下一状态及奖励。

        Args:
            state_embedding: 当前状态嵌入 (B, embed_dim) 或 (B, 1, embed_dim)
            action_embedding: 动作嵌入 (B, embed_dim) 或 (B, 1, embed_dim)

        Returns:
            包含以下键的字典：
            - next_state_embedding: 预测的下一状态嵌入 (B, embed_dim)
            - reward_estimates: 奖励估计 (B, 4) [质量, 效率, 风险, 综合]
            - confidence: 预测置信度 (B, 1)
        """
        # 确保输入维度正确
        if state_embedding.dim() == 2:
            state_embedding = state_embedding.unsqueeze(1)  # (B, 1, D)
        if action_embedding.dim() == 2:
            action_embedding = action_embedding.unsqueeze(1)  # (B, 1, D)

        # 编码状态和动作
        state_encoded = self.state_encoder(state_embedding)  # (B, 1, H)
        action_encoded = self.action_encoder(action_embedding)  # (B, 1, H)

        # 交叉注意力融合：以状态为query，动作为key/value
        fused, _ = self.fusion_attention(
            state_encoded, action_encoded, action_encoded,
        )
        fused = self.fusion_norm(fused + state_encoded)  # 残差连接

        # 压缩序列维度
        fused = fused.squeeze(1)  # (B, H)

        # 预测下一状态嵌入
        next_state_embedding = self.predictor(fused)  # (B, embed_dim)

        # L2归一化输出嵌入
        next_state_embedding = F.normalize(next_state_embedding, p=2, dim=-1)

        # 奖励估计
        reward_estimates = self.reward_head(next_state_embedding)  # (B, 4)

        # 置信度估计
        confidence = self.confidence_head(next_state_embedding)  # (B, 1)

        return {
            "next_state_embedding": next_state_embedding,
            "reward_estimates": reward_estimates,
            "confidence": confidence,
        }

    def predict_step(
        self,
        state_embedding: np.ndarray,
        action_embedding: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """单步预测（NumPy接口）。

        Args:
            state_embedding: 当前状态嵌入 (512,)
            action_embedding: 动作嵌入 (512,)

        Returns:
            预测结果字典
        """
        self.eval()
        device = next(self.parameters()).device
        with torch.no_grad():
            state_t = torch.from_numpy(state_embedding).float().unsqueeze(0).to(device)
            action_t = torch.from_numpy(action_embedding).float().unsqueeze(0).to(device)
            output = self.forward(state_t, action_t)
            return {
                "next_state_embedding": output["next_state_embedding"].squeeze(0).cpu().numpy(),
                "reward_estimates": output["reward_estimates"].squeeze(0).cpu().numpy(),
                "confidence": output["confidence"].squeeze(0).cpu().numpy(),
            }

    def predict_trajectory(
        self,
        initial_state_embedding: np.ndarray,
        action_sequence: np.ndarray,
    ) -> Dict[str, np.ndarray]:
        """预测完整轨迹。

        Args:
            initial_state_embedding: 初始状态嵌入 (512,)
            action_sequence: 动作序列嵌入 (T, 512)

        Returns:
            包含以下键的字典：
            - state_trajectory: 状态轨迹 (T+1, 512)
            - reward_trajectory: 奖励轨迹 (T, 4)
            - confidence_trajectory: 置信度轨迹 (T,)
        """
        self.eval()
        _ = next(self.parameters()).device
        T = action_sequence.shape[0]
        state_trajectory = np.zeros((T + 1, self.config.embed_dim), dtype=np.float32)
        reward_trajectory = np.zeros((T, 4), dtype=np.float32)
        confidence_trajectory = np.zeros(T, dtype=np.float32)

        state_trajectory[0] = initial_state_embedding

        current_state = initial_state_embedding.copy()
        with torch.no_grad():
            for t in range(T):
                result = self.predict_step(current_state, action_sequence[t])
                state_trajectory[t + 1] = result["next_state_embedding"]
                reward_trajectory[t] = result["reward_estimates"]
                confidence_trajectory[t] = float(np.squeeze(result["confidence"]))
                current_state = result["next_state_embedding"]

        return {
            "state_trajectory": state_trajectory,
            "reward_trajectory": reward_trajectory,
            "confidence_trajectory": confidence_trajectory,
        }

    def compute_prediction_loss(
        self,
        pred_state: torch.Tensor,
        target_state: torch.Tensor,
    ) -> torch.Tensor:
        """计算预测损失（余弦相似度损失）。

        Args:
            pred_state: 预测状态嵌入 (B, D)
            target_state: 目标状态嵌入 (B, D)

        Returns:
            损失值
        """
        cosine_sim = F.cosine_similarity(pred_state, target_state, dim=-1)
        return (1.0 - cosine_sim).mean()
