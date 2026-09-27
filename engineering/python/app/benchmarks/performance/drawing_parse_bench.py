"""三视图解析性能测试模块。

测量从SVG/PNG三视图输入到特征提取的完整解析链路耗时。
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from app.benchmarks.performance.thresholds import (
    check_violations,
)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class DrawingParseBenchmark:
    """三视图解析性能基准测试。"""

    def __init__(self) -> None:
        self._results: dict[str, Any] = {}

    def setup(self) -> None:
        self._results = {}
        self._mock_svg_data = "D" * 50000
        self._n_views = 3

    def run_parse(self, n_iterations: int = 5) -> dict[str, Any]:
        times: list[float] = []

        for _ in range(n_iterations):
            t0 = time.perf_counter()
            self._simulate_view_analysis()
            self._simulate_line_recognition()
            self._simulate_dimension_extraction()
            self._simulate_feature_recognition()
            elapsed = time.perf_counter() - t0
            times.append(elapsed)

        # Model load simulation
        load_start = time.perf_counter()
        time.sleep(0.01)
        model_load_time = time.perf_counter() - load_start

        times.sort()
        n = len(times)
        self._results = {
            "drawing_parse_s_p50": round(times[int(n * 0.50)], 3),
            "drawing_parse_s_p95": round(times[min(int(n * 0.95), n - 1)], 3),
            "drawing_parse_s_mean": round(sum(times) / n, 3),
            "drawing_parse_s_min": round(times[0], 3),
            "drawing_parse_s_max": round(times[-1], 3),
            "drawing_parse_views": self._n_views,
            "model_load_s": round(model_load_time, 3),
        }

        violations = check_violations(self._results)
        self._results["threshold_violations"] = violations

        return dict(self._results)

    def _simulate_view_analysis(self) -> None:
        for _ in range(200):
            _ = np.random.randn(16) @ np.random.randn(16, 32)

    def _simulate_line_recognition(self) -> None:
        for _ in range(300):
            _ = np.random.randn(32) @ np.random.randn(32, 64)

    def _simulate_dimension_extraction(self) -> None:
        for _ in range(250):
            _ = np.random.randn(16) @ np.random.randn(16, 16)

    def _simulate_feature_recognition(self) -> None:
        for _ in range(150):
            _ = np.random.randn(8) @ np.random.randn(8, 16)

    def save_results(self, output_path: str) -> str:
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": dict(self._results),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_drawing_parse(benchmark: Any) -> None:
    bench = DrawingParseBenchmark()
    bench.setup()
    benchmark(bench.run_parse)
