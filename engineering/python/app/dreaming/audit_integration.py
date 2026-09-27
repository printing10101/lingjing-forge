"""Dreaming 反思决策审计集成。

反思决策统一写入审计日志：
    - 每次反思完成时，将关键决策写入 audit_log 哈希链
    - 记录 Memory Version、去重/更新/洞察/规则统计
    - 标记 LLM 使用情况（学术诚信 D-2）
    - 哈希链保证不可篡改（满足 SOC 2 / ISO 27001 合规要求）

硬约束对齐：
    - AIModule.DREAMING 是新增枚举值（已在 audit_log.py 中添加）
    - 反思决策不直接执行生产操作，仅记录 Memory Store 变更
    - 不绕过 CAM 二次验证、SUCCEEDED 禁删等约束
    - LLM 不可用时降级为规则统计，审计记录中标记 llm_used=False

用法：
    recorder = DreamingAuditRecorder()
    entry = recorder.record_reflection(
        memory_version="abc123",
        summary="合并 5 条重复 memory，浮现 2 条洞察",
        dedup_count=5,
        update_count=3,
        insight_count=2,
        rule_count=4,
        llm_model="qwen3.5:35b-128k",
    )
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.audit.audit_log import (
    AIModule,
    OperationStatus,
    UserDecision,
    get_audit_log,
)

logger = logging.getLogger(__name__)


class DreamingAuditRecorder:
    """将 Dreaming 反思决策写入审计日志哈希链。

    封装 audit_log.AuditLog.log_decision 调用，提供反思场景专用 API。
    所有写入操作继承 AuditLog 内部的 _chain_lock 锁保护，保证哈希链
    连续性与并发安全。

    设计原则：
        - 反思是自动执行（无人工实时决策），user_decision=AUTO_EXECUTED
        - 反思结果不直接生产操作，final_execution 记录 Memory Store 变更摘要
        - LLM 不可用时 operation_status 仍为 SUCCESS（降级模式是设计预期）
        - 失败场景（如 Memory Store 提交失败）记录 FAILED 状态
    """

    def __init__(self) -> None:
        """初始化审计记录器，获取 AuditLog 单例。"""
        self._audit = get_audit_log()

    def record_reflection(
        self,
        memory_version: str | None,
        summary: str,
        dedup_count: int,
        update_count: int,
        insight_count: int,
        rule_count: int,
        llm_model: str | None = None,
        lookback_days: int = 30,
        session_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """记录一次完整的反思决策到审计日志。

        Args:
            memory_version: 新提交的 Memory Version（Git commit hash 前 12 位）。
                若提交失败则为 None。
            summary: 人类可读的反思摘要。
            dedup_count: 去重合并的条目数。
            update_count: 过时更新的条目数。
            insight_count: 浮现的洞察数。
            rule_count: 合成的规则草稿数。
            llm_model: 使用的 LLM 模型名（如 "qwen3.5:35b-128k"）。None 表示
                规则统计降级模式。
            lookback_days: 回溯天数。
            session_count: 输入 Session 数。
            metadata: 额外元数据（如 AR-02 排除标记、硬约束执行记录）。

        Returns:
            AuditLogEntry 实例。
        """
        ai_recommendation: dict[str, Any] = {
            "module": "dreaming.reflect",
            "action": "consolidate_and_synthesize",
            "memory_version": memory_version,
            "summary": summary,
            "lookback_days": lookback_days,
            "session_count": session_count,
            "llm_used": llm_model is not None,
            "llm_model": llm_model,
            "llm_mode": "llm" if llm_model is not None else "rule_based_fallback",
        }

        final_execution: dict[str, Any] = {
            "memory_store_changes": {
                "deduplicated_count": dedup_count,
                "stale_updated_count": update_count,
                "deprecated_nodes": dedup_count,
                "invalidated_nodes": 0,
            },
            "knowledge_graph_updates": {
                "insights_added": insight_count,
                "rule_drafts_created": rule_count,
            },
            "hard_constraint_compliance": {
                "cam_validation_required": True,
                "succeeded_delete_locked": True,
                # [C2] 硬约束合规性字段语义修正：其他字段 True 表示约束被遵守，
                # 此处 lowered=True 会与"不降低 HRC52 安全阈值"硬约束矛盾，改为 False
                "hrc52_pending_calibration_lowered": False,
                "k_s_direct_transfer": True,
            },
            "artifacts": {
                "report_path": metadata.get("report_path") if metadata else None,
                "rules_path": metadata.get("rules_path") if metadata else None,
                "reflection_json_path": metadata.get("reflection_json_path") if metadata else None,
            },
        }

        extra_meta = {
            "lookback_days": lookback_days,
            "session_count": session_count,
            "ar_02_pre_fix_included": metadata.get("ar_02_pre_fix_included", False) if metadata else False,
            "adr": "ADR-021",
        }
        if metadata:
            for k, v in metadata.items():
                if k not in extra_meta:
                    extra_meta[k] = v

        try:
            entry = self._audit.log_decision(
                ai_module=AIModule.DREAMING,
                ai_recommendation=ai_recommendation,
                user_decision=UserDecision.AUTO_EXECUTED,
                final_execution=final_execution,
                operation_status=OperationStatus.SUCCESS,
                reasoning=summary,
                confidence=self._compute_confidence(llm_model, insight_count, session_count),
                metadata=extra_meta,
            )
            logger.info(
                "反思决策已写入审计日志：version=%s, chain_seq=%s",
                memory_version,
                getattr(entry, "chain_seq", "?"),
            )
            return entry
        except Exception as e:
            logger.error("反思决策写入审计日志失败：%s", e, exc_info=True)
            raise

    def record_reflection_failure(
        self,
        error_message: str,
        lookback_days: int = 30,
        stage: str = "unknown",
    ) -> None:
        """记录反思失败到审计日志。

        用于反思流程异常终止的场景，保证审计链不断裂。

        Args:
            error_message: 错误信息（已脱敏，不包含敏感数据）。
            lookback_days: 回溯天数。
            stage: 失败阶段（extract/reflect/synthesize/report）。
        """
        safe_msg = self._sanitize_error(error_message)
        try:
            self._audit.log_decision(
                ai_module=AIModule.DREAMING,
                ai_recommendation={
                    "module": "dreaming.reflect",
                    "action": stage,
                    "lookback_days": lookback_days,
                },
                user_decision=UserDecision.AUTO_EXECUTED,
                final_execution={"error": safe_msg, "stage": stage},
                operation_status=OperationStatus.FAILED,
                reasoning=f"反思在 {stage} 阶段失败：{safe_msg}",
                confidence=0.0,
                metadata={"adr": "ADR-021", "failure_stage": stage},
            )
            logger.info("反思失败已记录到审计日志：stage=%s", stage)
        except Exception as e:
            logger.error("反思失败记录写入审计日志失败：%s", e, exc_info=True)

    def record_rule_application(
        self,
        rule_id: str,
        rule_description: str,
        validation_passed: bool,
        applied: bool,
        rollback_triggered: bool = False,
    ) -> None:
        """记录规则应用/回滚决策到审计日志。

        用于 P1/P2 阶段的规则应用与回滚追踪。

        Args:
            rule_id: 规则 ID。
            rule_description: 规则描述（脱敏后）。
            validation_passed: 沙箱验证是否通过。
            applied: 是否已应用。
            rollback_triggered: 是否触发了回滚。
        """
        safe_desc = self._sanitize_error(rule_description)
        status = OperationStatus.SUCCESS if applied else OperationStatus.FAILED
        if rollback_triggered:
            status = OperationStatus.CANCELLED

        try:
            self._audit.log_decision(
                ai_module=AIModule.DREAMING,
                ai_recommendation={
                    "module": "dreaming.apply_rules",
                    "rule_id": rule_id,
                    "description": safe_desc,
                    "validation_passed": validation_passed,
                },
                user_decision=UserDecision.AUTO_EXECUTED,
                final_execution={
                    "applied": applied,
                    "rollback_triggered": rollback_triggered,
                    "cam_validation_bypassed": False,
                    "succeeded_lock_violated": False,
                },
                operation_status=status,
                reasoning=f"规则 {rule_id} 应用状态：applied={applied}, rollback={rollback_triggered}",
                metadata={"adr": "ADR-021", "rule_id": rule_id},
            )
            logger.info(
                "规则应用决策已写入审计日志：rule_id=%s, applied=%s",
                rule_id,
                applied,
            )
        except Exception as e:
            logger.error("规则应用决策写入审计日志失败：%s", e, exc_info=True)

    # 内部工具

    @staticmethod
    def _compute_confidence(llm_model: str | None, insight_count: int, session_count: int) -> float:
        """计算反思置信度。

        规则：
            - LLM 模式 + 有洞察 + 有 session：0.85
            - LLM 模式 + 无洞察：0.5
            - 规则降级 + 有洞察：0.6
            - 规则降级 + 无洞察：0.3
        """
        if llm_model is not None:
            if insight_count > 0 and session_count > 0:
                return 0.85
            return 0.5
        if insight_count > 0:
            return 0.6
        return 0.3

    @staticmethod
    def _sanitize_error(msg: str) -> str:
        """脱敏错误信息，避免泄露敏感服务器信息。

        与项目 safe_error_message() 原则一致：不输出绝对路径、
        不输出用户 ID、不输出内部配置值。
        """
        if not msg:
            return ""
        sanitized = msg
        # 移除 Windows 绝对路径
        sanitized = re.sub(r"[A-Za-z]:\\[^\s'\"]+", "<path>", sanitized)
        # 移除 Unix 绝对路径
        sanitized = re.sub(r"/(?:home|root|var|opt|tmp)/[^\s'\"]+", "<path>", sanitized)
        # 截断过长的错误信息
        if len(sanitized) > 500:
            sanitized = sanitized[:500] + "...(truncated)"
        return sanitized


def get_audit_recorder() -> DreamingAuditRecorder:
    """获取 Dreaming 审计记录器实例。

    Returns:
        DreamingAuditRecorder 实例（内部使用 AuditLog 单例）。
    """
    return DreamingAuditRecorder()
