"""
Goal Chain Store

SQLite-backed storage for goal hierarchy with version tracking,
goal chain resolution, and progress computation.
"""

import logging
import time
import threading
from pathlib import Path
from typing import Any

from app.models.goals import (
    Goal,
    GoalLevel,
    GoalStatus,
    GoalRef,
    GoalVersion,
    GoalProgress,
    DEFAULT_GOALS,
)
from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


class GoalChainStore:
    """Persistent goal chain storage with SQLite backend"""

    # 允许的列名白名单，防止 SQL 注入
    _ALLOWED_COLUMNS = {"name", "description", "level", "parent_id", "status", "created_at", "completed_at", "version"}

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = str(Path(".lingjing/.gstack") / "goal_chain.db")
        self._db_path = db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # 使用统一的连接池管理器（传入 db_path，避免不同测试共享连接池导致死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("goal_chain", db_path=self._db_path)
        self._conn: Any = self._pool.get_connection()
        # 即使 check_same_thread=False 允许多线程访问连接，sqlite3 本身对单连接的
        # 写操作不是线程安全的；通过统一的写锁保护 _conn 上的所有写入，避免
        # "OperationalError: database is locked" 与数据竞争。
        self._write_lock = threading.Lock()
        self._init_tables()
        self._seed_defaults()

    def _init_tables(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                level TEXT NOT NULL,
                parent_id TEXT,
                status TEXT NOT NULL DEFAULT 'not_started',
                created_at REAL,
                completed_at REAL,
                version INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (parent_id) REFERENCES goals(id)
            );

            CREATE TABLE IF NOT EXISTS goal_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                changed_at REAL,
                changed_by TEXT NOT NULL DEFAULT 'system',
                change_type TEXT NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT DEFAULT '',
                new_value TEXT DEFAULT '',
                FOREIGN KEY (goal_id) REFERENCES goals(id)
            );

            CREATE INDEX IF NOT EXISTS idx_goals_level ON goals(level);
            CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_id);
            CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
            CREATE INDEX IF NOT EXISTS idx_versions_goal ON goal_versions(goal_id);
        """)
        self._conn.commit()

    def _seed_defaults(self):
        # P1 并发修复：原代码 SELECT+INSERT 在 _write_lock 外，仅 commit 在锁内，
        # 多实例启动时会并发读到 existing==0 并重复 seed。
        # 现将 SELECT+INSERT+commit 整个序列纳入 _write_lock 保护，保证原子性。
        # 防复发：seed 操作的读-改-写必须完整持锁，不得拆分。
        with self._write_lock:
            existing = self._conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
            if existing == 0:
                for goal in DEFAULT_GOALS:
                    self._conn.execute(
                        "INSERT INTO goals (id, name, description, level, parent_id, "
                        "status, created_at, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            goal.id,
                            goal.name,
                            goal.description,
                            goal.level.value,
                            goal.parent_id,
                            goal.status.value,
                            time.time(),
                            goal.version,
                        ),
                    )
                self._conn.commit()
                logger.info("Default goal hierarchy seeded")

    def add_goal(self, goal: Goal) -> Goal:
        if goal.created_at is None:
            goal.created_at = time.time()
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO goals (id, name, description, level, parent_id, "
                "status, created_at, version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    goal.id,
                    goal.name,
                    goal.description,
                    goal.level.value,
                    goal.parent_id,
                    goal.status.value,
                    goal.created_at,
                    goal.version,
                ),
            )
            self._conn.commit()
        return goal

    def get_goal(self, goal_id: str) -> Goal | None:
        row = self._conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_goal(row)

    def update_goal(self, goal_id: str, **kwargs) -> Goal | None:
        goal = self.get_goal(goal_id)
        if goal is None:
            return None

        changes = []
        for key, value in kwargs.items():
            # 验证列名是否在白名单中，防止 SQL 注入
            if key not in self._ALLOWED_COLUMNS:
                logger.warning("Attempted to update disallowed column: %s", key)
                continue
            if hasattr(goal, key) and value != getattr(goal, key):
                old = str(getattr(goal, key))
                new = str(value)
                self._record_version(
                    goal_id=goal_id,
                    version=goal.version + 1,
                    change_type="update",
                    field_name=key,
                    old_value=old,
                    new_value=new,
                )
                changes.append((key, value))
                setattr(goal, key, value)

        if not changes:
            return goal

        with self._write_lock:
            for key, value in changes:
                self._conn.execute(
                    f"UPDATE goals SET {key} = ? WHERE id = ?",
                    (value, goal_id),
                )

            goal.version += 1
            self._conn.execute("UPDATE goals SET version = ? WHERE id = ?", (goal.version, goal_id))
            self._conn.commit()
        return goal

    def delete_goal(self, goal_id: str) -> bool:
        with self._write_lock:
            row = self._conn.execute("SELECT id FROM goals WHERE id = ?", (goal_id,)).fetchone()
            if row is None:
                return False
            self._conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
            self._conn.execute("DELETE FROM goal_versions WHERE goal_id = ?", (goal_id,))
            self._conn.commit()
            return True

    def get_children(self, goal_id: str) -> list[Goal]:
        rows = self._conn.execute(
            "SELECT * FROM goals WHERE parent_id = ? ORDER BY created_at",
            (goal_id,),
        ).fetchall()
        return [self._row_to_goal(r) for r in rows]

    def resolve_goal_chain(self, goal_id: str) -> list[GoalRef]:
        chain: list[GoalRef] = []
        visited = set()
        # parent_id 可为 None（链终止）；Optional 使 213 行赋值类型一致
        current_id: str | None = goal_id

        while current_id and current_id not in visited:
            visited.add(current_id)
            goal = self.get_goal(current_id)
            if goal is None:
                break
            chain.append(
                GoalRef(
                    id=goal.id,
                    level=goal.level,
                    name=goal.name,
                    description=goal.description,
                )
            )
            current_id = goal.parent_id

        return chain

    def get_all_goals(self, level: GoalLevel | None = None) -> list[Goal]:
        if level:
            rows = self._conn.execute(
                "SELECT * FROM goals WHERE level = ? ORDER BY created_at",
                (level.value,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM goals ORDER BY level, created_at").fetchall()
        return [self._row_to_goal(r) for r in rows]

    def get_goal_tree(self) -> list[dict[str, Any]]:
        all_goals = self.get_all_goals()
        {g.id: g for g in all_goals}
        root_goals = [g for g in all_goals if g.parent_id is None or g.level == GoalLevel.MISSION]

        def _build_tree(goal: Goal) -> dict[str, Any]:
            children = [g for g in all_goals if g.parent_id == goal.id]
            node: dict[str, Any] = {
                **goal.to_dict(),
                "children": [_build_tree(c) for c in children],
            }
            return node

        return [_build_tree(g) for g in root_goals]

    def compute_progress(self, goal_id: str, task_status_map: dict[str, str] | None = None) -> GoalProgress:
        goal = self.get_goal(goal_id)
        if goal is None:
            return GoalProgress()

        child_goals = self.get_children(goal_id)
        total = 0
        completed = 0
        in_progress = 0

        if task_status_map is None:
            task_status_map = {}

        for cg in child_goals:
            sub = self.compute_progress(cg.id, task_status_map)
            total += sub.total_tasks
            completed += sub.completed_tasks
            in_progress += sub.in_progress_tasks

        direct_tasks = {tid: st for tid, st in task_status_map.items() if self._task_belongs_to_goal(tid, goal_id)}
        for tid, st in direct_tasks.items():
            if st == "completed":
                completed += 1
            elif st in ("in_progress", "running", "queued"):
                in_progress += 1
            total += 1

        progress = GoalProgress(
            goal_id=goal_id,
            goal_name=goal.name,
            level=goal.level,
            total_tasks=total,
            completed_tasks=completed,
            in_progress_tasks=in_progress,
            progress_percent=round((completed / total * 100) if total > 0 else 0.0, 1),
            last_updated=time.time(),
        )
        return progress

    def get_version_history(self, goal_id: str, limit: int = 50) -> list[GoalVersion]:
        rows = self._conn.execute(
            "SELECT * FROM goal_versions WHERE goal_id = ? ORDER BY changed_at DESC LIMIT ?",
            (goal_id, limit),
        ).fetchall()
        return [self._row_to_version(r) for r in rows]

    def propagate_cancellation(self, cancelled_goal_id: str) -> list[str]:
        affected_ids: list[str] = []
        self._mark_descendants_needs_review(cancelled_goal_id, affected_ids)
        return affected_ids

    def _mark_descendants_needs_review(self, parent_id: str, affected: list[str]):
        children = self.get_children(parent_id)
        updates: list = []
        for child in children:
            if child.level == GoalLevel.TASK and child.status in (
                GoalStatus.IN_PROGRESS,
                GoalStatus.NOT_STARTED,
            ):
                updates.append((GoalStatus.NEEDS_REVIEW.value, child.id))
                affected.append(child.id)
            self._mark_descendants_needs_review(child.id, affected)
        if updates:
            with self._write_lock:
                for status_value, child_id in updates:
                    self._conn.execute(
                        "UPDATE goals SET status = ? WHERE id = ?",
                        (status_value, child_id),
                    )
                self._conn.commit()

    def _record_version(
        self,
        goal_id: str,
        version: int,
        change_type: str,
        field_name: str,
        old_value: str,
        new_value: str,
        changed_by: str = "system",
    ):
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO goal_versions (goal_id, version, changed_at, "
                "changed_by, change_type, field_name, old_value, new_value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    goal_id,
                    version,
                    time.time(),
                    changed_by,
                    change_type,
                    field_name,
                    old_value,
                    new_value,
                ),
            )

    def _task_belongs_to_goal(self, task_id: str, goal_id: str) -> bool:
        if hasattr(self, "_task_belongs_checker"):
            return self._task_belongs_checker(task_id, goal_id)
        return False

    def set_task_belongs_checker(self, checker):
        self._task_belongs_checker = checker

    def _row_to_goal(self, row) -> Goal:
        return Goal(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            level=GoalLevel(row["level"]),
            parent_id=row["parent_id"],
            status=GoalStatus(row["status"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            version=row["version"],
        )

    def _row_to_version(self, row) -> GoalVersion:
        return GoalVersion(
            id=row["id"],
            goal_id=row["goal_id"],
            version=row["version"],
            changed_at=row["changed_at"],
            changed_by=row["changed_by"],
            change_type=row["change_type"],
            field_name=row["field_name"],
            old_value=row["old_value"],
            new_value=row["new_value"],
        )

    def close(self):
        with self._write_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


class _GoalChainStoreHolder:
    """Thread-safe lazy holder for the :class:`GoalChainStore` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: GoalChainStore | None = None

    def get(self, db_path: str | None = None) -> GoalChainStore:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = GoalChainStore(db_path)
            return self._instance

    def init(self, db_path: str | None = None) -> GoalChainStore:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            self._instance = GoalChainStore(db_path)
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _GoalChainStoreHolder()


def get_goal_chain_store(db_path: str | None = None) -> GoalChainStore:
    """获取共享的 :class:`GoalChainStore` 单例；首次访问时懒初始化。

    Returns:
        :class:`GoalChainStore` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_goal_chain_store)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get(db_path)


def init_goal_chain_store(db_path: str | None = None) -> GoalChainStore:
    """初始化目标链存储，行为与重构前完全一致。"""
    return _holder.init(db_path)
