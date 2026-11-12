"""用训练集持久性误差定义损失/增量单位，不转换接口的物理输出。"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

STATE_ORDER = ["chatter_probability", "tool_wear", "surface_roughness", "vibration_rms",
               "temperature_over_100c", "cutting_force_norm", "mrr_norm", "confidence"]


def validate_state_scale(spec: dict[str, Any], state_dim: int = 8) -> None:
    if not isinstance(spec, dict) or spec.get("schema_version") != 1 or spec.get("method") != "train_persistence_rmse":
        raise ValueError("状态尺度契约格式必须为版本1 train_persistence_rmse")
    if spec.get("fit_scope") != "train_targets_and_last_state_only" or spec.get("constant_scale") != 1.0:
        raise ValueError("状态尺度只能拟合训练目标及末条状态，常量通道尺度须为1")
    if state_dim != 8 or spec.get("state_order") != STATE_ORDER:
        raise ValueError("状态尺度需要明确的machining_v1八列口径")
    try:
        scale = np.asarray(spec.get("scale"), dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("状态尺度须为八个正有限数") from exc
    if scale.shape != (8,) or not np.isfinite(scale).all() or np.any(scale < np.finfo(np.float32).tiny) or np.any(scale > np.finfo(np.float32).max):
        raise ValueError("状态尺度须为八个可表示的正有限float32数")


def fit_state_scale(target: np.ndarray, initial_state: np.ndarray) -> dict[str, Any]:
    """只传训练部分，尺度为各通道未来目标相对当前状态的RMSE。"""
    target, initial = np.asarray(target, dtype=np.float64), np.asarray(initial_state, dtype=np.float64)
    if target.ndim != 3 or target.shape[-1] != 8 or initial.shape != (target.shape[0], 8):
        raise ValueError("拟合状态尺度需要target[N,H,8]与initial_state[N,8]")
    if min(target.shape[:2]) <= 0 or not np.isfinite(target).all() or not np.isfinite(initial).all():
        raise ValueError("状态尺度训练数据不能为空或包含非有限数")
    rms = np.sqrt(np.square(target - initial[:, None]).mean(axis=(0, 1)))
    constant = rms < 1e-8
    spec = {"schema_version": 1, "method": "train_persistence_rmse",
            "fit_scope": "train_targets_and_last_state_only", "state_order": list(STATE_ORDER),
            "constant_scale": 1.0, "zero_change_threshold": 1e-8,
            "constant_channels": np.flatnonzero(constant).tolist(), "scale": np.where(constant, 1.0, rms).tolist(),
            "train_windows": int(target.shape[0]), "horizon": int(target.shape[1]),
            "fit_inputs_sha256": hashlib.sha256(target.tobytes() + initial.tobytes()).hexdigest()}
    validate_state_scale(spec)
    return spec


try:
    import torch
    from torch import nn
except ImportError:
    torch = None
    nn = None


if nn is not None:
    class DeltaOutputScaler(nn.Module):
        """把增量头的相对数值转换成原单位增量，持久buffer须与元数据一致。"""

        def __init__(self, spec: dict[str, Any]):
            super().__init__()
            validate_state_scale(spec)
            self.register_buffer("scale", torch.tensor(spec["scale"], dtype=torch.float32))

        def forward(self, delta):
            if delta.shape[-1] != 8:
                raise ValueError("增量输出须为八列")
            result = delta * self.scale
            if not torch.isfinite(result).all():
                raise ValueError("增量输出包含非有限数")
            return result

        def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
            supplied = state_dict.get(prefix + "scale")
            if supplied is not None and (supplied.shape != self.scale.shape or not torch.equal(supplied.cpu(), self.scale.cpu())):
                error_msgs.append(f"{prefix}scale与model_config增量尺度不一致")
            super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs)
