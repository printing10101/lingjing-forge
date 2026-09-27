"""Outcomes 反馈闭环：将规则真实效果反馈到置信度与灰度阶段。

Outcomes 反馈机制：
    - 工作中规则触发后产生 Outcome 样本（成功/失败/异常）
    - 闭环收集多源证据，通过 Dempster-Shafer 融合得到聚合置信度
    - 基于 FusionResult 的 confidence + conflict + ds_mass 决策晋级/降级
    - TaskRouter 接收反馈更新 EngineType.RULE 的在线成功率
    - 异常时联动 RollbackManager 自动回滚

本地化实现策略（与 HybridInferenceEngine 解耦）：
    - 自行实例化 DempsterShaferFusion 与 TaskRouter，不复用
      HybridInferenceEngine 内部实例，避免参数耦合
    - 自持 ``Dict[rule_id, Deque[float]]`` 滚动窗口按 rule_id 分别
      追踪效果，因 TaskRouter.update_outcome 的 engine 参数限定为
      EngineType 枚举，无法按 rule_id 细粒度追踪
    - 将规则效果证据包装为 InferenceResult 列表（成功/失败两类），
      调用 DempsterShaferFusion.fuse() 得到 FusionResult
    - 通过 EffectivenessMetricsCollector 获取度量
    - 通过 ProgressivePublisher 执行升级/降级
    - 通过 RollbackManager 处理硬约束违反
    - 所有决策写入审计日志（AIModule.DREAMING）

硬约束对齐：
    - cam_validation_required 始终 True
    - SUCCEEDED 任务禁删
    - HRC52 pending_calibration 强制降低置信度
    - K_s → cutting_force_coeff 直接传递
    - 单轮审核状态机不破坏
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.dreaming._closed_loop_models import (
    CLOSED_LOOP_STATE_DIR,
    DEFAULT_DEMOTE_CONFIDENCE,
    DEFAULT_FUSION_CONFLICT_THRESHOLD,
    DEFAULT_FUSION_MIN_CONFIDENCE,
    DEFAULT_HRC52_CONFIDENCE_PENALTY,
    DEFAULT_MAX_CONFLICT_FOR_PROMOTE,
    DEFAULT_MIN_SAMPLES_FOR_DECISION,
    DEFAULT_PROMOTE_CONFIDENCE,
    DEFAULT_ROUTER_CONFIDENCE_THRESHOLD,
    DEFAULT_RULE_WINDOW_SIZE,
    ClosedLoopDecision,
    RuleOutcomeRecord,
)

from app.dreaming._closed_loop_action_mixin import _ClosedLoopActionMixin
from app.dreaming._closed_loop_persistence_mixin import _ClosedLoopPersistenceMixin
from app.dreaming._closed_loop_rule_mixin import _ClosedLoopRuleMixin

logger = logging.getLogger(__name__)


# 默认参数（12-Factor App：环境变量可覆盖，代码提供合理默认值）


# 闭环决策持久化目录


# 数据结构


# ClosedLoop


class ClosedLoop(_ClosedLoopRuleMixin, _ClosedLoopActionMixin, _ClosedLoopPersistenceMixin):
    """Outcomes 反馈闭环核心。

    用法：
        loop = ClosedLoop()
        # 1. 记录规则触发结果
        loop.record_outcome("rule_xxx", success=True, confidence=0.8)
        # 2. 评估单条规则
        decision = loop.evaluate_rule("rule_xxx")
        # 3. 应用决策（晋级/降级/保持）
        loop.apply_decision(decision)
        # 4. 批量迭代
        results = loop.run_closed_loop_iteration()
    """

    def __init__(
        self,
        window_size: int = DEFAULT_RULE_WINDOW_SIZE,
        promote_confidence: float = DEFAULT_PROMOTE_CONFIDENCE,
        demote_confidence: float = DEFAULT_DEMOTE_CONFIDENCE,
        max_conflict_for_promote: float = DEFAULT_MAX_CONFLICT_FOR_PROMOTE,
        min_samples_for_decision: int = DEFAULT_MIN_SAMPLES_FOR_DECISION,
        hrc52_confidence_penalty: float = DEFAULT_HRC52_CONFIDENCE_PENALTY,
        fusion_conflict_threshold: float = DEFAULT_FUSION_CONFLICT_THRESHOLD,
        fusion_min_confidence: float = DEFAULT_FUSION_MIN_CONFIDENCE,
        router_confidence_threshold: float = DEFAULT_ROUTER_CONFIDENCE_THRESHOLD,
        progressive_publisher: Any | None = None,
        metrics_collector: Any | None = None,
        rollback_manager: Any | None = None,
        state_dir: str | None = None,
    ) -> None:
        """初始化闭环。

        Args:
            window_size: 每条规则的滚动样本窗口大小。
            promote_confidence: 晋级置信度阈值。
            demote_confidence: 降级置信度阈值。
            max_conflict_for_promote: 晋级允许的最大冲突系数。
            min_samples_for_decision: 决策所需的最小样本数。
            hrc52_confidence_penalty: HRC52 pending_calibration 置信度乘子。
            fusion_conflict_threshold: DempsterShaferFusion 冲突阈值。
            fusion_min_confidence: DempsterShaferFusion 最小置信度。
            router_confidence_threshold: TaskRouter 置信度阈值。
            progressive_publisher: ProgressivePublisher 实例。None 表示延迟初始化。
            metrics_collector: EffectivenessMetricsCollector 实例。None 表示延迟初始化。
            rollback_manager: RollbackManager 实例。None 表示延迟初始化。
            state_dir: 决策状态持久化目录。
        """
        self._lock = threading.RLock()
        self._window_size = max(1, int(window_size))
        self._promote_confidence = float(promote_confidence)
        self._demote_confidence = float(demote_confidence)
        self._max_conflict_for_promote = float(max_conflict_for_promote)
        self._min_samples_for_decision = max(1, int(min_samples_for_decision))
        self._hrc52_confidence_penalty = float(hrc52_confidence_penalty)

        # 自持滚动窗口：Dict[rule_id, Deque[RuleOutcomeRecord]]
        self._windows: dict[str, deque[RuleOutcomeRecord]] = {}

        # 延迟初始化的依赖
        self._publisher = progressive_publisher
        self._collector = metrics_collector
        self._rollback_mgr = rollback_manager

        # 自行实例化 DempsterShaferFusion 与 TaskRouter（不复用 HybridInferenceEngine 实例）
        self._fusion: Any | None = None
        self._router: Any | None = None
        self._fusion_params = {
            "conflict_threshold": fusion_conflict_threshold,
            "min_confidence": fusion_min_confidence,
            "enable_conflict_resolution": True,
        }
        self._router_params: dict[str, Any] = {
            "rule_weight": 0.4,
            "ml_weight": 0.6,
            "confidence_threshold": router_confidence_threshold,
            "enable_fallback": True,
            "history_size": 256,
        }

        # 持久化目录
        self._state_dir = Path(state_dir or CLOSED_LOOP_STATE_DIR)
        self._state_dir.mkdir(parents=True, exist_ok=True)

        # 决策历史（内存）
        self._decision_history: dict[str, list[ClosedLoopDecision]] = {}

    # 延迟初始化依赖

    def _get_fusion(self):
        """延迟获取 DempsterShaferFusion 实例。"""
        if self._fusion is None:
            try:
                from app.ai.lnn.fusion import DempsterShaferFusion

                self._fusion = DempsterShaferFusion(**self._fusion_params)
                logger.debug(
                    "ClosedLoop: DempsterShaferFusion 已实例化 params=%s",
                    self._fusion_params,
                )
            except Exception as e:
                logger.warning(
                    "ClosedLoop: DempsterShaferFusion 初始化失败，将退化为加权平均：%s",
                    e,
                )
                self._fusion = None
        return self._fusion

    def _get_router(self):
        """延迟获取 TaskRouter 实例。"""
        if self._router is None:
            try:
                from app.ai.lnn.router.task_router import TaskRouter

                self._router = TaskRouter(**self._router_params)
                logger.debug(
                    "ClosedLoop: TaskRouter 已实例化 params=%s",
                    self._router_params,
                )
            except Exception as e:
                logger.warning(
                    "ClosedLoop: TaskRouter 初始化失败，router 反馈将跳过：%s",
                    e,
                )
                self._router = None
        return self._router

    def _get_publisher(self):
        """延迟获取 ProgressivePublisher 实例。"""
        if self._publisher is None:
            try:
                from app.dreaming.progressive_publisher import (
                    ProgressivePublisher,
                )

                self._publisher = ProgressivePublisher()
            except Exception as e:
                logger.warning("ClosedLoop: ProgressivePublisher 初始化失败：%s", e)
                self._publisher = None
        return self._publisher

    def _get_collector(self):
        """延迟获取 EffectivenessMetricsCollector 实例。"""
        if self._collector is None:
            try:
                from app.dreaming.effectiveness_metrics import (
                    EffectivenessMetricsCollector,
                )

                self._collector = EffectivenessMetricsCollector()
            except Exception as e:
                logger.warning(
                    "ClosedLoop: EffectivenessMetricsCollector 初始化失败：%s",
                    e,
                )
                self._collector = None
        return self._collector

    def _get_rollback_manager(self):
        """延迟获取 RollbackManager 实例。"""
        if self._rollback_mgr is None:
            try:
                from app.dreaming.rollback_manager import RollbackManager

                self._rollback_mgr = RollbackManager(
                    publisher=self._get_publisher(),
                )
            except Exception as e:
                logger.warning("ClosedLoop: RollbackManager 初始化失败：%s", e)
                self._rollback_mgr = None
        return self._rollback_mgr

    def _get_audit_recorder(self):
        """延迟获取审计记录器。"""
        try:
            from app.dreaming.audit_integration import get_audit_recorder

            return get_audit_recorder()
        except Exception as e:
            logger.warning("ClosedLoop: 审计记录器获取失败：%s", e)
            return None

    # 1. 记录规则触发结果

    def record_outcome(
        self,
        rule_id: str,
        success: bool,
        confidence: float,
        source: str = "manual",
    ) -> None:
        """记录一次规则触发的结果。

        Args:
            rule_id: 规则 ID。
            success: 是否成功。
            confidence: 触发时的置信度（0.0-1.0）。
            source: 来源标签。
        """
        if not rule_id:
            return
        confidence = max(0.0, min(1.0, float(confidence)))

        with self._lock:
            if rule_id not in self._windows:
                self._windows[rule_id] = deque(maxlen=self._window_size)
            record = RuleOutcomeRecord(
                rule_id=rule_id,
                success=success,
                confidence=confidence,
                source=source,
            )
            self._windows[rule_id].append(record)

        # 同步反馈到 TaskRouter（EngineType.RULE 的全局在线成功率）
        router = self._get_router()
        if router is not None:
            try:
                from app.ai.lnn.core import EngineType

                router.update_outcome(
                    engine=EngineType.RULE,
                    success=success,
                    confidence=confidence,
                )
            except Exception as e:
                logger.debug(
                    "ClosedLoop: TaskRouter.update_outcome 失败（忽略）：%s",
                    e,
                )

        # 同步录入 EffectivenessMetricsCollector（便于统一度量）
        collector = self._get_collector()
        if collector is not None:
            try:
                from app.dreaming.effectiveness_metrics import (
                    OutcomeSample,
                )

                sample = OutcomeSample(
                    rule_id=rule_id,
                    triggered_at=datetime.now(timezone.utc).isoformat(),
                    correct=success,
                    false_positive=(not success),
                    false_negative=False,
                    production_error=False,
                    cam_validation_bypassed=False,
                    succeeded_lock_violated=False,
                    source=source,
                )
                collector.record_sample(sample)
            except Exception as e:
                logger.debug(
                    "ClosedLoop: EffectivenessMetricsCollector 录入失败（忽略）：%s",
                    e,
                )

    # 2. 评估规则

    # 3. 应用决策

    def _record_decision_to_audit(self, decision: ClosedLoopDecision) -> None:
        """将闭环决策写入审计日志。"""
        recorder = self._get_audit_recorder()
        if recorder is None:
            return
        try:
            # DreamingAuditRecorder 提供 record_rule_application 方法
            recorder.record_rule_application(
                rule_id=decision.rule_id,
                rule_description=(f"闭环决策 action={decision.action} reason={decision.reason}"),
                validation_passed=True,
                applied=decision.applied,
                rollback_triggered=(decision.action == "rollback"),
            )
        except Exception as e:
            logger.debug("ClosedLoop: 审计记录失败（忽略）：%s", e)

    # 4. 批量迭代

    def run_closed_loop_iteration(
        self,
        rule_ids: list[str] | None = None,
        apply: bool = True,
    ) -> list[ClosedLoopDecision]:
        """执行一次闭环迭代。

        Args:
            rule_ids: 指定评估的规则 ID 列表。None 表示评估所有有样本的规则。
            apply: 是否自动应用决策。False 表示仅评估不应用。

        Returns:
            决策结果列表。
        """
        if rule_ids is None:
            with self._lock:
                rule_ids = list(self._windows.keys())

        if not rule_ids:
            logger.info("ClosedLoop: 无可评估规则，迭代结束")
            return []

        decisions: list[ClosedLoopDecision] = []
        for rule_id in rule_ids:
            try:
                decision = self.evaluate_rule(rule_id)
                if apply:
                    self.apply_decision(decision)
                decisions.append(decision)

                # 记录到历史
                with self._lock:
                    if rule_id not in self._decision_history:
                        self._decision_history[rule_id] = []
                    self._decision_history[rule_id].append(decision)
            except Exception as e:
                logger.error(
                    "ClosedLoop: 评估 rule=%s 失败：%s",
                    rule_id,
                    e,
                    exc_info=True,
                )

        # 持久化本次迭代结果
        self._persist_iteration(decisions)

        logger.info(
            "ClosedLoop: 迭代完成，共 %d 条决策（promote=%d, demote=%d, keep=%d, rollback=%d）",
            len(decisions),
            sum(1 for d in decisions if d.action == "promote"),
            sum(1 for d in decisions if d.action == "demote"),
            sum(1 for d in decisions if d.action == "keep"),
            sum(1 for d in decisions if d.action == "rollback"),
        )
        return decisions


# 5. 查询接口

# 6. 持久化与恢复


# 便捷函数
