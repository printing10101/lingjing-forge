"""
滑动窗口处理模块

为传感器时序数据提供可配置的滑动窗口分割，支持窗口重叠、动态采样率调整。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SlidingWindowConfig:
    """滑动窗口配置参数"""

    window_size: int = 256
    overlap_ratio: float = 0.5
    sample_rate: float = 1000.0
    sample_rate_min: float = 100.0
    sample_rate_max: float = 10000.0
    min_samples: int = 128
    pad_mode: str = "edge"

    @property
    def step_size(self) -> int:
        """根据窗口大小和重叠比例自动计算步长"""
        return max(1, int(self.window_size * (1 - self.overlap_ratio)))


class SlidingWindowProcessor:
    """
    滑动窗口处理器

    将连续时序数据分割为重叠窗口，用于模型训练和推理。
    支持动态采样率配置（100Hz-10kHz）。
    """

    def __init__(self, config: SlidingWindowConfig):
        self.config = config
        self._validate_config()

    def _validate_config(self):
        if self.config.sample_rate < self.config.sample_rate_min:
            raise ValueError(f"采样率 {self.config.sample_rate}Hz 低于最小值 {self.config.sample_rate_min}Hz")
        if self.config.sample_rate > self.config.sample_rate_max:
            raise ValueError(f"采样率 {self.config.sample_rate}Hz 超过最大值 {self.config.sample_rate_max}Hz")
        if self.config.overlap_ratio < 0 or self.config.overlap_ratio >= 1:
            raise ValueError(f"重叠比例 {self.config.overlap_ratio} 必须在 [0, 1) 范围内")

    def set_sample_rate(self, rate: float):
        """动态调整采样率"""
        if rate < self.config.sample_rate_min or rate > self.config.sample_rate_max:
            raise ValueError(f"采样率 {rate}Hz 超出范围 [{self.config.sample_rate_min}, {self.config.sample_rate_max}]")
        self.config.sample_rate = rate

    def resample(
        self,
        data: np.ndarray,
        target_rate: float | None = None,
    ) -> np.ndarray:
        """
        重采样数据到目标采样率

        Args:
            data: 输入数据 (n_samples, n_channels)
            target_rate: 目标采样率，默认使用配置的采样率

        Returns:
            重采样后的数据
        """
        if target_rate is None:
            target_rate = self.config.sample_rate

        if target_rate == self.config.sample_rate:
            return data

        try:
            from scipy.signal import resample

            ratio = target_rate / self.config.sample_rate
            n_samples = int(data.shape[0] * ratio)
            return resample(data, n_samples, axis=0)
        except ImportError:
            logger.warning("scipy不可用，无法重采样")
            return data

    def apply(
        self,
        data: np.ndarray,
        window_size: int | None = None,
    ) -> np.ndarray:
        """
        应用滑动窗口分割

        Args:
            data: 输入数据 (n_samples, n_channels) 或 (n_samples,)
            window_size: 窗口大小，默认使用配置值

        Returns:
            窗口分割后的数据 (n_windows, window_size, n_channels)
        """
        if window_size is None:
            window_size = self.config.window_size

        if data.ndim == 1:
            data = data.reshape(-1, 1)

        n_samples, n_channels = data.shape

        if n_samples < self.config.min_samples:
            raise ValueError(f"数据长度 {n_samples} 小于最小样本数 {self.config.min_samples}")

        if n_samples < window_size:
            pad_size = int(window_size - n_samples)
            data = np.pad(
                data,
                ((0, pad_size), (0, 0)),
                mode=str(self.config.pad_mode),  # type: ignore[call-overload]
            )
            n_samples = data.shape[0]

        step = self.config.step_size
        n_windows = max(1, (n_samples - window_size) // step + 1)

        windows = np.zeros(
            (n_windows, window_size, n_channels),
            dtype=data.dtype,
        )

        for i in range(n_windows):
            start = i * step
            windows[i] = data[start : start + window_size]

        logger.debug(
            "滑动窗口: %d样本 -> %d窗口 (size=%d, step=%d, overlap=%.0f%%)",
            n_samples,
            n_windows,
            window_size,
            step,
            self.config.overlap_ratio * 100,
        )

        return windows

    def get_window_indices(
        self,
        n_samples: int,
        window_size: int | None = None,
    ) -> list[tuple[int, int]]:
        """
        获取窗口索引范围，不实际分割数据

        Args:
            n_samples: 总样本数
            window_size: 窗口大小

        Returns:
            [(start, end), ...] 索引范围列表
        """
        if window_size is None:
            window_size = self.config.window_size

        step = self.config.step_size
        indices = []

        for i in range(0, n_samples - window_size + 1, step):
            indices.append((i, i + window_size))

        if not indices and n_samples > 0:
            indices.append((0, min(window_size, n_samples)))

        return indices

    def get_config_summary(self) -> dict[str, Any]:
        """获取配置摘要"""
        return {
            "window_size": self.config.window_size,
            "overlap_ratio": self.config.overlap_ratio,
            "step_size": self.config.step_size,
            "sample_rate": self.config.sample_rate,
            "sample_rate_range": (
                self.config.sample_rate_min,
                self.config.sample_rate_max,
            ),
        }
