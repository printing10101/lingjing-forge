"""对话历史会话存储（SQLite 持久化）。

为「LLM 对话式工艺 / NC 代码解释」提供多轮对话上下文管理。

设计说明：
- 独立 SQLite 文件（默认 ``data/process_explainer_sessions.db``），
  与主业务库解耦，避免影响主库迁移
- 同步 sqlite3 + asyncio.to_thread 包装，避免引入额外异步依赖
- 单条消息结构：session_id / role / content / created_at / metadata_json
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


from app.config.limits import DEFAULT_SQLITE_LOCK_TIMEOUT_SEC

logger = logging.getLogger(__name__)


# ── 默认存储路径 ──────────────────────────────────────────────────────
_DEFAULT_DB_DIR = Path(
    os.environ.get(
        "PROCESS_EXPLAINER_DB_DIR",
        str(Path(__file__).resolve().parents[3] / "data"),
    )
)
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "process_explainer_sessions.db"

# 单会话最大保留消息数（防止历史无限增长）
MAX_MESSAGES_PER_SESSION = 50
# 会话过期时间（秒）—— 默认 7 天
SESSION_TTL_SECONDS = 7 * 24 * 3600


@dataclass
class ChatMessage:
    """单条对话消息。"""

    session_id: str
    role: str  # "user" / "assistant" / "system"
    content: str
    created_at: float
    metadata: dict

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class SessionStore:
    """对话历史 SQLite 存储。

    线程安全：使用 ``threading.local`` 连接池，每个工作线程持有独立连接，
    规避 sqlite3 默认 ``check_same_thread=True`` 限制；写入操作通过
    ``_write_lock`` 串行化，避免 SQLite "database is locked" 错误。
    异步友好：所有 IO 操作通过 ``asyncio.to_thread`` 包装。

    连接池设计：
        - 每个线程首次访问时创建连接（懒加载），缓存到 ``_thread_local.conn``
        - 连接配置 ``check_same_thread=False`` + ``isolation_level=None``（手动事务）
        - 进程退出时通过 ``atexit`` 注册的清理回调统一关闭所有线程的连接，
          避免 sqlite3 自动 close 时序不确定导致的资源泄漏
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # 写入锁：串行化所有 DML，规避 SQLite 多写者冲突
        self._write_lock = threading.Lock()
        # 线程局部连接池：每个工作线程持有独立 connection
        self._thread_local = threading.local()
        # 跟踪所有线程创建的连接，用于进程退出时统一关闭
        # （threading.local 无法跨线程枚举属性，必须自行登记）
        self._all_conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        # 注册进程退出时的清理回调（幂等，多次注册只生效一次）
        atexit.register(self._cleanup_at_exit)
        # 初始化表结构
        self._init_db_sync()

    # 连接池

    def _get_conn(self) -> sqlite3.Connection:
        """获取当前线程的 sqlite3 连接（懒加载 + 线程局部缓存）。

        - 首次调用时创建连接并缓存到 ``_thread_local``
        - 后续调用直接复用，避免每次 connect/ close 的开销
        - ``check_same_thread=False`` 允许连接跨 asyncio 工作线程使用
          （但实际由 threading.local 保证每线程独占，无真正跨线程共享）
        - ``isolation_level=None`` 启用手动事务，配合显式 BEGIN/COMMIT
        """
        conn = getattr(self._thread_local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # 手动事务
                timeout=DEFAULT_SQLITE_LOCK_TIMEOUT_SEC,  # 等待锁
            )
            conn.row_factory = sqlite3.Row
            self._thread_local.conn = conn
            # 登记到全局连接列表，供 close_all / atexit 统一关闭
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    # 资源清理

    def _cleanup_at_exit(self) -> None:
        """进程退出时关闭所有线程创建的 SQLite 连接。

        作为 ``atexit`` 回调注册，由解释器在退出时调用。
        连接被 close 后，``_thread_local.conn`` 仍持有失效引用——
        进程退出后无影响；测试场景需配合 ``reset_session_store`` 重置单例，
        否则后续访问会复用已关闭连接导致 ``ProgrammingError``。
        """
        try:
            with self._conns_lock:
                for conn in self._all_conns:
                    try:
                        conn.close()
                    except sqlite3.Error as e:
                        logger.debug("close conn failed: %s", e)
                self._all_conns.clear()
        except Exception as e:  # atexit 回调不应抛异常
            logger.debug("session_store cleanup failed: %s", e)

    def close_all(self) -> None:
        """显式关闭所有线程创建的 SQLite 连接。

        供测试或应用 shutdown 场景使用，调用后该实例不可再用；
        单例场景请配合 ``reset_session_store`` 重置全局实例。
        """
        self._cleanup_at_exit()

    # 初始化

    def _init_db_sync(self) -> None:
        """同步初始化表结构（在构造时调用一次）。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    metadata    TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_session_created
                ON chat_messages (session_id, created_at)
                """
            )
            conn.commit()

    # 公开异步接口

    async def create_session(self) -> str:
        """创建新会话，返回 session_id。"""
        session_id = f"pe_{uuid.uuid4().hex[:16]}"
        # 创建会话即插入一条 system 占位消息（便于追踪创建时间）
        await self.add_message(
            session_id=session_id,
            role="system",
            content=f"Session created at {time.strftime('%Y-%m-%d %H:%M:%S')}",
            metadata={"event": "session_created"},
        )
        return session_id

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ChatMessage:
        """追加一条消息到会话历史。

        原子性保证：INSERT + TRIM 在同一事务中完成，避免裁剪失败时
        消息超过上限而无报错的隐式数据不一致。
        """
        msg = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
            created_at=time.time(),
            metadata=metadata or {},
        )
        await asyncio.to_thread(self._add_message_with_trim_sync, msg)
        return msg

    async def get_history(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[ChatMessage]:
        """获取会话最近 N 条消息（按时间升序）。"""
        return await asyncio.to_thread(self._get_history_sync, session_id, limit)

    async def get_messages_as_llm_format(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        """获取会话历史并转为 LLM messages 格式。"""
        msgs = await self.get_history(session_id, limit)
        return [
            {"role": m.role, "content": m.content}
            for m in msgs
            if m.role in ("user", "assistant", "system") and not m.metadata.get("event")
        ]

    async def clear_session(self, session_id: str) -> int:
        """清空指定会话的所有消息，返回删除条数。"""
        return await asyncio.to_thread(self._clear_session_sync, session_id)

    async def cleanup_expired(self) -> int:
        """清理过期会话，返回删除条数。"""
        cutoff = time.time() - SESSION_TTL_SECONDS
        return await asyncio.to_thread(self._cleanup_expired_sync, cutoff)

    # 同步实现（仅供 asyncio.to_thread 调用）
    # 使用线程局部连接池 + 写入锁串行化

    def _add_message_with_trim_sync(self, msg: ChatMessage) -> None:
        """原子化写入 + 裁剪（单事务）。

        事务边界：BEGIN → INSERT → COUNT → DELETE(可选) → COMMIT
        任意步骤失败整体回滚，避免 INSERT 成功但 TRIM 失败导致超限。
        """
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO chat_messages
                        (session_id, role, content, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        msg.session_id,
                        msg.role,
                        msg.content,
                        msg.created_at,
                        json.dumps(msg.metadata, ensure_ascii=False),
                    ),
                )
                # 同事务内裁剪：超过上限时删除最早消息（保留 system 占位）
                count = conn.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
                    (msg.session_id,),
                ).fetchone()[0]
                if count > MAX_MESSAGES_PER_SESSION:
                    excess = count - MAX_MESSAGES_PER_SESSION
                    conn.execute(
                        """
                        DELETE FROM chat_messages
                        WHERE rowid IN (
                            SELECT rowid FROM chat_messages
                            WHERE session_id = ? AND role != 'system'
                            ORDER BY created_at ASC
                            LIMIT ?
                        )
                        """,
                        (msg.session_id, excess),
                    )
                conn.execute("COMMIT")
            except (sqlite3.Error, OSError) as e:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error as rb_err:
                    # ROLLBACK 失败通常意味着连接已损坏，记录便于排查但不抛出
                    # （外层 except 已记录原始事务异常并 re-raise）
                    logger.debug("ROLLBACK failed: %s", rb_err, exc_info=True)
                logger.error(
                    "add_message 事务失败 (session=%s): %s",
                    msg.session_id,
                    e,
                    exc_info=True,
                )
                raise

    def _get_history_sync(
        self,
        session_id: str,
        limit: int,
    ) -> list[ChatMessage]:
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM chat_messages
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        # 反转为升序
        rows = list(reversed(rows))
        result = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
            except json.JSONDecodeError:
                meta = {}
            result.append(
                ChatMessage(
                    session_id=r["session_id"],
                    role=r["role"],
                    content=r["content"],
                    created_at=r["created_at"],
                    metadata=meta,
                )
            )
        return result

    def _clear_session_sync(self, session_id: str) -> int:
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    "DELETE FROM chat_messages WHERE session_id = ?",
                    (session_id,),
                )
                conn.execute("COMMIT")
                return cur.rowcount
            except (sqlite3.Error, OSError) as e:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error as rb_err:
                    # ROLLBACK 失败通常意味着连接已损坏，记录便于排查但不抛出
                    # （外层 except 已记录原始事务异常并 re-raise）
                    logger.debug("ROLLBACK failed: %s", rb_err, exc_info=True)
                logger.error(
                    "clear_session 事务失败 (session=%s): %s",
                    session_id,
                    e,
                    exc_info=True,
                )
                raise

    def _cleanup_expired_sync(self, cutoff_ts: float) -> int:
        with self._write_lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    """
                    DELETE FROM chat_messages
                    WHERE session_id IN (
                        SELECT session_id FROM chat_messages
                        GROUP BY session_id
                        HAVING MAX(created_at) < ?
                    )
                    """,
                    (cutoff_ts,),
                )
                conn.execute("COMMIT")
                return cur.rowcount
            except (sqlite3.Error, OSError) as e:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error as rb_err:
                    # ROLLBACK 失败通常意味着连接已损坏，记录便于排查但不抛出
                    # （外层 except 已记录原始事务异常并 re-raise）
                    logger.debug("ROLLBACK failed: %s", rb_err, exc_info=True)
                logger.error(
                    "cleanup_expired 事务失败: %s",
                    e,
                    exc_info=True,
                )
                raise


# ── 全局单例（双重检查锁） ────────────────────────────────────────────
_global_store: SessionStore | None = None
_singleton_lock = threading.Lock()


def get_session_store() -> SessionStore:
    """获取全局 SessionStore 单例（双重检查锁，线程安全）。"""
    global _global_store
    if _global_store is None:
        with _singleton_lock:
            if _global_store is None:
                _global_store = SessionStore()
    return _global_store


def reset_session_store() -> None:
    """重置全局 SessionStore 单例（仅供测试使用）。

    调用前应确保无进行中的 IO 操作；调用后单例会被置空，
    下次 ``get_session_store`` 调用将创建新实例。
    """
    global _global_store
    with _singleton_lock:
        if _global_store is not None:
            _global_store.close_all()
            _global_store = None


__all__ = [
    "ChatMessage",
    "SessionStore",
    "get_session_store",
    "reset_session_store",
    "MAX_MESSAGES_PER_SESSION",
    "SESSION_TTL_SECONDS",
]
