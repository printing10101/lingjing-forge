"""G代码生成性能基准测试。

评估 G代码生成的效率，包括生成速度、各阶段耗时分布、
及大型复杂模型的处理能力。
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


class GCodeGenerationBenchmark(BaseBenchmarkRunner):
    @property
    def benchmark_type(self) -> str:
        return "gcode_generation"

    def __init__(self, settings: BenchmarkSettings | None = None) -> None:
        self.settings = settings or BenchmarkSettings.from_env()
        self.generator = None

    def setup(self) -> None:
        try:
            from app.process_planning.gcode_generator import GCodeGenerator
            self.generator = GCodeGenerator(controller_type="fanuc_0i")
        except ImportError:
            self.generator = None

    def _generate_sample_operations(self, n_operations: int) -> list[dict]:
        operations = []
        for i in range(n_operations):
            operations.append({
                "type": "face_milling" if i % 3 == 0 else "contour_milling" if i % 3 == 1 else "drilling",
                "tool_id": f"T{i % 10 + 1:02d}",
                "spindle_speed": 3000 + i * 100,
                "feed_rate": 200 + i * 10,
                "depth": 0.5 + i * 0.1,
                "positions": [
                    {"x": 0, "y": 0, "z": 2},
                    {"x": 10 + i, "y": 10 + i, "z": -1},
                    {"x": 20 + i * 2, "y": 5 + i, "z": -1},
                    {"x": 30 + i * 2, "y": 15 + i, "z": 2},
                ],
            })
        return operations

    def _generate_gcode(self, n_operations: int) -> str:
        if self.generator is None:
            return ""
        operations = self._generate_sample_operations(n_operations)
        return self.generator.generate(operations)

    def _run_generation_pipeline(self, complexity: str) -> dict[str, float]:
        n_ops = {"simple": 5, "medium": 20, "complex": 50}[complexity]
        timings: dict[str, float] = {}

        start = os.times()
        t0 = start.cpu  # type: ignore

        operations = self._generate_sample_operations(n_operations=n_ops)
        t1 = os.times().cpu  # type: ignore
        timings["operation_prep_s"] = t1 - t0

        gcode = self.generator.generate(operations) if self.generator else ""
        t2 = os.times().cpu  # type: ignore
        timings["generation_s"] = t2 - t1

        if gcode:
            if hasattr(self.generator, "validate"):
                self.generator.validate(gcode)
            t3 = os.times().cpu  # type: ignore
            timings["validation_s"] = t3 - t2
        else:
            timings["validation_s"] = 0

        timings["total_s"] = timings["operation_prep_s"] + timings["generation_s"] + timings["validation_s"]
        timings["gcode_length"] = len(gcode) if gcode else 0
        timings["operation_count"] = n_ops

        return timings

    def run(self) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        if self.generator is None:
            print("  [GCODE] GCodeGenerator 不可用，跳过测试")
            return results

        for complexity in self.settings.gcode_complexity_levels:
            print(f"  [GCODE] {complexity} 复杂度 G代码生成测试...")
            timings_list = []
            for i in range(self.settings.gcode_benchmark_rounds):
                timings_list.append(self._run_generation_pipeline(complexity))

            avg_timings = {}
            for key in timings_list[0]:
                values = [t[key] for t in timings_list]
                avg_timings[key] = sum(values) / len(values)

            results[complexity] = {
                "total_s": {"value": round(avg_timings["total_s"], 3), "unit": "s"},
                "generation_s": {"value": round(avg_timings["generation_s"], 3), "unit": "s"},
                "validation_s": {"value": round(avg_timings["validation_s"], 3), "unit": "s"},
                "operation_count": {"value": avg_timings["operation_count"], "unit": "ops"},
                "gcode_length": {"value": int(avg_timings["gcode_length"]), "unit": "chars"},
            }
            print(f"    total={avg_timings['total_s']:.3f}s, "
                  f"gen={avg_timings['generation_s']:.3f}s, "
                  f"ops={int(avg_timings['operation_count'])}")

        return results

    def teardown(self) -> None:
        self.generator = None
