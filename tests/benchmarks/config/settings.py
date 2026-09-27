"""基准测试全局配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BenchmarkSettings:
    history_dir: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "history",
    ))
    output_dir: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports",
    ))
    visualizer_dir: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "reports",
        "visualizations",
    ))
    db_path: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "benchmark_results.db",
    ))

    lnn_warmup_rounds: int = 3
    lnn_benchmark_rounds: int = 30
    lnn_batch_sizes: list[int] = field(default_factory=lambda: [1, 10, 50, 100])

    gcode_warmup_rounds: int = 2
    gcode_benchmark_rounds: int = 10
    gcode_complexity_levels: list[str] = field(
        default_factory=lambda: ["simple", "medium", "complex"],
    )

    render_benchmark_duration_ms: int = 5000
    render_complexity_levels: list[str] = field(
        default_factory=lambda: ["low", "medium", "high"],
    )

    ci_mode: bool = field(default_factory=lambda: os.environ.get("CI", "") == "true")
    fail_on_regression: bool = field(default_factory=lambda: os.environ.get("FAIL_ON_REGRESSION", "true") == "true")

    @classmethod
    def from_env(cls) -> BenchmarkSettings:
        return cls(
            history_dir=os.environ.get("BENCHMARK_HISTORY_DIR", cls().history_dir),
            output_dir=os.environ.get("BENCHMARK_OUTPUT_DIR", cls().output_dir),
            db_path=os.environ.get("BENCHMARK_DB_PATH", cls().db_path),
            ci_mode=os.environ.get("CI", "") == "true",
            fail_on_regression=os.environ.get("FAIL_ON_REGRESSION", "true") == "true",
        )

    def ensure_dirs(self) -> None:
        Path(self.history_dir).mkdir(parents=True, exist_ok=True)
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        Path(self.visualizer_dir).mkdir(parents=True, exist_ok=True)
