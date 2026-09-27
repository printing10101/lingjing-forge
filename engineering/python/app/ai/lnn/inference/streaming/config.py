"""流式推理数据结构与配置（StreamingPredictor 拆分子模块）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KeyframeDecision:
    """关键帧判定结果。

    Attributes
    ----------
    is_keyframe : bool
        当前帧是否为关键帧。关键帧的隐状态会写入长期缓存。
    reason : str
        判定依据（interval / energy / anomaly / forced）。
    energy : float
        当前帧信号能量（用于异常检测关键帧判定）。
    """

    is_keyframe: bool
    reason: str
    energy: float = 0.0


@dataclass
class HiddenStatePage:
    """隐状态分页（对应 lingbot-map 的 KV cache page）.

    一页保存一个关键帧的完整隐状态。``device`` 控制常驻位置：
    GPU 页用于活跃推理，CPU 页用于冷存储，可按 LRU 淘汰。
    """

    frame_id: int
    hidden: Any  # torch.Tensor 或 np.ndarray
    device: str
    timestamp: float = field(default_factory=time.time)
    access_count: int = 0

    def touch(self) -> None:
        self.access_count += 1
        self.timestamp = time.time()


@dataclass
class StreamingConfig:
    """流式推理配置。

    Parameters
    ----------
    keyframe_interval : int
        关键帧间隔（每 N 帧强制一个关键帧），对应 lingbot-map 的
        ``--keyframe_interval``。默认 1（每帧都是关键帧，等价于朴素流式）。
    keyframe_mode : str
        关键帧判定策略：
        - ``interval``：固定间隔
        - ``energy``：信号能量突变触发
        - ``hybrid``：间隔 + 能量异常联合判定（默认）
    energy_threshold : float
        能量关键帧触发阈值（相对能量增益）。默认 1.5（能量提升 50%）。
    max_cache_pages : int
        长期隐状态缓存最大页数。超过后 LRU 淘汰。默认 320（对齐 lingbot-map
        训练上限）。
    cache_device : str
        隐状态缓存常驻设备。``auto`` 表示与 predictor 一致。
    anchor_enabled : bool
        是否启用锚点上下文漂移修正。
    anchor_update_rate : float
        锚点 EMA 更新速率。默认 0.01（慢速跟踪稳态）。
    anchor_correction_strength : float
        漂移修正强度 [0, 1]。0 表示不修正，1 表示完全拉回锚点。
    trajectory_memory_size : int
        轨迹记忆窗口大小。默认 64。
    trajectory_correction_strength : float
        轨迹一致性约束强度 [0, 1]。
    window_size : Optional[int]
        窗口化推理窗口大小。None 表示不启用窗口化。
    overlap_keyframes : int
        窗口间重叠的关键帧数量，用于隐状态传递，避免状态重置崩溃。
    """

    keyframe_interval: int = 1
    keyframe_mode: str = "hybrid"
    energy_threshold: float = 1.5
    max_cache_pages: int = 320
    cache_device: str = "auto"
    anchor_enabled: bool = True
    anchor_update_rate: float = 0.01
    anchor_correction_strength: float = 0.1
    trajectory_memory_size: int = 64
    trajectory_correction_strength: float = 0.05
    window_size: int | None = None
    overlap_keyframes: int = 8

    def validate(self) -> None:
        """参数校验，避免运行时崩溃。"""
        if self.keyframe_interval < 1:
            raise ValueError("keyframe_interval 必须 >= 1")
        if self.keyframe_mode not in ("interval", "energy", "hybrid"):
            raise ValueError(f"未知 keyframe_mode: {self.keyframe_mode}")
        if not 0.0 <= self.anchor_correction_strength <= 1.0:
            raise ValueError("anchor_correction_strength 必须在 [0, 1]")
        if not 0.0 <= self.trajectory_correction_strength <= 1.0:
            raise ValueError("trajectory_correction_strength 必须在 [0, 1]")
        if self.max_cache_pages < 1:
            raise ValueError("max_cache_pages 必须 >= 1")
        if self.window_size is not None and self.window_size <= 0:
            raise ValueError("window_size 必须为正或 None")
        if self.overlap_keyframes < 0:
            raise ValueError("overlap_keyframes 必须 >= 0")
