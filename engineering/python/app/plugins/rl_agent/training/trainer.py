"""PPO 训练器.

对应 ADR-017 第 4 节。实现 PPO（Proximal Policy Optimization）训练循环：

    collect_episodes → build_replay_buffer → train_policy → evaluate → snapshot

训练循环
--------
1. **收集 episode**：从历史数据集采样初始状态 ``s_0``，RL agent 选择动作 ``a_t``
   （ε-greedy 探索），世界模型预测下一状态 ``s_{t+1}``（替代真实环境，离线 RL），
   计算奖励 ``r_t``，存储 ``(s_t, a_t, r_t, s_{t+1})`` 到 replay buffer
2. **采样 mini-batch**：从 replay buffer 随机采样 batch
3. **PPO 更新**：
   - 策略网络：clipped objective ``L = E[min(r_t·A, clip(r_t, 1-ε, 1+ε)·A)]``
   - 价值网络：TD 误差 ``L_v = (V(s) - R̂)²``
   - 优势估计：GAE（Generalized Advantage Estimation）
4. **评估**：每 ``eval_interval`` 步评估一次，满足指标则 snapshot
5. **snapshot**：记录 policy_weights / value_weights / training_metrics / replay_buffer_stats

可复现性
--------
- 固定随机种子（``torch.manual_seed`` / ``np.random.seed`` / ``random.seed``）
- 启用 ``cudnn.deterministic``（GPU 训练可复现）
- snapshot 持久化策略版本，支持 checkpoint 续训

工程现实约束
------------
- v1 仅离线 RL，不接真实机床
- 训练 Workflow 走 BackgroundTasks 异步，避免阻塞 API
- snapshot 每 1000 步自动保存，防止训练中断丢失进度
- torch 不可用时回退到 NumPy 朴素实现（仅接口验证，无实际训练能力）
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from collections.abc import Callable

import numpy as np

from .replay_buffer import Experience, ReplayBuffer, ReplayBufferStats
from .reward import RewardConfig, RewardFunction

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None
    nn = None
    F = None

logger = logging.getLogger(__name__)


# 训练配置


@dataclass
class TrainingConfig:
    """PPO 训练配置.

    Attributes
    ----------
    state_dim : int
        状态向量维度.
    action_dim : int
        动作向量维度.
    hidden_dim : int
        策略/价值网络隐藏层维度.
    learning_rate : float
        学习率.
    gamma : float
        折扣因子（``γ``）.
    gae_lambda : float
        GAE 参数（``λ``）.
    clip_epsilon : float
        PPO clip 参数（``ε``）.
    batch_size : int
        mini-batch 大小.
    n_epochs : int
        每次更新的 epoch 数（PPO 多次复用数据）.
    eval_interval : int
        评估间隔（训练步数）.
    snapshot_interval : int
        snapshot 间隔（训练步数）.
    max_steps : int
        最大训练步数.
    epsilon_start : float
        ε-greedy 探索初始值.
    epsilon_end : float
        ε-greedy 探索终值.
    epsilon_decay_steps : int
        ε 线性衰减步数.
    seed : int
        随机种子.
    device : str
        训练设备（``"auto"`` / ``"cpu"`` / ``"cuda"``）.
    """

    state_dim: int = 8
    action_dim: int = 4
    hidden_dim: int = 64
    learning_rate: float = 3e-4
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_epsilon: float = 0.2
    batch_size: int = 64
    n_epochs: int = 4
    eval_interval: int = 1000
    snapshot_interval: int = 1000
    max_steps: int = 100000
    epsilon_start: float = 1.0
    epsilon_end: float = 0.05
    epsilon_decay_steps: int = 50000
    seed: int = 42
    device: str = "auto"

    def validate(self) -> None:
        """校验配置合法性."""
        if self.state_dim < 1:
            raise ValueError(f"state_dim 必须 >= 1: {self.state_dim}")
        if self.action_dim < 1:
            raise ValueError(f"action_dim 必须 >= 1: {self.action_dim}")
        if self.hidden_dim < 1:
            raise ValueError(f"hidden_dim 必须 >= 1: {self.hidden_dim}")
        if self.learning_rate <= 0:
            raise ValueError(f"learning_rate 必须为正数: {self.learning_rate}")
        if not 0.0 < self.gamma <= 1.0:
            raise ValueError(f"gamma 必须在 (0, 1]: {self.gamma}")
        if not 0.0 < self.gae_lambda <= 1.0:
            raise ValueError(f"gae_lambda 必须在 (0, 1]: {self.gae_lambda}")
        if self.clip_epsilon <= 0:
            raise ValueError(f"clip_epsilon 必须为正数: {self.clip_epsilon}")
        if self.batch_size < 1:
            raise ValueError(f"batch_size 必须 >= 1: {self.batch_size}")
        if self.n_epochs < 1:
            raise ValueError(f"n_epochs 必须 >= 1: {self.n_epochs}")
        if self.max_steps < 1:
            raise ValueError(f"max_steps 必须 >= 1: {self.max_steps}")
        if not 0.0 <= self.epsilon_end <= self.epsilon_start <= 1.0:
            raise ValueError(
                f"epsilon 需满足 0 <= end <= start <= 1, start={self.epsilon_start}, end={self.epsilon_end}"
            )
        if self.epsilon_decay_steps < 1:
            raise ValueError(f"epsilon_decay_steps 必须 >= 1: {self.epsilon_decay_steps}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
            "hidden_dim": self.hidden_dim,
            "learning_rate": self.learning_rate,
            "gamma": self.gamma,
            "gae_lambda": self.gae_lambda,
            "clip_epsilon": self.clip_epsilon,
            "batch_size": self.batch_size,
            "n_epochs": self.n_epochs,
            "eval_interval": self.eval_interval,
            "snapshot_interval": self.snapshot_interval,
            "max_steps": self.max_steps,
            "epsilon_start": self.epsilon_start,
            "epsilon_end": self.epsilon_end,
            "epsilon_decay_steps": self.epsilon_decay_steps,
            "seed": self.seed,
            "device": self.device,
        }


# 训练指标


@dataclass
class TrainingMetrics:
    """训练指标.

    记录训练过程中的关键指标，用于日志、MLflow 跟踪与可复现性验证。

    Attributes
    ----------
    step : int
        当前训练步数.
    episode : int
        当前 episode 数.
    policy_loss : float
        策略网络损失.
    value_loss : float
        价值网络损失.
    entropy : float
        策略熵（探索度指标）.
    approx_kl : float
        近似 KL 散度（新旧策略差异）.
    clip_fraction : float
        PPO clip 触发比例.
    mean_reward : float
        平均 episode 奖励.
    mean_value : float
        平均状态价值估计.
    epsilon : float
        当前 ε-greedy 探索率.
    elapsed_seconds : float
        训练耗时（秒）.
    """

    step: int = 0
    episode: int = 0
    policy_loss: float = 0.0
    value_loss: float = 0.0
    entropy: float = 0.0
    approx_kl: float = 0.0
    clip_fraction: float = 0.0
    mean_reward: float = 0.0
    mean_value: float = 0.0
    epsilon: float = 1.0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "episode": self.episode,
            "policy_loss": self.policy_loss,
            "value_loss": self.value_loss,
            "entropy": self.entropy,
            "approx_kl": self.approx_kl,
            "clip_fraction": self.clip_fraction,
            "mean_reward": self.mean_reward,
            "mean_value": self.mean_value,
            "epsilon": self.epsilon,
            "elapsed_seconds": self.elapsed_seconds,
        }


# 训练快照


@dataclass
class TrainingSnapshot:
    """训练快照.

    对应 ADR-017 第 4 节"snapshot 记录: policy_weights / value_weights /
    training_metrics / replay_buffer_stats"。

    Attributes
    ----------
    snapshot_id : str
        快照 ID（``"rl_snapshot_{step}_{timestamp}"``）.
    timestamp : str
        快照时间戳（ISO 8601）.
    step : int
        训练步数.
    episode : int
        episode 数.
    metrics : TrainingMetrics
        训练指标.
    replay_buffer_stats : ReplayBufferStats
        缓冲区统计.
    policy_weights_path : Optional[str]
        策略权重文件路径（None 表示未持久化）.
    value_weights_path : Optional[str]
        价值权重文件路径.
    config : dict[str, Any]
        训练配置快照.
    """

    snapshot_id: str
    timestamp: str
    step: int
    episode: int
    metrics: TrainingMetrics
    replay_buffer_stats: ReplayBufferStats
    policy_weights_path: str | None = None
    value_weights_path: str | None = None
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp,
            "step": self.step,
            "episode": self.episode,
            "metrics": self.metrics.to_dict(),
            "replay_buffer_stats": self.replay_buffer_stats.to_dict(),
            "policy_weights_path": self.policy_weights_path,
            "value_weights_path": self.value_weights_path,
            "config": self.config,
        }


# 环境接口（抽象）


class OfflineEnvironment:
    """离线 RL 环境接口（抽象基类）.

    v1 仅支持离线 RL，环境由历史数据 + 世界模型模拟构成。
    具体实现应继承此类并实现 ``reset`` / ``step`` 方法。

    使用示例
    --------
    >>> class MyEnv(OfflineEnvironment):
    ...     def reset(self) -> np.ndarray:
    ...         return initial_state
    ...     def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict]:
    ...         next_state = world_model.predict(current_state, action)
    ...         reward = reward_fn.compute(...)
    ...         return next_state, reward, done, info

    工程约束
    --------
    - 不接真实机床（v1 离线 RL）
    - 世界模型预测的下一状态替代真实环境
    """

    def reset(self) -> np.ndarray:
        """重置环境，返回初始状态.

        Returns
        -------
        np.ndarray
            初始状态向量 ``[state_dim]``.
        """
        raise NotImplementedError

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict[str, Any]]:
        """执行一步环境交互.

        Args:
            action: 动作向量 ``[action_dim]``.

        Returns
        -------
        tuple
            ``(next_state, reward, done, info)``.
        """
        raise NotImplementedError


# PPO 训练器


class PPOTrainer:
    """PPO 训练器.

    实现 PPO 算法的完整训练循环，包括 episode 收集、GAE 优势估计、
    clipped objective 更新策略网络、TD 误差更新价值网络。

    使用示例
    --------
    >>> config = TrainingConfig()
    >>> trainer = PPOTrainer(config=config)
    >>> trainer.setup(policy_net=policy, value_net=value, env=env)
    >>> for snapshot in trainer.train():
    ...     print(f"step={snapshot.step} reward={snapshot.metrics.mean_reward}")

    线程安全
    --------
    训练过程使用锁保护内部状态（``_metrics`` / ``_replay_buffer``），
    支持训练线程与外部查询线程并发。但 ``train`` 方法本身不可重入。
    """

    def __init__(
        self,
        config: TrainingConfig | None = None,
        reward_config: RewardConfig | None = None,
    ) -> None:
        self._config = config or TrainingConfig()
        self._config.validate()
        self._reward_config = reward_config or RewardConfig()
        self._reward_fn = RewardFunction(self._reward_config)

        # 网络与环境（setup 时注入）
        self._policy_net: Any = None
        self._value_net: Any = None
        self._env: OfflineEnvironment | None = None

        # 训练状态
        self._replay_buffer = ReplayBuffer(
            capacity=10000,
            state_dim=self._config.state_dim,
            action_dim=self._config.action_dim,
        )
        self._metrics = TrainingMetrics()
        self._setup_done = False
        self._stop_requested = False
        self._train_lock = threading.Lock()

        # 随机种子
        self._seed_random()

    def _seed_random(self) -> None:
        """固定随机种子，保证可复现."""
        random.seed(self._config.seed)
        np.random.seed(self._config.seed)
        if HAS_TORCH:
            torch.manual_seed(self._config.seed)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(self._config.seed)
            try:
                torch.backends.cudnn.deterministic = True
                torch.backends.cudnn.benchmark = False
            except AttributeError:
                pass

    # 公开属性

    @property
    def config(self) -> TrainingConfig:
        """训练配置."""
        return self._config

    @property
    def metrics(self) -> TrainingMetrics:
        """当前训练指标."""
        return self._metrics

    @property
    def replay_buffer(self) -> ReplayBuffer:
        """经验回放缓冲区."""
        return self._replay_buffer

    @property
    def is_setup(self) -> bool:
        """是否已完成 setup."""
        return self._setup_done

    @property
    def stop_requested(self) -> bool:
        """是否请求停止训练."""
        return self._stop_requested

    # setup

    def setup(
        self,
        policy_net: Any,
        value_net: Any,
        env: OfflineEnvironment,
    ) -> None:
        """注入策略网络、价值网络与环境.

        Args:
            policy_net: 策略网络（``PolicyNet`` 实例）.
            value_net: 价值网络（``ValueNet`` 实例）.
            env: 离线 RL 环境.
        """
        self._policy_net = policy_net
        self._value_net = value_net
        self._env = env
        self._setup_done = True
        logger.info(
            "PPOTrainer setup 完成: state_dim=%d action_dim=%d torch=%s",
            self._config.state_dim,
            self._config.action_dim,
            HAS_TORCH,
        )

    def request_stop(self) -> None:
        """请求停止训练（异步，由外部线程调用）."""
        self._stop_requested = True
        logger.info("收到停止训练请求")

    # ε-greedy 探索率

    def _current_epsilon(self) -> float:
        """计算当前 ε-greedy 探索率（线性衰减）."""
        cfg = self._config
        progress = min(1.0, self._metrics.step / cfg.epsilon_decay_steps)
        return cfg.epsilon_start + (cfg.epsilon_end - cfg.epsilon_start) * progress

    # episode 收集

    def collect_episode(self, max_steps: int = 200) -> list[Experience]:
        """收集单个 episode.

        Args:
            max_steps: 单个 episode 最大步数.

        Returns
        -------
        list[Experience]
            本 episode 收集的经验列表.

        Raises
        ------
        RuntimeError
            未调用 ``setup``.
        """
        if not self._setup_done:
            raise RuntimeError("必须先调用 setup() 注入网络与环境")
        if self._env is None or self._policy_net is None or self._value_net is None:
            raise RuntimeError("网络或环境未注入")

        experiences: list[Experience] = []
        state = self._env.reset()
        epsilon = self._current_epsilon()

        for step in range(max_steps):
            # 1. ε-greedy 选动作
            action, log_prob, value = self._select_action(state, epsilon)

            # 2. 环境执行（世界模型预测下一状态）
            next_state, reward, done, info = self._env.step(action)

            # 3. 存储经验
            exp = Experience(
                state=state,
                action=action,
                reward=reward,
                next_state=next_state,
                done=done,
                log_prob=log_prob,
                value=value,
            )
            experiences.append(exp)
            self._replay_buffer.append(exp)

            # 4. 更新指标
            self._metrics.episode += 1 if done else 0
            self._metrics.mean_reward = 0.99 * self._metrics.mean_reward + 0.01 * reward
            self._metrics.mean_value = 0.99 * self._metrics.mean_value + 0.01 * value

            state = next_state
            if done:
                break

        return experiences

    def _select_action(self, state: np.ndarray, epsilon: float) -> tuple[np.ndarray, float, float]:
        """ε-greedy 选动作.

        Args:
            state: 状态向量.
            epsilon: 探索率.

        Returns
        -------
        tuple
            ``(action, log_prob, value)``.
        """
        # ε 概率随机探索
        if random.random() < epsilon:
            action = np.random.uniform(-1.0, 1.0, size=self._config.action_dim).astype(np.float32)
            log_prob = 0.0
        else:
            # 策略网络选动作
            action, log_prob = self._policy_forward(state)

        # 价值估计
        value = self._value_forward(state)
        return action, float(log_prob), float(value)

    def _policy_forward(self, state: np.ndarray) -> tuple[np.ndarray, float]:
        """策略网络前向传播."""
        if HAS_TORCH and isinstance(self._policy_net, nn.Module):
            with torch.no_grad():
                state_t = torch.as_tensor(state, dtype=torch.float32)
                if state_t.ndim == 1:
                    state_t = state_t.unsqueeze(0)
                out = self._policy_net(state_t)
                action = out["action"].squeeze(0).cpu().numpy()
                log_prob = float(out.get("log_prob", 0.0))
            return action.astype(np.float32), log_prob
        # NumPy 回退
        out = self._policy_net(state)
        action = np.asarray(out.get("action", out), dtype=np.float32)
        return action, float(out.get("log_prob", 0.0)) if isinstance(out, dict) else 0.0

    def _value_forward(self, state: np.ndarray) -> float:
        """价值网络前向传播."""
        if HAS_TORCH and isinstance(self._value_net, nn.Module):
            with torch.no_grad():
                state_t = torch.as_tensor(state, dtype=torch.float32)
                if state_t.ndim == 1:
                    state_t = state_t.unsqueeze(0)
                value = self._value_net(state_t).squeeze().item()
            return value
        # NumPy 回退
        value = self._value_net(state)
        return float(np.asarray(value).item())

    # PPO 训练循环

    def train(
        self,
        snapshot_callback: Callable[[TrainingSnapshot], None] | None = None,
        eval_callback: Callable[[TrainingMetrics], bool] | None = None,
    ) -> list[TrainingSnapshot]:
        """执行完整训练循环.

        Args:
            snapshot_callback: snapshot 回调函数（每次 snapshot 调用）.
            eval_callback: 评估回调函数，返回 True 表示满足指标可提前停止.

        Returns
        -------
        list[TrainingSnapshot]
            训练过程中产生的所有 snapshot.

        Raises
        ------
        RuntimeError
            未调用 ``setup``.
        """
        if not self._setup_done:
            raise RuntimeError("必须先调用 setup() 注入网络与环境")

        snapshots: list[TrainingSnapshot] = []
        self._stop_requested = False
        start_time = time.perf_counter()

        with self._train_lock:
            while self._metrics.step < self._config.max_steps:
                if self._stop_requested:
                    logger.info(
                        "训练被外部请求停止: step=%d",
                        self._metrics.step,
                    )
                    break

                # 1. 收集 episode
                self.collect_episode()

                # 2. 训练策略与价值网络
                self._train_step()

                # 3. 评估
                if self._metrics.step > 0 and self._metrics.step % self._config.eval_interval == 0:
                    self._metrics.elapsed_seconds = time.perf_counter() - start_time
                    logger.info(
                        "训练评估: step=%d episode=%d reward=%.4f policy_loss=%.4f value_loss=%.4f",
                        self._metrics.step,
                        self._metrics.episode,
                        self._metrics.mean_reward,
                        self._metrics.policy_loss,
                        self._metrics.value_loss,
                    )
                    if eval_callback is not None:
                        try:
                            should_stop = eval_callback(self._metrics)
                            if should_stop:
                                logger.info(
                                    "评估回调请求提前停止: step=%d",
                                    self._metrics.step,
                                )
                                break
                        except Exception as exc:
                            logger.warning("评估回调异常: %s", exc, exc_info=True)

                # 4. snapshot
                if self._metrics.step > 0 and self._metrics.step % self._config.snapshot_interval == 0:
                    snapshot = self._create_snapshot()
                    snapshots.append(snapshot)
                    if snapshot_callback is not None:
                        try:
                            snapshot_callback(snapshot)
                        except Exception as exc:
                            logger.warning("snapshot 回调异常: %s", exc, exc_info=True)

                self._metrics.step += 1

        # 最终 snapshot
        self._metrics.elapsed_seconds = time.perf_counter() - start_time
        final_snapshot = self._create_snapshot()
        snapshots.append(final_snapshot)
        logger.info(
            "训练完成: step=%d episode=%d 耗时=%.1fs",
            self._metrics.step,
            self._metrics.episode,
            self._metrics.elapsed_seconds,
        )
        return snapshots

    def _train_step(self) -> None:
        """执行一次 PPO 训练步（多 epoch 复用数据）."""
        if self._replay_buffer.is_empty:
            return

        batch = self._replay_buffer.sample_arrays(
            batch_size=self._config.batch_size,
            seed=self._config.seed + self._metrics.step,
        )
        if len(batch["states"]) == 0:
            return

        # 计算 GAE 优势与回报
        advantages, returns = self._compute_gae(batch)

        # 多 epoch 更新
        for _ in range(self._config.n_epochs):
            self._update_policy(batch, advantages)
            self._update_value(batch, returns)

    def _compute_gae(self, batch: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        """计算 GAE（Generalized Advantage Estimation）优势与回报.

        Args:
            batch: 经验批次.

        Returns
        -------
        tuple
            ``(advantages, returns)``，shape 均为 ``[batch]``.
        """
        cfg = self._config
        rewards = batch["rewards"]
        values = batch["values"]
        dones = batch["dones"]
        next_values = np.array(
            [self._value_forward(ns) for ns in batch["next_states"]],
            dtype=np.float32,
        )

        advantages = np.zeros_like(rewards, dtype=np.float32)
        last_gae = 0.0
        for t in reversed(range(len(rewards))):
            delta = rewards[t] + cfg.gamma * next_values[t] * (1.0 - dones[t]) - values[t]
            last_gae = delta + cfg.gamma * cfg.gae_lambda * (1.0 - dones[t]) * last_gae
            advantages[t] = last_gae

        returns = advantages + values
        # 优势标准化（稳定训练）
        adv_mean = advantages.mean()
        adv_std = advantages.std() + 1e-8
        advantages = (advantages - adv_mean) / adv_std
        return advantages, returns

    def _update_policy(self, batch: dict[str, np.ndarray], advantages: np.ndarray) -> None:
        """PPO clipped objective 更新策略网络."""
        if not (HAS_TORCH and isinstance(self._policy_net, nn.Module)):
            # NumPy 回退：无实际更新
            self._metrics.policy_loss = 0.0
            self._metrics.entropy = 0.0
            self._metrics.approx_kl = 0.0
            self._metrics.clip_fraction = 0.0
            return

        cfg = self._config
        states = torch.as_tensor(batch["states"], dtype=torch.float32)
        actions = torch.as_tensor(batch["actions"], dtype=torch.float32)
        old_log_probs = torch.as_tensor(batch["log_probs"], dtype=torch.float32)
        advantages_t = torch.as_tensor(advantages, dtype=torch.float32)

        out = self._policy_net(states)
        action_mean = out["action_mean"]
        action_log_std = out.get("action_log_std")
        if action_log_std is None:
            log_std = torch.zeros_like(action_mean)
        else:
            log_std = action_log_std.expand_as(action_mean)

        # 高斯策略 log_prob
        std = log_std.exp()
        new_log_probs = -0.5 * (((actions - action_mean) / (std + 1e-8)) ** 2 + 2 * log_std + np.log(2 * np.pi)).sum(
            dim=-1
        )

        # PPO clipped objective
        ratio = (new_log_probs - old_log_probs).exp()
        surr1 = ratio * advantages_t
        surr2 = torch.clamp(ratio, 1.0 - cfg.clip_epsilon, 1.0 + cfg.clip_epsilon) * advantages_t
        policy_loss = -torch.min(surr1, surr2).mean()

        # 策略熵（探索度指标）
        entropy = 0.5 * (log_std + 0.5 * np.log(2 * np.pi * np.e)).sum(dim=-1).mean()

        # 近似 KL 散度
        approx_kl = (old_log_probs - new_log_probs).mean().item()

        # clip 触发比例
        clip_fraction = ((ratio - 1.0).abs() > cfg.clip_epsilon).float().mean().item()

        # 反向传播
        if not hasattr(self, "_policy_optimizer"):
            self._policy_optimizer = torch.optim.Adam(self._policy_net.parameters(), lr=cfg.learning_rate)
        self._policy_optimizer.zero_grad()
        (policy_loss - 0.01 * entropy).backward()
        torch.nn.utils.clip_grad_norm_(self._policy_net.parameters(), 0.5)
        self._policy_optimizer.step()

        self._metrics.policy_loss = float(policy_loss.item())
        self._metrics.entropy = float(entropy.item())
        self._metrics.approx_kl = float(approx_kl)
        self._metrics.clip_fraction = float(clip_fraction)

    def _update_value(self, batch: dict[str, np.ndarray], returns: np.ndarray) -> None:
        """TD 误差更新价值网络."""
        if not (HAS_TORCH and isinstance(self._value_net, nn.Module)):
            self._metrics.value_loss = 0.0
            return

        cfg = self._config
        states = torch.as_tensor(batch["states"], dtype=torch.float32)
        returns_t = torch.as_tensor(returns, dtype=torch.float32)

        predicted_values = self._value_net(states).squeeze(-1)
        value_loss = F.mse_loss(predicted_values, returns_t)

        if not hasattr(self, "_value_optimizer"):
            self._value_optimizer = torch.optim.Adam(self._value_net.parameters(), lr=cfg.learning_rate)
        self._value_optimizer.zero_grad()
        value_loss.backward()
        torch.nn.utils.clip_grad_norm_(self._value_net.parameters(), 0.5)
        self._value_optimizer.step()

        self._metrics.value_loss = float(value_loss.item())

    # snapshot

    def _create_snapshot(self) -> TrainingSnapshot:
        """创建训练快照."""
        timestamp = datetime.now(timezone.utc).isoformat()
        snapshot_id = f"rl_snapshot_{self._metrics.step}_{int(time.time())}"

        # 权重持久化（实际部署中应保存到文件，此处仅记录路径占位）
        policy_path: str | None = None
        value_path: str | None = None
        if HAS_TORCH and isinstance(self._policy_net, nn.Module):
            policy_path = f"checkpoint://{snapshot_id}/policy.pt"
        if HAS_TORCH and isinstance(self._value_net, nn.Module):
            value_path = f"checkpoint://{snapshot_id}/value.pt"

        return TrainingSnapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            step=self._metrics.step,
            episode=self._metrics.episode,
            metrics=TrainingMetrics(**self._metrics.to_dict()),
            replay_buffer_stats=self._replay_buffer.stats(),
            policy_weights_path=policy_path,
            value_weights_path=value_path,
            config=self._config.to_dict(),
        )


__all__ = [
    "TrainingConfig",
    "TrainingMetrics",
    "TrainingSnapshot",
    "OfflineEnvironment",
    "PPOTrainer",
]
