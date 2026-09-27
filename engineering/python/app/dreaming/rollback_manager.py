"""规则回滚管理器（Rollback Manager）。

异常自动回滚机制：
    - 灰度发布中的规则触发异常指标时，自动回滚
    - 异常检测：连续 N 次效果指标低于阈值、硬约束违反、生产异常率超限
    - 回滚动作：降级灰度阶段 → 标记 DEPRECATED → 知识图谱回滚
    - 所有回滚决策写入审计日志

设计原则：
    - 回滚不删除审计记录，仅标记规则状态为 deprecated
    - 回滚优先级：硬约束违反 > 生产异常 > 指标恶化
    - 硬约束违反立即回滚，不等指标窗口
    - 回滚后规则进入冷却期（默认 24h），期间不可重新发布
    - 回滚幂等：已 deprecated 的规则再次回滚返回成功

硬约束对齐：
    - CAM 校验绕过 → 立即回滚
    - SUCCEEDED 任务解锁 → 立即回滚
    - HRC52 pending_calibration 阈值降低 → 立即回滚
    - 这些回滚不通过 ProgressivePublisher.demote，直接调用 RuleApplicator.rollback

用法：
    manager = RollbackManager()
    # 自动检测异常并回滚
    rolled_back = manager.monitor_and_rollback()
    # 显式回滚
    result = manager.rollback_rule("rule_chatter_v1", "硬约束违反")

本模块为门面：实现已拆分至 _rollback_models / _cooldown_mixin / _detect_mixin / _execute_mixin。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app.dreaming._cooldown_mixin import _CooldownMixin
from app.dreaming._detect_mixin import _DetectMixin
from app.dreaming._execute_mixin import _ExecuteMixin
from app.dreaming._rollback_models import (  # noqa: F401
    DEFAULT_COOLDOWN_HOURS,
    DEFAULT_CONSECUTIVE_ANOMALY_THRESHOLD,
    DEFAULT_PRODUCTION_ERROR_RATE_THRESHOLD,
    ROLLBACK_HISTORY_DIR,
    RollbackDecision,
    RollbackExecutionResult,
)
from app.dreaming.apply_rules import RuleApplicator
from app.dreaming.effectiveness_metrics import EffectivenessMetricsCollector
from app.dreaming.progressive_publisher import ProgressivePublisher

logger = logging.getLogger(__name__)


class RollbackManager(_CooldownMixin, _DetectMixin, _ExecuteMixin):
    """规则回滚管理器。

    负责：
        1. 定期检查所有灰度发布中规则的效果指标
        2. 检测异常：硬约束违反、生产异常、指标连续恶化
        3. 触发自动回滚（通过 ProgressivePublisher.demote 或 RuleApplicator.rollback）
        4. 管理回滚冷却期
        5. 所有回滚决策写入审计日志

    回滚优先级：
        1. 硬约束违反（立即回滚到 DEPRECATED）
        2. 生产异常率超限（立即降级，连续触发则回滚）
        3. 指标连续恶化（连续 N 次低于阈值则回滚）

    线程安全：通过内部锁保护冷却期字典和回滚历史。
    """

    def __init__(
        self,
        history_dir: str | None = None,
        publisher: ProgressivePublisher | None = None,
        applicator: RuleApplicator | None = None,
        metrics_collector: EffectivenessMetricsCollector | None = None,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        consecutive_anomaly_threshold: int = DEFAULT_CONSECUTIVE_ANOMALY_THRESHOLD,
        production_error_rate_threshold: float = DEFAULT_PRODUCTION_ERROR_RATE_THRESHOLD,
    ) -> None:
        """初始化回滚管理器。

        Args:
            history_dir: 回滚历史持久化目录。
            publisher: ProgressivePublisher 实例。
            applicator: RuleApplicator 实例。
            metrics_collector: 效果度量收集器实例。
            cooldown_hours: 冷却期小时数。
            consecutive_anomaly_threshold: 连续异常次数阈值。
            production_error_rate_threshold: 生产异常率阈值。
        """
        self.history_dir = Path(history_dir or ROLLBACK_HISTORY_DIR)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._publisher = publisher
        self._applicator = applicator
        self._metrics_collector = metrics_collector
        self.cooldown_hours = cooldown_hours
        self.consecutive_anomaly_threshold = consecutive_anomaly_threshold
        self.production_error_rate_threshold = production_error_rate_threshold
        self._lock = threading.RLock()
        # rule_id -> 冷却到期时间戳
        self._cooldowns: dict[str, str] = {}
        # rule_id -> 连续异常计数
        self._consecutive_anomalies: dict[str, int] = {}
        # 回滚历史
        self._rollback_history: list[dict[str, Any]] = []
        self._load_history()


# 便捷函数


def rollback_rule(rule_id: str, reason: str) -> RollbackExecutionResult:
    """便捷函数：执行规则回滚。"""
    manager = RollbackManager()
    return manager.rollback_rule(rule_id, reason=reason)


def monitor_and_rollback() -> list[RollbackExecutionResult]:
    """便捷函数：扫描并回滚异常规则。"""
    manager = RollbackManager()
    return manager.monitor_and_rollback()
