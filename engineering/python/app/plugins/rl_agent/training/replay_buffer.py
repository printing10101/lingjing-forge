"""经验回放缓冲区（Replay Buffer）.

对应 ADR-017 第 4 节。存储 RL 训练过程中的经验元组
``(s_t, a_t, r_t, s_{t+1}, done, log_prob, value)``，
供 PPO 训练器采样 mini-batch 更新策略网络。

设计要点
--------
1. **离线 RL 优先**：v1 仅支持离线 RL，经验来自历史数据 + 仿真环境
2. **环形缓冲区**：固定容量，超出后覆盖最旧经验（FIFO）
3. **优先级采样**（可选）：基于 TD 误差的优先级采样，加速收敛
4. **线程安全**：``append`` / ``sample`` 使用锁保护，支持训练线程与
   数据收集线程并发（异步收集 + 同步训练）
5. **序列化**：支持 ``to_dict`` / ``from_dict``，便于 snapshot 持久化

数据结构
--------
经验元组字段：
- ``state``：状态向量 ``np.ndarray [state_dim]``
- ``action``：动作向量 ``np.ndarray [action_dim]``
- ``reward``：标量奖励 ``float``
- ``next_state``：下一状态向量 ``np.ndarray [state_dim]``
- ``done``：episode 结束标志 ``bool``
- ``log_prob``：策略对数概率 ``float``（PPO 重要性采样用）
- ``value``：状态价值估计 ``float``（GAE 优势估计用）
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# 经验元组


@dataclass
class Experience:
    """单步经验元组.

    Attributes
    ----------
    state : np.ndarray
        状态向量 ``[state_dim]``.
    action : np.ndarray
        动作向量 ``[action_dim]``.
    reward : float
        标量奖励.
    next_state : np.ndarray
        下一状态向量 ``[state_dim]``.
    done : bool
        episode 是否结束.
    log_prob : float
        策略 ``log π(a|s)``，PPO 重要性采样用.
    value : float
        状态价值估计 ``V(s)``，GAE 优势估计用.
    """

    state: np.ndarray
    action: np.ndarray
    reward: float
    next_state: np.ndarray
    done: bool
    log_prob: float = 0.0
    value: float = 0.0

    def __post_init__(self) -> None:
        self.state = np.asarray(self.state, dtype=np.float32)
        self.action = np.asarray(self.action, dtype=np.float32)
        self.next_state = np.asarray(self.next_state, dtype=np.float32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.tolist(),
            "action": self.action.tolist(),
            "reward": self.reward,
            "next_state": self.next_state.tolist(),
            "done": self.done,
            "log_prob": self.log_prob,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Experience":
        return cls(
            state=np.asarray(data["state"], dtype=np.float32),
            action=np.asarray(data["action"], dtype=np.float32),
            reward=float(data["reward"]),
            next_state=np.asarray(data["next_state"], dtype=np.float32),
            done=bool(data["done"]),
            log_prob=float(data.get("log_prob", 0.0)),
            value=float(data.get("value", 0.0)),
        )


# 缓冲区统计


@dataclass
class ReplayBufferStats:
    """缓冲区统计信息.

    Attributes
    ----------
    size : int
        当前经验数量.
    capacity : int
        缓冲区容量.
    mean_reward : float
        缓冲区中所有经验的平均奖励.
    std_reward : float
        奖励标准差.
    mean_value : float
        平均状态价值估计.
    done_count : int
        已完成的 episode 数量（done=True 的经验数）.
    """

    size: int
    capacity: int
    mean_reward: float
    std_reward: float
    mean_value: float
    done_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "capacity": self.capacity,
            "mean_reward": self.mean_reward,
            "std_reward": self.std_reward,
            "mean_value": self.mean_value,
            "done_count": self.done_count,
        }


# 经验回放缓冲区


class ReplayBuffer:
    """经验回放缓冲区.

    环形缓冲区实现，固定容量，超出后覆盖最旧经验。

    使用示例
    --------
    >>> buffer = ReplayBuffer(capacity=10000)
    >>> buffer.append(experience)
    >>> batch = buffer.sample(batch_size=64)
    >>> stats = buffer.stats()

    线程安全
    --------
    所有公开方法使用 ``threading.Lock`` 保护，支持训练线程与
    数据收集线程并发调用。
    """

    def __init__(
        self,
        capacity: int = 10000,
        state_dim: int = 8,
        action_dim: int = 4,
    ) -> None:
        """初始化缓冲区.

        Args:
            capacity: 缓冲区容量（最大经验数）.
            state_dim: 状态向量维度（仅用于统计，不强制校验）.
            action_dim: 动作向量维度（仅用于统计，不强制校验）.

        Raises
        ------
        ValueError
            ``capacity`` 非正数.
        """
        if capacity <= 0:
            raise ValueError(f"capacity 必须为正数: {capacity}")
        self._capacity = capacity
        self._state_dim = state_dim
        self._action_dim = action_dim
        self._buffer: deque[Experience] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        """缓冲区容量."""
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    @property
    def size(self) -> int:
        """当前经验数量."""
        with self._lock:
            return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        """缓冲区是否为空."""
        with self._lock:
            return len(self._buffer) == 0

    def append(self, experience: Experience) -> None:
        """添加一条经验.

        缓冲区满时自动覆盖最旧经验（FIFO）。

        Args:
            experience: 经验元组.
        """
        with self._lock:
            self._buffer.append(experience)

    def extend(self, experiences: list[Experience]) -> None:
        """批量添加经验.

        Args:
            experiences: 经验列表.
        """
        with self._lock:
            for exp in experiences:
                self._buffer.append(exp)

    def sample(self, batch_size: int = 64, seed: int | None = None) -> list[Experience]:
        """随机采样 mini-batch.

        Args:
            batch_size: 采样批次大小.
            seed: 随机种子（用于可复现性，None 表示不固定）.

        Returns
        -------
        list[Experience]
            采样的经验列表。若缓冲区大小不足 ``batch_size``，返回全部经验。

        Raises
        ------
        ValueError
            ``batch_size`` 非正数.
        """
        if batch_size <= 0:
            raise ValueError(f"batch_size 必须为正数: {batch_size}")

        with self._lock:
            size = len(self._buffer)
            if size == 0:
                return []
            actual_size = min(batch_size, size)
            rng = np.random.default_rng(seed)
            indices = rng.choice(size, size=actual_size, replace=False)
            buffer_list = list(self._buffer)
            return [buffer_list[i] for i in indices]

    def sample_arrays(self, batch_size: int = 64, seed: int | None = None) -> dict[str, np.ndarray]:
        """随机采样并返回数组形式（便于 PPO 训练器批量计算）.

        Args:
            batch_size: 采样批次大小.
            seed: 随机种子.

        Returns
        -------
        dict[str, np.ndarray]
            含以下键：
            - ``states``: ``[batch, state_dim]``
            - ``actions``: ``[batch, action_dim]``
            - ``rewards``: ``[batch]``
            - ``next_states``: ``[batch, state_dim]``
            - ``dones``: ``[batch]``
            - ``log_probs``: ``[batch]``
            - ``values``: ``[batch]``

        若缓冲区为空，返回空数组。
        """
        batch = self.sample(batch_size=batch_size, seed=seed)
        if not batch:
            return {
                "states": np.empty((0, self._state_dim), dtype=np.float32),
                "actions": np.empty((0, self._action_dim), dtype=np.float32),
                "rewards": np.empty((0,), dtype=np.float32),
                "next_states": np.empty((0, self._state_dim), dtype=np.float32),
                "dones": np.empty((0,), dtype=np.bool_),
                "log_probs": np.empty((0,), dtype=np.float32),
                "values": np.empty((0,), dtype=np.float32),
            }

        return {
            "states": np.stack([e.state for e in batch]),
            "actions": np.stack([e.action for e in batch]),
            "rewards": np.array([e.reward for e in batch], dtype=np.float32),
            "next_states": np.stack([e.next_state for e in batch]),
            "dones": np.array([e.done for e in batch], dtype=np.bool_),
            "log_probs": np.array([e.log_prob for e in batch], dtype=np.float32),
            "values": np.array([e.value for e in batch], dtype=np.float32),
        }

    def stats(self) -> ReplayBufferStats:
        """计算缓冲区统计信息.

        Returns
        -------
        ReplayBufferStats
            统计信息（平均奖励 / 标准差 / 平均价值 / episode 数）.
        """
        with self._lock:
            size = len(self._buffer)
            if size == 0:
                return ReplayBufferStats(
                    size=0,
                    capacity=self._capacity,
                    mean_reward=0.0,
                    std_reward=0.0,
                    mean_value=0.0,
                    done_count=0,
                )

            rewards = np.array([e.reward for e in self._buffer])
            values = np.array([e.value for e in self._buffer])
            done_count = sum(1 for e in self._buffer if e.done)

            return ReplayBufferStats(
                size=size,
                capacity=self._capacity,
                mean_reward=float(rewards.mean()),
                std_reward=float(rewards.std()),
                mean_value=float(values.mean()),
                done_count=done_count,
            )

    def clear(self) -> None:
        """清空缓冲区."""
        with self._lock:
            self._buffer.clear()

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的字典（snapshot 用）.

        注意：大型缓冲区序列化后体积较大，建议仅在 checkpoint 时调用。
        """
        with self._lock:
            return {
                "capacity": self._capacity,
                "state_dim": self._state_dim,
                "action_dim": self._action_dim,
                "size": len(self._buffer),
                "experiences": [e.to_dict() for e in self._buffer],
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplayBuffer":
        """从字典反序列化缓冲区."""
        buffer = cls(
            capacity=int(data["capacity"]),
            state_dim=int(data.get("state_dim", 8)),
            action_dim=int(data.get("action_dim", 4)),
        )
        for exp_data in data.get("experiences", []):
            buffer._buffer.append(Experience.from_dict(exp_data))
        return buffer


__all__ = ["Experience", "ReplayBuffer", "ReplayBufferStats"]
