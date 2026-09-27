"""融合路径训练数据集（ADR-020 思路 1 P1）.

每个训练样本是一个四元组::

    (
        geometry_seq,        # [T, geo_dim=3+feature_dim+2]
        dynamics_seq,        # [T, 6]
        actions,             # [T + horizon, action_dim]
        target_trajectory,   # [horizon, state_dim]
    )

对应 ``WorldModelNet.forward(states=None, actions=..., horizon=...,
unified_states=(geo, dyn))`` 的输入契约：LSTM 编码前 ``T`` 步融合
embedding + 动作，LTC 解码器自回归预测后 ``horizon`` 步状态轨迹，
target_trajectory 作为 MSE 监督信号。

数据来源（生产链路）
--------------------
- geometry_seq：``GeometryFeaturesDeriver`` 产出的 UnifiedState.geometry
  按 ``to_tensor_input()`` 展平，再沿时间维堆叠成 ``[T, geo_dim]``
- dynamics_seq：``DynamicsStateBridge`` 产出的 UnifiedState.dynamics
  按 ``to_tensor_input()`` 展平，再沿时间维堆叠成 ``[T, 6]``
- actions：候选切削参数序列（含历史 + 未来 horizon 步）
- target_trajectory：未来 horizon 步的真实状态（颤振概率/磨损/质量等）

工程约束
--------
- torch 不可用时本模块导入即抛 RuntimeError（Dataset 必须 torch）；
  但 ``validate_sample`` 是纯 numpy 校验，可在无 torch 环境下用于
  数据预处理流水线的早期失败检测。
- 严格类型校验：拒绝 ``tuple("not_a_list")`` 类隐患（与 P0-3 修复一致），
  geometry/dynamics/actions/target 必须是 list/np.ndarray，不能是 str。
"""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Sequence

import numpy as np

try:
    import torch
    from torch.utils.data import Dataset

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None
    Dataset = object

logger = logging.getLogger(__name__)

# 几何输入维度 = bbox(3) + feature_vector(feature_dim) + symmetry(1) + complexity(1)
# 此处仅给出默认 32 维 feature_vector 时的常量，实际维度由 WorldModelConfig.feature_dim 决定
DEFAULT_FEATURE_DIM = 32
DEFAULT_GEO_INPUT_DIM = 3 + DEFAULT_FEATURE_DIM + 1 + 1  # = 37
DYNAMICS_INPUT_DIM = 6


class FusionSampleError(ValueError):
    """单个训练样本格式/形状非法。"""


def _coerce_to_ndarray(value: Any, name: str, expected_dim: int) -> np.ndarray:
    """把输入强制转为 float32 ndarray 并校验最后一维.

    严格类型校验：拒绝 str 等可迭代但语义错误的类型
    （``np.asarray("abc")`` 会产生 shape=() 的字符串数组，污染数据）。
    """
    if isinstance(value, str) or not isinstance(value, (list, tuple, np.ndarray)):
        raise FusionSampleError(f"{name} 必须为 list/tuple/np.ndarray，实际类型={type(value).__name__}")
    try:
        arr = np.asarray(value, dtype=np.float32)
    except (ValueError, TypeError) as exc:
        raise FusionSampleError(f"{name} 无法转为 float32 ndarray: {exc}") from exc
    if arr.ndim < 2:
        raise FusionSampleError(f"{name} 至少 2 维 [T, {expected_dim}]，实际 ndim={arr.ndim}")
    if arr.shape[-1] != expected_dim:
        raise FusionSampleError(f"{name} 最后一维必须为 {expected_dim}，实际={arr.shape[-1]}")
    if not np.all(np.isfinite(arr)):
        raise FusionSampleError(f"{name} 含 NaN/Inf，无法训练")
    return arr


def validate_sample(
    geometry_seq: Any,
    dynamics_seq: Any,
    actions: Any,
    target_trajectory: Any,
    *,
    geo_input_dim: int = DEFAULT_GEO_INPUT_DIM,
    dynamics_input_dim: int = DYNAMICS_INPUT_DIM,
    action_dim: int = 4,
    state_dim: int = 8,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """校验单个训练样本并转为 float32 ndarray 四元组.

    纯 numpy 校验，不依赖 torch，可用于数据预处理流水线的早期失败检测
    （在 DataLoader 之前批量校验，避免训练中途崩溃）。

    Args:
        geometry_seq: ``[T, geo_input_dim]`` 几何特征序列。
        dynamics_seq: ``[T, 6]`` 动力学状态序列。
        actions: ``[T + horizon, action_dim]`` 候选动作序列（含历史 + 未来）。
        target_trajectory: ``[horizon, state_dim]`` 监督目标轨迹。
        geo_input_dim: 几何输入维度（默认 37）。
        dynamics_input_dim: 动力学输入维度（默认 6）。
        action_dim: 动作维度（默认 4）。
        state_dim: 状态维度（默认 8）。

    Returns
    -------
    tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]
        ``(geometry, dynamics, actions, target)`` 四个 float32 ndarray。

    Raises
    ------
    FusionSampleError
        类型/形状/有限性校验失败，或时间维度不一致。
    """
    geo = _coerce_to_ndarray(geometry_seq, "geometry_seq", geo_input_dim)
    dyn = _coerce_to_ndarray(dynamics_seq, "dynamics_seq", dynamics_input_dim)
    act = _coerce_to_ndarray(actions, "actions", action_dim)
    tgt = _coerce_to_ndarray(target_trajectory, "target_trajectory", state_dim)

    T_geo = geo.shape[0]
    T_dyn = dyn.shape[0]
    if T_geo != T_dyn:
        raise FusionSampleError(f"geometry_seq 与 dynamics_seq 时间维度必须一致: T_geo={T_geo} T_dyn={T_dyn}")
    T = T_geo
    horizon = tgt.shape[0]
    if act.shape[0] != T + horizon:
        raise FusionSampleError(
            f"actions 时间维度 ({act.shape[0]}) 必须等于 T + horizon ({T} + {horizon} = {T + horizon})"
        )
    if T < 1:
        raise FusionSampleError("历史序列长度 T 必须 >= 1")
    if horizon < 1:
        raise FusionSampleError("预测步长 horizon 必须 >= 1")
    return geo, dyn, act, tgt


class FusionTrajectoryDataset(Dataset):
    """融合路径轨迹训练数据集.

    Parameters
    ----------
    samples : Sequence[dict]
        样本列表，每个样本是 dict::

            {
                "geometry_seq": [[...], ...],      # [T, geo_dim]
                "dynamics_seq": [[...], ...],      # [T, 6]
                "actions": [[...], ...],           # [T + horizon, action_dim]
                "target_trajectory": [[...], ...], # [horizon, state_dim]
            }
    geo_input_dim : int
        几何输入维度（默认 37，与 ``WorldModelConfig.feature_dim=32`` 对应）。
    dynamics_input_dim : int
        动力学输入维度（默认 6）。
    action_dim : int
        动作维度（默认 4）。
    state_dim : int
        状态维度（默认 8）。
    strict : bool
        True（默认）时构造数据集即校验所有样本，任一非法立即抛错；
        False 时延后到 ``__getitem__`` 校验（适用于流式加载，但训练
        中途崩溃风险较高，不推荐）。
    """

    def __init__(
        self,
        samples: Sequence[dict[str, Any]],
        *,
        geo_input_dim: int = DEFAULT_GEO_INPUT_DIM,
        dynamics_input_dim: int = DYNAMICS_INPUT_DIM,
        action_dim: int = 4,
        state_dim: int = 8,
        strict: bool = True,
    ) -> None:
        if not HAS_TORCH:
            raise RuntimeError(
                "FusionTrajectoryDataset 需要 torch，当前环境未安装。"
                "请安装 torch 或在纯 numpy 环境下使用 validate_sample 做预处理校验。"
            )
        if not isinstance(samples, (list, tuple)):
            raise TypeError(f"samples 必须为 list/tuple，实际={type(samples).__name__}")
        self._geo_input_dim = geo_input_dim
        self._dynamics_input_dim = dynamics_input_dim
        self._action_dim = action_dim
        self._state_dim = state_dim
        self._samples: list[dict[str, np.ndarray]] = []

        for idx, raw in enumerate(samples):
            if not isinstance(raw, dict):
                raise FusionSampleError(f"samples[{idx}] 必须为 dict，实际={type(raw).__name__}")
            try:
                geo, dyn, act, tgt = validate_sample(
                    raw["geometry_seq"],
                    raw["dynamics_seq"],
                    raw["actions"],
                    raw["target_trajectory"],
                    geo_input_dim=geo_input_dim,
                    dynamics_input_dim=dynamics_input_dim,
                    action_dim=action_dim,
                    state_dim=state_dim,
                )
            except KeyError as exc:
                raise FusionSampleError(f"samples[{idx}] 缺少必需字段: {exc}") from exc
            self._samples.append(
                {
                    "geometry_seq": geo,
                    "dynamics_seq": dyn,
                    "actions": act,
                    "target_trajectory": tgt,
                }
            )
        if strict and not self._samples:
            logger.warning("FusionTrajectoryDataset 为空（0 个样本）")
        logger.info(
            "FusionTrajectoryDataset 初始化: samples=%d geo_dim=%d dyn_dim=%d action_dim=%d state_dim=%d",
            len(self._samples),
            geo_input_dim,
            dynamics_input_dim,
            action_dim,
            state_dim,
        )

    def __len__(self) -> int:
        return len(self._samples)

    def __getitem__(self, idx: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        sample = self._samples[idx]
        return (
            sample["geometry_seq"],
            sample["dynamics_seq"],
            sample["actions"],
            sample["target_trajectory"],
        )

    @property
    def geo_input_dim(self) -> int:
        return self._geo_input_dim

    @property
    def dynamics_input_dim(self) -> int:
        return self._dynamics_input_dim

    @property
    def action_dim(self) -> int:
        return self._action_dim

    @property
    def state_dim(self) -> int:
        return self._state_dim


def fusion_collate_fn(
    batch: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
) -> tuple[
    "torch.Tensor",
    "torch.Tensor",
    "torch.Tensor",
    "torch.Tensor",
]:
    """自定义 collate_fn：把变长 numpy 四元组 batch 化为 torch 张量.

    要求 batch 内所有样本的 ``T`` 和 ``horizon`` 一致（变长序列请用
    ``pad_sequence`` 或在数据预处理阶段对齐）。这是 WorldModelNet.forward
    的硬约束：``actions`` 必须是 ``[batch, T + horizon, action_dim]`` 整张量。

    Returns
    -------
    tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]
        ``(geometry, dynamics, actions, target)``，shape 分别为
        ``[batch, T, geo_dim]`` / ``[batch, T, 6]`` /
        ``[batch, T + horizon, action_dim]`` / ``[batch, horizon, state_dim]``。
    """
    if not HAS_TORCH:
        raise RuntimeError("fusion_collate_fn 需要 torch")
    if not batch:
        raise ValueError("batch 不能为空")

    geo_list, dyn_list, act_list, tgt_list = zip(*batch, strict=True)
    # 形状一致性校验（batch 内 T / horizon 必须一致）
    T0 = geo_list[0].shape[0]
    horizon0 = tgt_list[0].shape[0]
    for i, (g, t) in enumerate(zip(geo_list, tgt_list, strict=True)):
        if g.shape[0] != T0:
            raise ValueError(f"batch 内 T 不一致: samples[0].T={T0} samples[{i}].T={g.shape[0]}")
        if t.shape[0] != horizon0:
            raise ValueError(
                f"batch 内 horizon 不一致: samples[0].horizon={horizon0} samples[{i}].horizon={t.shape[0]}"
            )

    geometry = torch.from_numpy(np.stack(geo_list)).float()  # [B, T, geo_dim]
    dynamics = torch.from_numpy(np.stack(dyn_list)).float()  # [B, T, 6]
    actions = torch.from_numpy(np.stack(act_list)).float()  # [B, T+H, A]
    target = torch.from_numpy(np.stack(tgt_list)).float()  # [B, H, S]
    return geometry, dynamics, actions, target


__all__ = [
    "DEFAULT_FEATURE_DIM",
    "DEFAULT_GEO_INPUT_DIM",
    "DYNAMICS_INPUT_DIM",
    "FusionSampleError",
    "FusionTrajectoryDataset",
    "fusion_collate_fn",
    "validate_sample",
]
