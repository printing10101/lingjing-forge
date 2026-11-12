"""训练输入拟合的融合标准化契约；在网络内统一应用，输出保持原单位。"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

DYNAMICS_ORDER = ["spindle_speed", "feed_rate", "depth_of_cut", "tool_wear", "vibration_rms", "temperature"]


def validate_normalization(spec: dict[str, Any], geometry_dim: int) -> None:
    if not isinstance(spec, dict) or spec.get("schema_version") != 1 or spec.get("method") != "train_zscore":
        raise ValueError("[标准化契约错误] 未知格式。建议操作：用训练集重新导出版本1参数。")
    if spec.get("fit_scope") != "train_history_inputs_only" or spec.get("constant_scale") != 1.0:
        raise ValueError("标准化须声明仅拟合训练历史输入，常量列尺度为1")
    if spec.get("dynamics_order") != DYNAMICS_ORDER:
        raise ValueError("标准化动力学列顺序不匹配")
    for group, dimension in (("geometry", geometry_dim), ("dynamics", 6)):
        values = spec.get(group)
        if not isinstance(values, dict):
            raise ValueError(f"缺少{group}标准化参数")
        for field in ("mean", "scale"):
            try:
                array = np.asarray(values.get(field), dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{group}.{field}须为数值列表") from exc
            if array.shape != (dimension,) or not np.isfinite(array).all() or np.any(np.abs(array) > np.finfo(np.float32).max):
                raise ValueError(f"{group}.{field}须含{dimension}个有限浮点数")
            if field == "scale" and (np.any(array <= 0) or np.any(array < np.finfo(np.float32).tiny)):
                raise ValueError("标准化尺度须为可表示的正浮点数")


def fit_fusion_normalization(geometry: np.ndarray, dynamics: np.ndarray) -> dict[str, Any]:
    """调用方只能传入划分完成后的训练历史，不接收验证集、动作或目标。"""
    geo, dyn = np.asarray(geometry, dtype=np.float64), np.asarray(dynamics, dtype=np.float64)
    if geo.ndim != 3 or dyn.ndim != 3 or geo.shape[:2] != dyn.shape[:2] or dyn.shape[-1] != 6 or geo.shape[-1] < 6:
        raise ValueError("拟合需要同批次/历史长度的geometry[N,T,D]和dynamics[N,T,6]")
    if min(geo.shape[:2]) <= 0 or not np.isfinite(geo).all() or not np.isfinite(dyn).all():
        raise ValueError("标准化训练输入不能为空或含非有限值")
    spec: dict[str, Any] = {"schema_version": 1, "method": "train_zscore",
        "fit_scope": "train_history_inputs_only", "constant_scale": 1.0, "dynamics_order": list(DYNAMICS_ORDER),
        "train_windows": int(geo.shape[0]), "train_history_points": int(geo.shape[0] * geo.shape[1]),
        "fit_inputs_sha256": hashlib.sha256(geo.tobytes() + dyn.tobytes()).hexdigest()}
    for name, array in (("geometry", geo), ("dynamics", dyn)):
        mean, std = array.mean(axis=(0, 1)), array.std(axis=(0, 1))
        constant = std < 1e-8
        spec[name] = {"mean": mean.tolist(), "scale": np.where(constant, 1.0, std).tolist(),
                      "constant_features": np.flatnonzero(constant).tolist()}
    validate_normalization(spec, geo.shape[-1])
    return spec


try:
    import torch
    from torch import nn
except ImportError:
    torch = None
    nn = None


if nn is not None:
    class FusionInputNormalizer(nn.Module):
        """持久buffer与元数据双重记录，删除/篡改契约不能静默退回原量纲。"""

        def __init__(self, spec: dict[str, Any], geometry_dim: int):
            super().__init__()
            validate_normalization(spec, geometry_dim)
            for group in ("geometry", "dynamics"):
                for field in ("mean", "scale"):
                    self.register_buffer(f"{group}_{field}", torch.tensor(spec[group][field], dtype=torch.float32))

        def forward(self, geometry, dynamics):
            if geometry.shape[-1] != self.geometry_mean.numel() or dynamics.shape[-1] != 6:
                raise ValueError("标准化输入列数与checkpoint不匹配")
            geo = (geometry - self.geometry_mean) / self.geometry_scale
            dyn = (dynamics - self.dynamics_mean) / self.dynamics_scale
            if not torch.isfinite(geo).all() or not torch.isfinite(dyn).all():
                raise ValueError("标准化结果包含非有限值")
            return geo, dyn

        def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs):
            for name, buffer in self.named_buffers():
                supplied = state_dict.get(prefix + name)
                if supplied is not None and (supplied.shape != buffer.shape or not torch.equal(supplied.cpu(), buffer.cpu())):
                    error_msgs.append(f"{prefix}{name}与model_config标准化契约不一致")
            super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys, unexpected_keys, error_msgs)
