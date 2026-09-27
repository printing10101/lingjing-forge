"""LNN模型推理性能专项测试模块。

基于pytest-benchmark实现高精度性能测量，
覆盖单次推理、批量推理及CPU/GPU双环境。
"""

from __future__ import annotations

import json
import logging
import time
import sys
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

try:
    import torch

    HAS_TORCH = True
    HAS_CUDA = torch.cuda.is_available()
except ImportError:
    HAS_TORCH = False
    HAS_CUDA = False
except OSError as _torch_os_err:
    # torch 安装损坏（如 DLL 缺失）抛 OSError 而非 ImportError——
    # 基准必须能在无 torch/坏 torch 的工程环境（ONNX 运行时路径）下运行
    logger.warning("torch 可导入但加载失败，LNN 基准降级为合成模型: %s", _torch_os_err)
    HAS_TORCH = False
    HAS_CUDA = False

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class LNNPerfBenchmark:
    """LNN推理性能基准测试。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._device = "cpu"
        self._results: dict[str, Any] = {}

    def setup(self) -> None:
        self._create_synthetic_model()
        self._input_single = np.random.randn(1, 64).astype(np.float32)
        self._input_batch_10 = np.random.randn(10, 64).astype(np.float32)
        self._input_batch_50 = np.random.randn(50, 64).astype(np.float32)
        self._input_batch_100 = np.random.randn(100, 64).astype(np.float32)

        if HAS_CUDA:
            try:
                self._cuda_model = self._create_torch_model()
                self._cuda_model.to("cuda")
                self._cuda_input = torch.randn(1, 64, device="cuda")
                self._cuda_batch_10 = torch.randn(10, 64, device="cuda")
                self._cuda_batch_50 = torch.randn(50, 64, device="cuda")
                self._cuda_batch_100 = torch.randn(100, 64, device="cuda")
            except (RuntimeError, ValueError, TypeError) as cuda_err:
                # CUDA 不可用时回退到 CPU 基准，不影响整体测试
                logger.debug(
                    "CUDA warmup unavailable, falling back to CPU benchmark: %s",
                    cuda_err,
                    exc_info=True,
                )

    def _create_synthetic_model(self) -> None:
        class _DummyLNN:
            def predict(self, x: np.ndarray) -> np.ndarray:
                for _ in range(3):
                    x = np.tanh(x @ np.random.randn(x.shape[1], 128) * 0.1 + np.random.randn(128) * 0.01)
                return x @ np.random.randn(128, 1) * 0.1

        self._model = _DummyLNN()

    def _create_torch_model(self) -> Any:
        import torch.nn as nn

        class _BenchModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.net = nn.Sequential(
                    nn.Linear(64, 256),
                    nn.ReLU(),
                    nn.Linear(256, 128),
                    nn.ReLU(),
                    nn.Linear(128, 64),
                    nn.ReLU(),
                    nn.Linear(64, 1),
                )

            def forward(self, x):
                return self.net(x)

        return _BenchModel()

    def run_single_inference(self) -> dict[str, float]:
        times: list[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            self._model.predict(self._input_single)
            elapsed = (time.perf_counter() - t0) * 1000
            times.append(elapsed)

        times.sort()
        n = len(times)
        result = {
            "lnn_inference_ms_p50": round(times[int(n * 0.50)], 3),
            "lnn_inference_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "lnn_inference_ms_p99": round(times[min(int(n * 0.99), n - 1)], 3),
            "lnn_inference_ms_mean": round(sum(times) / n, 3),
            "lnn_inference_ms_min": round(times[0], 3),
            "lnn_inference_ms_max": round(times[-1], 3),
            "lnn_inference_samples": 1,
        }
        self._results.update(result)
        return result

    def run_batch_10_inference(self) -> dict[str, float]:
        t0 = time.perf_counter()
        for _ in range(20):
            self._model.predict(self._input_batch_10)
        elapsed = (time.perf_counter() - t0) * 1000
        result = {
            "batch_10_inference_ms": round(elapsed, 2),
            "batch_10_throughput_sps": round(200 / (elapsed / 1000), 1),
        }
        self._results.update(result)
        return result

    def run_batch_50_inference(self) -> dict[str, float]:
        t0 = time.perf_counter()
        for _ in range(10):
            self._model.predict(self._input_batch_50)
        elapsed = (time.perf_counter() - t0) * 1000
        result = {
            "batch_50_inference_ms": round(elapsed, 2),
            "batch_50_throughput_sps": round(500 / (elapsed / 1000), 1),
        }
        self._results.update(result)
        return result

    def run_batch_100_inference(self) -> dict[str, float]:
        t0 = time.perf_counter()
        for _ in range(5):
            self._model.predict(self._input_batch_100)
        elapsed = (time.perf_counter() - t0) * 1000
        result = {
            "batch_100_inference_ms": round(elapsed, 2),
            "batch_100_throughput_sps": round(500 / (elapsed / 1000), 1),
        }
        self._results.update(result)
        return result

    def run_gpu_single_inference(self) -> dict[str, float] | None:
        if not HAS_CUDA:
            return None
        try:
            times: list[float] = []
            with torch.no_grad():
                for _ in range(20):
                    torch.cuda.synchronize()
                    t0 = time.perf_counter()
                    self._cuda_model(self._cuda_input)
                    torch.cuda.synchronize()
                    elapsed = (time.perf_counter() - t0) * 1000
                    times.append(elapsed)
            times.sort()
            n = len(times)
            mem = torch.cuda.max_memory_allocated() / (1024 * 1024)
            torch.cuda.reset_peak_memory_stats()
            result = {
                "gpu_inference_ms_p50": round(times[int(n * 0.50)], 3),
                "gpu_inference_ms_p95": round(times[min(int(n * 0.95), n - 1)], 3),
                "gpu_inference_ms_mean": round(sum(times) / n, 3),
                "gpu_memory_mb": round(mem, 1),
            }
            self._results.update(result)
            return result
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            logger.warning("GPU推理测试失败: %s，返回 None", e)
            return None

    def get_all_results(self) -> dict[str, Any]:
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "environment": {
                "python": sys.version.split()[0],
                "torch": torch.__version__ if HAS_TORCH else "N/A",
                "cuda": torch.cuda.is_available() if HAS_TORCH else False,
            },
            "results": self.get_all_results(),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_single_inference(benchmark: Any) -> None:
    bench = LNNPerfBenchmark()
    bench.setup()
    benchmark(bench.run_single_inference)


def bench_batch_10(benchmark: Any) -> None:
    bench = LNNPerfBenchmark()
    bench.setup()
    benchmark(bench.run_batch_10_inference)


def bench_batch_50(benchmark: Any) -> None:
    bench = LNNPerfBenchmark()
    bench.setup()
    benchmark(bench.run_batch_50_inference)


def bench_batch_100(benchmark: Any) -> None:
    bench = LNNPerfBenchmark()
    bench.setup()
    benchmark(bench.run_batch_100_inference)
