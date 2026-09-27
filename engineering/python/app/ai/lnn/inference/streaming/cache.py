"""分页隐状态缓存 PagedHiddenStateCache（StreamingPredictor 拆分子模块）。"""

from __future__ import annotations

import logging
import threading
from typing import Any

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from .config import HiddenStatePage

logger = logging.getLogger(__name__)


class PagedHiddenStateCache:
    """分页隐状态缓存（对应 lingbot-map 的 paged KV cache）.

    管理关键帧隐状态的分页存储，支持 GPU/CPU 分级存储与 LRU 淘汰。
    所有读写操作在锁保护下执行，防止并发推理导致的状态竞争。

    设计要点
    --------
    - ``max_pages`` 限制常驻页数，超限后淘汰最久未访问的页
    - ``device`` 控制页的存储设备，支持 ``cuda``/``cpu``
    - 关键帧写入时若页满则触发 LRU 淘汰
    """

    def __init__(
        self,
        max_pages: int = 320,
        device: str = "auto",
        predictor_device: Any | None = None,
    ) -> None:
        self._max_pages = max_pages
        self._device = device
        self._predictor_device = predictor_device
        self._pages: dict[int, HiddenStatePage] = {}
        self._lock = threading.RLock()
        self._eviction_count = 0

    def _resolve_device(self) -> str:
        if self._device != "auto":
            return self._device
        if self._predictor_device is None:
            return "cpu"
        if HAS_TORCH and isinstance(self._predictor_device, torch.device):
            return self._predictor_device.type
        return str(self._predictor_device)

    def put(self, frame_id: int, hidden: Any) -> None:
        """写入一帧隐状态。页满时 LRU 淘汰。"""
        with self._lock:
            device = self._resolve_device()
            # 如果需要迁移设备
            hidden_stored = self._maybe_to_device(hidden, device)
            if len(self._pages) >= self._max_pages and frame_id not in self._pages:
                self._evict_lru()
            self._pages[frame_id] = HiddenStatePage(
                frame_id=frame_id,
                hidden=hidden_stored,
                device=device,
            )

    def get(self, frame_id: int) -> Any | None:
        """读取一帧隐状态。不存在返回 None。"""
        with self._lock:
            page = self._pages.get(frame_id)
            if page is None:
                return None
            page.touch()
            return page.hidden

    def latest_frame_id(self) -> int | None:
        """获取最新关键帧 ID（用于窗口 overlap 传递）。"""
        with self._lock:
            if not self._pages:
                return None
            return max(self._pages.keys())

    def recent_frames(self, n: int) -> list[int]:
        """获取最近 n 个关键帧 ID（按 frame_id 升序）。"""
        with self._lock:
            ids = sorted(self._pages.keys())
            return ids[-n:] if n > 0 else []

    def clear(self) -> None:
        with self._lock:
            self._pages.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "page_count": len(self._pages),
                "max_pages": self._max_pages,
                "eviction_count": self._eviction_count,
                "device": self._resolve_device(),
            }

    def _evict_lru(self) -> None:
        if not self._pages:
            return
        # 选择 timestamp 最早的页（LRU）
        oldest_fid = min(self._pages, key=lambda k: self._pages[k].timestamp)
        evicted = self._pages.pop(oldest_fid)
        self._eviction_count += 1
        logger.debug(
            "PagedHiddenStateCache: LRU 淘汰帧 %d（访问 %d 次）",
            oldest_fid,
            evicted.access_count,
        )

    def _maybe_to_device(self, hidden: Any, device: str) -> Any:
        """将隐状态迁移到指定设备。torch 不可用时原样返回。"""
        if not HAS_TORCH or not isinstance(hidden, torch.Tensor):
            return hidden
        try:
            target = torch.device(device) if device != "cpu" else torch.device("cpu")
            if hidden.device != target:
                return hidden.detach().to(target)
        except (RuntimeError, ValueError) as exc:
            logger.debug("隐状态设备迁移失败，保持原设备: %s", exc)
        return hidden
