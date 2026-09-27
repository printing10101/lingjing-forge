"""离线 RL 环境（``OfflineEnvironment`` 具体实现）.

对应 ADR-017 第 4 节：v1 仅离线 RL，训练循环的数据源有两类：

1. **ReplayOfflineEnvironment**：回放真实历史轨迹数据集（JSONL）。
   每条记录为一次真实状态转移 ``{"state", "action", "next_state"}``，
   可选 ``"episode"`` 分组键。奖励由 ``RewardFunction`` 从记录的状态
   变化中计算。注意：回放模式下智能体动作不改变环境转移（数据集是
   固定的），训练目标是在既有行为数据上优化策略价值估计——这是离线
   RL 回放环境的标准语义，非合成数据。
2. **WorldModelOfflineEnvironment**：以世界模型作为动力学模型
   （model-based RL）。``predict_fn(state_dict, action_dict) -> 预测状态
   dict`` 由调用方注入（如 ``WorldModelService.predict`` 的同步桥接）。

学术诚信约束
------------
- 两个环境都不生成合成数据：Replay 数据集缺失/为空时直接抛异常；
  WorldModel 环境必须显式注入 ``predict_fn`` 与真实初始状态列表。
- 状态/动作向量布局与 ``StateField.all()`` / ``ActionField.all()`` 对齐。
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.contracts.world_model import ActionField, StateField
from app.plugins.rl_agent.training.reward import RewardFunction
from app.plugins.rl_agent.training.trainer import OfflineEnvironment

logger = logging.getLogger(__name__)

_DEFAULT_MAX_EPISODE_STEPS = 50
_DEFAULT_WEAR_LIMIT_MM = 0.3
_DEFAULT_CHATTER_LIMIT = 0.95


def _build_reward(state: np.ndarray, next_state: np.ndarray, action: np.ndarray, reward_fn: RewardFunction) -> float:
    """从状态转移中提取奖励相关字段并计算标量奖励.

    ``surface_roughness`` 不在 ``StateField`` 中，缺省时
    ``RewardFunction`` 视为 +inf（质量奖励为 0），不做任何补造。
    """
    chatter_idx = StateField.all().index(StateField.CHATTER_PROBABILITY)
    wear_idx = StateField.all().index(StateField.TOOL_WEAR)
    predicted_state = {
        "chatter_probability": float(np.clip(next_state[chatter_idx], 0.0, 1.0)),
        "tool_wear_increment": max(0.0, float(next_state[wear_idx] - state[wear_idx])),
    }
    action_dict = {name: float(action[i]) for i, name in enumerate(ActionField.all()) if i < len(action)}
    return float(reward_fn.compute(predicted_state, action_dict, safety_violation=False).total)


class ReplayOfflineEnvironment(OfflineEnvironment):
    """真实轨迹回放环境（数据驱动，无合成数据）.

    Attributes:
        state_dim: 状态向量维度（与 ``StateField.all()`` 对齐）.
        action_dim: 动作向量维度（与 ``ActionField.all()`` 对齐）.
    """

    def __init__(
        self,
        *,
        state_dim: int,
        action_dim: int,
        trajectories: list[list[tuple[np.ndarray, np.ndarray, np.ndarray]]],
        reward_fn: RewardFunction | None = None,
        max_episode_steps: int = _DEFAULT_MAX_EPISODE_STEPS,
        seed: int = 0,
    ) -> None:
        """注入轨迹数据集.

        Args:
            state_dim: 状态向量维度.
            action_dim: 动作向量维度.
            trajectories: 轨迹列表；每条轨迹为 ``(state, action, next_state)``
                转移元组序列，数组均为 ``float32`` 且维度匹配.
            reward_fn: 奖励函数（默认使用默认 ``RewardConfig``）.
            max_episode_steps: 单 episode 最大步数（截断长轨迹）.
            seed: 采样随机种子.

        Raises:
            ValueError: 轨迹列表为空，或某转移维度不匹配.
        """
        if not trajectories:
            raise ValueError(
                "[数据缺失] 轨迹数据集为空，拒绝构建回放环境。"
                "建议操作：提供真实历史轨迹 JSONL（state/action/next_state），禁止合成数据。"
            )
        self.state_dim = state_dim
        self.action_dim = action_dim
        self._reward_fn = reward_fn or RewardFunction()
        self._max_episode_steps = max(1, int(max_episode_steps))
        self._rng = random.Random(seed)

        self._trajectories: list[list[tuple[np.ndarray, np.ndarray, np.ndarray, float]]] = []
        for ep_idx, episode in enumerate(trajectories):
            cleaned: list[tuple[np.ndarray, np.ndarray, np.ndarray, float]] = []
            for state, action, next_state in episode:
                s = np.asarray(state, dtype=np.float32)
                a = np.asarray(action, dtype=np.float32)
                ns = np.asarray(next_state, dtype=np.float32)
                if s.shape != (state_dim,) or a.shape != (action_dim,) or ns.shape != (state_dim,):
                    raise ValueError(
                        f"轨迹维度不匹配（episode={ep_idx}）: state={s.shape} "
                        f"action={a.shape} next_state={ns.shape}，期望 state=({state_dim},) action=({action_dim},)"
                    )
                reward = _build_reward(s, ns, a, self._reward_fn)
                cleaned.append((s, a, ns, reward))
            if cleaned:
                self._trajectories.append(cleaned)
        if not self._trajectories:
            raise ValueError("[数据缺失] 轨迹数据集中无有效转移，拒绝构建回放环境")

    @classmethod
    def from_jsonl(
        cls,
        path: str | Path,
        *,
        state_dim: int,
        action_dim: int,
        reward_fn: RewardFunction | None = None,
        max_episode_steps: int = _DEFAULT_MAX_EPISODE_STEPS,
        seed: int = 0,
    ) -> "ReplayOfflineEnvironment":
        """从 JSONL 文件加载轨迹并构建环境.

        每行格式::

            {"state": [...], "action": [...], "next_state": [...], "episode": 0}

        ``episode`` 可省略（缺省视为逐行连续 episode 的一部分：相邻行
        ``next_state`` 与 ``state`` 一致时合并为同一条轨迹）。

        Raises:
            FileNotFoundError: 文件不存在.
            ValueError: 文件为空 / 无有效记录 / 字段缺失或维度不匹配.
        """
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"[数据缺失] RL 训练轨迹文件不存在: {file_path}")

        episodes: list[list[tuple[np.ndarray, np.ndarray, np.ndarray]]] = []
        current: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        current_ep: Any = None
        prev_next: np.ndarray | None = None

        def _flush() -> None:
            nonlocal current
            if current:
                episodes.append(current)
                current = []

        with file_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(f"轨迹文件第 {line_no} 行不是合法 JSON: {e}") from e
                try:
                    state = np.asarray(rec["state"], dtype=np.float32)
                    action = np.asarray(rec["action"], dtype=np.float32)
                    next_state = np.asarray(rec["next_state"], dtype=np.float32)
                except KeyError as e:
                    raise ValueError(f"轨迹文件第 {line_no} 行缺少字段: {e}") from e
                except (TypeError, ValueError) as e:
                    raise ValueError(f"轨迹文件第 {line_no} 行字段不是数值数组: {e}") from e

                episode_id = rec.get("episode")
                if episode_id is not None:
                    # 显式分组：episode id 变化即切换轨迹
                    if episode_id != current_ep:
                        _flush()
                        current_ep = episode_id
                elif prev_next is not None and not np.allclose(prev_next, state, atol=1e-6):
                    # 无分组键：上一转移的 next_state 与本行 state 不连续 → 切换轨迹
                    _flush()
                current.append((state, action, next_state))
                prev_next = next_state
        _flush()
        return cls(
            state_dim=state_dim,
            action_dim=action_dim,
            trajectories=episodes,
            reward_fn=reward_fn,
            max_episode_steps=max_episode_steps,
            seed=seed,
        )

    def reset(self) -> np.ndarray:
        """随机选择一条轨迹，返回其初始状态."""
        self._episode = self._rng.choice(self._trajectories)
        self._cursor = 0
        return self._episode[0][0].copy()

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict[str, Any]]:
        """回放下一条真实转移.

        回放语义：转移由数据集固定，智能体动作仅参与奖励计算（记录在
        ``info`` 中供调试），不改变环境状态——这是离线 RL 回放环境的
        标准约定。

        Returns:
            ``(next_state, reward, done, info)``；``done`` 在轨迹耗尽、
            磨损超限（0.3mm）或颤振概率超限（0.95）时为 True。
        """
        if self._cursor >= len(self._episode):
            # 上一步已是终态仍被调用：返回自环并标记 done
            s, _, ns, r = self._episode[-1]
            return ns.copy(), r, True, {"replay": True, "agent_action": np.asarray(action).tolist()}

        state, _recorded_action, next_state, reward = self._episode[self._cursor]
        self._cursor += 1

        wear_idx = StateField.all().index(StateField.TOOL_WEAR)
        chatter_idx = StateField.all().index(StateField.CHATTER_PROBABILITY)
        done = (
            self._cursor >= len(self._episode)
            or float(next_state[wear_idx]) >= _DEFAULT_WEAR_LIMIT_MM
            or float(next_state[chatter_idx]) >= _DEFAULT_CHATTER_LIMIT
        )
        info: dict[str, Any] = {
            "replay": True,
            "agent_action": np.asarray(action, dtype=np.float32).tolist(),
            "progress": self._cursor / len(self._episode),
        }
        return next_state.copy(), reward, done, info


class WorldModelOfflineEnvironment(OfflineEnvironment):
    """世界模型动力学环境（model-based 离线 RL）.

    由调用方注入同步 ``predict_fn``，签名::

        predict_fn(state_dict: dict[str, float], action_dict: dict[str, float]) -> dict

    返回的预测状态 dict 应包含 ``StateField`` 字段（至少含
    ``chatter_probability`` / ``tool_wear``）；额外键（如
    ``safety_violation``）会透传给奖励与终止判定。
    """

    def __init__(
        self,
        *,
        predict_fn: Callable[[dict[str, float], dict[str, float]], dict[str, Any]],
        initial_states: list[dict[str, float]],
        reward_fn: RewardFunction | None = None,
        max_episode_steps: int = _DEFAULT_MAX_EPISODE_STEPS,
        wear_limit_mm: float = _DEFAULT_WEAR_LIMIT_MM,
        chatter_limit: float = _DEFAULT_CHATTER_LIMIT,
        seed: int = 0,
    ) -> None:
        """注入世界模型预测函数与真实初始状态.

        Raises:
            ValueError: ``initial_states`` 为空（拒绝凭空造初始状态）.
        """
        if not initial_states:
            raise ValueError(
                "[数据缺失] initial_states 为空，拒绝构建世界模型环境。"
                "建议操作：从真实加工记录中提取初始状态列表，禁止合成状态。"
            )
        self._predict_fn = predict_fn
        self._initial_states = [dict(s) for s in initial_states]
        self._reward_fn = reward_fn or RewardFunction()
        self._max_episode_steps = max(1, int(max_episode_steps))
        self._wear_limit = float(wear_limit_mm)
        self._chatter_limit = float(chatter_limit)
        self._rng = random.Random(seed)
        self._state_dict: dict[str, float] = {}
        self._steps = 0

    def _to_vector(self, state_dict: dict[str, float]) -> np.ndarray:
        fields = StateField.all()
        return np.asarray([float(state_dict.get(f, 0.0)) for f in fields], dtype=np.float32)

    def reset(self) -> np.ndarray:
        """随机选择一个真实初始状态，重置步数计数."""
        self._state_dict = dict(self._rng.choice(self._initial_states))
        self._steps = 0
        return self._to_vector(self._state_dict)

    def step(self, action: np.ndarray) -> tuple[np.ndarray, float, bool, dict[str, Any]]:
        """调用世界模型预测下一状态并计算奖励."""
        action_arr = np.asarray(action, dtype=np.float32)
        action_dict = {name: float(action_arr[i]) for i, name in enumerate(ActionField.all()) if i < len(action_arr)}
        prediction = self._predict_fn(dict(self._state_dict), action_dict)
        if not isinstance(prediction, dict):
            raise ValueError(f"predict_fn 必须返回 dict，收到: {type(prediction).__name__}")

        next_state_dict = {f: float(prediction.get(f, self._state_dict.get(f, 0.0))) for f in StateField.all()}
        reward = float(
            self._reward_fn.compute(
                {
                    "chatter_probability": next_state_dict.get(StateField.CHATTER_PROBABILITY, 0.0),
                    "tool_wear_increment": max(
                        0.0,
                        next_state_dict.get(StateField.TOOL_WEAR, 0.0)
                        - self._state_dict.get(StateField.TOOL_WEAR, 0.0),
                    ),
                    # 世界模型未提供表面粗糙度时传 +inf（质量奖励为 0），不传 None
                    "surface_roughness": prediction.get("surface_roughness", float("inf")),
                },
                action_dict,
                safety_violation=bool(prediction.get("safety_violation", False)),
            ).total
        )

        self._state_dict = next_state_dict
        self._steps += 1
        done = (
            self._steps >= self._max_episode_steps
            or next_state_dict.get(StateField.TOOL_WEAR, 0.0) >= self._wear_limit
            or next_state_dict.get(StateField.CHATTER_PROBABILITY, 0.0) >= self._chatter_limit
        )
        info: dict[str, Any] = {"prediction": prediction, "progress": self._steps / self._max_episode_steps}
        return self._to_vector(next_state_dict), reward, done, info


__all__ = [
    "ReplayOfflineEnvironment",
    "WorldModelOfflineEnvironment",
]
