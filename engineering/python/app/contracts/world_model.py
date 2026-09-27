"""世界模型契约：定义 ``wm_predict_state`` 任务的数据结构与接口契约.

对应 ADR-017 第 1 节。本文件只定义数据结构与接口契约，实现见：

- ``app/plugins/world_model/plugin.py``：``WorldModelPlugin`` 任务处理器
- ``app/plugins/world_model/net.py``：``WorldModelNet`` 网络架构
- ``app/plugins/world_model/predictor.py``：``TrajectoryPredictor`` 自回归预测器
- ``app/api/v1/world_model.py``：路由层（REST API 端点）

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点
--------
1. **离线 RL 优先**：v1 仅离线 RL，世界模型预测的轨迹供 RL agent 离线训练使用
2. **不接 CNC 控制器**：预测结果仅供决策参考，物理执行需"持证操作员 + 导师签字 + 保险"
3. **任务类型预留**：``wm_predict_state`` 已在 ``core-contracts-design.md`` 预留
4. **状态向量约定**：默认 8 维（颤振概率 / 磨损 / 质量 / 主轴转速 / 进给 / 切深 / 切宽 / 温度）
5. **轨迹预测**：自回归多步预测，horizon 默认 10，上限 100（防止漂移累积）
6. **不确定性估计**：``uncertainty_estimate`` 字段记录模型预测不确定性
7. **权限模型**：``world_model:read``（查询/列表）、``world_model:write``（预测/注册版本）
8. **异常层级**：``WorldModelError`` 基类 → ``PredictionError`` / ``ModelNotFound`` /
   ``InvalidStateError`` 子类，与现有服务层异常风格一致
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


# 任务类型常量

WM_PREDICT_STATE_TASK_TYPE = "wm_predict_state"
"""世界模型状态预测任务类型常量.

在 ``PluginManifest`` 中声明，由 ``WorldModelPlugin`` 实现并注册到
``ITaskRegistry``。工作流编排器通过此任务类型调度世界模型插件。
"""

# 默认状态/动作字段名（与世界模型插件实现对齐）

DEFAULT_STATE_DIM = 8
"""默认状态向量维度（颤振概率/磨损/质量/主轴转速/进给/切深/切宽/温度）."""

DEFAULT_ACTION_DIM = 4
"""默认动作向量维度（主轴转速/进给/切深/切宽的 delta）."""

DEFAULT_HORIZON = 10
"""默认预测步长."""

MAX_HORIZON = 100
"""最大预测步长（防止自回归漂移累积）."""

MIN_HORIZON = 1
"""最小预测步长."""


# 状态/动作字段标签


class StateField:
    """状态向量字段标签常量（与 ADR-017 第 1 节对齐）.

    状态字典字段名，用于世界模型输入输出的结构化描述。
    """

    SPINDLE_SPEED = "spindle_speed"  # 主轴转速 (rpm)
    FEED_RATE = "feed_rate"  # 进给速度 (mm/min)
    DEPTH_OF_CUT = "depth_of_cut"  # 切削深度 (mm)
    WIDTH_OF_CUT = "width_of_cut"  # 切削宽度 (mm)
    TOOL_WEAR = "tool_wear"  # 刀具磨损 (mm)
    VIBRATION_RMS = "vibration_rms"  # 振动 RMS (g)
    TEMPERATURE = "temperature"  # 温度 (°C)
    CHATTER_PROBABILITY = "chatter_probability"  # 颤振概率 [0, 1]

    @classmethod
    def all(cls) -> list[str]:
        """返回所有状态字段名."""
        return [
            cls.SPINDLE_SPEED,
            cls.FEED_RATE,
            cls.DEPTH_OF_CUT,
            cls.WIDTH_OF_CUT,
            cls.TOOL_WEAR,
            cls.VIBRATION_RMS,
            cls.TEMPERATURE,
            cls.CHATTER_PROBABILITY,
        ]


class ActionField:
    """动作向量字段标签常量（与 ``RLAgentPlugin`` 动作向量标签对齐）.

    动作为相对调整量（delta），取值范围 [-1, 1]，绝对值由
    ``SafetyConstraints`` 的 range 决定。
    """

    SPINDLE_SPEED_DELTA = "spindle_speed_delta"
    FEED_RATE_DELTA = "feed_rate_delta"
    DEPTH_OF_CUT_DELTA = "depth_of_cut_delta"
    WIDTH_OF_CUT_DELTA = "width_of_cut_delta"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有动作字段名."""
        return [
            cls.SPINDLE_SPEED_DELTA,
            cls.FEED_RATE_DELTA,
            cls.DEPTH_OF_CUT_DELTA,
            cls.WIDTH_OF_CUT_DELTA,
        ]


# 预测请求/响应


@dataclass
class WorldModelPredictRequest:
    """世界模型预测请求.

    Attributes
    ----------
    current_state : dict[str, float]
        当前加工状态（字段名见 ``StateField``）. 融合模式下可为空 dict
        （由 ``unified_state`` 提供状态信息）.
    candidate_action : dict[str, float]
        候选切削参数调整量（字段名见 ``ActionField``）.
    horizon : int
        预测步长（1 ~ 100，默认 10）.
    model_uri : str
        世界模型 URI（如 ``model://world_model/1.0.0``）.
    unified_state : Optional[dict[str, Any]]
        ADR-020 思路 1 融合模式可选输入. 包含几何特征（ADR-007）与
        动力学状态（ADR-013）的统一状态字典. 提供时 service 层路由到
        融合路径（GeometryEncoder/DynamicsEncoder/FusionLayer）.
        为 None 时走原始 state_dim 字段拼接路径（向后兼容）.
    """

    current_state: dict[str, float]
    candidate_action: dict[str, float]
    horizon: int = DEFAULT_HORIZON
    model_uri: str = "model://world_model/1.0.0"
    # ADR-020 思路 1：融合模式可选输入（默认 None 保持向后兼容）
    unified_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # 融合模式下 current_state 可为空（由 unified_state 提供状态信息）
        if not self.current_state and not self.unified_state:
            raise ValueError("current_state 不能为空（除非提供 unified_state 走融合模式）")
        if not self.candidate_action:
            raise ValueError("candidate_action 不能为空")
        if not MIN_HORIZON <= self.horizon <= MAX_HORIZON:
            raise ValueError(f"horizon 必须在 [{MIN_HORIZON}, {MAX_HORIZON}], 当前: {self.horizon}")
        if not self.model_uri:
            raise ValueError("model_uri 不能为空")


@dataclass
class TrajectoryStep:
    """单步预测结果.

    Attributes
    ----------
    step : int
        步骤索引（0-based）.
    predicted_state : dict[str, float]
        预测的状态（字段名见 ``StateField``）.
    chatter_probability : float
        颤振概率 [0, 1].
    tool_wear_increment : float
        刀具磨损增量 (mm).
    surface_roughness : float
        表面粗糙度 Ra (μm).
    confidence : float
        模型置信度 [0, 1].
    """

    step: int
    predicted_state: dict[str, float]
    chatter_probability: float
    tool_wear_increment: float
    surface_roughness: float
    confidence: float

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError(f"step 不能为负数: {self.step}")
        if not 0.0 <= self.chatter_probability <= 1.0:
            raise ValueError(f"chatter_probability 必须在 [0, 1]: {self.chatter_probability}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence 必须在 [0, 1]: {self.confidence}")
        if self.tool_wear_increment < 0:
            raise ValueError(f"tool_wear_increment 不能为负数: {self.tool_wear_increment}")
        if self.surface_roughness < 0:
            raise ValueError(f"surface_roughness 不能为负数: {self.surface_roughness}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "predicted_state": self.predicted_state,
            "chatter_probability": self.chatter_probability,
            "tool_wear_increment": self.tool_wear_increment,
            "surface_roughness": self.surface_roughness,
            "confidence": self.confidence,
        }


@dataclass
class TrajectoryMetrics:
    """轨迹汇总指标.

    Attributes
    ----------
    mean_chatter_probability : float
        平均颤振概率.
    max_chatter_probability : float
        最大颤振概率.
    cumulative_tool_wear : float
        累计刀具磨损 (mm).
    final_surface_roughness : float
        最终表面粗糙度 Ra (μm).
    """

    mean_chatter_probability: float
    max_chatter_probability: float
    cumulative_tool_wear: float
    final_surface_roughness: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.mean_chatter_probability <= 1.0:
            raise ValueError(f"mean_chatter_probability 必须在 [0, 1]: {self.mean_chatter_probability}")
        if not 0.0 <= self.max_chatter_probability <= 1.0:
            raise ValueError(f"max_chatter_probability 必须在 [0, 1]: {self.max_chatter_probability}")
        if self.cumulative_tool_wear < 0:
            raise ValueError(f"cumulative_tool_wear 不能为负数: {self.cumulative_tool_wear}")
        if self.final_surface_roughness < 0:
            raise ValueError(f"final_surface_roughness 不能为负数: {self.final_surface_roughness}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_chatter_probability": self.mean_chatter_probability,
            "max_chatter_probability": self.max_chatter_probability,
            "cumulative_tool_wear": self.cumulative_tool_wear,
            "final_surface_roughness": self.final_surface_roughness,
        }


@dataclass
class WorldModelInfo:
    """世界模型元信息.

    Attributes
    ----------
    world_model_version : str
        世界模型版本（semver）.
    training_data_size : int
        训练数据样本数.
    prediction_horizon : int
        训练时的预测步长.
    uncertainty_estimate : float
        模型预测不确定性估计 [0, 1].
    """

    world_model_version: str
    training_data_size: int
    prediction_horizon: int
    uncertainty_estimate: float

    def __post_init__(self) -> None:
        if not self.world_model_version:
            raise ValueError("world_model_version 不能为空")
        if self.training_data_size < 0:
            raise ValueError(f"training_data_size 不能为负数: {self.training_data_size}")
        if not MIN_HORIZON <= self.prediction_horizon <= MAX_HORIZON:
            raise ValueError(f"prediction_horizon 必须在 [{MIN_HORIZON}, {MAX_HORIZON}]: {self.prediction_horizon}")
        if not 0.0 <= self.uncertainty_estimate <= 1.0:
            raise ValueError(f"uncertainty_estimate 必须在 [0, 1]: {self.uncertainty_estimate}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_model_version": self.world_model_version,
            "training_data_size": self.training_data_size,
            "prediction_horizon": self.prediction_horizon,
            "uncertainty_estimate": self.uncertainty_estimate,
        }


@dataclass
class WorldModelPredictResponse:
    """世界模型预测响应.

    Attributes
    ----------
    predicted_trajectory : list[TrajectoryStep]
        预测的状态轨迹（长度 = horizon）.
    trajectory_metrics : TrajectoryMetrics
        轨迹汇总指标.
    model_info : WorldModelInfo
        世界模型元信息.
    """

    predicted_trajectory: list[TrajectoryStep]
    trajectory_metrics: TrajectoryMetrics
    model_info: WorldModelInfo

    def __post_init__(self) -> None:
        if not self.predicted_trajectory:
            raise ValueError("predicted_trajectory 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted_trajectory": [s.to_dict() for s in self.predicted_trajectory],
            "trajectory_metrics": self.trajectory_metrics.to_dict(),
            "model_info": self.model_info.to_dict(),
        }


# 模型版本


@dataclass
class WorldModelVersion:
    """世界模型版本记录.

    Attributes
    ----------
    version : str
        版本号（semver，如 ``1.0.0``）.
    model_uri : str
        模型 URI（``model://world_model/<version>``）.
    description : str
        版本描述.
    created_at : datetime
        创建时间.
    training_data_size : int
        训练数据样本数.
    prediction_horizon : int
        训练时的预测步长.
    is_active : bool
        是否为当前激活版本.
    """

    version: str
    model_uri: str
    description: str
    created_at: datetime
    training_data_size: int
    prediction_horizon: int
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_uri": self.model_uri,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "training_data_size": self.training_data_size,
            "prediction_horizon": self.prediction_horizon,
            "is_active": self.is_active,
        }


# 异常层级


class WorldModelError(Exception):
    """世界模型错误基类."""


class PredictionError(WorldModelError):
    """预测失败（网络前向传播异常 / 数据加载失败）."""


class ModelNotFoundError(WorldModelError):
    """模型未找到（``model_uri`` 未注册）."""


class InvalidStateError(WorldModelError):
    """无效状态（状态字典字段缺失 / 值超出范围）."""


__all__ = [
    # 任务类型常量
    "WM_PREDICT_STATE_TASK_TYPE",
    # 维度与步长常量
    "DEFAULT_STATE_DIM",
    "DEFAULT_ACTION_DIM",
    "DEFAULT_HORIZON",
    "MAX_HORIZON",
    "MIN_HORIZON",
    # 字段标签
    "StateField",
    "ActionField",
    # 请求/响应
    "WorldModelPredictRequest",
    "TrajectoryStep",
    "TrajectoryMetrics",
    "WorldModelInfo",
    "WorldModelPredictResponse",
    # 版本
    "WorldModelVersion",
    # 异常
    "WorldModelError",
    "PredictionError",
    "ModelNotFoundError",
    "InvalidStateError",
]
