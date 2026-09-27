"""RL agent 决策 + SafetyShield 性能基准测试模块.

对应 ADR-017 第 1.2 节 + 阶段 8 性能基准扩展。

覆盖范围
--------
1. 单次 RL 决策端到端延迟：``RLAgentPlugin.execute()`` 完整路径
   （策略前向 + 值网络前向 + SafetyShield 过滤 + Artifact 构造）
2. SafetyShield 过滤延迟：纯 ``SafetyShield.filter()`` 调用，
   strict 模式 vs non-strict 模式对比
3. 批量决策吞吐：10/50/100 个不同状态向量的批量决策
4. 策略缓存命中 vs 冷启动：对比 ``_policy_cache`` 命中与首次加载开销
5. 安全违反率统计：随机动作中触发 SafetyShield 过滤的比例

设计原则
--------
- 与 ``lnn_inference_bench.py`` / ``world_model_bench.py`` 风格对齐：
  ``__init__`` → ``setup()`` → ``run_*()`` → ``get_all_results()`` → ``save_results()``
- warmup 机制：正式测量前执行 3 次预热，避免冷启动影响
- 输出 JSON 含 environment + threshold_violations，便于 CI 回归检测
- pytest-benchmark 集成：模块级 ``bench_*()`` 函数

工程现实约束
------------
- v1 仅离线 RL：基准使用随机初始化权重，仅测量推理路径性能，
  不涉及训练梯度计算
- SafetyShield 是硬约束层，基准单独测量其过滤延迟，确保硬约束
  不会成为决策路径的性能瓶颈
- 不直接接 CNC 控制器，输出动作仅供 CAM 验证层参考
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import numpy as np

from app.benchmarks.performance.thresholds import check_violations

if __package__ in (None, ""):
    import _bootstrap  # noqa: F401  # 脚本直跑时引导 engineering/python 入 sys.path


def _percentiles(times: list[float]) -> dict[str, float]:
    """计算延迟分位数统计.

    Parameters
    ----------
    times : list[float]
        延迟样本（毫秒）。

    Returns
    -------
    dict[str, float]
        p50/p95/p99/mean/min/max 统计值（毫秒，保留 3 位小数）。
    """
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


class RLAgentPerfBenchmark:
    """RL agent 决策 + SafetyShield 性能基准测试."""

    def __init__(self) -> None:
        self._plugin: Any = None
        self._policy_net: Any = None
        self._value_net: Any = None
        self._shield_strict: Any = None
        self._shield_nonstrict: Any = None
        self._results: dict[str, Any] = {}
        self._state_dim = 8
        self._action_dim = 4
        self._model_uri = "model://rl_agent/bench/1.0.0"

    # 初始化

    def setup(self) -> None:
        """初始化策略/值网络 + SafetyShield + RL agent 插件.

        使用随机初始化权重（torch 优先，无则 NumPy 回退）。
        SafetyShield 准备 strict / non-strict 两个实例用于对比。
        """
        from app.plugins.rl_agent.policy import PolicyConfig, PolicyNet
        from app.plugins.rl_agent.safety_shield import (
            SafetyConstraints,
            SafetyShield,
        )
        from app.plugins.rl_agent.plugin import RLAgentPlugin
        from app.plugins.rl_agent.value import ValueConfig, ValueNet

        policy_config = PolicyConfig()
        value_config = ValueConfig(
            state_dim=policy_config.state_dim,
            hidden_dim=policy_config.hidden_dim,
            seed=policy_config.seed,
        )
        constraints = SafetyConstraints()

        self._policy_net = PolicyNet(policy_config)
        self._value_net = ValueNet(value_config)
        self._shield_strict = SafetyShield(constraints=constraints, strict=True)
        self._shield_nonstrict = SafetyShield(constraints=constraints, strict=False)
        self._plugin = RLAgentPlugin(
            policy_config=policy_config,
            value_config=value_config,
            safety_constraints=constraints,
            safety_strict=True,
        )

    # 基准 1：单次 RL 决策端到端延迟

    def run_single_decision(self, n_iterations: int = 50) -> dict[str, Any]:
        """单次 RL 决策端到端延迟（含 policy + value + shield + artifact）.

        通过 ``RLAgentPlugin.execute()`` 完整路径测量，
        覆盖策略前向 + 值网络前向 + SafetyShield 过滤 + Artifact 构造。
        """
        from app.contracts.task import Artifact, TaskContext

        async def _run_once() -> Any:
            state = np.random.randn(self._state_dim).astype(np.float32)
            state_artifact = Artifact(
                name="current_state",
                type="metrics",
                uri="metrics://bench/state",
                metadata={"data": state.tolist()},
            )
            ctx = TaskContext(
                job_id=f"bench-{time.time_ns()}",
                workflow_run_id=None,
                inputs={"current_state": state_artifact},
                config={"model_uri": self._model_uri},
                retry_count=0,
                deadline_ts=None,
            )
            return await self._plugin.execute(ctx)

        # warmup
        for _ in range(3):
            asyncio.run(_run_once())

        times: list[float] = []
        for _ in range(n_iterations):
            t0 = time.perf_counter()
            asyncio.run(_run_once())
            times.append((time.perf_counter() - t0) * 1000)

        stats = _percentiles(times)
        result = {f"rl_single_decision_ms_{k}": v for k, v in stats.items()}
        result["rl_single_decision_samples"] = n_iterations
        self._results.update(result)
        return result

    # 基准 2：SafetyShield 过滤延迟（strict vs non-strict）

    def run_safety_shield_filter(self, n_iterations: int = 200) -> dict[str, Any]:
        """SafetyShield.filter() 纯过滤延迟.

        生成 [-1, +1] 范围内的随机动作向量，分别测量 strict 与
        non-strict 模式的过滤延迟。strict 模式触发回退，
        non-strict 模式触发裁剪，覆盖两条执行路径。
        """
        # 准备动作序列：交替生成合法动作与违规动作
        actions: list[np.ndarray] = []
        prev_actions: list[np.ndarray] = []
        legal_action = np.zeros(self._action_dim, dtype=np.float32)
        for i in range(n_iterations):
            if i % 2 == 0:
                # 合法动作：小幅 delta
                act = np.clip(
                    np.random.randn(self._action_dim) * 0.1,
                    -0.15,
                    0.15,
                ).astype(np.float32)
            else:
                # 违规动作：大幅 delta 触发边界/变化率违反
                act = np.clip(
                    np.random.randn(self._action_dim) * 2.0,
                    -2.0,
                    2.0,
                ).astype(np.float32)
            actions.append(act)
            prev_actions.append(legal_action.copy())
            # 更新 legal_action 为当前动作的合法版本
            legal_action = np.clip(act, -0.15, 0.15).astype(np.float32)

        # warmup
        for shield in (self._shield_strict, self._shield_nonstrict):
            for i in range(3):
                shield.filter(actions[i], prev_actions[i])

        # strict 模式测量
        strict_times: list[float] = []
        for i in range(n_iterations):
            t0 = time.perf_counter()
            self._shield_strict.filter(actions[i], prev_actions[i])
            strict_times.append((time.perf_counter() - t0) * 1000)

        # non-strict 模式测量
        nonstrict_times: list[float] = []
        for i in range(n_iterations):
            t0 = time.perf_counter()
            self._shield_nonstrict.filter(actions[i], prev_actions[i])
            nonstrict_times.append((time.perf_counter() - t0) * 1000)

        strict_stats = _percentiles(strict_times)
        nonstrict_stats = _percentiles(nonstrict_times)

        result: dict[str, Any] = {}
        for k, v in strict_stats.items():
            result[f"rl_shield_strict_ms_{k}"] = v
        for k, v in nonstrict_stats.items():
            result[f"rl_shield_nonstrict_ms_{k}"] = v
        result["rl_shield_samples"] = n_iterations
        result["rl_shield_strict_overhead_vs_nonstrict_pct"] = round(
            (strict_stats.get("mean", 0.0) - nonstrict_stats.get("mean", 0.0))
            / max(nonstrict_stats.get("mean", 1.0), 1e-6)
            * 100,
            3,
        )
        self._results.update(result)
        return result

    # 基准 3：批量决策吞吐

    def run_batch_decisions(self) -> dict[str, Any]:
        """批量决策吞吐：10/50/100 个不同状态向量.

        每个批次使用不同的状态向量，测量总耗时与吞吐量
        （decisions/second）。批量间共享同一 plugin 实例，
        验证策略缓存命中场景下的稳态吞吐。
        """
        from app.contracts.task import Artifact, TaskContext

        async def _run_batch(n: int) -> float:
            t0 = time.perf_counter()
            for _ in range(n):
                state = np.random.randn(self._state_dim).astype(np.float32)
                state_artifact = Artifact(
                    name="current_state",
                    type="metrics",
                    uri="metrics://bench/state",
                    metadata={"data": state.tolist()},
                )
                ctx = TaskContext(
                    job_id=f"bench-{time.time_ns()}",
                    workflow_run_id=None,
                    inputs={"current_state": state_artifact},
                    config={"model_uri": self._model_uri},
                    retry_count=0,
                    deadline_ts=None,
                )
                await self._plugin.execute(ctx)
            return (time.perf_counter() - t0) * 1000

        # warmup
        asyncio.run(_run_batch(3))

        result: dict[str, Any] = {}
        for batch_size in (10, 50, 100):
            elapsed_ms = asyncio.run(_run_batch(batch_size))
            throughput = round(batch_size / (elapsed_ms / 1000.0), 2)
            result[f"rl_batch_{batch_size}_total_ms"] = round(elapsed_ms, 3)
            result[f"rl_batch_{batch_size}_throughput_dps"] = throughput
            result[f"rl_batch_{batch_size}_avg_ms"] = round(elapsed_ms / batch_size, 3)

        self._results.update(result)
        return result

    # 基准 4：策略缓存命中 vs 冷启动

    def run_policy_cache_hit(self, n_iterations: int = 20) -> dict[str, Any]:
        """策略缓存命中 vs 冷启动开销对比.

        冷启动：使用 5 个不同的 model_uri 触发首次加载
        （实例化 PolicyNet + 尝试 ModelRegistry 解析 + 失败回退随机初始化）。
        热命中：复用已加载的 model_uri，仅查字典命中。
        """
        cold_uris = [f"model://rl_agent/cold/{i}" for i in range(n_iterations)]
        hot_uri = self._model_uri  # setup 阶段已加载

        # warmup 热路径
        for _ in range(3):
            self._plugin._get_or_load_policy(hot_uri)

        # 冷启动测量：每个 URI 仅调用一次
        cold_times: list[float] = []
        for uri in cold_uris:
            t0 = time.perf_counter()
            self._plugin._get_or_load_policy(uri)
            cold_times.append((time.perf_counter() - t0) * 1000)

        # 热命中测量
        hot_times: list[float] = []
        for _ in range(n_iterations):
            t0 = time.perf_counter()
            self._plugin._get_or_load_policy(hot_uri)
            hot_times.append((time.perf_counter() - t0) * 1000)

        cold_stats = _percentiles(cold_times)
        hot_stats = _percentiles(hot_times)

        result: dict[str, Any] = {}
        for k, v in cold_stats.items():
            result[f"rl_policy_cold_ms_{k}"] = v
        for k, v in hot_stats.items():
            result[f"rl_policy_hot_ms_{k}"] = v
        result["rl_policy_cache_samples"] = n_iterations
        result["rl_policy_cache_speedup"] = round(
            cold_stats.get("mean", 0.0) / max(hot_stats.get("mean", 1e-6), 1e-6),
            3,
        )
        self._results.update(result)
        return result

    # 基准 5：安全违反率统计

    def run_safety_violation_rate(self, n_samples: int = 1000) -> dict[str, Any]:
        """随机动作中触发 SafetyShield 过滤的比例统计.

        生成 n_samples 个随机动作（[-2, +2] 范围，刻意触发违反），
        统计：
        - 边界违反率（超出物理区间）
        - 变化率违反率（相邻动作变化过大）
        - 总违反率（任一违反）
        - 回退使用率（strict 模式触发回退）

        Returns
        -------
        dict[str, Any]
            违反率统计指标。
        """
        legal_action = np.zeros(self._action_dim, dtype=np.float32)
        boundary_violations = 0
        delta_violations = 0
        total_violations = 0
        fallback_count = 0

        for _ in range(n_samples):
            # 随机动作，刻意覆盖合法/违规场景
            raw_action = np.random.uniform(-2.0, 2.0, size=self._action_dim).astype(np.float32)

            safe_action, result = self._shield_strict.filter(raw_action, prev_action=legal_action)

            if result.violated:
                total_violations += 1
                # 判断违反类型
                for v in result.violations:
                    if "变化" in v:
                        delta_violations += 1
                    else:
                        boundary_violations += 1
                if result.fallback_used:
                    fallback_count += 1

            # 更新参考动作为过滤后的安全动作（模拟连续决策）
            legal_action = safe_action.copy()

        result: dict[str, Any] = {
            "rl_safety_total_samples": n_samples,
            "rl_safety_boundary_violation_rate": round(boundary_violations / n_samples, 4),
            "rl_safety_delta_violation_rate": round(delta_violations / n_samples, 4),
            "rl_safety_total_violation_rate": round(total_violations / n_samples, 4),
            "rl_safety_fallback_rate": round(fallback_count / n_samples, 4),
        }
        self._results.update(result)
        return result

    # 汇总与持久化

    def get_all_results(self) -> dict[str, Any]:
        """返回所有已测量的结果."""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存基准结果为 JSON 文件.

        Parameters
        ----------
        output_path : str
            输出文件路径。

        Returns
        -------
        str
            实际写入的文件路径。
        """
        try:
            import torch as _torch

            has_torch = True
            torch_version = _torch.__version__
            cuda_available = _torch.cuda.is_available()
        except ImportError:
            has_torch = False
            torch_version = None
            cuda_available = False

        violations = check_violations(self._results)

        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "module": "rl_agent_bench",
            "environment": {
                "python": sys.version.split()[0],
                "torch_available": has_torch,
                "torch_version": torch_version,
                "cuda_available": cuda_available,
                "numpy_version": np.__version__,
            },
            "results": dict(self._results),
            "threshold_violations": violations,
        }
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


# pytest-benchmark 集成


def bench_single_decision(benchmark: Any) -> None:
    """pytest-benchmark 入口：单次 RL 决策端到端延迟."""
    bench = RLAgentPerfBenchmark()
    bench.setup()
    benchmark(bench.run_single_decision)


def bench_safety_shield_filter(benchmark: Any) -> None:
    """pytest-benchmark 入口：SafetyShield 过滤延迟."""
    bench = RLAgentPerfBenchmark()
    bench.setup()
    benchmark(bench.run_safety_shield_filter)


def bench_batch_decisions(benchmark: Any) -> None:
    """pytest-benchmark 入口：批量决策吞吐."""
    bench = RLAgentPerfBenchmark()
    bench.setup()
    benchmark(bench.run_batch_decisions)


def bench_policy_cache_hit(benchmark: Any) -> None:
    """pytest-benchmark 入口：策略缓存命中 vs 冷启动."""
    bench = RLAgentPerfBenchmark()
    bench.setup()
    benchmark(bench.run_policy_cache_hit)


__all__ = [
    "RLAgentPerfBenchmark",
    "bench_single_decision",
    "bench_safety_shield_filter",
    "bench_batch_decisions",
    "bench_policy_cache_hit",
]
