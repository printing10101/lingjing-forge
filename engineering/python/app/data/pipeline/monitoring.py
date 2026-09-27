"""
管道监控模块

提供数据质量指标监控与异常报警机制，支持性能指标采集和日志记录。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

import numpy as np

from app.data.pipeline.config import MonitoringConfig

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标"""

    latency_ms: float = 0.0
    throughput_per_sec: float = 0.0
    memory_usage_mb: float = 0.0
    cpu_usage_pct: float = 0.0
    batch_count: int = 0
    error_count: int = 0
    total_processed: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "latency_ms": self.latency_ms,
            "throughput_per_sec": self.throughput_per_sec,
            "memory_usage_mb": self.memory_usage_mb,
            "cpu_usage_pct": self.cpu_usage_pct,
            "batch_count": self.batch_count,
            "error_count": self.error_count,
            "total_processed": self.total_processed,
            "timestamp": self.timestamp,
        }


class PipelineMonitor:
    """
    管道监控器

    实时监控管道性能指标，支持异常报警和指标导出。
    """

    def __init__(self, config: MonitoringConfig):
        self.config = config
        self._metrics_history: deque = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._error_count = 0
        self._processed_count = 0
        self._alert_callbacks: list[Callable] = []

    def register_alert_callback(self, callback: Callable[[str, dict[str, Any]], None]):
        """注册报警回调"""
        self._alert_callbacks.append(callback)

    def record_processing(
        self,
        latency_ms: float,
        data_type: str,
        success: bool = True,
    ):
        """记录处理事件"""
        with self._lock:
            if not success:
                self._error_count += 1
            self._processed_count += 1

            elapsed = time.time() - self._start_time
            throughput = self._processed_count / max(elapsed, 0.001)

            memory_mb = self._get_memory_usage()

            metrics = PerformanceMetrics(
                latency_ms=latency_ms,
                throughput_per_sec=throughput,
                memory_usage_mb=memory_mb,
                batch_count=1,
                error_count=1 if not success else 0,
                total_processed=self._processed_count,
            )

            self._metrics_history.append(metrics)

            self._check_alerts(metrics, data_type)

    def _get_memory_usage(self) -> float:
        try:
            import psutil

            process = psutil.Process()
            return process.memory_info().rss / (1024 * 1024)
        except ImportError:
            logger.debug("psutil 未安装，无法获取内存使用信息")
            return 0.0

    def _check_alerts(self, metrics: PerformanceMetrics, data_type: str):
        if metrics.latency_ms > self.config.alert_threshold_latency_ms:
            self._trigger_alert(
                "latency_high",
                {
                    "latency_ms": metrics.latency_ms,
                    "threshold": self.config.alert_threshold_latency_ms,
                    "data_type": data_type,
                },
            )

        if metrics.memory_usage_mb > 0:
            try:
                import psutil

                mem_pct = psutil.Process().memory_percent()
                if mem_pct > self.config.alert_threshold_memory_pct:
                    self._trigger_alert(
                        "memory_high",
                        {
                            "memory_pct": mem_pct,
                            "threshold": self.config.alert_threshold_memory_pct,
                            "data_type": data_type,
                        },
                    )
            except ImportError as imp_err:
                # psutil 不可用时跳过内存监控，其他维度监控继续
                logger.debug(
                    "psutil unavailable, skipping memory monitoring: %s",
                    imp_err,
                    exc_info=True,
                )

    def _trigger_alert(self, alert_type: str, details: dict[str, Any]):
        logger.warning("[管道监控] 报警: %s - %s", alert_type, details)
        for callback in self._alert_callbacks:
            try:
                callback(alert_type, details)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as e:
                logger.error("报警回调执行失败: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            if not self._metrics_history:
                return {
                    "total_processed": self._processed_count,
                    "total_errors": self._error_count,
                    "uptime_seconds": time.time() - self._start_time,
                }

            latencies = [m.latency_ms for m in self._metrics_history]
            throughputs = [m.throughput_per_sec for m in self._metrics_history]

            latencies_arr = np.array(latencies)
            throughputs_arr = np.array(throughputs)

            return {
                "total_processed": self._processed_count,
                "total_errors": self._error_count,
                "error_rate": self._error_count / max(self._processed_count, 1),
                "uptime_seconds": time.time() - self._start_time,
                "latency_mean_ms": float(np.mean(latencies_arr)),
                "latency_p50_ms": float(np.percentile(latencies_arr, 50)),
                "latency_p95_ms": float(np.percentile(latencies_arr, 95)),
                "latency_p99_ms": float(np.percentile(latencies_arr, 99)),
                "latency_max_ms": float(np.max(latencies_arr)),
                "throughput_mean": float(np.mean(throughputs_arr)),
                "memory_mb": self._get_memory_usage(),
            }

    def export_metrics(self) -> dict[str, Any]:
        """导出所有指标"""
        return {
            "config": {
                "alert_threshold_latency_ms": self.config.alert_threshold_latency_ms,
                "alert_threshold_memory_pct": self.config.alert_threshold_memory_pct,
            },
            "stats": self.get_stats(),
            "recent_metrics": [m.to_dict() for m in list(self._metrics_history)[-20:]],
        }

    def reset(self):
        """重置统计"""
        with self._lock:
            self._metrics_history.clear()
            self._start_time = time.time()
            self._error_count = 0
            self._processed_count = 0
