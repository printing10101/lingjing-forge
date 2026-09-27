"""Template Branching System — Foundation for template evolution."""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.dict_utils import deep_merge
from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


@dataclass
class CommitEntry:
    action: str
    branch_name: str
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "branch_name": self.branch_name,
            "timestamp": self.timestamp,
            "details": self.details,
        }


@dataclass
class TemplateBranch:
    branch_id: str
    name: str
    base_branch: str | None
    template_data: dict[str, Any]
    metadata: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    commit_log: list[CommitEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_id": self.branch_id,
            "name": self.name,
            "base_branch": self.base_branch,
            "template_data": self.template_data,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "commit_log": [e.to_dict() for e in self.commit_log],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TemplateBranch":
        return cls(
            branch_id=data["branch_id"],
            name=data["name"],
            base_branch=data.get("base_branch"),
            template_data=data.get("template_data", {}),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            commit_log=[CommitEntry(**e) for e in data.get("commit_log", [])],
        )


class TemplateBranchManager:
    """Manages template branches with SQLite metadata + JSON content storage."""

    BRANCH_TYPES = {"main", "industry", "material", "project", "experiment"}

    def __init__(
        self,
        db_path: str = "data/templates/branches.db",
        json_dir: str = "data/templates/branches",
    ):
        self.db_path = db_path
        self.json_dir = json_dir
        self._lock = threading.RLock()
        self._cache: dict[str, TemplateBranch] = {}
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("template_branches", db_path=self.db_path)
        self._db: sqlite3.Connection

    def initialize(self) -> None:
        """Create SQLite table and ensure directories exist."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        os.makedirs(self.json_dir, exist_ok=True)

        self._db = self._pool.get_connection()
        assert self._db is not None, "template_branching 连接池返回空连接"
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS template_branches (
                branch_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_branch TEXT,
                type TEXT NOT NULL DEFAULT 'main',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)
        self._db.commit()
        self._load_cache()
        logger.info(
            "TemplateBranchManager initialized: db=%s, json_dir=%s",
            self.db_path,
            self.json_dir,
        )

    def _load_cache(self) -> None:
        """Load all branches from storage into memory cache."""
        self._cache.clear()
        cursor = self._db.execute("SELECT * FROM template_branches")
        for row in cursor.fetchall():
            branch_id = row["branch_id"]
            json_path = os.path.join(self.json_dir, f"{branch_id}.json")
            # M19 修复：移除 TOCTOU（os.path.exists 后再 open），
            # 用 try/except FileNotFoundError 同时处理"不存在"和"读取失败"
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except FileNotFoundError:
                logger.warning("分支 JSON 文件缺失，跳过：%s", json_path)
                continue
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("分支 JSON 文件损坏，跳过：%s: %s", json_path, e)
                continue
            try:
                self._cache[branch_id] = TemplateBranch.from_dict(data)
            except (KeyError, TypeError, ValueError) as e:
                logger.warning("分支 JSON 数据格式错误，跳过：%s: %s", json_path, e)
                continue

    def _save_branch(self, branch: TemplateBranch) -> None:
        """Persist branch to SQLite + JSON."""
        with open(
            os.path.join(self.json_dir, f"{branch.branch_id}.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(branch.to_dict(), f, indent=2, ensure_ascii=False)

        self._db.execute(
            """INSERT OR REPLACE INTO template_branches
               (branch_id, name, base_branch, type, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                branch.branch_id,
                branch.name,
                branch.base_branch,
                branch.metadata.get("type", "main"),
                branch.created_at,
                branch.updated_at,
            ),
        )
        self._db.commit()

    def _compute_content_hash(self, data: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:16]

    def create_branch(
        self,
        name: str,
        base_branch: str | None,
        data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> TemplateBranch:
        """Create a new template branch."""
        with self._lock:
            branch_id = uuid.uuid4().hex[:12]
            now = time.time()

            commit_entry = CommitEntry(
                action="create",
                branch_name=name,
                timestamp=now,
                details={
                    "base_branch": base_branch,
                    "content_hash": self._compute_content_hash(data),
                },
            )

            branch = TemplateBranch(
                branch_id=branch_id,
                name=name,
                base_branch=base_branch,
                template_data=data,
                metadata=metadata or {"type": "main"},
                created_at=now,
                updated_at=now,
                commit_log=[commit_entry],
            )

            self._cache[branch_id] = branch
            self._save_branch(branch)

            logger.info(
                "Branch created: id=%s, name=%s, type=%s",
                branch_id,
                name,
                branch.metadata.get("type"),
            )
            return branch

    def get_branch(self, branch_id: str) -> TemplateBranch | None:
        """Retrieve a branch by ID."""
        with self._lock:
            return self._cache.get(branch_id)

    def list_branches(self, type_filter: str | None = None) -> list[TemplateBranch]:
        """List all branches, optionally filtered by type."""
        with self._lock:
            branches = list(self._cache.values())
            if type_filter:
                branches = [b for b in branches if b.metadata.get("type") == type_filter]
            return sorted(branches, key=lambda b: b.updated_at, reverse=True)

    def get_commit_log(self, branch_id: str) -> list[dict[str, Any]]:
        """Get full commit history for a branch."""
        with self._lock:
            branch = self._cache.get(branch_id)
            if branch is None:
                return []
            return [e.to_dict() for e in branch.commit_log]

    def update_branch_data(self, branch_id: str, data: dict[str, Any], action: str = "update") -> TemplateBranch | None:
        """Update a branch's template data and log the change."""
        with self._lock:
            branch = self._cache.get(branch_id)
            if branch is None:
                return None

            branch.template_data = data
            branch.updated_at = time.time()
            branch.commit_log.append(
                CommitEntry(
                    action=action,
                    branch_name=branch.name,
                    timestamp=branch.updated_at,
                    details={"content_hash": self._compute_content_hash(data)},
                )
            )

            self._save_branch(branch)
            logger.info("Branch updated: id=%s, action=%s", branch_id, action)
            return branch

    def merge_branch(self, source_id: str, target_id: str, strategy: str = "overwrite") -> TemplateBranch | None:
        """Merge source branch into target branch."""
        with self._lock:
            source = self._cache.get(source_id)
            target = self._cache.get(target_id)
            if source is None or target is None:
                return None

            if strategy == "overwrite":
                target.template_data = source.template_data.copy()
            elif strategy == "deep_merge":
                target.template_data = deep_merge(target.template_data, source.template_data)

            target.updated_at = time.time()
            target.commit_log.append(
                CommitEntry(
                    action="merge",
                    branch_name=f"{source.name}→{target.name}",
                    timestamp=target.updated_at,
                    details={
                        "source_id": source_id,
                        "target_id": target_id,
                        "strategy": strategy,
                        "content_hash": self._compute_content_hash(target.template_data),
                    },
                )
            )

            self._save_branch(target)
            logger.info("Branch merged: %s → %s (strategy=%s)", source_id, target_id, strategy)
            return target

    def delete_branch(self, branch_id: str) -> bool:
        """Delete a branch (cannot delete main)."""
        with self._lock:
            branch = self._cache.get(branch_id)
            if branch is None:
                return False
            if branch.metadata.get("type") == "main":
                raise ValueError("Cannot delete main branch")

            del self._cache[branch_id]
            self._db.execute("DELETE FROM template_branches WHERE branch_id = ?", (branch_id,))
            self._db.commit()

            json_path = os.path.join(self.json_dir, f"{branch_id}.json")
            # M19 修复：移除 TOCTOU，os.remove 本身会处理文件不存在的情况
            try:
                os.remove(json_path)
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning("删除分支 JSON 失败：%s: %s", json_path, e)

            logger.info("Branch deleted: id=%s", branch_id)
            return True

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池"""
        if self._db:
            self._pool.return_connection(self._db)
            self._db = None  # type: ignore[assignment]
            logger.info("TemplateBranchManager closed")


class _BranchManagerHolder:
    """Thread-safe lazy holder for the :class:`TemplateBranchManager` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: TemplateBranchManager | None = None

    def get(self) -> TemplateBranchManager:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = TemplateBranchManager()
                self._instance.initialize()
            return self._instance

    def init(
        self,
        db_path: str = "data/templates/branches.db",
        json_dir: str = "data/templates/branches",
    ) -> TemplateBranchManager:
        """强制重新创建实例（用于启动时指定 db_path/json_dir 的场景）。"""
        with self._lock:
            if self._instance is not None:
                self._instance.close()
            self._instance = TemplateBranchManager(db_path=db_path, json_dir=json_dir)
            self._instance.initialize()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _BranchManagerHolder()


def get_branch_manager() -> TemplateBranchManager:
    """获取共享的 :class:`TemplateBranchManager` 单例；首次访问时懒初始化。

    Returns:
        :class:`TemplateBranchManager` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_branch_manager)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_template_branching(
    db_path: str = "data/templates/branches.db",
    json_dir: str = "data/templates/branches",
) -> TemplateBranchManager:
    """初始化模板分支管理器，行为与重构前完全一致。"""
    return _holder.init(db_path=db_path, json_dir=json_dir)
