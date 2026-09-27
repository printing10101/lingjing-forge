"""闭环加工优化工作流端到端性能基准测试模块.

对应 ADR-017 第 3 节 + 阶段 8 性能基准扩展。

覆盖范围
--------
端到端测量闭环加工优化工作流（7 节点线性 DAG）的完整链路延迟：

    perceive → predict → decide → generate_params → validate_cam → execute → collect_feedback

节点实现策略
------------
- **真实插件节点**（2 个）：
    - ``predict`` (wm_predict_state) → ``WorldModelPlugin.execute()``
    - ``decide`` (rl_act) → ``RLAgentPlugin.execute()``
- **模拟节点**（5 个，无真实插件实现，用 numpy 矩阵乘法模拟耗时）：
    - ``perceive`` (data_ingest) - 传感器信号处理（FFT/小波包特征提取）
    - ``generate_params`` (cam_generate) - CAM 参数生成 + G-code 生成
    - ``validate_cam`` (cam_validate) - CAM 软件二次验证
    - ``execute`` (job_dispatch) - CAM 仿真（dry_run=true）
    - ``collect_feedback`` (flywheel_collect) - 反馈回写

设计原则
--------
- 与 ``nc_generation_bench.py`` 风格对齐：分阶段计时 + 瓶颈分析 + 阈值违规检查
- 真实插件节点使用 ``asyncio.run()`` 调用 ``execute()`` 完整路径
- artifact 流转：前一节点输出 metadata.data 作为后一节点输入
- v1 物理加工硬门控：``execute`` 节点固定 ``dry_run=true``，
  不接 CNC 控制器，物理执行需"持证操作员 + 导师签字 + 保险"
- max_concurrent=1：闭环严格顺序执行

工程现实约束
------------
- 基准测试不通过 ``WorkflowRunner`` + ``DAGStore`` 编排，
  避免数据库依赖（本地环境无 fastapi/aiosqlite）
- 直接顺序调用各节点 ``TaskHandler.execute()``，
  测量真实插件执行路径 + 模拟节点的稳态耗时
- 阈值基准：闭环端到端 p95 < 5s（v1 离线 RL 场景可接受）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

import numpy as np

from app.benchmarks.performance.thresholds import (
    BOTTLENECK_THRESHOLD_PCT,
    check_violations,
)

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


class ClosedLoopPerfBenchmark:
    """闭环加工优化工作流端到端性能基准测试."""

    # 7 节点顺序（与 closed_loop_machining_optimization.yaml 对齐）
    NODE_SEQUENCE: list[str] = [
        "perceive",
        "predict",
        "decide",
        "generate_params",
        "validate_cam",
        "execute",
        "collect_feedback",
    ]

    def __init__(self) -> None:
        self._wm_plugin: Any = None
        self._rl_plugin: Any = None
        self._results: dict[str, Any] = {}
        self._stage_times: dict[str, list[float]] = {n: [] for n in self.NODE_SEQUENCE}
        self._total_times: list[float] = []
        self._state_dim = 8
        self._action_dim = 4
        self._horizon = 10
        self._wm_model_uri = "model://world_model/bench/1.0.0"
        self._rl_model_uri = "model://rl_agent/bench/1.0.0"

    # 初始化

    def setup(self) -> None:
        """初始化 WorldModelPlugin + RLAgentPlugin."""
        from app.plugins.world_model.net import WorldModelConfig
        from app.plugins.world_model.plugin import WorldModelPlugin
        from app.plugins.rl_agent.plugin import RLAgentPlugin
        from app.plugins.rl_agent.safety_shield import SafetyConstraints

        wm_config = WorldModelConfig()
        self._wm_plugin = WorldModelPlugin(config=wm_config)

        self._rl_plugin = RLAgentPlugin(
            safety_constraints=SafetyConstraints(),
            safety_strict=True,
        )

        self._results = {}
        self._stage_times = {n: [] for n in self.NODE_SEQUENCE}
        self._total_times = []

    # 单次闭环执行

    def _run_single_closed_loop(self) -> dict[str, float]:
        """执行单次闭环工作流，返回各节点耗时（毫秒）.

        Returns
        -------
        dict[str, float]
            ``{node_id: elapsed_ms}`` 字典，含 ``total`` 总耗时。
        """
        from app.contracts.task import Artifact, TaskContext

        stage_times: dict[str, float] = {}

        # 节点 1: perceive（模拟传感器信号处理）
        t0 = time.perf_counter()
        current_state = self._simulate_perceive()
        stage_times["perceive"] = (time.perf_counter() - t0) * 1000

        # 节点 2: predict（真实 WorldModelPlugin）
        t0 = time.perf_counter()
        candidate_action = np.random.randn(self._horizon, self._action_dim).astype(np.float32)
        state_artifact = Artifact(
            name="current_state",
            type="metrics",
            uri="metrics://bench/state",
            metadata={"data": current_state.tolist()},
        )
        action_artifact = Artifact(
            name="candidate_action",
            type="metrics",
            uri="metrics://bench/action",
            metadata={"data": candidate_action.tolist()},
        )
        predict_ctx = TaskContext(
            job_id=f"bench-predict-{time.time_ns()}",
            workflow_run_id=None,
            inputs={
                "current_state": state_artifact,
                "candidate_action": action_artifact,
            },
            config={
                "horizon": self._horizon,
                "model_uri": self._wm_model_uri,
            },
            retry_count=0,
            deadline_ts=None,
        )
        predict_result = asyncio.run(self._wm_plugin.execute(predict_ctx))
        stage_times["predict"] = (time.perf_counter() - t0) * 1000

        # 提取预测轨迹摘要作为 decide 节点输入
        trajectory_artifact = predict_result.outputs.get("predicted_trajectory")
        trajectory_metrics_artifact = predict_result.outputs.get("trajectory_metrics")

        # 节点 3: decide（真实 RLAgentPlugin）
        t0 = time.perf_counter()
        decide_ctx = TaskContext(
            job_id=f"bench-decide-{time.time_ns()}",
            workflow_run_id=None,
            inputs={
                "current_state": state_artifact,
                "predicted_trajectory": trajectory_artifact or state_artifact,
            },
            config={"model_uri": self._rl_model_uri},
            retry_count=0,
            deadline_ts=None,
        )
        decide_result = asyncio.run(self._rl_plugin.execute(decide_ctx))
        stage_times["decide"] = (time.perf_counter() - t0) * 1000

        recommended_action = decide_result.outputs.get("action")

        # 节点 4: generate_params（模拟 CAM 参数生成）
        t0 = time.perf_counter()
        gcode_artifact = self._simulate_generate_params(recommended_action)
        stage_times["generate_params"] = (time.perf_counter() - t0) * 1000

        # 节点 5: validate_cam（模拟 CAM 软件验证）
        t0 = time.perf_counter()
        validation_artifact = self._simulate_validate_cam(gcode_artifact)
        stage_times["validate_cam"] = (time.perf_counter() - t0) * 1000

        # 节点 6: execute（模拟 CAM 仿真，dry_run=true）
        t0 = time.perf_counter()
        execution_artifact = self._simulate_execute(gcode_artifact, validation_artifact)
        stage_times["execute"] = (time.perf_counter() - t0) * 1000

        # 节点 7: collect_feedback（模拟反馈回写）
        t0 = time.perf_counter()
        self._simulate_collect_feedback(
            execution_artifact,
            trajectory_metrics_artifact,
            recommended_action,
        )
        stage_times["collect_feedback"] = (time.perf_counter() - t0) * 1000

        # 汇总总耗时
        stage_times["total"] = sum(stage_times[n] for n in self.NODE_SEQUENCE)
        return stage_times

    # 模拟节点实现

    def _simulate_perceive(self) -> np.ndarray:
        """模拟传感器信号处理（FFT + 小波包特征提取）.

        Returns
        -------
        np.ndarray
            当前加工状态向量 [state_dim]，float32。
        """
        # 模拟 50000 Hz × 5s = 250000 采样点信号处理
        # 用 numpy 矩阵乘法模拟 FFT + 特征提取开销
        signal = np.random.randn(1024).astype(np.float32)
        _ = np.fft.fft(signal)
        _ = np.random.randn(64, 32) @ np.random.randn(32, 16)
        # 输出状态向量
        return np.random.randn(self._state_dim).astype(np.float32)

    def _simulate_generate_params(self, action_artifact: Any) -> Artifact:  # noqa: F821  # 函数内延迟导入 + future annotations，注解运行时不求值
        """模拟 CAM 参数生成 + G-code 生成."""
        from app.contracts.task import Artifact

        # 模拟 G-code 字符串生成（~1000 行）
        gcode_lines = [f"G01 X{i * 0.1:.3f} Y{i * 0.05:.3f} F{500 + i}" for i in range(200)]
        gcode_str = "\n".join(gcode_lines)
        # 模拟参数查表 + 约束校验开销
        _ = np.random.randn(32, 32) @ np.random.randn(32, 16)
        return Artifact(
            name="gcode_artifact",
            type="file",
            uri="file://bench/output.gcode",
            metadata={
                "gcode_length": len(gcode_str),
                "lines": len(gcode_lines),
                "backend": "PyCAM",
            },
        )

    def _simulate_validate_cam(self, gcode_artifact: Any) -> Artifact:  # noqa: F821  # 函数内延迟导入 + future annotations，注解运行时不求值
        """模拟 CAM 软件二次验证（碰撞/过切/颤振稳定性检查）."""
        from app.contracts.task import Artifact

        # 模拟 CAM 软件解析 G-code + 5 类检查
        _ = np.random.randn(64, 64) @ np.random.randn(64, 32)
        _ = np.random.randn(32, 32) @ np.random.randn(32, 32)
        return Artifact(
            name="validation_report_artifact",
            type="metrics",
            uri="metrics://bench/validation",
            metadata={
                "checks_passed": 5,
                "checks_failed": 0,
                "safety_margin_mm": 0.15,
                "warnings": [],
            },
        )

    def _simulate_execute(self, gcode_artifact: Any, validation_artifact: Any) -> Artifact:  # noqa: F821  # 函数内延迟导入 + future annotations，注解运行时不求值
        """模拟 CAM 仿真执行（dry_run=true，v1 硬门控）."""
        from app.contracts.task import Artifact

        # 模拟 CAM 仿真（不接 CNC 控制器）
        _ = np.random.randn(128, 64) @ np.random.randn(64, 32)
        return Artifact(
            name="result_artifact",
            type="metrics",
            uri="metrics://bench/execution",
            metadata={
                "dry_run": True,
                "simulation_completed": True,
                "material_removed_mm3": 1250.5,
                "cycle_time_seconds": 45.2,
            },
        )

    def _simulate_collect_feedback(
        self,
        execution_artifact: Any,
        trajectory_metrics_artifact: Any,
        recommended_action: Any,
    ) -> Artifact:  # noqa: F821  # 函数内延迟导入 + future annotations，注解运行时不求值
        """模拟反馈回写数据飞轮."""
        from app.contracts.task import Artifact

        # 模拟反馈记录组装 + 写入数据集
        _ = np.random.randn(32, 16) @ np.random.randn(16, 8)
        return Artifact(
            name="feedback_artifact",
            type="metrics",
            uri="metrics://bench/feedback",
            metadata={
                "actual_vs_predicted_error": 0.05,
                "chatter_detected": False,
                "surface_quality_score": 0.92,
                "tool_wear_increment_mm": 0.012,
            },
        )

    # 基准方法

    def run_full_pipeline(self, n_iterations: int = 10) -> dict[str, Any]:
        """完整闭环端到端延迟基准（n_iterations 轮）.

        Parameters
        ----------
        n_iterations : int
            闭环执行轮数，默认 10。

        Returns
        -------
        dict[str, Any]
            各节点延迟分位数 + 总延迟分位数 + 瓶颈分析。
        """
        # warmup
        for _ in range(2):
            self._run_single_closed_loop()

        # 正式测量
        self._stage_times = {n: [] for n in self.NODE_SEQUENCE}
        self._total_times = []
        for _ in range(n_iterations):
            stage_times = self._run_single_closed_loop()
            for node in self.NODE_SEQUENCE:
                self._stage_times[node].append(stage_times[node])
            self._total_times.append(stage_times["total"])

        # 汇总
        result: dict[str, Any] = {}
        for node in self.NODE_SEQUENCE:
            stats = _percentiles(self._stage_times[node])
            for k, v in stats.items():
                result[f"cl_{node}_ms_{k}"] = v
            result[f"cl_{node}_samples"] = n_iterations

        total_stats = _percentiles(self._total_times)
        for k, v in total_stats.items():
            result[f"cl_total_ms_{k}"] = v
        result["cl_total_samples"] = n_iterations

        # 瓶颈分析
        bottlenecks = self._analyze_bottlenecks(total_stats.get("mean", 0.0))
        result["cl_bottlenecks"] = bottlenecks

        # 阈值违规检查
        violations = check_violations(result)
        result["cl_threshold_violations"] = violations

        self._results.update(result)
        return result

    def run_node_breakdown(self) -> dict[str, Any]:
        """单次循环各节点延迟分解（百分比占比）.

        Returns
        -------
        dict[str, Any]
            各节点平均耗时 + 占总耗时百分比 + 瓶颈标记。
        """
        if not self._total_times:
            # 若未执行过 full_pipeline，先执行一次
            self.run_full_pipeline(n_iterations=5)

        result: dict[str, Any] = {}
        total_mean = sum(
            sum(self._stage_times[n]) / len(self._stage_times[n]) for n in self.NODE_SEQUENCE if self._stage_times[n]
        )
        if total_mean <= 0:
            return result

        for node in self.NODE_SEQUENCE:
            if not self._stage_times[node]:
                continue
            node_mean = sum(self._stage_times[node]) / len(self._stage_times[node])
            pct = node_mean / total_mean * 100
            result[f"cl_{node}_mean_ms"] = round(node_mean, 3)
            result[f"cl_{node}_pct_of_total"] = round(pct, 2)
            result[f"cl_{node}_is_bottleneck"] = pct >= BOTTLENECK_THRESHOLD_PCT

        result["cl_breakdown_total_mean_ms"] = round(total_mean, 3)
        self._results.update(result)
        return result

    def run_throughput(self, n_iterations: int = 10) -> dict[str, Any]:
        """连续多轮闭环吞吐量基准.

        Parameters
        ----------
        n_iterations : int
            连续执行轮数。

        Returns
        -------
        dict[str, Any]
            总耗时 + 吞吐量（loops/second）+ 平均单轮延迟。
        """
        # warmup
        self._run_single_closed_loop()

        t0 = time.perf_counter()
        for _ in range(n_iterations):
            self._run_single_closed_loop()
        total_elapsed_s = time.perf_counter() - t0

        throughput = round(n_iterations / total_elapsed_s, 3)
        avg_per_loop_ms = round(total_elapsed_s / n_iterations * 1000, 3)

        result = {
            "cl_throughput_samples": n_iterations,
            "cl_throughput_total_s": round(total_elapsed_s, 3),
            "cl_throughput_lps": throughput,
            "cl_throughput_avg_ms": avg_per_loop_ms,
        }
        self._results.update(result)
        return result

    # 瓶颈分析

    def _analyze_bottlenecks(self, total_mean_ms: float) -> list[dict[str, Any]]:
        """识别闭环中的性能瓶颈节点.

        Parameters
        ----------
        total_mean_ms : float
            闭环总平均耗时（毫秒）。

        Returns
        -------
        list[dict[str, Any]]
            瓶颈节点列表（占比 >= BOTTLENECK_THRESHOLD_PCT）。
        """
        bottlenecks: list[dict[str, Any]] = []
        if total_mean_ms <= 0:
            return bottlenecks

        for node in self.NODE_SEQUENCE:
            if not self._stage_times[node]:
                continue
            node_mean = sum(self._stage_times[node]) / len(self._stage_times[node])
            pct = node_mean / total_mean_ms * 100
            if pct >= BOTTLENECK_THRESHOLD_PCT:
                bottlenecks.append(
                    {
                        "node": node,
                        "mean_ms": round(node_mean, 3),
                        "percentage": round(pct, 2),
                        "is_bottleneck": True,
                    }
                )

        return bottlenecks

    # 汇总与持久化

    def get_all_results(self) -> dict[str, Any]:
        """返回所有已测量的结果."""
        return dict(self._results)

    def save_results(self, output_path: str) -> str:
        """保存基准结果为 JSON 文件."""
        try:
            import torch as _torch

            has_torch = True
            torch_version = _torch.__version__
            cuda_available = _torch.cuda.is_available()
        except ImportError:
            has_torch = False
            torch_version = None
            cuda_available = False

        data = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "module": "closed_loop_bench",
            "workflow_template": "closed_loop_machining_optimization",
            "node_sequence": list(self.NODE_SEQUENCE),
            "environment": {
                "python": sys.version.split()[0],
                "torch_available": has_torch,
                "torch_version": torch_version,
                "cuda_available": cuda_available,
                "numpy_version": np.__version__,
            },
            "results": dict(self._results),
        }
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return output_path


# pytest-benchmark 集成


def bench_full_pipeline(benchmark: Any) -> None:
    """pytest-benchmark 入口：完整闭环端到端延迟."""
    bench = ClosedLoopPerfBenchmark()
    bench.setup()
    benchmark(bench.run_full_pipeline)


def bench_node_breakdown(benchmark: Any) -> None:
    """pytest-benchmark 入口：各节点延迟分解."""
    bench = ClosedLoopPerfBenchmark()
    bench.setup()
    benchmark(bench.run_node_breakdown)


def bench_throughput(benchmark: Any) -> None:
    """pytest-benchmark 入口：连续多轮闭环吞吐量."""
    bench = ClosedLoopPerfBenchmark()
    bench.setup()
    benchmark(bench.run_throughput)


__all__ = [
    "ClosedLoopPerfBenchmark",
    "bench_full_pipeline",
    "bench_node_breakdown",
    "bench_throughput",
]
