"""基准实验评估指标计算模块。

实现全面的模型性能评估指标：
- 预测精度：MAE、RMSE、R²、MAPE
- 效率与资源：推理速度、模型大小、训练时间
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class MetricsResult:
    mae: float = 0.0
    rmse: float = 0.0
    r2: float = 0.0
    mape: float = 0.0
    inference_time_ms: float = 0.0
    model_size_mb: float = 0.0
    training_time_s: float = 0.0
    params_count: int = 0
    sample_fraction: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mae": round(self.mae, 6),
            "rmse": round(self.rmse, 6),
            "r2": round(self.r2, 6),
            "mape": round(self.mape, 4),
            "inference_time_ms": round(self.inference_time_ms, 4),
            "model_size_mb": round(self.model_size_mb, 4),
            "training_time_s": round(self.training_time_s, 3),
            "params_count": self.params_count,
            "sample_fraction": self.sample_fraction,
        }


def compute_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def compute_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def compute_r2(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    ss_res = np.sum((y_true - y_pred) ** 2)
    ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
    if ss_tot == 0:
        return 1.0
    return float(1.0 - ss_res / ss_tot)


def compute_mape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true_safe = np.where(np.abs(y_true) < 1e-8, 1e-8, y_true)
    return float(np.mean(np.abs((y_true - y_pred) / y_true_safe)) * 100)


def measure_inference_speed(
    predict_fn: Any,
    X_test: np.ndarray,
    n_warmup: int = 10,
    n_repeat: int = 100,
) -> float:
    for _ in range(n_warmup):
        predict_fn(X_test[:1])

    times: list[float] = []
    for i in range(min(n_repeat, X_test.shape[0])):
        start = time.perf_counter()
        predict_fn(X_test[i : i + 1])
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    return float(np.mean(times))


def measure_model_size_mb(model: Any) -> float:
    tmp_path = (
        "/tmp/_benchmark_model_tmp.pkl"
        if os.name != "nt"
        else os.path.join(os.environ.get("TEMP", "/tmp"), "_benchmark_model_tmp.pkl")
    )
    try:
        import pickle

        with open(tmp_path, "wb") as f:
            pickle.dump(model, f)
        size = os.path.getsize(tmp_path) / (1024 * 1024)
    except (OSError, pickle.PickleError, AttributeError) as e:
        import logging

        logging.getLogger(__name__).warning("模型大小计算失败: %s", e, exc_info=True)
        size = 0.0
    finally:
        # H18 修复：TOCTOU — exists() 与 unlink() 之间文件可能被其他进程删除。
        # 直接 try/except FileNotFoundError 更安全。
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
    return size


def compute_all_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    predict_fn: Any | None = None,
    X_test: np.ndarray | None = None,
    model: Any | None = None,
    training_time_s: float = 0.0,
    params_count: int = 0,
    sample_fraction: float = 1.0,
) -> MetricsResult:
    result = MetricsResult(
        mae=compute_mae(y_true, y_pred),
        rmse=compute_rmse(y_true, y_pred),
        r2=compute_r2(y_true, y_pred),
        mape=compute_mape(y_true, y_pred),
        training_time_s=training_time_s,
        params_count=params_count,
        sample_fraction=sample_fraction,
    )

    if predict_fn is not None and X_test is not None and X_test.shape[0] > 0:
        result.inference_time_ms = measure_inference_speed(predict_fn, X_test)

    if model is not None:
        result.model_size_mb = measure_model_size_mb(model)

    return result
