"""RL Agent 契约单元测试.

对应 ADR-017 第 2 / 4 节 / app/contracts/rl_agent.py.

覆盖：
- RL_ACT_TASK_TYPE 常量
- OptimizationTarget（3 值 + all / is_valid / default）
- PolicyAlgorithm（3 值 + all / is_valid / default）
- TrainingStatus（6 值 + all / is_valid / is_terminal）
- SafetyConstraintsSpec（3 个数值边界 + to_dict）
- RLActRequest（默认值 + 4 个空值/非法值拒绝）
- ActionEvaluation（chatter_prob / tool_wear 边界 + to_dict）
- PolicyInfo（algorithm / policy_version / training_episodes / exploration_rate 边界 + to_dict）
- RecommendedAction（action / reasoning 非空 + to_dict）
- RLActResponse（空列表拒绝 + 嵌套 to_dict）
- PolicyVersion（仅校验 algorithm + to_dict isoformat + is_active 默认 False）
- TrainingMetricsSnapshot（无 __post_init__，仅 to_dict 字段映射）
- TrainingStatusInfo（status / current_step / max_steps / current_episode 边界 + 嵌套 to_dict）
- TrainingStartRequest（默认值 + max_steps / algorithm / optimization_target / seed 边界）
- 6 个异常类继承关系
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.contracts.rl_agent import (
    ActionEvaluation,
    OptimizationTarget,
    PolicyAlgorithm,
    PolicyError,
    PolicyInfo,
    PolicyNotFoundError,
    PolicyVersion,
    RecommendedAction,
    RL_ACT_TASK_TYPE,
    RLActRequest,
    RLActResponse,
    RLAgentError,
    SafetyConstraintsSpec,
    SafetyViolationError,
    TrainingAlreadyRunningError,
    TrainingError,
    TrainingMetricsSnapshot,
    TrainingStartRequest,
    TrainingStatus,
    TrainingStatusInfo,
)


# 任务类型常量


@pytest.mark.unit
@pytest.mark.contracts
class TestTaskTypeConstant:
    """RL_ACT_TASK_TYPE 模块级常量."""

    def test_value(self):
        assert RL_ACT_TASK_TYPE == "rl_act"

    def test_is_string(self):
        assert isinstance(RL_ACT_TASK_TYPE, str)

    def test_non_empty(self):
        assert RL_ACT_TASK_TYPE


# OptimizationTarget


@pytest.mark.unit
@pytest.mark.contracts
class TestOptimizationTarget:
    """OptimizationTarget 常量类."""

    def test_values(self):
        assert OptimizationTarget.MINIMIZE_CHATTER == "minimize_chatter"
        assert OptimizationTarget.MAXIMIZE_MATERIAL_REMOVAL == "maximize_material_removal"
        assert OptimizationTarget.BALANCE == "balance"

    def test_all_returns_three(self):
        result = OptimizationTarget.all()
        assert len(result) == 3
        assert OptimizationTarget.MINIMIZE_CHATTER in result
        assert OptimizationTarget.MAXIMIZE_MATERIAL_REMOVAL in result
        assert OptimizationTarget.BALANCE in result

    def test_all_no_duplicates(self):
        result = OptimizationTarget.all()
        assert len(set(result)) == 3

    @pytest.mark.parametrize("value", ["minimize_chatter", "maximize_material_removal", "balance"])
    def test_is_valid_true(self, value: str):
        assert OptimizationTarget.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "MINIMIZE_CHATTER", "maximize", "balance_mode", None])
    def test_is_valid_false(self, value: Any):
        assert OptimizationTarget.is_valid(value) is False  # type: ignore[arg-type]

    def test_default_is_balance(self):
        assert OptimizationTarget.default() == OptimizationTarget.BALANCE


# PolicyAlgorithm


@pytest.mark.unit
@pytest.mark.contracts
class TestPolicyAlgorithm:
    """PolicyAlgorithm 常量类."""

    def test_values(self):
        assert PolicyAlgorithm.PPO == "ppo"
        assert PolicyAlgorithm.DQN == "dqn"
        assert PolicyAlgorithm.SAC == "sac"

    def test_all_returns_three(self):
        result = PolicyAlgorithm.all()
        assert len(result) == 3
        assert PolicyAlgorithm.PPO in result
        assert PolicyAlgorithm.DQN in result
        assert PolicyAlgorithm.SAC in result

    def test_all_no_duplicates(self):
        result = PolicyAlgorithm.all()
        assert len(set(result)) == 3

    @pytest.mark.parametrize("value", ["ppo", "dqn", "sac"])
    def test_is_valid_true(self, value: str):
        assert PolicyAlgorithm.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "PPO", "dqn_v2", "random", None])
    def test_is_valid_false(self, value: Any):
        assert PolicyAlgorithm.is_valid(value) is False  # type: ignore[arg-type]

    def test_default_is_ppo(self):
        assert PolicyAlgorithm.default() == PolicyAlgorithm.PPO


# TrainingStatus


@pytest.mark.unit
@pytest.mark.contracts
class TestTrainingStatus:
    """TrainingStatus 常量类."""

    def test_values(self):
        assert TrainingStatus.IDLE == "idle"
        assert TrainingStatus.RUNNING == "running"
        assert TrainingStatus.PAUSED == "paused"
        assert TrainingStatus.COMPLETED == "completed"
        assert TrainingStatus.FAILED == "failed"
        assert TrainingStatus.STOPPING == "stopping"

    def test_all_returns_six(self):
        result = TrainingStatus.all()
        assert len(result) == 6
        for s in (
            TrainingStatus.IDLE,
            TrainingStatus.RUNNING,
            TrainingStatus.PAUSED,
            TrainingStatus.COMPLETED,
            TrainingStatus.FAILED,
            TrainingStatus.STOPPING,
        ):
            assert s in result

    def test_all_no_duplicates(self):
        result = TrainingStatus.all()
        assert len(set(result)) == 6

    @pytest.mark.parametrize(
        "value",
        ["idle", "running", "paused", "completed", "failed", "stopping"],
    )
    def test_is_valid_true(self, value: str):
        assert TrainingStatus.is_valid(value) is True

    @pytest.mark.parametrize("value", ["", "RUNNING", "done", "stopped", "failed_done", None])
    def test_is_valid_false(self, value: Any):
        assert TrainingStatus.is_valid(value) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize("value", ["completed", "failed"])
    def test_is_terminal_true(self, value: str):
        assert TrainingStatus.is_terminal(value) is True

    @pytest.mark.parametrize(
        "value",
        ["idle", "running", "paused", "stopping"],
    )
    def test_is_terminal_false(self, value: str):
        assert TrainingStatus.is_terminal(value) is False

    def test_is_terminal_invalid_value(self):
        """is_terminal 对非法值返回 False（不抛异常）."""
        assert TrainingStatus.is_terminal("unknown") is False
        assert TrainingStatus.is_terminal("") is False


# SafetyConstraintsSpec


@pytest.mark.unit
@pytest.mark.contracts
class TestSafetyConstraintsSpec:
    """SafetyConstraintsSpec dataclass 构造校验."""

    def _make(self, **overrides) -> SafetyConstraintsSpec:
        defaults: dict[str, Any] = dict(
            max_chatter_probability=0.3,
            max_tool_wear_increment=0.01,
            min_surface_quality=0.8,
        )
        defaults.update(overrides)
        return SafetyConstraintsSpec(**defaults)

    def test_valid_with_defaults(self):
        spec = SafetyConstraintsSpec()
        assert spec.max_chatter_probability == 0.3
        assert spec.max_tool_wear_increment == 0.01
        assert spec.min_surface_quality == 0.8

    @pytest.mark.parametrize("value", [0.0, 0.3, 0.5, 1.0])
    def test_valid_max_chatter_probability(self, value: float):
        spec = self._make(max_chatter_probability=value)
        assert spec.max_chatter_probability == value

    @pytest.mark.parametrize("value", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_max_chatter_probability_rejected(self, value: float):
        with pytest.raises(ValueError, match="max_chatter_probability"):
            self._make(max_chatter_probability=value)

    @pytest.mark.parametrize("value", [0.001, 0.01, 1.0, 100.0])
    def test_valid_max_tool_wear_increment(self, value: float):
        spec = self._make(max_tool_wear_increment=value)
        assert spec.max_tool_wear_increment == value

    @pytest.mark.parametrize("value", [0.0, -0.001, -1.0])
    def test_invalid_max_tool_wear_increment_rejected(self, value: float):
        with pytest.raises(ValueError, match="max_tool_wear_increment"):
            self._make(max_tool_wear_increment=value)

    @pytest.mark.parametrize("value", [0.0, 0.5, 0.8, 1.0])
    def test_valid_min_surface_quality(self, value: float):
        spec = self._make(min_surface_quality=value)
        assert spec.min_surface_quality == value

    @pytest.mark.parametrize("value", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_min_surface_quality_rejected(self, value: float):
        with pytest.raises(ValueError, match="min_surface_quality"):
            self._make(min_surface_quality=value)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "max_chatter_probability",
            "max_tool_wear_increment",
            "min_surface_quality",
        }

    def test_to_dict_values(self):
        d = self._make(
            max_chatter_probability=0.25,
            max_tool_wear_increment=0.02,
            min_surface_quality=0.9,
        ).to_dict()
        assert d["max_chatter_probability"] == 0.25
        assert d["max_tool_wear_increment"] == 0.02
        assert d["min_surface_quality"] == 0.9


# RLActRequest


@pytest.mark.unit
@pytest.mark.contracts
class TestRLActRequest:
    """RLActRequest dataclass 构造校验."""

    def _make(self, **overrides) -> RLActRequest:
        defaults: dict[str, Any] = dict(
            current_state={"spindle_speed": 8000.0, "feed_rate": 0.05},
            candidate_actions=[
                {"spindle_speed_delta": 0.1},
                {"spindle_speed_delta": -0.1},
            ],
        )
        defaults.update(overrides)
        return RLActRequest(**defaults)

    def test_valid_request(self):
        req = self._make()
        assert req.current_state == {"spindle_speed": 8000.0, "feed_rate": 0.05}
        assert len(req.candidate_actions) == 2

    def test_default_optimization_target(self):
        req = self._make()
        assert req.optimization_target == OptimizationTarget.BALANCE

    def test_default_safety_constraints(self):
        req = self._make()
        assert isinstance(req.safety_constraints, SafetyConstraintsSpec)
        assert req.safety_constraints.max_chatter_probability == 0.3

    def test_default_model_uri(self):
        req = self._make()
        assert req.model_uri == "model://rl_agent/1.0.0"

    def test_empty_current_state_rejected(self):
        with pytest.raises(ValueError, match="current_state"):
            self._make(current_state={})

    def test_empty_candidate_actions_rejected(self):
        with pytest.raises(ValueError, match="candidate_actions"):
            self._make(candidate_actions=[])

    def test_invalid_optimization_target_rejected(self):
        with pytest.raises(ValueError, match="optimization_target"):
            self._make(optimization_target="invalid_target")

    def test_empty_model_uri_rejected(self):
        with pytest.raises(ValueError, match="model_uri"):
            self._make(model_uri="")

    def test_valid_optimization_target_overrides(self):
        for target in OptimizationTarget.all():
            req = self._make(optimization_target=target)
            assert req.optimization_target == target


# ActionEvaluation


@pytest.mark.unit
@pytest.mark.contracts
class TestActionEvaluation:
    """ActionEvaluation dataclass 构造校验."""

    def _make(self, **overrides) -> ActionEvaluation:
        defaults: dict[str, Any] = dict(
            action={"spindle_speed_delta": 0.1},
            expected_return=0.85,
            predicted_chatter_prob=0.2,
            predicted_tool_wear=0.005,
            safety_violation=False,
            q_value=0.85,
        )
        defaults.update(overrides)
        return ActionEvaluation(**defaults)

    def test_valid_evaluation(self):
        ev = self._make()
        assert ev.action == {"spindle_speed_delta": 0.1}
        assert ev.expected_return == 0.85

    @pytest.mark.parametrize("prob", [0.0, 0.2, 0.5, 1.0])
    def test_valid_chatter_prob(self, prob: float):
        ev = self._make(predicted_chatter_prob=prob)
        assert ev.predicted_chatter_prob == prob

    @pytest.mark.parametrize("prob", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_chatter_prob_rejected(self, prob: float):
        with pytest.raises(ValueError, match="predicted_chatter_prob"):
            self._make(predicted_chatter_prob=prob)

    @pytest.mark.parametrize("wear", [0.0, 0.001, 0.5, 10.0])
    def test_valid_tool_wear(self, wear: float):
        ev = self._make(predicted_tool_wear=wear)
        assert ev.predicted_tool_wear == wear

    @pytest.mark.parametrize("wear", [-0.001, -0.5, -1.0])
    def test_invalid_tool_wear_rejected(self, wear: float):
        with pytest.raises(ValueError, match="predicted_tool_wear"):
            self._make(predicted_tool_wear=wear)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "action",
            "expected_return",
            "predicted_chatter_prob",
            "predicted_tool_wear",
            "safety_violation",
            "q_value",
        }

    def test_to_dict_values(self):
        d = self._make(
            action={"delta": 0.2},
            expected_return=0.9,
            predicted_chatter_prob=0.1,
            predicted_tool_wear=0.002,
            safety_violation=True,
            q_value=0.9,
        ).to_dict()
        assert d["action"] == {"delta": 0.2}
        assert d["expected_return"] == 0.9
        assert d["safety_violation"] is True
        assert d["q_value"] == 0.9


# PolicyInfo


@pytest.mark.unit
@pytest.mark.contracts
class TestPolicyInfo:
    """PolicyInfo dataclass 构造校验."""

    def _make(self, **overrides) -> PolicyInfo:
        defaults: dict[str, Any] = dict(
            algorithm=PolicyAlgorithm.PPO,
            policy_version="1.0.0",
            training_episodes=100,
            exploration_rate=0.1,
        )
        defaults.update(overrides)
        return PolicyInfo(**defaults)

    def test_valid_info(self):
        info = self._make()
        assert info.algorithm == PolicyAlgorithm.PPO
        assert info.policy_version == "1.0.0"
        assert info.training_episodes == 100

    @pytest.mark.parametrize("algo", ["ppo", "dqn", "sac"])
    def test_valid_algorithm(self, algo: str):
        info = self._make(algorithm=algo)
        assert info.algorithm == algo

    @pytest.mark.parametrize("algo", ["", "PPO", "random", "ppo_v2"])
    def test_invalid_algorithm_rejected(self, algo: str):
        with pytest.raises(ValueError, match="algorithm"):
            self._make(algorithm=algo)

    def test_empty_policy_version_rejected(self):
        with pytest.raises(ValueError, match="policy_version"):
            self._make(policy_version="")

    @pytest.mark.parametrize("ep", [0, 1, 100, 10000])
    def test_valid_training_episodes(self, ep: int):
        info = self._make(training_episodes=ep)
        assert info.training_episodes == ep

    @pytest.mark.parametrize("ep", [-1, -100])
    def test_invalid_training_episodes_rejected(self, ep: int):
        with pytest.raises(ValueError, match="training_episodes"):
            self._make(training_episodes=ep)

    @pytest.mark.parametrize("rate", [0.0, 0.1, 0.5, 1.0])
    def test_valid_exploration_rate(self, rate: float):
        info = self._make(exploration_rate=rate)
        assert info.exploration_rate == rate

    @pytest.mark.parametrize("rate", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_exploration_rate_rejected(self, rate: float):
        with pytest.raises(ValueError, match="exploration_rate"):
            self._make(exploration_rate=rate)

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "algorithm",
            "policy_version",
            "training_episodes",
            "exploration_rate",
            "weights_loaded",
        }

    def test_weights_loaded_defaults_to_false(self):
        """空壳修复回归：未加载真实权重时必须显式标记（随机策略不可信）."""
        info = self._make()
        assert info.weights_loaded is False
        assert info.to_dict()["weights_loaded"] is False


@pytest.mark.unit
@pytest.mark.contracts
class TestRecommendedAction:
    """RecommendedAction dataclass 构造校验."""

    def _make(self, **overrides) -> RecommendedAction:
        defaults: dict[str, Any] = dict(
            action={"spindle_speed_delta": 0.1, "feed_rate_delta": -0.05},
            reasoning="降低进给以抑制颤振",
        )
        defaults.update(overrides)
        return RecommendedAction(**defaults)

    def test_valid_action(self):
        act = self._make()
        assert act.action == {"spindle_speed_delta": 0.1, "feed_rate_delta": -0.05}
        assert act.reasoning == "降低进给以抑制颤振"

    def test_empty_action_rejected(self):
        with pytest.raises(ValueError, match="action"):
            self._make(action={})

    def test_empty_reasoning_rejected(self):
        with pytest.raises(ValueError, match="reasoning"):
            self._make(reasoning="")

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {"action", "reasoning"}


# RLActResponse


@pytest.mark.unit
@pytest.mark.contracts
class TestRLActResponse:
    """RLActResponse dataclass 构造校验."""

    def _make_eval(self, **overrides) -> ActionEvaluation:
        defaults: dict[str, Any] = dict(
            action={"spindle_speed_delta": 0.1},
            expected_return=0.85,
            predicted_chatter_prob=0.2,
            predicted_tool_wear=0.005,
            safety_violation=False,
            q_value=0.85,
        )
        defaults.update(overrides)
        return ActionEvaluation(**defaults)

    def _make_recommended(self, **overrides) -> RecommendedAction:
        defaults: dict[str, Any] = dict(
            action={"spindle_speed_delta": 0.1},
            reasoning="推荐动作",
        )
        defaults.update(overrides)
        return RecommendedAction(**defaults)

    def _make_policy(self, **overrides) -> PolicyInfo:
        defaults: dict[str, Any] = dict(
            algorithm=PolicyAlgorithm.PPO,
            policy_version="1.0.0",
            training_episodes=100,
            exploration_rate=0.1,
        )
        defaults.update(overrides)
        return PolicyInfo(**defaults)

    def _make_response(self, **overrides) -> RLActResponse:
        defaults: dict[str, Any] = dict(
            recommended_action=self._make_recommended(),
            action_evaluation=[self._make_eval()],
            policy_info=self._make_policy(),
        )
        defaults.update(overrides)
        return RLActResponse(**defaults)

    def test_valid_response(self):
        resp = self._make_response()
        assert resp.recommended_action.reasoning == "推荐动作"
        assert len(resp.action_evaluation) == 1
        assert resp.policy_info.algorithm == PolicyAlgorithm.PPO

    def test_empty_action_evaluation_rejected(self):
        with pytest.raises(ValueError, match="action_evaluation"):
            self._make_response(action_evaluation=[])

    def test_to_dict_nested_structure(self):
        """to_dict 递归调用 recommended_action / action_evaluation / policy_info."""
        resp = self._make_response(
            action_evaluation=[self._make_eval(), self._make_eval()],
        )
        d = resp.to_dict()
        assert set(d.keys()) == {
            "recommended_action",
            "action_evaluation",
            "policy_info",
        }
        assert isinstance(d["recommended_action"], dict)
        assert d["recommended_action"]["reasoning"] == "推荐动作"
        assert isinstance(d["action_evaluation"], list)
        assert len(d["action_evaluation"]) == 2
        assert isinstance(d["action_evaluation"][0], dict)
        assert d["action_evaluation"][0]["expected_return"] == 0.85
        assert isinstance(d["policy_info"], dict)
        assert d["policy_info"]["algorithm"] == PolicyAlgorithm.PPO


# PolicyVersion


@pytest.mark.unit
@pytest.mark.contracts
class TestPolicyVersion:
    """PolicyVersion dataclass 构造校验.

    注意：PolicyVersion 仅校验 algorithm，不校验 version / model_uri / description 非空，
    不校验 training_episodes / training_steps ≥ 0，不校验 mean_reward 数值范围。
    """

    def _make(self, **overrides) -> PolicyVersion:
        defaults: dict[str, Any] = dict(
            version="1.0.0",
            model_uri="model://rl_agent/1.0.0",
            algorithm=PolicyAlgorithm.PPO,
            description="initial release",
            created_at=datetime(2026, 7, 14, 12, 0, 0),
            training_episodes=100,
            training_steps=100000,
            mean_reward=0.85,
        )
        defaults.update(overrides)
        return PolicyVersion(**defaults)

    def test_valid_version(self):
        pv = self._make()
        assert pv.version == "1.0.0"
        assert pv.algorithm == PolicyAlgorithm.PPO
        assert pv.is_active is False  # 默认 False

    @pytest.mark.parametrize("algo", ["ppo", "dqn", "sac"])
    def test_valid_algorithm(self, algo: str):
        pv = self._make(algorithm=algo)
        assert pv.algorithm == algo

    @pytest.mark.parametrize("algo", ["", "PPO", "random", "ppo_v2"])
    def test_invalid_algorithm_rejected(self, algo: str):
        with pytest.raises(ValueError, match="algorithm"):
            self._make(algorithm=algo)

    def test_is_active_default_false(self):
        pv = self._make()
        assert pv.is_active is False

    def test_is_active_true(self):
        pv = self._make(is_active=True)
        assert pv.is_active is True

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "version",
            "model_uri",
            "algorithm",
            "description",
            "created_at",
            "training_episodes",
            "training_steps",
            "mean_reward",
            "is_active",
        }

    def test_to_dict_isoformat(self):
        """to_dict 将 created_at 转为 isoformat 字符串."""
        ts = datetime(2026, 7, 14, 12, 30, 0)
        d = self._make(created_at=ts).to_dict()
        assert d["created_at"] == ts.isoformat()


# TrainingMetricsSnapshot


@pytest.mark.unit
@pytest.mark.contracts
class TestTrainingMetricsSnapshot:
    """TrainingMetricsSnapshot dataclass 构造校验.

    注意：TrainingMetricsSnapshot 无 __post_init__，所有字段无校验。
    仅验证 to_dict 字段映射正确性。
    """

    def _make(self, **overrides) -> TrainingMetricsSnapshot:
        defaults: dict[str, Any] = dict(
            step=1000,
            episode=10,
            policy_loss=0.05,
            value_loss=0.02,
            entropy=0.5,
            approx_kl=0.01,
            clip_fraction=0.1,
            mean_reward=0.8,
            mean_value=0.6,
            epsilon=0.1,
            elapsed_seconds=3600.0,
        )
        defaults.update(overrides)
        return TrainingMetricsSnapshot(**defaults)

    def test_valid_snapshot(self):
        snap = self._make()
        assert snap.step == 1000
        assert snap.episode == 10
        assert snap.policy_loss == 0.05

    def test_no_validation_negative_step(self):
        """无 __post_init__，负 step 不会抛异常."""
        snap = self._make(step=-1)
        assert snap.step == -1

    def test_no_validation_negative_loss(self):
        """无 __post_init__，负 loss 不会抛异常."""
        snap = self._make(policy_loss=-0.5)
        assert snap.policy_loss == -0.5

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "step",
            "episode",
            "policy_loss",
            "value_loss",
            "entropy",
            "approx_kl",
            "clip_fraction",
            "mean_reward",
            "mean_value",
            "epsilon",
            "elapsed_seconds",
        }

    def test_to_dict_values(self):
        d = self._make(
            step=2000,
            episode=20,
            policy_loss=0.03,
            mean_reward=0.9,
            epsilon=0.05,
        ).to_dict()
        assert d["step"] == 2000
        assert d["episode"] == 20
        assert d["policy_loss"] == 0.03
        assert d["mean_reward"] == 0.9
        assert d["epsilon"] == 0.05


# TrainingStatusInfo


@pytest.mark.unit
@pytest.mark.contracts
class TestTrainingStatusInfo:
    """TrainingStatusInfo dataclass 构造校验."""

    def _make_metrics(self, **overrides) -> TrainingMetricsSnapshot:
        defaults: dict[str, Any] = dict(
            step=1000,
            episode=10,
            policy_loss=0.05,
            value_loss=0.02,
            entropy=0.5,
            approx_kl=0.01,
            clip_fraction=0.1,
            mean_reward=0.8,
            mean_value=0.6,
            epsilon=0.1,
            elapsed_seconds=3600.0,
        )
        defaults.update(overrides)
        return TrainingMetricsSnapshot(**defaults)

    def _make(self, **overrides) -> TrainingStatusInfo:
        defaults: dict[str, Any] = dict(
            status=TrainingStatus.RUNNING,
            current_step=1000,
            max_steps=100000,
            current_episode=10,
        )
        defaults.update(overrides)
        return TrainingStatusInfo(**defaults)

    def test_valid_info(self):
        info = self._make()
        assert info.status == TrainingStatus.RUNNING
        assert info.current_step == 1000
        assert info.max_steps == 100000

    def test_default_optional_fields(self):
        info = self._make()
        assert info.metrics is None
        assert info.started_at is None
        assert info.finished_at is None
        assert info.error_message is None

    @pytest.mark.parametrize("status", TrainingStatus.all())
    def test_valid_status(self, status: str):
        info = self._make(status=status)
        assert info.status == status

    @pytest.mark.parametrize("status", ["", "RUNNING", "done", "unknown"])
    def test_invalid_status_rejected(self, status: str):
        with pytest.raises(ValueError, match="status"):
            self._make(status=status)

    @pytest.mark.parametrize("step", [0, 1, 1000, 100000])
    def test_valid_current_step(self, step: int):
        info = self._make(current_step=step)
        assert info.current_step == step

    @pytest.mark.parametrize("step", [-1, -100])
    def test_invalid_current_step_rejected(self, step: int):
        with pytest.raises(ValueError, match="current_step"):
            self._make(current_step=step)

    @pytest.mark.parametrize("max_steps", [1, 100, 100000])
    def test_valid_max_steps(self, max_steps: int):
        info = self._make(max_steps=max_steps)
        assert info.max_steps == max_steps

    @pytest.mark.parametrize("max_steps", [0, -1, -100])
    def test_invalid_max_steps_rejected(self, max_steps: int):
        with pytest.raises(ValueError, match="max_steps"):
            self._make(max_steps=max_steps)

    @pytest.mark.parametrize("ep", [0, 1, 10, 1000])
    def test_valid_current_episode(self, ep: int):
        info = self._make(current_episode=ep)
        assert info.current_episode == ep

    @pytest.mark.parametrize("ep", [-1, -10])
    def test_invalid_current_episode_rejected(self, ep: int):
        with pytest.raises(ValueError, match="current_episode"):
            self._make(current_episode=ep)

    def test_to_dict_without_metrics(self):
        d = self._make().to_dict()
        assert set(d.keys()) == {
            "status",
            "current_step",
            "max_steps",
            "current_episode",
            "metrics",
            "started_at",
            "finished_at",
            "error_message",
        }
        assert d["metrics"] is None
        assert d["started_at"] is None
        assert d["finished_at"] is None
        assert d["error_message"] is None

    def test_to_dict_with_metrics(self):
        """to_dict 嵌套调用 metrics.to_dict()."""
        metrics = self._make_metrics(step=500, episode=5)
        info = self._make(metrics=metrics)
        d = info.to_dict()
        assert isinstance(d["metrics"], dict)
        assert d["metrics"]["step"] == 500
        assert d["metrics"]["episode"] == 5

    def test_to_dict_with_datetimes(self):
        """to_dict 将 started_at / finished_at 转为 isoformat."""
        started = datetime(2026, 7, 14, 10, 0, 0)
        finished = datetime(2026, 7, 14, 11, 0, 0)
        info = self._make(
            status=TrainingStatus.COMPLETED,
            started_at=started,
            finished_at=finished,
        )
        d = info.to_dict()
        assert d["started_at"] == started.isoformat()
        assert d["finished_at"] == finished.isoformat()

    def test_to_dict_with_error_message(self):
        info = self._make(
            status=TrainingStatus.FAILED,
            error_message="gradient explosion",
        )
        d = info.to_dict()
        assert d["error_message"] == "gradient explosion"


# TrainingStartRequest


@pytest.mark.unit
@pytest.mark.contracts
class TestTrainingStartRequest:
    """TrainingStartRequest dataclass 构造校验."""

    def test_default_values(self):
        req = TrainingStartRequest()
        assert req.max_steps == 100000
        assert req.seed is None
        assert req.algorithm == PolicyAlgorithm.PPO
        assert req.optimization_target == OptimizationTarget.BALANCE

    @pytest.mark.parametrize("max_steps", [1, 100, 50000, 100000, 1000000])
    def test_valid_max_steps(self, max_steps: int):
        req = TrainingStartRequest(max_steps=max_steps)
        assert req.max_steps == max_steps

    @pytest.mark.parametrize("max_steps", [0, -1, -100])
    def test_invalid_max_steps_rejected(self, max_steps: int):
        with pytest.raises(ValueError, match="max_steps"):
            TrainingStartRequest(max_steps=max_steps)

    @pytest.mark.parametrize("algo", ["ppo", "dqn", "sac"])
    def test_valid_algorithm(self, algo: str):
        req = TrainingStartRequest(algorithm=algo)
        assert req.algorithm == algo

    @pytest.mark.parametrize("algo", ["", "PPO", "random"])
    def test_invalid_algorithm_rejected(self, algo: str):
        with pytest.raises(ValueError, match="algorithm"):
            TrainingStartRequest(algorithm=algo)

    @pytest.mark.parametrize("target", OptimizationTarget.all())
    def test_valid_optimization_target(self, target: str):
        req = TrainingStartRequest(optimization_target=target)
        assert req.optimization_target == target

    @pytest.mark.parametrize("target", ["", "BALANCE", "invalid"])
    def test_invalid_optimization_target_rejected(self, target: str):
        with pytest.raises(ValueError, match="optimization_target"):
            TrainingStartRequest(optimization_target=target)

    @pytest.mark.parametrize("seed", [None, 0, 1, 42, 1000])
    def test_valid_seed(self, seed):
        req = TrainingStartRequest(seed=seed)
        assert req.seed == seed

    @pytest.mark.parametrize("seed", [-1, -100])
    def test_invalid_seed_rejected(self, seed: int):
        with pytest.raises(ValueError, match="seed"):
            TrainingStartRequest(seed=seed)


# 异常层级


@pytest.mark.unit
@pytest.mark.contracts
class TestExceptions:
    """异常层级关系."""

    def test_policy_error_is_rl_agent_error(self):
        assert issubclass(PolicyError, RLAgentError)

    def test_training_error_is_rl_agent_error(self):
        assert issubclass(TrainingError, RLAgentError)

    def test_safety_violation_error_is_rl_agent_error(self):
        assert issubclass(SafetyViolationError, RLAgentError)

    def test_policy_not_found_error_is_rl_agent_error(self):
        assert issubclass(PolicyNotFoundError, RLAgentError)

    def test_training_already_running_error_is_rl_agent_error(self):
        assert issubclass(TrainingAlreadyRunningError, RLAgentError)

    def test_rl_agent_error_is_exception(self):
        assert issubclass(RLAgentError, Exception)

    def test_raise_policy_error(self):
        with pytest.raises(PolicyError):
            raise PolicyError("network forward failed")

    def test_raise_training_error(self):
        with pytest.raises(TrainingError):
            raise TrainingError("gradient explosion")

    def test_raise_safety_violation_error(self):
        with pytest.raises(SafetyViolationError):
            raise SafetyViolationError("all actions filtered")

    def test_raise_policy_not_found_error(self):
        with pytest.raises(PolicyNotFoundError):
            raise PolicyNotFoundError("model_uri not registered")

    def test_raise_training_already_running_error(self):
        with pytest.raises(TrainingAlreadyRunningError):
            raise TrainingAlreadyRunningError("training already running")

    def test_catch_subclass_as_base(self):
        """子类异常可被基类 except 捕获."""
        with pytest.raises(RLAgentError):
            raise PolicyError("network forward failed")

    def test_distinct_subclasses(self):
        """5 个子类互不继承."""
        assert not issubclass(PolicyError, TrainingError)
        assert not issubclass(PolicyError, SafetyViolationError)
        assert not issubclass(PolicyError, PolicyNotFoundError)
        assert not issubclass(PolicyError, TrainingAlreadyRunningError)
        assert not issubclass(TrainingError, SafetyViolationError)
        assert not issubclass(TrainingError, PolicyNotFoundError)
        assert not issubclass(TrainingError, TrainingAlreadyRunningError)
        assert not issubclass(SafetyViolationError, PolicyNotFoundError)
        assert not issubclass(SafetyViolationError, TrainingAlreadyRunningError)
        assert not issubclass(PolicyNotFoundError, TrainingAlreadyRunningError)
