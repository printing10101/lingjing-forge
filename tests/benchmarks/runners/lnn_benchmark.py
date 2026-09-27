"""LNN 推理延迟基准测试。

精确测量 LNN 模型推理过程的响应时间，包括平均延迟、
95%分位延迟及最大延迟等关键指标，以及批量推理吞吐量。
"""

from __future__ import annotations

import sys
import os
from typing import Any

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_THIS_DIR, "..", "..", "..", "python")
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tests.benchmarks.config.settings import BenchmarkSettings  # noqa: E402
from tests.benchmarks.runners.base_runner import BaseBenchmarkRunner  # noqa: E402


class LNNInferenceBenchmark(BaseBenchmarkRunner):
    @property
    def benchmark_type(self) -> str:
        return "lnn_inference"

    def __init__(self, settings: BenchmarkSettings | None = None) -> None:
        self.settings = settings or BenchmarkSettings.from_env()
        self.model = None
        self.input_data = None

    def setup(self) -> None:
        try:
            from app.ai.lnn.models.torch_base_lnn import BaseTorchModel, LNNConfig
            import torch

            config = LNNConfig(
                input_size=10,
                hidden_size=64,
                output_size=4,
                num_layers=2,
            )
            self.model = BaseTorchModel(config)
            self.model.eval()
            if torch.cuda.is_available():
                self.model = self.model.cuda()
            self.input_data = torch.randn(1, 10)
            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.input_data = self.input_data.to(self.device)
        except ImportError:
            self.model = None
            self.input_data = None

    def _do_inference(self) -> None:
        if self.model is not None and self.input_data is not None:
            import torch
            with torch.no_grad():
                self.model(self.input_data)

    def _do_batch_inference(self, batch_size: int) -> None:
        if self.model is not None:
            import torch
            batch_input = torch.randn(batch_size, 10).to(self.device)
            with torch.no_grad():
                self.model(batch_input)

    def run(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        if self.model is None:
            results["single_inference"] = {
                "p50_ms": {"value": 0, "unit": "ms", "status": "SKIP"},
                "p95_ms": {"value": 0, "unit": "ms", "status": "SKIP"},
                "p99_ms": {"value": 0, "unit": "ms", "status": "SKIP"},
                "mean_ms": {"value": 0, "unit": "ms", "status": "SKIP"},
                "max_ms": {"value": 0, "unit": "ms", "status": "SKIP"},
            }
            return results

        print("  [LNN] 单次推理延迟测试...")
        latencies = self.measure_multiple(
            self._do_inference,
            iterations=self.settings.lnn_benchmark_rounds,
            warmup=self.settings.lnn_warmup_rounds,
        )
        stats = self.compute_stats(latencies)
        results["single_inference"] = {
            "p50_ms": {"value": round(stats["p50"], 3), "unit": "ms"},
            "p95_ms": {"value": round(stats["p95"], 3), "unit": "ms"},
            "p99_ms": {"value": round(stats["p99"], 3), "unit": "ms"},
            "mean_ms": {"value": round(stats["mean"], 3), "unit": "ms"},
            "max_ms": {"value": round(stats["max"], 3), "unit": "ms"},
            "min_ms": {"value": round(stats["min"], 3), "unit": "ms"},
            "std_ms": {"value": round(stats["std"], 3), "unit": "ms"},
        }
        print(f"    p50={stats['p50']:.3f}ms, p95={stats['p95']:.3f}ms, "
              f"p99={stats['p99']:.3f}ms, mean={stats['mean']:.3f}ms")

        for batch_size in self.settings.lnn_batch_sizes:
            if batch_size == 1:
                continue
            print(f"  [LNN] 批量推理测试 (batch={batch_size})...")
            batch_latencies = self.measure_multiple(
                lambda: self._do_batch_inference(batch_size),
                iterations=max(5, self.settings.lnn_benchmark_rounds // 2),
                warmup=self.settings.lnn_warmup_rounds,
            )
            avg_latency = sum(batch_latencies) / len(batch_latencies)
            throughput = (batch_size / avg_latency) * 1000 if avg_latency > 0 else 0
            results[f"batch_{batch_size}"] = {
                "avg_latency_ms": {"value": round(avg_latency, 3), "unit": "ms"},
                "throughput_sps": {"value": round(throughput, 1), "unit": "samples/s"},
            }
            print(f"    avg_latency={avg_latency:.3f}ms, throughput={throughput:.1f} samples/s")

        return results

    def teardown(self) -> None:
        self.model = None
        self.input_data = None
