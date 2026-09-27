"""关键帧选择器 KeyframeSelector（StreamingPredictor 拆分子模块）。"""

from __future__ import annotations

import logging
import threading


import numpy as np

from .config import KeyframeDecision

logger = logging.getLogger(__name__)


class KeyframeSelector:
    """关键帧选择器（对应 lingbot-map 的 keyframe_interval 策略）.

    支持三种判定模式：
    - ``interval``：固定间隔（每 N 帧一关键帧）
    - ``energy``：信号能量突变触发（颤振前兆检测）
    - ``hybrid``：间隔 + 能量联合判定（默认）

    能量模式特别适合颤振预测场景：颤振发生前通常伴随信号能量突增，
    将这些帧作为关键帧写入长期记忆，能显著提升 LTC 对颤振前兆的捕获能力。
    """

    def __init__(
        self,
        interval: int = 1,
        mode: str = "hybrid",
        energy_threshold: float = 1.5,
        seed: int = 42,
    ) -> None:
        self._interval = max(1, interval)
        self._mode = mode
        self._energy_threshold = energy_threshold
        self._frame_counter = 0
        self._baseline_energy: float | None = None
        self._baseline_ema_alpha = 0.95
        self._lock = threading.Lock()
        # 固定种子保证关键帧判定可复现（学术诚信）
        self._rng = np.random.default_rng(seed)

    def decide(
        self,
        features: np.ndarray,
        force_keyframe: bool = False,
    ) -> KeyframeDecision:
        """判定当前帧是否为关键帧。

        Parameters
        ----------
        features : np.ndarray
            当前帧特征向量（1D 或 2D）。
        force_keyframe : bool
            强制当前帧为关键帧（用于窗口边界、外部触发等场景）。

        Returns
        -------
        KeyframeDecision
            判定结果与能量信息。

        Notes
        -----
        interval 模式下，首帧（counter==1）强制为关键帧以建立基线，
        之后每隔 ``interval`` 帧触发一次。这与 energy/hybrid 模式首帧
        必为关键帧的行为保持一致，避免 interval 模式下首帧隐状态缺失。
        """
        with self._lock:
            self._frame_counter += 1
            energy = self._compute_energy(features)

            if force_keyframe:
                is_kf = True
                reason = "forced"
            elif self._mode == "interval":
                # (counter - 1) % interval == 0：首帧必为关键帧
                is_kf = (self._frame_counter - 1) % self._interval == 0
                reason = "interval"
            elif self._mode == "energy":
                is_kf, reason = self._energy_decision(energy)
            else:  # hybrid
                interval_hit = (self._frame_counter - 1) % self._interval == 0
                energy_hit, energy_reason = self._energy_decision(energy)
                if interval_hit and energy_hit:
                    is_kf = True
                    reason = "hybrid_both"
                elif interval_hit:
                    is_kf = True
                    reason = "interval"
                elif energy_hit:
                    is_kf = True
                    reason = energy_reason
                else:
                    is_kf = False
                    reason = "skip"

            self._update_baseline(energy)
            return KeyframeDecision(is_keyframe=is_kf, reason=reason, energy=energy)

    def reset(self) -> None:
        """重置状态（新窗口/新加工工序开始时调用）。"""
        with self._lock:
            self._frame_counter = 0
            self._baseline_energy = None

    def _compute_energy(self, features: np.ndarray) -> float:
        """计算信号能量（L2 范数平方的均值）。"""
        try:
            arr = np.asarray(features, dtype=np.float64)
            if arr.size == 0:
                return 0.0
            return float(np.mean(arr**2))
        except (ValueError, TypeError) as exc:
            logger.debug("能量计算失败: %s", exc)
            return 0.0

    def _energy_decision(self, energy: float) -> tuple[bool, str]:
        """能量突变判定。首帧强制为关键帧以建立基线。"""
        if self._baseline_energy is None:
            return True, "energy_init"
        if self._baseline_energy <= 0:
            return energy > 0, "energy_zero_baseline"
        ratio = energy / self._baseline_energy
        if ratio >= self._energy_threshold:
            return True, "energy_spike"
        return False, "energy_stable"

    def _update_baseline(self, energy: float) -> None:
        """EMA 更新能量基线。慢速更新避免短暂颤振污染基线。"""
        if self._baseline_energy is None:
            self._baseline_energy = energy
        else:
            self._baseline_energy = (
                self._baseline_ema_alpha * self._baseline_energy + (1 - self._baseline_ema_alpha) * energy
            )
