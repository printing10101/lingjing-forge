"""3D 渲染帧率基准测试（Python 侧封装）。

本模块提供对前端 3D 渲染帧率测试结果的处理和封装。
实际的帧率测试在 Vitest 中执行（tests/benchmarks/renderFPS.bench.ts），
本模块负责解析测试生成的 JSON 结果并整合到统一的基准报告中。
"""

from __future__ import annotations

import json
import os
from typing import Any

from tests.benchmarks.config.settings import BenchmarkSettings
from tests.benchmarks.runners.base_runner import BaseBenchmarkRunner


class RenderFPSBenchmark(BaseBenchmarkRunner):
    @property
    def benchmark_type(self) -> str:
        return "render_fps"

    def __init__(self, settings: BenchmarkSettings | None = None) -> None:
        self.settings = settings or BenchmarkSettings.from_env()
        self.results_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports",
            "render_benchmark_results.json",
        )

    def setup(self) -> None:
        os.makedirs(os.path.dirname(self.results_file), exist_ok=True)

    def run(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        if os.path.exists(self.results_file):
            try:
                with open(self.results_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for complexity, metrics in data.items():
                    results[complexity] = {}
                    for metric, value in metrics.items():
                        if isinstance(value, (int, float)):
                            unit = (
                                "fps" if "fps" in metric
                                else "ms" if "time" in metric
                                else "MB" if "memory" in metric
                                else ""
                            )
                            results[complexity][metric] = {
                                "value": value,
                                "unit": unit,
                            }
                if results:
                    return results
            except Exception:
                pass

        results["skipped"] = {
            "message": {"value": "前端渲染测试未运行。请使用 pnpm bench:render 运行前端帧率测试", "status": "SKIP"},
        }
        return results

    def teardown(self) -> None:
        pass
