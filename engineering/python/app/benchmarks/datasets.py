"""基准实验数据集加载、预处理与划分模块。

支持UniWear刀具磨损数据集和CNC加工数据集的自动加载，
提供统一的特征预处理管道与分层抽样划分策略。
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler


def _get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _find_uniwear_csv() -> str:
    proj = _get_project_root()
    # engineering/python 是 proj 的根目录；research 目录在 engineering 同级
    research_dir = os.path.abspath(os.path.join(proj, "..", "..", "research"))
    candidates = [
        os.path.join(proj, "python", "data", "uniwear", "uniwear.csv"),
        os.path.join(proj, "data", "uniwear", "uniwear.csv"),
        # research 模块共享数据集（spec 解耦后的实际位置）
        os.path.join(research_dir, "datasets", "uniwear", "uniwear", "uniwear.csv"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("未找到uniwear.csv，已搜索: " + ", ".join(candidates))


def load_uniwear_data(
    path: str | None = None,
    random_seed: int = 42,
    val_size: float = 0.10,
    test_size: float = 0.10,
) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], dict[str, Any], StandardScaler]:
    """加载UniWear数据集并完成预处理与train/val/test划分。

    为避免数据泄漏，先按 ``random_seed`` 划分 train/val/test，再仅用
    训练集拟合 ``StandardScaler``，随后用该 scaler 转换验证集与测试集，
    最后对标准化后的特征做 4σ 截断。

    Returns:
        splits: ``{"train": (X_train, y_train), "val": ..., "test": ...}``，
                其中 X 已用训练集统计量标准化。
        metadata: 数据集元信息（n_samples 为全量样本数）。
        scaler: 仅在训练集上拟合好的 ``StandardScaler``，供推理时复用。
    """
    if path is None:
        path = _find_uniwear_csv()

    df = pd.read_csv(path)
    df = df.dropna()

    # Drop index/ID columns
    for col in ["Unnamed: 0", "index", "id"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Keep only numeric columns
    numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    if not numeric_cols:
        raise ValueError("数据集中无数值型列")
    df = df[numeric_cols]

    label_col = _find_label_column(df)
    y = df[label_col].values.astype(np.float64)
    X = df.drop(columns=[label_col]).values.astype(np.float64)

    feature_names = [c for c in df.columns if c != label_col]

    # 先划分 train/val/test，再用训练集拟合 scaler，避免测试集统计量泄漏
    raw_splits = split_dataset(
        X,
        y,
        random_seed=random_seed,
        val_size=val_size,
        test_size=test_size,
    )
    X_train, y_train = raw_splits["train"]
    X_val, y_val = raw_splits["val"]
    X_test, y_test = raw_splits["test"]

    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_val = scaler.transform(X_val)
    X_test = scaler.transform(X_test)

    # Handle extreme outliers by clipping to 4 sigma
    X_train = np.clip(X_train, -4, 4)
    X_val = np.clip(X_val, -4, 4)
    X_test = np.clip(X_test, -4, 4)

    splits = {
        "train": (X_train, y_train),
        "val": (X_val, y_val),
        "test": (X_test, y_test),
    }

    metadata = {
        "n_samples": X.shape[0],
        "n_features": X.shape[1],
        "feature_names": feature_names,
        "label_name": label_col,
        "label_mean": float(np.mean(y)),
        "label_std": float(np.std(y)),
        "label_min": float(np.min(y)),
        "label_max": float(np.max(y)),
        "dataset": os.path.basename(path),
    }
    return splits, metadata, scaler


def _find_label_column(df: pd.DataFrame) -> str:
    label_keywords = ["wear", "target", "label", "tool_wear", "vb", "cutting_force"]
    for kw in label_keywords:
        for col in df.columns:
            if kw.lower() in col.lower():
                return col
    # Fallback: last numeric column
    numeric_cols = df.select_dtypes(include=["number"]).columns
    return numeric_cols[-1]


def split_dataset(
    X: np.ndarray,
    y: np.ndarray,
    random_seed: int = 42,
    val_size: float = 0.10,
    test_size: float = 0.10,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    X_temp, X_test, y_temp, y_test = train_test_split(
        X,
        y,
        test_size=test_size,
        random_state=random_seed,
    )
    val_frac = val_size / (1.0 - test_size)
    X_train, X_val, y_train, y_val = train_test_split(
        X_temp,
        y_temp,
        test_size=val_frac,
        random_state=random_seed,
    )
    return {
        "train": (X_train, y_train),
        "val": (X_val, y_val),
        "test": (X_test, y_test),
    }


def sample_training_subset(
    X_train: np.ndarray,
    y_train: np.ndarray,
    fraction: float,
    random_seed: int = 42,
) -> tuple[np.ndarray, np.ndarray]:
    if fraction >= 1.0:
        return X_train.copy(), y_train.copy()
    n = max(int(X_train.shape[0] * fraction), 2)
    rng = np.random.RandomState(random_seed)
    idx = rng.choice(X_train.shape[0], size=n, replace=False)
    return X_train[idx].copy(), y_train[idx].copy()
