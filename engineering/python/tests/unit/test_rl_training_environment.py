"""ReplayOfflineEnvironment / WorldModelOfflineEnvironment 单元测试.

覆盖：
- 空数据集 / 数据文件缺失 → 诚实拒绝（不生成合成数据）
- from_jsonl 的显式 episode 分组与连续性合并分组
- reset/step 返回维度、奖励与终止条件
- WorldModelOfflineEnvironment 的 predict_fn 调用与终止判定
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.contracts.world_model import ActionField, StateField
from app.plugins.rl_agent.training.environment import (
    ReplayOfflineEnvironment,
    WorldModelOfflineEnvironment,
)
from app.plugins.rl_agent.training.reward import RewardFunction

STATE_DIM = len(StateField.all())
ACTION_DIM = len(ActionField.all())


def _state(wear: float = 0.05, chatter: float = 0.1) -> np.ndarray:
    """按 StateField 顺序构造状态向量，可指定磨损与颤振概率。"""
    vec = np.zeros(STATE_DIM, dtype=np.float32)
    vec[StateField.all().index(StateField.TOOL_WEAR)] = wear
    vec[StateField.all().index(StateField.CHATTER_PROBABILITY)] = chatter
    vec[StateField.all().index(StateField.SPINDLE_SPEED)] = 8000.0
    return vec


def _transition(wear: float, next_wear: float, chatter: float = 0.1) -> dict:
    return {
        "state": _state(wear=wear).tolist(),
        "action": np.zeros(ACTION_DIM, dtype=np.float32).tolist(),
        "next_state": _state(wear=next_wear, chatter=chatter).tolist(),
    }


class TestReplayOfflineEnvironment:
    def test_empty_trajectories_rejected(self):
        with pytest.raises(ValueError, match="轨迹数据集为空"):
            ReplayOfflineEnvironment(state_dim=STATE_DIM, action_dim=ACTION_DIM, trajectories=[])

    def test_dimension_mismatch_rejected(self):
        bad = [(_state(), np.zeros(3, dtype=np.float32), _state())]
        with pytest.raises(ValueError, match="维度不匹配"):
            ReplayOfflineEnvironment(state_dim=STATE_DIM, action_dim=ACTION_DIM, trajectories=[bad])

    def test_reset_step_shapes_and_done(self):
        episode = [
            (_state(wear=0.05), np.zeros(ACTION_DIM, dtype=np.float32), _state(wear=0.08)),
            (_state(wear=0.08), np.zeros(ACTION_DIM, dtype=np.float32), _state(wear=0.40)),  # 磨损超限 → done
        ]
        env = ReplayOfflineEnvironment(
            state_dim=STATE_DIM, action_dim=ACTION_DIM, trajectories=[episode], seed=1
        )
        state = env.reset()
        assert state.shape == (STATE_DIM,)
        next_state, reward, done, info = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
        assert next_state.shape == (STATE_DIM,)
        assert isinstance(reward, float)
        assert done is False
        assert info["replay"] is True
        next_state2, reward2, done2, _ = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
        assert done2 is True  # 磨损 0.40 ≥ 0.3 终止
        assert reward2 <= 0.0  # 磨损惩罚为负

    def test_from_jsonl_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ReplayOfflineEnvironment.from_jsonl(tmp_path / "missing.jsonl", state_dim=STATE_DIM, action_dim=ACTION_DIM)

    def test_from_jsonl_explicit_episode_grouping(self, tmp_path: Path):
        lines = [
            {**_transition(0.05, 0.06), "episode": 0},
            {**_transition(0.06, 0.07), "episode": 0},
            {**_transition(0.05, 0.06), "episode": 1},
        ]
        path = tmp_path / "traj.jsonl"
        path.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
        env = ReplayOfflineEnvironment.from_jsonl(path, state_dim=STATE_DIM, action_dim=ACTION_DIM)
        assert len(env._trajectories) == 2  # episode 0（2 步）+ episode 1（1 步）
        assert len(env._trajectories[0]) == 2

    def test_from_jsonl_continuity_grouping(self, tmp_path: Path):
        # 无 episode 键：next_state 与下一行 state 连续 → 同一轨迹
        lines = [
            _transition(0.05, 0.06),
            _transition(0.06, 0.07),
            _transition(0.30, 0.31),  # 不连续 → 新轨迹
        ]
        path = tmp_path / "traj.jsonl"
        path.write_text("\n".join(json.dumps(l) for l in lines), encoding="utf-8")
        env = ReplayOfflineEnvironment.from_jsonl(path, state_dim=STATE_DIM, action_dim=ACTION_DIM)
        assert len(env._trajectories) == 2
        assert len(env._trajectories[0]) == 2

    def test_from_jsonl_missing_field_rejected(self, tmp_path: Path):
        path = tmp_path / "bad.jsonl"
        path.write_text(json.dumps({"state": [0.0] * STATE_DIM, "action": [0.0] * ACTION_DIM}), encoding="utf-8")
        with pytest.raises(ValueError, match="缺少字段"):
            ReplayOfflineEnvironment.from_jsonl(path, state_dim=STATE_DIM, action_dim=ACTION_DIM)

    def test_reward_uses_wear_increment(self, tmp_path: Path):
        path = tmp_path / "traj.jsonl"
        path.write_text(json.dumps(_transition(0.10, 0.20)), encoding="utf-8")
        env = ReplayOfflineEnvironment.from_jsonl(path, state_dim=STATE_DIM, action_dim=ACTION_DIM)
        env.reset()
        _, reward, _, _ = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
        expected = RewardFunction().compute(
            {"chatter_probability": 0.1, "tool_wear_increment": 0.10}, {}, safety_violation=False
        ).total
        assert reward == pytest.approx(expected)


class TestWorldModelOfflineEnvironment:
    def test_empty_initial_states_rejected(self):
        with pytest.raises(ValueError, match="initial_states 为空"):
            WorldModelOfflineEnvironment(predict_fn=lambda s, a: {}, initial_states=[])

    def test_step_uses_predict_fn_and_terminates(self):
        calls: list[tuple[dict, dict]] = []

        def predict_fn(state: dict, action: dict) -> dict:
            calls.append((state, action))
            return {
                **state,
                StateField.TOOL_WEAR: state.get(StateField.TOOL_WEAR, 0.0) + 0.25,
                StateField.CHATTER_PROBABILITY: 0.2,
                "surface_roughness": 0.8,
            }

        initial = [{StateField.TOOL_WEAR: 0.05, StateField.CHATTER_PROBABILITY: 0.1}]
        env = WorldModelOfflineEnvironment(predict_fn=predict_fn, initial_states=initial, seed=3)
        state_vec = env.reset()
        assert state_vec.shape == (STATE_DIM,)
        _, reward, done, info = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
        assert done is True  # 0.05 + 0.25 = 0.30 ≥ wear limit
        assert "prediction" in info
        assert len(calls) == 1
        assert calls[0][1] == {name: 0.0 for name in ActionField.all()}

    def test_max_episode_steps_termination(self):
        def predict_fn(state: dict, action: dict) -> dict:
            return {**state, StateField.CHATTER_PROBABILITY: 0.1}

        initial = [{StateField.TOOL_WEAR: 0.05}]
        env = WorldModelOfflineEnvironment(
            predict_fn=predict_fn, initial_states=initial, max_episode_steps=3, seed=0
        )
        env.reset()
        action = np.zeros(ACTION_DIM, dtype=np.float32)
        _, _, done1, _ = env.step(action)
        _, _, done2, _ = env.step(action)
        _, _, done3, _ = env.step(action)
        assert (done1, done2, done3) == (False, False, True)

    def test_predict_fn_without_roughness_defaults_no_bonus(self):
        """世界模型未返回 surface_roughness 时不得把 None 传给奖励函数。"""

        def predict_fn(state: dict, action: dict) -> dict:
            return {**state, StateField.CHATTER_PROBABILITY: 0.1}

        env = WorldModelOfflineEnvironment(
            predict_fn=predict_fn, initial_states=[{StateField.TOOL_WEAR: 0.05}], seed=0
        )
        env.reset()
        _, reward, _, _ = env.step(np.zeros(ACTION_DIM, dtype=np.float32))
        assert isinstance(reward, float)

    def test_predict_fn_non_dict_rejected(self):
        env = WorldModelOfflineEnvironment(
            predict_fn=lambda s, a: "not-a-dict", initial_states=[{StateField.TOOL_WEAR: 0.05}]
        )
        env.reset()
        with pytest.raises(ValueError, match="predict_fn 必须返回 dict"):
            env.step(np.zeros(ACTION_DIM, dtype=np.float32))
