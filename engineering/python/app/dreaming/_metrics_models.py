"""效果度量数据类与常量（从 effectiveness_metrics 拆出）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class OutcomeSample:
    """单次规则触发的效果观测样本。

    Attributes:
        rule_id: 规则 ID。
        triggered_at: 触发时间戳。
        trigger_context: 触发时的上下文（输入数据）。
        predicted_outcome: 规则预测的结果。
        actual_outcome: 实际发生的结果。
        correct: 预测是否正确（predicted == actual）。
        false_positive: 是否为误报（不应触发而触发）。
        false_negative: 是否为漏报（应触发而未触发）。
        production_error: 是否导致生产异常。
        cam_validation_bypassed: 是否绕过了 CAM 校验（硬约束违反标记）。
        succeeded_lock_violated: 是否违反 SUCCEEDED 禁删（硬约束违反标记）。
        source: 数据来源（audit_log / session / cutting_store / cam_validation）。
    """

    rule_id: str
    triggered_at: str
    trigger_context: dict[str, Any] = field(default_factory=dict)
    predicted_outcome: Any = None
    actual_outcome: Any = None
    correct: bool = False
    false_positive: bool = False
    false_negative: bool = False
    production_error: bool = False
    cam_validation_bypassed: bool = False
    succeeded_lock_violated: bool = False
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EffectivenessMetrics:
    """规则效果度量结果。

    Attributes:
        rule_id: 规则 ID。
        accuracy: 准确率 = correct / sample_size。
        recall: 召回率 = triggered / (triggered + missed)。
        false_positive_rate: 误报率 = false_positive / sample_size。
        error_rate: 错误率 = production_error / sample_size。
        sample_size: 样本数。
        conflict: 多源证据冲突度（0-1，None 表示无融合）。
        confidence: 度量置信度（基于样本数和一致性）。
        window_start: 度量窗口起始时间。
        window_end: 度量窗口结束时间。
        collected_at: 度量收集时间。
        insufficient_data: 是否样本数不足（< min_sample_size）。
        hard_constraint_violations: 硬约束违反次数（CAM 绕过 + SUCCEEDED 解锁）。
    """

    rule_id: str
    accuracy: float = 0.0
    recall: float = 0.0
    false_positive_rate: float = 0.0
    error_rate: float = 0.0
    sample_size: int = 0
    conflict: float | None = None
    confidence: float = 0.0
    window_start: str = ""
    window_end: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    insufficient_data: bool = False
    hard_constraint_violations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_publisher_snapshot(self) -> dict[str, Any]:
        """转换为 ProgressivePublisher 使用的指标快照格式。

        ProgressivePublisher 的 _check_promotion_thresholds 和
        _check_demotion_thresholds 期望以下字段：
            - accuracy
            - false_positive_rate
            - sample_size
            - error_rate
        """
        return {
            "accuracy": self.accuracy,
            "false_positive_rate": self.false_positive_rate,
            "sample_size": self.sample_size,
            "error_rate": self.error_rate,
            "recall": self.recall,
            "conflict": self.conflict,
            "confidence": self.confidence,
            "hard_constraint_violations": self.hard_constraint_violations,
            "insufficient_data": self.insufficient_data,
        }


# 度量收集器


# 度量样本持久化目录
METRICS_SAMPLES_DIR = "python/outputs/dreaming/metrics_samples"

# 默认度量窗口（天）
DEFAULT_METRICS_WINDOW_DAYS = 7

# 最小样本数（低于此值标记 insufficient_data）
DEFAULT_MIN_SAMPLE_SIZE = 10

# 度量置信度计算参数
# 样本数 >= CONFIDENT_HIGH_SAMPLES 时置信度 = 0.9
# 样本数 >= CONFIDENT_MID_SAMPLES 时置信度 = 0.7
# 样本数 >= DEFAULT_MIN_SAMPLE_SIZE 时置信度 = 0.5
# 样本数 < DEFAULT_MIN_SAMPLE_SIZE 时置信度 = 0.2
CONFIDENT_HIGH_SAMPLES = 50
CONFIDENT_MID_SAMPLES = 20
