"""轨迹记忆约束 TrajectoryMemory（StreamingPredictor 拆分子模块）。"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


logger = logging.getLogger(__name__)


class TrajectoryMemory:
    """轨迹记忆（对应 lingbot-map 的 trajectory memory）.

    维护近期预测结果的滑动窗口，对当前预测做长程一致性约束。

    LTC 长时间推理会出现"预测漂移"（刀具磨损预测值偏离真实趋势），
    通过轨迹记忆对当前预测做平滑约束，类似 lingbot-map 用 trajectory
    memory 修正长程位姿漂移。

    约束策略：当前预测 = (1 - s) * 当前预测 + s * 轨迹均值
    其中 s 是 ``correction_strength``。
    """

    def __init__(
        self,
        window_size: int = 64,
        correction_strength: float = 0.05,
    ) -> None:
        self._window_size = max(1, window_size)
        self._correction_strength = correction_strength
        self._trajectory: deque[Any] = deque(maxlen=self._window_size)
        self._lock = threading.RLock()

    def push(self, prediction: Any) -> None:
        """记录一次预测结果。"""
        with self._lock:
            self._trajectory.append(self._to_array(prediction))

    def correct(self, prediction: Any) -> tuple[Any, float]:
        """对当前预测施加轨迹一致性约束。

        Returns
        -------
        Tuple[Any, float]
            (修正后的预测, 轨迹偏差量)。
        """
        with self._lock:
            if len(self._trajectory) < 2:
                return prediction, 0.0
            try:
                stacked = np.stack(list(self._trajectory), axis=0)
                trajectory_mean = np.mean(stacked, axis=0)
                current = self._to_array(prediction)
                deviation = float(np.linalg.norm(current - trajectory_mean))
                strength = self._correction_strength
                corrected = (1 - strength) * current + strength * trajectory_mean
                return corrected, deviation
            except (ValueError, TypeError) as exc:
                logger.debug("轨迹修正失败: %s", exc)
                return prediction, 0.0

    def reset(self) -> None:
        with self._lock:
            self._trajectory.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "window_size": self._window_size,
                "current_size": len(self._trajectory),
                "correction_strength": self._correction_strength,
            }

    def _to_array(self, value: Any) -> np.ndarray:
        if HAS_TORCH and isinstance(value, torch.Tensor):
            return value.detach().cpu().numpy().ravel()
        if isinstance(value, np.ndarray):
            return value.ravel()
        try:
            return np.asarray(value, dtype=np.float64).ravel()
        except (ValueError, TypeError):
            return np.array([0.0])
