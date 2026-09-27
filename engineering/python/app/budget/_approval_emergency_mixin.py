"""_ApprovalEmergencyMixin (split from ApprovalWorkflowEngine)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from collections.abc import Callable


logger = logging.getLogger(__name__)


class _ApprovalEmergencyMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _emergency_audit_callback: Any
    _log_audit: Callable[..., Any]
    _conn: Any
    _consecutive_emergency_count: Any
    _emergency_threshold: Any

    def record_emergency_operation(
        self,
        request_id: str,
        task_id: str,
        operator_id: str,
        reason: str,
        emergency_type: str,
    ) -> dict[str, Any]:
        """记录紧急操作"""
        now = time.time()
        emergency_id = f"EMG-{uuid.uuid4().hex[:8].upper()}"

        self._conn.execute(
            """INSERT INTO emergency_operations
               (id, request_id, task_id, operator_id, reason, emergency_type,
                executed_at, retroactive_approval_required, retroactive_approval_completed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?)""",
            (
                emergency_id,
                request_id,
                task_id,
                operator_id,
                reason,
                emergency_type,
                now,
                now,
            ),
        )
        self._conn.commit()

        self._consecutive_emergency_count += 1
        if self._consecutive_emergency_count >= self._emergency_threshold:
            if self._emergency_audit_callback is not None:
                self._emergency_audit_callback(
                    consecutive_count=self._consecutive_emergency_count,
                    emergency_id=emergency_id,
                    task_id=task_id,
                    operator_id=operator_id,
                )

        self._log_audit(
            request_id,
            "emergency_override",
            operator_id,
            {
                "emergency_id": emergency_id,
                "reason": reason,
                "emergency_type": emergency_type,
                "consecutive_count": self._consecutive_emergency_count,
            },
        )

        logger.warning(
            "Emergency operation recorded: %s by %s, consecutive=%d",
            emergency_id,
            operator_id,
            self._consecutive_emergency_count,
        )

        return {
            "emergency_id": emergency_id,
            "retroactive_approval_required": True,
            "deadline": now + 24 * 3600,
        }

    def set_emergency_audit_callback(self, callback: Callable) -> None:
        """设置紧急操作审计回调"""
        self._emergency_audit_callback = callback

    def reset_consecutive_emergency_count(self) -> None:
        """重置连续紧急操作计数"""
        self._consecutive_emergency_count = 0
