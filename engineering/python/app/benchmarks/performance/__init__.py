"""自动化性能基准测试与回归检测框架。

提供LNN推理、NC代码生成、三视图解析、世界模型、RL agent、闭环工作流等关键流程的性能基准。
"""

from __future__ import annotations

from app.benchmarks.performance.run_perf_benchmark import (
    PerformanceBenchmarkRunner,
    RegressionEntry,
    RegressionReport,
    check_regression,
)
from app.benchmarks.performance.thresholds import (
    PERFORMANCE_THRESHOLDS,
    REGRESSION_THRESHOLDS,
    check_violations,
    is_within_threshold,
)
from app.benchmarks.performance.world_model_bench import WorldModelPerfBenchmark
from app.benchmarks.performance.rl_agent_bench import RLAgentPerfBenchmark
from app.benchmarks.performance.closed_loop_bench import ClosedLoopPerfBenchmark

__all__ = [
    "PERFORMANCE_THRESHOLDS",
    "REGRESSION_THRESHOLDS",
    "check_violations",
    "is_within_threshold",
    "check_regression",
    "PerformanceBenchmarkRunner",
    "RegressionEntry",
    "RegressionReport",
    "WorldModelPerfBenchmark",
    "RLAgentPerfBenchmark",
    "ClosedLoopPerfBenchmark",
]
