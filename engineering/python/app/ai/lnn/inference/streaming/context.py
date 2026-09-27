"""锚点上下文漂移修正 AnchorContext（StreamingPredictor 拆分子模块）。"""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False


logger = logging.getLogger(__name__)


class AnchorContext:
    """锚点上下文（对应 lingbot-map 的 anchor context）.

    维护一个"稳态锚点"隐状态，用于修正 LTC 长时序推理的隐状态漂移。

    在加工场景中，锚点对应"稳态切削"或"刀具初始锋利状态"的隐状态。
    长时间推理后 LTC 隐状态会衰减/漂移，此时以 ``correction_strength``
    将当前隐状态向锚点拉回，类似 lingbot-map 用 anchor 修正全局坐标漂移。

    锚点本身通过 EMA 慢速跟踪稳态，避免被瞬时颤振污染。
    """

    def __init__(
        self,
        update_rate: float = 0.01,
        correction_strength: float = 0.1,
        enabled: bool = True,
    ) -> None:
        self._update_rate = update_rate
        self._correction_strength = correction_strength
        self._enabled = enabled
        self._anchor: Any | None = None
        self._lock = threading.RLock()
        self._update_count = 0

    def update(self, hidden: Any, is_stable: bool = True) -> None:
        """用当前隐状态更新锚点。

        Parameters
        ----------
        hidden : Any
            当前隐状态（torch.Tensor 或 np.ndarray）。
        is_stable : bool
            当前帧是否为稳态。非稳态帧（如颤振帧）不更新锚点，
            避免异常状态污染稳态基准。
        """
        if not self._enabled or not is_stable:
            return
        with self._lock:
            if self._anchor is None:
                self._anchor = self._clone(hidden)
            else:
                self._anchor = self._ema_update(self._anchor, hidden)
            self._update_count += 1

    def correct(self, hidden: Any) -> tuple[Any, float]:
        """对隐状态施加锚点漂移修正。

        Returns
        -------
        Tuple[Any, float]
            (修正后的隐状态, 漂移量)。漂移量用于可观测性。
        """
        if not self._enabled or self._anchor is None:
            return hidden, 0.0
        with self._lock:
            drift = self._compute_drift(hidden, self._anchor)
            corrected = self._apply_correction(hidden, self._anchor, self._correction_strength)
            return corrected, drift

    def reset(self) -> None:
        with self._lock:
            self._anchor = None
            self._update_count = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "initialized": self._anchor is not None,
                "update_count": self._update_count,
                "correction_strength": self._correction_strength,
            }

    def _clone(self, hidden: Any) -> Any:
        if HAS_TORCH and isinstance(hidden, torch.Tensor):
            return hidden.detach().clone()
        if isinstance(hidden, np.ndarray):
            return hidden.copy()
        return hidden

    def _ema_update(self, anchor: Any, hidden: Any) -> Any:
        rate = self._update_rate
        if HAS_TORCH and isinstance(anchor, torch.Tensor) and isinstance(hidden, torch.Tensor):
            return (1 - rate) * anchor + rate * hidden.detach()
        if isinstance(anchor, np.ndarray) and isinstance(hidden, np.ndarray):
            return (1 - rate) * anchor + rate * hidden
        return self._clone(hidden)

    def _compute_drift(self, hidden: Any, anchor: Any) -> float:
        try:
            if HAS_TORCH and isinstance(hidden, torch.Tensor) and isinstance(anchor, torch.Tensor):
                return float(torch.norm(hidden - anchor).item())
            if isinstance(hidden, np.ndarray) and isinstance(anchor, np.ndarray):
                return float(np.linalg.norm(hidden - anchor))
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.debug("漂移量计算失败: %s", exc)
        return 0.0

    def _apply_correction(self, hidden: Any, anchor: Any, strength: float) -> Any:
        """hidden_new = (1 - s) * hidden + s * anchor"""
        if HAS_TORCH and isinstance(hidden, torch.Tensor) and isinstance(anchor, torch.Tensor):
            return (1 - strength) * hidden + strength * anchor
        if isinstance(hidden, np.ndarray) and isinstance(anchor, np.ndarray):
            return (1 - strength) * hidden + strength * anchor
        return hidden
