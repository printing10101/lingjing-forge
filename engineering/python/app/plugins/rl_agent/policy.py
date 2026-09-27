"""PPO 策略网络（Actor）.

输入：当前加工状态向量（世界模型预测的轨迹摘要）
输出：切削参数动作向量（主轴转速 / 进给 / 切深 等的连续调整量）

架构
----
- 状态编码器：MLP [state_dim → 128 → 64]
- 动作均值头：MLP [64 → action_dim]
- 动作对数标准差：可学习参数 log_std（PPO 标准做法）

双路径
------
torch 不可用时回退到 NumPy 朴素实现（仅推理，无梯度），与
world_model/net.py 风格对齐。

随机种子
--------
训练与推理均固定随机种子（torch.manual_seed / np.random.seed），
保证可复现，符合学术诚信要求。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

try:
    import torch
    import torch.nn as nn

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None
    nn = None

logger = logging.getLogger(__name__)


@dataclass
class PolicyConfig:
    """策略网络配置.

    Attributes
    ----------
    state_dim : int
        状态向量维度（与世界模型 trajectory_metrics 对齐，默认 8）。
    action_dim : int
        动作向量维度（切削参数调整量，默认 4：主轴转速 / 进给 /
        切深 / 切宽）。
    hidden_dim : int
        隐藏层维度。
    max_action_norm : float
        动作向量最大 L2 范数（输出裁剪）。
    seed : int
        随机种子。
    """

    state_dim: int = 8
    action_dim: int = 4
    hidden_dim: int = 64
    max_action_norm: float = 1.0
    seed: int = 42

    def validate(self) -> None:
        if self.state_dim < 1:
            raise ValueError(f"state_dim 必须 >= 1, 当前: {self.state_dim}")
        if self.action_dim < 1:
            raise ValueError(f"action_dim 必须 >= 1, 当前: {self.action_dim}")
        if self.hidden_dim < 1:
            raise ValueError(f"hidden_dim 必须 >= 1, 当前: {self.hidden_dim}")
        if self.max_action_norm <= 0:
            raise ValueError(f"max_action_norm 必须为正数, 当前: {self.max_action_norm}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
            "hidden_dim": self.hidden_dim,
            "max_action_norm": self.max_action_norm,
            "seed": self.seed,
        }


# torch 实现


if HAS_TORCH:

    class PolicyNet(nn.Module):
        """PPO 策略网络（Actor）—— torch 实现.

        输出高斯策略的均值与对数标准差，采样后裁剪到 max_action_norm。
        """

        def __init__(self, config: PolicyConfig) -> None:
            super().__init__()
            config.validate()
            self._config = config

            torch.manual_seed(config.seed)

            self.encoder = nn.Sequential(
                nn.Linear(config.state_dim, config.hidden_dim),
                nn.Tanh(),
                nn.Linear(config.hidden_dim, config.hidden_dim),
                nn.Tanh(),
            )
            self.mean_head = nn.Linear(config.hidden_dim, config.action_dim)
            # log_std 作为可学习参数（PPO 标准做法），初始化为 0（std=1）
            self.log_std = nn.Parameter(torch.zeros(config.action_dim))

        def forward(self, state: torch.Tensor) -> dict[str, torch.Tensor]:
            """前向传播.

            Returns
            -------
            dict[str, torch.Tensor]
                - ``action_mean``: 动作均值 [batch, action_dim]
                - ``action_log_std``: 动作对数标准差 [action_dim]
                - ``action``: 采样动作（训练时）或均值（推理时）[batch, action_dim]
            """
            h = self.encoder(state)
            mean = self.mean_head(h)
            log_std = self.log_std.expand_as(mean)

            if self.training:
                std = torch.exp(log_std)
                action = mean + std * torch.randn_like(mean)
            else:
                action = mean

            # L2 范数裁剪
            action = self._clip_action_norm(action)
            return {
                "action_mean": mean,
                "action_log_std": log_std,
                "action": action,
            }

        def _clip_action_norm(self, action: torch.Tensor) -> torch.Tensor:
            """裁剪动作向量的 L2 范数到 max_action_norm."""
            max_norm = self._config.max_action_norm
            norms = torch.norm(action, p=2, dim=-1, keepdim=True)
            scale = torch.clamp(max_norm / (norms + 1e-8), max=1.0)
            return action * scale

        def get_config(self) -> PolicyConfig:
            return self._config

else:

    class PolicyNet:  # type: ignore[no-redef]
        """PPO 策略网络（Actor）—— NumPy 回退实现.

        torch 不可用时使用随机初始化的权重，仅支持推理（无梯度）。
        输出动作语义不可靠，仅用于接口验证与冒烟测试。
        """

        def __init__(self, config: PolicyConfig) -> None:
            config.validate()
            self._config = config
            # M21 修复：使用局部 RandomState 替代全局 np.random.seed，
            # 避免污染调用方的全局随机状态
            rng = np.random.RandomState(config.seed)

            # 随机初始化权重（仅推理回退，无训练意义）
            self._w1 = rng.randn(config.state_dim, config.hidden_dim).astype(np.float32) * 0.1
            self._b1 = np.zeros(config.hidden_dim, dtype=np.float32)
            self._w2 = rng.randn(config.hidden_dim, config.hidden_dim).astype(np.float32) * 0.1
            self._b2 = np.zeros(config.hidden_dim, dtype=np.float32)
            self._w_mean = rng.randn(config.hidden_dim, config.action_dim).astype(np.float32) * 0.1
            self._b_mean = np.zeros(config.action_dim, dtype=np.float32)
            self._log_std = np.zeros(config.action_dim, dtype=np.float32)

            logger.warning("PolicyNet: torch 不可用，使用 NumPy 回退实现。输出动作仅用于接口验证，不具备训练意义。")

        def __call__(self, state: Any) -> dict[str, np.ndarray]:
            state_arr = np.asarray(state, dtype=np.float32)
            if state_arr.ndim == 1:
                state_arr = state_arr[None, :]

            h1 = np.tanh(state_arr @ self._w1 + self._b1)
            h2 = np.tanh(h1 @ self._w2 + self._b2)
            mean = h2 @ self._w_mean + self._b_mean

            # 推理模式：动作 = 均值
            action = self._clip_action_norm(mean)
            return {
                "action_mean": mean,
                "action_log_std": self._log_std,
                "action": action,
            }

        def _clip_action_norm(self, action: np.ndarray) -> np.ndarray:
            max_norm = self._config.max_action_norm
            norms = np.linalg.norm(action, axis=-1, keepdims=True)
            scale = np.clip(max_norm / (norms + 1e-8), 0.0, 1.0)
            return action * scale

        def get_config(self) -> PolicyConfig:
            return self._config


__all__ = ["PolicyConfig", "PolicyNet"]
