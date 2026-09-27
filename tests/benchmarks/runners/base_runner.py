"""基准测试运行器基类。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class BaseBenchmarkRunner(ABC):
    @property
    @abstractmethod
    def benchmark_type(self) -> str:
        ...

    @abstractmethod
    def setup(self) -> None:
        ...

    @abstractmethod
    def run(self) -> dict[str, dict[str, Any]]:
        ...

    def teardown(self) -> None:
        pass

    def measure_time(self, func, *args, **kwargs) -> float:
        start = time.perf_counter()
        func(*args, **kwargs)
        return (time.perf_counter() - start) * 1000

    def measure_multiple(
        self,
        func,
        iterations: int,
        warmup: int = 3,
        *args,
        **kwargs,
    ) -> list[float]:
        for _ in range(warmup):
            func(*args, **kwargs)

        latencies = []
        for _ in range(iterations):
            start = time.perf_counter()
            func(*args, **kwargs)
            latencies.append((time.perf_counter() - start) * 1000)

        return latencies

    def compute_stats(self, latencies: list[float]) -> dict[str, float]:
        sorted_lat = sorted(latencies)
        n = len(sorted_lat)
        return {
            "min": sorted_lat[0] if n > 0 else 0,
            "max": sorted_lat[-1] if n > 0 else 0,
            "mean": sum(sorted_lat) / n if n > 0 else 0,
            "median": sorted_lat[n // 2] if n > 0 else 0,
            "p50": sorted_lat[int(n * 0.50)] if n > 0 else 0,
            "p95": sorted_lat[int(n * 0.95)] if n > 0 else 0,
            "p99": sorted_lat[int(n * 0.99)] if n > 0 else 0,
            "std": (
                (sum((x - sum(sorted_lat) / n) ** 2 for x in sorted_lat) / n) ** 0.5
                if n > 1
                else 0
            ),
        }
