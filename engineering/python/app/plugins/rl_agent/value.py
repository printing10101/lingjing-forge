"""PPO 值网络（Critic）.

输入：当前加工状态向量
输出：状态价值标量 V(s)

用途
----
- 训练阶段：计算优势函数 A(s,a) = R + γV(s') - V(s)（GAE）
- 推理阶段：可选输出，用于可解释性（"当前状态的价值估计"）

v1 离线 RL 场景下，值网络主要用于训练阶段。推理阶段策略网络
独立输出动作，值网络可选输出价值估计用于日志记录。
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
class ValueConfig:
    """值网络配置.

    Attributes
    ----------
    state_dim : int
        状态向量维度（与 PolicyConfig.state_dim 对齐）。
    hidden_dim : int
        隐藏层维度。
    seed : int
        随机种子。
    """

    state_dim: int = 8
    hidden_dim: int = 64
    seed: int = 42

    def validate(self) -> None:
        if self.state_dim < 1:
            raise ValueError(f"state_dim 必须 >= 1, 当前: {self.state_dim}")
        if self.hidden_dim < 1:
            raise ValueError(f"hidden_dim 必须 >= 1, 当前: {self.hidden_dim}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "hidden_dim": self.hidden_dim,
            "seed": self.seed,
        }


if HAS_TORCH:

    class ValueNet(nn.Module):
        """PPO 值网络（Critic）—— torch 实现."""

        def __init__(self, config: ValueConfig) -> None:
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
            self.value_head = nn.Linear(config.hidden_dim, 1)

        def forward(self, state: torch.Tensor) -> torch.Tensor:
            """前向传播.

            Returns
            -------
            torch.Tensor
                状态价值 [batch, 1]
            """
            h = self.encoder(state)
            return self.value_head(h)

        def get_config(self) -> ValueConfig:
            return self._config

else:

    class ValueNet:  # type: ignore[no-redef]
        """PPO 值网络（Critic）—— NumPy 回退实现."""

        def __init__(self, config: ValueConfig) -> None:
            config.validate()
            self._config = config
            # M22 修复：使用局部 RandomState 替代全局 np.random.seed，
            # 避免污染调用方的全局随机状态
            rng = np.random.RandomState(config.seed)

            self._w1 = rng.randn(config.state_dim, config.hidden_dim).astype(np.float32) * 0.1
            self._b1 = np.zeros(config.hidden_dim, dtype=np.float32)
            self._w2 = rng.randn(config.hidden_dim, config.hidden_dim).astype(np.float32) * 0.1
            self._b2 = np.zeros(config.hidden_dim, dtype=np.float32)
            self._w_v = rng.randn(config.hidden_dim, 1).astype(np.float32) * 0.1
            self._b_v = np.zeros(1, dtype=np.float32)

            logger.warning("ValueNet: torch 不可用，使用 NumPy 回退实现。输出价值仅用于接口验证，不具备训练意义。")

        def __call__(self, state: Any) -> np.ndarray:
            state_arr = np.asarray(state, dtype=np.float32)
            if state_arr.ndim == 1:
                state_arr = state_arr[None, :]
            h1 = np.tanh(state_arr @ self._w1 + self._b1)
            h2 = np.tanh(h1 @ self._w2 + self._b2)
            value = h2 @ self._w_v + self._b_v
            return value

        def get_config(self) -> ValueConfig:
            return self._config


__all__ = ["ValueConfig", "ValueNet"]
