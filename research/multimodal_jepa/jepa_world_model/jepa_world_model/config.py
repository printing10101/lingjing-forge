"""JEPA World Model 配置模块。

定义基于JEPA的工艺规划World Model的所有可配置参数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class JEPAWorldModelConfig:
    """JEPA World Model 全局配置。

    Attributes:
        # 嵌入维度
        embed_dim: 统一嵌入维度（512维）
        state_embed_dim: 状态嵌入维度
        action_embed_dim: 动作嵌入维度

        # 预测器参数
        predictor_hidden_dim: 预测器隐藏层维度
        predictor_depth: 预测器MLP层数
        predictor_dropout: 预测器dropout率

        # CEM规划参数
        cem_population_size: CEM采样候选序列数（N）
        cem_top_k_ratio: Top-K%精英比例
        cem_max_iterations: 最大迭代次数
        cem_planning_horizon: 规划步数
        cem_elite_fraction: 精英比例

        # 奖励函数权重
        reward_quality_weight: 质量达标率权重（40%）
        reward_efficiency_weight: 生产效率权重（35%）
        reward_risk_weight: 工艺风险权重（25%）

        # 训练参数
        epochs: 训练轮次
        initial_lr: 初始学习率
        batch_size: 批处理大小
        weight_decay: L2正则化系数
        early_stopping_patience: 早停耐心值

        # 操作类型定义
        operation_types: 支持的操作类型列表
    """

    # 嵌入维度
    embed_dim: int = 512
    state_embed_dim: int = 512
    action_embed_dim: int = 512

    # 预测器参数
    predictor_hidden_dim: int = 1024
    predictor_depth: int = 3
    predictor_dropout: float = 0.1

    # CEM规划参数
    cem_population_size: int = 500
    cem_top_k_ratio: float = 0.15
    cem_max_iterations: int = 20
    cem_planning_horizon: int = 10
    cem_elite_fraction: float = 0.15

    # 奖励函数权重
    reward_quality_weight: float = 0.40
    reward_efficiency_weight: float = 0.35
    reward_risk_weight: float = 0.25

    # 训练参数
    epochs: int = 100
    initial_lr: float = 1e-4
    batch_size: int = 32
    weight_decay: float = 1e-5
    early_stopping_patience: int = 10

    # 操作类型定义
    operation_types: List[str] = field(
        default_factory=lambda: [
            "rough_milling",
            "finish_milling",
            "drilling",
            "reaming",
            "tapping",
            "boring",
            "facing",
            "chamfering",
            "grooving",
            "threading",
        ]
    )

    @property
    def cem_num_elite(self) -> int:
        """精英样本数量。"""
        return max(1, int(self.cem_population_size * self.cem_elite_fraction))
