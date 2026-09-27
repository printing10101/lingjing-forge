"""RL Agent 契约：定义 ``rl_act`` 任务与训练管线的数据结构.

对应 ADR-017 第 2 / 4 节。本文件只定义数据结构与接口契约，实现见：

- ``app/plugins/rl_agent/plugin.py``：``RLAgentPlugin`` 任务处理器
- ``app/plugins/rl_agent/policy.py``：``PolicyNet`` PPO Actor 网络
- ``app/plugins/rl_agent/value.py``：``ValueNet`` PPO Critic 网络
- ``app/plugins/rl_agent/safety_shield.py``：``SafetyShield`` 硬约束过滤层
- ``app/plugins/rl_agent/training/trainer.py``：``PPOTrainer`` PPO 训练器
- ``app/api/v1/rl_agent.py``：路由层（REST API 端点）

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点
--------
1. **离线 RL 优先**：v1 仅支持基于历史数据 + 仿真环境的离线 RL，
   在线 RL 列入 v2 且必须有人工监督
2. **SafetyShield 硬约束**：强制过滤违反安全约束的动作，不可被 RL 策略覆盖
3. **任务类型预留**：``rl_act`` 已在 ``core-contracts-design.md`` 预留
4. **动作向量约定**：4 维 delta（主轴转速/进给/切深/切宽），取值 [-1, 1]
5. **PPO 算法**：默认策略算法，clipped objective + GAE 优势估计
6. **训练管线**：``collect_episodes → build_replay_buffer → train_policy →
   evaluate → snapshot``，基于阶段 1 ``Workflow`` + 阶段 2 ``Snapshot``
7. **可复现性**：训练随机种子固定，snapshot 持久化策略版本
8. **权限模型**：``rl_agent:read``（查询/列表）、``rl_agent:write``（决策/训练）
9. **异常层级**：``RLAgentError`` 基类 → ``PolicyError`` / ``TrainingError`` /
   ``SafetyViolationError`` / ``PolicyNotFoundError`` 子类
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# 任务类型常量

RL_ACT_TASK_TYPE = "rl_act"
"""RL agent 决策任务类型常量.

在 ``PluginManifest`` 中声明，由 ``RLAgentPlugin`` 实现并注册到
``ITaskRegistry``。工作流编排器通过此任务类型调度 RL agent 插件。
"""

# 优化目标与安全约束常量


class OptimizationTarget:
    """优化目标常量.

    Attributes
    ----------
    MINIMIZE_CHATTER : str
        优先最小化颤振概率.
    MAXIMIZE_MATERIAL_REMOVAL : str
        优先最大化材料去除率.
    BALANCE : str
        平衡颤振抑制 / 刀具寿命 / 加工效率.
    """

    MINIMIZE_CHATTER = "minimize_chatter"
    MAXIMIZE_MATERIAL_REMOVAL = "maximize_material_removal"
    BALANCE = "balance"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有优化目标."""
        return [
            cls.MINIMIZE_CHATTER,
            cls.MAXIMIZE_MATERIAL_REMOVAL,
            cls.BALANCE,
        ]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断优化目标是否合法."""
        return value in cls.all()

    @classmethod
    def default(cls) -> str:
        """返回默认优化目标."""
        return cls.BALANCE


class PolicyAlgorithm:
    """RL 策略算法常量.

    Attributes
    ----------
    PPO : str
        Proximal Policy Optimization（v1 默认）.
    DQN : str
        Deep Q-Network（离散动作空间，v2 计划）.
    SAC : str
        Soft Actor-Critic（v2 计划）.
    """

    PPO = "ppo"
    DQN = "dqn"
    SAC = "sac"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有策略算法."""
        return [cls.PPO, cls.DQN, cls.SAC]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断策略算法是否合法."""
        return value in cls.all()

    @classmethod
    def default(cls) -> str:
        """返回默认策略算法."""
        return cls.PPO


class TrainingStatus:
    """训练状态常量.

    Attributes
    ----------
    IDLE : str
        空闲（未启动训练）.
    RUNNING : str
        训练中.
    PAUSED : str
        已暂停.
    COMPLETED : str
        训练完成.
    FAILED : str
        训练失败.
    STOPPING : str
        收到停止请求，正在保存 checkpoint.
    """

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPING = "stopping"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有训练状态."""
        return [
            cls.IDLE,
            cls.RUNNING,
            cls.PAUSED,
            cls.COMPLETED,
            cls.FAILED,
            cls.STOPPING,
        ]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断训练状态是否合法."""
        return value in cls.all()

    @classmethod
    def is_terminal(cls, value: str) -> bool:
        """判断是否为终态（不可继续训练）."""
        return value in (cls.COMPLETED, cls.FAILED)


# 决策请求/响应


@dataclass
class SafetyConstraintsSpec:
    """安全约束规格（与 ``SafetyConstraints`` 对齐）.

    Attributes
    ----------
    max_chatter_probability : float
        最大允许颤振概率.
    max_tool_wear_increment : float
        最大允许刀具磨损增量 (mm/步).
    min_surface_quality : float
        最小表面质量（``1 - surface_roughness / threshold``）.
    """

    max_chatter_probability: float = 0.3
    max_tool_wear_increment: float = 0.01
    min_surface_quality: float = 0.8

    def __post_init__(self) -> None:
        if not 0.0 <= self.max_chatter_probability <= 1.0:
            raise ValueError(f"max_chatter_probability 必须在 [0, 1]: {self.max_chatter_probability}")
        if self.max_tool_wear_increment <= 0:
            raise ValueError(f"max_tool_wear_increment 必须为正数: {self.max_tool_wear_increment}")
        if not 0.0 <= self.min_surface_quality <= 1.0:
            raise ValueError(f"min_surface_quality 必须在 [0, 1]: {self.min_surface_quality}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_chatter_probability": self.max_chatter_probability,
            "max_tool_wear_increment": self.max_tool_wear_increment,
            "min_surface_quality": self.min_surface_quality,
        }


@dataclass
class RLActRequest:
    """RL agent 决策请求.

    Attributes
    ----------
    current_state : dict[str, float]
        当前加工状态.
    candidate_actions : list[dict[str, float]]
        候选动作集（离散动作空间）.
    optimization_target : str
        优化目标（``OptimizationTarget`` 常量）.
    safety_constraints : SafetyConstraintsSpec
        安全约束规格.
    model_uri : str
        RL 策略模型 URI.
    """

    current_state: dict[str, float]
    candidate_actions: list[dict[str, float]]
    optimization_target: str = OptimizationTarget.BALANCE
    safety_constraints: SafetyConstraintsSpec = field(default_factory=SafetyConstraintsSpec)
    model_uri: str = "model://rl_agent/1.0.0"

    def __post_init__(self) -> None:
        if not self.current_state:
            raise ValueError("current_state 不能为空")
        if not self.candidate_actions:
            raise ValueError("candidate_actions 不能为空")
        if not OptimizationTarget.is_valid(self.optimization_target):
            raise ValueError(f"optimization_target 不合法: {self.optimization_target}")
        if not self.model_uri:
            raise ValueError("model_uri 不能为空")


@dataclass
class ActionEvaluation:
    """单候选动作评估结果.

    Attributes
    ----------
    action : dict[str, float]
        候选动作.
    expected_return : float
        RL 价值函数期望回报.
    predicted_chatter_prob : float
        预测颤振概率.
    predicted_tool_wear : float
        预测刀具磨损增量.
    safety_violation : bool
        是否违反安全约束.
    q_value : float
        Q 值（与 ``expected_return`` 一致，PPO 离线 RL 中 Q ≈ V(s)）.
    """

    action: dict[str, float]
    expected_return: float
    predicted_chatter_prob: float
    predicted_tool_wear: float
    safety_violation: bool
    q_value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.predicted_chatter_prob <= 1.0:
            raise ValueError(f"predicted_chatter_prob 必须在 [0, 1]: {self.predicted_chatter_prob}")
        if self.predicted_tool_wear < 0:
            raise ValueError(f"predicted_tool_wear 不能为负数: {self.predicted_tool_wear}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "expected_return": self.expected_return,
            "predicted_chatter_prob": self.predicted_chatter_prob,
            "predicted_tool_wear": self.predicted_tool_wear,
            "safety_violation": self.safety_violation,
            "q_value": self.q_value,
        }


@dataclass
class PolicyInfo:
    """策略元信息.

    Attributes
    ----------
    algorithm : str
        策略算法（``PolicyAlgorithm`` 常量）.
    policy_version : str
        策略版本（semver）.
    training_episodes : int
        训练 episode 数.
    exploration_rate : float
        探索率 ε [0, 1].
    weights_loaded : bool
        是否成功加载了真实训练权重。False 表示策略网络为随机初始化，
        推荐动作不具备决策意义（仅接口验证），消费方必须显式提示.
    """

    algorithm: str
    policy_version: str
    training_episodes: int
    exploration_rate: float
    weights_loaded: bool = False

    def __post_init__(self) -> None:
        if not PolicyAlgorithm.is_valid(self.algorithm):
            raise ValueError(f"algorithm 不合法: {self.algorithm}")
        if not self.policy_version:
            raise ValueError("policy_version 不能为空")
        if self.training_episodes < 0:
            raise ValueError(f"training_episodes 不能为负数: {self.training_episodes}")
        if not 0.0 <= self.exploration_rate <= 1.0:
            raise ValueError(f"exploration_rate 必须在 [0, 1]: {self.exploration_rate}")
        self.weights_loaded = bool(self.weights_loaded)

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "policy_version": self.policy_version,
            "training_episodes": self.training_episodes,
            "exploration_rate": self.exploration_rate,
            "weights_loaded": self.weights_loaded,
        }


@dataclass
class RecommendedAction:
    """推荐动作.

    Attributes
    ----------
    action : dict[str, float]
        推荐的切削参数调整量.
    reasoning : str
        推荐理由（自然语言，供工程师审查）.
    """

    action: dict[str, float]
    reasoning: str

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("action 不能为空")
        if not self.reasoning:
            raise ValueError("reasoning 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "reasoning": self.reasoning}


@dataclass
class RLActResponse:
    """RL agent 决策响应.

    Attributes
    ----------
    recommended_action : RecommendedAction
        推荐动作.
    action_evaluation : list[ActionEvaluation]
        所有候选动作的评估结果.
    policy_info : PolicyInfo
        策略元信息.
    """

    recommended_action: RecommendedAction
    action_evaluation: list[ActionEvaluation]
    policy_info: PolicyInfo

    def __post_init__(self) -> None:
        if not self.action_evaluation:
            raise ValueError("action_evaluation 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommended_action": self.recommended_action.to_dict(),
            "action_evaluation": [e.to_dict() for e in self.action_evaluation],
            "policy_info": self.policy_info.to_dict(),
        }


# 策略版本


@dataclass
class PolicyVersion:
    """RL 策略版本记录.

    Attributes
    ----------
    version : str
        版本号（semver）.
    model_uri : str
        策略模型 URI（``model://rl_agent/<version>``）.
    algorithm : str
        策略算法（``PolicyAlgorithm`` 常量）.
    description : str
        版本描述.
    created_at : datetime
        创建时间.
    training_episodes : int
        训练 episode 数.
    training_steps : int
        训练步数.
    mean_reward : float
        训练时平均 episode 奖励.
    is_active : bool
        是否为当前激活版本.
    """

    version: str
    model_uri: str
    algorithm: str
    description: str
    created_at: datetime
    training_episodes: int
    training_steps: int
    mean_reward: float
    is_active: bool = False

    def __post_init__(self) -> None:
        if not PolicyAlgorithm.is_valid(self.algorithm):
            raise ValueError(f"algorithm 不合法: {self.algorithm}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_uri": self.model_uri,
            "algorithm": self.algorithm,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "training_episodes": self.training_episodes,
            "training_steps": self.training_steps,
            "mean_reward": self.mean_reward,
            "is_active": self.is_active,
        }


# 训练状态与控制


@dataclass
class TrainingMetricsSnapshot:
    """训练指标快照（对应 ``TrainingMetrics`` 的可序列化版本）.

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
        策略熵.
    approx_kl : float
        近似 KL 散度.
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

    step: int
    episode: int
    policy_loss: float
    value_loss: float
    entropy: float
    approx_kl: float
    clip_fraction: float
    mean_reward: float
    mean_value: float
    epsilon: float
    elapsed_seconds: float

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


@dataclass
class TrainingStatusInfo:
    """训练状态信息.

    Attributes
    ----------
    status : str
        训练状态（``TrainingStatus`` 常量）.
    current_step : int
        当前训练步数.
    max_steps : int
        最大训练步数.
    current_episode : int
        当前 episode 数.
    metrics : Optional[TrainingMetricsSnapshot]
        最新训练指标（``status=RUNNING`` 时非空）.
    started_at : Optional[datetime]
        训练开始时间.
    finished_at : Optional[datetime]
        训练结束时间（终态时非空）.
    error_message : Optional[str]
        失败原因（``status=FAILED`` 时非空）.
    """

    status: str
    current_step: int
    max_steps: int
    current_episode: int
    metrics: TrainingMetricsSnapshot | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not TrainingStatus.is_valid(self.status):
            raise ValueError(f"status 不合法: {self.status}")
        if self.current_step < 0:
            raise ValueError(f"current_step 不能为负数: {self.current_step}")
        if self.max_steps <= 0:
            raise ValueError(f"max_steps 必须为正数: {self.max_steps}")
        if self.current_episode < 0:
            raise ValueError(f"current_episode 不能为负数: {self.current_episode}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "current_step": self.current_step,
            "max_steps": self.max_steps,
            "current_episode": self.current_episode,
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error_message": self.error_message,
        }


@dataclass
class TrainingStartRequest:
    """训练启动请求.

    Attributes
    ----------
    max_steps : int
        最大训练步数（覆盖配置默认值）.
    seed : Optional[int]
        随机种子（None 表示使用配置默认值）.
    algorithm : str
        策略算法（``PolicyAlgorithm`` 常量）.
    optimization_target : str
        优化目标（``OptimizationTarget`` 常量）.
    """

    max_steps: int = 100000
    seed: int | None = None
    algorithm: str = PolicyAlgorithm.PPO
    optimization_target: str = OptimizationTarget.BALANCE

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError(f"max_steps 必须为正数: {self.max_steps}")
        if not PolicyAlgorithm.is_valid(self.algorithm):
            raise ValueError(f"algorithm 不合法: {self.algorithm}")
        if not OptimizationTarget.is_valid(self.optimization_target):
            raise ValueError(f"optimization_target 不合法: {self.optimization_target}")
        if self.seed is not None and self.seed < 0:
            raise ValueError(f"seed 不能为负数: {self.seed}")


# 异常层级


class RLAgentError(Exception):
    """RL agent 错误基类."""


# 别名：早期 service/api 模块使用 RLActError 名称导入。
# 保留 RLAgentError 作为规范名的同时，通过别名兼容历史代码，
# 避免大规模重命名 import 语句引入回归风险。
RLActError = RLAgentError


class PolicyError(RLAgentError):
    """策略推理失败（网络前向传播异常 / 权重加载失败）."""


class TrainingError(RLAgentError):
    """训练失败（环境交互异常 / 梯度爆炸 / 收敛失败）."""


class SafetyViolationError(RLAgentError):
    """安全约束违反（所有候选动作均被 SafetyShield 过滤）."""


class PolicyNotFoundError(RLAgentError):
    """策略未找到（``model_uri`` 未注册）."""


class TrainingAlreadyRunningError(RLAgentError):
    """训练已在运行（重复启动训练）."""


__all__ = [
    # 任务类型常量
    "RL_ACT_TASK_TYPE",
    # 优化目标与算法常量
    "OptimizationTarget",
    "PolicyAlgorithm",
    "TrainingStatus",
    # 请求/响应
    "SafetyConstraintsSpec",
    "RLActRequest",
    "ActionEvaluation",
    "PolicyInfo",
    "RecommendedAction",
    "RLActResponse",
    # 策略版本
    "PolicyVersion",
    # 训练状态与控制
    "TrainingMetricsSnapshot",
    "TrainingStatusInfo",
    "TrainingStartRequest",
    # 异常
    "RLAgentError",
    "PolicyError",
    "TrainingError",
    "SafetyViolationError",
    "PolicyNotFoundError",
    "TrainingAlreadyRunningError",
]
