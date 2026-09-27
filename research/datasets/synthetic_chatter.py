"""合成颤振时序数据生成器（Phase 3a：④ SSM 预测 backbone 的数据管线）。

背景：用户暂无自采数据（TDengine 无切削颤振时序），但 GPU 可用。
本模块按加工物理规律生成「工艺参数 + 振动信号 + 颤振标签」的仿真数据集，
用于：① SSM 模型冒烟训练；② 数据管线原型（后续接真实采集即替换数据源）。

物理建模（简化但可解释）：
- 特征通道：channel0=振动信号（动态），channel1=主轴转速（归一化），
  channel2=轴向切深 mm（静态，主变量），channel3=刀具磨损指数（静态）；
- 基础振动 = 主轴谐波（rpm/60×k Hz）叠加噪声，幅值随切深/磨损增长；
- 颤振触发：切深超过阈值（含磨损修正）→ 概率性进入颤振态；
  颤振态叠加高频阻尼振荡突发（burst），能量计入连续强度标签；
- 标签：y_intensity ∈ [0,1]（连续颤振强度，回归目标），
  y_chatter ∈ {0,1}（二分类目标）。

用法：
    from datasets.synthetic_chatter import generate_chatter_dataset, make_chatter_dataloader
    train_ds = generate_chatter_dataset(n_samples=1024, seq_len=200, seed=42)
    loader = make_chatter_dataloader(train_ds, batch_size=32)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset


@dataclass
class SyntheticChatterConfig:
    """合成数据配置。"""

    n_samples: int = 1024
    seq_len: int = 200
    n_features: int = 4  # 0=振动, 1=转速, 2=切深, 3=磨损
    fs: float = 1000.0  # 采样率 Hz
    chatter_ratio: float = 0.5
    seed: int = 42
    meta: dict = field(default_factory=dict)


def _chatter_probability(depth: float, wear: float) -> float:
    """颤振概率：随切深增大、磨损加重而上升（sigmoid 型）。"""
    threshold = 1.8 - 0.5 * wear  # 磨损越重，临界切深越低
    return 1.0 / (1.0 + math.exp(-4.0 * (depth - threshold)))


def _build_vibration(
    rng: np.random.Generator,
    seq_len: int,
    fs: float,
    rpm_norm: float,
    depth: float,
    wear: float,
    chatter: int,
) -> np.ndarray:
    """生成单样本振动信号。"""
    rpm = 3000 + rpm_norm * 20000  # 3000 ~ 23000 rpm
    spindle_hz = rpm / 60.0
    t = np.arange(seq_len) / fs

    base_amp = 0.02 + 0.06 * depth + 0.05 * wear
    signal = base_amp * (
        0.6 * np.sin(2 * np.pi * spindle_hz * t)
        + 0.25 * np.sin(2 * np.pi * 2 * spindle_hz * t)
        + 0.15 * np.sin(2 * np.pi * 3 * spindle_hz * t)
    )

    if chatter:
        # 颤振：高频阻尼振荡突发（burst），随机起始相位
        t0 = rng.integers(0, max(seq_len // 2, 1))
        burst_len = seq_len - t0
        tb = np.arange(burst_len) / fs
        chatter_hz = spindle_hz * 8.0
        envelope = np.exp(-4.0 * tb)
        burst = (0.15 + 0.25 * depth) * envelope * np.sin(2 * np.pi * chatter_hz * tb)
        signal[t0:] += burst

    noise = rng.normal(0, 0.01, seq_len)
    return signal + noise


def _chatter_intensity(rng: np.random.Generator, depth: float, wear: float, chatter: int) -> float:
    """连续颤振强度标签 [0,1]。"""
    base = 1.0 / (1.0 + math.exp(-4.0 * (depth - (1.8 - 0.5 * wear))))
    burst = 0.3 * (0.5 + 0.5 * depth) if chatter else 0.0
    return float(np.clip(base + burst, 0.0, 1.0))


def generate_chatter_dataset(
    n_samples: int = 1024,
    seq_len: int = 200,
    n_features: int = 4,
    fs: float = 1000.0,
    seed: int = 42,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, SyntheticChatterConfig]:
    """生成合成颤振数据集。

    Returns:
        (X (N, T, F), y_intensity (N,1), y_chatter (N,1), config)
    """
    cfg = SyntheticChatterConfig(
        n_samples=n_samples,
        seq_len=seq_len,
        n_features=n_features,
        fs=fs,
        seed=seed,
    )
    rng = np.random.default_rng(seed)

    X = np.zeros((n_samples, seq_len, n_features), dtype=np.float32)
    y_intensity = np.zeros((n_samples, 1), dtype=np.float32)
    y_chatter = np.zeros((n_samples, 1), dtype=np.float32)

    for i in range(n_samples):
        rpm_norm = rng.uniform(0.3, 0.9)
        depth = rng.uniform(0.5, 3.2)
        wear = rng.uniform(0.0, 1.0)
        p = _chatter_probability(depth, wear)
        chatter = int(rng.random() < p)

        vibration = _build_vibration(rng, seq_len, fs, rpm_norm, depth, wear, chatter)

        X[i, :, 0] = vibration
        X[i, :, 1] = rpm_norm  # 静态工艺参数广播到每个时间步
        X[i, :, 2] = depth
        X[i, :, 3] = wear
        y_intensity[i, 0] = _chatter_intensity(rng, depth, wear, chatter)
        y_chatter[i, 0] = float(chatter)

    cfg.meta = {
        "chatter_ratio": float(y_chatter.mean()),
        "n_features": n_features,
        "seq_len": seq_len,
        "fs": fs,
    }
    return (
        torch.from_numpy(X),
        torch.from_numpy(y_intensity),
        torch.from_numpy(y_chatter),
        cfg,
    )


def make_chatter_dataloader(
    X: torch.Tensor,
    y_intensity: torch.Tensor,
    y_chatter: torch.Tensor,
    batch_size: int = 32,
    shuffle: bool = True,
) -> DataLoader:
    """打包为 (X, y_intensity, y_chatter) 的 DataLoader。"""
    dataset = TensorDataset(X, y_intensity, y_chatter)
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


__all__ = [
    "SyntheticChatterConfig",
    "generate_chatter_dataset",
    "make_chatter_dataloader",
]
