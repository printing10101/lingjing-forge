"""性能基准阈值定义与管理。

为 LNN 推理、G代码生成、3D渲染等核心操作定义性能基线、
波动范围和告警阈值。支持 CI/CD 自动回归检测。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PERFORMANCE_THRESHOLDS: dict[str, dict[str, float]] = {
    "lnn_inference_p50_ms": {"max": 50.0, "warning": 35.0},
    "lnn_inference_p95_ms": {"max": 200.0, "warning": 150.0},
    "lnn_inference_p99_ms": {"max": 500.0, "warning": 350.0},
    "lnn_inference_mean_ms": {"max": 80.0, "warning": 50.0},
    "lnn_inference_max_ms": {"max": 1000.0, "warning": 600.0},
    "lnn_batch_10_throughput_sps": {"min": 5000.0, "warning": 3000.0},
    "lnn_batch_50_throughput_sps": {"min": 20000.0, "warning": 10000.0},
    "lnn_batch_100_throughput_sps": {"min": 30000.0, "warning": 15000.0},
    "gcode_generation_total_s": {"max": 30.0, "warning": 20.0},
    "gcode_generation_per_part_s": {"max": 10.0, "warning": 7.0},
    "gcode_toolpath_gen_s": {"max": 15.0, "warning": 10.0},
    "gcode_post_processor_s": {"max": 5.0, "warning": 3.0},
    "gcode_syntax_validation_s": {"max": 2.0, "warning": 1.0},
    "render_fps_low_complexity": {"min": 55.0, "warning": 45.0},
    "render_fps_medium_complexity": {"min": 30.0, "warning": 24.0},
    "render_fps_high_complexity": {"min": 15.0, "warning": 12.0},
    "render_frame_time_low_ms": {"max": 18.0, "warning": 22.0},
    "render_frame_time_medium_ms": {"max": 33.0, "warning": 40.0},
    "render_frame_time_high_ms": {"max": 66.0, "warning": 80.0},
    "render_memory_usage_mb": {"max": 512.0, "warning": 400.0},
    "model_load_time_s": {"max": 5.0, "warning": 3.0},
    "drawing_parse_p50_s": {"max": 10.0, "warning": 7.0},
    "drawing_parse_p95_s": {"max": 15.0, "warning": 10.0},
}

REGRESSION_THRESHOLDS: dict[str, float] = {
    "warning_pct": 20.0,
    "critical_pct": 50.0,
    "improvement_pct": -50.0,
}

BOTTLENECK_THRESHOLD_PCT: float = 30.0


@dataclass
class ThresholdViolation:
    metric: str
    current_value: float
    threshold_value: float
    threshold_type: str
    severity: str
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "current_value": self.current_value,
            "threshold_value": self.threshold_value,
            "threshold_type": self.threshold_type,
            "severity": self.severity,
            "message": self.message,
        }


def get_threshold(metric: str) -> dict[str, float] | None:
    return PERFORMANCE_THRESHOLDS.get(metric)


def is_within_threshold(metric: str, value: float) -> bool:
    t = PERFORMANCE_THRESHOLDS.get(metric)
    if t is None:
        return True
    for key, limit in t.items():
        if key == "warning":
            continue
        if key == "max" and value > limit:
            return False
        if key == "min" and value < limit:
            return False
    return True


def check_violations(results: dict[str, float]) -> list[ThresholdViolation]:
    violations: list[ThresholdViolation] = []
    for metric, value in results.items():
        t = PERFORMANCE_THRESHOLDS.get(metric)
        if t is None:
            continue
        for key, limit in t.items():
            if key == "warning":
                continue
            if key == "max" and value > limit:
                severity = "CRITICAL" if value > limit * 1.5 else "VIOLATED"
                violations.append(ThresholdViolation(
                    metric=metric,
                    current_value=value,
                    threshold_value=limit,
                    threshold_type="max",
                    severity=severity,
                    message=f"{metric}={value:.3f} 超过阈值上限 {limit}",
                ))
            elif key == "min" and value < limit:
                severity = "CRITICAL" if value < limit * 0.5 else "VIOLATED"
                violations.append(ThresholdViolation(
                    metric=metric,
                    current_value=value,
                    threshold_value=limit,
                    threshold_type="min",
                    severity=severity,
                    message=f"{metric}={value:.3f} 低于阈值下限 {limit}",
                ))
    return violations
