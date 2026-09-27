"""
审计日志写入模块 - append-only 日志写入与哈希链接入。

本模块为 P1-5 重构从原 ``audit_log.py`` 拆分而来，提供 ``WriterMixin``：
- ``_get_current_log_file``：按日期分目录定位当日审计日志文件
- ``log_decision``：AI 决策类审计日志写入
- ``log_security_event``：安全事件类审计日志写入（P0-17 修复）

写入流程：
1. 构造 ``AuditLogEntry``
2. 在 ``_chain_lock`` 保护下填充 ``chain_seq`` / ``prev_hash`` / ``entry_hash``
3. append-only 写入当日日志文件
4. 更新链状态并按需触发归档轮转

依赖 ``ChainMixin``（``_compute_entry_hash`` / ``_save_chain_state``）与
``ArchiverMixin``（``_rotate_if_needed``），通过 ``AuditLog`` 多重继承组合。
"""

import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Callable

from app.audit.chain import (
    AuditLogEntry,
    AIModule,
    UserDecision,
    OperationStatus,
)

# 保留原 audit_log 模块的 logger 名称，避免日志配置因重构失效
logger = logging.getLogger("app.audit.audit_log")


class WriterMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _compute_entry_hash: Callable[..., Any]
    _rotate_if_needed: Callable[..., Any]
    _save_chain_state: Callable[..., Any]
    _chain_lock: Any
    _chain_seq: Any
    _last_hash: Any
    _log_root: Any

    """审计日志写入 mixin。

    依赖 ``AuditLog`` 实例的以下属性（由 ``AuditLog.__init__`` 初始化）：
    ``_log_root`` / ``_chain_lock`` / ``_last_hash`` / ``_chain_seq``。

    跨 mixin 调用：
    - ``self._compute_entry_hash`` / ``self._save_chain_state``：来自 ``ChainMixin``
    - ``self._rotate_if_needed``：来自 ``ArchiverMixin``
    """

    # 日志写入

    def _get_current_log_file(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        date_dir = self._log_root / today
        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir / "audit.log"

    def log_decision(
        self,
        ai_module: AIModule,
        ai_recommendation: dict,
        user_decision: UserDecision,
        final_execution: dict,
        operation_status: OperationStatus,
        user_id: str | None = None,
        username: str | None = None,
        input_parameters: dict | None = None,
        confidence: float | None = None,
        reasoning: str | None = None,
        user_modifications: dict | None = None,
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        entry = AuditLogEntry(
            timestamp_ms=int(time.time() * 1000),
            ai_module=ai_module.value,
            ai_recommendation=ai_recommendation,
            user_decision=user_decision.value,
            final_execution=final_execution,
            operation_status=operation_status.value,
            user_id=user_id,
            username=username,
            input_parameters=input_parameters or {},
            confidence=confidence,
            reasoning=reasoning,
            user_modifications=user_modifications,
            metadata=metadata,
        )

        # 填充哈希链字段并写入（加锁保证 chain_seq 单调递增与哈希链连续）
        with self._chain_lock:
            entry.chain_seq = self._chain_seq
            entry.prev_hash = self._last_hash
            entry.entry_hash = self._compute_entry_hash(entry, self._last_hash)

            try:
                log_file = self._get_current_log_file()
                # append-only 写入
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

                # 写入成功后更新链状态
                self._last_hash = entry.entry_hash
                self._chain_seq += 1
                self._save_chain_state()

                self._rotate_if_needed(log_file)

                logger.info(
                    "Audit log entry created: module=%s, decision=%s, status=%s, chain_seq=%d",
                    ai_module.value,
                    user_decision.value,
                    operation_status.value,
                    entry.chain_seq,
                )

            except (OSError, IOError, PermissionError) as e:
                # 不再静默吞异常，抛出 RuntimeError 让调用方感知
                logger.error("Failed to write audit log: %s", e, exc_info=True)
                raise RuntimeError(f"Failed to write audit log entry (chain_seq={entry.chain_seq}): {e}") from e

        return entry

    def log_security_event(
        self,
        event_type: str,
        operation_status: OperationStatus = OperationStatus.SUCCESS,
        user_id: str | None = None,
        username: str | None = None,
        input_parameters: dict | None = None,
        metadata: dict | None = None,
    ) -> AuditLogEntry:
        """记录安全相关事件（登录/登出/令牌刷新/权限变更等）。

        P0-17 修复：与 ``log_decision`` 不同，本方法不绑定特定 ``AIModule``
        枚举值，用于认证、授权、令牌管理等非 AI 决策类安全事件的审计
        追踪。所有登录/登出事件必须通过本方法写入哈希链审计日志。

        合规依据：
        - FDA 21 CFR Part 11 §11.10(d)：要求记录系统访问控制事件
        - SOC 2 CC6.1：要求逻辑访问控制事件被记录且可追溯
        - ISO 27001 A.9.2.5：要求用户访问日志保留

        Args:
            event_type: 事件类型字符串（如 "auth_login"/"auth_logout"/
                "auth_refresh"/"auth_register"），写入 ``ai_module`` 字段
            operation_status: 事件结果（SUCCESS/FAILED/CANCELLED/PENDING）
            user_id: 用户标识
            username: 用户名
            input_parameters: 输入参数（已脱敏，不含密码等敏感字段）
            metadata: 额外元数据（如 IP、User-Agent、request_id）

        Returns:
            已写入的 AuditLogEntry（含哈希链字段）

        Raises:
            RuntimeError: 写入审计日志失败
        """
        entry = AuditLogEntry(
            timestamp_ms=int(time.time() * 1000),
            ai_module=event_type,  # 字符串形式，非 AIModule 枚举
            ai_recommendation={},  # 非决策类事件，无 AI 推荐
            user_decision=UserDecision.AUTO_EXECUTED.value,
            final_execution={
                "event_type": event_type,
                "status": operation_status.value,
            },
            operation_status=operation_status.value,
            user_id=user_id,
            username=username,
            input_parameters=input_parameters or {},
            metadata=metadata or {},
        )

        # 填充哈希链字段并写入（加锁保证 chain_seq 单调递增与哈希链连续）
        with self._chain_lock:
            entry.chain_seq = self._chain_seq
            entry.prev_hash = self._last_hash
            entry.entry_hash = self._compute_entry_hash(entry, self._last_hash)

            try:
                log_file = self._get_current_log_file()
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

                self._last_hash = entry.entry_hash
                self._chain_seq += 1
                self._save_chain_state()
                self._rotate_if_needed(log_file)

                logger.info(
                    "Security audit event: type=%s, status=%s, chain_seq=%d",
                    event_type,
                    operation_status.value,
                    entry.chain_seq,
                )
            except (OSError, IOError, PermissionError) as e:
                logger.error("Failed to write security audit log: %s", e, exc_info=True)
                raise RuntimeError(f"Failed to write security audit log (chain_seq={entry.chain_seq}): {e}") from e

        return entry
