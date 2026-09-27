from __future__ import annotations

import sqlite3
import threading
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TIMEOUT_HOURS = 4
DEFAULT_HEARTBEAT_INTERVAL_MINUTES = 15


class LockStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    RELEASED = "released"
    FORCE_RELEASED = "force_released"


@dataclass
class ExecutionLock:
    task_id: str
    agent_id: str
    status: LockStatus = LockStatus.ACTIVE
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + DEFAULT_LOCK_TIMEOUT_HOURS * 3600)
    heartbeat_at: float = field(default_factory=time.time)
    released_at: float | None = None
    release_reason: str | None = None

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def time_remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "status": self.status.value,
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "expires_at": datetime.fromtimestamp(self.expires_at).isoformat(),
            "heartbeat_at": datetime.fromtimestamp(self.heartbeat_at).isoformat(),
            "released_at": datetime.fromtimestamp(self.released_at).isoformat() if self.released_at else None,
            "release_reason": self.release_reason,
            "is_expired": self.is_expired(),
            "time_remaining_seconds": self.time_remaining_seconds(),
        }


class ExecutionLockStore:
    def __init__(self, db_path: str = "execution_locks.db"):
        self._db_path = db_path
        self._lock = threading.Lock()
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("execution_locks", db_path=self._db_path)
        self._conn = self._pool.get_connection()
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接（从连接池）"""
        return self._conn

    def _ensure_tables(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_locks (
                task_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                released_at REAL,
                release_reason TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lock_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                timestamp REAL NOT NULL
            )
        """)
        conn.commit()

    def _record_history(self, task_id: str, agent_id: str, action: str, reason: str | None = None):
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO lock_history (task_id, agent_id, action, reason, timestamp) VALUES (?, ?, ?, ?, ?)",
            (task_id, agent_id, action, reason, time.time()),
        )
        conn.commit()

    def create_lock(
        self,
        task_id: str,
        agent_id: str,
        timeout_hours: float = DEFAULT_LOCK_TIMEOUT_HOURS,
    ) -> ExecutionLock:
        with self._lock:
            conn = self._get_conn()

            existing = conn.execute(
                "SELECT task_id, status FROM execution_locks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing:
                raise LockConflictError(f"Lock already exists for task '{task_id}' (status: {existing['status']})")

            now = time.time()
            lock = ExecutionLock(
                task_id=task_id,
                agent_id=agent_id,
                created_at=now,
                expires_at=now + timeout_hours * 3600,
                heartbeat_at=now,
            )

            conn.execute(
                """INSERT INTO execution_locks (task_id, agent_id, status, created_at, expires_at, heartbeat_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    lock.task_id,
                    lock.agent_id,
                    lock.status.value,
                    lock.created_at,
                    lock.expires_at,
                    lock.heartbeat_at,
                ),
            )
            conn.commit()

            self._record_history(task_id, agent_id, "created")
            logger.info(
                "Execution lock created: task=%s agent=%s expires=%s",
                task_id,
                agent_id,
                datetime.fromtimestamp(lock.expires_at).isoformat(),
            )
            return lock

    def get_lock(self, task_id: str) -> ExecutionLock | None:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM execution_locks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_lock(row)

    def heartbeat(self, task_id: str, agent_id: str) -> ExecutionLock:
        with self._lock:
            conn = self._get_conn()

            row = conn.execute(
                "SELECT * FROM execution_locks WHERE task_id = ? AND agent_id = ?",
                (task_id, agent_id),
            ).fetchone()

            if row is None:
                raise LockNotFoundError(f"No active lock found for task '{task_id}' by agent '{agent_id}'")

            lock = self._row_to_lock(row)
            if lock.status != LockStatus.ACTIVE:
                raise LockNotFoundError(f"Lock for task '{task_id}' is not active (status: {lock.status.value})")

            if lock.is_expired():
                conn.execute(
                    "UPDATE execution_locks SET status = ? WHERE task_id = ?",
                    (LockStatus.EXPIRED.value, task_id),
                )
                conn.commit()
                self._record_history(task_id, agent_id, "expired", "heartbeat on expired lock")
                raise LockExpiredError(f"Lock for task '{task_id}' has expired")

            new_expires = time.time() + DEFAULT_LOCK_TIMEOUT_HOURS * 3600
            new_heartbeat = time.time()
            conn.execute(
                "UPDATE execution_locks SET expires_at = ?, heartbeat_at = ? WHERE task_id = ?",
                (new_expires, new_heartbeat, task_id),
            )
            conn.commit()

            lock.expires_at = new_expires
            lock.heartbeat_at = new_heartbeat
            logger.debug(
                "Heartbeat received: task=%s agent=%s new_expires=%s",
                task_id,
                agent_id,
                datetime.fromtimestamp(new_expires).isoformat(),
            )
            return lock

    def release_lock(self, task_id: str, agent_id: str, reason: str | None = None) -> ExecutionLock:
        with self._lock:
            conn = self._get_conn()

            row = conn.execute("SELECT * FROM execution_locks WHERE task_id = ?", (task_id,)).fetchone()

            if row is None:
                raise LockNotFoundError(f"No lock found for task '{task_id}'")

            lock = self._row_to_lock(row)
            if lock.agent_id != agent_id:
                raise LockOwnershipError(f"Lock for task '{task_id}' is held by '{lock.agent_id}', not '{agent_id}'")

            now = time.time()
            conn.execute(
                """UPDATE execution_locks SET status = ?, released_at = ?, release_reason = ?
                   WHERE task_id = ?""",
                (LockStatus.RELEASED.value, now, reason, task_id),
            )
            conn.commit()

            lock.status = LockStatus.RELEASED
            lock.released_at = now
            lock.release_reason = reason

            self._record_history(task_id, agent_id, "released", reason)
            logger.info(
                "Execution lock released: task=%s agent=%s reason=%s",
                task_id,
                agent_id,
                reason,
            )
            return lock

    def force_release(self, task_id: str, admin_id: str = "admin") -> ExecutionLock:
        with self._lock:
            conn = self._get_conn()

            row = conn.execute("SELECT * FROM execution_locks WHERE task_id = ?", (task_id,)).fetchone()

            if row is None:
                raise LockNotFoundError(f"No lock found for task '{task_id}'")

            lock = self._row_to_lock(row)
            reason = f"Force released by {admin_id}"

            now = time.time()
            conn.execute(
                """UPDATE execution_locks SET status = ?, released_at = ?, release_reason = ?
                   WHERE task_id = ?""",
                (LockStatus.FORCE_RELEASED.value, now, reason, task_id),
            )
            conn.commit()

            lock.status = LockStatus.FORCE_RELEASED
            lock.released_at = now
            lock.release_reason = reason

            self._record_history(task_id, lock.agent_id, "force_released", reason)
            logger.warning("Execution lock force-released: task=%s by=%s", task_id, admin_id)
            return lock

    def cleanup_expired_locks(self) -> list[ExecutionLock]:
        with self._lock:
            conn = self._get_conn()
            now = time.time()

            rows = conn.execute(
                "SELECT * FROM execution_locks WHERE status = 'active' AND expires_at < ?",
                (now,),
            ).fetchall()

            expired_locks: list[ExecutionLock] = []
            for row in rows:
                conn.execute(
                    "UPDATE execution_locks SET status = ? WHERE task_id = ?",
                    (LockStatus.EXPIRED.value, row["task_id"]),
                )
                lock = self._row_to_lock(row)
                lock.status = LockStatus.EXPIRED
                expired_locks.append(lock)
                self._record_history(row["task_id"], row["agent_id"], "expired", "lock timeout")

            conn.commit()

            if expired_locks:
                logger.info("Cleaned up %d expired locks", len(expired_locks))
            return expired_locks

    def list_active_locks(self) -> list[ExecutionLock]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM execution_locks WHERE status = 'active' ORDER BY created_at DESC").fetchall()
        return [self._row_to_lock(row) for row in rows]

    def list_all_locks(self) -> list[ExecutionLock]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM execution_locks ORDER BY created_at DESC").fetchall()
        return [self._row_to_lock(row) for row in rows]

    def get_lock_history(self, task_id: str) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM lock_history WHERE task_id = ? ORDER BY timestamp DESC",
            (task_id,),
        ).fetchall()
        return [
            {
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "action": row["action"],
                "reason": row["reason"],
                "timestamp": datetime.fromtimestamp(row["timestamp"]).isoformat(),
            }
            for row in rows
        ]

    def get_active_lock_by_agent(self, agent_id: str) -> ExecutionLock | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM execution_locks WHERE agent_id = ? AND status = 'active' LIMIT 1",
            (agent_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_lock(row)

    def close(self):
        """关闭数据库连接，归还连接到连接池"""
        if self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None  # type: ignore[assignment]
            logger.info("ExecutionLockStore closed")

    def __del__(self):
        try:
            self.close()
        except (OSError, RuntimeError, AttributeError) as close_err:
            # __del__ 中无法保证 logger 可用，使用 warnings 兜底
            import warnings

            warnings.warn(
                f"ExecutionLock.__del__ close failed: {close_err}",
                RuntimeWarning,
                stacklevel=2,
            )

    @staticmethod
    def _row_to_lock(row) -> ExecutionLock:
        return ExecutionLock(
            task_id=row["task_id"],
            agent_id=row["agent_id"],
            status=LockStatus(row["status"]),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            heartbeat_at=row["heartbeat_at"],
            released_at=row["released_at"],
            release_reason=row["release_reason"],
        )


class LockError(Exception):
    pass


class LockConflictError(LockError):
    pass


class LockNotFoundError(LockError):
    pass


class LockExpiredError(LockError):
    pass


class LockOwnershipError(LockError):
    pass


class _LockStoreHolder:
    """Thread-safe lazy holder for the :class:`ExecutionLockStore` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: ExecutionLockStore | None = None

    def get(self) -> ExecutionLockStore:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = ExecutionLockStore(db_path="execution_locks.db")
            return self._instance

    def init(self, db_path: str = "execution_locks.db") -> ExecutionLockStore:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = ExecutionLockStore(db_path=db_path)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _LockStoreHolder()


def get_execution_lock_store() -> ExecutionLockStore:
    """获取共享的 :class:`ExecutionLockStore` 单例；首次访问时懒初始化。

    Returns:
        :class:`ExecutionLockStore` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_execution_lock_store)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_execution_lock_store(
    db_path: str = "execution_locks.db",
) -> ExecutionLockStore:
    """初始化执行锁存储，行为与重构前完全一致。"""
    return _holder.init(db_path)
