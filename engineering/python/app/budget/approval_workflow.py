"""
Approval Workflow Engine

Complete approval lifecycle management:
- Approval request creation and routing
- State machine: pending → under_review → approved/rejected/escalated
- Intelligent approver assignment (role-based, load-based, relationship-based)
- Time limit management with auto-escalation/rejection
- Immutable approval record system for audit trail
"""

import logging
import threading
from pathlib import Path

from collections.abc import Callable

from app.models.governance import (
    ApprovalDelegation,
)
from app.utils.sqlite_pool import get_sqlite_manager
from app.budget._approval_flow_mixin import _ApprovalFlowMixin
from app.budget._approval_query_mixin import _ApprovalQueryMixin
from app.budget._approval_emergency_mixin import _ApprovalEmergencyMixin
from app.budget._approval_report_mixin import _ApprovalReportMixin

logger = logging.getLogger(__name__)


class ApprovalWorkflowEngine(_ApprovalFlowMixin, _ApprovalQueryMixin, _ApprovalEmergencyMixin, _ApprovalReportMixin):
    """审批工作流引擎"""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            from app.config import PROJECT_ROOT

            db_path = str(Path(PROJECT_ROOT) / "data" / "approval_workflow.db")

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("approval_workflow", db_path=self.db_path)
        self._conn = self._pool.get_connection()
        self._init_schema()
        self._approver_callbacks: dict[str, Callable] = {}
        self._load_delegations: list[ApprovalDelegation] = []
        self._consecutive_emergency_count = 0
        self._emergency_threshold = 3
        self._emergency_audit_callback: Callable | None = None

        logger.info("ApprovalWorkflowEngine initialized at %s", db_path)

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS approval_requests (
                request_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                requester TEXT NOT NULL,
                requested_at REAL NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                context TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_approver TEXT,
                approvers TEXT DEFAULT '[]',
                decisions TEXT DEFAULT '[]',
                required_approvals INTEGER NOT NULL DEFAULT 1,
                risk_score REAL NOT NULL DEFAULT 0.0,
                risk_factors TEXT DEFAULT '[]',
                suggested_decision TEXT DEFAULT '',
                escalated_from TEXT,
                escalated_at REAL,
                emergency_override INTEGER NOT NULL DEFAULT 0,
                emergency_reason TEXT DEFAULT '',
                expires_at REAL,
                completed_at REAL,
                created_at REAL
            );

            CREATE INDEX IF NOT EXISTS idx_ar_status ON approval_requests(status);
            CREATE INDEX IF NOT EXISTS idx_ar_task ON approval_requests(task_id);
            CREATE INDEX IF NOT EXISTS idx_ar_requester ON approval_requests(requester);
            CREATE INDEX IF NOT EXISTS idx_ar_priority ON approval_requests(priority);
            CREATE INDEX IF NOT EXISTS idx_ar_expires ON approval_requests(expires_at);

            CREATE TABLE IF NOT EXISTS approval_delegations (
                id TEXT PRIMARY KEY,
                delegator_id TEXT NOT NULL,
                delegate_id TEXT NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL,
                reason TEXT DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_ad_delegator ON approval_delegations(delegator_id);
            CREATE INDEX IF NOT EXISTS idx_ad_delegate ON approval_delegations(delegate_id);

            CREATE TABLE IF NOT EXISTS emergency_operations (
                id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                emergency_type TEXT NOT NULL,
                executed_at REAL NOT NULL,
                retroactive_approval_required INTEGER NOT NULL DEFAULT 1,
                retroactive_approval_completed INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_eo_task ON emergency_operations(task_id);
            CREATE INDEX IF NOT EXISTS idx_eo_operator ON emergency_operations(operator_id);
            CREATE INDEX IF NOT EXISTS idx_eo_executed ON emergency_operations(executed_at);

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                timestamp REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_log(request_id);
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
        """)
        self._conn.commit()

    def close(self) -> None:
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            logger.info("ApprovalWorkflowEngine closed")


class _ApprovalEngineHolder:
    """Thread-safe lazy holder for the :class:`ApprovalWorkflowEngine` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: ApprovalWorkflowEngine | None = None

    def get(self) -> ApprovalWorkflowEngine:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = ApprovalWorkflowEngine()
            return self._instance

    def init(self, db_path: str | None = None) -> ApprovalWorkflowEngine:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = ApprovalWorkflowEngine(db_path)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _ApprovalEngineHolder()


def get_approval_engine() -> ApprovalWorkflowEngine:
    """获取共享的 :class:`ApprovalWorkflowEngine` 单例；首次访问时懒初始化。

    .. deprecated:: V3.0 (2026-08-02)
    """
    return _holder.get()


def init_approval_engine(db_path: str | None = None) -> ApprovalWorkflowEngine:
    """初始化审批工作流引擎，行为与重构前完全一致。"""
    return _holder.init(db_path)
