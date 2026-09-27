"""_ApprovalReportMixin (split from ApprovalWorkflowEngine)."""

from __future__ import annotations

import logging
import time
import uuid
import json
from typing import Any
from app.models.governance import (
    ApprovalDecision,
    ApprovalPriority,
    ApprovalRequest,
    ApprovalStatus,
    GovernanceReport,
)


logger = logging.getLogger(__name__)


class _ApprovalReportMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _conn: Any

    def generate_governance_report(
        self,
        period_start: float | None = None,
        period_end: float | None = None,
    ) -> GovernanceReport:
        """生成治理报告"""
        now = time.time()
        if period_start is None:
            period_start = now - 30 * 24 * 3600
        if period_end is None:
            period_end = now

        report = GovernanceReport(
            report_id=f"GR-{uuid.uuid4().hex[:8].upper()}",
            period_start=period_start,
            period_end=period_end,
            generated_at=now,
        )

        rows = self._conn.execute(
            """SELECT status, COUNT(*) as count
               FROM approval_requests
               WHERE requested_at >= ? AND requested_at <= ?
               GROUP BY status""",
            (period_start, period_end),
        ).fetchall()

        for row in rows:
            status = row["status"]
            count = row["count"]
            report.total_requests += count
            if status == ApprovalStatus.APPROVED.value:
                report.approved_count = count
            elif status == ApprovalStatus.REJECTED.value:
                report.rejected_count = count
            elif status == ApprovalStatus.ESCALATED.value:
                report.escalated_count = count

        emergency_count = self._conn.execute(
            """SELECT COUNT(*) FROM emergency_operations
               WHERE executed_at >= ? AND executed_at <= ?""",
            (period_start, period_end),
        ).fetchone()[0]
        report.emergency_count = emergency_count

        if report.total_requests > 0:
            report.rejection_rate = report.rejected_count / report.total_requests
            report.escalation_rate = report.escalated_count / report.total_requests

        avg_time = self._conn.execute(
            """SELECT AVG(completed_at - requested_at) / 3600.0 as avg_hours
               FROM approval_requests
               WHERE completed_at IS NOT NULL
               AND requested_at >= ? AND requested_at <= ?""",
            (period_start, period_end),
        ).fetchone()
        if avg_time and avg_time[0] is not None:
            report.avg_approval_time_hours = avg_time[0]

        risk_trend = self._get_risk_trend(period_start, period_end)
        report.risk_trend = risk_trend

        top_risks = self._conn.execute(
            """SELECT request_id, task_id, risk_score, status
               FROM approval_requests
               WHERE risk_score > 0.7
               AND requested_at >= ? AND requested_at <= ?
               ORDER BY risk_score DESC
               LIMIT 10""",
            (period_start, period_end),
        ).fetchall()
        report.top_risk_operations = [dict(row) for row in top_risks]

        return report

    def export_audit_log(
        self,
        start_time: float | None = None,
        end_time: float | None = None,
        format: str = "json",
    ) -> str:
        """导出审计日志"""
        if start_time is None:
            start_time = 0
        if end_time is None:
            end_time = time.time()

        rows = self._conn.execute(
            """SELECT * FROM audit_log
               WHERE timestamp >= ? AND timestamp <= ?
               ORDER BY timestamp ASC""",
            (start_time, end_time),
        ).fetchall()

        entries = [dict(row) for row in rows]

        if format == "json":
            return json.dumps(entries, ensure_ascii=False, indent=2)
        elif format == "csv":
            if not entries:
                return ""
            headers = ["id", "request_id", "action", "actor_id", "details", "timestamp"]
            lines = [",".join(headers)]
            for entry in entries:
                row = [
                    str(entry["id"]),
                    entry["request_id"],
                    entry["action"],
                    entry["actor_id"],
                    f'"{(entry["details"] or "").replace(chr(34), chr(34) + chr(34))}"',
                    str(entry["timestamp"]),
                ]
                lines.append(",".join(row))
            return "\n".join(lines)
        else:
            raise ValueError(f"Unsupported export format: {format}")

    def _log_audit(self, request_id: str, action: str, actor_id: str, details: dict[str, Any]) -> None:
        """记录不可变审计日志"""
        self._conn.execute(
            """INSERT INTO audit_log (request_id, action, actor_id, details, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (
                request_id,
                action,
                actor_id,
                json.dumps(details),
                time.time(),
            ),
        )
        self._conn.commit()

    def _get_risk_trend(self, start: float, end: float) -> list[dict[str, Any]]:
        """获取风险趋势数据"""
        days = max(1, int((end - start) / (24 * 3600)))
        step = (end - start) / max(days, 1)
        trend = []

        for i in range(days):
            period_start = start + i * step
            period_end = start + (i + 1) * step

            avg_risk = self._conn.execute(
                """SELECT AVG(risk_score) as avg_risk, COUNT(*) as count
                   FROM approval_requests
                   WHERE requested_at >= ? AND requested_at < ?""",
                (period_start, period_end),
            ).fetchone()

            trend.append(
                {
                    "period_start": period_start,
                    "period_end": period_end,
                    "avg_risk": round(avg_risk["avg_risk"], 2) if avg_risk["avg_risk"] else 0,
                    "count": avg_risk["count"],
                }
            )

        return trend

    def _save_request(self, request: ApprovalRequest) -> None:
        """保存审批请求到数据库"""
        self._conn.execute(
            """INSERT OR REPLACE INTO approval_requests
               (request_id, task_id, requester, requested_at, priority, context,
                status, assigned_approver, approvers, decisions, required_approvals,
                risk_score, risk_factors, suggested_decision, escalated_from,
                escalated_at, emergency_override, emergency_reason, expires_at,
                completed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.request_id,
                request.task_id,
                request.requester,
                request.requested_at,
                request.priority.value,
                json.dumps(request.context),
                request.status.value,
                request.assigned_approver,
                json.dumps(request.approvers),
                json.dumps([d.to_dict() for d in request.decisions]),
                request.required_approvals,
                request.risk_score,
                json.dumps(request.risk_factors),
                request.suggested_decision,
                request.escalated_from,
                request.escalated_at,
                int(request.emergency_override),
                request.emergency_reason,
                request.expires_at,
                request.completed_at,
                request.requested_at,
            ),
        )
        self._conn.commit()

    def _get_request(self, request_id: str) -> ApprovalRequest | None:
        """从数据库获取审批请求"""
        row = self._conn.execute("SELECT * FROM approval_requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_request(row)

    def _row_to_request(self, row) -> ApprovalRequest:
        """将数据库行转换为审批请求对象"""
        decisions_data = json.loads(row["decisions"]) if row["decisions"] else []
        decisions = [
            ApprovalDecision(
                approver_id=d["approver_id"],
                decision=d["decision"],
                comment=d.get("comment", ""),
                decided_at=d.get("decided_at"),
            )
            for d in decisions_data
        ]

        return ApprovalRequest(
            request_id=row["request_id"],
            task_id=row["task_id"],
            requester=row["requester"],
            requested_at=row["requested_at"],
            priority=ApprovalPriority(row["priority"]),
            context=json.loads(row["context"]) if row["context"] else {},
            status=ApprovalStatus(row["status"]),
            assigned_approver=row["assigned_approver"],
            approvers=json.loads(row["approvers"]) if row["approvers"] else [],
            decisions=decisions,
            required_approvals=row["required_approvals"],
            risk_score=row["risk_score"],
            risk_factors=json.loads(row["risk_factors"]) if row["risk_factors"] else [],
            suggested_decision=row["suggested_decision"],
            escalated_from=row["escalated_from"],
            escalated_at=row["escalated_at"],
            emergency_override=bool(row["emergency_override"]),
            emergency_reason=row["emergency_reason"],
            expires_at=row["expires_at"],
            completed_at=row["completed_at"],
        )
