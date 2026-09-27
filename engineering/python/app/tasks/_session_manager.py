"""会话状态管理器（从 execution 拆出）。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.utils.utils import get_output_dir
from app.utils.sqlite_pool import get_sqlite_manager

from app.tasks._execution_models import ExecutionSession, ExecutionStatus

logger = logging.getLogger(__name__)


class SessionManager:
    """会话状态管理器"""

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(get_output_dir("data") / "sessions.db")

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("sessions", db_path=self.db_path)
        self._conn = self._pool.get_connection()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS execution_sessions (
                session_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                checkpoint_data TEXT,
                started_at REAL,
                last_updated REAL,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                created_at REAL DEFAULT (strftime('%s', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_session_task ON execution_sessions(task_id);
            CREATE INDEX IF NOT EXISTS idx_session_status ON execution_sessions(status);
        """)
        self._conn.commit()

    def create_session(self, session: ExecutionSession) -> None:
        """创建执行会话"""
        self._conn.execute(
            """INSERT OR REPLACE INTO execution_sessions
               (session_id, task_id, status, checkpoint_data, started_at,
                last_updated, retry_count, max_retries)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.task_id,
                session.status.value,
                json.dumps(session.checkpoint_data) if session.checkpoint_data else None,
                session.started_at,
                session.last_updated,
                session.retry_count,
                session.max_retries,
            ),
        )
        self._conn.commit()

    # 允许的列名白名单，防止 SQL 注入
    _ALLOWED_COLUMNS = {"status", "last_updated", "checkpoint_data", "retry_count", "max_retries"}

    def update_session(
        self,
        session_id: str,
        status: ExecutionStatus,
        checkpoint_data: dict[str, Any] | None = None,
    ) -> None:
        """更新会话状态"""
        updates = {
            "status": status.value,
            "last_updated": time.time(),
        }

        if checkpoint_data is not None:
            updates["checkpoint_data"] = json.dumps(checkpoint_data)

        # 验证列名是否在白名单中，防止 SQL 注入
        for key in updates.keys():
            if key not in self._ALLOWED_COLUMNS:
                logger.warning("Attempted to update disallowed column: %s", key)
                continue

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys() if k in self._ALLOWED_COLUMNS)
        values = [v for k, v in updates.items() if k in self._ALLOWED_COLUMNS] + [session_id]

        self._conn.execute(f"UPDATE execution_sessions SET {set_clause} WHERE session_id = ?", values)
        self._conn.commit()

    def get_session(self, session_id: str) -> ExecutionSession | None:
        """获取会话"""
        row = self._conn.execute("SELECT * FROM execution_sessions WHERE session_id = ?", (session_id,)).fetchone()

        if row is None:
            return None

        return ExecutionSession(
            session_id=row["session_id"],
            task_id=row["task_id"],
            status=ExecutionStatus(row["status"]),
            checkpoint_data=json.loads(row["checkpoint_data"]) if row["checkpoint_data"] else None,
            started_at=row["started_at"],
            last_updated=row["last_updated"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
        )

    def get_sessions_by_task(self, task_id: str) -> list[ExecutionSession]:
        """获取任务的所有会话"""
        rows = self._conn.execute(
            "SELECT * FROM execution_sessions WHERE task_id = ? ORDER BY started_at DESC",
            (task_id,),
        ).fetchall()

        sessions = []
        for row in rows:
            sessions.append(
                ExecutionSession(
                    session_id=row["session_id"],
                    task_id=row["task_id"],
                    status=ExecutionStatus(row["status"]),
                    checkpoint_data=json.loads(row["checkpoint_data"]) if row["checkpoint_data"] else None,
                    started_at=row["started_at"],
                    last_updated=row["last_updated"],
                    retry_count=row["retry_count"],
                    max_retries=row["max_retries"],
                )
            )

        return sessions

    def get_orphaned_sessions(self, timeout_seconds: float = 3600) -> list[ExecutionSession]:
        """
        获取孤立会话（超时未更新的运行中会话）

        Args:
            timeout_seconds: 超时阈值（秒）

        Returns:
            孤立会话列表
        """
        cutoff = time.time() - timeout_seconds

        rows = self._conn.execute(
            """SELECT * FROM execution_sessions
               WHERE status IN ('running', 'preparing') AND last_updated < ?""",
            (cutoff,),
        ).fetchall()

        sessions = []
        for row in rows:
            sessions.append(
                ExecutionSession(
                    session_id=row["session_id"],
                    task_id=row["task_id"],
                    status=ExecutionStatus(row["status"]),
                    checkpoint_data=json.loads(row["checkpoint_data"]) if row["checkpoint_data"] else None,
                    started_at=row["started_at"],
                    last_updated=row["last_updated"],
                    retry_count=row["retry_count"],
                    max_retries=row["max_retries"],
                )
            )

        if sessions:
            logger.warning("Found %d orphaned sessions", len(sessions))

        return sessions

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池"""
        if hasattr(self, "_conn") and self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None  # type: ignore[assignment]
            logger.info("SessionManager closed")
