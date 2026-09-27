"""规则灰度发布管理器（Progressive Publisher）。

规则按灰度阶段逐步放量（shadow → canary → full）：
    - 通过沙箱验证的规则不直接全量应用，而是按 1% → 10% → 50% → 100% 四级灰度发布
    - 每个阶段收集效果指标（effectiveness_metrics.py），决定晋级（promote）或降级（demote）
    - 异常指标触发自动回滚（rollback_manager.py）
    - 所有发布决策写入审计日志（AIModule.DREAMING）

灰度阶段（PublicationStage）：
    - shadow（影子模式，0% 流量）：仅记录规则触发日志，不实际执行动作
    - canary（金丝雀，1% 流量）：在真实流量上小范围执行，监控关键指标
    - rolling（滚动发布，10% → 50%）：逐步扩大流量比例
    - full（全量发布，100%）：规则完全生效

设计原则：
    - 灰度发布不绕过硬约束：CAM 二次验证始终 True、SUCCEEDED 禁删
    - 每次晋级前重新调用 RuleValidator 双重校验
    - 灰度比例通过 config 控制（DreamingConfig），可被环境变量覆盖
    - shadow 阶段不修改生产数据，仅记录"如果应用会发生什么"
    - 异常自动降级到上一阶段，连续两次降级触发回滚

硬约束对齐：
    - cam_validation_required 始终 True（不被灰度发布绕过）
    - allow_delete_succeeded 始终 False
    - K_s → cutting_force_coeff 直接传递
    - HRC52 pending_calibration 在 canary 阶段强制降低置信度

用法：
    publisher = ProgressivePublisher()
    result = publisher.publish(rule, PublicationStage.SHADOW)
    if result.success and metrics_good:
        publisher.promote(rule.rule_id, target_stage=PublicationStage.CANARY)
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path


from app.dreaming.apply_rules import (
    RuleApplicator,
)
from app.dreaming.rule_synthesizer import RuleDraft
from app.dreaming.rule_validator import RuleValidator
from app.dreaming._publisher_persist_mixin import _PublisherPersistMixin
from app.dreaming._publisher_publish_mixin import _PublisherPublishMixin
from app.dreaming._publisher_promote_mixin import _PublisherPromoteMixin
from app.dreaming._publisher_demote_mixin import _PublisherDemoteMixin
from app.dreaming._publisher_models import (
    PublicationStage,
    PublicationRecord,
    PublicationResult,
)

logger = logging.getLogger(__name__)


# 灰度阶段定义


# 灰度阶段常量（_STAGE_ORDER / _STAGE_TRAFFIC_PERCENTAGE）已迁至 _publisher_models.py


# 灰度发布状态


# 灰度发布管理器


# 灰度状态持久化目录
PUBLICATION_STATE_DIR = "python/outputs/dreaming/publication_state"

# 晋级阈值：效果指标达标才允许晋级
# 这些阈值对应 effectiveness_metrics.py 中的指标
DEFAULT_PROMOTION_THRESHOLDS: dict[str, float] = {
    "min_accuracy": 0.70,  # 准确率下限
    "max_false_positive_rate": 0.20,  # 误报率上限
    "min_sample_size": 20,  # 最小样本数（canary 阶段必须有足够样本）
    "max_error_rate": 0.10,  # 错误率上限
}

# 降级阈值：指标恶化时自动降级
DEFAULT_DEMOTION_THRESHOLDS: dict[str, float] = {
    "max_false_positive_rate": 0.40,  # 误报率超过 40% 降级
    "max_error_rate": 0.25,  # 错误率超过 25% 降级
    "min_accuracy": 0.50,  # 准确率低于 50% 降级
}


class ProgressivePublisher(
    _PublisherPersistMixin, _PublisherPublishMixin, _PublisherPromoteMixin, _PublisherDemoteMixin
):
    """规则灰度发布管理器。

    负责：
        1. 接收通过验证的规则，进入 SHADOW 阶段
        2. 根据 effectiveness_metrics 指标决定晋级或降级
        3. 晋级前重新调用 RuleValidator 双重校验
        4. 全量发布（FULL）后调用 RuleApplicator.apply 真正应用到知识图谱
        5. 异常指标自动降级，连续降级到 SHADOW 后触发回滚
        6. 所有发布决策写入审计日志

    线程安全：通过内部锁保护 _records 字典。
    """

    def __init__(
        self,
        state_dir: str | None = None,
        applicator: RuleApplicator | None = None,
        validator: RuleValidator | None = None,
        promotion_thresholds: dict[str, float] | None = None,
        demotion_thresholds: dict[str, float] | None = None,
    ) -> None:
        """初始化灰度发布管理器。

        Args:
            state_dir: 灰度状态持久化目录。
            applicator: RuleApplicator 实例（None 则按需创建）。
            validator: RuleValidator 实例（None 则按需创建）。
            promotion_thresholds: 晋级阈值覆盖。
            demotion_thresholds: 降级阈值覆盖。
        """
        self.state_dir = Path(state_dir or PUBLICATION_STATE_DIR)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._applicator = applicator
        self._validator = validator or RuleValidator()
        self._lock = threading.RLock()
        self._records: dict[str, PublicationRecord] = {}
        self._promotion_thresholds = dict(promotion_thresholds or DEFAULT_PROMOTION_THRESHOLDS)
        self._demotion_thresholds = dict(demotion_thresholds or DEFAULT_DEMOTION_THRESHOLDS)
        self._load_state()

    # 延迟依赖

    def _get_applicator(self) -> RuleApplicator:
        if self._applicator is None:
            self._applicator = RuleApplicator()
        return self._applicator

    def _get_audit_recorder(self):
        from app.dreaming.audit_integration import get_audit_recorder

        return get_audit_recorder()


# 状态持久化

# 公开 API

# 内部辅助


# 便捷函数


def publish_rule(
    rule: RuleDraft,
    stage: PublicationStage = PublicationStage.SHADOW,
) -> PublicationResult:
    """便捷函数：发布规则到指定灰度阶段。"""
    publisher = ProgressivePublisher()
    return publisher.publish(rule, stage=stage)


def promote_rule(
    rule_id: str,
    target_stage: PublicationStage | None = None,
) -> PublicationResult:
    """便捷函数：晋级规则。"""
    publisher = ProgressivePublisher()
    return publisher.promote(rule_id, target_stage=target_stage)


def demote_rule(rule_id: str, reason: str) -> PublicationResult:
    """便捷函数：降级规则。"""
    publisher = ProgressivePublisher()
    return publisher.demote(rule_id, reason=reason)
