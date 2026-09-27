"""系统性能阈值定义与管理.

定义各关键业务流程的性能基准线和回归检测标准.

阶段 8 扩展（ADR-017）：
- 世界模型轨迹预测阈值（wm_*）：基于 CNC 控制周期约束
  （单次预测 100ms，horizon=50 阈值 500ms）
- RL agent 决策阈值（rl_*）：SafetyShield 硬约束不能成为决策瓶颈
  （strict/non-strict 过滤延迟 < 5ms p95）
- 闭环工作流阈值（cl_*）：v1 离线 RL 场景端到端 p95 < 5s

注意：基准模块输出的指标键名为扁平形式（如 ``wm_single_pred_ms_p50``），
而非嵌套形式（如 ``wm_single_pred_ms: {p50: ...}``）。因此阈值字典的
键必须与基准模块输出的扁平键名完全匹配，``check_violations`` 才能
正确查找。所有延迟类阈值统一使用 ``{"max": value}`` 形式，与
``nc_generation_total_s`` 风格对齐。

不适合作阈值的指标（不设条目）：
- ``*_samples`` / ``*_total_samples``：样本数，非延迟
- ``*_throughput_*`` / ``*_speedup``：越高越好，``check_violations``
  默认按 "lower is better" 检查，设置会误报
- ``*_violation_rate`` / ``*_fallback_rate``：违反率统计，关注趋势
  而非绝对阈值
- ``*_pct_of_total`` / ``*_is_bottleneck``：占比分析字段
- ``*_overhead_vs_nonstrict_pct``：差异百分比，关注趋势
"""

from __future__ import annotations

PERFORMANCE_THRESHOLDS: dict[str, dict[str, float]] = {
    # 阶段 0-7 原有阈值
    "lnn_inference_ms": {
        "p50": 50,
        "p95": 200,
        "p99": 500,
    },
    "nc_generation_total_s": {
        "max": 30,
    },
    "drawing_parse_s": {
        "max": 10,
    },
    "model_load_s": {
        "max": 5,
    },
    "toolpath_gen_s": {
        "max": 15,
    },
    "post_processor_s": {
        "max": 5,
    },
    "batch_10_inference_ms": {
        "max": 200,
    },
    "batch_50_inference_ms": {
        "max": 800,
    },
    "batch_100_inference_ms": {
        "max": 1500,
    },
    # 阶段 8 扩展：世界模型轨迹预测阈值（world_model_bench.py）
    # 工程约束：单次预测 100ms（CNC 控制周期内），horizon=50 阈值 500ms
    # 单次预测延迟（horizon=10，50 次重复）
    "wm_single_pred_ms_p50": {"max": 100.0},
    "wm_single_pred_ms_p95": {"max": 200.0},
    "wm_single_pred_ms_p99": {"max": 500.0},
    # horizon 扩展性（各 horizon 的 p95）
    "wm_horizon_5_ms_p95": {"max": 100.0},
    "wm_horizon_10_ms_p95": {"max": 150.0},
    "wm_horizon_20_ms_p95": {"max": 250.0},
    "wm_horizon_50_ms_p95": {"max": 500.0},
    # 批量预测总耗时（10/50/100 个 candidate_action）
    "wm_batch_10_ms": {"max": 500.0},
    "wm_batch_50_ms": {"max": 2000.0},
    "wm_batch_100_ms": {"max": 4000.0},
    # 端到端插件执行（含 artifact 解析 + 模型缓存查找）
    "wm_plugin_exec_ms_p50": {"max": 150.0},
    "wm_plugin_exec_ms_p95": {"max": 400.0},
    "wm_plugin_exec_ms_p99": {"max": 1000.0},
    # 模型缓存命中 vs 冷启动
    "wm_cache_cold_ms_p50": {"max": 50.0},
    "wm_cache_cold_ms_mean": {"max": 80.0},
    "wm_cache_hot_ms_p50": {"max": 10.0},
    # 阶段 8 扩展：RL agent 决策 + SafetyShield 阈值（rl_agent_bench.py）
    # 工程约束：SafetyShield 硬约束不能成为决策路径瓶颈
    # 单次 RL 决策端到端延迟（含 policy + value + shield + artifact）
    "rl_single_decision_ms_p50": {"max": 100.0},
    "rl_single_decision_ms_p95": {"max": 300.0},
    "rl_single_decision_ms_p99": {"max": 800.0},
    # SafetyShield 过滤延迟（strict 模式）
    "rl_shield_strict_ms_p50": {"max": 1.0},
    "rl_shield_strict_ms_p95": {"max": 5.0},
    "rl_shield_strict_ms_p99": {"max": 10.0},
    # SafetyShield 过滤延迟（non-strict 模式）
    "rl_shield_nonstrict_ms_p50": {"max": 1.0},
    "rl_shield_nonstrict_ms_p95": {"max": 5.0},
    "rl_shield_nonstrict_ms_p99": {"max": 10.0},
    # 批量决策总耗时与平均耗时
    "rl_batch_10_total_ms": {"max": 1500.0},
    "rl_batch_50_total_ms": {"max": 6000.0},
    "rl_batch_100_total_ms": {"max": 12000.0},
    "rl_batch_10_avg_ms": {"max": 150.0},
    "rl_batch_50_avg_ms": {"max": 120.0},
    "rl_batch_100_avg_ms": {"max": 120.0},
    # 策略缓存命中 vs 冷启动
    "rl_policy_cold_ms_p50": {"max": 50.0},
    "rl_policy_cold_ms_p95": {"max": 200.0},
    "rl_policy_hot_ms_p50": {"max": 1.0},
    "rl_policy_hot_ms_p95": {"max": 5.0},
    # 阶段 8 扩展：闭环工作流端到端阈值（closed_loop_bench.py）
    # 工程约束：v1 离线 RL 场景端到端 p95 < 5s
    # 闭环总延迟（7 节点完整链路）
    "cl_total_ms_p50": {"max": 2000.0},
    "cl_total_ms_p95": {"max": 5000.0},
    "cl_total_ms_p99": {"max": 10000.0},
    # 各节点 p95 延迟（识别瓶颈）
    "cl_perceive_ms_p95": {"max": 200.0},
    "cl_predict_ms_p95": {"max": 500.0},
    "cl_decide_ms_p95": {"max": 500.0},
    "cl_generate_params_ms_p95": {"max": 300.0},
    "cl_validate_cam_ms_p95": {"max": 500.0},
    "cl_execute_ms_p95": {"max": 1000.0},
    "cl_collect_feedback_ms_p95": {"max": 200.0},
    # 吞吐量平均延迟（loops/second 的倒数）
    "cl_throughput_avg_ms": {"max": 5000.0},
}

REGRESSION_THRESHOLDS: dict[str, float] = {
    "warning_pct": 20.0,
    "critical_pct": 50.0,
}

BOTTLENECK_THRESHOLD_PCT: float = 30.0


def get_threshold(metric: str) -> dict[str, float] | None:
    return PERFORMANCE_THRESHOLDS.get(metric)


def is_within_threshold(metric: str, value: float) -> bool:
    t = PERFORMANCE_THRESHOLDS.get(metric)
    if t is None:
        return True
    if "max" in t:
        return value <= t["max"]
    for key, limit in t.items():
        if value > limit:
            return False
    return True


def check_violations(results: dict[str, float]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    for metric, value in results.items():
        t = PERFORMANCE_THRESHOLDS.get(metric)
        if t is None:
            continue
        if "max" in t and value > t["max"]:
            violations.append(
                {
                    "metric": metric,
                    "value": str(value),
                    "threshold": str(t["max"]),
                    "status": "VIOLATED",
                    "message": f"{metric}={value} 超过阈值 {t['max']}",
                }
            )
        for key, limit in t.items():
            if key == "max":
                continue
            if value > limit:
                violations.append(
                    {
                        "metric": f"{metric}.{key}",
                        "value": str(value),
                        "threshold": str(limit),
                        "status": "VIOLATED",
                        "message": f"{metric}.{key}={value} 超过阈值 {limit}",
                    }
                )
    return violations
