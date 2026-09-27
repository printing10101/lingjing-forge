"""规则效果度量收集器（Effectiveness Metrics Collector）。

规则真实效果需要被度量并反馈（Outcomes 闭环）：
    - 规则在灰度发布期间触发的真实效果需要被度量
    - 度量结果反馈给 ProgressivePublisher 决定晋级/降级
    - 度量结果反馈给 ClosedLoop 决定规则置信度调整
    - 度量结果写入审计日志（学术诚信 D-2：每条规则效果可追溯）

度量维度（与 ProgressivePublisher 阈值对齐）：
    - accuracy（准确率）：规则触发后预测正确的比例
    - recall（召回率）：应触发规则而实际触发的比例
    - false_positive_rate（误报率）：不应触发而触发的比例
    - error_rate（错误率）：规则触发后导致生产异常的比例
    - sample_size（样本数）：观测窗口内的触发次数
    - conflict（冲突度）：多源证据融合的冲突系数

数据来源：
    - audit_log：规则应用/回滚决策记录
    - session_extractor：Session 中的规则触发与 outcome 数据
    - cutting_store：切削参数调整的后续效果
    - cam_validation：CAM 校验结果（是否被规则影响）

设计原则：
    - 度量不修改生产数据，只读
    - 度量结果写入审计日志（AIModule.DREAMING）
    - 度量窗口可配置（默认 7 天滚动窗口）
    - 样本数不足时返回低置信度（不阻断发布，但标记 insufficient_data）

硬约束对齐：
    - CAM 校验失败的触发不计入 accuracy（避免鼓励绕过 CAM）
    - SUCCEEDED 任务解锁的触发计为 error（错误率 +1）
    - HRC52 pending_calibration 触发降低置信度但不计为 error

用法：
    collector = EffectivenessMetricsCollector()
    metrics = collector.collect_metrics("rule_chatter_threshold_v1")
    if metrics["accuracy"] >= 0.70:
        publisher.promote("rule_chatter_threshold_v1", metrics_snapshot=metrics)

本模块为门面：实现已拆分至 _metrics_models / _samples_mixin / _compute_mixin。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from app.dreaming._compute_mixin import _ComputeMixin
from app.dreaming._metrics_models import (  # noqa: F401
    CONFIDENT_HIGH_SAMPLES,
    CONFIDENT_MID_SAMPLES,
    DEFAULT_METRICS_WINDOW_DAYS,
    DEFAULT_MIN_SAMPLE_SIZE,
    METRICS_SAMPLES_DIR,
    EffectivenessMetrics,
    OutcomeSample,
)
from app.dreaming._samples_mixin import _SamplesMixin

logger = logging.getLogger(__name__)


class EffectivenessMetricsCollector(_SamplesMixin, _ComputeMixin):
    """规则效果度量收集器。

    负责：
        1. 从 audit_log / session / cutting_store / cam_validation 收集规则触发的效果样本
        2. 计算准确率、召回率、误报率、错误率
        3. 检测硬约束违反（CAM 绕过、SUCCEEDED 解锁）
        4. 输出与 ProgressivePublisher 阈值对齐的指标快照
        5. 度量结果写入审计日志

    线程安全：通过内部锁保护样本列表。
    """

    def __init__(
        self,
        samples_dir: str | None = None,
        window_days: int = DEFAULT_METRICS_WINDOW_DAYS,
        min_sample_size: int = DEFAULT_MIN_SAMPLE_SIZE,
    ) -> None:
        """初始化度量收集器。

        Args:
            samples_dir: 样本持久化目录。
            window_days: 度量窗口天数。
            min_sample_size: 最小样本数阈值。
        """
        self.samples_dir = Path(samples_dir or METRICS_SAMPLES_DIR)
        self.samples_dir.mkdir(parents=True, exist_ok=True)
        self.window_days = window_days
        self.min_sample_size = min_sample_size
        self._lock = threading.RLock()
        # 内存缓存：rule_id -> List[OutcomeSample]
        self._samples: dict[str, list[OutcomeSample]] = {}
        self._load_samples()

    # 延迟依赖

    def _get_audit_recorder(self):
        from app.dreaming.audit_integration import get_audit_recorder

        return get_audit_recorder()

    def _get_audit_log(self):
        from app.audit.audit_log import get_audit_log

        return get_audit_log()


# 样本持久化


def collect_rule_metrics(
    rule_id: str,
    window_days: int = DEFAULT_METRICS_WINDOW_DAYS,
) -> EffectivenessMetrics:
    """便捷函数：收集规则效果度量。"""
    collector = EffectivenessMetricsCollector(window_days=window_days)
    return collector.collect_metrics(rule_id)


def record_outcome_sample(sample: OutcomeSample) -> bool:
    """便捷函数：录入单次规则效果样本。"""
    collector = EffectivenessMetricsCollector()
    return collector.record_sample(sample)
