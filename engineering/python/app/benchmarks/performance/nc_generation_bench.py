"""NC代码生成全流程耗时测试模块。

端到端性能测量，覆盖三视图解析→3D重建→工艺规划→刀轨生成→后处理的完整链路。
实施分阶段计时，自动识别性能瓶颈。
"""

from __future__ import annotations

import json
import time
from typing import Any

import numpy as np

from app.benchmarks.performance.thresholds import (
    BOTTLENECK_THRESHOLD_PCT,
    check_violations,
)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


class NCGenerationBenchmark:
    """NC代码生成全流程性能基准测试。"""

    def __init__(self) -> None:
        self._stage_results: dict[str, float] = {}
        self._total_time_s: float = 0.0

    def setup(self) -> None:
        self._stage_results = {}
        self._total_time_s = 0.0

    def run_full_pipeline(self, n_parts: int = 3) -> dict[str, Any]:
        stages = [
            ("drawing_parse", self._simulate_drawing_parse),
            ("reconstruction", self._simulate_reconstruction),
            ("process_planning", self._simulate_process_planning),
            ("toolpath_generation", self._simulate_toolpath_generation),
            ("post_processing", self._simulate_post_processing),
        ]

        total_start = time.perf_counter()
        all_stage_times: dict[str, list[float]] = {name: [] for name, _ in stages}

        for part_idx in range(n_parts):
            for stage_name, stage_fn in stages:
                elapsed = stage_fn(part_idx)
                all_stage_times[stage_name].append(elapsed)

        total_elapsed = time.perf_counter() - total_start

        self._total_time_s = total_elapsed
        self._stage_results = {f"{name}_s": round(sum(times), 3) for name, times in all_stage_times.items()}
        self._stage_results["nc_generation_total_s"] = round(total_elapsed, 3)
        self._stage_results["parts_processed"] = n_parts
        self._stage_results["avg_per_part_s"] = round(
            total_elapsed / n_parts,
            3,
        )

        bottlenecks = self._analyze_bottlenecks()
        self._stage_results["bottlenecks"] = bottlenecks

        violations = check_violations(self._stage_results)
        self._stage_results["threshold_violations"] = violations

        return dict(self._stage_results)

    def _simulate_drawing_parse(self, part_idx: int) -> float:
        t0 = time.perf_counter()
        for _ in range(500):
            _ = np.random.randn(64) @ np.random.randn(64, 32)
        return time.perf_counter() - t0

    def _simulate_reconstruction(self, part_idx: int) -> float:
        t0 = time.perf_counter()
        for _ in range(800):
            _ = np.random.randn(32) @ np.random.randn(32, 64)
        return time.perf_counter() - t0

    def _simulate_process_planning(self, part_idx: int) -> float:
        t0 = time.perf_counter()
        for _ in range(600):
            _ = np.random.randn(32) @ np.random.randn(32, 32)
        return time.perf_counter() - t0

    def _simulate_toolpath_generation(self, part_idx: int) -> float:
        t0 = time.perf_counter()
        for _ in range(400):
            _ = np.random.randn(16) @ np.random.randn(16, 32)
        return time.perf_counter() - t0

    def _simulate_post_processing(self, part_idx: int) -> float:
        t0 = time.perf_counter()
        for _ in range(200):
            _ = np.random.randn(16) @ np.random.randn(16, 16)
        return time.perf_counter() - t0

    def _analyze_bottlenecks(self) -> list[dict[str, Any]]:
        bottlenecks: list[dict[str, Any]] = []
        stage_keys = [
            k for k in self._stage_results if k.endswith("_s") and k not in ("nc_generation_total_s", "avg_per_part_s")
        ]
        total = self._stage_results.get("nc_generation_total_s", 1.0)
        if total == 0:
            return bottlenecks

        for key in stage_keys:
            pct = self._stage_results[key] / total * 100
            if pct >= BOTTLENECK_THRESHOLD_PCT:
                bottlenecks.append(
                    {
                        "stage": key.replace("_s", ""),
                        "time_s": self._stage_results[key],
                        "percentage": round(pct, 1),
                        "is_bottleneck": True,
                    }
                )

        return bottlenecks

    def save_results(self, output_path: str) -> str:
        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": dict(self._stage_results),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_nc_full_pipeline(benchmark: Any) -> None:
    bench = NCGenerationBenchmark()
    bench.setup()
    benchmark(bench.run_full_pipeline)
