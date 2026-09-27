"""训练↔服务权重桥接（接线方案 WP3）。

核心问题：训练侧用标准化特征收敛更快，但服务侧 NumPy 模型是纯 MLP
前向、不做任何预处理——解法是把标准化**折叠进首末层权重**：

- 首层：``z = ((x - μx) / σx) @ W1 + b1`` ⇒ ``W1' = W1 / σx``，
  ``b1' = b1 - (μx / σx) @ W1``
- 末层：``y = (Wl @ h + bl) · σy + μy`` ⇒ ``Wl' = Wl · σy``，
  ``bl' = bl · σy + μy``

折叠后模型自包含（服务运行时零改动），训练-服务数值一致性由
:func:`numpy_mirror_forward` 与 torch 前向的 parity 检查兜底。
全部纯 NumPy，无 torch 依赖，可独立单测。
"""

from __future__ import annotations

import numpy as np


def numpy_mirror_forward(
    x: np.ndarray,
    weights: list[np.ndarray],
    biases: list[np.ndarray],
) -> np.ndarray:
    """与工程侧 NumPy 模型 2D 批量前向逐层同构：ReLU 隐层 + 线性输出层。"""
    h = np.asarray(x, dtype=np.float64)
    for i, (w, b) in enumerate(zip(weights, biases)):
        h = h @ w + b
        if i < len(weights) - 1:
            h = np.maximum(0, h)
    return h


def fold_standardization(
    weights: list[np.ndarray],
    biases: list[np.ndarray],
    x_mean: np.ndarray,
    x_std: np.ndarray,
    y_mean: float,
    y_std: float,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """把特征/目标标准化折进首末层权重，返回新 (weights, biases)。

    Raises:
        ValueError: 层数 < 1 或标准差含零（常数列无法折叠）。
    """
    if not weights:
        raise ValueError("折叠失败：权重层为空")
    if np.any(x_std <= 0) or y_std <= 0:
        bad = [i for i, s in enumerate(x_std) if s <= 0]
        raise ValueError(f"折叠失败：标准差非正（x_std 异常列 {bad}，y_std={y_std}）——常数列无法标准化")

    new_weights = [w.copy() for w in weights]
    new_biases = [b.copy() for b in biases]

    x_mean = np.asarray(x_mean, dtype=np.float64)
    x_std = np.asarray(x_std, dtype=np.float64)

    # W1 形状 (in, out)：按输入维度逐行除以 σ_i（z = Σ_i (x_i/σ_i)·W1[i,j] + b1'）
    new_weights[0] = new_weights[0] / x_std[:, None]
    new_biases[0] = new_biases[0] - ((x_mean / x_std) @ weights[0])

    last = len(new_weights) - 1
    new_weights[last] = new_weights[last] * y_std
    new_biases[last] = new_biases[last] * y_std + y_mean
    return new_weights, new_biases


def fit_standardization(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """拟合标准化参数（均值/总体标准差），零标准差列回退为 1 防除零。

    返回 (x_mean, x_std, y_mean, y_std)；常数列的 std 置 1（标准化无效果但可折叠）。
    """
    x_mean = x.mean(axis=0)
    x_std = x.std(axis=0)
    x_std = np.where(x_std > 0, x_std, 1.0)
    y_mean = float(y.mean())
    y_std = float(y.std())
    if y_std <= 0:
        y_std = 1.0
    return x_mean, x_std, y_mean, y_std


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """回归指标：R² / MAE / MAPE(%) / 最大绝对误差。"""
    y_true = np.asarray(y_true, dtype=np.float64).reshape(-1)
    y_pred = np.asarray(y_pred, dtype=np.float64).reshape(-1)
    if y_true.shape != y_pred.shape or y_true.size == 0:
        raise ValueError(f"指标计算输入形状不一致或为空: {y_true.shape} vs {y_pred.shape}")

    ss_res = float(np.sum((y_true - y_pred) ** 2))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else float("nan")
    mae = float(np.mean(np.abs(y_true - y_pred)))
    denom = np.maximum(np.abs(y_true), 1e-12)
    mape = float(np.mean(np.abs(y_true - y_pred) / denom) * 100.0)
    return {
        "r2": r2,
        "mae": mae,
        "mape_percent": mape,
        "max_abs_error": float(np.max(np.abs(y_true - y_pred))),
    }
