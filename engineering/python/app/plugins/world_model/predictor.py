"""轨迹预测器：封装世界模型的自回归预测逻辑.

对应 ADR-017 第 1.3 节。``TrajectoryPredictor`` 负责加载模型权重、
执行前向预测、序列化输出，是 ``WorldModelPlugin`` 的核心依赖。

设计要点
--------
1. **模型加载**：从 ``model://world_model/<version>`` URI 解析模型文件路径，
   加载 ``WorldModelNet`` 权重
2. **设备管理**：自动选择 cuda/cpu/mps，与 ``LNNPredictor`` 风格一致
3. **批处理**：支持单样本与批量预测，自动 batch 维度扩展
4. **轨迹截断**：``horizon > max_trajectory_length`` 时报错，防止显存爆炸
5. **线程安全**：推理路径只读模型参数，无状态修改；模型加载用锁保护
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Union

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None

from app.plugins.world_model.net import WorldModelConfig, WorldModelNet
from app.plugins.world_model.unified_state import UnifiedState

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryPrediction:
    """轨迹预测结果.

    Attributes
    ----------
    predicted_trajectory : np.ndarray
        预测的状态轨迹，shape ``[horizon, state_dim]``（单样本）或
        ``[batch, horizon, state_dim]``（批量）。
    trajectory_metrics : np.ndarray
        轨迹指标，shape ``[3]`` 或 ``[batch, 3]``（颤振峰值/最大磨损/平均质量）。
    horizon : int
        实际预测步长。
    model_info : dict[str, Any]
        模型信息（model_uri / device / config）。
    """

    predicted_trajectory: np.ndarray
    trajectory_metrics: np.ndarray
    horizon: int
    model_info: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（API 响应）."""
        traj = self.predicted_trajectory
        metrics = self.trajectory_metrics
        if isinstance(traj, np.ndarray):
            traj = traj.tolist()
        if isinstance(metrics, np.ndarray):
            metrics = metrics.tolist()
        return {
            "predicted_trajectory": traj,
            "trajectory_metrics": metrics,
            "horizon": self.horizon,
            "model_info": self.model_info,
        }


class TrajectoryPredictor:
    """轨迹预测器：加载世界模型并执行自回归预测.

    Parameters
    ----------
    config : WorldModelConfig
        网络配置.
    device : str
        推理设备（``auto`` / ``cuda`` / ``cpu`` / ``mps``）.
    """

    def __init__(
        self,
        config: WorldModelConfig | None = None,
        device: str = "auto",
    ) -> None:
        self.config = config or WorldModelConfig()
        self.config.validate()
        self._device_str = device
        self._device = self._select_device(device)
        self._model: WorldModelNet | None = None
        self._model_uri: str | None = None
        self._lock = threading.Lock()
        logger.info(
            "TrajectoryPredictor 初始化: device=%s config=%s",
            self._device,
            self.config.to_dict(),
        )

    def _select_device(self, device: str) -> Any:
        """选择推理设备."""
        if not HAS_TORCH or device == "cpu":
            return "cpu"
        if device == "auto":
            if torch.cuda.is_available():
                return torch.device("cuda")
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return torch.device("mps")
            return torch.device("cpu")
        return torch.device(device)

    def load_model(self, model_uri: str, weights_path: str | None = None) -> None:
        """加载模型权重.

        Args:
            model_uri: 模型 URI（如 ``model://world_model/1.0.0``）。
            weights_path: 权重文件路径。若为空，则使用随机初始化权重
                （仅用于接口验证，预测无意义）。
        """
        with self._lock:
            self._model = WorldModelNet(self.config)
            self._model_uri = model_uri

            if HAS_TORCH and weights_path:
                try:
                    # 安全：强制 weights_only=True，只接受纯张量 state_dict，
                    # 拒绝反序列化任意对象（RCE 风险）。weights_only 自
                    # torch 1.13 起支持，桌面 runtime 自带现代 torch，无需
                    # 为史前版本保留无防护的降级路径。
                    state_dict = torch.load(weights_path, map_location="cpu", weights_only=True)
                except ValueError as e:
                    # weights_only 校验拒绝的权重（含非白名单 pickle 对象）：
                    # 显式拒绝加载，提示使用可信权重
                    raise RuntimeError(
                        f"世界模型权重加载被拒绝（weights_only 校验失败）: {weights_path}。"
                        "文件包含非标准权重对象，可能已损坏或被篡改，请使用可信权重。"
                    ) from e
                    if hasattr(self._model, "load_state_dict"):
                        self._model.load_state_dict(state_dict)
                    logger.info(
                        "世界模型权重已加载: uri=%s path=%s",
                        model_uri,
                        weights_path,
                    )
                except (RuntimeError, OSError, KeyError) as exc:
                    logger.warning("权重加载失败，使用随机初始化: %s", exc)
            elif not weights_path:
                logger.warning("未提供权重路径，使用随机初始化权重。预测结果无实际意义，仅用于接口验证。")

            if HAS_TORCH and hasattr(self._model, "to") and hasattr(self._device, "type"):
                self._model.to(self._device)
                self._model.eval()

    def predict(
        self,
        current_state: Union[np.ndarray, UnifiedState, dict, None] = None,
        candidate_action: np.ndarray | None = None,
        horizon: int = 10,
        unified_state: Union[UnifiedState, dict, None] = None,
    ) -> TrajectoryPrediction:
        """执行轨迹预测.

        支持两种输入模式：

        1. **原始模式**（``config.use_fusion=False``，默认）：
            传入 ``current_state`` (np.ndarray) + ``candidate_action``。
            状态向量由字段拼接而成（颤振概率/磨损/质量等 state_dim 维）。

        2. **融合模式**（``config.use_fusion=True``，ADR-020 思路 1）：
            传入 ``unified_state`` (UnifiedState | dict) + ``candidate_action``。
            UnifiedState 包含几何特征（ADR-007）与动力学状态（ADR-013），
            经 GeometryEncoder/DynamicsEncoder/FusionLayer 投影到融合
            embedding 后喂入 LSTM。

        Args:
            current_state: 原始模式下当前状态，shape ``[state_dim]`` 或
                ``[T, state_dim]``。融合模式下可为 None。
            candidate_action: 候选动作序列，shape ``[horizon, action_dim]``。
            horizon: 预测步长。
            unified_state: 融合模式下的统一状态（UnifiedState 对象或字典）。

        Returns
        -------
        TrajectoryPrediction
            轨迹预测结果。

        Raises
        ------
        RuntimeError
            模型未加载，或融合模式下无 torch 环境。
        ValueError
            输入形状不匹配或 horizon 越界。
        """
        if self._model is None:
            raise RuntimeError("模型未加载，请先调用 load_model()。")

        self.config.validate()
        if horizon <= 0 or horizon > self.config.max_trajectory_length:
            raise ValueError(f"horizon 必须在 [1, {self.config.max_trajectory_length}], 当前: {horizon}")
        if candidate_action is None:
            raise ValueError("candidate_action 不能为 None")

        # 选择输入路径：融合模式 vs 原始模式
        # ADR-020 P3：基于输入类型判定路径，而非仅依赖 use_fusion 配置。
        # has_unified_input=True 当且仅当传入 unified_state 或 current_state
        # 是 UnifiedState/dict（非 np.ndarray）。这样 use_fusion=True 默认
        # 开启后，legacy 调用（current_state=np.ndarray）仍走原始路径。
        has_unified_input = unified_state is not None or (
            current_state is not None and not isinstance(current_state, np.ndarray)
        )

        # 校验：传入 unified_state 但 config.use_fusion=False
        if has_unified_input and not self.config.use_fusion:
            raise ValueError(
                "传入 unified_state 但 config.use_fusion=False，请在 WorldModelConfig 中设置 use_fusion=True。"
            )

        # ADR-020 P3 降级兜底：融合输入但 torch 不可用 降级到原始 NumPy 路径。
        # UnifiedState 无法直接转为 state_dim 维向量（geometry 37 + dynamics 6
        # ≠ state_dim 8），构造零向量兜底（仅满足接口契约，预测无意义，
        # 与 NumPy 回退语义一致）。
        if has_unified_input and not HAS_TORCH:
            logger.warning(
                "use_fusion=True 但 torch 不可用，降级到原始 NumPy 路径。"
                "UnifiedState 无法转换为 state_dim 向量，使用零向量兜底。"
                "预测结果无实际意义，请安装 torch 以启用融合模式。"
            )
            has_unified_input = False
            current_state = np.zeros((1, self.config.state_dim), dtype=np.float32)

        if has_unified_input:
            us_source = unified_state if unified_state is not None else current_state
            if us_source is None:
                raise ValueError("融合模式需要 unified_state 或可用的 current_state")
            if not isinstance(us_source, (UnifiedState, dict)):
                raise ValueError(f"融合模式 unified_state 必须为 UnifiedState 或 dict，实际 {type(us_source)}")
            us = self._coerce_unified_state(us_source)
            return self._predict_fused(us, candidate_action, horizon)

        # 原始模式：current_state 必须是 np.ndarray
        if current_state is None:
            raise ValueError("原始模式需要 current_state (np.ndarray)")
        if not isinstance(current_state, np.ndarray):
            raise ValueError(f"原始模式 current_state 必须为 np.ndarray，实际: {type(current_state)}")

        states_arr = self._standardize_states(current_state)
        actions_arr = self._standardize_actions(candidate_action, horizon, states_arr.shape[0])

        if HAS_TORCH:
            return self._predict_torch(states_arr, actions_arr, horizon)
        return self._predict_numpy(states_arr, actions_arr, horizon)

    # 融合路径辅助方法

    def _coerce_unified_state(self, source: Union[UnifiedState, dict]) -> UnifiedState:
        """把 UnifiedState 对象或 dict 统一为 UnifiedState 实例."""
        if isinstance(source, UnifiedState):
            return source
        if isinstance(source, dict):
            return UnifiedState.from_dict(source)
        raise ValueError(f"unified_state 必须为 UnifiedState 或 dict，实际: {type(source)}")

    def _predict_fused(
        self,
        unified_state: UnifiedState,
        candidate_action: np.ndarray,
        horizon: int,
    ) -> TrajectoryPrediction:
        """融合模式前向预测.

        把单个 UnifiedState（无时序）扩展为长度 T=1 的历史序列，
        再走 WorldModelNet.forward(unified_states=...) 融合路径。
        """
        if not HAS_TORCH:
            raise RuntimeError("融合模式需要 torch 支持，当前环境未安装 torch。")
        assert torch is not None
        assert self._model is not None

        # 1. 张量化 UnifiedState [1, 1, input_dim]
        geo_input = np.asarray(unified_state.geometry.to_tensor_input(), dtype=np.float32).reshape(1, 1, -1)
        dyn_input = np.asarray(unified_state.dynamics.to_tensor_input(), dtype=np.float32).reshape(1, 1, -1)

        # 维度校验
        expected_geo = 3 + self.config.feature_dim + 1 + 1
        if geo_input.shape[-1] != expected_geo:
            raise ValueError(
                f"geometry 输入维度不匹配: 期望 {expected_geo}, "
                f"实际 {geo_input.shape[-1]}（feature_dim={self.config.feature_dim}）"
            )
        if dyn_input.shape[-1] != 6:
            raise ValueError(f"dynamics 输入维度不匹配: 期望 6, 实际 {dyn_input.shape[-1]}")

        # 2. 标准化动作：[1, T + horizon, action_dim]（T=1）
        actions_arr = self._standardize_actions(candidate_action, horizon, batch_size=1)

        # 3. 转 torch 张量
        geo_tensor = torch.from_numpy(geo_input).to(self._device)
        dyn_tensor = torch.from_numpy(dyn_input).to(self._device)
        actions_tensor = torch.from_numpy(actions_arr).to(self._device)

        # 4. 前向（融合路径）
        with torch.inference_mode():
            outputs = self._model(
                states=None,
                actions=actions_tensor,
                horizon=horizon,
                unified_states=(geo_tensor, dyn_tensor),
            )

        trajectory = outputs["predicted_trajectory"].cpu().numpy()
        metrics = outputs["trajectory_metrics"].cpu().numpy()
        # 单样本去 batch 维度
        if trajectory.shape[0] == 1:
            trajectory = trajectory[0]
            metrics = metrics[0]

        return TrajectoryPrediction(
            predicted_trajectory=trajectory,
            trajectory_metrics=metrics,
            horizon=horizon,
            model_info={
                "model_uri": self._model_uri,
                "device": str(self._device),
                "config": self.config.to_dict(),
                "backend": "torch",
                "mode": "fusion",
                "fused_embedding_dim": self.config.fused_dim,
            },
        )

    def _standardize_states(self, current_state: np.ndarray) -> np.ndarray:
        """标准化状态输入为 ``[batch, T, state_dim]``."""
        arr = np.asarray(current_state, dtype=np.float32)
        if arr.ndim == 1:
            # [state_dim] [1, 1, state_dim]
            arr = arr.reshape(1, 1, -1)
        elif arr.ndim == 2:
            # [T, state_dim] [1, T, state_dim]
            arr = arr.reshape(1, arr.shape[0], arr.shape[1])
        elif arr.ndim != 3:
            raise ValueError(f"current_state 维度必须为 1/2/3，当前: {arr.ndim}")
        if arr.shape[-1] != self.config.state_dim:
            raise ValueError(f"state_dim 不匹配: 期望 {self.config.state_dim}, 实际 {arr.shape[-1]}")
        return arr

    def _standardize_actions(
        self,
        candidate_action: np.ndarray,
        horizon: int,
        batch_size: int,
    ) -> np.ndarray:
        """标准化动作输入为 ``[batch, T + horizon, action_dim]``.

        历史动作用零填充（世界模型主要依赖状态历史，动作历史为辅助）。
        """
        arr = np.asarray(candidate_action, dtype=np.float32)
        if arr.ndim == 1:
            # [action_dim] [horizon, action_dim]（单步动作广播到 horizon 步）
            arr = np.tile(arr.reshape(1, -1), (horizon, 1))
        if arr.ndim != 2:
            raise ValueError(f"candidate_action 维度必须为 1/2，当前: {arr.ndim}")
        if arr.shape[0] != horizon:
            raise ValueError(f"candidate_action 时间维度 ({arr.shape[0]}) 必须等于 horizon ({horizon})")
        if arr.shape[1] != self.config.action_dim:
            raise ValueError(f"action_dim 不匹配: 期望 {self.config.action_dim}, 实际 {arr.shape[1]}")
        # 历史动作用零填充（长度 T=1）
        history_actions = np.zeros((batch_size, 1, self.config.action_dim), dtype=np.float32)
        future_actions = np.tile(arr.reshape(1, horizon, -1), (batch_size, 1, 1))
        return np.concatenate([history_actions, future_actions], axis=1)

    def _predict_torch(
        self,
        states: np.ndarray,
        actions: np.ndarray,
        horizon: int,
    ) -> TrajectoryPrediction:
        """torch 前向预测."""
        assert self._model is not None
        assert torch is not None

        states_tensor = torch.from_numpy(states).to(self._device)
        actions_tensor = torch.from_numpy(actions).to(self._device)

        with torch.inference_mode():
            outputs = self._model(states_tensor, actions_tensor, horizon)

        trajectory = outputs["predicted_trajectory"].cpu().numpy()
        metrics = outputs["trajectory_metrics"].cpu().numpy()
        # 单样本去 batch 维度
        if trajectory.shape[0] == 1:
            trajectory = trajectory[0]
            metrics = metrics[0]

        return TrajectoryPrediction(
            predicted_trajectory=trajectory,
            trajectory_metrics=metrics,
            horizon=horizon,
            model_info={
                "model_uri": self._model_uri,
                "device": str(self._device),
                "config": self.config.to_dict(),
                "backend": "torch",
            },
        )

    def _predict_numpy(
        self,
        states: np.ndarray,
        actions: np.ndarray,
        horizon: int,
    ) -> TrajectoryPrediction:
        """NumPy 回退前向预测."""
        assert self._model is not None

        outputs = self._model.forward(states, actions, horizon)
        trajectory = outputs["predicted_trajectory"]
        metrics = outputs["trajectory_metrics"]
        if trajectory.shape[0] == 1:
            trajectory = trajectory[0]
            metrics = metrics[0]

        return TrajectoryPrediction(
            predicted_trajectory=trajectory,
            trajectory_metrics=metrics,
            horizon=horizon,
            model_info={
                "model_uri": self._model_uri,
                "device": "cpu",
                "config": self.config.to_dict(),
                "backend": "numpy",
            },
        )

    @property
    def model_uri(self) -> str | None:
        """已加载的模型 URI."""
        return self._model_uri

    @property
    def is_loaded(self) -> bool:
        """模型是否已加载."""
        return self._model is not None
