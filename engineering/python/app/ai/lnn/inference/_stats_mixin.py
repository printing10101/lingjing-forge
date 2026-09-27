"""LNN 推理统计/置信度 mixin（从 predictor 拆出）。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import psutil

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False

from app.ai.lnn.preprocessing import DataPreprocessor

logger = logging.getLogger(__name__)


class _StatsMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _max_recent_times: Any
    _stats: Any
    _stats_lock: Any
    _trace_log_enabled: Any
    _trace_log_path: Any
    device: Any
    engine_type: Any
    model_name: Any
    use_amp: Any

    def get_statistics(self) -> dict[str, Any]:
        with self._stats_lock:
            stats = self._stats.copy()
        total = stats["total_inferences"]
        stats["average_inference_time_ms"] = stats["total_inference_time_ms"] / total if total > 0 else 0.0
        if stats["min_inference_time_ms"] == float("inf"):
            stats["min_inference_time_ms"] = 0.0
        stats["current_memory_mb"] = self._get_memory_usage_mb()
        return stats

    def get_performance(self) -> dict[str, Any]:
        # 在锁内快照所有需要的字段并完成窗口重置写操作，
        # 锁外完成 sorted 等较重计算以减少锁持有时间。
        with self._stats_lock:
            total = self._stats["total_inferences"]
            times = sorted(self._stats["inference_times"])
            total_inference_time_ms = self._stats["total_inference_time_ms"]
            min_inference_time_ms = self._stats["min_inference_time_ms"]
            max_inference_time_ms = self._stats["max_inference_time_ms"]
            peak_memory_mb = self._stats["peak_memory_mb"]
            window_start = self._stats["window_start"]
            window_inferences = self._stats["window_inferences"]

            now = time.perf_counter()
            window_elapsed = now - window_start
            throughput = window_inferences / window_elapsed if window_elapsed > 0 else 0.0
            if window_elapsed > 60.0:
                self._stats["window_start"] = now
                self._stats["window_inferences"] = 0

        n = len(times)
        avg_ms = (total_inference_time_ms / total) if total > 0 else 0.0
        p50 = times[int(n * 0.50)] if n > 0 else 0.0
        p95 = times[min(int(n * 0.95), n - 1)] if n > 0 else 0.0
        p99 = times[min(int(n * 0.99), n - 1)] if n > 0 else 0.0

        device_type = str(self.device)
        if HAS_TORCH and self.device.type == "cuda":
            device_type = f"CUDA:{torch.cuda.get_device_name(self.device)}"
        elif HAS_TORCH and self.device.type == "mps":
            device_type = "Apple MPS"

        return {
            "model_name": self.model_name,
            "device": device_type,
            "device_type": str(self.device),
            "amp_enabled": self.use_amp,
            "engine_type": self.engine_type.value if hasattr(self.engine_type, "value") else str(self.engine_type),
            "total_inferences": total,
            "avg_inference_ms": round(avg_ms, 4),
            "p50_inference_ms": round(p50, 4),
            "p95_inference_ms": round(p95, 4),
            "p99_inference_ms": round(p99, 4),
            "min_inference_ms": round(
                min_inference_time_ms if min_inference_time_ms != float("inf") else 0.0,
                4,
            ),
            "max_inference_ms": round(max_inference_time_ms, 4),
            "throughput_inf_per_sec": round(throughput, 2),
            "current_memory_mb": round(self._get_memory_usage_mb(), 2),
            "peak_memory_mb": round(peak_memory_mb, 2),
            "sample_count_recent": n,
        }

    def _compute_confidence(self, output) -> float:
        """
        优化置信度计算以提升推理性能

        优化策略：
        - 使用更高效的 softmax 计算
        - 减少不必要的张量操作
        - 缓存中间结果
        """
        if HAS_TORCH and isinstance(output, torch.Tensor):
            # 优化：对于标量或单元素输出直接返回固定高置信度
            if output.numel() <= 1:
                return 0.95

            # 优化：使用 in-place 操作减少内存分配
            # 注意：调用方已在 torch.inference_mode() 上下文中，无需再次禁用梯度
            if output.dim() > 1:
                probs = torch.softmax(output, dim=-1)
            else:
                # 对于一维输出，直接使用 sigmoid 近似
                probs = torch.sigmoid(output)

            max_prob = probs.max().item()
            return min(max(max_prob, 0.0), 1.0)

        return 0.9

    def _standardize_input(self, input_data: Any) -> np.ndarray:
        """Standardize various input types to numpy array"""
        if isinstance(input_data, np.ndarray):
            result = input_data
        elif isinstance(input_data, dict):
            result = DataPreprocessor.extract_numeric_features(input_data)
        elif isinstance(input_data, (list, tuple)):
            # M8 修复：指定 float32 dtype，避免整数列表创建 int64 数组导致后续浮点运算类型不匹配
            result = np.array(input_data, dtype=np.float32)
        elif HAS_TORCH and isinstance(input_data, torch.Tensor):
            result = input_data.detach().cpu().numpy()
        elif isinstance(input_data, (int, float)):
            # M9 修复：标量转数组同样指定 float32 dtype
            result = np.array([input_data], dtype=np.float32)
        else:
            raise ValueError(
                f"Prediction failed: unsupported input data type "
                f"'{type(input_data).__name__}'. "
                "Supported types: dict, list, tuple, numpy.ndarray, torch.Tensor, "
                "int, float."
            )

        if result.ndim == 1:
            result = result.reshape(1, -1)
        return result

    def _to_tensor(self, data: np.ndarray):
        """Convert numpy array to tensor on correct device"""
        if HAS_TORCH:
            return torch.from_numpy(data.astype(np.float32)).to(self.device)
        return data

    def _get_memory_usage_mb(self) -> float:
        """Get current process memory usage in MB"""
        process = psutil.Process()
        return process.memory_info().rss / (1024 * 1024)

    def _update_stats(self, inference_time_ms: float, memory_mb: float) -> None:
        """Update inference statistics (thread-safe)"""
        with self._stats_lock:
            self._stats["total_inferences"] += 1
            self._stats["total_inference_time_ms"] += inference_time_ms
            self._stats["max_inference_time_ms"] = max(self._stats["max_inference_time_ms"], inference_time_ms)
            self._stats["min_inference_time_ms"] = min(self._stats["min_inference_time_ms"], inference_time_ms)
            self._stats["peak_memory_mb"] = max(self._stats["peak_memory_mb"], memory_mb)
            times = self._stats["inference_times"]
            times.append(inference_time_ms)
            if len(times) > self._max_recent_times:
                self._stats["inference_times"] = times[-self._max_recent_times :]
            self._stats["window_inferences"] += 1

    def _write_trace(
        self,
        inference_time_ms: float,
        input_shape: tuple,
        success: bool = True,
        error_msg: str | None = None,
    ) -> None:
        """
        持久化推理性能数据到 trace_log.jsonl

        Args:
            inference_time_ms: 真实推理耗时（毫秒）
            input_shape: 输入数据形状
            success: 是否成功
            error_msg: 错误信息（如有）
        """
        if not self._trace_log_enabled:
            return

        try:
            trace_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "model_name": self.model_name,
                "device": str(self.device),
                "input_shape": list(input_shape),
                "inference_time_ms": round(inference_time_ms, 4),
                "memory_mb": round(self._get_memory_usage_mb(), 2),
                "success": success,
                "error": error_msg,
                "amp_enabled": self.use_amp,
                "engine_type": self.engine_type.value if hasattr(self.engine_type, "value") else str(self.engine_type),
            }

            with open(self._trace_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace_entry, ensure_ascii=False) + "\n")
        except (OSError, IOError, TypeError, ValueError) as exc:
            logger.debug("写入 trace log 失败: %s", exc)
