"""Template Update Service — proactively pushes template optimization suggestions to projects."""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


@dataclass
class UpdateNotification:
    notification_id: str
    project_id: str
    suggestion_id: str
    priority: str
    title: str
    description: str
    change_preview: dict[str, Any]
    expected_impact: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "project_id": self.project_id,
            "suggestion_id": self.suggestion_id,
            "priority": self.priority,
            "title": self.title,
            "description": self.description,
            "change_preview": self.change_preview,
            "expected_impact": self.expected_impact,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UpdateNotification":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class TemplateUpdateService:
    """Monitors projects for applicable template optimizations and pushes notifications."""

    PRIORITY_THRESHOLDS = {
        "critical": 0.10,
        "recommended": 0.03,
    }

    def __init__(self, db_path: str = "data/templates/updates.db"):
        self.db_path = db_path
        self._lock = threading.RLock()
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("template_updates", db_path=self.db_path)
        self._db: sqlite3.Connection
        self._notifications: dict[str, UpdateNotification] = {}

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        self._db = self._pool.get_connection()
        assert self._db is not None, "template_update_service 连接池返回空连接"
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS update_notifications (
                notification_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                suggestion_id TEXT,
                priority TEXT,
                title TEXT,
                description TEXT,
                change_preview TEXT,
                expected_impact TEXT,
                created_at REAL,
                status TEXT
            )
        """)
        self._db.commit()
        self._load_data()
        logger.info("TemplateUpdateService initialized: db=%s", self.db_path)

    def _load_data(self) -> None:
        cursor = self._db.execute("SELECT * FROM update_notifications ORDER BY created_at DESC")
        for row in cursor.fetchall():
            self._notifications[row["notification_id"]] = UpdateNotification(
                notification_id=row["notification_id"],
                project_id=row["project_id"],
                suggestion_id=row["suggestion_id"] or "",
                priority=row["priority"],
                title=row["title"] or "",
                description=row["description"] or "",
                change_preview=json.loads(row["change_preview"]) if row["change_preview"] else {},
                expected_impact=json.loads(row["expected_impact"]) if row["expected_impact"] else {},
                created_at=row["created_at"],
                status=row["status"],
            )

    def classify_priority(self, improvement: float) -> str:
        if improvement >= self.PRIORITY_THRESHOLDS["critical"]:
            return "critical"
        elif improvement >= self.PRIORITY_THRESHOLDS["recommended"]:
            return "recommended"
        else:
            return "optional"

    def create_notification(
        self,
        project_id: str,
        suggestion: dict[str, Any],
        priority: str,
    ) -> UpdateNotification:
        with self._lock:
            notif = UpdateNotification(
                notification_id=f"upd_{uuid.uuid4().hex[:8]}",
                project_id=project_id,
                suggestion_id=suggestion.get("suggestion_id", ""),
                priority=priority,
                title=suggestion.get("title", "Template Optimization Available"),
                description=suggestion.get("description", ""),
                change_preview=suggestion.get("change_preview", {}),
                expected_impact=suggestion.get("expected_impact", {}),
            )
            self._notifications[notif.notification_id] = notif
            self._db.execute(
                """INSERT INTO update_notifications
                   (notification_id, project_id, suggestion_id, priority, title, description, change_preview, expected_impact, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    notif.notification_id,
                    notif.project_id,
                    notif.suggestion_id,
                    notif.priority,
                    notif.title,
                    notif.description,
                    json.dumps(notif.change_preview),
                    json.dumps(notif.expected_impact),
                    notif.created_at,
                    notif.status,
                ),
            )
            self._db.commit()
            logger.info(
                "Update notification created: id=%s, project=%s, priority=%s",
                notif.notification_id,
                project_id,
                priority,
            )
            return notif

    def scan_for_updates(
        self,
        project_id: str,
        suggestions: list[dict[str, Any]],
    ) -> list[UpdateNotification]:
        with self._lock:
            notifications = []
            for suggestion in suggestions:
                improvement = suggestion.get("expected_impact", {}).get("improvement", 0)
                priority = self.classify_priority(improvement)

                existing = [
                    n
                    for n in self._notifications.values()
                    if n.project_id == project_id
                    and n.suggestion_id == suggestion.get("suggestion_id", "")
                    and n.status in ("pending", "dismissed", "applied")
                ]
                if existing:
                    continue

                notif = self.create_notification(project_id, suggestion, priority)
                notifications.append(notif)

            return notifications

    def apply_update(self, notification_id: str) -> UpdateNotification | None:
        with self._lock:
            notif = self._notifications.get(notification_id)
            if notif is None:
                return None

            notif.status = "applied"
            self._db.execute(
                "UPDATE update_notifications SET status = ? WHERE notification_id = ?",
                ("applied", notification_id),
            )
            self._db.commit()
            logger.info("Update applied: id=%s", notification_id)
            return notif

    def dismiss_notification(self, notification_id: str) -> bool:
        with self._lock:
            notif = self._notifications.get(notification_id)
            if notif is None:
                return False

            notif.status = "dismissed"
            self._db.execute(
                "UPDATE update_notifications SET status = ? WHERE notification_id = ?",
                ("dismissed", notification_id),
            )
            self._db.commit()
            logger.info("Notification dismissed: id=%s", notification_id)
            return True

    def preview_update(self, notification_id: str) -> dict[str, Any] | None:
        with self._lock:
            notif = self._notifications.get(notification_id)
            if notif is None:
                return None
            return {
                "title": notif.title,
                "description": notif.description,
                "change_preview": notif.change_preview,
                "expected_impact": notif.expected_impact,
                "priority": notif.priority,
            }

    def get_notifications(
        self,
        project_id: str,
        status_filter: str | None = None,
    ) -> list[UpdateNotification]:
        with self._lock:
            notifs = [n for n in self._notifications.values() if n.project_id == project_id]
            if status_filter:
                notifs = [n for n in notifs if n.status == status_filter]
            return sorted(notifs, key=lambda n: n.created_at, reverse=True)

    def close(self) -> None:
        """关闭数据库连接，归还连接到连接池"""
        if self._db:
            self._pool.return_connection(self._db)
            self._db = None  # type: ignore[assignment]
            logger.info("TemplateUpdateService closed")


class _UpdateServiceHolder:
    """Thread-safe lazy holder for the :class:`TemplateUpdateService` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: TemplateUpdateService | None = None

    def get(self) -> TemplateUpdateService:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = TemplateUpdateService()
                self._instance.initialize()
            return self._instance

    def init(self, db_path: str = "data/templates/updates.db") -> TemplateUpdateService:
        """强制重新创建实例（用于启动时指定 db_path 的场景）。"""
        with self._lock:
            if self._instance is not None:
                self._instance.close()
            self._instance = TemplateUpdateService(db_path=db_path)
            self._instance.initialize()
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _UpdateServiceHolder()


def get_update_service() -> TemplateUpdateService:
    """获取共享的 :class:`TemplateUpdateService` 单例；首次访问时懒初始化。

    Returns:
        :class:`TemplateUpdateService` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_update_service)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_update_service(
    db_path: str = "data/templates/updates.db",
) -> TemplateUpdateService:
    """初始化模板更新服务，行为与重构前完全一致。"""
    return _holder.init(db_path)
