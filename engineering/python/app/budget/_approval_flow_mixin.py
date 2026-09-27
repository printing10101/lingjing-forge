"""_ApprovalFlowMixin (split from ApprovalWorkflowEngine)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from collections.abc import Callable
from app.models.governance import (
    ApprovalDecision,
    ApprovalDelegation,
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
)


logger = logging.getLogger(__name__)


class _ApprovalFlowMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供（mypy 需要显式声明）
    _get_request: Callable[..., Any]
    _log_audit: Callable[..., Any]
    _save_request: Callable[..., Any]
    _conn: Any
    _consecutive_emergency_count: Any
    _load_delegations: Any

    def create_approval_request(
        self,
        task_id: str,
        requester: str,
        context: dict[str, Any],
        priority: ApprovalPriority = ApprovalPriority.MEDIUM,
        approvers: list[str] | None = None,
        required_approvals: int = 1,
        risk_score: float = 0.0,
        risk_factors: list[str] | None = None,
        suggested_decision: str = "",
        expires_at: float | None = None,
    ) -> ApprovalRequest:
        """创建审批请求"""
        request_id = f"AR-{uuid.uuid4().hex[:12].upper()}"
        now = time.time()

        if expires_at is None:
            expires_at = now + 24 * 3600

        request = ApprovalRequest(
            request_id=request_id,
            task_id=task_id,
            requester=requester,
            requested_at=now,
            priority=priority,
            context=context,
            status=ApprovalStatus.PENDING,
            approvers=approvers or [],
            required_approvals=required_approvals,
            risk_score=risk_score,
            risk_factors=risk_factors or [],
            suggested_decision=suggested_decision,
            expires_at=expires_at,
        )

        self._save_request(request)
        self._log_audit(
            request_id,
            "created",
            requester,
            {
                "task_id": task_id,
                "priority": priority.value,
                "risk_score": risk_score,
            },
        )

        logger.info(
            "Approval request created: %s for task %s, risk=%.2f",
            request_id,
            task_id,
            risk_score,
        )
        return request

    def assign_approver(self, request_id: str, approver_id: str) -> ApprovalRequest | None:
        """分配审批人"""
        request = self._get_request(request_id)
        if request is None:
            return None

        request.assigned_approver = approver_id
        request.status = ApprovalStatus.UNDER_REVIEW

        if approver_id not in request.approvers:
            request.approvers.append(approver_id)

        self._save_request(request)
        self._log_audit(
            request_id,
            "approver_assigned",
            approver_id,
            {
                "request_id": request_id,
            },
        )

        return request

    def make_decision(
        self,
        request_id: str,
        approver_id: str,
        decision: str,
        comment: str = "",
    ) -> ApprovalRequest | None:
        """审批决策"""
        request = self._get_request(request_id)
        if request is None:
            return None

        if request.status not in (ApprovalStatus.PENDING, ApprovalStatus.UNDER_REVIEW):
            logger.warning(
                "Cannot decide on request %s: invalid status %s",
                request_id,
                request.status,
            )
            return None

        approval_decision = ApprovalDecision(
            approver_id=approver_id,
            decision=decision,
            comment=comment,
            decided_at=time.time(),
        )
        request.decisions.append(approval_decision)

        if decision == "approved":
            approved_count = sum(1 for d in request.decisions if d.decision == "approved")
            if approved_count >= request.required_approvals:
                request.status = ApprovalStatus.APPROVED
                request.completed_at = time.time()
                self._log_audit(
                    request_id,
                    "approved",
                    approver_id,
                    {
                        "comment": comment,
                        "total_decisions": len(request.decisions),
                    },
                )
            else:
                self._log_audit(
                    request_id,
                    "decision_approved",
                    approver_id,
                    {
                        "comment": comment,
                        "approved_so_far": approved_count,
                        "required": request.required_approvals,
                    },
                )
        elif decision == "rejected":
            request.status = ApprovalStatus.REJECTED
            request.completed_at = time.time()
            self._log_audit(
                request_id,
                "rejected",
                approver_id,
                {
                    "comment": comment,
                },
            )
        elif decision == "escalated":
            request.status = ApprovalStatus.ESCALATED
            request.escalated_from = approver_id
            request.escalated_at = time.time()
            self._log_audit(
                request_id,
                "escalated",
                approver_id,
                {
                    "comment": comment,
                },
            )
        elif decision == "request_info":
            self._log_audit(
                request_id,
                "request_info",
                approver_id,
                {
                    "comment": comment,
                },
            )

        self._save_request(request)
        return request

    def escalate_request(self, request_id: str, escalator_id: str, reason: str = "") -> ApprovalRequest | None:
        """升级审批请求"""
        request = self._get_request(request_id)
        if request is None:
            return None

        request.status = ApprovalStatus.ESCALATED
        request.escalated_from = escalator_id
        request.escalated_at = time.time()

        self._save_request(request)
        self._log_audit(
            request_id,
            "escalated",
            escalator_id,
            {
                "reason": reason,
            },
        )

        return request

    def handle_timeout(self) -> int:
        now = time.time()
        rows = self._conn.execute(
            """SELECT request_id, status, expires_at
               FROM approval_requests
               WHERE status IN ('pending', 'under_review')
               AND expires_at IS NOT NULL
               AND expires_at < ?""",
            (now,),
        ).fetchall()

        handled_count = 0
        for row in rows:
            request_id = row["request_id"]
            request = self._get_request(request_id)
            if request is None:
                continue

            if request.escalated_at and request.escalated_from:
                pass

            request.status = ApprovalStatus.ESCALATED
            request.escalated_at = now
            request.escalated_from = "system_timeout"

            self._save_request(request)
            self._log_audit(
                request_id,
                "timeout_escalated",
                "system",
                {
                    "expires_at": request.expires_at,
                },
            )
            handled_count += 1

        if handled_count > 0:
            logger.info("Timeout handling: %d requests escalated", handled_count)
        return handled_count

    def delegate_approval(
        self,
        delegator_id: str,
        delegate_id: str,
        start_time: float,
        end_time: float,
        reason: str = "",
    ) -> ApprovalDelegation:
        """委托审批权限"""
        delegation_id = f"DEL-{uuid.uuid4().hex[:8].upper()}"
        now = time.time()

        delegation = ApprovalDelegation(
            id=delegation_id,
            delegator_id=delegator_id,
            delegate_id=delegate_id,
            start_time=start_time,
            end_time=end_time,
            reason=reason,
            created_at=now,
        )

        self._conn.execute(
            """INSERT INTO approval_delegations
               (id, delegator_id, delegate_id, start_time, end_time, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                delegation.id,
                delegation.delegator_id,
                delegation.delegate_id,
                delegation.start_time,
                delegation.end_time,
                delegation.reason,
                delegation.created_at,
            ),
        )
        self._conn.commit()
        self._load_delegations.append(delegation)

        logger.info("Approval delegated: %s → %s (%s)", delegator_id, delegate_id, reason)
        return delegation

    def get_active_delegation(self, user_id: str) -> ApprovalDelegation | None:
        """获取用户的活跃委托"""
        now = time.time()
        for delegation in self._load_delegations:
            if delegation.delegator_id == user_id and delegation.start_time <= now:
                if delegation.end_time is None or delegation.end_time > now:
                    return delegation
        return None

    def get_delegates_for_user(self, user_id: str) -> list[str]:
        """获取用户可以代理的用户列表"""
        now = time.time()
        delegates = []
        for delegation in self._load_delegations:
            if delegation.delegate_id == user_id and delegation.start_time <= now:
                if delegation.end_time is None or delegation.end_time > now:
                    delegates.append(delegation.delegator_id)
        return delegates

    def complete_retroactive_approval(self, emergency_id: str) -> bool:
        """完成事后审批"""
        self._conn.execute(
            """UPDATE emergency_operations
               SET retroactive_approval_completed = 1
               WHERE id = ?""",
            (emergency_id,),
        )
        self._conn.commit()

        if self._conn.execute("SELECT changes()").fetchone()[0] > 0:
            self._consecutive_emergency_count = max(0, self._consecutive_emergency_count - 1)
            self._log_audit(
                emergency_id,
                "retroactive_completed",
                "system",
                {
                    "emergency_id": emergency_id,
                },
            )
            return True
        return False
