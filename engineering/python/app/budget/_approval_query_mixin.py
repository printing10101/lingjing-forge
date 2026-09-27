"""_ApprovalQueryMixin (split from ApprovalWorkflowEngine)."""

from __future__ import annotations

import logging
from typing import Any
from collections.abc import Callable
from app.models.governance import (
    ApprovalRequest,
    ApprovalStatus,
)


logger = logging.getLogger(__name__)


class _ApprovalQueryMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _get_request: Callable[..., Any]
    _row_to_request: Callable[..., Any]
    _conn: Any

    def get_request(self, request_id: str) -> ApprovalRequest | None:
        """获取审批请求"""
        return self._get_request(request_id)

    def get_requests_by_status(
        self,
        status: ApprovalStatus,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """按状态获取审批请求列表"""
        rows = self._conn.execute(
            """SELECT * FROM approval_requests
               WHERE status = ?
               ORDER BY requested_at DESC
               LIMIT ? OFFSET ?""",
            (status.value, limit, offset),
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get_requests_by_approver(
        self,
        approver_id: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """获取分配给审批人的请求"""
        rows = self._conn.execute(
            """SELECT * FROM approval_requests
               WHERE assigned_approver = ?
               OR approvers LIKE ?
               ORDER BY requested_at DESC
               LIMIT ? OFFSET ?""",
            (approver_id, f"%{approver_id}%", limit, offset),
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get_requests_by_requester(
        self,
        requester: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """获取请求人发起的审批"""
        rows = self._conn.execute(
            """SELECT * FROM approval_requests
               WHERE requester = ?
               ORDER BY requested_at DESC
               LIMIT ? OFFSET ?""",
            (requester, limit, offset),
        ).fetchall()
        return [self._row_to_request(row) for row in rows]

    def get_pending_requests(self, limit: int = 100) -> list[ApprovalRequest]:
        """获取待处理审批请求"""
        return self.get_requests_by_status(ApprovalStatus.PENDING, limit)
