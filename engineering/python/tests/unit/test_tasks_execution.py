"""tasks/execution SessionManager 覆盖率补强测试。

覆盖 app/tasks/execution.py 的 SessionManager（纯 SQLite CRUD）：
- 创建/查询/更新/删除会话
- 按任务查询 / 孤儿会话检测
- 序列化（checkpoint_data JSON roundtrip）
"""

from __future__ import annotations

import pytest

from app.tasks.execution import SessionManager
from app.tasks._execution_models import ExecutionSession, ExecutionStatus

pytestmark = pytest.mark.unit


def _make_session(session_id: str = "s-1", task_id: str = "t-1") -> ExecutionSession:
    return ExecutionSession(
        session_id=session_id,
        task_id=task_id,
        status=ExecutionStatus.PENDING,
        checkpoint_data={"step": 1},
        retry_count=0,
        max_retries=3,
    )


class TestSessionManager:
    def test_init_creates_schema(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        assert mgr.db_path.endswith("sessions.db")

    def test_create_and_get_roundtrip(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        mgr.create_session(_make_session())
        got = mgr.get_session("s-1")
        assert got is not None
        assert got.task_id == "t-1"
        assert got.status == ExecutionStatus.PENDING
        assert got.checkpoint_data == {"step": 1}

    def test_get_missing_returns_none(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        assert mgr.get_session("nope") is None

    def test_update_status(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        mgr.create_session(_make_session())
        mgr.update_session("s-1", ExecutionStatus.RUNNING)
        got = mgr.get_session("s-1")
        assert got is not None
        assert got.status == ExecutionStatus.RUNNING

    def test_update_with_checkpoint(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        mgr.create_session(_make_session())
        mgr.update_session("s-1", ExecutionStatus.RUNNING, checkpoint_data={"step": 5})
        got = mgr.get_session("s-1")
        assert got is not None
        assert got.checkpoint_data == {"step": 5}

    def test_get_sessions_by_task(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        mgr.create_session(_make_session("s-1", "task-x"))
        mgr.create_session(_make_session("s-2", "task-x"))
        mgr.create_session(_make_session("s-3", "task-y"))
        sessions = mgr.get_sessions_by_task("task-x")
        assert len(sessions) == 2

    def test_get_sessions_by_task_empty(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        assert mgr.get_sessions_by_task("nothing") == []

    def test_get_orphaned_sessions(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        # 孤儿判定要求 status ∈ (running, preparing) 且 last_updated 超时
        import time

        mgr.create_session(
            ExecutionSession(
                session_id="s-orphan",
                task_id="t-orphan",
                status=ExecutionStatus.RUNNING,
                checkpoint_data={},
                retry_count=0,
                max_retries=3,
                last_updated=time.time() - 36000,  # 10 小时前
            )
        )
        orphans = mgr.get_orphaned_sessions(timeout_seconds=3600)
        assert len(orphans) >= 1
        assert orphans[0].session_id == "s-orphan"

    def test_close(self, tmp_path):
        mgr = SessionManager(db_path=str(tmp_path / "sessions.db"))
        mgr.close()  # 不抛错即可
