"""世界模型轨迹预测性能基准测试模块.

对应 ADR-017 阶段 8。覆盖 ``WorldModelPlugin`` + ``TrajectoryPredictor`` 的
单次预测、horizon 扩展性、批量预测吞吐与端到端插件执行开销。

基准场景
--------
1. **单次预测延迟**（horizon=10）：50 次重复，p50/p95/p99/mean/min/max
2. **horizon 扩展性**：horizon ∈ {5, 10, 20, 50} 的预测耗时
3. **批量预测吞吐**：10/50/100 个不同 candidate_action 的批量预测
4. **端到端插件执行**：通过 ``WorldModelPlugin.execute`` 完整路径
   （含 artifact 解析 + 模型缓存查找），对比纯推理开销

工程现实约束
------------
- v1 仅离线 RL，预测供训练使用，但延迟仍需控制在可接受范围
- 单次预测阈值 100ms（CNC 控制周期内），horizon=50 阈值 500ms
- 不依赖 torch：允许 NumPy 回退模式（CPU-only 生产环境）
- 模型权重使用随机初始化（基准测试关注推理框架开销，不关注模型精度）
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any

import numpy as np

from app.benchmarks.performance.thresholds import check_violations
from app.contracts.task import Artifact, TaskContext, TaskResult, TaskStatus

logger = logging.getLogger(__name__)

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


def _percentiles(times: list[float]) -> dict[str, float]:
    """计算延迟分位数统计."""
    if not times:
        return {}
    times_sorted = sorted(times)
    n = len(times_sorted)
    return {
        "p50": round(times_sorted[int(n * 0.50)], 3),
        "p95": round(times_sorted[min(int(n * 0.95), n - 1)], 3),
        "p99": round(times_sorted[min(int(n * 0.99), n - 1)], 3),
        "mean": round(sum(times_sorted) / n, 3),
        "min": round(times_sorted[0], 3),
        "max": round(times_sorted[-1], 3),
    }


class WorldModelPerfBenchmark:
    """世界模型轨迹预测性能基准测试."""

    def __init__(self) -> None:
        self._predictor: Any = None
        self._plugin: Any = None
        self._results: dict[str, Any] = {}
        # 8 维状态向量（对应 StateField 8 字段）
        self._state_dim = 8
        # 4 维动作向量（对应 ActionField 4 个 delta 字段）
        self._action_dim = 4

    def setup(self) -> None:
        """初始化预测器与插件（随机初始化权重）."""
        from app.plugins.world_model.net import WorldModelConfig
        from app.plugins.world_model.predictor import TrajectoryPredictor
        from app.plugins.world_model.plugin import WorldModelPlugin

        # W9.4 修复：本基准测原始 state_dim 字段拼接路径（StateField 8 维），
        # 必须显式 use_fusion=False——WorldModelConfig 默认 use_fusion=True
        # （ADR-020 融合模式，encoder 输入 fused_dim+action_dim=132），
        # 与本基准的 8 维状态输入不匹配会导致整组预测崩溃。
        # 融合模式性能由 research/ 侧 fusion_trainer 专项覆盖。
        config = WorldModelConfig(use_fusion=False)
        self._predictor = TrajectoryPredictor(config=config, device="auto")
        # 加载随机初始化权重（model_uri 不对应真实文件，ModelRegistry 回退随机初始化）
        self._predictor.load_model(
            model_uri="model://world_model/bench/1.0.0",
            weights_path=None,
        )
        self._plugin = WorldModelPlugin(config=config)

    # 纯推理路径基准

    def run_single_prediction(self, n_iterations: int = 50) -> dict[str, float]:
        """单次轨迹预测延迟（horizon=10）."""
        current_state = np.random.randn(self._state_dim).astype(np.float32)
        candidate_action = np.random.randn(10, self._action_dim).astype(np.float32)

        # warmup
        for _ in range(3):
            self._predictor.predict(
                current_state=current_state,
                candidate_action=candidate_action,
                horizon=10,
            )

        times: list[float] = []
        for _ in range(n_iterations):
            t0 = time.perf_counter()
            self._predictor.predict(
                current_state=current_state,
                candidate_action=candidate_action,
                horizon=10,
            )
            times.append((time.perf_counter() - t0) * 1000)

        stats = _percentiles(times)
        result = {f"wm_single_pred_ms_{k}": v for k, v in stats.items()}
        result["wm_single_pred_samples"] = n_iterations
        self._results.update(result)
        return result

    def run_horizon_scaling(self) -> dict[str, float]:
        """horizon 扩展性测试：horizon ∈ {5, 10, 20, 50}."""
        current_state = np.random.randn(self._state_dim).astype(np.float32)
        result: dict[str, float] = {}

        for horizon in [5, 10, 20, 50]:
            candidate_action = np.random.randn(horizon, self._action_dim).astype(np.float32)

            # warmup
            self._predictor.predict(
                current_state=current_state,
                candidate_action=candidate_action,
                horizon=horizon,
            )

            times: list[float] = []
            for _ in range(20):
                t0 = time.perf_counter()
                self._predictor.predict(
                    current_state=current_state,
                    candidate_action=candidate_action,
                    horizon=horizon,
                )
                times.append((time.perf_counter() - t0) * 1000)

            stats = _percentiles(times)
            result[f"wm_horizon_{horizon}_ms_p50"] = stats["p50"]
            result[f"wm_horizon_{horizon}_ms_p95"] = stats["p95"]
            result[f"wm_horizon_{horizon}_ms_mean"] = stats["mean"]

        self._results.update(result)
        return result

    def run_batch_prediction(self) -> dict[str, float]:
        """批量预测吞吐：10/50/100 个不同 candidate_action."""
        current_state = np.random.randn(self._state_dim).astype(np.float32)
        result: dict[str, float] = {}

        for batch_size in [10, 50, 100]:
            actions = [np.random.randn(10, self._action_dim).astype(np.float32) for _ in range(batch_size)]

            t0 = time.perf_counter()
            for action in actions:
                self._predictor.predict(
                    current_state=current_state,
                    candidate_action=action,
                    horizon=10,
                )
            elapsed_ms = (time.perf_counter() - t0) * 1000

            result[f"wm_batch_{batch_size}_ms"] = round(elapsed_ms, 3)
            result[f"wm_batch_{batch_size}_throughput_sps"] = round(batch_size / (elapsed_ms / 1000), 1)

        self._results.update(result)
        return result

    # 端到端插件路径基准

    def run_plugin_execute(self, n_iterations: int = 20) -> dict[str, float]:
        """端到端插件执行基准（含 artifact 解析 + 模型缓存查找）.

        对比纯推理路径，量化框架开销。
        """

        async def _run_once() -> TaskResult:
            state_artifact = Artifact(
                name="current_state",
                type="metrics",
                uri="metrics://bench/state",
                metadata={"data": np.random.randn(self._state_dim).astype(np.float32).tolist()},
            )
            action_artifact = Artifact(
                name="candidate_action",
                type="metrics",
                uri="metrics://bench/action",
                metadata={"data": np.random.randn(10, self._action_dim).astype(np.float32).tolist()},
            )
            ctx = TaskContext(
                job_id=f"bench-{time.time_ns()}",
                workflow_run_id=None,
                inputs={
                    "current_state": state_artifact,
                    "candidate_action": action_artifact,
                },
                config={
                    "horizon": 10,
                    "model_uri": "model://world_model/bench/1.0.0",
                },
                retry_count=0,
                deadline_ts=None,
            )
            return await self._plugin.execute(ctx)

        # warmup（首次调用会触发模型加载与缓存）
        asyncio.run(_run_once())

        times: list[float] = []
        for _ in range(n_iterations):
            t0 = time.perf_counter()
            result = asyncio.run(_run_once())
            if result.status != TaskStatus.COMPLETED:
                logger.warning("插件执行未完成: %s", result.error)
                continue
            times.append((time.perf_counter() - t0) * 1000)

        stats = _percentiles(times)
        result = {f"wm_plugin_exec_ms_{k}": v for k, v in stats.items()}
        result["wm_plugin_exec_samples"] = len(times)
        self._results.update(result)
        return result

    def run_model_cache_hit(self, n_iterations: int = 20) -> dict[str, float]:
        """模型缓存命中 vs 首次加载开销对比."""
        # 首次加载（冷启动）
        state_artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://bench/state",
            metadata={"data": np.random.randn(self._state_dim).astype(np.float32).tolist()},
        )
        action_artifact = Artifact(
            name="candidate_action",
            type="metrics",
            uri="metrics://bench/action",
            metadata={"data": np.random.randn(10, self._action_dim).astype(np.float32).tolist()},
        )

        async def _run_with_uri(uri: str) -> TaskResult:
            ctx = TaskContext(
                job_id=f"cache-{time.time_ns()}",
                workflow_run_id=None,
                inputs={
                    "current_state": state_artifact,
                    "candidate_action": action_artifact,
                },
                config={"horizon": 10, "model_uri": uri},
                retry_count=0,
                deadline_ts=None,
            )
            return await self._plugin.execute(ctx)

        # 冷启动：新 URI 触发加载
        cold_times: list[float] = []
        for i in range(5):
            t0 = time.perf_counter()
            asyncio.run(_run_with_uri(f"model://world_model/cold/{i}"))
            cold_times.append((time.perf_counter() - t0) * 1000)

        # 热命中：复用已加载 URI
        hot_times: list[float] = []
        for _ in range(n_iterations):
            t0 = time.perf_counter()
            asyncio.run(_run_with_uri("model://world_model/cold/0"))
            hot_times.append((time.perf_counter() - t0) * 1000)

        cold_stats = _percentiles(cold_times)
        hot_stats = _percentiles(hot_times)
        result = {
            "wm_cache_cold_ms_p50": cold_stats["p50"],
            "wm_cache_cold_ms_mean": cold_stats["mean"],
            "wm_cache_hot_ms_p50": hot_stats["p50"],
            "wm_cache_hot_ms_mean": hot_stats["mean"],
            "wm_cache_speedup": round(cold_stats["mean"] / max(hot_stats["mean"], 0.001), 2),
        }
        self._results.update(result)
        return result

    # 结果汇总

    def get_all_results(self) -> dict[str, Any]:
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        try:
            import torch as _torch

            torch_version = _torch.__version__
            has_torch = True
        except ImportError:
            torch_version = "N/A (NumPy fallback)"
            has_torch = False

        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "module": "world_model_bench",
            "environment": {
                "python": sys.version.split()[0],
                "torch": torch_version,
                "numpy": np.__version__,
                "has_torch": has_torch,
            },
            "results": self.get_all_results(),
            "threshold_violations": check_violations(self.get_all_results()),
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


def bench_single_prediction(benchmark: Any) -> None:
    bench = WorldModelPerfBenchmark()
    bench.setup()
    benchmark(bench.run_single_prediction)


def bench_horizon_scaling(benchmark: Any) -> None:
    bench = WorldModelPerfBenchmark()
    bench.setup()
    benchmark(bench.run_horizon_scaling)


def bench_batch_prediction(benchmark: Any) -> None:
    bench = WorldModelPerfBenchmark()
    bench.setup()
    benchmark(bench.run_batch_prediction)


def bench_plugin_execute(benchmark: Any) -> None:
    bench = WorldModelPerfBenchmark()
    bench.setup()
    benchmark(bench.run_plugin_execute)
