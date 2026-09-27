"""多维度资源追踪（从 budget 拆出）。"""

from __future__ import annotations

import logging
import threading
import time

import psutil

from app.models.budget import ResourceType

logger = logging.getLogger(__name__)


class ResourceTracker:
    """多维度资源追踪系统"""

    def __init__(self):
        self._lock = threading.RLock()
        self._gpu_memory_mb = 0.0
        self._gpu_hours_today = 0.0
        self._gpu_time_today = 0.0  # GPU 计算时间（秒）
        self._inference_count_today = 0
        self._memory_peak_mb = 0.0
        self._api_calls_today = 0
        self._data_transfer_today = 0.0  # 数据传输量（MB）
        self._last_reset = time.time()
        self._update_current_metrics()

    def _update_current_metrics(self) -> None:
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            with self._lock:
                self._memory_peak_mb = max(self._memory_peak_mb, mem_info.rss / (1024 * 1024))
        except (RuntimeError, OSError, AttributeError):
            logger.warning("Failed to update memory metrics", exc_info=True)

    def get_gpu_memory_available(self) -> float:
        try:
            import torch

            if torch.cuda.is_available():
                total = torch.cuda.get_device_properties(0).total_memory / (1024**2)
                allocated = torch.cuda.memory_allocated(0) / (1024**2)
                return total - allocated
            return 0.0
        except ImportError:
            logger.debug("PyTorch 未安装，无法获取 GPU 可用内存信息")
            return 0.0

    def get_gpu_memory_total(self) -> float:
        try:
            import torch

            if torch.cuda.is_available():
                return torch.cuda.get_device_properties(0).total_memory / (1024**2)
            return 0.0
        except ImportError:
            logger.debug("PyTorch 未安装，无法获取 GPU 总内存信息")
            return 0.0

    def increment_inference_count(self) -> None:
        with self._lock:
            self._inference_count_today += 1

    def increment_gpu_hours(self, hours: float) -> None:
        with self._lock:
            self._gpu_hours_today += hours

    def increment_gpu_time(self, seconds: float) -> None:
        """累加 GPU 计算时间（秒）。"""
        with self._lock:
            self._gpu_time_today += seconds

    def increment_data_transfer(self, megabytes: float) -> None:
        """累加数据传输量（MB）。"""
        with self._lock:
            self._data_transfer_today += megabytes

    def increment_api_calls(self) -> None:
        with self._lock:
            self._api_calls_today += 1

    def get_current_usage(self, resource_type: ResourceType) -> float:
        self._update_current_metrics()
        with self._lock:
            if resource_type == ResourceType.GPU_MEMORY:
                return self.get_gpu_memory_total() - self.get_gpu_memory_available()
            elif resource_type == ResourceType.GPU_HOURS:
                return self._gpu_hours_today
            elif resource_type == ResourceType.GPU_TIME:
                return self._gpu_time_today
            elif resource_type == ResourceType.INFERENCE_COUNT:
                return self._inference_count_today
            elif resource_type == ResourceType.MEMORY_PEAK:
                return self._memory_peak_mb
            elif resource_type == ResourceType.API_CALLS:
                return self._api_calls_today
            elif resource_type == ResourceType.DATA_TRANSFER:
                return self._data_transfer_today
            elif resource_type == ResourceType.TOTAL_COST:
                # 总成本由 BudgetManager 从数据库汇总，ResourceTracker 不持有该状态
                return 0.0
            else:
                raise ValueError(f"Unknown resource type: {resource_type}")

    def reset_daily(self) -> None:
        now = time.time()
        with self._lock:
            elapsed = now - self._last_reset

            if elapsed >= 86400:
                self._inference_count_today = 0
                self._gpu_hours_today = 0.0
                self._gpu_time_today = 0.0
                self._api_calls_today = 0
                self._data_transfer_today = 0.0
                self._last_reset = now
                logger.info("Daily resource counters reset")
