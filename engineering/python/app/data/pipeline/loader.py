"""
并行数据加载器模块

支持 PyTorch DataLoader 风格的并行数据加载，配置异步预取和内存缓存。
"""

from __future__ import annotations

import logging
import threading
from queue import Queue
from typing import Any
from collections.abc import Iterator

import numpy as np

from app.config.limits import DEFAULT_THREAD_JOIN_TIMEOUT_SEC
from app.data.pipeline.config import BatchConfig

logger = logging.getLogger(__name__)

# 预取线程 join 超时（秒）。C2 bug 修复：原代码引用未定义常量导致 NameError。
# 已迁移至 app.config.limits 集中管理。


class CachedDataset:
    """带缓存的通用数据集"""

    def __init__(self, max_cache_size: int = 1000):
        self._cache: dict[str, Any] = {}
        self._cache_keys: list[str] = []
        self._max_cache_size = max_cache_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            return self._cache.get(key)

    def put(self, key: str, value: Any):
        with self._lock:
            if key in self._cache:
                return
            while len(self._cache_keys) >= self._max_cache_size:
                oldest = self._cache_keys.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = value
            self._cache_keys.append(key)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._cache_keys.clear()

    def __len__(self):
        return len(self._cache_keys)


class ParallelDataLoader:
    """
    并行数据加载器

    支持异步预取、多工作进程、批处理配置。
    """

    def __init__(
        self,
        config: BatchConfig,
        use_multiprocessing: bool = False,
    ):
        self.config = config
        self.use_multiprocessing = use_multiprocessing
        self._prefetch_queue: Queue | None = None
        self._prefetch_thread: threading.Thread | None = None
        self._stop_prefetch = threading.Event()
        self._num_workers = config.num_worker_processes

    def set_num_workers(self, n: int):
        self._num_workers = n

    def get_batch_size(self, mode: str = "inference") -> int:
        """获取批大小"""
        if mode == "inference":
            return self.config.image_inference
        elif mode == "training":
            return self.config.image_training
        return self.config.image_inference

    def start_prefetch(
        self,
        data_generator: Iterator,
        prefetch_size: int = 4,
    ):
        """启动异步预取"""
        self._prefetch_queue = Queue(maxsize=prefetch_size)
        self._stop_prefetch.clear()

        def _prefetch_worker():
            try:
                for item in data_generator:
                    if self._stop_prefetch.is_set():
                        break
                    if self._prefetch_queue is not None:
                        self._prefetch_queue.put(item)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
                logger.error("预取线程错误: %s", e)
            finally:
                if self._prefetch_queue is not None:
                    self._prefetch_queue.put(None)

        self._prefetch_thread = threading.Thread(
            target=_prefetch_worker,
            daemon=True,
            name="prefetch-worker",
        )
        self._prefetch_thread.start()

    def stop_prefetch(self):
        self._stop_prefetch.set()
        if self._prefetch_thread is not None:
            self._prefetch_thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT_SEC)

    def iter_batches(
        self,
        data: np.ndarray,
        batch_size: int | None = None,
        mode: str = "inference",
    ) -> Iterator[np.ndarray]:
        """迭代批数据"""
        if batch_size is None:
            batch_size = self.get_batch_size(mode)

        n = len(data)
        for i in range(0, n, batch_size):
            yield data[i : i + batch_size]

    def collate_images(self, images: list[np.ndarray]) -> np.ndarray:
        """批处理 - 图像数据"""
        batch_size = len(images)
        if images[0].ndim == 3:
            h, w, c = images[0].shape
            batch = np.zeros((batch_size, h, w, c), dtype=np.float32)
        else:
            dim = images[0].shape[0]
            batch = np.zeros((batch_size, dim), dtype=np.float32)
        for i, img in enumerate(images):
            batch[i] = img
        return batch

    def collate_time_series(self, sequences: list[np.ndarray]) -> np.ndarray:
        """批处理 - 时序数据"""
        batch_size = len(sequences)
        if sequences[0].ndim == 2:
            seq_len, n_channels = sequences[0].shape
            batch = np.zeros((batch_size, seq_len, n_channels), dtype=np.float32)
        else:
            seq_len = sequences[0].shape[0]
            batch = np.zeros((batch_size, seq_len), dtype=np.float32)
        for i, seq in enumerate(sequences):
            batch[i] = seq
        return batch

    def get_config_summary(self) -> dict[str, Any]:
        return {
            "image_inference_batch": self.config.image_inference,
            "image_training_batch": self.config.image_training,
            "time_series_inference_batch": self.config.time_series_inference,
            "time_series_training_batch": self.config.time_series_training,
            "num_workers": self._num_workers,
            "prefetch_factor": self.config.prefetch_factor,
            "pin_memory": self.config.pin_memory,
        }
