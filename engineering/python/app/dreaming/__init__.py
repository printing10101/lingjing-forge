"""Dreaming 离线反思模块（ADR-021）。

本模块为"灵境制造"项目实现离线反思（Dreaming）机制，
仿生神经科学的"记忆巩固"理论：

    Memory（工作中学习） + Dreaming（休息时反思） + Outcomes（自检）
    => 完整的自我改进闭环

核心组件：
    - LocalMemoryStore：基于 GraphStore + Git 的本地 Memory Store
    - SessionExtractor：从 MLflow / CAM / Audit / CuttingStore 提取 Session
    - DreamReflector：离线反思核心（去重 / 更新 / 洞察浮现）
    - RuleSynthesizer：将洞察转化为可执行规则
    - ReportGenerator：生成 Markdown 反思报告
    - DreamingCLI：命令行入口

P1 阶段新增：
    - DreamingAuditRecorder：反思决策写入审计日志哈希链
    - DreamingSchedulerAdapter：HeartbeatScheduler 定时反思集成
    - RuleValidator：规则草稿沙箱验证器
    - RuleApplicator：规则应用入口（含回滚）

P2 阶段新增（Outcomes 反馈闭环）：
    - ProgressivePublisher：规则灰度发布（shadow→canary→rolling→full）
    - EffectivenessMetricsCollector：规则效果度量（准确率/召回率/误报率）
    - RollbackManager：异常检测与自动回滚（含冷却期管理）
    - ClosedLoop：Outcomes 反馈闭环核心（DempsterShaferFusion + TaskRouter）

硬约束（不可绕过）：
    - cam_validation_required 始终 True
    - SUCCEEDED 任务禁删
    - HRC52 pending_calibration 强制降低置信度
    - 单轮审核状态机（PARAMS_RECOMMENDED → REVIEWED → SUCCEEDED）
    - 所有反思决策写入 audit_log 哈希链
    - K_s → cutting_force_coeff 直接传递（不二次拟合）
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

__version__ = "0.1.0"

__all__ = [
    # P0 阶段
    "LocalMemoryStore",
    "SessionExtractor",
    "DreamReflector",
    "RuleSynthesizer",
    "ReportGenerator",
    "DreamingCLI",
    # P1 阶段
    "DreamingAuditRecorder",
    "DreamingSchedulerAdapter",
    "RuleValidator",
    "RuleApplicator",
    # P2 阶段
    "ProgressivePublisher",
    "EffectivenessMetricsCollector",
    "RollbackManager",
    "ClosedLoop",
    "ClosedLoopDecision",
    "RuleOutcomeRecord",
    # 便捷函数
    "get_audit_recorder",
    "register_default_dreaming_task",
    "unregister_dreaming_task",
    "validate_rule",
    "apply_validated_rules",
    "publish_rule",
    "promote_rule",
    "demote_rule",
    "collect_rule_metrics",
    "record_outcome_sample",
    "rollback_rule",
    "monitor_and_rollback",
    "run_closed_loop",
    "record_rule_outcome",
    "__version__",
]


def __getattr__(name: str):  # PEP 562 延迟导入
    # P0 阶段
    if name == "LocalMemoryStore":
        from app.dreaming.memory_store import LocalMemoryStore

        return LocalMemoryStore
    if name == "SessionExtractor":
        from app.dreaming.session_extractor import SessionExtractor

        return SessionExtractor
    if name == "DreamReflector":
        from app.dreaming.reflector import DreamReflector

        return DreamReflector
    if name == "RuleSynthesizer":
        from app.dreaming.rule_synthesizer import RuleSynthesizer

        return RuleSynthesizer
    if name == "ReportGenerator":
        from app.dreaming.report_generator import ReportGenerator

        return ReportGenerator
    if name == "DreamingCLI":
        from app.dreaming.cli import DreamingCLI

        return DreamingCLI
    # P1 阶段
    if name == "DreamingAuditRecorder":
        from app.dreaming.audit_integration import DreamingAuditRecorder

        return DreamingAuditRecorder
    if name == "DreamingSchedulerAdapter":
        from app.dreaming.scheduler_adapter import DreamingSchedulerAdapter

        return DreamingSchedulerAdapter
    if name == "RuleValidator":
        from app.dreaming.rule_validator import RuleValidator

        return RuleValidator
    if name == "RuleApplicator":
        from app.dreaming.apply_rules import RuleApplicator

        return RuleApplicator
    # P2 阶段
    if name == "ProgressivePublisher":
        from app.dreaming.progressive_publisher import ProgressivePublisher

        return ProgressivePublisher
    if name == "EffectivenessMetricsCollector":
        from app.dreaming.effectiveness_metrics import (
            EffectivenessMetricsCollector,
        )

        return EffectivenessMetricsCollector
    if name == "RollbackManager":
        from app.dreaming.rollback_manager import RollbackManager

        return RollbackManager
    if name == "ClosedLoop":
        from app.dreaming.closed_loop import ClosedLoop

        return ClosedLoop
    if name == "ClosedLoopDecision":
        from app.dreaming._closed_loop_models import ClosedLoopDecision

        return ClosedLoopDecision
    if name == "RuleOutcomeRecord":
        from app.dreaming._closed_loop_models import RuleOutcomeRecord

        return RuleOutcomeRecord
    # 便捷函数
    if name == "get_audit_recorder":
        from app.dreaming.audit_integration import get_audit_recorder

        return get_audit_recorder
    if name == "register_default_dreaming_task":
        from app.dreaming.scheduler_adapter import register_default_dreaming_task

        return register_default_dreaming_task
    if name == "unregister_dreaming_task":
        from app.dreaming.scheduler_adapter import unregister_dreaming_task

        return unregister_dreaming_task
    if name == "validate_rule":
        from app.dreaming.rule_validator import validate_rule

        return validate_rule
    if name == "apply_validated_rules":
        from app.dreaming.apply_rules import apply_validated_rules

        return apply_validated_rules
    if name == "publish_rule":
        from app.dreaming.progressive_publisher import publish_rule

        return publish_rule
    if name == "promote_rule":
        from app.dreaming.progressive_publisher import promote_rule

        return promote_rule
    if name == "demote_rule":
        from app.dreaming.progressive_publisher import demote_rule

        return demote_rule
    if name == "collect_rule_metrics":
        from app.dreaming.effectiveness_metrics import collect_rule_metrics

        return collect_rule_metrics
    if name == "record_outcome_sample":
        from app.dreaming.effectiveness_metrics import record_outcome_sample

        return record_outcome_sample
    if name == "rollback_rule":
        from app.dreaming.rollback_manager import rollback_rule

        return rollback_rule
    if name == "monitor_and_rollback":
        from app.dreaming.rollback_manager import monitor_and_rollback

        return monitor_and_rollback
    if name == "run_closed_loop":
        from app.dreaming._closed_loop_models import run_closed_loop

        return run_closed_loop
    if name == "record_rule_outcome":
        from app.dreaming._closed_loop_models import record_rule_outcome

        return record_rule_outcome
    raise AttributeError(f"module 'app.dreaming' has no attribute {name!r}")
